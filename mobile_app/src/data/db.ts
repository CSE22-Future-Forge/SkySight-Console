/**
 * SqliteRepo: the phone's local DB on expo-sqlite (ARCHITECTURE.md 9.5).
 *
 * Every write is one statement, so the WebSocket handler and the outbox flush (which run
 * concurrently) can never interleave a read-modify-write: a server upsert only touches the
 * server columns, and only when its version is not older than the cached one.
 */

import * as SQLite from 'expo-sqlite';

import type { CachedVictim, LocationPoint, OutboxItem, PhoneSettings, SyncState, Victim } from '../core/types';
import { DEFAULT_SETTINGS } from '../core/types';
import { type LocalVictimFields, type Repo, SERVER_VICTIM_FIELDS } from './repo';

const DB_NAME = 'sar_rescue.db';
/** Bump with a migration below when a table changes. */
const SCHEMA_VERSION = 2;

const TABLES = ['cached_victims', 'alert_log', 'outbox', 'location_buffer', 'sync_state'];

const SCHEMA = `
CREATE TABLE IF NOT EXISTS cached_victims (
  id                   TEXT PRIMARY KEY NOT NULL,
  display_code         TEXT NOT NULL,
  status               TEXT NOT NULL,
  lat                  REAL NOT NULL,
  lon                  REAL NOT NULL,
  position_error_m     REAL,
  max_confidence       REAL,
  modalities           TEXT,
  sighting_count       INTEGER NOT NULL DEFAULT 1,
  first_detected_at    INTEGER NOT NULL,
  last_seen_at         INTEGER NOT NULL,
  assigned_team_id     TEXT,
  accepted_at          INTEGER,
  completed_at         INTEGER,
  people_count         INTEGER,
  source               TEXT NOT NULL DEFAULT 'pipeline',
  thumb_url            TEXT,
  version              INTEGER NOT NULL DEFAULT 0,
  updated_seq          INTEGER,
  thumb_local_path     TEXT,
  local_pending_action TEXT,
  alerted_at           INTEGER,
  opened_at            INTEGER
);
CREATE TABLE IF NOT EXISTS alert_log (
  victim_id   TEXT PRIMARY KEY NOT NULL,
  alerted_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  kind            TEXT NOT NULL,
  victim_id       TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  client_ts       INTEGER NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT,
  created_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS location_buffer (
  client_point_id TEXT PRIMARY KEY NOT NULL,
  lat             REAL NOT NULL,
  lon             REAL NOT NULL,
  accuracy_m      REAL,
  altitude_m      REAL,
  speed_mps       REAL,
  heading_deg     REAL,
  battery_pct     INTEGER,
  fix_ts          INTEGER NOT NULL,
  uploaded        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_location_pending ON location_buffer (uploaded, fix_ts);
CREATE TABLE IF NOT EXISTS sync_state (
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  server_url     TEXT NOT NULL,
  device_id      TEXT NOT NULL,
  team_id        TEXT NOT NULL,
  team_name      TEXT NOT NULL,
  team_call_sign TEXT,
  team_color     TEXT NOT NULL,
  mission_name   TEXT,
  last_seq       INTEGER NOT NULL DEFAULT 0,
  map_sha256     TEXT,
  settings_json  TEXT NOT NULL
);
`;

const LOCAL_COLUMNS: (keyof LocalVictimFields)[] = ['thumb_local_path', 'local_pending_action', 'alerted_at', 'opened_at'];
const SYNC_COLUMNS = ['server_url', 'device_id', 'team_id', 'team_name', 'team_call_sign', 'team_color', 'mission_name',
  'last_seq', 'map_sha256'] as const;

type Bind = string | number | null;

interface SyncRow extends Omit<SyncState, 'settings'> {
  settings_json: string;
}

