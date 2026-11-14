/**
 * Outbox (ARCHITECTURE.md 9.4): every action -> outbox row -> optimistic UI -> send.
 *
 * Items are sent strictly in order; a network failure stops the flush so later actions
 * never overtake earlier ones. Each carries a client_event_id, so a resend after a lost
 * response is answered by the server with the original outcome (idempotency).
 * A rejection (409/404) rolls the optimistic state back and is reported to the user.
 */

import { ApiError, NetworkError, type ApiClient } from '../core/api';
import type { OutboxItem, OutboxKind } from '../core/types';
import type { Repo } from '../data/repo';

export interface OutboxDeps {
  repo: Repo;
  api: Pick<ApiClient, 'accept' | 'release' | 'complete' | 'receipt'>;
  myTeamId: string;
  now: () => number;
  newId: () => string;
}

export interface Rejection {
  victimId: string;
  kind: OutboxKind;
  code: string;
  message: string;
  teamName?: string;
  displayCode?: string;
}

export interface FlushReport {
  sent: number;
  rejected: Rejection[];
  /** Stopped early: base unreachable. The rest stays queued in order. */
  blocked: boolean;
  revoked: boolean;
}

export async function enqueueAction(deps: OutboxDeps, kind: OutboxKind, victimId: string, payload: object = {}) {
  const now = deps.now();
  const item: OutboxItem = {
    client_event_id: deps.newId(),
    kind,
    victim_id: victimId,
    payload_json: JSON.stringify(payload),
    client_ts: now,
    attempts: 0,
    last_error: null,
    created_at: now,
  };
  await deps.repo.enqueue(item);
  if (kind !== 'receipt') await deps.repo.patchVictim(victimId, { local_pending_action: kind });
  return item;
}

async function send(deps: OutboxDeps, item: OutboxItem) {
  const p = JSON.parse(item.payload_json || '{}');
  switch (item.kind) {
    case 'accept':
      return deps.api.accept(item.victim_id, item.client_event_id, item.client_ts);
    case 'release':
      return deps.api.release(item.victim_id, item.client_event_id, item.client_ts, p.reason ?? null);
    case 'complete':
      return deps.api.complete(item.victim_id, item.client_event_id, item.client_ts, p.people_count ?? null, p.note ?? null);
    case 'receipt':
      return deps.api.receipt(item.victim_id, p);
  }
}

export async function flushOutbox(deps: OutboxDeps): Promise<FlushReport> {
  const report: FlushReport = { sent: 0, rejected: [], blocked: false, revoked: false };
  for (const item of await deps.repo.listOutbox()) {
    try {
      const res: any = await send(deps, item);
      if (res?.victim) {
        await deps.repo.upsertServerVictim(res.victim);
        await deps.repo.patchVictim(item.victim_id, { local_pending_action: null });
      }
      await deps.repo.removeOutbox(item.client_event_id);
      report.sent += 1;
    } catch (e) {
      const transient = e instanceof NetworkError || (e instanceof ApiError && (e.status >= 500 || e.status === 401));
      if (transient || !(e instanceof ApiError)) {
        await deps.repo.updateOutbox(item.client_event_id, {
          attempts: item.attempts + 1,
          last_error: (e as Error).message ?? String(e),
        });
        report.blocked = true;
        report.revoked = e instanceof ApiError && e.status === 401;
        break;
      }
      // The base said no: undo what the phone showed optimistically.
      await deps.repo.removeOutbox(item.client_event_id);
      if (item.kind === 'receipt') continue;
      const victim = await deps.repo.getVictim(item.victim_id);
      await deps.repo.patchVictim(item.victim_id, { local_pending_action: null });
      if (e.code === 'already_claimed' || e.code === 'not_found' || e.code === 'not_assigned') {
        // No longer ours to see; the server sends victim.hidden as well.
        await deps.repo.deleteVictim(item.victim_id);
      } else if (e.body?.victim) {
        await deps.repo.upsertServerVictim(e.body.victim);
      }
      report.rejected.push({
        victimId: item.victim_id,
        kind: item.kind,
        code: e.code,
        message: e.message,
        teamName: e.body?.team?.name,
        displayCode: victim?.display_code,
      });
    }
  }
  return report;
}

export function describeRejection(r: Rejection): string {
  const code = r.displayCode ?? 'Victim';
  if (r.code === 'already_claimed') return `${code}: ${r.teamName ?? 'another team'} already accepted it.`;
  if (r.code === 'not_assigned') return `${code} is no longer assigned to your team.`;
  if (r.code === 'not_found') return `${code} was withdrawn by the base.`;
  return `${code}: ${r.message}`;
}
