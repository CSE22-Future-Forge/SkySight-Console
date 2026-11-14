/**
 * The phone's background engine (ARCHITECTURE.md 9.4). One instance for the app's lifetime;
 * the foreground service keeps the process (and so this JS) alive in the background.
 *
 *  - BaseLink: WebSocket with replay from last_seq, ack, ping/pong, reconnect.
 *  - Outbox: actions are queued, shown optimistically and sent in order.
 *  - Sync tick: flush outbox -> upload GPS buffer -> fetch missing thumbnails.
 *  - Alarms: siren + full-screen intent for new open victims, repeated every
 *    alert_repeat_s until opened; receipts delivered/opened go back to the base.
 */

import * as Battery from 'expo-battery';
import * as Crypto from 'expo-crypto';
import * as Location from 'expo-location';
import * as SecureStore from 'expo-secure-store';

import SarNative from '../../modules/sar-native';
import { ApiClient, NetworkError } from '../core/api';
import { describeFrom } from '../core/geo';
import type { CachedVictim, ConnectionStatus, Envelope, LocationPoint, SyncState } from '../core/types';
import { SqliteRepo } from '../data/db';
import type { Repo } from '../data/repo';
import { store } from '../state/store';
import { applyEnvelope, type Effects } from './events';
import { mapFile, styleFile, thumbFile } from './files';
import { describeRejection, enqueueAction, flushOutbox, type OutboxDeps } from './outbox';
import { BaseLink } from '../core/ws';

export const TOKEN_KEY = 'sar_device_token';
const SYNC_EVERY_MS = 10_000;
const OFFLINE_FIX_EVERY_MS = 30_000;
const LOCATION_BATCH = 200;
const KEEP_UPLOADED_MS = 24 * 3600_000;

class Engine {
  repo: Repo | null = null;
  api: ApiClient | null = null;
  sync: SyncState | null = null;
  private link: BaseLink | null = null;
  private running = false;
  private syncing = false;
  private timers: ReturnType<typeof setInterval>[] = [];
  private subs: { remove(): void }[] = [];
  private lastAlarmShown = new Map<string, number>();
  private lastStoredFix = 0;
  private battery: number | null = null;
  private refreshQueued = false;

  // ------------------------------------------------------------------ boot ---
  async boot(): Promise<boolean> {
    this.repo ??= await SqliteRepo.open();
    this.sync = await this.repo.getSyncState();
    const token = await SecureStore.getItemAsync(TOKEN_KEY);
    if (!this.sync || !token) {
      store.set({ paired: false });
      return false;
    }
    this.api = new ApiClient(this.sync.server_url, token);
    store.set({
      paired: true,
      teamId: this.sync.team_id,
      teamName: this.sync.team_name,
      teamCallSign: this.sync.team_call_sign,
      teamColor: this.sync.team_color,
      missionName: this.sync.mission_name,
      settings: this.sync.settings,
      mapFile: mapFile().exists ? mapFile().uri : null,
      styleFile: styleFile().exists ? styleFile().uri : null,
    });
    await this.refresh();
    return true;
  }

  get outboxDeps(): OutboxDeps {
    return {
      repo: this.repo!,
      api: this.api!,
      myTeamId: this.sync!.team_id,
      now: () => Date.now(),
      newId: () => Crypto.randomUUID(),
    };
  }

  // ------------------------------------------------------------- lifecycle ---
  async start() {
    if (this.running || !this.api || !this.sync) return;
    this.running = true;
    SarNative.startService('SAR Rescue', `Connecting to SAR base - ${this.sync.team_name}`);
    SarNative.bindToWifi(8000).catch(() => false).then((b) => store.set({ wifiBound: !!b }));

    this.link = new BaseLink({
      url: (since) => this.api!.wsUrl(since),
      lastSeq: () => this.sync?.last_seq ?? 0,
      onEnvelope: (env) => this.onEnvelope(env),
      onStatus: (s) => this.onStatus(s),
      onOpen: () => void this.syncTick(),
      deadAfterMs: () => (this.sync?.settings.ws_dead_after_s ?? 45) * 1000,
    });
    this.link.start();

    this.subs.push(SarNative.addListener('onNetworkChange', ({ bound }) => {
      store.set({ wifiBound: bound });
      if (bound) {
        this.link?.kick();
        void this.syncTick();
      }
    }));
    this.timers.push(setInterval(() => void this.syncTick(), SYNC_EVERY_MS));
    this.timers.push(setInterval(() => void this.alarmTick(), 5_000));
    this.timers.push(setInterval(() => void this.readBattery(), 60_000));
    void this.readBattery();
    void this.startLocation();
    void this.refreshSettings();
  }

  async stop() {
    this.running = false;
    this.link?.stop();
    this.link = null;
    this.timers.forEach(clearInterval);
    this.timers = [];
    this.subs.forEach((s) => s.remove());
    this.subs = [];
    SarNative.stopService();
  }