const UPSERT_VICTIM = `
INSERT INTO cached_victims (id, ${SERVER_VICTIM_FIELDS.join(', ')})
VALUES (?, ${SERVER_VICTIM_FIELDS.map(() => '?').join(', ')})
ON CONFLICT (id) DO UPDATE SET ${SERVER_VICTIM_FIELDS.map((c) => `${c} = excluded.${c}`).join(', ')}
WHERE excluded.version >= cached_victims.version`;

function parseSettings(text: string | null | undefined): PhoneSettings {
  try {
    return { ...DEFAULT_SETTINGS, ...(text ? JSON.parse(text) : {}) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export class SqliteRepo implements Repo {
  private constructor(private db: SQLite.SQLiteDatabase) {}

  static async open(): Promise<SqliteRepo> {
    const db = await SQLite.openDatabaseAsync(DB_NAME);
    await db.execAsync('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
    const row = await db.getFirstAsync<{ user_version: number }>('PRAGMA user_version');
    if ((row?.user_version ?? 0) !== SCHEMA_VERSION) {
      // Everything here can be fetched again from the base; an unknown layout is rebuilt
      // (the phone then asks to pair again) rather than half-migrated.
      await db.execAsync(TABLES.map((t) => `DROP TABLE IF EXISTS ${t};`).join('\n'));
      await db.execAsync(`${SCHEMA}\nPRAGMA user_version = ${SCHEMA_VERSION};`);
    } else {
      await db.execAsync(SCHEMA);
    }
    return new SqliteRepo(db);
  }

  // ---------------------------------------------------------------- sync ---
  async getSyncState(): Promise<SyncState | null> {
    const row = await this.db.getFirstAsync<SyncRow>('SELECT * FROM sync_state WHERE id = 1');
    if (!row) return null;
    const { settings_json, ...rest } = row;
    delete (rest as any).id;
    return { ...rest, settings: parseSettings(settings_json) };
  }

  async saveSyncState(patch: Partial<SyncState>) {
    // Only the patched columns: last_seq (every envelope) and settings (refresh) are saved
    // concurrently, and a whole-row write could put an older last_seq back.
    const cols = SYNC_COLUMNS.filter((c) => c in patch);
    const sets = cols.map((c) => `${c} = ?`);
    const values: Bind[] = cols.map((c) => (patch[c] ?? null) as Bind);
    if (patch.settings) {
      sets.push('settings_json = ?');
      values.push(JSON.stringify(patch.settings));
    }
    if (sets.length) {
      const res = await this.db.runAsync(`UPDATE sync_state SET ${sets.join(', ')} WHERE id = 1`, values);
      if (res.changes > 0) return;
    }
    const row = { ...patch } as Partial<SyncState>;
    await this.db.runAsync(
      `INSERT OR IGNORE INTO sync_state (id, ${SYNC_COLUMNS.join(', ')}, settings_json)
       VALUES (1, ${SYNC_COLUMNS.map(() => '?').join(', ')}, ?)`,
      [...SYNC_COLUMNS.map((c) => (row[c] ?? (c === 'last_seq' ? 0 : null)) as Bind),
       JSON.stringify(row.settings ?? DEFAULT_SETTINGS)],
    );
  }

  // ------------------------------------------------------------- victims ---
  async listVictims() {
    return this.db.getAllAsync<CachedVictim>('SELECT * FROM cached_victims ORDER BY first_detected_at DESC');
  }

  async getVictim(id: string) {
    return this.db.getFirstAsync<CachedVictim>('SELECT * FROM cached_victims WHERE id = ?', [id]);
  }

  async upsertServerVictim(v: Victim) {
    const values = SERVER_VICTIM_FIELDS.map((c) => (v[c] ?? null) as Bind);
    await this.db.runAsync(UPSERT_VICTIM, [v.id, ...values]);
    return (await this.getVictim(v.id))!;
  }

  async patchVictim(id: string, patch: Partial<LocalVictimFields>) {
    const cols = LOCAL_COLUMNS.filter((c) => c in patch);
    if (!cols.length) return;
    await this.db.runAsync(
      `UPDATE cached_victims SET ${cols.map((c) => `${c} = ?`).join(', ')} WHERE id = ?`,
      [...cols.map((c) => (patch[c] ?? null) as Bind), id],
    );
    if (patch.alerted_at != null) {
      await this.db.runAsync('INSERT OR IGNORE INTO alert_log (victim_id, alerted_at) VALUES (?, ?)',
                             [id, patch.alerted_at]);
    }
  }

  async deleteVictim(id: string) {
    await this.db.runAsync('DELETE FROM cached_victims WHERE id = ?', [id]);
  }

  async wasAlerted(id: string) {
    return !!(await this.db.getFirstAsync('SELECT 1 AS hit FROM alert_log WHERE victim_id = ?', [id]));
  }

  // -------------------------------------------------------------- outbox ---
  async enqueue(item: OutboxItem) {
    await this.db.runAsync(
      `INSERT OR IGNORE INTO outbox (client_event_id, kind, victim_id, payload_json, client_ts, attempts, last_error,
                                     created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [item.client_event_id, item.kind, item.victim_id, item.payload_json, item.client_ts, item.attempts,
       item.last_error, item.created_at],
    );
  }

  async listOutbox() {
    return this.db.getAllAsync<OutboxItem>(
      `SELECT client_event_id, kind, victim_id, payload_json, client_ts, attempts, last_error, created_at
       FROM outbox ORDER BY seq`);
  }

  async countOutbox() {
    return (await this.db.getFirstAsync<{ n: number }>('SELECT COUNT(*) AS n FROM outbox'))?.n ?? 0;
  }

  async updateOutbox(clientEventId: string, patch: Partial<Pick<OutboxItem, 'attempts' | 'last_error'>>) {
    const cols = (['attempts', 'last_error'] as const).filter((c) => c in patch);
    if (!cols.length) return;
    await this.db.runAsync(
      `UPDATE outbox SET ${cols.map((c) => `${c} = ?`).join(', ')} WHERE client_event_id = ?`,
      [...cols.map((c) => (patch[c] ?? null) as Bind), clientEventId],
    );
  }

  async removeOutbox(clientEventId: string) {
    await this.db.runAsync('DELETE FROM outbox WHERE client_event_id = ?', [clientEventId]);
  }

  // ---------------------------------------------------------- GPS buffer ---
  async addLocation(p: LocationPoint) {
    await this.db.runAsync(
      `INSERT OR IGNORE INTO location_buffer (client_point_id, lat, lon, accuracy_m, altitude_m, speed_mps, heading_deg,
                                              battery_pct, fix_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [p.client_point_id, p.lat, p.lon, p.accuracy_m, p.altitude_m, p.speed_mps, p.heading_deg, p.battery_pct,
       p.fix_ts],
    );
  }

  async pendingLocations(limit: number) {
    return this.db.getAllAsync<LocationPoint>(
      `SELECT client_point_id, lat, lon, accuracy_m, altitude_m, speed_mps, heading_deg, battery_pct, fix_ts
       FROM location_buffer WHERE uploaded = 0 ORDER BY fix_ts LIMIT ?`, [limit]);
  }

  async countPendingLocations() {
    return (await this.db.getFirstAsync<{ n: number }>(
      'SELECT COUNT(*) AS n FROM location_buffer WHERE uploaded = 0'))?.n ?? 0;
  }

  async markLocationsUploaded(ids: string[]) {
    if (!ids.length) return;
    await this.db.runAsync(
      `UPDATE location_buffer SET uploaded = 1 WHERE client_point_id IN (${ids.map(() => '?').join(', ')})`, ids);
  }

  async pruneLocations(beforeTs: number) {
    await this.db.runAsync('DELETE FROM location_buffer WHERE uploaded = 1 AND fix_ts < ?', [beforeTs]);
  }

  async clearAll() {
    await this.db.execAsync(TABLES.map((t) => `DELETE FROM ${t};`).join('\n'));
  }
}
