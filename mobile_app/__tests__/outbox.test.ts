import { ApiError, NetworkError } from '../src/core/api';
import { MemoryRepo } from '../src/data/memory';
import { describeRejection, enqueueAction, flushOutbox, type OutboxDeps } from '../src/engine/outbox';
import { ALPHA, victim } from './fixtures';

function setup(api: Partial<OutboxDeps['api']>) {
  const repo = new MemoryRepo();
  let id = 0;
  let clock = 1000;
  const deps: OutboxDeps = {
    repo,
    api: api as OutboxDeps['api'],
    myTeamId: ALPHA,
    now: () => (clock += 1),
    newId: () => `cei-${++id}`,
  };
  return { repo, deps };
}

describe('outbox', () => {
  it('accept is optimistic, then confirmed by the server response', async () => {
    const v = victim();
    const accept = jest.fn(async () => ({ victim: { ...v, status: 'ongoing' as const, assigned_team_id: ALPHA } }));
    const { repo, deps } = setup({ accept });
    await repo.upsertServerVictim(v);
    await enqueueAction(deps, 'accept', v.id);
    expect((await repo.getVictim(v.id))!.local_pending_action).toBe('accept');

    const report = await flushOutbox(deps);
    expect(report).toMatchObject({ sent: 1, blocked: false, rejected: [] });
    expect(accept).toHaveBeenCalledWith(v.id, 'cei-1', expect.any(Number));
    const cached = (await repo.getVictim(v.id))!;
    expect(cached.status).toBe('ongoing');
    expect(cached.local_pending_action).toBeNull();
    expect(await repo.countOutbox()).toBe(0);
  });

  it('offline: nothing is lost, order is kept, and the same client_event_id is resent', async () => {
    const v = victim();
    const calls: string[] = [];
    let online = false;
    const api = {
      accept: jest.fn(async (_id: string, cei: string) => {
        calls.push(`accept:${cei}`);
        if (!online) throw new NetworkError('no route to host');
        return { victim: { ...v, status: 'ongoing' as const, assigned_team_id: ALPHA } };
      }),
      complete: jest.fn(async (_id: string, cei: string) => {
        calls.push(`complete:${cei}`);
        return { victim: { ...v, status: 'completed' as const, assigned_team_id: ALPHA } };
      }),
    };
    const { repo, deps } = setup(api);
    await repo.upsertServerVictim(v);
    await enqueueAction(deps, 'accept', v.id);
    await enqueueAction(deps, 'complete', v.id, { people_count: 2, note: 'two adults' });

    const first = await flushOutbox(deps);
    expect(first.blocked).toBe(true);
    expect(calls).toEqual(['accept:cei-1']); // complete never overtakes accept
    expect((await repo.listOutbox()).map((o) => [o.kind, o.attempts])).toEqual([['accept', 1], ['complete', 0]]);

    online = true;
    const second = await flushOutbox(deps);
    expect(second.sent).toBe(2);
    expect(calls).toEqual(['accept:cei-1', 'accept:cei-1', 'complete:cei-2']);
    expect(api.complete).toHaveBeenCalledWith(v.id, 'cei-2', expect.any(Number), 2, 'two adults');
    expect((await repo.getVictim(v.id))!.status).toBe('completed');
  });

  it('409 already_claimed rolls back and says who has it', async () => {
    const v = victim();
    const accept = jest.fn(async () => {
      throw new ApiError(409, 'already_claimed', 'Another team already has this victim', { team: { id: 'b', name: 'Bravo' } });
    });
    const { repo, deps } = setup({ accept });
    await repo.upsertServerVictim(v);
    await enqueueAction(deps, 'accept', v.id);
    const report = await flushOutbox(deps);
    expect(report.rejected).toHaveLength(1);
    expect(describeRejection(report.rejected[0])).toBe(`${v.display_code}: Bravo already accepted it.`);
    expect(await repo.getVictim(v.id)).toBeNull();
    expect(await repo.countOutbox()).toBe(0);
  });

  it('a 5xx keeps the item queued; a 401 is reported as revoked', async () => {
    const v = victim();
    let status = 503;
    const accept = jest.fn(async () => {
      throw new ApiError(status, 'x', 'server error');
    });
    const { repo, deps } = setup({ accept });
    await repo.upsertServerVictim(v);
    await enqueueAction(deps, 'accept', v.id);
    expect((await flushOutbox(deps)).blocked).toBe(true);
    status = 401;
    const report = await flushOutbox(deps);
    expect(report.revoked).toBe(true);
    expect(await repo.countOutbox()).toBe(1);
  });

  it('receipts flow through the same queue and do not mark the victim pending', async () => {
    const v = victim();
    const receipt = jest.fn(async () => undefined);
    const { repo, deps } = setup({ receipt });
    await repo.upsertServerVictim(v);
    await enqueueAction(deps, 'receipt', v.id, { delivered_at: 5 });
    expect((await repo.getVictim(v.id))!.local_pending_action).toBeNull();
    await flushOutbox(deps);
    expect(receipt).toHaveBeenCalledWith(v.id, { delivered_at: 5 });
  });
});
