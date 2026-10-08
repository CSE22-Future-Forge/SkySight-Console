"""SkySight -> ground station hand-off (core/manifest_link.py) against a real server."""

import socket
import time

import httpx
import numpy as np
import pytest

from core.manifest_link import LinkConfig, ManifestLink, project_to_ground
from core.tracker import Track
from ground_station.api.server import ServerThread
from ground_station.core import geo

from .conftest import BASE_LAT, BASE_LON, wait_for

POSE = {"lat": BASE_LAT, "lon": BASE_LON, "alt_agl_m": 100.0, "heading_deg": 0.0, "gimbal_pitch_deg": -90.0}


# ---------------------------------------------------------------- projection ---
def test_nadir_centre_is_below_the_uav():
    lat, lon, err = project_to_ground(POSE, 640, 360, 1280, 720, 90)
    assert geo.haversine_m(lat, lon, BASE_LAT, BASE_LON) < 0.01 and err > 3


def test_image_edge_with_90_deg_fov_is_one_altitude_away():
    # Heading east: image right points south.
    lat, lon, _ = project_to_ground({**POSE, "heading_deg": 90}, 1280, 360, 1280, 720, 90)
    assert geo.haversine_m(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(100, rel=1e-3)
    assert geo.bearing_deg(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(180, abs=0.5)


def test_oblique_gimbal_looks_ahead():
    lat, lon, _ = project_to_ground({**POSE, "gimbal_pitch_deg": -45}, 640, 360, 1280, 720, 70)
    assert geo.haversine_m(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(100, rel=1e-3)
    assert geo.bearing_deg(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(0, abs=0.5)


def test_ray_above_horizon_misses_ground():
    assert project_to_ground({**POSE, "gimbal_pitch_deg": 0}, 640, 0, 1280, 720, 70) is None


# ---------------------------------------------------------------- live link ---
@pytest.fixture
def server(manifest):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ServerThread(manifest, host="127.0.0.1", port=port, advertise=False)
    assert srv.start_and_wait()
    yield srv
    srv.stop()


@pytest.fixture
def link(server, tmp_path):
    path = tmp_path / "manifest_link.json"
    LinkConfig(enabled=True, url=f"http://127.0.0.1:{server.port}", min_hits=3, camera_hfov_deg=90).save(path)
    lk = ManifestLink(path)
    yield lk
    lk.close()


def _track(track_id, hits=5, score=0.8, box=(600, 300, 680, 420)):
    return Track(track_id=track_id, box=box, score=score, source="fusion", hits=hits, confirmed=True)


def test_confirmed_track_posts_once_with_projected_position(manifest, server, link):
    httpx.post(f"http://127.0.0.1:{server.port}/internal/telemetry",
               json={"ts": int(time.time() * 1000), **POSE})
    assert wait_for(lambda: link.pose is not None)
    frame = np.full((720, 1280, 3), 80, np.uint8)

    young = _track(1, hits=2)
    link.offer([young], frame)
    track = _track(2)
    link.offer([track, young], frame)
    link.offer([track], frame)  # already posted: ignored
    assert wait_for(lambda: link.sent == 1)
    time.sleep(0.3)
    assert link.sent == 1

    victims = manifest.victims.list()
    assert len(victims) == 1 and victims[0]["modalities"] == "both"
    # Box centre (640, 360) on a nadir camera = straight below the UAV.
    assert geo.haversine_m(victims[0]["lat"], victims[0]["lon"], BASE_LAT, BASE_LON) < 1.0
    det = manifest.victims.detections(victims[0]["id"])[0]
    assert det["track_id"] == track.manifest_key and det["uav_alt_agl_m"] == 100


def test_reused_tracker_id_gets_a_new_key(manifest, server, link):
    """ByteTrack restarts ids after a reset; the server must not glue a new person onto an old victim."""
    link.config.fallback_lat, link.config.fallback_lon = BASE_LAT, BASE_LON
    frame = np.full((720, 1280, 3), 80, np.uint8)
    first, again = _track(7), _track(7)
    link.offer([first], frame)
    link.offer([again], frame)
    assert wait_for(lambda: link.sent == 2)
    assert first.manifest_key != again.manifest_key


def test_no_position_means_nothing_is_posted(manifest, server, link):
    link.offer([_track(3)], np.full((720, 1280, 3), 80, np.uint8))
    assert wait_for(lambda: link.dropped == 1)
    assert manifest.victims.list() == [] and link.status()[1] == "warn"


def test_disabled_link_ignores_tracks(manifest, link):
    link.set_enabled(False)
    link.offer([_track(4)], np.full((100, 100, 3), 80, np.uint8))
    time.sleep(0.3)
    assert link.sent == 0 and link.status() == ("Off", "off")
