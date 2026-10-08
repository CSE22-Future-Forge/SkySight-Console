"""HTTP + WebSocket API (spec 7) through FastAPI's TestClient."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from ground_station.api.server import create_app

from .conftest import add_device, cei, detection


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _post_detection(client, **kw):
    r = client.post("/internal/detections", json=detection(**kw))
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ pairing ---
def test_pairing_flow_with_team_choice(client, manifest):
    token = manifest.devices.create_pairing()["pairing_token"]
    body = {"pairing_token": token, "device_label": "Alpha-1 Samsung A15", "app_version": "0.1"}
    r = client.post("/api/v1/pair", json=body)
    assert r.status_code == 409 and r.json()["error"] == "team_required"
    assert {t["name"] for t in r.json()["teams"]} == {"Alpha", "Bravo"}

    r = client.post("/api/v1/pair", json={**body, "team_id": manifest.alpha["id"]})
    assert r.status_code == 200
    paired = r.json()
    assert paired["team"]["name"] == "Alpha" and paired["mission"]["name"] == "Test mission"

    assert client.post("/api/v1/pair", json={**body, "team_id": manifest.alpha["id"]}).status_code == 401
    me = client.get("/api/v1/me", headers=_auth(paired["device_token"])).json()
    assert me["team"]["id"] == manifest.alpha["id"]
    assert me["settings"]["alert_repeat_s"] == 30 and me["settings"]["gps_max_accuracy_m"] == 50

    stored = manifest.db.conn().execute("SELECT token_hash FROM devices").fetchone()[0]
    assert stored == hashlib.sha256(paired["device_token"].encode()).hexdigest()


def test_auth_required_and_revocation(client, manifest):
    assert client.get("/api/v1/victims").status_code == 401
    assert client.get("/api/v1/victims", headers=_auth("nope")).json()["error"] == "unauthorized"
    device, token = add_device(manifest, manifest.alpha)
    assert client.get("/api/v1/victims", headers=_auth(token)).status_code == 200
    manifest.devices.revoke(device["id"])
    r = client.get("/api/v1/victims", headers=_auth(token))
    assert r.status_code == 401 and r.json()["error"] == "revoked"


def test_health_needs_no_auth(client):
    body = client.get("/api/v1/health").json()
    assert body["mission"]["name"] == "Test mission" and body["server_time"] > 0


# ----------------------------------------------------------------- internal ---
def test_internal_rejects_non_localhost(manifest):
    app = create_app(manifest)  # default: only 127.0.0.1 / ::1; TestClient reports "testclient"
    with TestClient(app) as c:
        r = c.post("/internal/detections", json=detection())
        assert r.status_code == 403 and r.json()["error"] == "forbidden"


def test_internal_detection_validation(client):
    bad = detection()
    bad["confidence"] = 1.5
    r = client.post("/internal/detections", json=bad)
    assert r.status_code == 422 and r.json()["error"] == "validation_error"
    no_img = detection()
    no_img["images"] = {}
    assert client.post("/internal/detections", json=no_img).status_code == 422
    garbage = detection()
    garbage["images"] = {"frame_b64": "bm90IGFuIGltYWdl"}
    assert client.post("/internal/detections", json=garbage).json()["error"] == "bad_image"


def test_telemetry_is_throttled_to_1hz(client):
    base = {"ts": 1_000_000, "lat": 7.29, "lon": 80.63, "alt_agl_m": 60, "heading_deg": 10}
    assert client.post("/internal/telemetry", json=base).json()["stored"] is True
    assert client.post("/internal/telemetry", json={**base, "ts": 1_000_400}).json()["stored"] is False
    assert client.post("/internal/telemetry", json={**base, "ts": 1_001_000}).json()["stored"] is True


# ------------------------------------------------------------- phone REST ---
def test_victim_flow_over_http(client, manifest):
    _, token_a = add_device(manifest, manifest.alpha)
    _, token_b = add_device(manifest, manifest.bravo)
    created = _post_detection(client, track_id=3)
    vid = created["victim_id"]

    listing = client.get("/api/v1/victims", headers=_auth(token_a)).json()
    assert [v["id"] for v in listing["victims"]] == [vid]
    v = listing["victims"][0]
    assert v["display_code"] == "V-0001" and v["updated_seq"] > 0 and v["thumb_url"]

    img = client.get(v["thumb_url"], headers=_auth(token_a))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"

    r = client.post(f"/api/v1/victims/{vid}/accept", json={"client_event_id": cei(), "client_ts": 1},
                    headers=_auth(token_a))
    assert r.status_code == 200 and r.json()["victim"]["status"] == "ongoing"
    r = client.post(f"/api/v1/victims/{vid}/accept", json={"client_event_id": cei()}, headers=_auth(token_b))
    assert r.status_code == 409 and r.json()["error"] == "already_claimed" and r.json()["team"]["name"] == "Alpha"

    assert client.get(f"/api/v1/victims/{vid}", headers=_auth(token_b)).status_code == 404
    assert client.get(v["thumb_url"], headers=_auth(token_b)).status_code == 404
    detail = client.get(f"/api/v1/victims/{vid}", headers=_auth(token_a)).json()
    assert [h["to_status"] for h in detail["history"]] == ["open", "ongoing"]

    r = client.post(f"/api/v1/victims/{vid}/complete", json={"client_event_id": cei(), "people_count": 1},
                    headers=_auth(token_a))
    assert r.json()["victim"]["status"] == "completed"
    me = client.get("/api/v1/me", headers=_auth(token_a)).json()
    assert [v["status"] for v in me["assignments"]] == ["completed"]


def test_locations_dedupe_resent_points(client, manifest):
    _, token = add_device(manifest, manifest.alpha)
    pts = [{"client_point_id": f"pt-{i:06d}", "lat": 7.29 + i * 1e-4, "lon": 80.63, "accuracy_m": 6,
            "battery_pct": 80 - i, "fix_ts": 1000 + i} for i in range(3)]
    assert client.post("/api/v1/locations", json={"points": pts}, headers=_auth(token)).json() == \
        {"accepted": 3, "duplicates": 0}
    assert client.post("/api/v1/locations", json={"points": pts}, headers=_auth(token)).json() == \
        {"accepted": 0, "duplicates": 3}
    team = manifest.teams.get(manifest.alpha["id"])
    assert team["last_lat"] == pytest.approx(7.2902) and team["last_battery_pct"] == 78


def test_receipt_endpoint(client, manifest):
    _, token = add_device(manifest, manifest.alpha)
    vid = _post_detection(client, track_id=1)["victim_id"]
    r = client.post(f"/api/v1/alerts/{vid}/receipt", json={"delivered_at": 5}, headers=_auth(token))
    assert r.status_code == 204
    assert manifest.victims.receipts(vid)["delivered"] == 1


def test_sync_pages_and_filters(client, manifest):
    _, token = add_device(manifest, manifest.alpha)
    for i in range(3):
        _post_detection(client, track_id=i, lat=7.29 + i * 0.01)
    body = client.get("/api/v1/sync?since_seq=0", headers=_auth(token)).json()
    assert [e["type"] for e in body["events"]] == ["victim.created"] * 3
    seqs = [e["seq"] for e in body["events"]]
    assert seqs == sorted(seqs)
    again = client.get(f"/api/v1/sync?since_seq={body['last_seq']}", headers=_auth(token)).json()
    assert again["events"] == [] and again["last_seq"] == body["last_seq"]


# --------------------------------------------------------------------- maps ---
@pytest.fixture
def map_file(manifest):
    path = manifest.paths.maps / "srilanka.pmtiles"
    path.write_bytes(bytes(range(256)) * 40)  # 10240 bytes
    return path


def test_map_manifest_and_full_download(client, map_file):
    meta = client.get("/maps/manifest").json()
    assert meta["file"] == "srilanka.pmtiles" and meta["size"] == 10240
    assert meta["sha256"] == hashlib.sha256(map_file.read_bytes()).hexdigest()
    r = client.get("/maps/srilanka.pmtiles")
    assert r.status_code == 200 and r.content == map_file.read_bytes() and r.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize("header,start,end", [("bytes=0-99", 0, 99), ("bytes=10200-", 10200, 10239),
                                              ("bytes=-40", 10200, 10239), ("bytes=10000-99999", 10000, 10239)])
def test_map_range_requests(client, map_file, header, start, end):
    r = client.get("/maps/srilanka.pmtiles", headers={"Range": header})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes {start}-{end}/10240"
    assert r.content == map_file.read_bytes()[start:end + 1]


def test_map_bad_range_and_missing_map(client, manifest, map_file):
    assert client.get("/maps/srilanka.pmtiles", headers={"Range": "bytes=20000-"}).status_code == 416
    map_file.unlink()
    assert client.get("/maps/manifest").json()["error"] == "no_map"
