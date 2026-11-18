/** Derived views of the cached victims for the dashboard and lists. Pure, so Jest covers them. */

import { haversineM, type Fix } from '../core/geo';
import type { CachedVictim } from '../core/types';

export interface TeamStats {
  open: number;
  /** This team's victims it is on the way to. */
  onTheWay: number;
  /** This team's completed rescues. */
  rescued: number;
  /** People counted in this team's rescue reports. */
  peopleRescued: number;
}

const isMine = (v: CachedVictim, teamId: string | null) =>
  v.assigned_team_id === teamId || v.local_pending_action === 'accept';

export function teamStats(victims: CachedVictim[], teamId: string | null): TeamStats {
  const stats: TeamStats = { open: 0, onTheWay: 0, rescued: 0, peopleRescued: 0 };
  for (const v of victims) {
    if (v.status === 'open' && v.local_pending_action !== 'accept') stats.open += 1;
    else if (v.status === 'ongoing' && isMine(v, teamId)) stats.onTheWay += 1;
    else if (v.status === 'completed' && isMine(v, teamId)) {
      stats.rescued += 1;
      stats.peopleRescued += v.people_count ?? 0;
    }
  }
  // An accept still in the outbox counts as on the way, not as open.
  stats.onTheWay += victims.filter((v) => v.status === 'open' && v.local_pending_action === 'accept').length;
  return stats;
}

/** This team's jobs: ongoing first, then rescued; newest first within each. */
export function myJobs(victims: CachedVictim[], teamId: string | null): CachedVictim[] {
  const rank = (v: CachedVictim) => (v.status === 'completed' ? 1 : 0);
  return victims
    .filter((v) => isMine(v, teamId))
    .sort((a, b) => rank(a) - rank(b) || (b.accepted_at ?? b.first_detected_at) - (a.accepted_at ?? a.first_detected_at));
}

/** Open victims nobody has taken, nearest first when the phone has a fix (else newest first). */
export function openByDistance(victims: CachedVictim[], me: Fix | null): { v: CachedVictim; distance: number | null }[] {
  return victims
    .filter((v) => v.status === 'open' && v.local_pending_action !== 'accept')
    .map((v) => ({ v, distance: me ? haversineM(me.lat, me.lon, v.lat, v.lon) : null }))
    .sort((a, b) => (a.distance != null && b.distance != null ? a.distance - b.distance
                     : b.v.first_detected_at - a.v.first_detected_at));
}

export type ActivityKind = 'found' | 'accepted' | 'rescued';

export interface ActivityItem {
  key: string;
  kind: ActivityKind;
  at: number;
  victim: CachedVictim;
  text: string;
}

/** Recent mission events this phone can see, newest first. */
export function activityFeed(victims: CachedVictim[], teamId: string | null, limit = 8): ActivityItem[] {
  const items: ActivityItem[] = [];
  for (const v of victims) {
    items.push({ key: `${v.id}-found`, kind: 'found', at: v.first_detected_at, victim: v,
                 text: `${v.display_code} found by the drone` });
    if (v.accepted_at && v.assigned_team_id) {
      items.push({ key: `${v.id}-accepted`, kind: 'accepted', at: v.accepted_at, victim: v,
                   text: v.assigned_team_id === teamId ? `Your team accepted ${v.display_code}`
                     : `${v.display_code} accepted by another team` });
    }
    if (v.completed_at) {
      const people = v.people_count != null ? ` · ${v.people_count} ${v.people_count === 1 ? 'person' : 'people'}` : '';
      items.push({ key: `${v.id}-rescued`, kind: 'rescued', at: v.completed_at, victim: v,
                   text: `${v.display_code} rescued${people}` });
    }
  }
  return items.sort((a, b) => b.at - a.at).slice(0, limit);
}
