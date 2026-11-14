/** App state: a tiny observable store read by screens through useStore (useSyncExternalStore). */

import { useSyncExternalStore } from 'react';

import type { CachedVictim, ConnectionStatus, PhoneSettings } from '../core/types';
import { DEFAULT_SETTINGS } from '../core/types';

export interface MyPosition {
  lat: number;
  lon: number;
  accuracy: number | null;
  heading: number | null;
  ts: number;
}

export interface AppState {
  /** null while booting. */
  paired: boolean | null;
  teamId: string | null;
  teamName: string | null;
  teamCallSign: string | null;
  teamColor: string;
  missionName: string | null;
  victims: CachedVictim[];
  conn: ConnectionStatus;
  wifiBound: boolean;
  /** Outbox items not yet confirmed by the base. */
  queued: number;
  pendingPoints: number;
  me: MyPosition | null;
  compass: number | null;
  settings: PhoneSettings;
  /** Short message for the snackbar (e.g. "V-0003: Bravo already accepted it."). */
  toast: { text: string; at: number } | null;
  /** Victim whose full-screen alert should be shown. */
  alertVictimId: string | null;
  mapFile: string | null;
  styleFile: string | null;
}

const initial: AppState = {
  paired: null,
  teamId: null,
  teamName: null,
  teamCallSign: null,
  teamColor: '#1f77b4',
  missionName: null,
  victims: [],
  conn: 'offline',
  wifiBound: false,
  queued: 0,
  pendingPoints: 0,
  me: null,
  compass: null,
  settings: DEFAULT_SETTINGS,
  toast: null,
  alertVictimId: null,
  mapFile: null,
  styleFile: null,
};

let state: AppState = initial;
const listeners = new Set<() => void>();

export const store = {
  get: () => state,
  set(patch: Partial<AppState>) {
    state = { ...state, ...patch };
    listeners.forEach((l) => l());
  },
  reset() {
    state = { ...initial, paired: false };
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  toast(text: string) {
    store.set({ toast: { text, at: Date.now() } });
  },
};

export function useStore<T>(select: (s: AppState) => T): T {
  return useSyncExternalStore(store.subscribe, () => select(state), () => select(state));
}

export function useVictim(id: string | undefined): CachedVictim | undefined {
  return useStore((s) => s.victims.find((v) => v.id === id));
}
