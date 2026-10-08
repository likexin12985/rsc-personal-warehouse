import { id, object } from './formalLossReview';

export const conditionStatusLabels = {
  awaiting_regional: '待区域复核', awaiting_headquarters: '待总部复核', needs_evidence: '待补充证据',
  rejected_pending_release: '已拒绝，待释放冻结', cancelled_pending_release: '已取消，待释放冻结',
  approved: '已批准，待执行', released_rejected: '拒绝后已释放冻结',
  released_cancelled: '取消后已释放冻结', executed: '已完成成色纠正',
} as const;
export const conditionActionLabels = { submit: '提交并冻结', supplement: '补充证据', withdraw: '撤回申请',
  verify_region: '区域复核通过', return_evidence: '退回补充证据', reject_region: '区域拒绝',
  return_region: '退回区域复核', reject_hq: '总部拒绝', approve_hq: '总部批准',
  cancel_approved: '取消批准', execute: '执行成色纠正', release: '释放冻结' } as const;
export type Status = keyof typeof conditionStatusLabels;
export type Action = keyof typeof conditionActionLabels;
export type Fact = { schema_version: 'condition_result/1'; case_id: string; event_id: string; inbound_line_id: string;
  action: Action; status: Status; quantity: string; actor_user_id: string; actor_person_id: string;
  authorization_version: number; request_id: string; request_hash: string; plan_hash: string;
  posting_transaction_id: string | null; posting_movement_id: string | null;
  stock_effect: 'freeze' | 'none' | 'status_change' | 'unfreeze'; reason: string };
export type ConditionHistory = { schema_version: 'condition_history/1'; result_scope: 'verified_condition_history';
  inbound_line_id: string; root_disposition_id: string; material_id: string; source_account_id: string;
  sku_code: string; material_name: string; base_unit: string; serials: { serial_id: string; serial_no: string; qr_code: string }[];
  recorded_condition: 'new' | 'used'; required_condition: 'damaged'; historical_damaged_quantity: string;
  held_quantity: string; corrected_quantity: string; unclaimed_quantity: string;
  cases: { case_id: string; status: Status; quantity: string; serial_ids: string[];
    latest_event_id: string; latest_event_hash: string }[];
  events: { sequence: number; occurred_at: string; previous_event_id: string | null; evidence_file_ids: string[]; fact: Fact }[];
  observed_ledger_cursor: number; history_fingerprint: string; current_stock_verified: false;
  write_authorization_provided: false; retry_allowed: false; stock_effect: 'none' };
