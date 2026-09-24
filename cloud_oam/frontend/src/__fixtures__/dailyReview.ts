import { dailyDetail, dailyId, otherDailyId } from './dailyReconciliation';
import { prepareDailyCommand, type DailyReference } from '../dailyReviewProtocol';
import { createDailyReviewRecoveryStore, type DailyReviewLockManager, type DailyReviewSentinel } from '../dailyReviewRecoveryStore';
import type { AccessContext } from '../types';

export function dailyAccess(role = 'provincial_manager'): AccessContext {
  return { person_id: otherDailyId, authorization_version: 1, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: [role],
    assignments: [{ assignment_id: dailyId, role_code: role, scope_type: role === 'admin' ? 'national' : 'organization', scope_id: role === 'admin' ? '*' : dailyId,
      valid_from: '2026-01-01T00:00:00Z', valid_to: '2099-01-01T00:00:00Z' }],
    permissions: ['read', 'create_daily', 'explain_daily', 'approve_daily'].map(action => ({ resource: 'reconciliation', action, field_code: '' })) };
}
export function dailyPrepared() {
  return prepareDailyCommand({ ...dailyDetail(), review_status: 'not_recorded', review_version: 0, review_updated_at: null, allowed_actions: ['open'] }, dailyAccess(), { operation: 'open' });
}
export function dailyReceipt(reference: DailyReference) {
  return { recorded: true, event_id: dailyId, event_sha256: 'e'.repeat(64), version: reference.original_review_version + 1,
    review_status: reference.operation === 'approve' ? 'approved' : reference.operation === 'request_changes' ? 'changes_requested' : 'awaiting_explanations',
    comparison_status: 'differences', stock_written: false };
}
export function dailyRecovery(reference: DailyReference, outcome: 'not_observed' | 'found' | 'sealed' = 'not_observed', version = 1) {
  return { schema_version: 'rsc.daily_review_recovery.v1', reference, current_authorization_version: version, outcome,
    receipt: outcome === 'found' ? dailyReceipt(reference) : null,
    seal: outcome === 'sealed' ? { seal_id: dailyId, sealed_at: '2026-09-21T08:00:00Z', reference, permanent_nonexecution: true } : null,
    automatic_retry_allowed: false };
}
export class DailyMemoryStorage {
  values = new Map<string, string>();
  get length() { return this.values.size; }
  key(i: number) { return [...this.values.keys()][i] ?? null; }
  getItem(k: string) { return this.values.get(k) ?? null; }
  setItem(k: string, v: string) { this.values.set(k, v); }
  removeItem(k: string) { this.values.delete(k); }
}
export function dailyLocks(): DailyReviewLockManager {
  const held = new Set<string>();
  return { async request(name, _options, callback) {
    if (held.has(name)) return callback(null);
    held.add(name); try { return await callback({}); } finally { held.delete(name); }
  } };
}
export function dailyStore(storage = new DailyMemoryStorage(), locks = dailyLocks()) { return createDailyReviewRecoveryStore({ storage, locks }); }
export function dailyMarker(reference = dailyPrepared().reference): DailyReviewSentinel { return { v: 1, kind: 'daily_review', ...reference }; }
