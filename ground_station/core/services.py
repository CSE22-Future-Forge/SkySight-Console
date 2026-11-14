"""Service layer: every manifest change goes through here (GUI, API, pipeline).

Each public method is one transaction (``bus.unit()``): it reads, applies the
change with a guarded UPDATE, writes ``status_history`` and the ``events`` rows,
and only then are subscribers told. Errors are raised as ``ServiceError`` after
the transaction commits, so rejected attempts are still on the audit log.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from ground_station.config import SSID, Paths, Settings
from ground_station.core import dedup, events as ev, images
from ground_station.core import state_machine as sm
from ground_station.core.events import EventBus
from ground_station.db import repo
from ground_station.db.connection import Database

DEVICE_TOUCH_INTERVAL_MS = 10_000
TELEMETRY_MIN_INTERVAL_MS = 1_000
TRAIL_MINUTES = 15
SYNC_PAGE = 500
PHONE_SETTINGS = ("alert_repeat_s", "gps_max_accuracy_m", "ws_ping_interval_s", "ws_dead_after_s",
                  "team_stale_after_s")


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra

    def body(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


def new_id() -> str:
    return str(uuid.uuid4())


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- views ---
def phone_view(row) -> dict:
    """``Victim`` as phones see it (spec 7.1)."""
    keys = row.keys()
    return {
        "id": row["id"],
        "display_code": row["display_code"],
        "status": row["status"],
        "lat": row["lat"],
        "lon": row["lon"],
        "position_error_m": row["position_error_m"],
        "max_confidence": row["max_confidence"],
        "modalities": row["modalities"],
        "sighting_count": row["sighting_count"],
        "first_detected_at": row["first_detected_at"],
        "last_seen_at": row["last_seen_at"],
        "assigned_team_id": row["assigned_team_id"],
        "accepted_at": row["accepted_at"],
        "completed_at": row["completed_at"],
        "people_count": row["people_count"],
        "source": row["source"],
        "thumb_url": f"/api/v1/victims/{row['id']}/image?size=thumb" if row["best_detection_id"] else None,
        "version": row["version"],
        "updated_seq": row["updated_seq"] if "updated_seq" in keys else None,
    }


def team_view(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "call_sign": row["call_sign"], "color_hex": row["color_hex"]}


def mission_view(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "area_name": row["area_name"], "alert_mode": row["alert_mode"],
            "started_at": row["started_at"], "ended_at": row["ended_at"]}


def team_can_see(row, team_id) -> bool:
    status = row["status"]
    return status == sm.OPEN or (status in (sm.ONGOING, sm.COMPLETED) and row["assigned_team_id"] == team_id)


_ALL = object()  # "every team" in visibility sets


def _visibility(row):
    if row is None:
        return frozenset()
    if row["status"] == sm.OPEN:
        return _ALL
    if row["status"] in (sm.ONGOING, sm.COMPLETED):
        return frozenset({row["assigned_team_id"]})
    return frozenset()


# ------------------------------------------------------------------ manifest ---
class Manifest:
    """Application context: DB, settings, event bus and the services."""

    def __init__(self, paths: Paths | None = None):
        self.paths = (paths or Paths()).ensure()
        self.db = Database(self.paths.db)
        self.db.init()
        self.settings = Settings(self.db)
        self.bus = EventBus(self.db)
        self.missions = MissionService(self)
        self.teams = TeamService(self)
        self.devices = DeviceService(self)
        self.victims = VictimService(self)
        self.telemetry = TelemetryService(self)
        self.sync = SyncService(self)
        # A restart means nobody is connected, whatever the DB last recorded.
        with self.db.tx() as conn:
            repo.reset_all_connected(conn)

    def close(self) -> None:
        self.db.close_all()


class _Service:
    def __init__(self, m: Manifest):
        self.m = m

    def unit(self):
        return self.m.bus.unit()


# ------------------------------------------------------------------ missions ---
class MissionService(_Service):
    def active(self):
        return repo.get_active_mission(self.m.db.conn())

    def list(self):
        return repo.list_missions(self.m.db.conn())

    def start(self, name: str, area_name: str | None = None, alert_mode: str = "auto", notes: str | None = None):
        if alert_mode not in ("auto", "verify"):
            raise ServiceError("bad_alert_mode", "alert_mode must be auto or verify", 422)
        mission_id = new_id()
        with self.unit() as u:
            current = repo.get_active_mission(u.conn)
            if current:
                repo.end_mission(u.conn, current["id"], u.now)
            repo.insert_mission(u.conn, mission_id, name, area_name, alert_mode, u.now, notes)
            mission = repo.get_mission(u.conn, mission_id)
            u.emit(ev.MISSION_CHANGED, {"mission": mission_view(mission), "action": "started"})
        return mission

    def stop(self):
        with self.unit() as u:
            current = repo.get_active_mission(u.conn)
            if not current:
                return None
            repo.end_mission(u.conn, current["id"], u.now)
            u.emit(ev.MISSION_CHANGED, {"mission": mission_view(repo.get_mission(u.conn, current["id"])),
                                        "action": "stopped"})
        return current

    def set_alert_mode(self, alert_mode: str):
        if alert_mode not in ("auto", "verify"):
            raise ServiceError("bad_alert_mode", "alert_mode must be auto or verify", 422)
        with self.unit() as u:
            current = repo.get_active_mission(u.conn)
            if not current:
                raise ServiceError("no_active_mission", "Start a mission first")
            repo.set_mission_alert_mode(u.conn, current["id"], alert_mode)
            u.emit(ev.MISSION_CHANGED, {"mission": mission_view(repo.get_mission(u.conn, current["id"])),
                                        "action": "alert_mode"})

    def archive(self, mission_id: str, delete_images: bool = False):
        """Close-and-archive (spec 10): zip the mission's records + images into <data>/archives.

        With ``delete_images`` the image folder is removed once the zip is written, so
        photos of people do not stay on the laptop longer than needed.
        """
        import json
        import re
        import shutil
        import zipfile
        from datetime import datetime

        mission = repo.get_mission(self.m.db.conn(), mission_id)
        if mission is None:
            raise ServiceError("unknown_mission", "No such mission", 404)
        if mission["ended_at"] is None:
            raise ServiceError("mission_running", "End the mission before archiving it")
        with self.m.db.read() as conn:
            data = repo.mission_export(conn, mission_id)

        folder = self.m.paths.root / "archives"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(mission["started_at"] / 1000).strftime("%Y%m%d-%H%M")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", mission["name"]).strip("_")[:40] or "mission"
        path = folder / f"{stamp}_{safe}.zip"
        images = self.m.paths.images / mission_id
        tmp = path.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(data, indent=1))
            if images.is_dir():
                for f in sorted(images.iterdir()):
                    zf.write(f, f"images/{f.name}", compress_type=zipfile.ZIP_STORED)  # JPEGs: no gain
        tmp.replace(path)
        if delete_images and images.is_dir():
            shutil.rmtree(images)
        return path

    def resolve(self, mission_id: str | None):
        conn = self.m.db.conn()
        if mission_id:
            mission = repo.get_mission(conn, mission_id)
            if not mission:
                raise ServiceError("unknown_mission", f"No mission {mission_id}", 404)
            if mission["ended_at"] is not None:
                raise ServiceError("mission_ended", "That mission has ended", 409)
            return mission
        mission = repo.get_active_mission(conn)
        if not mission:
            raise ServiceError("no_active_mission", "No mission is running on the ground station")
        return mission


# --------------------------------------------------------------------- teams ---
class TeamService(_Service):
    def list(self, active_only: bool = False):
        return repo.list_teams(self.m.db.conn(), active_only)

    def get(self, team_id):
        return repo.get_team(self.m.db.conn(), team_id)

    def create(self, name: str, call_sign: str | None = None, color_hex: str = "#1f77b4",
               member_count: int | None = None, is_active: int | bool = 1):
        team_id = new_id()
        with self.unit() as u:
            repo.insert_team(u.conn, team_id, name, call_sign, color_hex, member_count, u.now)
            if not is_active:
                repo.update_team(u.conn, team_id, is_active=0)
            team = repo.get_team(u.conn, team_id)
            u.emit(ev.TEAM_CHANGED, {"team": team_view(team), "action": "created"})
        return team

    def update(self, team_id, **fields):
        allowed = {"name", "call_sign", "color_hex", "member_count", "is_active"}
        bad = set(fields) - allowed
        if bad:
            raise ServiceError("bad_field", f"Cannot update {sorted(bad)}", 422)
        with self.unit() as u:
            repo.update_team(u.conn, team_id, **fields)
            team = repo.get_team(u.conn, team_id)
            u.emit(ev.TEAM_CHANGED, {"team": team_view(team), "action": "updated"})
        return team

    def trail(self, team_id, minutes: int = TRAIL_MINUTES):
        since = ev.now_ms() - minutes * 60_000
        return repo.team_trail(self.m.db.conn(), team_id, since, self.m.settings.get("gps_max_accuracy_m"))

    def is_stale(self, team_row, now_ms: int | None = None) -> bool:
        last = team_row["last_fix_at"]
        if last is None:
            return True
        now_ms = now_ms or ev.now_ms()
        return now_ms - last > self.m.settings.get("team_stale_after_s") * 1000

    def add_locations(self, device, points: list[dict]) -> dict:
        """Store a batch of GPS fixes (re-sent points are ignored) and refresh the team's latest fix."""
        accepted = duplicates = 0
        max_acc = self.m.settings.get("gps_max_accuracy_m")
        with self.unit() as u:
            newest = None
            for p in points:
                if repo.insert_location(u.conn, device["team_id"], device["id"], p, u.now):
                    accepted += 1
                    good = p.get("accuracy_m") is None or p["accuracy_m"] <= max_acc
                    if good and (newest is None or p["fix_ts"] > newest["fix_ts"]):
                        newest = p
                else:
                    duplicates += 1
            if newest is not None:
                # last_fix_at is laptop time: phone clocks are not trusted (spec 11).
                repo.update_team_fix(u.conn, device["team_id"], newest["lat"], newest["lon"],
                                     newest.get("accuracy_m"), u.now, newest.get("battery_pct"))
                u.emit(ev.TEAM_LOCATION, {
                    "team_id": device["team_id"], "device_id": device["id"], "lat": newest["lat"],
                    "lon": newest["lon"], "accuracy_m": newest.get("accuracy_m"),
                    "heading_deg": newest.get("heading_deg"), "battery_pct": newest.get("battery_pct"),
                    "fix_ts": newest["fix_ts"],
                }, entity_id=device["team_id"])
        return {"accepted": accepted, "duplicates": duplicates}


