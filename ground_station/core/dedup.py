"""Detection -> victim merging, so one person raises one alert (spec section 6).

``resolve`` decides where an incoming detection belongs; ``attach_fields`` works
out the victim update once the detection is linked. Both run inside the caller's
ingest transaction, so two detections of the same person cannot both create a
victim.
"""

from __future__ import annotations

from dataclasses import dataclass

from ground_station.core import geo
from ground_station.core.state_machine import DEDUP_ACTIVE
from ground_station.db import repo

SAME_TRACK = "same_track"
NEARBY = "nearby"
NEAR_RESCUER = "near_rescuer"
NEW = "new"

# Detection modality -> victim.modalities vocabulary.
MODALITY = {"rgb": "rgb", "thermal": "thermal", "fused": "both"}


@dataclass(frozen=True)
class Decision:
    kind: str
    victim_id: str | None = None
    distance_m: float | None = None
    team_id: str | None = None


# Largest victim position error assumed when sizing the SQL bounding box.
_VICTIM_ERROR_CAP_M = 50.0


def merge_radius_m(settings, geo_error_m: float | None, victim_error_m: float | None = 0.0) -> float:
    """R = max(dedup_radius_min_m, dedup_error_multiplier x sigma).

    The spec uses sigma = geo_error_m of the new detection alone. We compare the new
    fix against the victim's *estimate*, so sigma is the error of their difference,
    sqrt(e_det^2 + e_victim^2). It falls back to the spec value as the victim's
    position converges, and stops the second sighting of a person splitting into a
    new victim (~13% of the time with the spec formula at default settings).
    """
    det = geo_error_m if geo_error_m is not None else geo.DEFAULT_GEO_ERROR_M
    vic = victim_error_m if victim_error_m is not None else geo.DEFAULT_GEO_ERROR_M
    sigma = (det * det + vic * vic) ** 0.5
    return max(settings.get("dedup_radius_min_m"), settings.get("dedup_error_multiplier") * sigma)


def resolve(conn, settings, mission_id: str, det, now_ms: int) -> Decision:
    # 1. Confirmation gate is pipeline-side. 2. Same track -> same victim.
    if det.track_id is not None:
        victim_id = repo.victim_for_track(conn, mission_id, det.track_id)
        if victim_id:
            return Decision(SAME_TRACK, victim_id)

    # 3. Nearest active victim within R, seen recently.
    since = det.frame_ts - int(settings.get("dedup_window_min") * 60_000)
    search = merge_radius_m(settings, det.geo_error_m, _VICTIM_ERROR_CAP_M)
    min_lat, max_lat, min_lon, max_lon = geo.bbox_deg(det.lat, det.lon, search)
    best = None
    for v in repo.victim_candidates(conn, mission_id, DEDUP_ACTIVE, since, min_lat, max_lat, min_lon, max_lon):
        d = geo.haversine_m(det.lat, det.lon, v["lat"], v["lon"])
        vic_err = v["position_error_m"]
        radius = merge_radius_m(settings, det.geo_error_m,
                                None if vic_err is None else min(vic_err, _VICTIM_ERROR_CAP_M))
        if d <= radius and (best is None or d < best[0]):
            best = (d, v["id"])
    if best:
        return Decision(NEARBY, best[1], distance_m=best[0])

    # 4. Rescuer filter: the drone is looking at one of our own teams.
    rescuer_r = settings.get("rescuer_radius_m")
    fresh_since = now_ms - int(settings.get("team_stale_after_s") * 1000)
    for team in repo.teams_with_fresh_fix(conn, fresh_since):
        d = geo.haversine_m(det.lat, det.lon, team["last_lat"], team["last_lon"])
        if d <= rescuer_r:
            return Decision(NEAR_RESCUER, distance_m=d, team_id=team["id"])

    # 5. Somebody new.
    return Decision(NEW)


def merge_modalities(current: str | None, incoming_modality: str) -> str:
    new = MODALITY[incoming_modality]
    if current is None or current == new:
        return new
    return "both"


def attach_fields(conn, victim, det, detection_id: str) -> dict:
    """Victim column updates after ``detection_id`` has been linked to ``victim``."""
    obs = [
        (d["lat"], d["lon"], d["confidence"], d["geo_error_m"])
        for d in repo.detections_for_victim(conn, victim["id"])
    ]
    lat, lon, err = geo.merge_fixes(obs)
    fields = {
        "lat": lat,
        "lon": lon,
        "position_error_m": round(err, 2),
        "sighting_count": victim["sighting_count"] + 1,
        "last_seen_at": max(victim["last_seen_at"], det.frame_ts),
        "modalities": merge_modalities(victim["modalities"], det.modality),
    }
    if victim["max_confidence"] is None or det.confidence > victim["max_confidence"]:
        fields["max_confidence"] = det.confidence
        fields["best_detection_id"] = detection_id
    return fields
