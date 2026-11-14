"""WebSocket push against a real uvicorn server thread (spec 7.4, acceptance tests in 12).

These use the production ServerThread on a free localhost port and a real
``websockets`` client, so connection teardown is exactly what phones cause.
"""

import json
import socket

import httpx
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from ground_station.api.server import ServerThread

from .conftest import add_device, cei, detection, wait_for


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(manifest):
    server = ServerThread(manifest, host="127.0.0.1", port=_free_port(), advertise=False)
    assert server.start_and_wait(), server.error
    base = f"http://127.0.0.1:{server.port}"
    with httpx.Client(base_url=base, timeout=10) as http:
        yield type("Live", (), {"server": server, "http": http, "ws_base": f"ws://127.0.0.1:{server.port}"})
    server.stop()


def _detect(live, **kw):
    r = live.http.post("/internal/detections", json=detection(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def _recv(ws, timeout=5):
    """Next non-ping message."""
    while True:
        msg = json.loads(ws.recv(timeout=timeout))
        if msg.get("type") != "ping":
            return msg


def test_internal_api_answers_real_localhost(live):
    assert live.http.post("/internal/telemetry", json={"ts": 1, "lat": 7.2, "lon": 80.6}).status_code == 200


def test_replay_missed_alerts_in_order_then_live(live, manifest):
    """Acceptance: Wi-Fi off, 5 victims created, Wi-Fi on -> all 5 arrive in order."""
    _, token = add_device(manifest, manifest.alpha)
    created = [_detect(live, track_id=i, lat=7.29 + i * 0.01)["victim_id"] for i in range(5)]
    with connect(f"{live.ws_base}/ws?token={token}&since_seq=0") as ws:
        replay = [_recv(ws) for _ in range(5)]
        assert [m["data"]["id"] for m in replay] == created
        assert all(m["type"] == "victim.created" for m in replay)
        ws.send(json.dumps({"type": "ack", "seq": replay[-1]["seq"]}))

        live_victim = _detect(live, track_id=99, lat=7.5)
        msg = _recv(ws)
        assert msg["type"] == "victim.created" and msg["data"]["id"] == live_victim["victim_id"]
        assert msg["seq"] > replay[-1]["seq"]
        assert wait_for(lambda: manifest.db.conn().execute("SELECT ws_connected FROM devices").fetchone()[0] == 1)

    def device():
        return manifest.db.conn().execute("SELECT * FROM devices").fetchone()

    assert wait_for(lambda: device()["ws_connected"] == 0)
    assert device()["last_acked_seq"] == replay[-1]["seq"]


def test_since_seq_skips_already_seen(live, manifest):
    _, token = add_device(manifest, manifest.alpha)
    _detect(live, track_id=1)
    seen = manifest.db.conn().execute("SELECT MAX(seq) FROM events").fetchone()[0]
    second = _detect(live, track_id=2, lat=7.4)
    with connect(f"{live.ws_base}/ws?token={token}&since_seq={seen}") as ws:
        assert _recv(ws)["data"]["id"] == second["victim_id"]


def test_claim_hides_victim_for_other_team(live, manifest):
    a, _ = add_device(manifest, manifest.alpha)
    _, token_b = add_device(manifest, manifest.bravo)
    vid = _detect(live, track_id=1)["victim_id"]
    with connect(f"{live.ws_base}/ws?token={token_b}&since_seq=0") as ws:
        assert _recv(ws)["type"] == "victim.created"
        manifest.victims.accept(vid, a, cei())
        msg = _recv(ws)
        assert msg["type"] == "victim.hidden" and msg["data"] == {"id": vid, "reason": "claimed"}


def test_rejects_bad_token(live):
    with connect(f"{live.ws_base}/ws?token=bad") as ws:
        assert json.loads(ws.recv(timeout=5))["error"] == "unauthorized"
        with pytest.raises(ConnectionClosed) as exc:
            ws.recv(timeout=5)
    assert exc.value.rcvd.code == 4401


def test_location_message_then_revoke_closes(live, manifest):
    device, token = add_device(manifest, manifest.alpha)
    with connect(f"{live.ws_base}/ws?token={token}") as ws:
        ws.send(json.dumps({"type": "location", "data": {"client_point_id": "ws-000001", "lat": 7.3, "lon": 80.6,
                                                          "accuracy_m": 4, "fix_ts": 5}}))
        ws.send(json.dumps({"type": "pong"}))
        assert wait_for(lambda: manifest.teams.get(manifest.alpha["id"])["last_lat"] is not None)
        assert manifest.teams.get(manifest.alpha["id"])["last_lat"] == pytest.approx(7.3)
        manifest.devices.revoke(device["id"])
        with pytest.raises(ConnectionClosed) as exc:
            while True:
                ws.recv(timeout=5)
    assert exc.value.rcvd.code == 4403


def test_newer_connection_replaces_older(live, manifest):
    _, token = add_device(manifest, manifest.alpha)
    with connect(f"{live.ws_base}/ws?token={token}") as old:
        assert wait_for(lambda: len(live.server.hub.connected_device_ids()) == 1)
        with connect(f"{live.ws_base}/ws?token={token}") as new:
            with pytest.raises(ConnectionClosed) as exc:
                old.recv(timeout=5)
            assert exc.value.rcvd.code == 4000
            vid = _detect(live, track_id=5)["victim_id"]
            assert _recv(new)["data"]["id"] == vid
    assert wait_for(lambda: manifest.db.conn().execute("SELECT ws_connected FROM devices").fetchone()[0] == 0)


def test_alert_latency_under_two_seconds(live, manifest):
    """Acceptance: /internal/detections -> phone message, median + p95 < 2 s (50 alerts)."""
    import statistics
    import time

    _, token = add_device(manifest, manifest.alpha)
    latencies = []
    with connect(f"{live.ws_base}/ws?token={token}") as ws:
        for i in range(50):
            t0 = time.perf_counter()
            vid = _detect(live, track_id=2000 + i, lat=7.0 + i * 0.01)["victim_id"]
            msg = _recv(ws)
            latencies.append(time.perf_counter() - t0)
            assert msg["data"]["id"] == vid
    latencies.sort()
    p95 = latencies[int(0.95 * len(latencies)) - 1]
    print(f"\nalert latency: median {statistics.median(latencies) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms")
    assert statistics.median(latencies) < 2 and p95 < 2
