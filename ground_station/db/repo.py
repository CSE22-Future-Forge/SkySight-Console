"""All SQL for the ground station (spec 4.1: "All SQL lives in db/repo.py").

Every function takes an open connection; transaction boundaries belong to the
caller (services / event bus). Rows come back as ``sqlite3.Row``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

Row = sqlite3.Row


def _one(conn, sql, params=()) -> Row | None:
    return conn.execute(sql, params).fetchone()


def _all(conn, sql, params=()) -> list[Row]:
    return conn.execute(sql, params).fetchall()


def _set_clause(fields: dict) -> tuple[str, list]:
    cols = ", ".join(f"{k} = ?" for k in fields)
    return cols, list(fields.values())


# ------------------------------------------------------------------ settings ---
def get_settings(conn) -> dict[str, str]:
    return {r["key"]: r["value"] for r in _all(conn, "SELECT key, value FROM settings")}


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ------------------------------------------------------------------ missions ---
def insert_mission(conn, mission_id, name, area_name, alert_mode, started_at, notes=None) -> None:
    conn.execute(
        "INSERT INTO missions (id, name, area_name, alert_mode, started_at, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (mission_id, name, area_name, alert_mode, started_at, notes),
    )


def get_mission(conn, mission_id) -> Row | None:
    return _one(conn, "SELECT * FROM missions WHERE id = ?", (mission_id,))


def get_active_mission(conn) -> Row | None:
    return _one(conn, "SELECT * FROM missions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1")


def list_missions(conn) -> list[Row]:
    return _all(conn, "SELECT * FROM missions ORDER BY started_at DESC")


def end_mission(conn, mission_id, ended_at) -> int:
    return conn.execute(
        "UPDATE missions SET ended_at = ? WHERE id = ? AND ended_at IS NULL", (ended_at, mission_id)
    ).rowcount


def set_mission_alert_mode(conn, mission_id, alert_mode) -> None:
    conn.execute("UPDATE missions SET alert_mode = ? WHERE id = ?", (alert_mode, mission_id))


# --------------------------------------------------------------------- teams ---
def insert_team(conn, team_id, name, call_sign, color_hex, member_count, created_at) -> None:
    conn.execute(
        "INSERT INTO teams (id, name, call_sign, color_hex, member_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, name, call_sign, color_hex, member_count, created_at),
    )


def get_team(conn, team_id) -> Row | None:
    return _one(conn, "SELECT * FROM teams WHERE id = ?", (team_id,))


def list_teams(conn, active_only=False) -> list[Row]:
    where = "WHERE is_active = 1" if active_only else ""
    return _all(conn, f"SELECT * FROM teams {where} ORDER BY name")


def list_team_ids(conn) -> list[str]:
    return [r[0] for r in _all(conn, "SELECT id FROM teams")]


def update_team(conn, team_id, **fields) -> None:
    if fields:
        cols, vals = _set_clause(fields)
        conn.execute(f"UPDATE teams SET {cols} WHERE id = ?", (*vals, team_id))


def update_team_fix(conn, team_id, lat, lon, accuracy_m, fix_at, battery_pct) -> None:
    conn.execute(
        "UPDATE teams SET last_lat = ?, last_lon = ?, last_accuracy_m = ?, last_fix_at = ?, "
        "last_battery_pct = COALESCE(?, last_battery_pct) WHERE id = ?",
        (lat, lon, accuracy_m, fix_at, battery_pct, team_id),
    )


def teams_with_fresh_fix(conn, since_ms) -> list[Row]:
    return _all(
        conn,
        "SELECT * FROM teams WHERE last_fix_at IS NOT NULL AND last_fix_at >= ? AND last_lat IS NOT NULL",
        (since_ms,),
    )


# ------------------------------------------------------------------- devices ---
def insert_device(conn, device_id, team_id, label, app_version, os_version, token_hash, paired_at, ip) -> None:
    conn.execute(
        "INSERT INTO devices (id, team_id, label, app_version, os_version, token_hash, paired_at, "
        "last_seen_at, last_ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, team_id, label, app_version, os_version, token_hash, paired_at, paired_at, ip),
    )


def get_device(conn, device_id) -> Row | None:
    return _one(conn, "SELECT * FROM devices WHERE id = ?", (device_id,))


def get_device_by_token_hash(conn, token_hash) -> Row | None:
    return _one(conn, "SELECT * FROM devices WHERE token_hash = ?", (token_hash,))


def list_devices(conn, team_id=None) -> list[Row]:
    if team_id:
        return _all(conn, "SELECT * FROM devices WHERE team_id = ? ORDER BY paired_at", (team_id,))
    return _all(conn, "SELECT * FROM devices ORDER BY paired_at")


def touch_device(conn, device_id, now, ip) -> None:
    conn.execute("UPDATE devices SET last_seen_at = ?, last_ip = COALESCE(?, last_ip) WHERE id = ?", (now, ip, device_id))


def set_device_connected(conn, device_id, connected: bool, now) -> None:
    conn.execute(
        "UPDATE devices SET ws_connected = ?, last_seen_at = ? WHERE id = ?", (1 if connected else 0, now, device_id)
    )


def reset_all_connected(conn) -> None:
    conn.execute("UPDATE devices SET ws_connected = 0 WHERE ws_connected = 1")


def set_device_acked(conn, device_id, seq) -> None:
    conn.execute(
        "UPDATE devices SET last_acked_seq = MAX(last_acked_seq, ?) WHERE id = ?", (seq, device_id)
    )


def revoke_device(conn, device_id) -> int:
    return conn.execute(
        "UPDATE devices SET revoked = 1, ws_connected = 0 WHERE id = ? AND revoked = 0", (device_id,)
    ).rowcount


# ------------------------------------------------------------ pairing tokens ---
def insert_pairing_token(conn, token_hash, team_id, expires_at) -> None:
    conn.execute(
        "INSERT INTO pairing_tokens (token_hash, team_id, expires_at) VALUES (?, ?, ?)",
        (token_hash, team_id, expires_at),
    )


def get_pairing_token(conn, token_hash) -> Row | None:
    return _one(conn, "SELECT * FROM pairing_tokens WHERE token_hash = ?", (token_hash,))


def use_pairing_token(conn, token_hash, now) -> int:
    """One-time use: only the first caller gets rowcount 1."""
    return conn.execute(
        "UPDATE pairing_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
        (now, token_hash, now),
    ).rowcount


# ---------------------------------------------------------------- detections ---
DETECTION_COLS = (
    "id", "mission_id", "victim_id", "track_id", "frame_ts", "modality", "confidence",
    "bbox_x", "bbox_y", "bbox_w", "bbox_h", "lat", "lon", "geo_error_m",
    "uav_lat", "uav_lon", "uav_alt_agl_m", "uav_heading_deg", "gimbal_pitch_deg",
    "weather_tag", "near_rescuer", "frame_path", "crop_path", "thumb_path", "thermal_crop_path", "created_at",
)


def insert_detection(conn, row: dict) -> None:
    cols = ", ".join(DETECTION_COLS)
    marks = ", ".join("?" for _ in DETECTION_COLS)
    conn.execute(f"INSERT INTO detections ({cols}) VALUES ({marks})", [row.get(c) for c in DETECTION_COLS])


def get_detection(conn, detection_id) -> Row | None:
    return _one(conn, "SELECT * FROM detections WHERE id = ?", (detection_id,))


def set_detection_victim(conn, detection_id, victim_id) -> None:
    conn.execute("UPDATE detections SET victim_id = ? WHERE id = ?", (victim_id, detection_id))


def victim_for_track(conn, mission_id, track_id) -> str | None:
    row = _one(
        conn,
        "SELECT victim_id FROM detections WHERE mission_id = ? AND track_id = ? AND victim_id IS NOT NULL "
        "ORDER BY frame_ts DESC LIMIT 1",
        (mission_id, track_id),
    )
    return row[0] if row else None


def detections_for_victim(conn, victim_id) -> list[Row]:
    return _all(conn, "SELECT * FROM detections WHERE victim_id = ? ORDER BY frame_ts", (victim_id,))


def count_near_rescuer(conn, mission_id) -> int:
    return _one(
        conn, "SELECT COUNT(*) FROM detections WHERE mission_id = ? AND near_rescuer = 1", (mission_id,)
    )[0]


# ------------------------------------------------------------------- victims ---
VICTIM_COLS = (
    "id", "display_code", "mission_id", "status", "verified", "lat", "lon", "position_error_m",
    "best_detection_id", "max_confidence", "modalities", "sighting_count", "first_detected_at",
    "last_seen_at", "assigned_team_id", "accepted_at", "completed_at", "people_count", "team_note",
    "coordinator_note", "source", "version", "created_at", "updated_at",
)

# Latest event seq touching each victim = Victim.updated_seq on the wire.
_UPDATED_SEQ = "(SELECT MAX(e.seq) FROM events e WHERE e.entity_id = v.id) AS updated_seq"


def insert_victim(conn, row: dict) -> None:
    cols = [c for c in VICTIM_COLS if c in row]
    conn.execute(
        f"INSERT INTO victims ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [row[c] for c in cols],
    )


def get_victim(conn, victim_id) -> Row | None:
    return _one(conn, f"SELECT v.*, {_UPDATED_SEQ} FROM victims v WHERE v.id = ?", (victim_id,))


def next_display_number(conn) -> int:
    row = _one(conn, "SELECT MAX(CAST(SUBSTR(display_code, 3) AS INTEGER)) FROM victims")
    return (row[0] or 0) + 1


def list_victims(conn, mission_id=None, statuses: Iterable[str] | None = None) -> list[Row]:
    where, params = [], []
    if mission_id:
        where.append("v.mission_id = ?")
        params.append(mission_id)
    if statuses:
        statuses = list(statuses)
        where.append(f"v.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return _all(conn, f"SELECT v.*, {_UPDATED_SEQ} FROM victims v {clause} ORDER BY v.first_detected_at", params)


def list_team_visible_victims(conn, team_id, since_seq=0) -> list[Row]:
    """Visibility rule 7.5: all open victims + everything assigned to this team."""
    return _all(
        conn,
        f"SELECT * FROM (SELECT v.*, {_UPDATED_SEQ} FROM victims v "
        "WHERE v.status = 'open' OR (v.assigned_team_id = ? AND v.status IN ('ongoing', 'completed'))) "
        "WHERE COALESCE(updated_seq, 0) > ? ORDER BY first_detected_at",
        (team_id, since_seq),
    )


def victim_candidates(conn, mission_id, statuses, since_ms, min_lat, max_lat, min_lon, max_lon) -> list[Row]:
    """Bounding-box prefilter for de-dup; the caller applies haversine."""
    statuses = list(statuses)
    return _all(
        conn,
        f"SELECT * FROM victims WHERE mission_id = ? AND status IN ({', '.join('?' for _ in statuses)}) "
        "AND last_seen_at >= ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (mission_id, *statuses, since_ms, min_lat, max_lat, min_lon, max_lon),
    )


def update_victim(conn, victim_id, now, *, where_status=None, where_team=None, **fields) -> int:
    """Apply ``fields``, bump version, optionally guarded. Returns rowcount (0 = guard failed)."""
    cols, vals = _set_clause(fields)
    sql = f"UPDATE victims SET {cols}{', ' if cols else ''}updated_at = ?, version = version + 1 WHERE id = ?"
    params = [*vals, now, victim_id]
    if where_status is not None:
        sql += " AND status = ?"
        params.append(where_status)
    if where_team is not None:
        sql += " AND assigned_team_id = ?"
        params.append(where_team)
    return conn.execute(sql, params).rowcount


def claim_victim(conn, victim_id, team_id, now) -> int:
    """The claim race (spec 5): one conditional UPDATE, never read-then-write."""
    return conn.execute(
        "UPDATE victims SET status = 'ongoing', assigned_team_id = ?, accepted_at = ?, updated_at = ?, "
        "version = version + 1 WHERE id = ? AND status = 'open'",
        (team_id, now, now, victim_id),
    ).rowcount


def release_victim(conn, victim_id, team_id, now) -> int:
    return conn.execute(
        "UPDATE victims SET status = 'open', assigned_team_id = NULL, accepted_at = NULL, updated_at = ?, "
        "version = version + 1 WHERE id = ? AND status = 'ongoing' AND assigned_team_id = ?",
        (now, victim_id, team_id),
    ).rowcount


def complete_victim(conn, victim_id, team_id, now, people_count, note) -> int:
    return conn.execute(
        "UPDATE victims SET status = 'completed', completed_at = ?, people_count = ?, "
        "team_note = COALESCE(?, team_note), updated_at = ?, version = version + 1 "
        "WHERE id = ? AND status = 'ongoing' AND assigned_team_id = ?",
        (now, people_count, note, now, victim_id, team_id),
    ).rowcount


# ------------------------------------------------------------ status history ---
def insert_history(
    conn, victim_id, from_status, to_status, actor_type, server_ts, applied, *,
    team_id=None, device_id=None, client_event_id=None, client_ts=None, reject_reason=None, note=None,
) -> None:
    conn.execute(
        "INSERT INTO status_history (victim_id, from_status, to_status, actor_type, team_id, device_id, "
        "client_event_id, client_ts, server_ts, applied, reject_reason, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (victim_id, from_status, to_status, actor_type, team_id, device_id, client_event_id, client_ts,
         server_ts, 1 if applied else 0, reject_reason, note),
    )


def history_by_client_event(conn, client_event_id) -> Row | None:
    return _one(conn, "SELECT * FROM status_history WHERE client_event_id = ?", (client_event_id,))


def list_history(conn, victim_id) -> list[Row]:
    return _all(
        conn,
        "SELECT h.*, t.name AS team_name FROM status_history h LEFT JOIN teams t ON t.id = h.team_id "
        "WHERE h.victim_id = ? ORDER BY h.id",
        (victim_id,),
    )


# ------------------------------------------------------------ team locations ---
def insert_location(conn, team_id, device_id, p: dict, received_at) -> bool:
    """Returns False when client_point_id was already stored (re-sent batch)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO team_locations (team_id, device_id, lat, lon, accuracy_m, altitude_m, "
        "speed_mps, heading_deg, battery_pct, fix_ts, received_at, client_point_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (team_id, device_id, p["lat"], p["lon"], p.get("accuracy_m"), p.get("altitude_m"), p.get("speed_mps"),
         p.get("heading_deg"), p.get("battery_pct"), p["fix_ts"], received_at, p["client_point_id"]),
    )
    return cur.rowcount == 1


