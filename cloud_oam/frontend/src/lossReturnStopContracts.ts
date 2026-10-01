/** Dedicated whole-return stop. Historical sources never authorize submission. */
import { canonical, id, identity, object, type Identity } from './formalLossReview';
import { command, fail, preview as inversePreview, reference, requestHash, reason,
  type Command, type Preview, type Reference, type Resolution } from './lossCorrectionContracts';

export type StopFact = { stop_id: string; reversal_id: string; posting_transaction_id: string;
  stopped_at: string; reason: string; quantity: string; serial_ids: string[]; evidence_fingerprint: string;
  stop_scope: 'whole_unshipped_return'; historical_stock_effect: 'return_pending_to_original_frozen' };
export type StopSource = Identity & { schema_version: '1.0'; result_scope: 'verified_loss_return_stop_references';
  stock_effect: 'none'; current_stock_verified: false; write_authorization_provided: false;
  queried_at: string; observed_ledger_cursor: number; root_disposition_id: string; report_operation_id: string;
  report_line_id: string; return_operation_id: string; return_line_id: string; quantity: string; serial_ids: string[];
  state: 'preview_required' | 'stopped' | 'downstream_compensation_required'; preview_reference: Reference | null; stop: StopFact | null };
export type StopPreview = Preview & { return_operation_id: string; return_line_id: string;
  stop_scope: 'whole_unshipped_return'; planned_stock_effect: 'return_pending_to_original_frozen' };
export type StopPending = Identity & { v: 1; flow: 'return-stop'; source: StopSource;
  preview: StopPreview; command: Command; request_hash: string };
