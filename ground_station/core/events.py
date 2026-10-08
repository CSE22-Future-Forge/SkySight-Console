"""Event bus: DB write -> events row -> fan-out (spec rule 4, "never lose an alert").

Services open a ``unit`` (one IMMEDIATE transaction), make their changes and call
``unit.emit(...)``. Each emit inserts the ``events`` row inside the same
transaction, so an event exists if and only if its change was committed. After
COMMIT the events are handed to subscribers (WebSocket hub, Qt bridge).

Ordering: SQLite serialises writers, and the commit + dispatch step runs under a
single lock, so subscribers always see events in ``seq`` order even when several
threads write at once. Subscribers must therefore be quick and non-blocking (put
on a queue / emit a Qt signal) and must not write to the DB synchronously.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from ground_station.db import repo

log = logging.getLogger(__name__)

# Phone-visible
VICTIM_CREATED = "victim.created"
VICTIM_UPDATED = "victim.updated"
VICTIM_HIDDEN = "victim.hidden"
ASSIGNMENT_CHANGED = "assignment.changed"

# Laptop-only (events.laptop_only = 1)
TEAM_LOCATION = "team.location"
DEVICE_ONLINE = "device.online"
DEVICE_OFFLINE = "device.offline"
DEVICE_REVOKED = "device.revoked"
ALERT_RECEIPT = "alert.receipt"
DETECTION_NEAR_RESCUER = "detection.near_rescuer"
UAV_POSE = "uav.pose"
MISSION_CHANGED = "mission.changed"
TEAM_CHANGED = "team.changed"

LAPTOP_ONLY_TYPES = frozenset(
    {TEAM_LOCATION, DEVICE_ONLINE, DEVICE_OFFLINE, DEVICE_REVOKED, ALERT_RECEIPT, DETECTION_NEAR_RESCUER,
     UAV_POSE, MISSION_CHANGED, TEAM_CHANGED}
)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Event:
    seq: int
    type: str
    ts: int
    data: dict
    entity_id: str | None = None
    scope_team_id: str | None = None
    laptop_only: bool = False

    def envelope(self) -> dict:
        """Wire format sent to phones."""
        return {"seq": self.seq, "type": self.type, "ts": self.ts, "data": self.data}

    def visible_to(self, team_id: str) -> bool:
        return not self.laptop_only and (self.scope_team_id is None or self.scope_team_id == team_id)

    @classmethod
    def from_row(cls, row) -> "Event":
        return cls(
            seq=row["seq"],
            type=row["type"],
            ts=row["created_at"],
            data=json.loads(row["payload_json"]),
            entity_id=row["entity_id"],
            scope_team_id=row["scope_team_id"],
            laptop_only=bool(row["laptop_only"]),
        )


@dataclass
class Unit:
    conn: Any
    now: int
    events: list[Event] = field(default_factory=list)

    def emit(self, type_: str, data: dict, *, entity_id=None, scope_team_id=None, laptop_only=None) -> Event:
        if laptop_only is None:
            laptop_only = type_ in LAPTOP_ONLY_TYPES
        seq = repo.insert_event(self.conn, type_, entity_id, scope_team_id, laptop_only, data, self.now)
        if "updated_seq" in data:
            # Victim payloads carry the seq of the event that carried them.
            data = {**data, "updated_seq": seq}
            repo.set_event_payload(self.conn, seq, data)
        event = Event(seq, type_, self.now, data, entity_id, scope_team_id, bool(laptop_only))
        self.events.append(event)
        return event


class EventBus:
    def __init__(self, db):
        self.db = db
        self._subscribers: list[Callable[[list[Event]], None]] = []
        self._dispatch_lock = threading.Lock()

    def subscribe(self, callback: Callable[[list[Event]], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    @contextmanager
    def unit(self):
        conn = self.db.conn()
        conn.execute("BEGIN IMMEDIATE")
        u = Unit(conn, now_ms())
        try:
            yield u
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        with self._dispatch_lock:
            conn.execute("COMMIT")
            if u.events:
                self._dispatch(u.events)

    def _dispatch(self, events: list[Event]) -> None:
        for callback in list(self._subscribers):
            try:
                callback(events)
            except Exception:  # a broken subscriber must never block the manifest
                log.exception("event subscriber failed")
