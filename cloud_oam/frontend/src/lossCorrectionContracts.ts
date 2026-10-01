/** Exact historical references and original commands; no query authorizes a retry. */
import { canonical, hash, id, identity, object, type Disposition, type Identity } from './formalLossReview';

export type Flow = 'inverses' | 'approvals' | 'executions';
export const ACTIONS = { inverses: 'reverse_loss', approvals: 'approve_loss_correction', executions: 'correct_loss' } as const;
export const DISPOSITIONS: Disposition[] = ['restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap'];
export type RootRef = { root_disposition_id: string; expected_root_request_hash: string; expected_submission_plan_hash: string };
export type Reference = RootRef & { reversed_correction_id?: string | null; expected_execution_request_hash?: string;
  reversal_id?: string; expected_reversal_hash?: string; correction_decision_id?: string; expected_correction_decision_hash?: string };
export type Intent = Reference & { reason: string; disposition?: Disposition };
export type Command = Intent & { request_id: string; idempotency_key: string; expected_plan_hash?: string };
export type Choice = { correction_decision_id: string; disposition: Disposition; reason: string;
  execution_mode: 'preview_required' | 'dedicated_flow_required'; preview_reference: Reference | null };
export type Fact = { kind: 'original_execution' | 'inverse' | 'approval' | 'correction_execution'; fact_id: string;
  request_hash: string; created_at: string; posting_transaction_id: string | null; disposition: Disposition | null; quantity: string | null };
export type Sources = Identity & { schema_version: '1.0'; result_scope: 'verified_loss_history_references'; write_authorization_provided: false;
  stock_effect: 'none'; queried_at: string; observed_ledger_cursor: number; root_disposition_id: string; operation_id: string; line_id: string;
  quantity: string; serial_ids: string[]; frozen_share_in_verified_history: string;
  chain_state: 'active_execution' | 'awaiting_approval' | 'awaiting_execution' | 'dedicated_compensation_required';
  inverse_preview_reference: Reference | null; approval_reference: Reference | null; approval_choices: Choice[]; history: Fact[] };
export type Preview = { planning_status: 'preview_only'; stock_effect: 'none'; root_disposition_id: string; source_account_id: string;
  target_account_id: string; source_condition: string; target_condition: string; quantity: string; serial_ids: string[]; plan_hash: string; checked_at: string;
  original_execution_id?: string; original_transaction_id?: string; original_movement_id?: string; reversal_id?: string;
  correction_decision_id?: string; disposition?: Disposition; target_requires_creation?: boolean };
export type Pending = Identity & { v: 1; flow: Flow; source: Sources; preview: Preview | null; command: Command; request_hash: string };
export type Resolution = { status: 'pending' | 'found' | 'sealed' };

export function fail(message = '纠正记录无法核验，请保留原请求'): never { throw new Error(message); }
const ROOT = ['root_disposition_id', 'expected_root_request_hash', 'expected_submission_plan_hash'];
const REF = { inverses: [...ROOT, 'reversed_correction_id', 'expected_execution_request_hash'],
  approvals: [...ROOT, 'reversal_id', 'expected_reversal_hash'],
  executions: [...ROOT, 'reversal_id', 'expected_reversal_hash', 'correction_decision_id', 'expected_correction_decision_hash'] };
export function flow(x: unknown): Flow { if (x !== 'inverses' && x !== 'approvals' && x !== 'executions') fail(); return x; }
function text(x: unknown, max = 1000): string {
  if (typeof x !== 'string' || !x.length || [...x].length > max || [...x].some(c => c.charCodeAt(0) === 0 || (c.length === 1 && /[\ud800-\udfff]/u.test(c)))) fail();
  return x;
}
export function reason(x: unknown): string {
  const s = text(x, 500);
  if (s !== s.trim() || /^[\u001c-\u001f\u0085]|[\u001c-\u001f\u0085]$/u.test(s) || /[\x00-\x08\x0b-\x1f]/.test(s)) fail('请填写明确理由，且不带首尾空白');
  return s;
}
function digest(x: unknown): string { if (typeof x !== 'string' || !/^[a-f0-9]{64}$/.test(x)) fail(); return x; }
function date(x: unknown): string { const s = text(x, 64); if (!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) fail(); return s; }
function quantity(x: unknown, zero = false): string {
  if (typeof x !== 'string' || !/^\d{1,15}(?:\.\d{1,3})?$/.test(x)) fail();
  const [n, f = ''] = x.split('.'); if (!zero && BigInt(n + f.padEnd(3, '0')) === 0n) fail(); return `${BigInt(n)}.${f.padEnd(3, '0')}`;
}
function list<T>(x: unknown, parse: (x: unknown) => T, max = 1001): T[] { if (!Array.isArray(x) || x.length > max) fail(); return x.map(parse); }
function unique(xs: string[]) { if (new Set(xs).size !== xs.length) fail(); }
function disposition(x: unknown): Disposition { if (!DISPOSITIONS.includes(x as Disposition)) fail(); return x as Disposition; }
function supported(x: Disposition) { return !['return_to_region', 'scrap'].includes(x); }
function serials(x: unknown, qty: string): string[] { const xs = list(x, id, 10000); unique(xs); if (xs.length && qty !== `${xs.length}.000`) fail(); return xs; }

