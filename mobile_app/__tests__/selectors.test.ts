import type { CachedVictim } from '../src/core/types';
import { MemoryRepo } from '../src/data/memory';
import { activityFeed, myJobs, openByDistance, teamStats } from '../src/state/selectors';
import { unseenAlerts } from '../src/state/store';
import { ALPHA, BRAVO, victim } from './fixtures';

const cached = (over: Partial<CachedVictim> = {}): CachedVictim => ({
  thumb_local_path: null, local_pending_action: null, alerted_at: null, opened_at: null, ...victim(over), ...over,
});

describe('dashboard selectors', () => {
  const list = [
    cached({ id: 'open-near', lat: 7.2906, lon: 80.6340 }),
    cached({ id: 'open-far', lat: 7.30, lon: 80.64 }),
    cached({ id: 'mine', status: 'ongoing', assigned_team_id: ALPHA, accepted_at: 5_000 }),
    cached({ id: 'pending', local_pending_action: 'accept' }),
    cached({ id: 'done', status: 'completed', assigned_team_id: ALPHA, accepted_at: 4_000, completed_at: 6_000,
             people_count: 3 }),
    cached({ id: 'bravo', status: 'completed', assigned_team_id: BRAVO, completed_at: 7_000 }),
  ];

  it('counts open, on the way (incl. an accept still queued), rescued and people', () => {
    expect(teamStats(list, ALPHA)).toEqual({ open: 2, onTheWay: 2, rescued: 1, peopleRescued: 3 });
  });

  it('lists my jobs active first', () => {
    expect(myJobs(list, ALPHA).map((v) => v.id)).toEqual(['mine', 'pending', 'done']);
  });

  it('sorts open victims by distance from me', () => {
    const me = { lat: 7.2906, lon: 80.6337 };
    const open = openByDistance(list, me);
    expect(open.map((o) => o.v.id)).toEqual(['open-near', 'open-far']);
    expect(open[0].distance).toBeLessThan(50);
  });

  it('builds a newest-first activity feed', () => {
    const feed = activityFeed(list, ALPHA, 3);
    expect(feed.map((f) => f.key)).toEqual(['bravo-rescued', 'done-rescued', 'mine-accepted']);
    expect(feed[1].text).toBe(`${list[4].display_code} rescued · 3 people`);
  });

  it('queues unseen alerts oldest first and drops opened or answered ones', () => {
    const alerts = [
      cached({ id: 'second', alerted_at: 20 }),
      cached({ id: 'first', alerted_at: 10 }),
      cached({ id: 'opened', alerted_at: 5, opened_at: 6 }),
      cached({ id: 'accepting', alerted_at: 5, local_pending_action: 'accept' }),
      cached({ id: 'taken', status: 'ongoing', alerted_at: 1 }),
    ];
    expect(unseenAlerts(alerts).map((v) => v.id)).toEqual(['first', 'second']);
  });
});

describe('repo rules', () => {
  it('an older server version never overwrites a newer one (outbox vs WebSocket race)', async () => {
    const repo = new MemoryRepo();
    const v = victim({ version: 1 });
    await repo.upsertServerVictim({ ...v, status: 'ongoing', assigned_team_id: ALPHA, version: 2 });
    const kept = await repo.upsertServerVictim({ ...v, status: 'open', version: 1 });
    expect(kept.status).toBe('ongoing');
  });

  it('remembers an alarm after the victim was hidden', async () => {
    const repo = new MemoryRepo();
    const v = victim();
    await repo.upsertServerVictim(v);
    await repo.patchVictim(v.id, { alerted_at: 1 });
    await repo.deleteVictim(v.id);
    expect(await repo.wasAlerted(v.id)).toBe(true);
    expect(await repo.wasAlerted('never')).toBe(false);
  });
});