# ------------------------------------------------------------------- devices ---
class DeviceService(_Service):
    def list(self):
        return repo.list_devices(self.m.db.conn())

    def create_pairing(self, team_id: str | None = None, server_url: str | None = None) -> dict:
        token = secrets.token_urlsafe(16)
        ttl = self.m.settings.get("pairing_token_ttl_min")
        with self.m.db.tx() as conn:
            if team_id and not repo.get_team(conn, team_id):
                raise ServiceError("unknown_team", "No such team", 404)
            expires = ev.now_ms() + ttl * 60_000
            repo.insert_pairing_token(conn, hash_token(token), team_id, expires)
        return {
            "pairing_token": token,
            "expires_at": expires,
            "qr_payload": {"server_url": server_url, "pairing_token": token, "ssid": SSID},
        }

    def pair(self, req, ip: str | None = None) -> dict:
        token_hash = hash_token(req.pairing_token)
        device_token = secrets.token_urlsafe(32)
        device_id = new_id()
        err = None
        with self.unit() as u:
            pt = repo.get_pairing_token(u.conn, token_hash)
            if not pt or pt["used_at"] is not None or pt["expires_at"] <= u.now:
                err = ServiceError("invalid_pairing_token", "Pairing code is unknown, used or expired", 401)
            else:
                team_id = pt["team_id"] or req.team_id
                team = repo.get_team(u.conn, team_id) if team_id else None
                if team is None:
                    # Token stays unused: the phone shows the list and posts again.
                    teams = [team_view(t) for t in repo.list_teams(u.conn, active_only=True)]
                    err = ServiceError("team_required", "Choose a team", 409, teams=teams)
                elif repo.use_pairing_token(u.conn, token_hash, u.now) != 1:
                    err = ServiceError("invalid_pairing_token", "Pairing code was already used", 401)
                else:
                    repo.insert_device(u.conn, device_id, team["id"], req.device_label, req.app_version,
                                       req.os_version, hash_token(device_token), u.now, ip)
                    u.emit(ev.TEAM_CHANGED, {"team": team_view(team), "action": "device_paired",
                                             "device_id": device_id})
                    mission = repo.get_active_mission(u.conn)
        if err:
            raise err
        return {"device_id": device_id, "device_token": device_token, "team": team_view(team),
                "mission": mission_view(mission), "server_time": ev.now_ms()}

    def authenticate(self, token: str | None, ip: str | None = None):
        if not token:
            raise ServiceError("unauthorized", "Missing device token", 401)
        conn = self.m.db.conn()
        device = repo.get_device_by_token_hash(conn, hash_token(token))
        if device is None:
            raise ServiceError("unauthorized", "Unknown device token", 401)
        if device["revoked"]:
            raise ServiceError("revoked", "This device was revoked by the coordinator", 401)
        now = ev.now_ms()
        if (device["last_seen_at"] or 0) < now - DEVICE_TOUCH_INTERVAL_MS or (ip and ip != device["last_ip"]):
            with self.m.db.tx() as wconn:
                repo.touch_device(wconn, device["id"], now, ip)
        return device

    def set_connected(self, device_id: str, connected: bool) -> None:
        with self.unit() as u:
            repo.set_device_connected(u.conn, device_id, connected, u.now)
            device = repo.get_device(u.conn, device_id)
            u.emit(ev.DEVICE_ONLINE if connected else ev.DEVICE_OFFLINE,
                   {"device_id": device_id, "team_id": device["team_id"], "label": device["label"]},
                   entity_id=device_id)

    def ack(self, device_id: str, seq: int) -> None:
        with self.m.db.tx() as conn:
            repo.set_device_acked(conn, device_id, int(seq))

    def revoke(self, device_id: str) -> bool:
        with self.unit() as u:
            changed = repo.revoke_device(u.conn, device_id) == 1
            if changed:
                device = repo.get_device(u.conn, device_id)
                u.emit(ev.DEVICE_REVOKED, {"device_id": device_id, "team_id": device["team_id"]},
                       entity_id=device_id)
        return changed

    def me(self, device) -> dict:
        conn = self.m.db.conn()
        team = repo.get_team(conn, device["team_id"])
        mine = [phone_view(v) for v in repo.list_team_visible_victims(conn, device["team_id"])
                if v["assigned_team_id"] == device["team_id"]]
        return {
            "device": {"id": device["id"], "label": device["label"], "paired_at": device["paired_at"],
                       "last_acked_seq": device["last_acked_seq"]},
            "team": team_view(team),
            "assignments": mine,
            # Phone-side tunables come from the settings table too, never hard-coded in the app.
            "settings": {k: self.m.settings.get(k) for k in PHONE_SETTINGS},
            "server_time": ev.now_ms(),
        }