export function reference(x: unknown, f: Flow): Reference {
  const r = object(x, REF[f]); id(r.root_disposition_id); digest(r.expected_root_request_hash); digest(r.expected_submission_plan_hash);
  if (f === 'inverses') { if (r.reversed_correction_id !== null) id(r.reversed_correction_id); digest(r.expected_execution_request_hash); }
  else { id(r.reversal_id); digest(r.expected_reversal_hash); }
  if (f === 'executions') { id(r.correction_decision_id); digest(r.expected_correction_decision_hash); }
  return { ...r } as Reference;
}
export function intent(x: unknown, f: Flow): Intent {
  const r = object(x, [...REF[f], 'reason', ...(f === 'approvals' ? ['disposition'] : [])]);
  const { reason: why, disposition: kind, ...ref } = r;
  return { ...reference(ref, f), reason: reason(why), ...(f === 'approvals' ? { disposition: disposition(kind) } : {}) };
}
export function command(x: unknown, f: Flow): Command {
  const r = object(x, [...REF[f], 'reason', 'request_id', 'idempotency_key', ...(f === 'approvals' ? ['disposition'] : ['expected_plan_hash'])]);
  const { request_id, idempotency_key, expected_plan_hash, ...body } = r;
  if (typeof request_id !== 'string' || !/^[A-Za-z0-9._:-]{8,160}$/.test(request_id) || typeof idempotency_key !== 'string' || !/^[A-Za-z0-9._:-]{8,200}$/.test(idempotency_key)) fail();
  return { ...intent(body, f), request_id, idempotency_key, ...(f === 'approvals' ? {} : { expected_plan_hash: digest(expected_plan_hash) }) };
}
export function sources(x: unknown, expected: Identity, root: string): Sources {
  const r = object(x, ['schema_version', 'result_scope', 'write_authorization_provided', 'stock_effect', 'person_id', 'authorization_version', 'queried_at',
    'observed_ledger_cursor', 'root_disposition_id', 'operation_id', 'line_id', 'quantity', 'serial_ids', 'frozen_share_in_verified_history', 'chain_state',
    'inverse_preview_reference', 'approval_reference', 'approval_choices', 'history']);
  if (r.schema_version !== '1.0' || r.result_scope !== 'verified_loss_history_references' || r.write_authorization_provided !== false || r.stock_effect !== 'none' || canonical(identity(r)) !== canonical(identity(expected)) || r.root_disposition_id !== id(root)) fail();
  date(r.queried_at); id(r.operation_id); id(r.line_id); if (!Number.isSafeInteger(r.observed_ledger_cursor) || Number(r.observed_ledger_cursor) < 1) fail();
  const qty = quantity(r.quantity), sn = serials(r.serial_ids, qty), frozen = quantity(r.frozen_share_in_verified_history, true);
  const history = list(r.history, v => {
    const h = object(v, ['kind', 'fact_id', 'request_hash', 'created_at', 'posting_transaction_id', 'disposition', 'quantity']);
    if (!['original_execution', 'inverse', 'approval', 'correction_execution'].includes(String(h.kind))) fail();
    id(h.fact_id); digest(h.request_hash); date(h.created_at);
    if (h.kind === 'approval') { if (h.posting_transaction_id !== null || h.quantity !== null) fail(); }
    else { id(h.posting_transaction_id); if (quantity(h.quantity) !== qty) fail(); }
    if (h.kind === 'inverse') { if (h.disposition !== null) fail(); } else disposition(h.disposition);
    return { ...h, quantity: h.quantity === null ? null : qty } as Fact;
  }); unique(history.map(h => h.fact_id));
  const originals = history.filter(h => h.kind === 'original_execution'); if (originals.length !== 1 || originals[0].fact_id !== root) fail();
  function proven(ref: Reference, f: Flow) {
    if (ref.root_disposition_id !== root || ref.expected_root_request_hash !== originals[0].request_hash) fail();
    if (f === 'inverses') {
      const fact = history.find(h => h.fact_id === (ref.reversed_correction_id ?? root));
      if (!fact || !['original_execution', 'correction_execution'].includes(fact.kind) || fact.request_hash !== ref.expected_execution_request_hash) fail();
    } else {
      if (!history.some(h => h.kind === 'inverse' && h.fact_id === ref.reversal_id && h.request_hash === ref.expected_reversal_hash)) fail();
      if (f === 'executions' && !history.some(h => h.kind === 'approval' && h.fact_id === ref.correction_decision_id && h.request_hash === ref.expected_correction_decision_hash)) fail();
    }
  }
  const inverse = r.inverse_preview_reference === null ? null : reference(r.inverse_preview_reference, 'inverses');
  const approval = r.approval_reference === null ? null : reference(r.approval_reference, 'approvals');
  if (inverse) proven(inverse, 'inverses'); if (approval) proven(approval, 'approvals');
  const choices = list(r.approval_choices, v => {
    const c = object(v, ['correction_decision_id', 'disposition', 'reason', 'execution_mode', 'preview_reference']);
    id(c.correction_decision_id); const kind = disposition(c.disposition); reason(c.reason);
    if (!history.some(h => h.kind === 'approval' && h.fact_id === c.correction_decision_id && h.disposition === kind)) fail();
    let preview: Reference | null = null;
    if (supported(kind)) {
      if (c.execution_mode !== 'preview_required') fail(); preview = reference(c.preview_reference, 'executions'); proven(preview, 'executions');
      const { correction_decision_id, expected_correction_decision_hash: _hash, ...binding } = preview;
      if (correction_decision_id !== c.correction_decision_id || !approval || canonical(binding) !== canonical(approval)) fail();
    } else if (c.execution_mode !== 'dedicated_flow_required' || c.preview_reference !== null) fail();
    return { ...c, preview_reference: preview } as Choice;
  }); unique(choices.map(c => c.correction_decision_id));
  if (r.chain_state === 'active_execution') { if (!inverse || approval || choices.length || frozen !== '0.000') fail(); }
  else if (r.chain_state === 'awaiting_approval' || r.chain_state === 'awaiting_execution') {
    if (inverse || !approval || frozen !== qty || (r.chain_state === 'awaiting_execution') !== !!choices.length) fail();
  } else if (r.chain_state !== 'dedicated_compensation_required' || inverse || approval || choices.length || frozen !== '0.000') fail();
  return { ...r, ...identity(r), quantity: qty, serial_ids: sn, frozen_share_in_verified_history: frozen,
    inverse_preview_reference: inverse, approval_reference: approval, approval_choices: choices, history } as Sources;
}
export function preview(x: unknown, f: Flow): Preview | null {
  if (f === 'approvals') { if (x !== null) fail(); return null; }
  const r = object(x, ['planning_status', 'stock_effect', 'root_disposition_id', 'source_account_id', 'target_account_id', 'source_condition', 'target_condition',
    'quantity', 'serial_ids', 'plan_hash', 'checked_at', ...(f === 'inverses' ? ['original_execution_id', 'original_transaction_id', 'original_movement_id'] : ['reversal_id', 'correction_decision_id', 'disposition', 'target_requires_creation'])]);
  if (r.planning_status !== 'preview_only' || r.stock_effect !== 'none') fail();
  for (const key of ['root_disposition_id', 'source_account_id', 'target_account_id', ...(f === 'inverses' ? ['original_execution_id', 'original_transaction_id', 'original_movement_id'] : ['reversal_id', 'correction_decision_id'])]) id(r[key]);
  if (r.source_account_id === r.target_account_id || !['new', 'used', 'damaged'].includes(String(r.source_condition)) || !['new', 'used', 'damaged'].includes(String(r.target_condition))) fail();
  digest(r.plan_hash); date(r.checked_at);
  if (f === 'executions' && (!supported(disposition(r.disposition)) || typeof r.target_requires_creation !== 'boolean' || r.target_condition !== (r.disposition === 'restore_available' ? r.source_condition : r.disposition === 'convert_used' ? 'used' : 'damaged'))) fail();
  const qty = quantity(r.quantity); return { ...r, quantity: qty, serial_ids: serials(r.serial_ids, qty) } as Preview;
}
export function intentFor(s: Sources, f: Flow, why: string, choice?: string): Intent {
  const ref = f === 'inverses' ? s.inverse_preview_reference : f === 'approvals' ? s.approval_reference : s.approval_choices.find(c => c.correction_decision_id === choice)?.preview_reference;
  if (!ref) fail('当前历史状态不支持此操作，请刷新');
  return intent({ ...ref, reason: reason(why), ...(f === 'approvals' ? { disposition: disposition(choice) } : {}) }, f);
}
export function checkTarget(p: Pending, s: Sources) {
  if (p.person_id !== s.person_id || s.authorization_version < p.authorization_version || p.source.root_disposition_id !== s.root_disposition_id || p.source.operation_id !== s.operation_id || p.source.line_id !== s.line_id || p.source.quantity !== s.quantity || canonical([...p.source.serial_ids].sort()) !== canonical([...s.serial_ids].sort())) fail('原处置或身份已变化，请重新核验');
  const { request_id: _r, idempotency_key: _k, expected_plan_hash: _p, ...body } = p.command;
  const choice = p.flow === 'approvals' ? p.command.disposition : p.command.correction_decision_id;
  if (canonical(body) !== canonical(intentFor(s, p.flow, p.command.reason, choice))) fail('原操作引用已变化，请重新预览');
}
export function pending(x: unknown): Pending {
  const r = object(x, ['v', 'person_id', 'authorization_version', 'flow', 'source', 'preview', 'command', 'request_hash']);
  if (r.v !== 1) fail(); const f = flow(r.flow), c = command(r.command, f), who = identity(r);
  const s = sources(r.source, who, c.root_disposition_id), plan = preview(r.preview, f); digest(r.request_hash);
  if (plan) {
    if (plan.root_disposition_id !== c.root_disposition_id || plan.plan_hash !== c.expected_plan_hash || plan.quantity !== s.quantity || canonical([...plan.serial_ids].sort()) !== canonical([...s.serial_ids].sort())) fail();
    if (f === 'inverses') { if (plan.original_execution_id !== (c.reversed_correction_id ?? c.root_disposition_id)) fail(); }
    else if (plan.reversal_id !== c.reversal_id || plan.correction_decision_id !== c.correction_decision_id || plan.disposition !== s.approval_choices.find(d => d.correction_decision_id === c.correction_decision_id)?.disposition) fail();
  }
  const result = { ...r, ...who, flow: f, source: s, preview: plan, command: c } as Pending; checkTarget(result, s); return result;
}
export async function requestHash(c: Command, f: Flow): Promise<string> {
  const { request_id, idempotency_key: _key, expected_plan_hash, ...body } = command(c, f);
  return hash({ schema_version: 1, action: ACTIONS[f], intent: body, request_id, ...(f === 'approvals' ? {} : { expected_plan_hash }) });
}
export async function verifyPending(x: unknown): Promise<Pending> { const p = pending(x); if (await requestHash(p.command, p.flow) !== p.request_hash) fail('原纠正请求摘要不一致'); return p; }
export async function prepare(s: Sources, f: Flow, body: Intent, rawPreview: unknown): Promise<Pending> {
  const plan = preview(rawPreview, f), c = command({ ...body, ...(plan ? { expected_plan_hash: plan.plan_hash } : {}), request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() }, f);
  return pending({ v: 1, ...identity(s), source: s, flow: f, preview: plan, command: c, request_hash: await requestHash(c, f) });
}

