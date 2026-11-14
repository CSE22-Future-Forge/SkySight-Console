import { DEFAULT_SETTINGS } from '../src/core/types';
import { MemoryRepo } from '../src/data/memory';
import { applyEnvelope, mergeEffects, noEffects } from '../src/engine/events';
import { ALPHA, BRAVO, victim } from './fixtures';

async function repoWithSync() {
  const repo = new MemoryRepo();
  await repo.saveSyncState({
    server_url: 'http://base', device_id: 'd1', team_id: ALPHA, team_name: 'Alpha', team_call_sign: 'A1',
    team_color: '#1f77b4', mission_name: 'm', last_seq: 0, map_sha256: null, settings: DEFAULT_SETTINGS,
  });
  return repo;
}

describe('applyEnvelope', () => {
  it('a new open victim is cached, raises one alarm and advances last_seq', async () => {
    const repo = await repoWithSync();
    const v = victim();
    const fx = await applyEnvelope(repo, { seq: 7, type: 'victim.created', ts: 1, data: v }, ALPHA);
    expect(fx.alarms).toEqual([v.id]);
    expect((await repo.getVictim(v.id))!.updated_seq).toBe(7);
    expect((await repo.getSyncState())!.last_seq).toBe(7);

    // Already alerted on this phone: an update does not ring again.
    await repo.patchVictim(v.id, { alerted_at: 123 });
    const again = await applyEnvelope(repo, { seq: 8, type: 'victim.updated', ts: 2, data: { ...v, sighting_count: 3 } }, ALPHA);
    expect(again.alarms).toEqual([]);
    expect((await repo.getVictim(v.id))!.sighting_count).toBe(3);
  });

  it('victim.hidden removes it and cancels the notification', async () => {
    const repo = await repoWithSync();
    const v = victim();
    await applyEnvelope(repo, { seq: 1, type: 'victim.created', ts: 1, data: v }, ALPHA);
    const fx = await applyEnvelope(repo, { seq: 2, type: 'victim.hidden', ts: 2, data: { id: v.id, reason: 'claimed' } }, ALPHA);
    expect(fx.cancel).toEqual([v.id]);
    expect(await repo.getVictim(v.id)).toBeNull();
  });

  it('server confirmation clears the pending accept and keeps phone-only columns', async () => {
    const repo = await repoWithSync();
    const v = victim();
    await applyEnvelope(repo, { seq: 1, type: 'victim.created', ts: 1, data: v }, ALPHA);
    await repo.patchVictim(v.id, { local_pending_action: 'accept', thumb_local_path: 'file:///t.jpg', alerted_at: 5 });
    const fx = await applyEnvelope(repo, {
      seq: 2, type: 'victim.updated', ts: 2, data: { ...v, status: 'ongoing', assigned_team_id: ALPHA, version: 2 },
    }, ALPHA);
    const cached = (await repo.getVictim(v.id))!;
    expect(cached.local_pending_action).toBeNull();
    expect(cached.thumb_local_path).toBe('file:///t.jpg');
    expect(cached.alerted_at).toBe(5);
    expect(fx.cancel).toEqual([v.id]); // no siren for a victim we are on
  });

  it('another team taking it does not clear our pending accept (outbox rolls back on 409)', async () => {
    const repo = await repoWithSync();
    const v = victim();
    await applyEnvelope(repo, { seq: 1, type: 'victim.created', ts: 1, data: v }, ALPHA);
    await repo.patchVictim(v.id, { local_pending_action: 'accept' });
    await applyEnvelope(repo, {
      seq: 2, type: 'victim.updated', ts: 2, data: { ...v, status: 'ongoing', assigned_team_id: BRAVO },
    }, ALPHA);
    expect((await repo.getVictim(v.id))!.local_pending_action).toBe('accept');
  });

  it('a reopened victim we already knew gives an info, not a second siren', async () => {
    const repo = await repoWithSync();
    const v = victim({ status: 'ongoing', assigned_team_id: ALPHA });
    await applyEnvelope(repo, { seq: 1, type: 'victim.updated', ts: 1, data: v }, ALPHA);
    await repo.patchVictim(v.id, { alerted_at: 9 });
    const fx = await applyEnvelope(repo, { seq: 2, type: 'victim.updated', ts: 2, data: { ...v, status: 'open', assigned_team_id: null } }, ALPHA);
    expect(fx.alarms).toEqual([]);
    expect(fx.infos[0].title).toMatch(/open again/);
  });

  it('assignment to my team produces an info notification', async () => {
    const repo = await repoWithSync();
    const v = victim({ status: 'ongoing', assigned_team_id: ALPHA });
    await applyEnvelope(repo, { seq: 1, type: 'victim.updated', ts: 1, data: v }, ALPHA);
    const fx = await applyEnvelope(repo, { seq: 2, type: 'assignment.changed', ts: 1, data: { victim_id: v.id, team_id: ALPHA } }, ALPHA);
    expect(fx.infos[0].title).toContain(v.display_code);
  });

  it('a replay that creates then hides a victim leaves no alarm', () => {
    const merged = mergeEffects({ ...noEffects(), alarms: ['x'] }, { ...noEffects(), cancel: ['x'] });
    expect(merged.alarms).toEqual([]);
  });
});
