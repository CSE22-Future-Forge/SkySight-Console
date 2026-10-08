import type { Victim } from '../src/core/types';

export const ALPHA = 'team-alpha';
export const BRAVO = 'team-bravo';

let n = 0;
export function victim(over: Partial<Victim> = {}): Victim {
  n += 1;
  return {
    id: over.id ?? `v-${n}`,
    display_code: over.display_code ?? `V-${String(n).padStart(4, '0')}`,
    status: 'open',
    lat: 7.2906,
    lon: 80.6337,
    position_error_m: 8,
    max_confidence: 0.87,
    modalities: 'both',
    sighting_count: 1,
    first_detected_at: 1_000 + n,
    last_seen_at: 1_000 + n,
    assigned_team_id: null,
    accepted_at: null,
    completed_at: null,
    people_count: null,
    source: 'pipeline',
    thumb_url: `/api/v1/victims/v-${n}/image?size=thumb`,
    version: 1,
    updated_seq: null,
    ...over,
  };
}
