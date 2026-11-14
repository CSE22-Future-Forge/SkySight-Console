"""Small-area geodesy: haversine distance, bearing and the weighted fix merge.

Everything works in WGS84 decimal degrees and metres. Search areas are a few
kilometres across, so a spherical earth is far more accurate than the GPS fixes
it is fed with.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_008.8

# Used when a detection arrives without a geolocation error estimate.
DEFAULT_GEO_ERROR_M = 10.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, 0..360 clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Point reached from (lat, lon) after distance_m along bearing (degrees)."""
    d = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def bbox_deg(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) enclosing a circle — the SQL prefilter."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    coslat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = math.degrees(radius_m / (EARTH_RADIUS_M * coslat))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def merge_fixes(obs: list[tuple[float, float, float, float | None]]) -> tuple[float, float, float]:
    """Merge (lat, lon, confidence, geo_error_m) sightings into one position.

    Weight per spec section 6: w = confidence / max(geo_error_m, 1)^2.
    The reported error is the larger of the inverse-variance error (what the
    sightings claim) and their weighted spread around the mean (what they
    actually show), so disagreeing sightings widen the circle instead of
    pretending to be precise.
    """
    if not obs:
        raise ValueError("merge_fixes needs at least one observation")
    weights, errs = [], []
    for _lat, _lon, conf, err in obs:
        e = max(err if err is not None else DEFAULT_GEO_ERROR_M, 1.0)
        errs.append(e)
        weights.append(max(conf, 1e-3) / (e * e))
    total = sum(weights)
    lat = sum(w * o[0] for w, o in zip(weights, obs)) / total
    lon = sum(w * o[1] for w, o in zip(weights, obs)) / total

    inverse_variance = 1.0 / math.sqrt(sum(1.0 / (e * e) for e in errs))
    spread = math.sqrt(
        sum(w * haversine_m(lat, lon, o[0], o[1]) ** 2 for w, o in zip(weights, obs)) / total
    )
    return lat, lon, max(inverse_variance, spread)
