"""Hand confirmed people to the SAR Manifest ground station (docs/ARCHITECTURE.md 7.3).

SkySight is the detection pipeline in the architecture: once a track has been held
for several frames above the confidence floor (the confirmation gate, spec 6.1) it
is posted once to ``POST /internal/detections`` on the ground station running on
this laptop. The ground station de-duplicates, alerts the rescue phones and owns
everything after that.

Geolocation: the newest UAV pose comes from the ground station
(``GET /internal/telemetry/latest``, fed by its MAVLink/NMEA reader). The box
centre is projected onto flat ground through a pinhole camera looking down along
the gimbal pitch. That is an approximation, so the reported ``geo_error_m`` grows
with altitude and obliqueness. With no fresh pose the configured fallback point
is used (e.g. a fixed camera mast), and without that nothing is posted.

Posting happens on a worker thread; ``offer`` only checks a few attributes per
track and copies a frame when something is actually sent.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

EARTH_R = 6_371_008.8
RETRY_LIMIT = 30      # x RETRY_DELAY_S = about a minute of ground-station downtime
RETRY_DELAY_S = 2.0
MODALITY = {"rgb": "rgb", "thermal": "thermal", "fusion": "fused", "fused": "fused"}


@dataclass
class LinkConfig:
    enabled: bool = False
    url: str = "http://127.0.0.1:8000"
    min_hits: int = 5            # tracker updates a track must survive before it is posted
    min_conf: float = 0.5
    camera_hfov_deg: float = 70.0
    pose_max_age_s: float = 3.0
    fallback_lat: float | None = None
    fallback_lon: float | None = None
    fallback_error_m: float = 50.0
    jpeg_quality: int = 85

    @classmethod
    def load(cls, path: Path) -> "LinkConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def project_to_ground(pose: dict, cx: float, cy: float, width: int, height: int, hfov_deg: float):
    """Pixel (cx, cy) -> (lat, lon, error_m) on flat ground below the UAV.

    Camera model: pinhole, optical axis tilted by the gimbal pitch (-90 = nadir),
    image top pointing along the UAV heading. Returns None if the ray misses the
    ground (pointing at or above the horizon).
    """
    alt = pose.get("alt_agl_m")
    if alt is None or alt <= 0:
        return None
    pitch = pose.get("gimbal_pitch_deg")
    pitch = -90.0 if pitch is None else float(pitch)
    heading = math.radians(pose.get("heading_deg") or 0.0)

    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)  # focal length in pixels
    # Ray in camera frame (x right, y down, z forward), then tilt about the x axis.
    x, y, z = cx - width / 2, cy - height / 2, f
    depression = math.radians(-pitch)  # angle of the optical axis below horizontal
    # Rotate into a level frame: forward (horizontal), right, down.
    fwd = z * math.cos(depression) - y * math.sin(depression)
    down = z * math.sin(depression) + y * math.cos(depression)
    if down <= 1e-6:
        return None
    t = alt / down
    forward_m, right_m = fwd * t, x * t
    north = forward_m * math.cos(heading) - right_m * math.sin(heading)
    east = forward_m * math.sin(heading) + right_m * math.cos(heading)

    lat = pose["lat"] + math.degrees(north / EARTH_R)
    lon = pose["lon"] + math.degrees(east / (EARTH_R * math.cos(math.radians(pose["lat"]))))
    # GPS (~3 m) + altitude/attitude error that scales with slant range.
    slant = math.sqrt(forward_m ** 2 + right_m ** 2 + alt ** 2)
    return lat, lon, round(3.0 + 0.08 * slant, 1)


class ManifestLink:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.config = LinkConfig.load(self.config_path)
        if not self.config_path.exists():
            self.config.save(self.config_path)
        self.sent = 0
        self.merged = 0
        self.dropped = 0
        self.last_error = ""
        self.reachable: bool | None = None
        self.pose: dict | None = None
        self._pose_rx = 0.0
        self._next_key = int(time.time() * 1000) * 1000  # unique across restarts
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="manifest-link", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ settings ---
    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def set_enabled(self, on: bool) -> None:
        self.config.enabled = bool(on)
        self.config.save(self.config_path)

    def status(self) -> tuple[str, str]:
        """(text, level) for the UI: level is ok / warn / off."""
        if not self.config.enabled:
            return "Off", "off"
        if self.reachable is False:
            return f"Ground station unreachable ({self.last_error})", "warn"
        if self.reachable is None:
            return "Connecting...", "warn"
        where = "UAV pose" if self._fresh_pose() else (
            "fallback position" if self.config.fallback_lat is not None else "NO POSITION - not sending")
        level = "ok" if where != "NO POSITION - not sending" else "warn"
        return f"Sent {self.sent} ({self.merged} merged) - {where}", level

    def _fresh_pose(self) -> bool:
        return self.pose is not None and time.monotonic() - self._pose_rx <= self.config.pose_max_age_s

    # --------------------------------------------------------------- offer ---
    def offer(self, tracks, image, scale=(1.0, 1.0), source_hint=None) -> None:
        """Call after each tracker step with the frame the boxes belong to."""
        if not self.config.enabled or image is None:
            return
        cfg = self.config
        for track in tracks:
            if not track.confirmed or getattr(track, "manifest_posted", False):
                continue
            if track.hits < cfg.min_hits or track.score < cfg.min_conf or track.missed > 0:
                continue
            if getattr(track, "manifest_key", None) is None:
                # Tracker ids restart when a tracker is rebuilt; the Track object does not survive that.
                track.manifest_key = self._next_key
                self._next_key += 1
            sx, sy = scale
            x1, y1, x2, y2 = (track.box[0] * sx, track.box[1] * sy, track.box[2] * sx, track.box[3] * sy)
            job = {
                "track_id": track.manifest_key, "frame_ts": int(time.time() * 1000),
                "modality": MODALITY.get(source_hint or track.source, "rgb"),
                "confidence": float(min(1.0, max(0.0, track.score))),
                "box": (x1, y1, x2, y2), "image": image.copy(),
            }
            try:
                self._queue.put_nowait(job)
                track.manifest_posted = True
            except queue.Full:
                self.dropped += 1

    # -------------------------------------------------------------- worker ---
    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _request(self, method, path, body=None, timeout=5):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.config.url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def _refresh_pose(self) -> None:
        try:
            res = self._request("GET", "/internal/telemetry/latest", timeout=2)
            self.reachable = True
            pose = res.get("pose")
            if pose and pose.get("ts") and res.get("server_time", 0) - pose["ts"] <= self.config.pose_max_age_s * 1000:
                self.pose, self._pose_rx = pose, time.monotonic()
        except (OSError, ValueError) as exc:
            self.reachable = False
            self.last_error = getattr(exc, "reason", None) or str(exc)

    def _locate(self, cx, cy, w, h):
        if self._fresh_pose():
            hit = project_to_ground(self.pose, cx, cy, w, h, self.config.camera_hfov_deg)
            if hit:
                return (*hit, self.pose)
        if self.config.fallback_lat is not None and self.config.fallback_lon is not None:
            return self.config.fallback_lat, self.config.fallback_lon, self.config.fallback_error_m, None
        return None

    def _run(self) -> None:
        last_pose_poll = 0.0
        while not self._stop.is_set():
            if self.config.enabled and time.monotonic() - last_pose_poll >= 1.0:
                last_pose_poll = time.monotonic()
                self._refresh_pose()
            try:
                job = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._send(job)
            except Exception as exc:  # never let a bad frame kill the link
                self.last_error = str(exc)
                log.exception("manifest post failed")

    def _send(self, job) -> None:
        img = job["image"]
        h, w = img.shape[:2]
        x1, y1, x2, y2 = job["box"]
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(math.ceil(x2))), min(h, int(math.ceil(y2)))
        if x2 <= x1 or y2 <= y1:
            return
        where = self._locate((x1 + x2) / 2, (y1 + y2) / 2, w, h)
        if where is None:
            self.dropped += 1
            return
        lat, lon, err, pose = where
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality])
        if not ok:
            return
        body = {
            "track_id": job["track_id"], "frame_ts": job["frame_ts"], "modality": job["modality"],
            "confidence": job["confidence"], "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
            "lat": lat, "lon": lon, "geo_error_m": err,
            "images": {"frame_b64": base64.b64encode(buf.tobytes()).decode()},
        }
        if pose:
            body["uav"] = {k: pose.get(k) for k in ("lat", "lon", "alt_agl_m", "heading_deg", "gimbal_pitch_deg")}
        try:
            res = self._request("POST", "/internal/detections", body, timeout=10)
        except urllib.error.HTTPError as exc:
            self.reachable = True
            try:
                self.last_error = json.loads(exc.read()).get("message", str(exc))
            except ValueError:
                self.last_error = str(exc)
            self.dropped += 1
            return
        except OSError as exc:
            # Ground station down or restarting: keep the person and try again shortly.
            self.reachable = False
            self.last_error = getattr(exc, "reason", None) or str(exc)
            job["attempts"] = job.get("attempts", 0) + 1
            if job["attempts"] < RETRY_LIMIT and not self._stop.wait(RETRY_DELAY_S):
                try:
                    self._queue.put_nowait(job)
                    return
                except queue.Full:
                    pass
            self.dropped += 1
            return
        self.reachable = True
        self.sent += 1
        if not res.get("created_new_victim"):
            self.merged += 1
