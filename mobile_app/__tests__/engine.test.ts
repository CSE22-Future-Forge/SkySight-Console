/**
 * The engine's alarm path with two phones in play: what the base sends to the *other* phone
 * when one phone acts. Real Engine, BaseLink and MemoryRepo; only native modules are faked.
 */

import { DEFAULT_SETTINGS, type Envelope } from '../src/core/types';
import { MemoryRepo } from '../src/data/memory';
import { ALPHA, BRAVO, victim } from './fixtures';

const mockRepo = new MemoryRepo();

jest.mock('../modules/sar-native', () => ({
  __esModule: true,
  default: {
    startService: jest.fn(), updateService: jest.fn(), stopService: jest.fn(), unbindNetwork: jest.fn(),
    bindToWifi: jest.fn(async () => true),
    addListener: jest.fn(() => ({ remove: jest.fn() })),
    showAlarm: jest.fn(), showInfo: jest.fn(), cancelNotification: jest.fn(),
    downloadFile: jest.fn(async () => 0),
  },
}));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => 'device-token'), setItemAsync: jest.fn(), deleteItemAsync: jest.fn(),
}));
jest.mock('expo-battery', () => ({ getBatteryLevelAsync: jest.fn(async () => 0.8) }));
jest.mock('expo-location', () => ({
  getForegroundPermissionsAsync: jest.fn(async () => ({ granted: false })), Accuracy: { High: 4 },
}));
jest.mock('expo-crypto', () => {
  let n = 0;
  return { randomUUID: () => `uuid-${++n}` };
});
jest.mock('../src/engine/files', () => ({
  mapFile: () => ({ exists: false, uri: 'file:///map.pmtiles' }),
  styleFile: () => ({ exists: false, uri: 'file:///style.json' }),
  thumbFile: (id: string) => ({ uri: `file:///thumbs/${id}.jpg` }),
}));
jest.mock('../src/data/db', () => ({ SqliteRepo: { open: async () => mockRepo } }));

// eslint-disable-next-line import/first
import SarNative from '../modules/sar-native';
// eslint-disable-next-line import/first
import { engine } from '../src/engine/engine';
// eslint-disable-next-line import/first
import { store, unseenAlerts } from '../src/state/store';

const native = SarNative as unknown as Record<string, jest.Mock>;

class FakeSocket {
  static last: FakeSocket | null = null;
  readyState = 0;
  onopen: ((ev: any) => void) | null = null;
  onmessage: ((ev: { data: any }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: any) => void) | null = null;
  sent: string[] = [];
  constructor(public url: string) {
    FakeSocket.last = this;
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.({});
    }, 0);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
}

let seq = 0;
function push(type: string, data: any) {
  const env: Envelope = { seq: ++seq, type, ts: Date.now(), data };
  FakeSocket.last!.onmessage?.({ data: JSON.stringify(env) });
}

const settle = (ms = 450) => new Promise((r) => setTimeout(r, ms));

beforeAll(async () => {
  (globalThis as any).WebSocket = FakeSocket;
  (globalThis as any).fetch = jest.fn(async (url: string) => {
    const body = url.endsWith('/api/v1/me')
      ? { settings: DEFAULT_SETTINGS, team: { id: ALPHA, name: 'Alpha', call_sign: 'A1', color_hex: '#1f77b4' } }
      : {};
    return { status: url.includes('/receipt') ? 204 : 200, ok: true, json: async () => body, text: async () => '' };
  });
  await mockRepo.saveSyncState({
    server_url: 'http://base:8000', device_id: 'phone-2', team_id: ALPHA, team_name: 'Alpha', team_call_sign: 'A1',
    team_color: '#1f77b4', mission_name: 'Test', last_seq: 0, map_sha256: null, settings: DEFAULT_SETTINGS,
  });
  expect(await engine.boot()).toBe(true);
  await engine.start();
  await settle(50);
});

afterAll(async () => {
  await engine.stop();
});

beforeEach(() => {
  native.showAlarm.mockClear();
  native.showInfo.mockClear();
  native.cancelNotification.mockClear();
});

describe('engine alarms with two phones', () => {
  it('a replay of "found" then "taken by the other phone" rings nothing', async () => {
    const v = victim({ id: 'v-replayed' });
    push('victim.created', v);
    push('victim.hidden', { id: v.id, reason: 'claimed' });
    await settle();
    expect(native.showAlarm).not.toHaveBeenCalled();
    expect(native.cancelNotification).toHaveBeenCalledWith(v.id);
    expect(await mockRepo.getVictim(v.id)).toBeNull();
    expect(unseenAlerts(store.get().victims)).toEqual([]);
  });

  it('two victims found together both ring and both wait for the alert screen', async () => {
    const a = victim({ id: 'v-a' });
    const b = victim({ id: 'v-b' });
    push('victim.created', a);
    push('victim.created', b);
    await settle();
    expect(native.showAlarm.mock.calls.map((c) => c[0]).sort()).toEqual(['v-a', 'v-b']);
    expect(native.showAlarm.mock.calls[0][1]).toMatch(/^NEW VICTIM V-/);
    expect(unseenAlerts(store.get().victims).map((v) => v.id).sort()).toEqual(['v-a', 'v-b']);
  });

  it('a teammate accepting on their phone silences this phone and says why', async () => {
    const a = (await mockRepo.getVictim('v-a'))!;
    push('victim.updated', { ...a, status: 'ongoing', assigned_team_id: ALPHA, version: a.version + 1 });
    await settle();
    expect(native.cancelNotification).toHaveBeenCalledWith('v-a');
    expect(native.showInfo).toHaveBeenCalledWith('teammate-v-a', `Your team is on ${a.display_code}`,
                                                 expect.any(String), 'v-a');
    expect(native.showAlarm).not.toHaveBeenCalled();
    expect(unseenAlerts(store.get().victims).map((v) => v.id)).toEqual(['v-b']);
  });

  it('another team taking it removes it and cancels the siren', async () => {
    push('victim.hidden', { id: 'v-b', reason: 'claimed' });
    await settle();
    expect(native.cancelNotification).toHaveBeenCalledWith('v-b');
    expect(unseenAlerts(store.get().victims)).toEqual([]);
  });

  it('released again by the other team: rings as "needs a team again", not "new victim"', async () => {
    const b = victim({ id: 'v-b', display_code: 'V-0099', assigned_team_id: null, version: 3 });
    push('victim.updated', b);
    await settle();
    expect(native.showAlarm).toHaveBeenCalledTimes(1);
    expect(native.showAlarm.mock.calls[0][1]).toBe('V-0099 NEEDS A TEAM AGAIN');
  });

  it('opening the alert stops it being shown again and sends an opened receipt', async () => {
    await engine.markOpened('v-b');
    expect(unseenAlerts(store.get().victims)).toEqual([]);
    expect(native.cancelNotification).toHaveBeenCalledWith('v-b');
    await settle(100);
    const opened = ((globalThis as any).fetch as jest.Mock).mock.calls
      .filter(([url, init]) => String(url).endsWith('/alerts/v-b/receipt') && /opened_at/.test(init?.body ?? ''));
    expect(opened.length).toBeGreaterThan(0);
  });

  it('a victim from another team never alarms when it arrives already taken', async () => {
    push('victim.updated', victim({ id: 'v-c', status: 'ongoing', assigned_team_id: BRAVO }));
    await settle();
    expect(native.showAlarm).not.toHaveBeenCalled();
  });
});
