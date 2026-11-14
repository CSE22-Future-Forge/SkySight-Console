"""Emulate the detection pipeline: post detections (+ UAV telemetry) to the ground station.

Scatters N simulated people around a centre point and "flies over" them, posting
confirmed detections to /internal/detections the way SkySight would. Revisits
re-detect people already seen (same track, or a new track with GPS jitter) so the
de-duplication can be exercised.

    python tools/mock_feed.py                                  # 8 people around Kandy
    python tools/mock_feed.py --count 30 --interval 0.5 --revisit 0.6 --telemetry
    python tools/mock_feed.py --images D:/flights/frames       # real frames instead of synthetic

Only talks to localhost: /internal/* refuses any other client.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

EARTH_R = 6_371_008.8


def offset(lat, lon, north_m, east_m):
    return (lat + math.degrees(north_m / EARTH_R),
            lon + math.degrees(east_m / (EARTH_R * math.cos(math.radians(lat)))))


def post(url, body, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def synthetic_frame(rng, w=1280, h=720):
    """Grey 'terrain' with one warm person-like blob; returns (jpeg_b64, bbox)."""
    img = np.full((h, w, 3), 70, np.uint8)
    img += rng.integers(0, 25, (h, w, 1), dtype=np.uint8)
    pw, ph = int(rng.integers(14, 30)), int(rng.integers(30, 60))
    x, y = int(rng.integers(50, w - 50 - pw)), int(rng.integers(50, h - 50 - ph))
    cv2.ellipse(img, (x + pw // 2, y + ph // 5), (pw // 3, ph // 6), 0, 0, 360, (190, 200, 230), -1)
    cv2.rectangle(img, (x, y + ph // 4), (x + pw, y + ph), (60, 90, 200), -1)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode(), {"x": x, "y": y, "w": pw, "h": ph}


def image_frame(rng, files):
    path = files[int(rng.integers(len(files)))]
    img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    bw, bh = max(12, w // 40), max(24, h // 15)
    x, y = int(rng.integers(0, w - bw)), int(rng.integers(0, h - bh))
    ok, buf = cv2.imencode(".jpg", img)
    return base64.b64encode(buf.tobytes()).decode(), {"x": x, "y": y, "w": bw, "h": bh}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--center", default="7.2906,80.6337", help="lat,lon (default Kandy)")
    p.add_argument("--spread", type=float, default=400, help="people scattered within this many metres")
    p.add_argument("--people", type=int, default=8, help="simulated people on the ground")
    p.add_argument("--count", type=int, default=12, help="detections to post")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between detections")
    p.add_argument("--revisit", type=float, default=0.4, help="probability a detection re-sees a known person")
    p.add_argument("--geo-error", type=float, default=8.0, help="simulated geolocation error (m, 1 sigma)")
    p.add_argument("--images", help="folder of real frames (.jpg/.png) to use instead of synthetic ones")
    p.add_argument("--telemetry", action="store_true", help="also post UAV pose at 1 Hz")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)
    clat, clon = (float(v) for v in args.center.split(","))
    people = [offset(clat, clon, *rng.uniform(-args.spread, args.spread, 2)) for _ in range(args.people)]
    files = sorted(f for f in Path(args.images).iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png")) \
        if args.images else []
    if args.images and not files:
        print(f"no images in {args.images}", file=sys.stderr)
        return 1

    status, health = 0, None
    try:
        with urllib.request.urlopen(f"{args.url}/api/v1/health", timeout=5) as r:
            health = json.loads(r.read())
    except OSError as exc:
        print(f"ground station not reachable at {args.url}: {exc}", file=sys.stderr)
        return 1
    if not health.get("mission"):
        print("no active mission on the ground station - start one first", file=sys.stderr)
        return 1
    print(f"mission '{health['mission']['name']}' - posting {args.count} detections")

    seen: dict[int, int] = {}  # person index -> track id
    next_track, heading = 1, 0.0
    for n in range(args.count):
        if seen and rng.random() < args.revisit:
            idx = int(rng.choice(list(seen)))
            # A revisit is either the tracker still holding the person or a new pass (new track id).
            track = seen[idx] if rng.random() < 0.5 else next_track
        else:
            idx = int(rng.integers(len(people)))
            track = next_track
        if track == next_track:
            next_track += 1
        seen[idx] = track

        # --geo-error is the radial RMS error, so each axis gets error / sqrt(2).
        lat, lon = offset(*people[idx], *rng.normal(0, args.geo_error / math.sqrt(2), 2))
        frame_b64, bbox = image_frame(rng, files) if files else synthetic_frame(rng)
        uav_lat, uav_lon = offset(lat, lon, *rng.uniform(-30, 30, 2))
        heading = (heading + rng.uniform(-20, 20)) % 360
        body = {
            "track_id": track, "frame_ts": int(time.time() * 1000),
            "modality": random.choice(["fused", "rgb", "thermal"]),
            "confidence": round(float(rng.uniform(0.55, 0.97)), 3), "bbox": bbox, "lat": lat, "lon": lon,
            "geo_error_m": round(max(1.0, float(rng.normal(args.geo_error, 1))), 1),
            "uav": {"lat": uav_lat, "lon": uav_lon, "alt_agl_m": 60, "heading_deg": heading, "gimbal_pitch_deg": -90},
            "weather_tag": random.choice(["clear", "rain", "haze", "night"]),
            "images": {"frame_b64": frame_b64},
        }
        t0 = time.perf_counter()
        status, res = post(f"{args.url}/internal/detections", body)
        ms = (time.perf_counter() - t0) * 1000
        if status == 200:
            tag = "NEW   " if res["created_new_victim"] else ("RESCUER" if res["near_rescuer"] else "MERGED")
            print(f"[{n + 1:3d}] {tag} person#{idx} track {track:3d} -> victim {str(res['victim_id'])[:8]} "
                  f"({res['dedup']}, {ms:.0f} ms)")
        else:
            print(f"[{n + 1:3d}] HTTP {status}: {res}", file=sys.stderr)

        if args.telemetry:
            steps = max(1, int(args.interval))
            for s in range(steps):
                post(f"{args.url}/internal/telemetry", {
                    "ts": int(time.time() * 1000), "lat": uav_lat, "lon": uav_lon, "alt_agl_m": 60,
                    "alt_msl_m": 560, "heading_deg": heading, "ground_speed_mps": 8, "battery_pct": 90 - n,
                    "gimbal_pitch_deg": -90,
                })
                time.sleep(args.interval / steps)
        elif n + 1 < args.count:
            time.sleep(args.interval)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