const transitions = new Set([
  'submit/draft/awaiting_regional', 'verify_region/awaiting_regional/awaiting_headquarters',
  'return_evidence/awaiting_regional/needs_evidence', 'supplement/needs_evidence/awaiting_regional',
  'return_region/awaiting_headquarters/awaiting_regional', 'reject_region/awaiting_regional/rejected_pending_release',
  'reject_hq/awaiting_headquarters/rejected_pending_release', 'approve_hq/awaiting_headquarters/approved',
  'withdraw/awaiting_regional/cancelled_pending_release', 'withdraw/awaiting_headquarters/cancelled_pending_release',
  'withdraw/needs_evidence/cancelled_pending_release', 'cancel_approved/approved/cancelled_pending_release',
  'release/rejected_pending_release/released_rejected', 'release/cancelled_pending_release/released_cancelled',
  'execute/approved/executed',
]);
export function conditionTransitionAllowed(action: Action, status: Status): boolean {
  return [...transitions].some(edge => edge.startsWith(`${action}/${status}/`));
}
function fail(): never { throw new Error('成色纠正历史不完整或不匹配，请重新查询'); }
function text(v: unknown, max = 500): string { if (typeof v !== 'string' || !v.length || v.length > max) fail(); return v; }
function hash(v: unknown): string { const s = text(v, 64); if (!/^[a-f0-9]{64}$/.test(s)) fail(); return s; }
function integer(v: unknown): number { if (!Number.isSafeInteger(v) || Number(v) < 1) fail(); return v as number; }
function quantity(v: unknown, positive = false): string {
  const s = text(v, 20); if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(s)) fail();
  if (positive && units(s) === 0n) fail(); return s;
}
function units(s: string): bigint { const [a, b = ''] = s.split('.'); return BigInt(a) * 1000n + BigInt(b.padEnd(3, '0')); }
function list<T>(v: unknown, parse: (v: unknown) => T): T[] { if (!Array.isArray(v) || v.length > 20000) fail(); return v.map(parse); }
function ids(v: unknown): string[] { const result = list(v, id); if (new Set(result).size !== result.length) fail(); return result; }
function nullable(v: unknown) { return v === null ? null : id(v); }
function status(v: unknown): Status { const s = text(v); if (!Object.hasOwn(conditionStatusLabels, s)) fail(); return s as Status; }
export function conditionFact(value: unknown): Fact {
  const r = object(value, ['schema_version', 'case_id', 'event_id', 'inbound_line_id', 'action', 'status', 'quantity',
    'actor_user_id', 'actor_person_id', 'authorization_version', 'request_id', 'request_hash', 'plan_hash',
    'posting_transaction_id', 'posting_movement_id', 'stock_effect', 'reason']);
  const action = text(r.action); if (!Object.hasOwn(conditionActionLabels, action) || r.schema_version !== 'condition_result/1') fail();
  if (![...transitions].some(edge => edge.startsWith(action + '/') && edge.endsWith('/' + status(r.status)))) fail();
  const effect = ({ submit: 'freeze', execute: 'status_change', release: 'unfreeze' } as Record<string, Fact['stock_effect']>)[action] ?? 'none';
  const transaction = nullable(r.posting_transaction_id), movement = nullable(r.posting_movement_id);
  if (r.stock_effect !== effect || (transaction !== null) !== (effect !== 'none') || (movement !== null) !== (effect !== 'none')) fail();
  return { schema_version: 'condition_result/1', case_id: id(r.case_id), event_id: id(r.event_id), inbound_line_id: id(r.inbound_line_id),
    action: action as Action, status: status(r.status), quantity: quantity(r.quantity, true), actor_user_id: text(r.actor_user_id),
    actor_person_id: id(r.actor_person_id), authorization_version: integer(r.authorization_version), request_id: text(r.request_id, 160),
    request_hash: hash(r.request_hash), plan_hash: hash(r.plan_hash), posting_transaction_id: transaction, posting_movement_id: movement,
    stock_effect: effect, reason: text(r.reason) };
}
export function conditionHistory(value: unknown, expected: { inbound: string; root: string; material: string }): ConditionHistory {
  const r = object(value, ['schema_version', 'result_scope', 'inbound_line_id', 'root_disposition_id', 'material_id', 'source_account_id',
    'sku_code', 'material_name', 'base_unit', 'serials',
    'recorded_condition', 'required_condition', 'historical_damaged_quantity', 'held_quantity', 'corrected_quantity', 'unclaimed_quantity',
    'cases', 'events', 'observed_ledger_cursor', 'history_fingerprint', 'current_stock_verified', 'write_authorization_provided', 'retry_allowed', 'stock_effect']);
  if (r.schema_version !== 'condition_history/1' || r.result_scope !== 'verified_condition_history'
      || r.current_stock_verified !== false || r.write_authorization_provided !== false || r.retry_allowed !== false
      || r.stock_effect !== 'none' || r.required_condition !== 'damaged' || !['new', 'used'].includes(String(r.recorded_condition))
      || id(r.inbound_line_id) !== id(expected.inbound) || id(r.root_disposition_id) !== id(expected.root) || id(r.material_id) !== id(expected.material)) fail();
  const cases = list(r.cases, v => { const c = object(v, ['case_id', 'status', 'quantity', 'serial_ids', 'latest_event_id', 'latest_event_hash']);
    const serials = ids(c.serial_ids), amount = quantity(c.quantity, true);
    if (serials.length && BigInt(serials.length) * 1000n !== units(amount)) fail();
    return { case_id: id(c.case_id), status: status(c.status), quantity: amount, serial_ids: serials,
      latest_event_id: id(c.latest_event_id), latest_event_hash: hash(c.latest_event_hash) }; });
  const byCase = new Map(cases.map(c => [c.case_id, c])); if (byCase.size !== cases.length) fail();
  const serials = list(r.serials, v => { const s = object(v, ['serial_id', 'serial_no', 'qr_code']);
    return { serial_id: id(s.serial_id), serial_no: text(s.serial_no, 200), qr_code: text(s.qr_code, 250) }; });
  const serialIds = new Set(serials.map(s => s.serial_id));
  if (serialIds.size !== serials.length || (serials.length && BigInt(serials.length) * 1000n !== units(quantity(r.historical_damaged_quantity)))) fail();
  for (const c of cases) if (!!c.serial_ids.length !== !!serials.length || c.serial_ids.some(s => !serialIds.has(s))) fail();
  const events = list(r.events, v => { const e = object(v, ['sequence', 'occurred_at', 'previous_event_id', 'evidence_file_ids', 'fact']);
    const time = text(e.occurred_at, 64);
    if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(time) || !Number.isFinite(Date.parse(time))) fail();
    return { sequence: integer(e.sequence), occurred_at: time, previous_event_id: nullable(e.previous_event_id), evidence_file_ids: ids(e.evidence_file_ids), fact: conditionFact(e.fact) }; });
  const latest = new Map<string, typeof events[number]>(), seen = new Set<string>();
  events.forEach((event, index) => {
    const f = event.fact, c = byCase.get(f.case_id), prior = latest.get(f.case_id);
    if (!c || event.sequence !== index + 1 || seen.has(f.event_id) || f.inbound_line_id !== expected.inbound
        || units(f.quantity) !== units(c.quantity) || event.previous_event_id !== (prior?.fact.event_id ?? null)
        || !transitions.has(`${f.action}/${prior?.fact.status ?? 'draft'}/${f.status}`)
        || (prior && Date.parse(event.occurred_at) < Date.parse(prior.occurred_at))) fail();
    latest.set(f.case_id, event); seen.add(f.event_id);
  });
  for (const c of cases) { const end = latest.get(c.case_id)?.fact;
    if (!end || end.event_id !== c.latest_event_id || end.request_hash !== c.latest_event_hash || end.status !== c.status) fail(); }
  const damaged = quantity(r.historical_damaged_quantity, true), held = quantity(r.held_quantity), corrected = quantity(r.corrected_quantity), unclaimed = quantity(r.unclaimed_quantity);
  const sum = (filter: (c: typeof cases[number]) => boolean) => cases.filter(filter).reduce((n, c) => n + units(c.quantity), 0n);
  if (units(damaged) !== units(held) + units(corrected) + units(unclaimed)
      || units(held) !== sum(c => !c.status.startsWith('released_') && c.status !== 'executed')
      || units(corrected) !== sum(c => c.status === 'executed')) fail();
  return { schema_version: 'condition_history/1', result_scope: 'verified_condition_history', inbound_line_id: expected.inbound,
    root_disposition_id: expected.root, material_id: expected.material, source_account_id: id(r.source_account_id),
    sku_code: text(r.sku_code, 80), material_name: text(r.material_name, 200), base_unit: text(r.base_unit, 32), serials,
    recorded_condition: r.recorded_condition as 'new' | 'used', required_condition: 'damaged', historical_damaged_quantity: damaged,
    held_quantity: held, corrected_quantity: corrected, unclaimed_quantity: unclaimed, cases, events,
    observed_ledger_cursor: integer(r.observed_ledger_cursor), history_fingerprint: hash(r.history_fingerprint),
    current_stock_verified: false, write_authorization_provided: false, retry_allowed: false, stock_effect: 'none' };
}
