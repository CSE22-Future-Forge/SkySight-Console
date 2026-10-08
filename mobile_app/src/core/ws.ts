/**
 * WebSocket link to the base (ARCHITECTURE.md 7.4).
 *
 * - Connects with `since_seq` = last applied seq, so the server replays what was missed.
 * - Envelopes are handled strictly one at a time, in order; seq <= last applied is skipped.
 * - Acks the highest applied seq (debounced) and answers server pings with pong.
 * - Reconnects with backoff; treats silence longer than ws_dead_after_s as a dead link.
 * - Close code 4401/4403 = this phone was revoked or unknown: stop and report.
 */

import type { ConnectionStatus, Envelope } from './types';

export interface SocketLike {
  readyState: number;
  onopen: ((ev: any) => void) | null;
  onmessage: ((ev: { data: any }) => void) | null;
  onclose: ((ev: { code: number; reason?: string }) => void) | null;
  onerror: ((ev: any) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type SocketFactory = (url: string) => SocketLike;

export interface WsOptions {
  url: (sinceSeq: number) => string;
  lastSeq: () => number;
  onEnvelope: (env: Envelope) => Promise<void>;
  onStatus: (status: ConnectionStatus) => void;
  onOpen?: () => void;
  deadAfterMs?: () => number;
  socketFactory?: SocketFactory;
  ackDelayMs?: number;
  backoffMs?: number[];
  now?: () => number;
}

const OPEN = 1;
const RECONNECTING_MS = 20_000;
export const REVOKED_CODES = new Set([4401, 4403]);

export class BaseLink {
  private socket: SocketLike | null = null;
  private stopped = true;
  private attempt = 0;
  private queue: Promise<void> = Promise.resolve();
  private ackTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private lastRx = 0;
  private pendingAck = 0;
  /** When the link was lost; null while live. Drives Reconnecting (amber) vs Offline (red). */
  private lostAt: number | null = null;
  status: ConnectionStatus = 'offline';

  constructor(private o: WsOptions) {}

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.lostAt = this.now();
    this.setStatus('connecting');
    this.connect();
    this.watchdog = setInterval(() => this.checkAlive(), 5000);
  }

  stop() {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.watchdog) clearInterval(this.watchdog);
    this.flushAck();
    this.socket?.close(1000, 'stopped');
    this.socket = null;
    this.setStatus('offline');
  }

  /** Force an immediate reconnect attempt (e.g. Wi-Fi came back). */
  kick() {
    if (this.stopped || this.status === 'revoked') return;
    if (this.socket && this.socket.readyState === OPEN) return;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.attempt = 0;
    this.connect();
  }

  send(obj: unknown): boolean {
    if (this.socket && this.socket.readyState === OPEN) {
      this.socket.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  /** Resolves once every envelope received so far has been applied. */
  idle(): Promise<void> {
    return this.queue;
  }

  private now() {
    return this.o.now ? this.o.now() : Date.now();
  }

  private setStatus(s: ConnectionStatus) {
    if (this.status !== s) {
      this.status = s;
      this.o.onStatus(s);
    }
  }

  private connect() {
    if (this.stopped) return;
    const factory: SocketFactory = this.o.socketFactory ?? ((url) => new (globalThis as any).WebSocket(url));
    let sock: SocketLike;
    try {
      sock = factory(this.o.url(this.o.lastSeq()));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = sock;
    this.lastRx = this.now();
    sock.onopen = () => {
      if (this.socket !== sock) return;
      this.attempt = 0;
      this.lastRx = this.now();
      this.lostAt = null;
      this.setStatus('live');
      this.o.onOpen?.();
    };
    sock.onmessage = (ev) => {
      if (this.socket !== sock) return;
      this.lastRx = this.now();
      let msg: any;
      try {
        msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
      } catch {
        return;
      }
      if (msg?.type === 'ping') {
        this.send({ type: 'pong' });
        return;
      }
      if (msg?.type === 'error') return; // followed by a close with the reason code
      if (typeof msg?.seq !== 'number') return;
      this.queue = this.queue.then(() => this.handle(msg as Envelope)).catch(() => undefined);
    };
    sock.onclose = (ev) => {
      if (this.socket !== sock) return;
      this.socket = null;
      if (REVOKED_CODES.has(ev?.code)) {
        this.stopped = true;
        if (this.watchdog) clearInterval(this.watchdog);
        this.setStatus('revoked');
        return;
      }
      this.markLost();
      this.scheduleReconnect();
    };
    sock.onerror = () => {
      /* onclose follows */
    };
  }

  private async handle(env: Envelope) {
    if (env.seq <= this.o.lastSeq()) return; // replay overlap
    await this.o.onEnvelope(env);
    this.pendingAck = Math.max(this.pendingAck, env.seq);
    if (!this.ackTimer) {
      this.ackTimer = setTimeout(() => this.flushAck(), this.o.ackDelayMs ?? 500);
    }
  }

  private flushAck() {
    if (this.ackTimer) clearTimeout(this.ackTimer);
    this.ackTimer = null;
    if (this.pendingAck > 0 && this.send({ type: 'ack', seq: this.pendingAck })) this.pendingAck = 0;
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    const steps = this.o.backoffMs ?? [1000, 2000, 4000, 8000, 15000];
    const delay = steps[Math.min(this.attempt, steps.length - 1)];
    this.attempt += 1;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private checkAlive() {
    const dead = this.o.deadAfterMs ? this.o.deadAfterMs() : 45_000;
    if (this.socket && this.socket.readyState === OPEN && this.now() - this.lastRx > dead) {
      const s = this.socket;
      this.socket = null;
      s.close(4000, 'no ping from base');
      this.markLost();
      this.scheduleReconnect();
    }
    if (this.lostAt != null) this.markLost();
  }

  private markLost() {
    if (this.lostAt == null) this.lostAt = this.now();
    this.setStatus(this.now() - this.lostAt < RECONNECTING_MS ? 'connecting' : 'offline');
  }
}
