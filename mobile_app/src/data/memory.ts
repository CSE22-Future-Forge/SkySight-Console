/** In-memory Repo for unit tests: same rules as SqliteRepo, no native module. */

import type { CachedVictim, LocationPoint, OutboxItem, SyncState, Victim } from '../core/types';
import { isNewer, LOCAL_DEFAULTS, type LocalVictimFields, type Repo } from './repo';

interface StoredPoint extends LocationPoint {
  uploaded: boolean;
}

export class MemoryRepo implements Repo {
  private sync: SyncState | null = null;
  private victims = new Map<string, CachedVictim>();
  private alerted = new Set<string>();
  private outbox: OutboxItem[] = [];
  private points = new Map<string, StoredPoint>();

  async getSyncState() {
    return this.sync ? { ...this.sync, settings: { ...this.sync.settings } } : null;
  }

  async saveSyncState(patch: Partial<SyncState>) {
    this.sync = { ...(this.sync ?? ({} as SyncState)), ...patch };
  }

  async listVictims() {
    return [...this.victims.values()].map((v) => ({ ...v })).sort((a, b) => b.first_detected_at - a.first_detected_at);
  }

  async getVictim(id: string) {
    const v = this.victims.get(id);
    return v ? { ...v } : null;
  }

  async upsertServerVictim(v: Victim) {
    const cached = this.victims.get(v.id) ?? null;
    if (cached && !isNewer(v, cached)) return { ...cached };
    const local: LocalVictimFields = cached
      ? { thumb_local_path: cached.thumb_local_path, local_pending_action: cached.local_pending_action,
          alerted_at: cached.alerted_at, opened_at: cached.opened_at }
      : LOCAL_DEFAULTS;
    const saved: CachedVictim = { ...v, ...local };
    this.victims.set(v.id, saved);
    return { ...saved };
  }

  async patchVictim(id: string, patch: Partial<LocalVictimFields>) {
    const v = this.victims.get(id);
    if (!v) return;
    this.victims.set(id, { ...v, ...patch });
    if (patch.alerted_at != null) this.alerted.add(id);
  }

  async deleteVictim(id: string) {
    this.victims.delete(id);
  }

  async wasAlerted(id: string) {
    return this.alerted.has(id);
  }

  async enqueue(item: OutboxItem) {
    this.outbox.push({ ...item });
  }

  async listOutbox() {
    return this.outbox.map((o) => ({ ...o }));
  }

  async countOutbox() {
    return this.outbox.length;
  }

  async updateOutbox(clientEventId: string, patch: Partial<Pick<OutboxItem, 'attempts' | 'last_error'>>) {
    this.outbox = this.outbox.map((o) => (o.client_event_id === clientEventId ? { ...o, ...patch } : o));
  }

  async removeOutbox(clientEventId: string) {
    this.outbox = this.outbox.filter((o) => o.client_event_id !== clientEventId);
  }

  async addLocation(p: LocationPoint) {
    if (!this.points.has(p.client_point_id)) this.points.set(p.client_point_id, { ...p, uploaded: false });
  }

  async pendingLocations(limit: number) {
    return [...this.points.values()]
      .filter((p) => !p.uploaded)
      .sort((a, b) => a.fix_ts - b.fix_ts)
      .slice(0, limit)
      .map(({ uploaded: _u, ...p }) => p);
  }

  async countPendingLocations() {
    return [...this.points.values()].filter((p) => !p.uploaded).length;
  }

  async markLocationsUploaded(ids: string[]) {
    for (const id of ids) {
      const p = this.points.get(id);
      if (p) p.uploaded = true;
    }
  }

  async pruneLocations(beforeTs: number) {
    for (const [id, p] of this.points) if (p.uploaded && p.fix_ts < beforeTs) this.points.delete(id);
  }

  async clearAll() {
    this.sync = null;
    this.victims.clear();
    this.alerted.clear();
    this.outbox = [];
    this.points.clear();
  }
}
