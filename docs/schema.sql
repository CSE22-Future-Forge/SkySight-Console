-- =====================================================================
-- SAR Manifest — ground station database (SQLite 3, WAL mode)
-- Source of truth for the laptop schema. Apply on first run, then use
-- numbered migrations in ground_station/db/migrations/ for changes.
--
-- Conventions
--   * Timestamps: INTEGER, UTC epoch milliseconds.
--   * Coordinates: REAL, WGS84 decimal degrees.
--   * IDs for victims, detections, devices, teams, missions: TEXT UUIDv4.
--   * Status values are lowercase strings.
--
-- Run on EVERY connection (not stored in the file except journal_mode):
--   PRAGMA journal_mode = WAL;
--   PRAGMA synchronous = NORMAL;
--   PRAGMA foreign_keys = ON;
--   PRAGMA busy_timeout = 5000;
-- =====================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    area_name   TEXT,
    alert_mode  TEXT NOT NULL DEFAULT 'auto' CHECK (alert_mode IN ('auto', 'verify')),
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    call_sign        TEXT,
    color_hex        TEXT NOT NULL DEFAULT '#1f77b4',
    member_count     INTEGER,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- denormalised latest fix, for fast map drawing
    last_lat         REAL,
    last_lon         REAL,
    last_accuracy_m  REAL,
    last_fix_at      INTEGER,
    last_battery_pct INTEGER,
    created_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id              TEXT PRIMARY KEY,               -- issued at pairing
    team_id         TEXT NOT NULL REFERENCES teams(id),
    label           TEXT NOT NULL,                  -- e.g. "Alpha-1 Samsung A15"
    app_version     TEXT,
    os_version      TEXT,
    token_hash      TEXT NOT NULL UNIQUE,           -- SHA-256 hex of bearer token
    paired_at       INTEGER NOT NULL,
    last_seen_at    INTEGER,
    last_ip         TEXT,
    ws_connected    INTEGER NOT NULL DEFAULT 0 CHECK (ws_connected IN (0, 1)),
    last_acked_seq  INTEGER NOT NULL DEFAULT 0,
    revoked         INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);

CREATE TABLE IF NOT EXISTS pairing_tokens (
    token_hash  TEXT PRIMARY KEY,                   -- SHA-256 hex of one-time token
    team_id     TEXT REFERENCES teams(id),          -- NULL = phone chooses team
    expires_at  INTEGER NOT NULL,
    used_at     INTEGER
);

-- One row per confirmed sighting (one image set) from the pipeline.
CREATE TABLE IF NOT EXISTS detections (
    id                 TEXT PRIMARY KEY,            -- also the image file stem
    mission_id         TEXT NOT NULL REFERENCES missions(id),
    victim_id          TEXT REFERENCES victims(id), -- set by de-duplication
    track_id           INTEGER,                     -- tracker ID within the mission
    frame_ts           INTEGER NOT NULL,
    modality           TEXT NOT NULL CHECK (modality IN ('rgb', 'thermal', 'fused')),
    confidence         REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    bbox_x             INTEGER NOT NULL,
    bbox_y             INTEGER NOT NULL,
    bbox_w             INTEGER NOT NULL,
    bbox_h             INTEGER NOT NULL,
    lat                REAL NOT NULL,
    lon                REAL NOT NULL,
    geo_error_m        REAL,
    uav_lat            REAL,
    uav_lon            REAL,
    uav_alt_agl_m      REAL,
    uav_heading_deg    REAL,
    gimbal_pitch_deg   REAL,
    weather_tag        TEXT CHECK (weather_tag IN ('clear', 'rain', 'fog', 'haze', 'night') OR weather_tag IS NULL),
    near_rescuer       INTEGER NOT NULL DEFAULT 0 CHECK (near_rescuer IN (0, 1)),
    frame_path         TEXT NOT NULL,               -- relative to images root
    crop_path          TEXT NOT NULL,
    thumb_path         TEXT NOT NULL,
    thermal_crop_path  TEXT,
    created_at         INTEGER NOT NULL
);

-- One row per real person on the ground = the rescue manifest.
CREATE TABLE IF NOT EXISTS victims (
    id                 TEXT PRIMARY KEY,
    display_code       TEXT NOT NULL UNIQUE,        -- V-0001, V-0002, ...
    mission_id         TEXT NOT NULL REFERENCES missions(id),
    status             TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('pending', 'open', 'ongoing', 'completed', 'dismissed')),
                       -- 'pending' = waiting for coordinator verification (alert_mode = 'verify'); never sent to phones
    verified           INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    lat                REAL NOT NULL,
    lon                REAL NOT NULL,
    position_error_m   REAL,
    best_detection_id  TEXT REFERENCES detections(id),
    max_confidence     REAL,
    modalities         TEXT CHECK (modalities IN ('rgb', 'thermal', 'both') OR modalities IS NULL),
    sighting_count     INTEGER NOT NULL DEFAULT 1,
    first_detected_at  INTEGER NOT NULL,
    last_seen_at       INTEGER NOT NULL,
    assigned_team_id   TEXT REFERENCES teams(id),
    accepted_at        INTEGER,
    completed_at       INTEGER,
    people_count       INTEGER,
    team_note          TEXT,
    coordinator_note   TEXT,
    source             TEXT NOT NULL DEFAULT 'pipeline' CHECK (source IN ('pipeline', 'manual')),
    version            INTEGER NOT NULL DEFAULT 1,  -- optimistic lock, +1 on every change
    created_at         INTEGER NOT NULL,
    updated_at         INTEGER NOT NULL,
    CHECK (status NOT IN ('ongoing', 'completed') OR assigned_team_id IS NOT NULL)
);

