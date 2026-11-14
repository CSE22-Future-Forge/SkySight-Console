"""Geo, state machine and de-duplication (spec sections 5 and 6)."""

import pytest

from ground_station.core import geo
from ground_station.core import state_machine as sm
from ground_station.db import repo

from .conftest import BASE_LAT, BASE_LON, add_device, ingest


# ----------------------------------------------------------------------- geo ---
def test_haversine_one_degree_latitude():
    assert geo.haversine_m(7.0, 80.0, 8.0, 80.0) == pytest.approx(111_195, rel=1e-3)


def test_destination_round_trip():
    lat, lon = geo.destination(BASE_LAT, BASE_LON, 45, 250)
    assert geo.haversine_m(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(250, abs=0.1)
    assert geo.bearing_deg(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(45, abs=0.1)


def test_merge_fixes_prefers_precise_confident_sightings():
    near = geo.destination(BASE_LAT, BASE_LON, 90, 10)
    lat, lon, err = geo.merge_fixes([(BASE_LAT, BASE_LON, 0.9, 2.0), (near[0], near[1], 0.3, 20.0)])
    assert geo.haversine_m(lat, lon, BASE_LAT, BASE_LON) < 1.0
    assert err >= 1.0


def test_merge_fixes_disagreement_widens_error():
    far = geo.destination(BASE_LAT, BASE_LON, 0, 40)
    _, _, err = geo.merge_fixes([(BASE_LAT, BASE_LON, 0.8, 3.0), (far[0], far[1], 0.8, 3.0)])
    assert err > 15  # spread (~20 m) beats the 2.1 m inverse-variance claim


# ------------------------------------------------------------- state machine ---
@pytest.mark.parametrize("frm,to,actor", [
    (sm.OPEN, sm.ONGOING, sm.TEAM), (sm.ONGOING, sm.COMPLETED, sm.TEAM), (sm.ONGOING, sm.OPEN, sm.TEAM),
    (sm.PENDING, sm.OPEN, sm.COORDINATOR), (sm.COMPLETED, sm.ONGOING, sm.COORDINATOR),
    (sm.DISMISSED, sm.OPEN, sm.COORDINATOR),
])
def test_allowed_transitions(frm, to, actor):
    assert sm.allowed(frm, to, actor)


@pytest.mark.parametrize("frm,to,actor", [
    (sm.PENDING, sm.OPEN, sm.TEAM), (sm.OPEN, sm.COMPLETED, sm.TEAM), (sm.COMPLETED, sm.ONGOING, sm.TEAM),
    (sm.DISMISSED, sm.ONGOING, sm.COORDINATOR), (sm.OPEN, sm.DISMISSED, sm.TEAM),
])
def test_rejected_transitions(frm, to, actor):
    assert not sm.allowed(frm, to, actor)
    with pytest.raises(sm.InvalidTransition):
        sm.check(frm, to, actor)


# ------------------------------------------------------------------- de-dup ---
def test_new_detection_creates_open_victim(manifest):
    r = ingest(manifest, track_id=1)
    assert r["created_new_victim"] and not r["near_rescuer"]
    v = manifest.victims.get(r["victim_id"])
    assert v["status"] == "open" and v["display_code"] == "V-0001" and v["modalities"] == "both"
    assert (manifest.paths.images / manifest.mission["id"] / f"{r['detection_id']}_thumb.jpg").is_file()


def test_same_track_attaches_even_when_far(manifest):
    first = ingest(manifest, track_id=7)
    far = geo.destination(BASE_LAT, BASE_LON, 0, 500)
    second = ingest(manifest, track_id=7, lat=far[0], lon=far[1])
    assert second["victim_id"] == first["victim_id"] and second["dedup"] == "same_track"
    assert manifest.victims.get(first["victim_id"])["sighting_count"] == 2


def test_nearby_detection_attaches_and_merges_position(manifest):
    first = ingest(manifest, track_id=1, geo_error_m=5)
    near = geo.destination(BASE_LAT, BASE_LON, 90, 8)
    second = ingest(manifest, track_id=2, lat=near[0], lon=near[1], geo_error_m=5, confidence=0.95,
                    modality="rgb")
    assert second["victim_id"] == first["victim_id"] and second["dedup"] == "nearby"
    v = manifest.victims.get(first["victim_id"])
    assert v["sighting_count"] == 2 and v["max_confidence"] == 0.95
    assert v["best_detection_id"] == second["detection_id"]
    assert 3 < geo.haversine_m(BASE_LAT, BASE_LON, v["lat"], v["lon"]) < 5  # weighted towards the 0.95 fix


def test_far_detection_creates_second_victim(manifest):
    ingest(manifest, track_id=1)
    far = geo.destination(BASE_LAT, BASE_LON, 180, 60)
    r = ingest(manifest, track_id=2, lat=far[0], lon=far[1])
    assert r["created_new_victim"]
    assert manifest.victims.get(r["victim_id"])["display_code"] == "V-0002"


def test_radius_grows_with_geo_error(manifest):
    ingest(manifest, track_id=1)
    off = geo.destination(BASE_LAT, BASE_LON, 0, 35)
    assert ingest(manifest, track_id=2, lat=off[0], lon=off[1], geo_error_m=20)["dedup"] == "nearby"


def test_radius_includes_victim_uncertainty(manifest):
    # Spec formula alone: R = max(15, 2 x 8) = 16 m, so a 20 m re-sighting would split.
    # Against a victim that is itself +-8 m: R = 2 x sqrt(8^2 + 8^2) = 22.6 m -> merge.
    ingest(manifest, track_id=1, geo_error_m=8)
    off = geo.destination(BASE_LAT, BASE_LON, 90, 20)
    assert ingest(manifest, track_id=2, lat=off[0], lon=off[1], geo_error_m=8)["dedup"] == "nearby"
    far = geo.destination(BASE_LAT, BASE_LON, 270, 40)
    assert ingest(manifest, track_id=3, lat=far[0], lon=far[1], geo_error_m=8)["created_new_victim"]


def test_dedup_window_expires(manifest):
    old_ts = 1_700_000_000_000
    ingest(manifest, track_id=1, frame_ts=old_ts)
    r = ingest(manifest, track_id=2, frame_ts=old_ts + 16 * 60_000)
    assert r["created_new_victim"]


def test_dismissed_victims_do_not_absorb_new_detections(manifest):
    first = ingest(manifest, track_id=1)
    manifest.victims.dismiss(first["victim_id"])
    assert ingest(manifest, track_id=2)["created_new_victim"]


def test_rescuer_filter_suppresses_alert(manifest):
    device, _ = add_device(manifest, manifest.alpha)
    manifest.teams.add_locations(device, [{"client_point_id": "p-000001", "lat": BASE_LAT, "lon": BASE_LON,
                                           "accuracy_m": 5, "fix_ts": 1}])
    near = geo.destination(BASE_LAT, BASE_LON, 0, 10)
    r = ingest(manifest, track_id=9, lat=near[0], lon=near[1])
    assert r["near_rescuer"] and r["victim_id"] is None
    assert repo.count_near_rescuer(manifest.db.conn(), manifest.mission["id"]) == 1
    assert manifest.victims.list() == []


def test_verify_mode_creates_pending_laptop_only(manifest):
    manifest.missions.set_alert_mode("verify")
    r = ingest(manifest, track_id=1)
    assert manifest.victims.get(r["victim_id"])["status"] == "pending"
    rows = manifest.db.conn().execute("SELECT type, laptop_only FROM events WHERE entity_id = ?",
                                      (r["victim_id"],)).fetchall()
    assert [(row["type"], row["laptop_only"]) for row in rows] == [("victim.created", 1)]

    manifest.victims.verify(r["victim_id"])
    rows = manifest.db.conn().execute(
        "SELECT type, laptop_only, scope_team_id FROM events WHERE entity_id = ? ORDER BY seq",
        (r["victim_id"],)).fetchall()
    assert (rows[-1]["type"], rows[-1]["laptop_only"], rows[-1]["scope_team_id"]) == ("victim.created", 0, None)


def test_mission_archive_zips_records_and_can_delete_images(manifest):
    import json
    import zipfile

    from ground_station.core.services import ServiceError

    r = ingest(manifest, track_id=1)
    with pytest.raises(ServiceError):  # still running
        manifest.missions.archive(manifest.mission["id"])
    manifest.missions.stop()
    path = manifest.missions.archive(manifest.mission["id"], delete_images=True)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        data = json.loads(zf.read("manifest.json"))
    assert f"images/{r['detection_id']}_thumb.jpg" in names
    assert [v["display_code"] for v in data["victims"]] == ["V-0001"]
    assert len(data["status_history"]) == 1 and data["mission"]["name"] == "Test mission"
    assert not (manifest.paths.images / manifest.mission["id"]).exists()


def test_manual_victim(manifest):
    v = manifest.victims.add_manual(BASE_LAT, BASE_LON, note="seen from road")
    assert v["source"] == "manual" and v["status"] == "open" and v["sighting_count"] == 0