def team_trail(conn, team_id, since_received_ms, max_accuracy_m) -> list[Row]:
    return _all(
        conn,
        "SELECT lat, lon, fix_ts, accuracy_m FROM team_locations WHERE team_id = ? AND received_at >= ? "
        "AND (accuracy_m IS NULL OR accuracy_m <= ?) ORDER BY fix_ts",
        (team_id, since_received_ms, max_accuracy_m),
    )


# ------------------------------------------------------------- UAV telemetry ---
TELEMETRY_COLS = (
    "mission_id", "ts", "lat", "lon", "alt_msl_m", "alt_agl_m", "heading_deg", "pitch_deg", "roll_deg",
    "gimbal_pitch_deg", "ground_speed_mps", "battery_pct",
)


def insert_telemetry(conn, row: dict) -> None:
    conn.execute(
        f"INSERT INTO uav_telemetry ({', '.join(TELEMETRY_COLS)}) VALUES ({', '.join('?' for _ in TELEMETRY_COLS)})",
        [row.get(c) for c in TELEMETRY_COLS],
    )


def last_telemetry(conn, mission_id) -> Row | None:
    return _one(conn, "SELECT * FROM uav_telemetry WHERE mission_id = ? ORDER BY ts DESC LIMIT 1", (mission_id,))


