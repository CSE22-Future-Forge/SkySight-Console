import { BaseLink, type SocketLike } from '../src/core/ws';
import type { ConnectionStatus, Envelope } from '../src/core/types';

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: any[] = [];
  onopen: ((ev: any) => void) | null = null;
  onmessage: ((ev: { data: any }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: any) => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  push(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close(code = 1000) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

function makeLink(overrides: Partial<ConstructorParameters<typeof BaseLink>[0]> = {}) {
  let lastSeq = 0;
  const applied: number[] = [];
  const statuses: ConnectionStatus[] = [];
  const link = new BaseLink({
    url: (since) => `ws://base/ws?since_seq=${since}`,
    lastSeq: () => lastSeq,
    onEnvelope: async (env: Envelope) => {
      applied.push(env.seq);
      lastSeq = env.seq;
    },
    onStatus: (s) => statuses.push(s),
    socketFactory: (url) => new FakeSocket(url),
    ackDelayMs: 0,
    backoffMs: [10],
    ...overrides,
  });
  return { link, applied, statuses, setSeq: (n: number) => (lastSeq = n) };
}

const flush = () => new Promise((r) => setTimeout(r, 5));

beforeEach(() => {
  FakeSocket.all = [];
});

describe('BaseLink', () => {
  it('applies envelopes in order, skips replay overlap, acks the highest seq', async () => {
    const { link, applied } = makeLink();
    link.start();
    const sock = FakeSocket.all[0];
    sock.open();
    for (const seq of [3, 4, 4, 2, 5]) sock.push({ seq, type: 'victim.updated', ts: 1, data: {} });
    await link.idle();
    await flush();
    expect(applied).toEqual([3, 4, 5]);
    expect(sock.sent).toContainEqual({ type: 'ack', seq: 5 });
    link.stop();
  });

  it('answers ping with pong', () => {
    const { link } = makeLink();
    link.start();
    const sock = FakeSocket.all[0];
    sock.open();
    sock.push({ type: 'ping' });
    expect(sock.sent).toEqual([{ type: 'pong' }]);
    link.stop();
  });

  it('reconnects with since_seq = last applied seq (missed-alert replay)', async () => {
    const { link } = makeLink();
    link.start();
    const first = FakeSocket.all[0];
    expect(first.url).toContain('since_seq=0');
    first.open();
    first.push({ seq: 41, type: 'victim.created', ts: 1, data: {} });
    await link.idle();
    first.close(1006); // Wi-Fi dropped
    await new Promise((r) => setTimeout(r, 30));
    const second = FakeSocket.all[1];
    expect(second.url).toContain('since_seq=41');
    link.stop();
  });

  it('goes live on open, reconnecting when the link drops', async () => {
    const { link, statuses } = makeLink();
    link.start();
    FakeSocket.all[0].open();
    FakeSocket.all[0].close(1006);
    expect(statuses).toEqual(['connecting', 'live', 'connecting']);
    link.stop();
  });

  it('stops for good when the base revokes the phone', async () => {
    const { link, statuses } = makeLink();
    link.start();
    FakeSocket.all[0].open();
    FakeSocket.all[0].close(4403);
    await new Promise((r) => setTimeout(r, 30));
    expect(statuses[statuses.length - 1]).toBe('revoked');
    expect(FakeSocket.all).toHaveLength(1);
  });
});
