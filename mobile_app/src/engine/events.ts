/**
 * Apply one server envelope to the local cache (ARCHITECTURE.md 7.4, 9.4).
 *
 * Pure apart from the Repo: returns the side effects (alarms to raise, notifications to
 * cancel, info messages) so the engine decides how to show them. The server already
 * filtered what this team may see (7.5); the phone just follows.
 */

import type { CachedVictim, Envelope, Victim } from '../core/types';
import type { Repo } from '../data/repo';

export interface Effects {
  /** Victim ids that should raise the siren (open and never alerted on this phone). */
  alarms: string[];
  /** Victim ids whose notification must go away. */
  cancel: string[];
  infos: { id: string; title: string; body: string; victimId: string | null }[];
  changed: boolean;
}

export const noEffects = (): Effects => ({ alarms: [], cancel: [], infos: [], changed: false });

export function mergeEffects(a: Effects, b: Effects): Effects {
  return {
    alarms: [...a.alarms.filter((id) => !b.cancel.includes(id)), ...b.alarms],
    cancel: [...a.cancel, ...b.cancel],
    infos: [...a.infos, ...b.infos],
    changed: a.changed || b.changed,
  };
}

/** Does the server state confirm the action the phone queued? */
export function confirmsPending(v: Victim, pending: CachedVictim['local_pending_action'], myTeamId: string): boolean {
  if (pending === 'accept') return v.status === 'ongoing' && v.assigned_team_id === myTeamId;
  if (pending === 'complete') return v.status === 'completed';
  if (pending === 'release') return v.status === 'open';
  return false;
}

export async function applyEnvelope(repo: Repo, env: Envelope, myTeamId: string): Promise<Effects> {
  const fx = noEffects();
  switch (env.type) {
    case 'victim.created':
    case 'victim.updated': {
      const v = env.data as Victim;
      const before = await repo.getVictim(v.id);
      const saved = await repo.upsertServerVictim({ ...v, updated_seq: v.updated_seq ?? env.seq });
      if (saved.local_pending_action && confirmsPending(v, saved.local_pending_action, myTeamId)) {
        await repo.patchVictim(v.id, { local_pending_action: null });
      }
      if (v.status === 'open' && !saved.alerted_at) {
        fx.alarms.push(v.id);
      } else if (v.status === 'open' && before && before.status !== 'open') {
        fx.infos.push({ id: `reopen-${v.id}`, title: `${v.display_code} is open again`,
                        body: 'Released by the team that had it - no team is on the way.', victimId: v.id });
      }
      if (v.status !== 'open') fx.cancel.push(v.id);
      fx.changed = true;
      break;
    }
    case 'victim.hidden': {
      const id = env.data?.id as string;
      if (id) {
        await repo.deleteVictim(id);
        fx.cancel.push(id);
        fx.changed = true;
      }
      break;
    }
    case 'assignment.changed': {
      if (env.data?.team_id === myTeamId) {
        const v = await repo.getVictim(env.data.victim_id);
        fx.infos.push({
          id: `assign-${env.data.victim_id}`,
          title: `Base assigned ${v?.display_code ?? 'a victim'} to your team`,
          body: 'Open the app for directions.',
          victimId: env.data.victim_id,
        });
        fx.changed = true;
      }
      break;
    }
    default:
      break; // unknown types are ignored, but still advance last_seq
  }
  await repo.saveSyncState({ last_seq: env.seq });
  return fx;
}