export function posted(x: unknown, p: Pending): Record<string, unknown> {
  const common = ['root_disposition_id', 'operation_id', 'line_id', 'original_headquarters_decision_id', 'requester_person_id', 'actor_user_id', 'actor_person_id', 'authorization_version', 'reason', 'request_id', 'request_hash'];
  const posting = ['posting_transaction_id', 'posting_movement_id', 'source_account_id', 'target_account_id', 'quantity', 'plan_hash', 'status'];
  const extra = p.flow === 'approvals' ? ['correction_decision_id', 'reversal_id', 'expected_reversal_hash', 'disposition', 'approval_stage', 'stock_effect'] : p.flow === 'inverses' ? [...posting, 'reversal_id', 'original_execution_id', 'reversed_correction_id', 'original_transaction_id', 'original_movement_id', 'stock_effect'] : [...posting, 'correction_execution_id', 'correction_decision_id', 'reversal_id', 'disposition', 'return_operation_id', 'return_fulfillment_required', 'stock_effect'];
  const r = object(x, [...common, ...extra]), c = p.command;
  if (r.root_disposition_id !== c.root_disposition_id || r.operation_id !== p.source.operation_id || r.line_id !== p.source.line_id || r.actor_person_id !== p.person_id || r.reason !== c.reason || r.request_id !== c.request_id || r.request_hash !== p.request_hash || !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version) < p.authorization_version) fail('响应与原纠正请求不符，保留原请求');
  id(r.original_headquarters_decision_id); id(r.requester_person_id); text(r.actor_user_id, 200); id(r.reversal_id);
  if (p.flow === 'approvals') {
    id(r.correction_decision_id); if (r.reversal_id !== c.reversal_id || r.expected_reversal_hash !== c.expected_reversal_hash || r.disposition !== c.disposition || r.approval_stage !== 'approved' || r.stock_effect !== 'none') fail();
  } else {
    const plan = p.preview!; id(r.posting_transaction_id); id(r.posting_movement_id);
    if (r.status !== 'posted' || r.plan_hash !== c.expected_plan_hash || quantity(r.quantity) !== plan.quantity || r.source_account_id !== plan.source_account_id || r.target_account_id !== plan.target_account_id) fail();
    if (p.flow === 'inverses') {
      if (r.reversed_correction_id !== c.reversed_correction_id || r.original_execution_id !== plan.original_execution_id || r.original_transaction_id !== plan.original_transaction_id || r.original_movement_id !== plan.original_movement_id || r.stock_effect !== 'restores_original_frozen_share') fail();
    } else {
      id(r.correction_execution_id); if (r.reversal_id !== c.reversal_id || r.correction_decision_id !== c.correction_decision_id || r.disposition !== plan.disposition || r.return_operation_id !== null || r.return_fulfillment_required !== false || r.stock_effect !== (plan.disposition === 'restore_available' ? 'frozen_to_available' : plan.disposition === 'convert_used' ? 'frozen_to_used' : 'frozen_to_damaged')) fail();
    }
  }
  return r;
}
export function resolution(x: unknown, p: Pending): Resolution {
  if (!x || typeof x !== 'object') fail(); const r = x as Record<string, unknown>;
  const keys = ['request_state', 'result_scope', 'retry_allowed', 'request_id', 'request_hash', 'result'];
  object(r, [...keys, ...(r.request_state === 'sealed' ? ['seal'] : [])]);
  if (r.retry_allowed !== false || r.request_id !== p.command.request_id || r.request_hash !== p.request_hash) fail();
  if (r.request_state === 'not_found') { if (r.result_scope !== 'unconfirmed_request' || r.result !== null) fail(); return { status: 'pending' }; }
  if (r.request_state === 'sealed') {
    if (r.result_scope !== 'closed_original_request' || r.result !== null) fail();
    const seal = object(r.seal, ['seal_id', 'root_disposition_id', 'sealed_at', 'stock_effect']); id(seal.seal_id); date(seal.sealed_at);
    if (seal.root_disposition_id !== p.command.root_disposition_id || seal.stock_effect !== 'none') fail(); return { status: 'sealed' };
  }
  const scope = p.flow === 'approvals' ? 'historical_approval' : p.flow === 'executions' ? 'historical_correction_posting' : p.command.reversed_correction_id === null ? 'historical_original_posting' : 'historical_correction_inverse_posting';
  if (r.request_state !== 'found' || r.result_scope !== scope) fail(); posted(r.result, p); return { status: 'found' };
}