# ------------------------------------------------------------------- victims ---
class VictimService(_Service):
    # ------------------------------------------------------------ reads ---
    def get(self, victim_id):
        return repo.get_victim(self.m.db.conn(), victim_id)

    def list(self, mission_id=None, statuses=None):
        return repo.list_victims(self.m.db.conn(), mission_id, statuses)

    def history(self, victim_id):
        return repo.list_history(self.m.db.conn(), victim_id)

    def detections(self, victim_id):
        return repo.detections_for_victim(self.m.db.conn(), victim_id)

    def receipts(self, victim_id) -> dict:
        conn = self.m.db.conn()
        return {**repo.receipt_counts(conn, victim_id), "devices": repo.count_alertable_devices(conn)}

    def visible_for_team(self, team_id, since_seq: int = 0) -> dict:
        with self.m.db.read() as conn:
            rows = repo.list_team_visible_victims(conn, team_id, since_seq)
            last_seq = repo.max_event_seq(conn)
        return {"victims": [phone_view(r) for r in rows], "last_seq": last_seq}

    def detail_for_team(self, victim_id, team_id) -> dict:
        conn = self.m.db.conn()
        row = repo.get_victim(conn, victim_id)
        if row is None or not team_can_see(row, team_id):
            raise ServiceError("not_found", "No such victim", 404)
        history = [
            {"from_status": h["from_status"], "to_status": h["to_status"], "actor_type": h["actor_type"],
             "team_name": h["team_name"], "server_ts": h["server_ts"], "note": h["note"]}
            for h in repo.list_history(conn, victim_id) if h["applied"]
        ]
        return {"victim": phone_view(row), "history": history}

    def image_path(self, victim_id, size: str = "thumb", team_id: str | None = None, detection_id=None):
        if size not in ("thumb", "crop", "frame", "thermal"):
            raise ServiceError("bad_size", "size must be thumb, crop or frame", 422)
        conn = self.m.db.conn()
        row = repo.get_victim(conn, victim_id)
        if row is None or (team_id is not None and not team_can_see(row, team_id)):
            raise ServiceError("not_found", "No such victim", 404)
        det_id = detection_id or row["best_detection_id"]
        det = repo.get_detection(conn, det_id) if det_id else None
        rel = det[f"{'thermal_crop' if size == 'thermal' else size}_path"] if det else None
        path = self.m.paths.images / rel if rel else None
        if path is None or not path.is_file():
            raise ServiceError("no_image", "No image for this victim", 404)
        return path

    # ---------------------------------------------------- event fan-out ---
    def _emit_change(self, u, old, new, kind=ev.VICTIM_UPDATED, hidden_reason=None):
        """Turn one victim change into per-team events (visibility rule 7.5)."""
        vid = new["id"]
        payload = phone_view(new)
        old_v, new_v = _visibility(old), _visibility(new)
        if new_v is _ALL:
            u.emit(kind, payload, entity_id=vid, laptop_only=False)
        else:
            for team_id in sorted(new_v):
                u.emit(kind, payload, entity_id=vid, scope_team_id=team_id, laptop_only=False)

        hidden = {"id": vid, "reason": hidden_reason or new["status"]}
        if old_v is _ALL and new_v is not _ALL:
            if not new_v:
                u.emit(ev.VICTIM_HIDDEN, hidden, entity_id=vid, laptop_only=False)
            else:
                for team_id in sorted(set(repo.list_team_ids(u.conn)) - new_v):
                    u.emit(ev.VICTIM_HIDDEN, hidden, entity_id=vid, scope_team_id=team_id, laptop_only=False)
        elif old_v is not _ALL and new_v is not _ALL:
            for team_id in sorted(old_v - new_v):
                u.emit(ev.VICTIM_HIDDEN, hidden, entity_id=vid, scope_team_id=team_id, laptop_only=False)

        if old_v is not _ALL and not old_v and new_v is not _ALL and not new_v:
            # Pending / dismissed: nobody in the field sees it, the GUI still must.
            u.emit(kind, payload, entity_id=vid, laptop_only=True)

    # ---------------------------------------------------------- ingest ---
    def ingest_detection(self, det) -> dict:
        mission = self.m.missions.resolve(det.mission_id)
        detection_id = new_id()
        try:
            rel = images.store_detection_images(self.m.paths.images, mission["id"], detection_id, det.images,
                                                det.bbox)
        except images.ImageError as exc:
            raise ServiceError("bad_image", str(exc), 422) from exc

        try:
            with self.unit() as u:
                mission = repo.get_mission(u.conn, mission["id"])
                decision = dedup.resolve(u.conn, self.m.settings, mission["id"], det, u.now)
                uav = det.uav
                repo.insert_detection(u.conn, {
                    "id": detection_id, "mission_id": mission["id"], "victim_id": None,
                    "track_id": det.track_id, "frame_ts": det.frame_ts, "modality": det.modality,
                    "confidence": det.confidence, "bbox_x": det.bbox.x, "bbox_y": det.bbox.y,
                    "bbox_w": det.bbox.w, "bbox_h": det.bbox.h, "lat": det.lat, "lon": det.lon,
                    "geo_error_m": det.geo_error_m,
                    "uav_lat": uav.lat if uav else None, "uav_lon": uav.lon if uav else None,
                    "uav_alt_agl_m": uav.alt_agl_m if uav else None,
                    "uav_heading_deg": uav.heading_deg if uav else None,
                    "gimbal_pitch_deg": uav.gimbal_pitch_deg if uav else None,
                    "weather_tag": det.weather_tag,
                    "near_rescuer": 1 if decision.kind == dedup.NEAR_RESCUER else 0,
                    "created_at": u.now, **rel,
                })

                victim_id, created = decision.victim_id, False
                if decision.kind == dedup.NEAR_RESCUER:
                    u.emit(ev.DETECTION_NEAR_RESCUER, {
                        "detection_id": detection_id, "team_id": decision.team_id, "lat": det.lat,
                        "lon": det.lon, "distance_m": round(decision.distance_m, 1),
                        "confidence": det.confidence,
                    }, entity_id=detection_id)
                elif victim_id:
                    old = repo.get_victim(u.conn, victim_id)
                    repo.set_detection_victim(u.conn, detection_id, victim_id)
                    repo.update_victim(u.conn, victim_id, u.now, **dedup.attach_fields(u.conn, old, det, detection_id))
                    self._emit_change(u, old, repo.get_victim(u.conn, victim_id))
                else:
                    victim_id, created = new_id(), True
                    status = sm.PENDING if mission["alert_mode"] == "verify" else sm.OPEN
                    repo.insert_victim(u.conn, {
                        "id": victim_id,
                        "display_code": f"V-{repo.next_display_number(u.conn):04d}",
                        "mission_id": mission["id"], "status": status, "lat": det.lat, "lon": det.lon,
                        "position_error_m": det.geo_error_m, "best_detection_id": detection_id,
                        "max_confidence": det.confidence, "modalities": dedup.MODALITY[det.modality],
                        "sighting_count": 1, "first_detected_at": det.frame_ts, "last_seen_at": det.frame_ts,
                        "source": "pipeline", "created_at": u.now, "updated_at": u.now,
                    })
                    repo.set_detection_victim(u.conn, detection_id, victim_id)
                    repo.insert_history(u.conn, victim_id, None, status, sm.SYSTEM, u.now, True)
                    self._emit_change(u, None, repo.get_victim(u.conn, victim_id), kind=ev.VICTIM_CREATED)
        except BaseException:
            images.remove_files(self.m.paths.images, rel)
            raise

        return {"detection_id": detection_id, "victim_id": victim_id, "created_new_victim": created,
                "near_rescuer": decision.kind == dedup.NEAR_RESCUER, "dedup": decision.kind}

    def add_manual(self, lat: float, lon: float, note: str | None = None, position_error_m: float | None = None):
        mission = self.m.missions.resolve(None)
        victim_id = new_id()
        with self.unit() as u:
            now = u.now
            repo.insert_victim(u.conn, {
                "id": victim_id, "display_code": f"V-{repo.next_display_number(u.conn):04d}",
                "mission_id": mission["id"], "status": sm.OPEN, "verified": 1, "lat": lat, "lon": lon,
                "position_error_m": position_error_m, "sighting_count": 0, "first_detected_at": now,
                "last_seen_at": now, "coordinator_note": note, "source": "manual", "created_at": now,
                "updated_at": now,
            })
            repo.insert_history(u.conn, victim_id, None, sm.OPEN, sm.COORDINATOR, now, True, note=note)
            self._emit_change(u, None, repo.get_victim(u.conn, victim_id), kind=ev.VICTIM_CREATED)
        return self.get(victim_id)

    # ---------------------------------------------------- team actions ---
    def _rejection(self, conn, reason: str, victim) -> ServiceError:
        if reason == "already_claimed":
            team = repo.get_team(conn, victim["assigned_team_id"]) if victim["assigned_team_id"] else None
            return ServiceError("already_claimed", "Another team already has this victim", 409,
                                team={"id": team["id"], "name": team["name"]} if team else None)
        if reason == "not_assigned":
            return ServiceError("not_assigned", "This victim is not assigned to your team", 409)
        if reason == "not_found":
            return ServiceError("not_found", "No such victim", 404)
        return ServiceError("invalid_transition", f"Not allowed while the victim is {victim['status']}", 409,
                            status_now=victim["status"])

    def _team_action(self, action, victim_id, device, client_event_id, client_ts, to_status, apply, classify,
                     note=None, hidden_reason=None):
        team_id = device["team_id"]
        err = result = None
        with self.unit() as u:
            victim = repo.get_victim(u.conn, victim_id)
            prior = repo.history_by_client_event(u.conn, client_event_id)
            if prior is not None:
                # Idempotency: the same phone action re-sent from the outbox.
                if prior["victim_id"] != victim_id:
                    err = ServiceError("client_event_id_reused", "client_event_id belongs to another victim", 409)
                elif prior["applied"] or prior["reject_reason"] == "noop":
                    result = phone_view(victim)
                else:
                    err = self._rejection(u.conn, prior["reject_reason"], victim)
            elif victim is None or victim["status"] == sm.PENDING:
                err = ServiceError("not_found", "No such victim", 404)
            else:
                if apply(u) == 1:
                    new = repo.get_victim(u.conn, victim_id)
                    repo.insert_history(u.conn, victim_id, victim["status"], to_status, sm.TEAM, u.now, True,
                                        team_id=team_id, device_id=device["id"], client_event_id=client_event_id,
                                        client_ts=client_ts, note=note)
                    self._emit_change(u, victim, new, hidden_reason=hidden_reason)
                    result = phone_view(new)
                else:
                    reason = classify(victim, team_id)
                    repo.insert_history(u.conn, victim_id, victim["status"], to_status, sm.TEAM, u.now, False,
                                        team_id=team_id, device_id=device["id"], client_event_id=client_event_id,
                                        client_ts=client_ts, reject_reason=reason, note=note)
                    if reason == "noop":
                        result = phone_view(victim)
                    else:
                        err = self._rejection(u.conn, reason, victim)
        if err:
            raise err
        return {"victim": result}

    def accept(self, victim_id, device, client_event_id, client_ts=None):
        def apply(u):
            return repo.claim_victim(u.conn, victim_id, device["team_id"], u.now)

        def classify(v, team_id):
            if v["status"] == sm.ONGOING and v["assigned_team_id"] == team_id:
                return "noop"  # another phone of the same team got there first
            if v["status"] in (sm.ONGOING, sm.COMPLETED):
                return "already_claimed"
            return "invalid_transition"

        return self._team_action("accept", victim_id, device, client_event_id, client_ts, sm.ONGOING, apply,
                                 classify, hidden_reason="claimed")

    def release(self, victim_id, device, client_event_id, client_ts=None, reason=None):
        def apply(u):
            return repo.release_victim(u.conn, victim_id, device["team_id"], u.now)

        def classify(v, team_id):
            if v["status"] in (sm.ONGOING, sm.COMPLETED) and v["assigned_team_id"] != team_id:
                return "not_assigned"
            return "invalid_transition"

        return self._team_action("release", victim_id, device, client_event_id, client_ts, sm.OPEN, apply,
                                 classify, note=reason)

    def complete(self, victim_id, device, client_event_id, client_ts=None, people_count=None, note=None):
        def apply(u):
            return repo.complete_victim(u.conn, victim_id, device["team_id"], u.now, people_count, note)

        def classify(v, team_id):
            if v["assigned_team_id"] != team_id and v["status"] in (sm.ONGOING, sm.COMPLETED):
                return "not_assigned"
            if v["status"] == sm.COMPLETED:
                return "noop"
            return "invalid_transition"

        return self._team_action("complete", victim_id, device, client_event_id, client_ts, sm.COMPLETED, apply,
                                 classify, note=note)

    def add_receipt(self, victim_id, device, delivered_at=None, opened_at=None) -> None:
        with self.unit() as u:
            victim = repo.get_victim(u.conn, victim_id)
            if victim is None or victim["status"] == sm.PENDING:
                raise ServiceError("not_found", "No such victim", 404)
            repo.upsert_receipt(u.conn, victim_id, device["id"], delivered_at, opened_at)
            counts = repo.receipt_counts(u.conn, victim_id)
            u.emit(ev.ALERT_RECEIPT, {"victim_id": victim_id, "device_id": device["id"], **counts,
                                      "devices": repo.count_alertable_devices(u.conn)}, entity_id=victim_id)

    # --------------------------------------------- coordinator actions ---
    def _coordinator(self, victim_id, to_status, action, *, kind=ev.VICTIM_UPDATED, hidden_reason=None,
                     note=None, assign_team=None, **fields):
        err = None
        with self.unit() as u:
            old = repo.get_victim(u.conn, victim_id)
            if old is None:
                raise ServiceError("not_found", "No such victim", 404)
            frm = old["status"]
            ok = sm.allowed(frm, to_status, sm.COORDINATOR, set(action.split("|")))
            if ok and assign_team is not None:
                if not repo.get_team(u.conn, assign_team):
                    raise ServiceError("unknown_team", "No such team", 404)
                if frm == sm.ONGOING and old["assigned_team_id"] == assign_team:
                    ok = False
            if ok and repo.update_victim(u.conn, victim_id, u.now, where_status=frm, status=to_status,
                                         **fields) == 1:
                new = repo.get_victim(u.conn, victim_id)
                repo.insert_history(u.conn, victim_id, frm, to_status, sm.COORDINATOR, u.now, True,
                                    team_id=assign_team, note=note)
                self._emit_change(u, old, new, kind=kind, hidden_reason=hidden_reason)
                if assign_team is not None:
                    u.emit(ev.ASSIGNMENT_CHANGED, {"victim_id": victim_id, "team_id": assign_team},
                           entity_id=victim_id, scope_team_id=assign_team, laptop_only=False)
            else:
                repo.insert_history(u.conn, victim_id, frm, to_status, sm.COORDINATOR, u.now, False,
                                    team_id=assign_team, reject_reason="invalid_transition", note=note)
                err = ServiceError("invalid_transition", f"Cannot go from {frm} to {to_status}", 409,
                                   status_now=frm)
        if err:
            raise err
        return self.get(victim_id)

    def verify(self, victim_id, note=None):
        # Phones never saw a pending victim, so for them it is brand new: alarm.
        return self._coordinator(victim_id, sm.OPEN, "verify", kind=ev.VICTIM_CREATED, note=note, verified=1)

    def dismiss(self, victim_id, note=None):
        return self._coordinator(victim_id, sm.DISMISSED, "dismiss", hidden_reason="dismissed", note=note)

    def restore(self, victim_id, note=None):
        return self._coordinator(victim_id, sm.OPEN, "restore", kind=ev.VICTIM_CREATED, note=note)

    def release_coordinator(self, victim_id, note=None):
        return self._coordinator(victim_id, sm.OPEN, "release", note=note, assigned_team_id=None, accepted_at=None)

    def assign(self, victim_id, team_id, note=None):
        """Open -> ongoing (dispatch) or ongoing -> ongoing for another team (reassign)."""
        victim = self.get(victim_id)
        if victim is None:
            raise ServiceError("not_found", "No such victim", 404)
        now = ev.now_ms()
        reason = "reassigned" if victim["status"] == sm.ONGOING else "claimed"
        return self._coordinator(victim_id, sm.ONGOING, "assign|reassign", hidden_reason=reason, note=note,
                                 assign_team=team_id, assigned_team_id=team_id, accepted_at=now)

    def mark_completed(self, victim_id, people_count=None, note=None):
        extra = {"people_count": people_count} if people_count is not None else {}
        return self._coordinator(victim_id, sm.COMPLETED, "mark_completed", note=note,
                                 completed_at=ev.now_ms(), **extra)

    def reopen(self, victim_id, note=None):
        return self._coordinator(victim_id, sm.ONGOING, "reopen", note=note, completed_at=None)

    def add_note(self, victim_id, note: str):
        with self.unit() as u:
            old = repo.get_victim(u.conn, victim_id)
            if old is None:
                raise ServiceError("not_found", "No such victim", 404)
            repo.update_victim(u.conn, victim_id, u.now, coordinator_note=note)
            self._emit_change(u, old, repo.get_victim(u.conn, victim_id))
        return self.get(victim_id)


