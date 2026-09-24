import { apiNoReplay, createIdempotencyKey, jsonBody } from './api';
import { hasFormalPermission } from './clientPolicy';
import { operationLabels, reviewLabels, type DailyDetail } from './formalDailyReconciliation';
import type { AccessContext } from './types';

export function requireDaily(ok: unknown, message = '日终请求证据不完整，已保留原请求等待核验'): asserts ok {
  if (!ok) throw new Error(message);
}
function object(value: unknown): Record<string, unknown> {
  requireDaily(value && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown>;
}
function exact(value: unknown, keys: string[]) {
  const v = object(value); requireDaily(Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k))); return v;
}
export function dailyUuid(value: unknown): string {
  requireDaily(typeof value === 'string' && /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)); return value;
}
function integer(value: unknown, minimum = 0): number {
  requireDaily(typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum); return value;
}
function pattern(value: unknown, regex: RegExp): string { requireDaily(typeof value === 'string' && regex.test(value)); return value; }
function digest(value: unknown) { return pattern(value, /^[0-9a-f]{64}$/); }
function time(value: unknown) {
  const v = pattern(value, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/);
  requireDaily(Number.isFinite(Date.parse(v))); return v;
}
function text(value: unknown, minimum = 4): string {
  requireDaily(typeof value === 'string' && value === value.trim() && value.length >= minimum && value.length <= 2000 && !/[\u0000-\u001f\u007f]/.test(value)); return value;
}
export type DailyOperation = keyof typeof operationLabels;
function operation(value: unknown): DailyOperation {
  requireDaily(typeof value === 'string' && Object.hasOwn(operationLabels, value)); return value as DailyOperation;
}
export function parseDailyReference(value: unknown) {
  const v = exact(value, ['cutoff_id', 'actor_person_id', 'original_authorization_version', 'original_review_version', 'operation', 'trace_request_id']);
  return Object.freeze({ cutoff_id: dailyUuid(v.cutoff_id), actor_person_id: dailyUuid(v.actor_person_id),
    original_authorization_version: integer(v.original_authorization_version, 1), original_review_version: integer(v.original_review_version),
    operation: operation(v.operation), trace_request_id: pattern(v.trace_request_id, /^[A-Za-z0-9._:-]{8,160}$/) });
}
export type DailyReference = ReturnType<typeof parseDailyReference>;
export function sameDailyReference(left: DailyReference, right: DailyReference) {
  return JSON.stringify(parseDailyReference(left)) === JSON.stringify(parseDailyReference(right));
}
export function parseDailyReceipt(value: unknown, reference: DailyReference) {
  const v = exact(value, ['recorded', 'event_id', 'event_sha256', 'version', 'review_status', 'comparison_status', 'stock_written']);
  requireDaily(v.recorded === true && v.stock_written === false && integer(v.version, 1) === reference.original_review_version + 1);
  requireDaily(typeof v.review_status === 'string' && Object.hasOwn(reviewLabels, v.review_status) && v.review_status !== 'not_recorded');
  requireDaily(v.comparison_status === 'matched' || v.comparison_status === 'differences');
  if (reference.operation === 'approve') requireDaily(v.review_status === 'approved');
  if (reference.operation === 'request_changes') requireDaily(v.review_status === 'changes_requested');
  return { recorded: true as const, event_id: dailyUuid(v.event_id), event_sha256: digest(v.event_sha256),
    version: v.version as number, review_status: v.review_status as keyof typeof reviewLabels,
    comparison_status: v.comparison_status, stock_written: false as const };
}
export type DailyReceipt = ReturnType<typeof parseDailyReceipt>;
export function parseDailyRecovery(value: unknown, reference: DailyReference, authorizationVersion: number) {
  const v = exact(value, ['schema_version', 'reference', 'current_authorization_version', 'outcome', 'receipt', 'seal', 'automatic_retry_allowed']);
  requireDaily(v.schema_version === 'rsc.daily_review_recovery.v1' && v.automatic_retry_allowed === false);
  requireDaily(sameDailyReference(parseDailyReference(v.reference), reference) && integer(v.current_authorization_version, 1) === authorizationVersion
    && authorizationVersion >= reference.original_authorization_version);
  if (v.outcome === 'found') { requireDaily(v.seal === null); return { outcome: 'found' as const, receipt: parseDailyReceipt(v.receipt, reference) }; }
  if (v.outcome === 'sealed') {
    requireDaily(v.receipt === null);
    const s = exact(v.seal, ['seal_id', 'sealed_at', 'reference', 'permanent_nonexecution']);
    requireDaily(s.permanent_nonexecution === true && sameDailyReference(parseDailyReference(s.reference), reference));
    return { outcome: 'sealed' as const, seal_id: dailyUuid(s.seal_id), sealed_at: time(s.sealed_at) };
  }
  requireDaily(v.outcome === 'not_observed' && v.receipt === null && v.seal === null);
  return { outcome: 'not_observed' as const };
}
export type DailyRecovery = ReturnType<typeof parseDailyRecovery>;
export type DailyExplanation = { ordinal: number; expected_item_version: number; explanation: string; evidence_file_id: string; evidence_sha256: string };
export type DailyDraft = { operation: DailyOperation; items?: DailyExplanation[]; comment?: string; ordinals?: number[] };
export function prepareDailyCommand(detail: DailyDetail, access: AccessContext, draft: DailyDraft) {
  const op = operation(draft.operation);
  requireDaily(detail.action_context?.person_id === access.person_id
    && detail.action_context?.authorization_version === access.authorization_version
    && Array.isArray(detail.allowed_actions) && detail.allowed_actions.includes(op)
    && access.account_status === 'active' && access.employment_status === 'active' && access.access_mode === 'active',
    '当前身份或对账可操作状态已变化，请刷新核验后再操作');
  requireDaily(op === 'open' ? detail.review_version === 0 : detail.review_version > 0);
  requireDaily(op !== 'approve' || detail.review_status === 'pending_review');
  const key = createIdempotencyKey('daily-review'), trace = createIdempotencyKey('daily-trace');
  const common = { cutoff_id: dailyUuid(detail.cutoff_id), expected_cutoff_sha256: digest(detail.cutoff_sha256),
    expected_comparison_sha256: digest(detail.comparison_sha256), expected_version: integer(detail.review_version),
    idempotency_key: key, request_id: trace, operation: op };
  requireDaily(detail.review_version < Number.MAX_SAFE_INTEGER && detail.review_status !== 'approved');
  let command: typeof common & { items?: DailyExplanation[]; comment?: string; ordinals?: number[] } = common;
  if (op === 'explain') {
    requireDaily(draft.items && draft.items.length > 0 && draft.items.length <= 100 && new Set(draft.items.map(i => i.ordinal)).size === draft.items.length);
    command = { ...common, items: draft.items.map(i => ({ ordinal: integer(i.ordinal, 1), expected_item_version: integer(i.expected_item_version),
      explanation: text(i.explanation), evidence_file_id: dailyUuid(i.evidence_file_id), evidence_sha256: digest(i.evidence_sha256) })) };
  } else if (op !== 'open') {
    const ordinals = (draft.ordinals || []).map(v => integer(v, 1));
    requireDaily(op === 'approve' ? !ordinals.length : ordinals.length > 0 && ordinals.length <= 100 && new Set(ordinals).size === ordinals.length);
    command = { ...common, comment: text(draft.comment), ordinals };
  }
  const reference = parseDailyReference({ cutoff_id: common.cutoff_id, actor_person_id: access.person_id,
    original_authorization_version: access.authorization_version, original_review_version: common.expected_version, operation: op, trace_request_id: trace });
  command.items?.forEach(Object.freeze); if (command.items) Object.freeze(command.items);
  if (command.ordinals) Object.freeze(command.ordinals);
  return Object.freeze({ command: Object.freeze(command), reference });
}
export type PreparedDailyCommand = ReturnType<typeof prepareDailyCommand>;
export async function currentDailyAccess(expected: AccessContext): Promise<AccessContext> {
  const value = object(await apiNoReplay('/access/context'));
  requireDaily(dailyUuid(value.person_id) === expected.person_id && integer(value.authorization_version, 1) === expected.authorization_version
    && value.account_status === 'active' && value.employment_status === 'active' && value.access_mode === 'active'
    && Array.isArray(value.permissions) && Array.isArray(value.assignments) && Array.isArray(value.role_codes), '身份或授权已变化，请重新进入页面核验原请求');
  const access = value as unknown as AccessContext;
  requireDaily(hasFormalPermission(access, 'reconciliation', 'read'), '当前身份无日终对账权限'); return access;
}
export async function requestDailyRecovery(reference: DailyReference, access: AccessContext, seal: boolean) {
  const ref = parseDailyReference(reference);
  const value = await apiNoReplay(`/v1/reconciliations/daily/${ref.cutoff_id}/${seal ? 'request-seal' : 'request-recovery'}`,
    { method: 'POST', headers: { 'X-Request-ID': createIdempotencyKey('daily-recovery') },
      ...jsonBody({ expected_authorization_version: access.authorization_version, reference: ref,
        ...(seal ? { confirmation: 'permanently_prevent_original_daily_review_request' } : {}) }) });
  return parseDailyRecovery(value, ref, access.authorization_version);
}
export async function requestDailyCommand(prepared: PreparedDailyCommand, access: AccessContext) {
  const c = prepared.command, r = parseDailyReference(prepared.reference);
  requireDaily(c.cutoff_id === r.cutoff_id && c.request_id === r.trace_request_id && c.operation === r.operation
    && c.expected_version === r.original_review_version && access.person_id === r.actor_person_id
    && access.authorization_version === r.original_authorization_version);
  const v = exact(await apiNoReplay(`/v1/reconciliations/daily/${prepared.reference.cutoff_id}/commands`,
    { method: 'POST', headers: { 'X-Request-ID': prepared.command.request_id, 'Idempotency-Key': prepared.command.idempotency_key },
      ...jsonBody({ expected_authorization_version: access.authorization_version, command: prepared.command }) }), ['receipt']);
  return parseDailyReceipt(v.receipt, prepared.reference);
}

/** The signed URL exists only inside this call and never enters application storage. */
export async function downloadDailyEvidence(fileId: string, stillCurrent: () => boolean) {
  const id = dailyUuid(fileId);
  const v = exact(await apiNoReplay(`/v1/files/${id}/download-intent`, { headers: { 'X-Request-ID': createIdempotencyKey('daily-file') } }),
    ['schema_version', 'file_id', 'purpose', 'status', 'download']);
  requireDaily(v.schema_version === '1.0' && v.file_id === id && v.purpose === 'daily_reconciliation_evidence' && v.status === 'available');
  const d = exact(v.download, ['method', 'url', 'expires_at']);
  requireDaily(d.method === 'GET' && typeof d.url === 'string' && d.url.length <= 8192 && !/[\u0000-\u0020\\]/.test(d.url));
  const url = new URL(d.url); requireDaily(url.protocol === 'https:' && !url.username && !url.password && !url.hash
    && Date.parse(time(d.expires_at)) > Date.now() && stillCurrent());
  const a = document.createElement('a'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; a.click();
}
