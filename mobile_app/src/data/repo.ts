/**
 * The phone's local store (ARCHITECTURE.md 9.5): cached victims, outbox, GPS buffer, sync state.
 *
 * Two implementations: SqliteRepo (expo-sqlite, the app) and MemoryRepo (Jest). Both keep
 * the same rules, which live in this file so they cannot drift apart:
 *  - a server victim never overwrites the phone-only columns (thumbnail, pending action,
 *    alarm/opened timestamps), and
 *  - an older server version never overwrites a newer one. The outbox (HTTP response) and
 *    the WebSocket both deliver victims and can arrive out of order.
 */

import type { CachedVictim, LocationPoint, OutboxItem, SyncState, Victim } from '../core/types';

/** Phone-only columns of cached_victims. */
export type LocalVictimFields = Pick<CachedVictim, 'thumb_local_path' | 'local_pending_action' | 'alerted_at' | 'opened_at'>;

export const LOCAL_DEFAULTS: LocalVictimFields = {
  thumb_local_path: null,
  local_pending_action: null,
  alerted_at: null,
  opened_at: null,
};

/** Server columns of cached_victims, in table order. */
export const SERVER_VICTIM_FIELDS = [
  'display_code', 'status', 'lat', 'lon', 'position_error_m', 'max_confidence', 'modalities', 'sighting_count',
  'first_detected_at', 'last_seen_at', 'assigned_team_id', 'accepted_at', 'completed_at', 'people_count', 'source',
  'thumb_url', 'version', 'updated_seq',
] as const satisfies readonly (keyof Victim)[];

/** True when ``incoming`` should replace ``cached`` (same or newer server version). */
export function isNewer(incoming: Pick<Victim, 'version'>, cached: Pick<Victim, 'version'> | null): boolean {
  return !cached || (incoming.version ?? 0) >= (cached.version ?? 0);
}

export interface Repo {
  // sync state (single row) ------------------------------------------------
  getSyncState(): Promise<SyncState | null>;
  /** Merge into the row; the first call must carry every field. */
  saveSyncState(patch: Partial<SyncState>): Promise<void>;

  // victims ---------------------------------------------------------------
  listVictims(): Promise<CachedVictim[]>;
  getVictim(id: string): Promise<CachedVictim | null>;
  /** Insert or update the server columns (see the rules above); returns the stored row. */
  upsertServerVictim(v: Victim): Promise<CachedVictim>;
  patchVictim(id: string, patch: Partial<LocalVictimFields>): Promise<void>;
  deleteVictim(id: string): Promise<void>;
  /** Has this phone ever rung the siren for this victim? Survives deleteVictim. */
  wasAlerted(id: string): Promise<boolean>;

  // outbox (kept in insertion order) ---------------------------------------
  enqueue(item: OutboxItem): Promise<void>;
  listOutbox(): Promise<OutboxItem[]>;
  countOutbox(): Promise<number>;
  updateOutbox(clientEventId: string, patch: Partial<Pick<OutboxItem, 'attempts' | 'last_error'>>): Promise<void>;
  removeOutbox(clientEventId: string): Promise<void>;

  // GPS buffer -------------------------------------------------------------
  addLocation(p: LocationPoint): Promise<void>;
  /** Oldest points not yet uploaded. */
  pendingLocations(limit: number): Promise<LocationPoint[]>;
  countPendingLocations(): Promise<number>;
  markLocationsUploaded(ids: string[]): Promise<void>;
  /** Drop uploaded points older than ``beforeTs`` (fix time). */
  pruneLocations(beforeTs: number): Promise<void>;

  /** Forget everything (unpair / pairing with a new base). */
  clearAll(): Promise<void>;
}
