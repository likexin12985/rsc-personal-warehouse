/** Complete original commands. The input digest is distinct from the posted event digest. */
import { canonical, hash, id, object } from './formalLossReview';
import { conditionActionLabels, conditionFact, conditionStatusLabels, type Action, type Fact } from './returnConditionHistory';
export type Scan = { serial_id: string; sku_code: string; serial_no: string; qr_code: string };
type Common = { request_id: string; idempotency_key: string; reason: string; evidence_file_ids: string[] };
export type SubmitCommand = Common & { action: 'submit_return_condition'; inbound_line_id: string;
  expected_source_hash: string; quantity: string; serial_verifications: Scan[] };
type CaseCommand = Common & { case_id: string; expected_event_id: string; expected_event_hash: string };
export type DecisionCommand = CaseCommand & { action: Exclude<Action, 'submit' | 'execute' | 'release'> };
export type SettlementCommand = CaseCommand & { action: 'execute' | 'release'; serial_verifications: Scan[] };
export type Command = SubmitCommand | DecisionCommand | SettlementCommand;
export type Pending = { v: 1; person_id: string; authorization_version: number; inbound_line_id: string;
  original: Command; original_input_hash: string };
export type Resolution = { status: 'pending' } | { status: 'found'; result: Fact } | { status: 'sealed' };
export function fail(message = '原成色纠正请求无法完整核验，请保留记录，勿重复操作'): never { throw new Error(message); }
export function digest(v: unknown): string { if (typeof v !== 'string' || !/^[a-f0-9]{64}$/.test(v)) fail(); return v; }
function text(v: unknown, max: number): string {
  if (typeof v !== 'string' || !v || [...v].length > max || [...v].some(c => {
    const n = c.codePointAt(0)!; return n >= 0xd800 && n <= 0xdfff;
  })) fail(); return v;
}
function coordinate(v: unknown, max: number): string { const s = text(v, max); if (!/^[A-Za-z0-9._:-]{8,}$/.test(s)) fail(); return s; }
function reason(v: unknown): string { const s = text(v, 500);
  if (/^[\s\u001c-\u001f\u0085]|[\s\u001c-\u001f\u0085]$/u.test(s) || /[\x00-\x08\x0b-\x1f]/.test(s)) fail(); return s; }
