"""WebSocket hub: per-device connections, team filtering, replay and keepalive (spec 7.4).

Lives on the uvicorn event loop. The event bus calls ``_on_events`` from whatever
thread committed the change; it hops onto the loop with call_soon_threadsafe and
puts each visible event on every matching connection's queue.

Replay without gaps or duplicates: a connection registers its queue *before*
replaying from the DB, then the sender drops queued events with
seq <= last replayed seq. Events committed after the replay snapshot have
higher seqs and arrive through the queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import WebSocket
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ground_station.core import events as ev
from ground_station.core.models import LocationPoint
from ground_station.core.services import SYNC_PAGE, ServiceError

log = logging.getLogger(__name__)

CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401
CLOSE_REVOKED = 4403
CLOSE_DEAD = 4408


class _Conn:
    def __init__(self, ws: WebSocket, device):
        self.ws = ws
        self.device = device
        self.device_id = device["id"]
        self.team_id = device["team_id"]
        self.queue: asyncio.Queue = asyncio.Queue()
        self.last_sent = 0
        self.last_rx = time.monotonic()
        self.closing = False

    async def close(self, code: int, reason: str = "") -> None:
        if self.closing:
            return
        self.closing = True
        try:
            if self.ws.application_state != WebSocketState.DISCONNECTED:
                await self.ws.close(code=code, reason=reason)
        except Exception:  # already gone
            pass


class WsHub:
    def __init__(self, manifest):
        self.m = manifest
        self.loop: asyncio.AbstractEventLoop | None = None
        self._conns: dict[str, _Conn] = {}
        manifest.bus.subscribe(self._on_events)

    # ------------------------------------------------------------ status ---
    def connected_device_ids(self) -> list[str]:
        return list(self._conns)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def shutdown(self) -> None:
        for conn in list(self._conns.values()):
            await conn.close(1001, "server shutting down")
        self.m.bus.unsubscribe(self._on_events)

    # ------------------------------------------------------------ fan-out ---
    def _on_events(self, events: list[ev.Event]) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, events)
        except RuntimeError:  # loop shutting down
            pass

    def _fanout(self, events: list[ev.Event]) -> None:
        for e in events:
            if e.type == ev.DEVICE_REVOKED:
                conn = self._conns.get(e.data["device_id"])
                if conn:
                    asyncio.ensure_future(conn.close(CLOSE_REVOKED, "revoked"))
            if e.laptop_only:
                continue
            for conn in list(self._conns.values()):
                if e.visible_to(conn.team_id):
                    conn.queue.put_nowait(e)

    # -------------------------------------------------------- connection ---
    async def serve(self, ws: WebSocket) -> None:
        token = ws.query_params.get("token")
        try:
            since_seq = max(0, int(ws.query_params.get("since_seq", "0")))
        except ValueError:
            since_seq = 0
        try:
            device = await run_in_threadpool(self.m.devices.authenticate, token, ws.client.host if ws.client else None)
        except ServiceError as exc:
            await ws.accept()
            await ws.send_json({"type": "error", "error": exc.code, "message": exc.message})
            await ws.close(code=CLOSE_REVOKED if exc.code == "revoked" else CLOSE_UNAUTHORIZED)
            return

        await ws.accept()
        conn = _Conn(ws, device)
        previous = self._conns.get(conn.device_id)
        self._conns[conn.device_id] = conn  # register before replay (see module doc)
        if previous:
            await previous.close(CLOSE_REPLACED, "replaced by a newer connection")
        await run_in_threadpool(self.m.devices.set_connected, conn.device_id, True)

        tasks = []
        try:
            await self._replay(conn, since_seq)
            tasks = [asyncio.create_task(self._sender(conn)), asyncio.create_task(self._receiver(conn)),
                     asyncio.create_task(self._pinger(conn))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            if self._conns.get(conn.device_id) is conn:
                del self._conns[conn.device_id]
                # Started before any further await: even if this task is being cancelled
                # (server shutdown), the executor still records the device as offline.
                offline = asyncio.get_running_loop().run_in_executor(None, self._mark_offline, conn.device_id)
            else:
                offline = None
            await asyncio.gather(*tasks, return_exceptions=True)
            await conn.close(1000)
            if offline is not None:
                await asyncio.shield(offline)

    def _mark_offline(self, device_id: str) -> None:
        try:
            self.m.devices.set_connected(device_id, False)
        except Exception:
            log.exception("could not mark %s offline", device_id)

    async def _replay(self, conn: _Conn, since_seq: int) -> None:
        last = since_seq
        while True:
            page = await run_in_threadpool(self.m.sync.events_for_team, conn.team_id, last)
            for envelope in page["events"]:
                await conn.ws.send_json(envelope)
            last = page["last_seq"]
            if len(page["events"]) < SYNC_PAGE:
                break
        conn.last_sent = last

    async def _sender(self, conn: _Conn) -> None:
        while True:
            e = await conn.queue.get()
            if e.seq <= conn.last_sent:
                continue  # already delivered by the replay
            await conn.ws.send_json(e.envelope())
            conn.last_sent = e.seq

    async def _pinger(self, conn: _Conn) -> None:
        interval = self.m.settings.get("ws_ping_interval_s")
        dead_after = self.m.settings.get("ws_dead_after_s")
        while True:
            await asyncio.sleep(interval)
            if time.monotonic() - conn.last_rx > dead_after:
                await conn.close(CLOSE_DEAD, "no pong")
                return
            await conn.ws.send_json({"type": "ping", "ts": ev.now_ms()})

    async def _receiver(self, conn: _Conn) -> None:
        while True:
            text = await conn.ws.receive_text()
            conn.last_rx = time.monotonic()
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ack":
                try:
                    await run_in_threadpool(self.m.devices.ack, conn.device_id, int(msg.get("seq", 0)))
                except (TypeError, ValueError):
                    pass
            elif kind == "location":
                try:
                    point = LocationPoint.model_validate(msg.get("data", msg))
                except ValidationError:
                    continue
                await run_in_threadpool(self.m.teams.add_locations, conn.device, [point.model_dump()])
            # "pong" and anything else only refresh last_rx.