const effect = 'return_pending_to_original_frozen', scope = 'whole_unshipped_return';
function digest(x: unknown): string { if (typeof x !== 'string' || !/^[a-f0-9]{64}$/.test(x)) fail(); return x; }
function date(x: unknown): string {
  if (typeof x !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(x) || !Number.isFinite(Date.parse(x))) fail(); return x;
}
function quantity(x: unknown): string {
  if (typeof x !== 'string' || !/^\d{1,15}(?:\.\d{1,3})?$/.test(x)) fail();
  const [n, fraction = ''] = x.split('.'), scaled = BigInt(n + fraction.padEnd(3, '0'));
  if (scaled <= 0n) fail(); return `${BigInt(n)}.${fraction.padEnd(3, '0')}`;
}
function serials(x: unknown, qty: string): string[] {
  if (!Array.isArray(x) || x.length > 10000) fail(); const values = x.map(id);
  if (new Set(values).size !== values.length || (values.length && qty !== `${values.length}.000`)) fail(); return values;
}
const sameSerials = (a: string[], b: string[]) => canonical([...a].sort()) === canonical([...b].sort());
function originalReference(x: unknown): Reference {
  const ref = reference(x, 'inverses');
  if (ref.reversed_correction_id !== null || ref.expected_execution_request_hash !== ref.expected_root_request_hash) fail(); return ref;
}
export function stopSource(x: unknown, expected: Identity, root: string): StopSource {
  const r = object(x, ['schema_version', 'result_scope', 'stock_effect', 'current_stock_verified', 'write_authorization_provided',
    'person_id', 'authorization_version', 'queried_at', 'observed_ledger_cursor', 'root_disposition_id', 'report_operation_id',
    'report_line_id', 'return_operation_id', 'return_line_id', 'quantity', 'serial_ids', 'state', 'preview_reference', 'stop']);
  if (r.schema_version !== '1.0' || r.result_scope !== 'verified_loss_return_stop_references' || r.stock_effect !== 'none' ||
      r.current_stock_verified !== false || r.write_authorization_provided !== false ||
      canonical(identity(r)) !== canonical(identity(expected)) || r.root_disposition_id !== id(root)) fail();
  for (const key of ['report_operation_id', 'report_line_id', 'return_operation_id', 'return_line_id']) id(r[key]);
  date(r.queried_at); if (!Number.isSafeInteger(r.observed_ledger_cursor) || Number(r.observed_ledger_cursor) < 1) fail();
  const qty = quantity(r.quantity), sn = serials(r.serial_ids, qty);
  let ref: Reference | null = null, stop: StopFact | null = null;
  if (r.state === 'preview_required') {
    ref = originalReference(r.preview_reference); if (ref.root_disposition_id !== root || r.stop !== null) fail();
  } else {
    if (r.preview_reference !== null) fail();
    if (r.state === 'stopped') {
      const row = object(r.stop, ['stop_id', 'reversal_id', 'posting_transaction_id', 'stopped_at', 'reason',
        'quantity', 'serial_ids', 'evidence_fingerprint', 'stop_scope', 'historical_stock_effect']);
      id(row.stop_id); id(row.reversal_id); id(row.posting_transaction_id); date(row.stopped_at); reason(row.reason); digest(row.evidence_fingerprint);
      const q = quantity(row.quantity), ids = serials(row.serial_ids, q);
      if (q !== qty || !sameSerials(ids, sn) || row.stop_scope !== scope || row.historical_stock_effect !== effect) fail();
      stop = { ...row, quantity: q, serial_ids: ids } as StopFact;
    } else if (r.state !== 'downstream_compensation_required' || r.stop !== null) fail();
  }
  return { ...r, ...identity(r), quantity: qty, serial_ids: sn, preview_reference: ref, stop } as StopSource;
}
export function stopPreview(x: unknown): StopPreview {
  if (!x || typeof x !== 'object' || Array.isArray(x)) fail();
  const { return_operation_id, return_line_id, stop_scope, planned_stock_effect, ...base } = x as Record<string, unknown>;
  id(return_operation_id); id(return_line_id);
  if (stop_scope !== scope || planned_stock_effect !== effect) fail();
  return { ...inversePreview(base, 'inverses')!, return_operation_id, return_line_id, stop_scope, planned_stock_effect } as StopPreview;
}
export function stopIntent(source: StopSource, why: string) {
  if (source.state !== 'preview_required' || !source.preview_reference || source.stop) fail('已有履约或停止记录，请重新核验');
  return { ...originalReference(source.preview_reference), reason: reason(why) };
}
export function checkStopTarget(p: StopPending, source: StopSource) {
  if (p.person_id !== source.person_id || source.authorization_version < p.authorization_version) fail('身份变化，请重新核验');
  for (const key of ['root_disposition_id', 'report_operation_id', 'report_line_id', 'return_operation_id', 'return_line_id', 'quantity'] as const) {
    if (p.source[key] !== source[key]) fail('原退回明细变化，请重新核验');
  }
  if (!sameSerials(p.source.serial_ids, source.serial_ids)) fail();
  const { request_id: _request, idempotency_key: _key, expected_plan_hash: _plan, ...body } = p.command;
  if (canonical(body) !== canonical(stopIntent(source, p.command.reason))) fail('原退回引用变化，请重新预览');
}
export function stopPending(x: unknown): StopPending {
  const r = object(x, ['v', 'flow', 'person_id', 'authorization_version', 'source', 'preview', 'command', 'request_hash']);
  if (r.v !== 1 || r.flow !== 'return-stop') fail();
  const who = identity(r), c = command(r.command, 'inverses'), source = stopSource(r.source, who, c.root_disposition_id), plan = stopPreview(r.preview);
  digest(r.request_hash);
  if (c.reversed_correction_id !== null || plan.root_disposition_id !== c.root_disposition_id || plan.original_execution_id !== c.root_disposition_id ||
      plan.plan_hash !== c.expected_plan_hash || plan.quantity !== source.quantity || !sameSerials(plan.serial_ids, source.serial_ids) ||
      plan.return_operation_id !== source.return_operation_id || plan.return_line_id !== source.return_line_id) fail();
  const p = { ...r, ...who, command: c, source, preview: plan } as StopPending; checkStopTarget(p, source); return p;
}
export async function verifyStopPending(x: unknown): Promise<StopPending> {
  const p = stopPending(x); if (await requestHash(p.command, 'inverses') !== p.request_hash) fail('原退回停止请求摘要不一致'); return p;
}
export async function prepareStop(source: StopSource, why: string, rawPreview: unknown): Promise<StopPending> {
  const plan = stopPreview(rawPreview), c = command({ ...stopIntent(source, why), expected_plan_hash: plan.plan_hash,
    request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() }, 'inverses');
  return stopPending({ v: 1, flow: 'return-stop', ...identity(source), source, preview: plan, command: c, request_hash: await requestHash(c, 'inverses') });
}
export function stopPosted(x: unknown, p: StopPending): Record<string, unknown> {
  const r = object(x, ['root_disposition_id', 'operation_id', 'line_id', 'original_headquarters_decision_id', 'requester_person_id',
    'actor_user_id', 'actor_person_id', 'authorization_version', 'reason', 'request_id', 'request_hash', 'posting_transaction_id',
    'posting_movement_id', 'source_account_id', 'target_account_id', 'quantity', 'plan_hash', 'status', 'reversal_id',
    'original_execution_id', 'reversed_correction_id', 'original_transaction_id', 'original_movement_id', 'stock_effect']);
  const c = p.command, plan = p.preview;
  if (r.root_disposition_id !== c.root_disposition_id || r.operation_id !== p.source.report_operation_id || r.line_id !== p.source.report_line_id ||
      r.actor_person_id !== p.person_id || r.reason !== c.reason || r.request_id !== c.request_id || r.request_hash !== p.request_hash ||
      !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version) < p.authorization_version) fail('响应与原停止请求不符');
  for (const key of ['original_headquarters_decision_id', 'requester_person_id', 'reversal_id', 'posting_transaction_id', 'posting_movement_id']) id(r[key]);
  if (typeof r.actor_user_id !== 'string' || !r.actor_user_id.length || r.actor_user_id.length > 200) fail();
  if (r.status !== 'posted' || r.plan_hash !== c.expected_plan_hash || quantity(r.quantity) !== plan.quantity ||
      r.source_account_id !== plan.source_account_id || r.target_account_id !== plan.target_account_id || r.reversed_correction_id !== null ||
      r.original_execution_id !== plan.original_execution_id || r.original_transaction_id !== plan.original_transaction_id ||
      r.original_movement_id !== plan.original_movement_id || r.stock_effect !== 'restores_original_frozen_share') fail();
  return r;
}
export function stopResolution(x: unknown, p: StopPending): Resolution {
  if (!x || typeof x !== 'object') fail(); const r = x as Record<string, unknown>;
  object(r, ['request_state', 'result_scope', 'retry_allowed', 'request_id', 'request_hash', 'result', ...(r.request_state === 'sealed' ? ['seal'] : [])]);
  if (r.retry_allowed !== false || r.request_id !== p.command.request_id || r.request_hash !== p.request_hash) fail();
  if (r.request_state === 'not_found') { if (r.result_scope !== 'unconfirmed_request' || r.result !== null) fail(); return { status: 'pending' }; }
  if (r.request_state === 'sealed') {
    if (r.result_scope !== 'closed_original_request' || r.result !== null) fail();
    const seal = object(r.seal, ['seal_id', 'root_disposition_id', 'sealed_at', 'stock_effect']); id(seal.seal_id); date(seal.sealed_at);
    if (seal.root_disposition_id !== p.command.root_disposition_id || seal.stock_effect !== 'none') fail(); return { status: 'sealed' };
  }
  if (r.request_state !== 'found' || r.result_scope !== 'historical_original_posting') fail(); stopPosted(r.result, p); return { status: 'found' };
}
