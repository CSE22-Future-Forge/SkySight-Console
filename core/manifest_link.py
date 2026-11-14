"""Hand confirmed people to the SAR Manifest ground station (docs/ARCHITECTURE.md 7.3).

SkySight is the detection pipeline in the architecture: once a track has been held
for several frames above the confidence floor (the confirmation gate, spec 6.1) it
is posted once to ``POST /internal/detections`` on the ground station running on
this laptop. The ground station de-duplicates, alerts the rescue phones and owns
everything after that.

People the operator files from CAPTURE FINDINGS are posted too, through
``submit_person``, whatever the auto-send switch says: the person database is the
operator's roster and every entry in it belongs on the ground station.

Geolocation: the newest UAV pose comes from the ground station
(``GET /internal/telemetry/latest``, fed by its MAVLink/NMEA reader). The box
centre is projected onto flat ground through a pinhole camera looking down along
the gimbal pitch. That is an approximation, so the reported ``geo_error_m`` grows
with altitude and obliqueness. With no fresh pose the configured fallback point
is used (e.g. a fixed camera mast). Without that, and while ``simulate_position``
is on (the prototype has no UAV), each person gets a random point in a search
area on the ground station's offline map: its bounds and centre come from the
.pmtiles header served under ``/maps/``. With none of these nothing is posted.

Posting happens on a worker thread; ``offer`` only checks a few attributes per
track and copies a frame when something is actually sent.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import queue
import random
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2

log = logging.getLogger(__name__)

EARTH_R = 6_371_008.8
RETRY_LIMIT = 30      # x RETRY_DELAY_S = about a minute of ground-station downtime
RETRY_DELAY_S = 2.0
MODALITY = {"rgb": "rgb", "thermal": "thermal", "fusion": "fused", "fused": "fused"}
PMTILES_HEADER_LEN = 127
MAP_AREA_REFRESH_S = 300.0


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
    # No UAV yet: random positions in a search area on the offline map.
    simulate_position: bool = True
    sim_center_lat: float | None = None   # None = the map's own centre
    sim_center_lon: float | None = None
    sim_radius_m: float = 1000.0
    sim_error_m: float = 5.0
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


@dataclass
class MapArea:
    """Coverage of an offline map: bounds as (west, south, east, north) and its centre."""

    file: str
    bounds: tuple[float, float, float, float]
    center_lat: float
    center_lon: float


def parse_pmtiles_header(raw: bytes, file: str = "") -> MapArea:
    """Bounds and centre from the fixed 127-byte PMTiles v3 header (all values are degrees x 1e7)."""
    if len(raw) < PMTILES_HEADER_LEN or not raw.startswith(b"PMTiles") or raw[7] != 3:
        raise ValueError("not a PMTiles v3 header")
    west, south, east, north = (v / 1e7 for v in struct.unpack_from("<4i", raw, 102))
    center_lon, center_lat = (v / 1e7 for v in struct.unpack_from("<2i", raw, 119))
    if not (west < east and south < north):
        raise ValueError("map header has empty bounds")
    if not (west <= center_lon <= east and south <= center_lat <= north):
        center_lon, center_lat = (west + east) / 2, (south + north) / 2
    return MapArea(file, (west, south, east, north), center_lat, center_lon)


def random_point_in_area(lat: float, lon: float, radius_m: float, bounds=None, rng=random):
    """Uniform random point within ``radius_m`` of (lat, lon), kept inside ``bounds`` if given."""
    w, s, e, n = bounds or (-180.0, -90.0, 180.0, 90.0)
    for _ in range(50):
        r = radius_m * math.sqrt(rng.random())
        theta = rng.uniform(0.0, 2 * math.pi)
        p_lat = lat + math.degrees(r * math.cos(theta) / EARTH_R)
        p_lon = lon + math.degrees(r * math.sin(theta) / (EARTH_R * math.cos(math.radians(lat))))
        if s <= p_lat <= n and w <= p_lon <= e:
            return p_lat, p_lon
    return min(max(lat, s), n), min(max(lon, w), e)


class ManifestLink:
    def __init__(self, config_path: str | Path,
                 on_person_result: Callable[[str, dict], None] | None = None):
        self.config_path = Path(config_path)
        self.config = LinkConfig.load(self.config_path)
        self.config.save(self.config_path)  # writes defaults for keys added since the file was made
        # Called on the worker thread with (person_id, result) after each submit_person attempt.
        self.on_person_result = on_person_result
        self.sent = 0
        self.merged = 0
        self.dropped = 0
        self.people_sent = 0
        self.last_error = ""
        self.last_person_error = ""
        self.reachable: bool | None = None
        self.pose: dict | None = None
        self.map_area: MapArea | None = None
        self._map_checked = -math.inf
        self._pose_rx = 0.0
        self._rng = random.Random()
        self._lock = threading.Lock()
        self._pending_people: set[str] = set()
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

    def status(self, people: tuple[int, int] | None = None) -> tuple[str, str]:
        """(text, level) for the UI: level is ok / warn / off.

        ``people`` is (on ground station, total) for the operator's person database.
        """
        synced, total = people or (0, 0)
        waiting = total - synced
        lines = [f"Database: {synced}/{total} people on ground station"] if total else []
        if not self.config.enabled and not waiting:
            return "\n".join(lines + ["Auto-send off"]) if lines else "Off", "ok" if lines else "off"
        if self.reachable is False:
            return "\n".join(lines + [f"Ground station unreachable ({self.last_error})"]), "warn"
        if self.reachable is None:
            return "\n".join(lines + ["Connecting..."]), "warn"
        if waiting and self.last_person_error:
            return "\n".join(lines + [f"Waiting: {self.last_person_error}"]), "warn"
        where = self.position_mode()
        if self.config.enabled:
            lines.append(f"Auto-sent {self.sent} ({self.merged} merged)")
        lines.append(where or "NO POSITION - not sending")
        return "\n".join(lines), "ok" if where else "warn"

    def position_mode(self) -> str | None:
        """Where the next position comes from, for the UI; None if there is none."""
        cfg = self.config
        if self._fresh_pose():
            return "UAV pose"
        if cfg.fallback_lat is not None and cfg.fallback_lon is not None:
            return "fallback position"
        if cfg.simulate_position:
            if cfg.sim_center_lat is not None and cfg.sim_center_lon is not None:
                return f"Simulated: random within {cfg.sim_radius_m:.0f} m of {cfg.sim_center_lat:.4f}, {cfg.sim_center_lon:.4f}"
            if self.map_area is not None:
                return f"Simulated: random within {cfg.sim_radius_m:.0f} m of the map centre ({self.map_area.file})"
        return None

    def _fresh_pose(self) -> bool:
        return self.pose is not None and time.monotonic() - self._pose_rx <= self.config.pose_max_age_s

    def new_key(self) -> int:
        """A track_id the ground station has never seen (it merges repeats of one key into one victim)."""
        with self._lock:
            key = self._next_key
            self._next_key += 1
            return key

    # ------------------------------------------------------------- people ---
    def is_pending(self, person_id: str) -> bool:
        with self._lock:
            return person_id in self._pending_people

    def submit_person(self, person_id: str, key: int, image, source: str = "rgb", score: float = 0.0,
                      lat: float | None = None, lon: float | None = None,
                      captured_ms: int | None = None) -> bool:
        """Queue one filed person for the ground station; False if it is already queued or cannot be.

        ``key`` should be stored with the person: a retry with the same key is merged into the
        same victim. A stored ``lat``/``lon`` is reused, so a person keeps one position.
        """
        if image is None or not getattr(image, "size", 0):
            return False
        with self._lock:
            if person_id in self._pending_people:
                return False
            self._pending_people.add(person_id)
        h, w = image.shape[:2]
        job = {
            "kind": "person", "person_id": person_id, "track_id": int(key),
            "frame_ts": captured_ms or int(time.time() * 1000),
            "modality": MODALITY.get(source, "rgb"), "confidence": float(min(1.0, max(0.0, score))),
            "box": (0, 0, w, h), "image": image.copy(), "lat": lat, "lon": lon,
        }
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            with self._lock:
                self._pending_people.discard(person_id)
            return False
        return True

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
                track.manifest_key = self.new_key()
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

    def _refresh_map_area(self) -> None:
        """Read the offline map's bounds and centre from the ground station (header bytes only)."""
        self._map_checked = time.monotonic()
        try:
            name = self._request("GET", "/maps/manifest", timeout=3)["file"]
            req = urllib.request.Request(
                f"{self.config.url.rstrip('/')}/maps/{urllib.parse.quote(name)}",
                headers={"Range": f"bytes=0-{PMTILES_HEADER_LEN - 1}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read(PMTILES_HEADER_LEN)
            self.map_area = parse_pmtiles_header(raw, name)
        except urllib.error.HTTPError:
            self.map_area = None  # 404: no map installed on the ground station
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.debug("map area unavailable: %s", exc)

    def _ensure_map_area(self) -> None:
        if not self.config.simulate_position:
            return
        age = time.monotonic() - self._map_checked
        if age >= MAP_AREA_REFRESH_S or (self.map_area is None and age >= 10.0):
            self._refresh_map_area()

    def _simulated_point(self):
        cfg = self.config
        if not cfg.simulate_position:
            return None
        self._ensure_map_area()
        area = self.map_area
        if cfg.sim_center_lat is not None and cfg.sim_center_lon is not None:
            centre = cfg.sim_center_lat, cfg.sim_center_lon
        elif area is not None:
            centre = area.center_lat, area.center_lon
        else:
            return None
        lat, lon = random_point_in_area(*centre, cfg.sim_radius_m, area.bounds if area else None, self._rng)
        return lat, lon, cfg.sim_error_m, None

    def _locate(self, cx, cy, w, h):
        if self._fresh_pose():
            hit = project_to_ground(self.pose, cx, cy, w, h, self.config.camera_hfov_deg)
            if hit:
                return (*hit, self.pose)
        if self.config.fallback_lat is not None and self.config.fallback_lon is not None:
            return self.config.fallback_lat, self.config.fallback_lon, self.config.fallback_error_m, None
        return self._simulated_point()

    def _run(self) -> None:
        last_pose_poll = 0.0
        while not self._stop.is_set():
            busy = self.config.enabled or self._pending_people
            if busy and time.monotonic() - last_pose_poll >= 1.0:
                last_pose_poll = time.monotonic()
                self._refresh_pose()
                if self.reachable:
                    self._ensure_map_area()
            try:
                job = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if job.get("kind") == "person":
                    self._send_person(job)
                else:
                    self._send(job)
            except Exception as exc:  # never let a bad frame kill the link
                self.last_error = str(exc)
                log.exception("manifest post failed")

    def _body(self, job, box, lat, lon, err, pose) -> dict | None:
        ok, buf = cv2.imencode(".jpg", job["image"], [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality])
        if not ok:
            return None
        x1, y1, x2, y2 = box
        body = {
            "track_id": job["track_id"], "frame_ts": job["frame_ts"], "modality": job["modality"],
            "confidence": job["confidence"], "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
            "lat": lat, "lon": lon, "geo_error_m": err,
            "images": {"frame_b64": base64.b64encode(buf.tobytes()).decode()},
        }
        if pose:
            body["uav"] = {k: pose.get(k) for k in ("lat", "lon", "alt_agl_m", "heading_deg", "gimbal_pitch_deg")}
        return body

    @staticmethod
    def _http_message(exc: urllib.error.HTTPError) -> str:
        try:
            return json.loads(exc.read()).get("message", str(exc))
        except ValueError:
            return str(exc)

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
        body = self._body(job, (x1, y1, x2, y2), *where)
        if body is None:
            return
        try:
            res = self._request("POST", "/internal/detections", body, timeout=10)
        except urllib.error.HTTPError as exc:
            self.reachable = True
            self.last_error = self._http_message(exc)
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

    def _send_person(self, job) -> None:
        """Post one filed person. The result goes to ``on_person_result``; failures are retried by the caller."""
        result: dict = {"ok": False}
        try:
            img = job["image"]
            h, w = img.shape[:2]
            if job["lat"] is not None and job["lon"] is not None:
                where = job["lat"], job["lon"], self.config.sim_error_m, None
            else:
                where = self._locate(w / 2, h / 2, w, h)
            if where is None:
                result["error"] = "no position (no UAV pose, fallback or offline map)"
                return
            lat, lon = where[0], where[1]
            result.update(lat=lat, lon=lon)  # kept even on failure, so a retry lands in the same place
            body = self._body(job, (0, 0, w, h), *where)
            if body is None:
                result["error"] = "could not encode the capture image"
                return
            res = self._request("POST", "/internal/detections", body, timeout=10)
            self.reachable = True
            if res.get("victim_id"):
                self.people_sent += 1
                result.update(ok=True, victim_id=res["victim_id"], merged=not res.get("created_new_victim"))
            else:
                # Filtered as one of our own rescuers; a new position is drawn next time.
                result.pop("lat")
                result.pop("lon")
                result["error"] = "position fell next to a rescue team"
        except urllib.error.HTTPError as exc:
            self.reachable = True
            result["error"] = self._http_message(exc)
        except OSError as exc:
            self.reachable = False
            self.last_error = getattr(exc, "reason", None) or str(exc)
            result["error"] = f"ground station unreachable ({self.last_error})"
        finally:
            with self._lock:
                self._pending_people.discard(job["person_id"])
            self.last_person_error = result.get("error", "")
            if self.on_person_result is not None:
                try:
                    self.on_person_result(job["person_id"], result)
                except Exception:
                    log.exception("person result callback failed")