  /** Forget this base completely (unpair / revoked). */
  async unpair() {
    await this.stop();
    await this.repo?.clearAll();
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    SarNative.unbindNetwork();
    this.api = null;
    this.sync = null;
    this.lastAlarmShown.clear();
    store.reset();
  }

  // ----------------------------------------------------------------- state ---
  /** Re-read the cache into the store (coalesced). */
  async refresh() {
    if (!this.repo || this.refreshQueued) return;
    this.refreshQueued = true;
    await Promise.resolve();
    this.refreshQueued = false;
    const [victims, queued, pendingPoints] = await Promise.all([
      this.repo.listVictims(), this.repo.countOutbox(), this.repo.countPendingLocations(),
    ]);
    store.set({ victims, queued, pendingPoints });
    this.updateServiceText();
  }

  private updateServiceText() {
    if (!this.running) return;
    const s = store.get();
    const open = s.victims.filter((v) => v.status === 'open').length;
    const mine = s.victims.filter((v) => v.status === 'ongoing' && v.assigned_team_id === s.teamId).length;
    const conn = s.conn === 'live' ? 'Connected to SAR base'
      : s.conn === 'connecting' ? 'Reconnecting to SAR base' : `Offline - ${s.queued} queued`;
    SarNative.updateService(conn, `${open} open · ${mine} with ${s.teamName ?? 'your team'}`);
  }

  private onStatus(status: ConnectionStatus) {
    store.set({ conn: status });
    this.updateServiceText();
    if (status === 'revoked') {
      store.toast('This phone was removed by the base. Pair it again.');
      void this.unpair();
    }
  }

  // ---------------------------------------------------------------- events ---
  private async onEnvelope(env: Envelope) {
    const fx = await applyEnvelope(this.repo!, env, this.sync!.team_id);
    this.sync!.last_seq = env.seq;
    await this.handleEffects(fx);
    if (fx.changed) await this.refresh();
  }

  private async handleEffects(fx: Effects) {
    for (const id of fx.cancel) {
      SarNative.cancelNotification(id);
      this.lastAlarmShown.delete(id);
      if (store.get().alertVictimId === id) store.set({ alertVictimId: null });
    }
    for (const info of fx.infos) SarNative.showInfo(info.id, info.title, info.body, info.victimId);
    for (const id of fx.alarms) {
      const v = await this.repo!.getVictim(id);
      if (v && v.status === 'open' && !v.alerted_at) await this.raiseAlarm(v);
    }
  }

  private alarmText(v: CachedVictim): { title: string; body: string } {
    const where = describeFrom(store.get().me, v);
    const conf = v.max_confidence != null ? ` · ${Math.round(v.max_confidence * 100)}% sure` : '';
    return {
      title: `NEW VICTIM ${v.display_code}`,
      body: `${where ? where.text : 'Position on map'}${conf} · tap to respond`,
    };
  }

  private async raiseAlarm(v: CachedVictim) {
    const thumb = await this.fetchThumb(v, 3000);
    const { title, body } = this.alarmText(v);
    SarNative.showAlarm(v.id, title, body, thumb, true);
    const now = Date.now();
    this.lastAlarmShown.set(v.id, now);
    await this.repo!.patchVictim(v.id, { alerted_at: now });
    await enqueueAction(this.outboxDeps, 'receipt', v.id, { delivered_at: now });
    store.set({ alertVictimId: v.id });
    void this.syncTick();
  }

  /** Siren repeats every alert_repeat_s until the alert is opened (9.3). */
  private async alarmTick() {
    if (!this.repo) return;
    const every = (this.sync?.settings.alert_repeat_s ?? 30) * 1000;
    const now = Date.now();
    for (const v of store.get().victims) {
      if (v.status !== 'open' || !v.alerted_at || v.opened_at) continue;
      if (now - (this.lastAlarmShown.get(v.id) ?? v.alerted_at) < every) continue;
      const { title, body } = this.alarmText(v);
      SarNative.showAlarm(v.id, title, body, v.thumb_local_path, true);
      this.lastAlarmShown.set(v.id, now);
    }
  }

  async markOpened(victimId: string) {
    const v = await this.repo?.getVictim(victimId);
    if (!v || v.opened_at) return;
    const now = Date.now();
    await this.repo!.patchVictim(victimId, { opened_at: now });
    SarNative.cancelNotification(victimId);
    this.lastAlarmShown.delete(victimId);
    await enqueueAction(this.outboxDeps, 'receipt', victimId, { opened_at: now });
    await this.refresh();
    void this.syncTick();
  }

  // --------------------------------------------------------------- actions ---
  async accept(victimId: string) {
    await this.markOpened(victimId);
    await enqueueAction(this.outboxDeps, 'accept', victimId);
    await this.afterAction();
  }

  async release(victimId: string, reason: string | null) {
    await enqueueAction(this.outboxDeps, 'release', victimId, { reason });
    await this.afterAction();
  }

  async complete(victimId: string, peopleCount: number | null, note: string | null) {
    await enqueueAction(this.outboxDeps, 'complete', victimId, { people_count: peopleCount, note });
    await this.afterAction();
  }

  private async afterAction() {
    await this.refresh();
    await this.syncTick();
  }