-- Append-only audit log. Rejected attempts are logged too (applied = 0).
CREATE TABLE IF NOT EXISTS status_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    victim_id        TEXT NOT NULL REFERENCES victims(id),
    from_status      TEXT,
    to_status        TEXT NOT NULL,
    actor_type       TEXT NOT NULL CHECK (actor_type IN ('team', 'coordinator', 'system')),
    team_id          TEXT REFERENCES teams(id),
    device_id        TEXT REFERENCES devices(id),
    client_event_id  TEXT UNIQUE,                  -- idempotency key from phone
    client_ts        INTEGER,
    server_ts        INTEGER NOT NULL,
    applied          INTEGER NOT NULL CHECK (applied IN (0, 1)),
    reject_reason    TEXT,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS team_locations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id          TEXT NOT NULL REFERENCES teams(id),
    device_id        TEXT NOT NULL REFERENCES devices(id),
    lat              REAL NOT NULL,
    lon              REAL NOT NULL,
    accuracy_m       REAL,
    altitude_m       REAL,
    speed_mps        REAL,
    heading_deg      REAL,
    battery_pct      INTEGER,
    fix_ts           INTEGER NOT NULL,             -- phone GPS time
    received_at      INTEGER NOT NULL,             -- laptop time
    client_point_id  TEXT NOT NULL UNIQUE          -- dedupes re-sent batches
);

CREATE TABLE IF NOT EXISTS uav_telemetry (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id        TEXT NOT NULL REFERENCES missions(id),
    ts                INTEGER NOT NULL,
    lat               REAL NOT NULL,
    lon               REAL NOT NULL,
    alt_msl_m         REAL,
    alt_agl_m         REAL,
    heading_deg       REAL,
    pitch_deg         REAL,
    roll_deg          REAL,
    gimbal_pitch_deg  REAL,
    ground_speed_mps  REAL,
    battery_pct       INTEGER
);

CREATE TABLE IF NOT EXISTS alert_receipts (
    victim_id     TEXT NOT NULL REFERENCES victims(id),
    device_id     TEXT NOT NULL REFERENCES devices(id),
    delivered_at  INTEGER,
    opened_at     INTEGER,
    PRIMARY KEY (victim_id, device_id)
);

-- Ordered change log. Every state change is written here first, then pushed.
-- scope_team_id NULL = visible to all teams; otherwise only to that team.
-- laptop_only = 1 for events phones never receive (team.location, device.*).
CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,
    entity_id      TEXT,
    scope_team_id  TEXT REFERENCES teams(id),
    laptop_only    INTEGER NOT NULL DEFAULT 0 CHECK (laptop_only IN (0, 1)),
    payload_json   TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS ix_victims_status          ON victims(status);
CREATE INDEX IF NOT EXISTS ix_victims_team            ON victims(assigned_team_id);
CREATE INDEX IF NOT EXISTS ix_victims_mission_status  ON victims(mission_id, status);
CREATE INDEX IF NOT EXISTS ix_detections_victim       ON detections(victim_id);
CREATE INDEX IF NOT EXISTS ix_detections_track        ON detections(mission_id, track_id);
CREATE INDEX IF NOT EXISTS ix_team_locations_team_ts  ON team_locations(team_id, fix_ts);
CREATE INDEX IF NOT EXISTS ix_uav_telemetry_ts        ON uav_telemetry(mission_id, ts);
CREATE INDEX IF NOT EXISTS ix_events_created          ON events(created_at);
CREATE INDEX IF NOT EXISTS ix_devices_team            ON devices(team_id);

-- Default settings (tunable from the GUI)
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('server_port',             '8000'),
    ('dedup_radius_min_m',      '15'),
    ('dedup_error_multiplier',  '2'),
    ('dedup_window_min',        '15'),
    ('rescuer_radius_m',        '20'),
    ('team_stale_after_s',      '120'),
    ('ws_ping_interval_s',      '15'),
    ('ws_dead_after_s',         '45'),
    ('pairing_token_ttl_min',   '10'),
    ('alert_repeat_s',          '30'),
    ('gps_max_accuracy_m',      '50'),
    ('map_file',                'maps/srilanka.pmtiles');

INSERT INTO schema_version (version, applied_at)
    SELECT 1, CAST(strftime('%s', 'now') AS INTEGER) * 1000
    WHERE NOT EXISTS (SELECT 1 FROM schema_version);
