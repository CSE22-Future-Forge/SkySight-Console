/** Wire + local types. Field names follow the ground station API (docs/ARCHITECTURE.md 7). */

export type VictimStatus = 'pending' | 'open' | 'ongoing' | 'completed' | 'dismissed';

/** `Victim` as the server sends it to phones (7.1). */
export interface Victim {
  id: string;
  display_code: string;
  status: VictimStatus;
  lat: number;
  lon: number;
  position_error_m: number | null;
  max_confidence: number | null;
  modalities: 'rgb' | 'thermal' | 'both' | null;
  sighting_count: number;
  first_detected_at: number;
  last_seen_at: number;
  assigned_team_id: string | null;
  accepted_at: number | null;
  completed_at: number | null;
  people_count: number | null;
  source: 'pipeline' | 'manual';
  thumb_url: string | null;
  version: number;
  updated_seq: number | null;
}

export type PendingAction = 'accept' | 'release' | 'complete';

/** cached_victims row (9.5) = server victim + phone-only state. */
export interface CachedVictim extends Victim {
  thumb_local_path: string | null;
  local_pending_action: PendingAction | null;
  /** When the alarm for this victim was raised on this phone (null = never). */
  alerted_at: number | null;
  /** When the alert was opened (stops the siren repeats). */
  opened_at: number | null;
}

export interface Envelope {
  seq: number;
  type: string;
  ts: number;
  data: any;
}

export interface Team {
  id: string;
  name: string;
  call_sign: string | null;
  color_hex: string;
}

export interface Mission {
  id: string;
  name: string;
  area_name?: string | null;
}

export interface PairingPayload {
  server_url: string;
  pairing_token: string;
  ssid?: string;
}

/** Phone-side tunables, served by the base from its settings table. */
export interface PhoneSettings {
  alert_repeat_s: number;
  gps_max_accuracy_m: number;
  ws_ping_interval_s: number;
  ws_dead_after_s: number;
  team_stale_after_s: number;
}

export const DEFAULT_SETTINGS: PhoneSettings = {
  alert_repeat_s: 30,
  gps_max_accuracy_m: 50,
  ws_ping_interval_s: 15,
  ws_dead_after_s: 45,
  team_stale_after_s: 120,
};

/** sync_state row (9.5). */
export interface SyncState {
  server_url: string;
  device_id: string;
  team_id: string;
  team_name: string;
  team_call_sign: string | null;
  team_color: string;
  mission_name: string | null;
  last_seq: number;
  map_sha256: string | null;
  settings: PhoneSettings;
}

export type OutboxKind = 'accept' | 'release' | 'complete' | 'receipt';

/** outbox row (9.5). */
export interface OutboxItem {
  client_event_id: string;
  kind: OutboxKind;
  victim_id: string;
  payload_json: string;
  client_ts: number;
  attempts: number;
  last_error: string | null;
  created_at: number;
}

/** location_buffer row (9.5). */
export interface LocationPoint {
  client_point_id: string;
  lat: number;
  lon: number;
  accuracy_m: number | null;
  altitude_m: number | null;
  speed_mps: number | null;
  heading_deg: number | null;
  battery_pct: number | null;
  fix_ts: number;
}

export type ConnectionStatus = 'live' | 'connecting' | 'offline' | 'revoked';
