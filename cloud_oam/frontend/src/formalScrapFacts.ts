/** Historical outcomes only. Approval, stock posting and request closure are distinct. */
export type Kind = 'original' | 'correction' | 'apply' | 'regional' | 'headquarters' | 'execute';
type Coordinates = { request_id: string; request_hash: string };
export type ScrapFact = Coordinates & {
  scrap_operation_id: string; scrap_line_id: string; source_kind: 'original' | 'correction';
  root_disposition_id: string; correction_execution_id: string | null;
  posting_transaction_id: string; posting_movement_id: string; quantity: string;
  source_account_id: string; target_account_id: null; status: 'posted';
  stock_effect: 'removed_from_managed_assets'; plan_hash: string;
};
export type ReviewFact = Coordinates & {
  fact_id: string; recovery_request_id: string; scrap_line_id: string;
  stage: 'apply' | 'regional' | 'headquarters';
  status: 'awaiting_regional' | 'awaiting_headquarters' | 'needs_evidence' | 'approved_pending_execution';
  stock_effect: 'none'; actor_person_id: string; authorization_version: number; reason: string;
  decision: 'verified' | 'needs_evidence' | 'approve' | 'request_regional_review' | null;
  regional_review_id: string | null;
};
export type RecoveryFact = Coordinates & {
  root_disposition_id: string; operation_id: string; line_id: string;
  original_headquarters_decision_id: string; requester_person_id: string;
  actor_user_id: string; actor_person_id: string; authorization_version: number; reason: string;
  posting_transaction_id: string; posting_movement_id: string; source_account_id: null;
  target_account_id: string; quantity: string; plan_hash: string; status: 'posted';
  reversal_id: string; original_execution_id: string; reversed_correction_id: string | null;
  original_transaction_id: string; original_movement_id: string;
  stock_effect: 'restores_original_frozen_share';
};
export type Fact = ScrapFact | ReviewFact | RecoveryFact;
export type Seal = { seal_id: string; kind: Kind; loss_operation_id: string; loss_line_id: string;
  root_disposition_id: string | null; sealed_at: string; stock_effect: 'none' };
export type Resolution = { status: 'pending' } | { status: 'found'; result: Fact } | { status: 'sealed'; seal: Seal };

export function fail(): never { throw new Error('报废或找回结果无法核验，请保留完整原请求'); }
function record(value: unknown, names: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const r = value as Record<string, unknown>;
  if (Object.keys(r).length !== names.length || names.some(name => !Object.hasOwn(r, name))) fail();
  return r;
}
function uuid(value: unknown) {
  if (typeof value !== 'string' || !/^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)) fail();
}
function digest(value: unknown) { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(); }
function text(value: unknown, max: number) {
  if (typeof value !== 'string' || !value.trim() || [...value].length > max || /[\u0000\ud800-\udfff]/u.test(value)) fail();
}
function coordinates(r: Record<string, unknown>) {
  if (typeof r.request_id !== 'string' || !/^[A-Za-z0-9._:-]{8,160}$/.test(r.request_id)) fail();
  digest(r.request_hash);
}
function version(value: unknown) { if (!Number.isSafeInteger(value) || Number(value) < 1) fail(); }
function quantity(value: unknown) {
  if (typeof value !== 'string' || !/^(?:0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value) || !/[1-9]/.test(value)) fail();
}
function ids(r: Record<string, unknown>, names: readonly string[]) { names.forEach(name => uuid(r[name])); }

const COORDINATES = ['request_id', 'request_hash'];
const SCRAP_IDS = ['scrap_operation_id', 'scrap_line_id', 'root_disposition_id',
  'posting_transaction_id', 'posting_movement_id', 'source_account_id'];
const REVIEW_IDS = ['fact_id', 'recovery_request_id', 'scrap_line_id', 'actor_person_id'];
const RECOVERY_IDS = ['root_disposition_id', 'operation_id', 'line_id', 'original_headquarters_decision_id',
  'requester_person_id', 'actor_person_id', 'posting_transaction_id', 'posting_movement_id', 'target_account_id',
  'reversal_id', 'original_execution_id', 'original_transaction_id', 'original_movement_id'];

