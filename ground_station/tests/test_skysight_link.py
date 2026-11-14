"""SkySight -> ground station hand-off (core/manifest_link.py) against a real server."""

import random
import socket
import struct
import time

import httpx
import numpy as np
import pytest

from core.manifest_link import (LinkConfig, ManifestLink, parse_pmtiles_header, project_to_ground,
                                random_point_in_area)
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


# -------------------------------------------------------- simulated position ---
MAP_BOUNDS = (79.16416, 5.621275, 82.64612, 10.0715299)  # the Sri Lanka build


def pmtiles_header(bounds=MAP_BOUNDS, centre=(BASE_LON, BASE_LAT)) -> bytes:
    raw = bytearray(127)
    raw[:8] = b"PMTiles\x03"
    struct.pack_into("<4i", raw, 102, *(round(v * 1e7) for v in bounds))
    raw[118] = 7
    struct.pack_into("<2i", raw, 119, *(round(v * 1e7) for v in centre))
    return bytes(raw)


def test_pmtiles_header_gives_bounds_and_centre():
    area = parse_pmtiles_header(pmtiles_header(), "srilanka.pmtiles")
    assert area.bounds == pytest.approx(MAP_BOUNDS) and area.file == "srilanka.pmtiles"
    assert (area.center_lat, area.center_lon) == pytest.approx((BASE_LAT, BASE_LON))
    with pytest.raises(ValueError):
        parse_pmtiles_header(b"not a map" + bytes(200))


def test_random_points_stay_in_radius_and_bounds():
    rng = random.Random(1)
    points = [random_point_in_area(BASE_LAT, BASE_LON, 1000, MAP_BOUNDS, rng) for _ in range(300)]
    assert all(geo.haversine_m(BASE_LAT, BASE_LON, lat, lon) <= 1000.5 for lat, lon in points)
    assert len({(round(lat, 5), round(lon, 5)) for lat, lon in points}) == 300
    # Centre on the map's western edge: nothing falls off the map.
    edge = [random_point_in_area(BASE_LAT, MAP_BOUNDS[0], 5000, MAP_BOUNDS, rng) for _ in range(100)]
    assert all(lon >= MAP_BOUNDS[0] for _, lon in edge)


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


# ------------------------------------------------------------- filed people ---
@pytest.fixture
def people_link(server, tmp_path):
    """A link with auto-send off: filed people go to the ground station regardless."""
    path = tmp_path / "people_link.json"
    LinkConfig(enabled=False, url=f"http://127.0.0.1:{server.port}", sim_radius_m=800).save(path)
    results = []
    lk = ManifestLink(path, on_person_result=lambda pid, res: results.append((pid, res)))
    lk.results = results
    yield lk
    lk.close()


def install_map(manifest, centre=(BASE_LON, BASE_LAT)):
    path = manifest.paths.map_file(manifest.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pmtiles_header(centre=centre) + bytes(4096))
    return path


def _crop():
    return np.full((120, 60, 3), 90, np.uint8)


def test_filed_person_gets_a_random_map_position(manifest, people_link):
    install_map(manifest)
    assert people_link.submit_person("P-0001", people_link.new_key(), _crop(), "rgb", 0.74)
    assert not people_link.submit_person("P-0001", people_link.new_key(), _crop())  # already queued
    assert wait_for(lambda: len(people_link.results) == 1, timeout=10)

    pid, res = people_link.results[0]
    assert pid == "P-0001" and res["ok"] and not res["merged"]
    victims = manifest.victims.list()
    assert len(victims) == 1 and victims[0]["id"] == res["victim_id"]
    assert (victims[0]["lat"], victims[0]["lon"]) == pytest.approx((res["lat"], res["lon"]))
    assert geo.haversine_m(BASE_LAT, BASE_LON, res["lat"], res["lon"]) <= 800.5
    assert people_link.map_area.file == "srilanka.pmtiles"
    assert people_link.status(people=(1, 1))[1] == "ok"


def test_people_spread_over_the_area_and_retries_merge(manifest, people_link):
    install_map(manifest)
    keys = [people_link.new_key() for _ in range(4)]
    for i, key in enumerate(keys):
        assert people_link.submit_person(f"P-{i:04d}", key, _crop())
    assert wait_for(lambda: len(people_link.results) == 4, timeout=10)
    assert len(manifest.victims.list()) == 4  # random positions: nobody merged

    # A retry (e.g. the answer was lost) reuses the stored key and position: same victim.
    first = dict(people_link.results)["P-0000"]
    assert people_link.submit_person("P-0000", keys[0], _crop(), lat=first["lat"], lon=first["lon"])
    assert wait_for(lambda: len(people_link.results) == 5, timeout=10)
    again = people_link.results[-1][1]
    assert again["victim_id"] == first["victim_id"] and again["merged"]
    assert len(manifest.victims.list()) == 4


def test_configured_centre_overrides_the_map_centre(manifest, people_link):
    people_link.config.sim_center_lat, people_link.config.sim_center_lon = 6.7951, 79.9009
    assert people_link.submit_person("P-0001", people_link.new_key(), _crop())
    assert wait_for(lambda: people_link.results, timeout=10)
    res = people_link.results[0][1]
    assert res["ok"] and geo.haversine_m(6.7951, 79.9009, res["lat"], res["lon"]) <= 800.5


def test_person_waits_without_a_map_or_mission(manifest, people_link):
    assert people_link.submit_person("P-0001", people_link.new_key(), _crop())
    assert wait_for(lambda: people_link.results, timeout=10)
    assert not people_link.results[0][1]["ok"] and "no position" in people_link.results[0][1]["error"]

    install_map(manifest)
    people_link._map_checked = float("-inf")  # do not wait out the 10 s re-check in a test
    manifest.missions.stop()
    assert people_link.submit_person("P-0001", people_link.new_key(), _crop())
    assert wait_for(lambda: len(people_link.results) == 2, timeout=10)
    res = people_link.results[1][1]
    assert not res["ok"] and "lat" in res  # the drawn position is kept for the retry
    assert people_link.status(people=(0, 1)) == (
        f"Database: 0/1 people on ground station\nWaiting: {res['error']}", "warn")
    assert manifest.victims.list() == []