# ----------------------------------------------------------------- telemetry ---
class TelemetryService(_Service):
    def __init__(self, m):
        super().__init__(m)
        self.latest: dict | None = None
        self._last_stored: dict[str, int] = {}

    def ingest(self, t) -> bool:
        """Keep the newest pose in memory; persist at most 1 Hz per mission. Returns True if stored."""
        mission = self.m.missions.resolve(t.mission_id)
        row = {**t.model_dump(), "mission_id": mission["id"]}
        self.latest = row
        if t.ts - self._last_stored.get(mission["id"], -10**15) < TELEMETRY_MIN_INTERVAL_MS:
            return False
        self._last_stored[mission["id"]] = t.ts
        with self.unit() as u:
            repo.insert_telemetry(u.conn, row)
            u.emit(ev.UAV_POSE, row, entity_id=mission["id"])
        return True


# ---------------------------------------------------------------------- sync ---
class SyncService(_Service):
    def events_for_team(self, team_id, since_seq: int, limit: int = SYNC_PAGE) -> dict:
        with self.m.db.read() as conn:
            rows = repo.team_events_since(conn, team_id, since_seq, limit)
            # Nothing visible left: jump to the global head so the next poll skips laptop-only rows.
            last = rows[-1]["seq"] if len(rows) == limit else max(since_seq, repo.max_event_seq(conn))
        return {"events": [ev.Event.from_row(r).envelope() for r in rows], "last_seq": last}