  // ------------------------------------------------------------------ sync ---
  async syncTick() {
    if (!this.api || !this.repo || this.syncing) return;
    this.syncing = true;
    try {
      const report = await flushOutbox(this.outboxDeps);
      for (const r of report.rejected) {
        const text = describeRejection(r);
        store.toast(text);
        SarNative.showInfo(`reject-${r.victimId}`, 'Not confirmed by base', text, null);
      }
      if (report.revoked) {
        this.onStatus('revoked');
        return;
      }
      if (!report.blocked) await this.uploadLocations();
      if (!report.blocked) await this.fetchMissingThumbs();
    } catch (e) {
      // keep everything queued; next tick retries
    } finally {
      this.syncing = false;
      await this.refresh();
    }
  }

  private async uploadLocations() {
    for (;;) {
      const batch = await this.repo!.pendingLocations(LOCATION_BATCH);
      if (!batch.length) break;
      try {
        await this.api!.locations(batch);
      } catch {
        return;
      }
      await this.repo!.markLocationsUploaded(batch.map((p) => p.client_point_id));
      if (batch.length < LOCATION_BATCH) break;
    }
    await this.repo!.pruneLocations(Date.now() - KEEP_UPLOADED_MS);
  }

  private async fetchThumb(v: CachedVictim, timeoutMs = 10_000): Promise<string | null> {
    if (v.thumb_local_path) return v.thumb_local_path;
    if (!v.thumb_url || !this.api) return null;
    const file = thumbFile(v.id);
    try {
      const download = SarNative.downloadFile(this.api.url(v.thumb_url), file.uri, this.api.authHeaders());
      await Promise.race([download, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs))]);
      await this.repo!.patchVictim(v.id, { thumb_local_path: file.uri });
      return file.uri;
    } catch {
      return null;
    }
  }

  private async fetchMissingThumbs() {
    const missing = store.get().victims.filter((v) => !v.thumb_local_path && v.thumb_url).slice(0, 4);
    for (const v of missing) await this.fetchThumb(v);
  }

  async refreshSettings() {
    if (!this.api) return;
    try {
      const me = await this.api.me();
      this.sync!.settings = me.settings;
      await this.repo!.saveSyncState({ settings: me.settings, team_name: me.team.name,
                                       team_call_sign: me.team.call_sign, team_color: me.team.color_hex });
      store.set({ settings: me.settings, teamName: me.team.name, teamCallSign: me.team.call_sign,
                  teamColor: me.team.color_hex });
    } catch (e) {
      if (!(e instanceof NetworkError)) {
        /* 401 handled by the link */
      }
    }
  }

  // -------------------------------------------------------------- location ---
  private async readBattery() {
    try {
      const level = await Battery.getBatteryLevelAsync();
      this.battery = level >= 0 ? Math.round(level * 100) : null;
    } catch {
      this.battery = null;
    }
  }

  private async startLocation() {
    const perm = await Location.getForegroundPermissionsAsync();
    if (!perm.granted) return;
    this.subs.push(await Location.watchPositionAsync(
      { accuracy: Location.Accuracy.High, timeInterval: 10_000, distanceInterval: 20 },
      (loc) => void this.onFix(loc),
    ));
    try {
      this.subs.push(await Location.watchHeadingAsync((h) => {
        const heading = h.trueHeading >= 0 ? h.trueHeading : h.magHeading;
        store.set({ compass: heading });
      }));
    } catch {
      /* no compass */
    }
  }

  /** Connected: every fix (10 s / 20 m). Offline: one every 30 s into the buffer (9.4). */
  private async onFix(loc: Location.LocationObject) {
    const c = loc.coords;
    store.set({ me: { lat: c.latitude, lon: c.longitude, accuracy: c.accuracy ?? null,
                      heading: c.heading != null && c.heading >= 0 ? c.heading : null, ts: loc.timestamp } });
    const online = store.get().conn === 'live';
    if (!online && loc.timestamp - this.lastStoredFix < OFFLINE_FIX_EVERY_MS) return;
    this.lastStoredFix = loc.timestamp;
    const point: LocationPoint = {
      client_point_id: Crypto.randomUUID(),
      lat: c.latitude,
      lon: c.longitude,
      accuracy_m: c.accuracy ?? null,
      altitude_m: c.altitude ?? null,
      speed_mps: c.speed != null && c.speed >= 0 ? c.speed : null,
      heading_deg: c.heading != null && c.heading >= 0 ? c.heading : null,
      battery_pct: this.battery,
      fix_ts: Math.round(loc.timestamp),
    };
    await this.repo?.addLocation(point);
    store.set({ pendingPoints: (await this.repo?.countPendingLocations()) ?? 0 });
  }

  /** Fetch a victim's detail/history from the base (online only). */
  async victimDetail(id: string) {
    if (!this.api) return null;
    try {
      const res = await this.api.victim(id);
      await this.repo!.upsertServerVictim(res.victim);
      await this.refresh();
      return res;
    } catch {
      return null;
    }
  }
}

export const engine = new Engine();
