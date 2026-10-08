/**
 * Pairing (ARCHITECTURE.md 9.3 step 1): scan QR -> bind Wi-Fi -> POST /pair -> choose team
 * (if the code does not name one) -> snapshot -> download map + verify SHA-256 -> test WS.
 * The phone cannot finish pairing without the offline map.
 */

import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as SecureStore from 'expo-secure-store';

import SarNative from '../../modules/sar-native';
import { ApiClient, ApiError } from '../core/api';
import type { PairingPayload, Team } from '../core/types';
import { DEFAULT_SETTINGS } from '../core/types';
import { SqliteRepo } from '../data/db';
import { store } from '../state/store';
import { engine, TOKEN_KEY } from './engine';
import { mapFile, styleFile } from './files';
import { mapAction, phoneStyle } from './mapStyle';

export type StepId = 'wifi' | 'pair' | 'snapshot' | 'map' | 'link';
export type StepState = 'todo' | 'running' | 'done' | 'warn' | 'error';

export interface PairingProgress {
  step: StepId;
  state: StepState;
  detail?: string;
  /** 0..1 while downloading the map. */
  fraction?: number;
}

export class TeamRequired extends Error {
  constructor(public teams: Team[]) {
    super('Choose your team');
  }
}

export function deviceLabel(): string {
  return [Device.manufacturer, Device.modelName].filter(Boolean).join(' ') || 'Android phone';
}

/** Steps 1-3: network + /pair + snapshot. Throws TeamRequired when the code has no team. */
export async function pairDevice(code: PairingPayload, teamId: string | null, label: string,
                                 progress: (p: PairingProgress) => void): Promise<void> {
  progress({ step: 'wifi', state: 'running', detail: `Joining ${code.ssid ?? 'SAR-NET'}` });
  const bound = await SarNative.bindToWifi(10_000).catch(() => false);
  store.set({ wifiBound: bound });
  progress({ step: 'wifi', state: bound ? 'done' : 'warn',
             detail: bound ? 'Using Wi-Fi without internet' : 'Not bound to Wi-Fi - trying the current network' });

  progress({ step: 'pair', state: 'running', detail: code.server_url });
  const api = new ApiClient(code.server_url);
  let res;
  try {
    res = await api.pair(code.pairing_token, label, teamId, String(Constants.expoConfig?.version ?? '0'),
                         `Android ${Device.osVersion ?? '?'}`);
  } catch (e) {
    if (e instanceof ApiError && e.code === 'team_required') {
      progress({ step: 'pair', state: 'todo', detail: 'Choose your team' });
      throw new TeamRequired(e.body?.teams ?? []);
    }
    progress({ step: 'pair', state: 'error', detail: (e as Error).message });
    throw e;
  }
  await SecureStore.setItemAsync(TOKEN_KEY, res.device_token);
  api.setToken(res.device_token);
  const repo = engine.repo ?? (engine.repo = await SqliteRepo.open());
  await repo.clearAll();
  await repo.saveSyncState({
    server_url: code.server_url, device_id: res.device_id, team_id: res.team.id, team_name: res.team.name,
    team_call_sign: res.team.call_sign, team_color: res.team.color_hex, mission_name: res.mission?.name ?? null,
    last_seq: 0, map_sha256: null, settings: DEFAULT_SETTINGS,
  });
  progress({ step: 'pair', state: 'done', detail: `Team ${res.team.name}` });

  progress({ step: 'snapshot', state: 'running' });
  const me = await api.me();
  const snap = await api.victims(0);
  for (const v of snap.victims) {
    // Victims that existed before pairing are on the map, but do not ring the siren.
    const saved = await repo.upsertServerVictim(v);
    await repo.patchVictim(saved.id, { alerted_at: Date.now(), opened_at: Date.now() });
  }
  await repo.saveSyncState({ last_seq: snap.last_seq, settings: me.settings });
  progress({ step: 'snapshot', state: 'done', detail: `${snap.victims.length} victims on the manifest` });
}

/** Step 4: offline map (resumable, checksum against /maps/manifest) + phone style. */
export async function downloadMap(progress: (p: PairingProgress) => void): Promise<void> {
  const repo = engine.repo!;
  const sync = (await repo.getSyncState())!;
  const token = await SecureStore.getItemAsync(TOKEN_KEY);
  const api = new ApiClient(sync.server_url, token);
  progress({ step: 'map', state: 'running', detail: 'Checking map on base', fraction: 0 });
  const manifest = await api.mapManifest();
  const file = mapFile();
  let action = mapAction({ exists: file.exists, size: file.exists ? file.size : null, knownSha: sync.map_sha256 },
                         manifest);
  if (action === 'verify') {
    // Re-pairing forgets the remembered checksum; the 109 MB file is usually still here and current.
    progress({ step: 'map', state: 'running', detail: 'Checking the map already on this phone', fraction: 1 });
    if ((await SarNative.sha256File(file.uri)) === manifest.sha256) {
      await repo.saveSyncState({ map_sha256: manifest.sha256 });
      action = 'keep';
    } else {
      action = 'download'; // the base has a newer map
    }
  }
  if (action === 'download') {
    const sub = SarNative.addListener('onDownloadProgress', (e) => {
      const total = e.total > 0 ? e.total : manifest.size;
      progress({ step: 'map', state: 'running', fraction: e.written / total,
                 detail: `${(e.written / 1e6).toFixed(0)} of ${(total / 1e6).toFixed(0)} MB` });
    });
    try {
      await SarNative.downloadFile(api.url(`/maps/${manifest.file}`), file.uri, api.authHeaders());
    } finally {
      sub.remove();
    }
    progress({ step: 'map', state: 'running', detail: 'Verifying checksum', fraction: 1 });
    const sha = await SarNative.sha256File(file.uri);
    if (sha !== manifest.sha256) {
      file.delete();
      throw new Error('Map file is corrupt (checksum mismatch). Try again.');
    }
    await repo.saveSyncState({ map_sha256: sha });
  }
  const style = phoneStyle(await api.styleText(), file.uri);
  styleFile().write(JSON.stringify(style));
  store.set({ mapFile: file.uri, styleFile: styleFile().uri });
  progress({ step: 'map', state: 'done', fraction: 1,
             detail: `${(manifest.size / 1e6).toFixed(0)} MB, checksum OK${action === 'keep' ? ' (already on phone)' : ''}` });
}

/** Step 5: the WebSocket opens and authenticates. */
export async function testLink(progress: (p: PairingProgress) => void): Promise<void> {
  progress({ step: 'link', state: 'running' });
  const sync = (await engine.repo!.getSyncState())!;
  const api = new ApiClient(sync.server_url, await SecureStore.getItemAsync(TOKEN_KEY));
  await new Promise<void>((resolve, reject) => {
    const ws = new WebSocket(api.wsUrl(sync.last_seq));
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error('No live link to the base'));
    }, 10_000);
    ws.onopen = () => {
      clearTimeout(timer);
      ws.close(1000, 'pairing test');
      resolve();
    };
    ws.onclose = (e) => {
      clearTimeout(timer);
      if (e.code >= 4400) reject(new Error('The base rejected this phone'));
    };
  });
  progress({ step: 'link', state: 'done', detail: 'Live alerts working' });
}