export function fact(kind: Kind, value: unknown): Fact {
  if (kind === 'original' || kind === 'correction') {
    const r = record(value, [...COORDINATES, ...SCRAP_IDS, 'source_kind', 'correction_execution_id',
      'quantity', 'target_account_id', 'status', 'stock_effect', 'plan_hash']);
    coordinates(r); ids(r, SCRAP_IDS); quantity(r.quantity); digest(r.plan_hash);
    if (r.source_kind !== kind || r.target_account_id !== null || r.status !== 'posted'
        || r.stock_effect !== 'removed_from_managed_assets') fail();
    if (kind === 'correction') uuid(r.correction_execution_id);
    else if (r.correction_execution_id !== null) fail();
    return { ...r } as ScrapFact;
  }
  if (kind === 'execute') {
    const r = record(value, [...COORDINATES, ...RECOVERY_IDS, 'actor_user_id', 'authorization_version', 'reason',
      'source_account_id', 'quantity', 'plan_hash', 'status', 'reversed_correction_id', 'stock_effect']);
    coordinates(r); ids(r, RECOVERY_IDS); text(r.actor_user_id, 36); text(r.reason, 500);
    version(r.authorization_version); quantity(r.quantity); digest(r.plan_hash);
    if (r.reversed_correction_id !== null) uuid(r.reversed_correction_id);
    if (r.original_execution_id !== (r.reversed_correction_id ?? r.root_disposition_id)
        || r.source_account_id !== null || r.status !== 'posted'
        || r.stock_effect !== 'restores_original_frozen_share') fail();
    return { ...r } as RecoveryFact;
  }
  if (kind !== 'apply' && kind !== 'regional' && kind !== 'headquarters') fail();
  const r = record(value, [...COORDINATES, ...REVIEW_IDS, 'stage', 'status', 'stock_effect',
    'authorization_version', 'reason', 'decision', 'regional_review_id']);
  coordinates(r); ids(r, REVIEW_IDS); version(r.authorization_version); text(r.reason, 500);
  if (r.stage !== kind || r.stock_effect !== 'none') fail();
  if (kind === 'apply') {
    if (r.decision !== null || r.status !== 'awaiting_regional' || r.fact_id !== r.recovery_request_id
        || r.regional_review_id !== null) fail();
  } else if (kind === 'regional') {
    if (r.regional_review_id !== null || !(r.decision === 'verified' && r.status === 'awaiting_headquarters'
        || r.decision === 'needs_evidence' && r.status === 'needs_evidence')) fail();
  } else {
    uuid(r.regional_review_id);
    if (!(r.decision === 'approve' && r.status === 'approved_pending_execution'
        || r.decision === 'request_regional_review' && r.status === 'awaiting_regional')) fail();
  }
  return { ...r } as ReviewFact;
}

export function resolution(value: unknown, kind: Kind, expected: Coordinates): Resolution {
  coordinates(expected);
  const state = value && typeof value === 'object' ? (value as Record<string, unknown>).request_state : undefined;
  const r = record(value, [...COORDINATES, 'request_state', 'retry_allowed', 'result_scope', 'result',
    ...(state === 'sealed' ? ['seal'] : [])]);
  coordinates(r);
  if (r.request_id !== expected.request_id || r.request_hash !== expected.request_hash || r.retry_allowed !== false) fail();
  if (state === 'not_found') {
    if (r.result !== null || r.result_scope !== 'unconfirmed_request') fail();
    return { status: 'pending' };
  }
  if (state === 'found') {
    if (r.result_scope !== 'historical_original_outcome') fail();
    const result = fact(kind, r.result);
    if (result.request_id !== expected.request_id || result.request_hash !== expected.request_hash) fail();
    return { status: 'found', result };
  }
  if (state !== 'sealed' || r.result !== null || r.result_scope !== 'closed_original_request') fail();
  const s = record(r.seal, ['seal_id', 'kind', 'loss_operation_id', 'loss_line_id',
    'root_disposition_id', 'sealed_at', 'stock_effect']);
  ids(s, ['seal_id', 'loss_operation_id', 'loss_line_id']);
  if (s.root_disposition_id !== null) uuid(s.root_disposition_id);
  else if (kind !== 'original') fail();
  if (s.kind !== kind || s.stock_effect !== 'none' || typeof s.sealed_at !== 'string'
      || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(s.sealed_at)
      || !Number.isFinite(Date.parse(s.sealed_at))) fail();
  return { status: 'sealed', seal: { ...s } as Seal };
}
