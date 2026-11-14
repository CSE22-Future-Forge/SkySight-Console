/** REST client for the ground station (/api/v1, /maps). Plain fetch with timeouts. */

import type { LocationPoint, Mission, PhoneSettings, Team, Victim } from './types';

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body: any = {},
  ) {
    super(message);
  }
}

/** The base could not be reached at all (out of Wi-Fi range, laptop off). */
export class NetworkError extends Error {}

export type FetchLike = (url: string, init?: any) => Promise<{ status: number; ok: boolean; json(): Promise<any>; text(): Promise<string> }>;

export interface PairResponse {
  device_id: string;
  device_token: string;
  team: Team;
  mission: Mission | null;
  server_time: number;
}

export interface MeResponse {
  device: { id: string; label: string };
  team: Team;
  assignments: Victim[];
  settings: PhoneSettings;
  server_time: number;
}

export class ApiClient {
  constructor(
    public baseUrl: string,
    private token: string | null = null,
    private fetchImpl: FetchLike = (globalThis as any).fetch,
    private timeoutMs = 10_000,
  ) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
  }

  setToken(token: string | null) {
    this.token = token;
  }

  authHeaders(): Record<string, string> {
    return this.token ? { Authorization: `Bearer ${this.token}` } : {};
  }

  url(path: string): string {
    return path.startsWith('http') ? path : `${this.baseUrl}${path}`;
  }

  async request<T = any>(method: string, path: string, body?: unknown, timeoutMs = this.timeoutMs): Promise<T> {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    let res;
    try {
      res = await this.fetchImpl(this.url(path), {
        method,
        headers: { 'Content-Type': 'application/json', ...this.authHeaders() },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller?.signal,
      });
    } catch (e: any) {
      throw new NetworkError(e?.message ?? 'network error');
    } finally {
      if (timer) clearTimeout(timer);
    }
    if (res.status === 204) return undefined as T;
    let data: any = {};
    try {
      data = await res.json();
    } catch {
      data = {};
    }
    if (!res.ok) {
      throw new ApiError(res.status, data?.error ?? `http_${res.status}`, data?.message ?? `HTTP ${res.status}`, data);
    }
    return data as T;
  }

  // ------------------------------------------------------------- endpoints ---
  pair(pairing_token: string, device_label: string, team_id: string | null, app_version: string, os_version: string) {
    return this.request<PairResponse>('POST', '/api/v1/pair', { pairing_token, device_label, team_id, app_version, os_version });
  }
  health() {
    return this.request<{ server_time: number; version: string; mission: Mission | null }>('GET', '/api/v1/health', undefined, 5000);
  }
  me() {
    return this.request<MeResponse>('GET', '/api/v1/me');
  }
  victims(sinceSeq = 0) {
    return this.request<{ victims: Victim[]; last_seq: number }>('GET', `/api/v1/victims?since_seq=${sinceSeq}`);
  }
  victim(id: string) {
    return this.request<{ victim: Victim; history: any[] }>('GET', `/api/v1/victims/${id}`);
  }
  accept(id: string, client_event_id: string, client_ts: number) {
    return this.request<{ victim: Victim }>('POST', `/api/v1/victims/${id}/accept`, { client_event_id, client_ts });
  }
  release(id: string, client_event_id: string, client_ts: number, reason: string | null) {
    return this.request<{ victim: Victim }>('POST', `/api/v1/victims/${id}/release`, { client_event_id, client_ts, reason });
  }
  complete(id: string, client_event_id: string, client_ts: number, people_count: number | null, note: string | null) {
    return this.request<{ victim: Victim }>('POST', `/api/v1/victims/${id}/complete`, {
      client_event_id, client_ts, people_count, note,
    });
  }
  locations(points: LocationPoint[]) {
    return this.request<{ accepted: number; duplicates: number }>('POST', '/api/v1/locations', { points });
  }
  receipt(victimId: string, body: { delivered_at?: number; opened_at?: number }) {
    return this.request<void>('POST', `/api/v1/alerts/${victimId}/receipt`, body);
  }
  sync(sinceSeq: number) {
    return this.request<{ events: any[]; last_seq: number }>('GET', `/api/v1/sync?since_seq=${sinceSeq}`);
  }
  mapManifest() {
    return this.request<{ file: string; size: number; sha256: string; style_version: string }>('GET', '/maps/manifest');
  }
  async styleText(): Promise<string> {
    const res = await this.fetchImpl(this.url('/maps/style.json'), {});
    if (!res.ok) throw new ApiError(res.status, 'no_style', 'Map style not available');
    return res.text();
  }

  wsUrl(sinceSeq: number): string {
    const base = this.baseUrl.replace(/^http/, 'ws');
    return `${base}/ws?token=${encodeURIComponent(this.token ?? '')}&since_seq=${sinceSeq}`;
  }
}

export const DEFAULT_PORT = 8000;

/** "192.168.1.2" / "192.168.1.2:8000" / "http://192.168.1.2:8000/" -> "http://192.168.1.2:8000". */
export function normalizeServerUrl(input: string): string {
  let url = input.trim().replace(/\/+$/, '');
  if (!url) return '';
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`;
  const hostPort = url.replace(/^https?:\/\//i, '').split('/')[0];
  if (!/:\d+$/.test(hostPort)) url = url.replace(hostPort, `${hostPort}:${DEFAULT_PORT}`);
  return url;
}

/** Parse the QR code shown by the base: {"server_url","pairing_token","ssid"}. */
export function parsePairingCode(text: string): { server_url: string; pairing_token: string; ssid?: string } {
  let data: any;
  try {
    data = JSON.parse(text.trim());
  } catch {
    const shown = text.length > 60 ? `${text.slice(0, 60)}...` : text;
    throw new Error(`This is not a SAR base pairing code (read: "${shown}").`);
  }
  if (typeof data?.server_url !== 'string' || !/^https?:\/\//.test(data.server_url)) {
    throw new Error('Pairing code has no server address.');
  }
  if (typeof data?.pairing_token !== 'string' || data.pairing_token.length < 8) {
    throw new Error('Pairing code has no token.');
  }
  return { server_url: data.server_url.replace(/\/+$/, ''), pairing_token: data.pairing_token, ssid: data.ssid };
}