# ------------------------------------------------------------ alert receipts ---
def upsert_receipt(conn, victim_id, device_id, delivered_at, opened_at) -> None:
    # Keep the earliest timestamp of each kind; a re-sent receipt changes nothing.
    conn.execute(
        "INSERT INTO alert_receipts (victim_id, device_id, delivered_at, opened_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(victim_id, device_id) DO UPDATE SET "
        "delivered_at = COALESCE(MIN(alert_receipts.delivered_at, excluded.delivered_at), "
        "alert_receipts.delivered_at, excluded.delivered_at), "
        "opened_at = COALESCE(MIN(alert_receipts.opened_at, excluded.opened_at), "
        "alert_receipts.opened_at, excluded.opened_at)",
        (victim_id, device_id, delivered_at, opened_at),
    )


def receipt_counts(conn, victim_id) -> dict:
    row = _one(
        conn,
        "SELECT COUNT(delivered_at) AS delivered, COUNT(opened_at) AS opened FROM alert_receipts WHERE victim_id = ?",
        (victim_id,),
    )
    return {"delivered": row["delivered"], "opened": row["opened"]}


def count_alertable_devices(conn) -> int:
    return _one(conn, "SELECT COUNT(*) FROM devices WHERE revoked = 0")[0]


# -------------------------------------------------------------------- export ---
def mission_export(conn, mission_id) -> dict:
    """Everything recorded for one mission, as plain dicts (mission archive, spec 10)."""
    def rows(sql, params=(mission_id,)):
        return [dict(r) for r in _all(conn, sql, params)]

    mission = _one(conn, "SELECT * FROM missions WHERE id = ?", (mission_id,))
    if mission is None:
        return {}
    start, end = mission["started_at"], mission["ended_at"] or 2**62
    return {
        "mission": dict(mission),
        "victims": rows("SELECT * FROM victims WHERE mission_id = ? ORDER BY display_code"),
        "detections": rows("SELECT * FROM detections WHERE mission_id = ? ORDER BY frame_ts"),
        "status_history": rows("SELECT h.* FROM status_history h JOIN victims v ON v.id = h.victim_id "
                               "WHERE v.mission_id = ? ORDER BY h.id"),
        "alert_receipts": rows("SELECT r.* FROM alert_receipts r JOIN victims v ON v.id = r.victim_id "
                               "WHERE v.mission_id = ?"),
        "uav_telemetry": rows("SELECT * FROM uav_telemetry WHERE mission_id = ? ORDER BY ts"),
        "teams": [dict(r) for r in _all(conn, "SELECT * FROM teams ORDER BY name")],
        "team_locations": [dict(r) for r in _all(
            conn, "SELECT * FROM team_locations WHERE received_at BETWEEN ? AND ? ORDER BY received_at",
            (start, end))],
    }


# -------------------------------------------------------------------- events ---
def insert_event(conn, type_, entity_id, scope_team_id, laptop_only, payload: dict, created_at) -> int:
    cur = conn.execute(
        "INSERT INTO events (type, entity_id, scope_team_id, laptop_only, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (type_, entity_id, scope_team_id, 1 if laptop_only else 0, json.dumps(payload, separators=(",", ":")),
         created_at),
    )
    return cur.lastrowid


def set_event_payload(conn, seq, payload: dict) -> None:
    conn.execute("UPDATE events SET payload_json = ? WHERE seq = ?", (json.dumps(payload, separators=(",", ":")), seq))


def team_events_since(conn, team_id, since_seq, limit) -> list[Row]:
    return _all(
        conn,
        "SELECT * FROM events WHERE seq > ? AND laptop_only = 0 AND (scope_team_id IS NULL OR scope_team_id = ?) "
        "ORDER BY seq LIMIT ?",
        (since_seq, team_id, limit),
    )


def max_event_seq(conn) -> int:
    return _one(conn, "SELECT COALESCE(MAX(seq), 0) FROM events")[0]