function list<T>(v: unknown, max: number, parse: (v: unknown) => T): T[] {
  if (!Array.isArray(v) || v.length > max) fail(); return v.map(parse);
}
function unique(v: string[]) { if (new Set(v).size !== v.length) fail(); }
function quantity(v: unknown): string {
  const s = text(v, 20); if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(s) || !/[1-9]/.test(s)) fail(); return s;
}
function normalizedQuantity(v: string): string { return v.includes('.') ? v.replace(/0+$/, '').replace(/\.$/, '') : v; }
export function action(c: Command): Action { return c.action === 'submit_return_condition' ? 'submit' : c.action; }
export function command(value: unknown): Command {
  if (!value || typeof value !== 'object') fail();
  const kind = (value as Record<string, unknown>).action, submit = kind === 'submit_return_condition';
  if (!submit && (typeof kind !== 'string' || kind === 'submit' || !Object.hasOwn(conditionActionLabels, kind))) fail();
  const settlement = kind === 'execute' || kind === 'release';
  const r = object(value, ['action', 'request_id', 'idempotency_key', 'reason', 'evidence_file_ids', ...(submit
    ? ['inbound_line_id', 'expected_source_hash', 'quantity', 'serial_verifications']
    : ['case_id', 'expected_event_id', 'expected_event_hash', ...(settlement ? ['serial_verifications'] : [])])]);
  const files = list(r.evidence_file_ids, 20, id); unique(files);
  if ((submit || kind === 'supplement' || kind === 'verify_region') && !files.length) fail('本次动作必须提供核验附件');
  const base = { action: kind, request_id: coordinate(r.request_id, 160), idempotency_key: coordinate(r.idempotency_key, 200),
    reason: reason(r.reason), evidence_file_ids: files };
  const scans = submit || settlement ? list(r.serial_verifications, 1000, v => {
    const s = object(v, ['serial_id', 'sku_code', 'serial_no', 'qr_code']);
    return { serial_id: id(s.serial_id), sku_code: text(s.sku_code, 80), serial_no: text(s.serial_no, 200), qr_code: text(s.qr_code, 250) };
  }) : [];
  unique(scans.map(s => s.serial_id));
  if (submit) return { ...base, action: 'submit_return_condition', inbound_line_id: id(r.inbound_line_id),
    expected_source_hash: digest(r.expected_source_hash), quantity: quantity(r.quantity), serial_verifications: scans };
  const result = { ...base, case_id: id(r.case_id), expected_event_id: id(r.expected_event_id), expected_event_hash: digest(r.expected_event_hash) };
  return (settlement ? { ...result, serial_verifications: scans } : result) as Command;
}
export async function originalInputHash(value: Command): Promise<string> {
  const c = command(value), { idempotency_key, ...publicFields } = c;
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('cloud_oam.inventory.idempotency.v1\0stock-condition:' + idempotency_key));
  const idempotency_key_hash = [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, '0')).join('');
  const schema_version = c.action === 'submit_return_condition' ? 'condition_input/1'
    : c.action === 'execute' || c.action === 'release' ? 'condition_settlement_input/1' : 'condition_decision_input/1';
  const document = { ...publicFields, schema_version, idempotency_key_hash, evidence_file_ids: [...c.evidence_file_ids].sort(),
    ...('quantity' in c ? { quantity: normalizedQuantity(c.quantity) } : {}),
    ...('serial_verifications' in c ? { serial_verifications: [...c.serial_verifications].sort((a, b) => a.serial_id < b.serial_id ? -1 : a.serial_id > b.serial_id ? 1 : 0) } : {}) };
  return hash(document);
}
export function pending(value: unknown): Pending {
  const r = object(value, ['v', 'person_id', 'authorization_version', 'inbound_line_id', 'original', 'original_input_hash']);
  if (r.v !== 1 || !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version) < 1) fail();
  const original = command(r.original), inbound = id(r.inbound_line_id);
  if (original.action === 'submit_return_condition' && original.inbound_line_id !== inbound) fail();
  return { v: 1, person_id: id(r.person_id), authorization_version: r.authorization_version as number,
    inbound_line_id: inbound, original, original_input_hash: digest(r.original_input_hash) };
}
export async function verifyPending(value: unknown): Promise<Pending> {
  const p = pending(value); if (await originalInputHash(p.original) !== p.original_input_hash) fail(); return p;
}
export async function prepare(person: string, version: number, inbound: string, value: unknown): Promise<Pending> {
  const original = command(value);
  return verifyPending({ v: 1, person_id: person, authorization_version: version, inbound_line_id: inbound,
    original, original_input_hash: await originalInputHash(original) });
}
export const same = (a: Pending, b: Pending) => canonical(pending(a)) === canonical(pending(b));
export function checkFact(value: unknown, p: Pending): Fact {
  const f = conditionFact(value), c = p.original;
  if (f.action !== action(c) || f.inbound_line_id !== p.inbound_line_id || f.actor_person_id !== p.person_id
      || f.request_id !== c.request_id || f.reason !== c.reason || f.authorization_version < p.authorization_version
      || (c.action === 'submit_return_condition' ? normalizedQuantity(f.quantity) !== normalizedQuantity(c.quantity) : f.case_id !== c.case_id)) fail();
  return f;
}
export function resolution(value: unknown, p: Pending): Resolution {
  if (!value || typeof value !== 'object') fail();
  const state = (value as Record<string, unknown>).request_state;
  const base = ['request_id', 'retry_allowed', 'current_stock_verified', 'observed_ledger_cursor', 'request_state', 'result_scope', 'result', 'absence_sealed'];
  const r = object(value, [...base, ...(state === 'found' ? ['current_case_status', 'original_input_hash']
    : state === 'sealed' ? ['seal', 'original_input_hash', 'original_preflight_verified', 'stock_effect'] : [])]);
  if (r.request_id !== p.original.request_id || r.retry_allowed !== false || r.current_stock_verified !== false
      || !Number.isSafeInteger(r.observed_ledger_cursor) || Number(r.observed_ledger_cursor) < 0) fail();
  if (state === 'unknown') {
    if (r.result_scope !== 'historical_original_outcome' || r.result !== null || r.absence_sealed !== false) fail();
    return { status: 'pending' };
  }
  if (digest(r.original_input_hash) !== p.original_input_hash) fail();
  if (state === 'found') {
    if (r.result_scope !== 'historical_original_outcome' || r.absence_sealed !== false
        || typeof r.current_case_status !== 'string' || !Object.hasOwn(conditionStatusLabels, r.current_case_status)) fail();
    return { status: 'found', result: checkFact(r.result, p) };
  }
  if (state !== 'sealed' || r.result_scope !== 'closed_original_request' || r.absence_sealed !== true || r.result !== null
      || r.original_preflight_verified !== false || r.stock_effect !== 'none') fail();
  const seal = object(r.seal, ['seal_id', 'kind', 'inbound_line_id', 'case_id', 'expected_event_id', 'sealed_at', 'stock_effect']);
  id(seal.seal_id); const c = p.original;
  if (seal.kind !== action(c) || seal.inbound_line_id !== p.inbound_line_id || seal.stock_effect !== 'none'
      || seal.case_id !== (c.action === 'submit_return_condition' ? null : c.case_id)
      || seal.expected_event_id !== (c.action === 'submit_return_condition' ? null : c.expected_event_id)
      || typeof seal.sealed_at !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(seal.sealed_at)
      || !Number.isFinite(Date.parse(seal.sealed_at))) fail();
  return { status: 'sealed' };
}
