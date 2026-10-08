import base64
import time
import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from ground_station.api.server import create_app
from ground_station.config import Paths
from ground_station.core.models import DetectionIn
from ground_station.core.services import Manifest

BASE_LAT, BASE_LON = 7.2906, 80.6337  # Kandy


def frame_b64(w=640, h=480) -> str:
    img = np.full((h, w, 3), 60, np.uint8)
    cv2.rectangle(img, (300, 200), (330, 260), (220, 220, 220), -1)
    ok, buf = cv2.imencode(".jpg", img)
    return base64.b64encode(buf.tobytes()).decode()


FRAME = frame_b64()


def detection(lat=BASE_LAT, lon=BASE_LON, track_id=None, confidence=0.8, geo_error_m=5.0, modality="fused",
              frame_ts=None, mission_id=None) -> dict:
    return {
        "mission_id": mission_id, "track_id": track_id, "frame_ts": frame_ts or int(time.time() * 1000),
        "modality": modality, "confidence": confidence, "bbox": {"x": 300, "y": 200, "w": 30, "h": 60},
        "lat": lat, "lon": lon, "geo_error_m": geo_error_m,
        "uav": {"lat": lat, "lon": lon, "alt_agl_m": 60, "heading_deg": 90, "gimbal_pitch_deg": -90},
        "images": {"frame_b64": FRAME},
    }


@pytest.fixture
def manifest(tmp_path):
    m = Manifest(Paths(tmp_path / "data"))
    m.alpha = m.teams.create("Alpha", "A1", "#1f77b4")
    m.bravo = m.teams.create("Bravo", "B1", "#ff7f0e")
    m.mission = m.missions.start("Test mission", "Kandy")
    yield m
    m.close()


def ingest(m, **kw):
    return m.victims.ingest_detection(DetectionIn.model_validate(detection(**kw)))


def add_device(m, team) -> tuple[dict, str]:
    """Pair a device through the real pairing flow; returns (device row, bearer token)."""
    from ground_station.core.models import PairIn

    p = m.devices.create_pairing(team_id=team["id"])
    res = m.devices.pair(PairIn(pairing_token=p["pairing_token"], device_label=f"{team['name']} phone"))
    return m.devices.authenticate(res["device_token"]), res["device_token"]


def cei() -> str:
    return str(uuid.uuid4())


def wait_for(predicate, timeout=3.0):
    """Poll for work the server finishes asynchronously (WS handlers run on another thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def client(manifest):
    app = create_app(manifest, internal_hosts={"testclient", "127.0.0.1"})
    with TestClient(app) as c:
        yield c
