/** An accepted return receipt needs its own preview, request and posting proof. */
import { canonical, digest, fail, id, integer, list, micros, object, quantity, requestId, sameSource, source, text, timestamp, unique, units, type Identity, type Receipt } from './formalReturnReceiving';

export async function hash(value: unknown): Promise<string> {
  const result = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value)));
  return [...new Uint8Array(result)].map(n => n.toString(16).padStart(2, '0')).join('');
}
function basis(r: Record<string, unknown>, expected: Identity, receipt: Receipt, versions = ['1.0']): void {
  if (!versions.includes(String(r.schema_version)) || id(r.receipt_id) !== receipt.receipt_id || id(r.shipment_id) !== receipt.shipment_id || id(r.operator_person_id) !== id(expected.person_id) || integer(r.authorization_version, 1) !== expected.authorization_version) fail('入库对象或当前身份已变化');
  integer(r.ledger_cursor); timestamp(r.checked_at);
  if (micros(r.checked_at) < micros(receipt.recorded_at)) fail();
}
export function state(value: unknown, expected: Identity, receipt: Receipt) {
  const r = object(value, ['schema_version', 'receipt_id', 'shipment_id', 'operator_person_id', 'authorization_version', 'status', 'inbound', 'ledger_cursor', 'checked_at']);
  basis(r, expected, receipt);
  const base = { schema_version: '1.0' as const, receipt_id: receipt.receipt_id, shipment_id: receipt.shipment_id, operator_person_id: expected.person_id, authorization_version: expected.authorization_version, ledger_cursor: integer(r.ledger_cursor), checked_at: timestamp(r.checked_at) };
  if (r.status === 'not_posted') { if (r.inbound !== null) fail(); return { ...base, status: 'not_posted' as const, inbound: null }; }
  if (r.status !== 'posted') fail();
  const row = object(r.inbound, ['inbound_id', 'inbound_no', 'target_location_id', 'posting_transaction_id', 'posted_at']);
  const posted_at = timestamp(row.posted_at);
  if (id(row.target_location_id) !== receipt.target_location_id || micros(posted_at) > micros(base.checked_at) || micros(posted_at) < micros(receipt.recorded_at)) fail();
  return { ...base, status: 'posted' as const, inbound: { inbound_id: id(row.inbound_id), inbound_no: text(row.inbound_no, 100), target_location_id: receipt.target_location_id, posting_transaction_id: id(row.posting_transaction_id), posted_at } };
}
export function preview(value: unknown, expected: Identity, receipt: Receipt) {
  const loss = Object.hasOwn(receipt, 'origin');
  const r = object(value, ['schema_version', 'planning_status', 'receipt_id', 'shipment_id', 'operator_person_id', 'authorization_version', 'target_location_id', 'target_custody_assignment_id', 'receipt_plan_hash', 'plan_hash', 'reason', 'checked_at', 'ledger_cursor', 'lines', ...(loss ? ['origin'] : [])]);
  basis(r, expected, receipt, ['1.0', '2.0']);
  const version = r.schema_version as '1.0' | '2.0';
  if (r.planning_status !== 'inbound_preview_only' || r.receipt_plan_hash !== receipt.plan_hash || r.target_location_id !== receipt.target_location_id || r.target_custody_assignment_id !== receipt.target_custody_assignment_id || (loss && !sameSource(r, receipt))) fail();
  const lines = list(r.lines, v => {
    const line = object(v, ['receipt_line_id', 'shipment_line_id', 'source_account_id', 'target_account_id', 'material_id', 'condition_code', 'lot_id', 'accepted_qty', 'serial_ids']);
    const original = receipt.lines.find(l => l.shipment_line_id === id(line.shipment_line_id));
    if (!original || line.material_id !== original.material_id || line.lot_id !== original.lot_id || !([original.condition_code, 'damaged'] as unknown[]).includes(line.condition_code) || units(line.accepted_qty) <= 0n) fail();
    const serial_ids = list(line.serial_ids, id, 1000); unique(serial_ids);
    if (original.accepted_serials.length ? units(line.accepted_qty) !== BigInt(serial_ids.length) * 1000n : serial_ids.length > 0) fail();
    if (serial_ids.some(sn => !original.accepted_serials.some(s => s.serial_id === sn))) fail();
    const source_account_id = id(line.source_account_id), target_account_id = id(line.target_account_id);
    if (source_account_id === target_account_id) fail();
    return { receipt_line_id: id(line.receipt_line_id), shipment_line_id: original.shipment_line_id, source_account_id, target_account_id, material_id: original.material_id, condition_code: line.condition_code as 'new' | 'used' | 'damaged', lot_id: original.lot_id, accepted_qty: quantity(line.accepted_qty), serial_ids };
  }, 200);
  unique(lines.map(l => `${l.receipt_line_id}:${l.condition_code}`)); unique(lines.flatMap(l => l.serial_ids));
  const origins = new Map<string, string>();
  const targets = new Map<string, string>();
  for (const l of lines) {
    const key = canonical([l.material_id, l.lot_id, l.condition_code]);
    if ((origins.has(l.receipt_line_id) && origins.get(l.receipt_line_id) !== l.shipment_line_id)
      || (targets.has(l.target_account_id) && targets.get(l.target_account_id) !== key)) fail();
    origins.set(l.receipt_line_id, l.shipment_line_id); targets.set(l.target_account_id, key);
  }
  if (!lines.length) fail('此验收没有可入库的已接受物料');
  for (const original of receipt.lines) {
    const matches = lines.filter(l => l.shipment_line_id === original.shipment_line_id);
    if (matches.reduce((n, l) => n + units(l.accepted_qty), 0n) !== units(original.accepted_qty)) fail();
    if (matches.length > 2 || new Set(matches.map(l => l.receipt_line_id)).size > 1
      || new Set(matches.map(l => l.source_account_id)).size > 1) fail();
    unique(matches.map(l => l.condition_code));
    const damaged = original.condition_code === 'damaged' ? units(original.accepted_qty) : units(original.damaged_qty);
    if (matches.filter(l => l.condition_code === 'damaged').reduce((n, l) => n + units(l.accepted_qty), 0n) !== damaged) fail('破损验收必须进入对应坏件账户');
    for (const part of matches) {
      const expectedSerials = original.accepted_serials.filter(sn => original.condition_code === 'damaged'
        || (part.condition_code === 'damaged') === original.damaged_serial_ids.includes(sn.serial_id)).map(sn => sn.serial_id).sort();
      if (canonical([...part.serial_ids].sort()) !== canonical(expectedSerials)) fail('入库成色与验收SN不一致');
    }
    if (version === '1.0' && matches.length > 1) fail();
    if (canonical(matches.flatMap(l => l.serial_ids).sort()) !== canonical(original.accepted_serials.map(s => s.serial_id).sort())) fail();
  }
  return { schema_version: version, planning_status: 'inbound_preview_only' as const, receipt_id: receipt.receipt_id, shipment_id: receipt.shipment_id, operator_person_id: expected.person_id, authorization_version: expected.authorization_version,
    target_location_id: receipt.target_location_id, target_custody_assignment_id: receipt.target_custody_assignment_id, receipt_plan_hash: receipt.plan_hash, plan_hash: digest(r.plan_hash), reason: text(r.reason, 500), checked_at: timestamp(r.checked_at), ledger_cursor: integer(r.ledger_cursor), lines, ...(loss ? source(receipt) : {}) };
}
export type InboundPreview = ReturnType<typeof preview>;
export type Command = { operator_person_id: string; expected_plan_hash: string; request_id: string; idempotency_key: string };
export type Original = { receipt_id: string; shipment_id: string; target_location_id: string; target_custody_assignment_id: string; request_hash: string; command: Command };
export function command(value: unknown): Command {
  const r = object(value, ['operator_person_id', 'expected_plan_hash', 'request_id', 'idempotency_key']);
  if (typeof r.idempotency_key !== 'string' || !/^[A-Za-z0-9._:-]{8,200}$/.test(r.idempotency_key)) fail();
  return { operator_person_id: id(r.operator_person_id), expected_plan_hash: digest(r.expected_plan_hash), request_id: requestId(r.request_id), idempotency_key: r.idempotency_key };
}
export async function original(value: unknown): Promise<Original> {
  const r = object(value, ['receipt_id', 'shipment_id', 'target_location_id', 'target_custody_assignment_id', 'request_hash', 'command']), c = command(r.command);
  const receipt_id = id(r.receipt_id), request_hash = digest(r.request_hash);
  if (request_hash !== await hash({ receipt_id, request_id: c.request_id, plan_hash: c.expected_plan_hash })) fail('原入库请求摘要不一致');
  return { receipt_id, shipment_id: id(r.shipment_id), target_location_id: id(r.target_location_id), target_custody_assignment_id: id(r.target_custody_assignment_id), request_hash, command: c };
}
export function result(value: unknown, expected: Original) {
  const r = object(value, ['schema_version', 'inbound_id', 'inbound_no', 'receipt_id', 'shipment_id', 'target_location_id', 'target_custody_assignment_id', 'status', 'posting_transaction_id', 'request_id', 'request_hash', 'plan_hash', 'replayed']);
  if (r.schema_version !== '1.0' || r.status !== 'posted' || r.receipt_id !== expected.receipt_id || r.shipment_id !== expected.shipment_id || r.target_location_id !== expected.target_location_id || r.target_custody_assignment_id !== expected.target_custody_assignment_id || r.request_id !== expected.command.request_id || r.request_hash !== expected.request_hash || r.plan_hash !== expected.command.expected_plan_hash || typeof r.replayed !== 'boolean') fail();
  return { schema_version: '1.0' as const, inbound_id: id(r.inbound_id), inbound_no: text(r.inbound_no, 100), receipt_id: expected.receipt_id, shipment_id: expected.shipment_id, target_location_id: expected.target_location_id, target_custody_assignment_id: expected.target_custody_assignment_id, status: 'posted' as const, posting_transaction_id: id(r.posting_transaction_id), request_id: expected.command.request_id, request_hash: expected.request_hash, plan_hash: expected.command.expected_plan_hash, replayed: r.replayed };
}
export function lookup(value: unknown, expected: Original) {
  if (value && typeof value === 'object' && Object.hasOwn(value, 'lookup_status')) {
    const r = object(value, ['schema_version', 'lookup_status', 'seal']), seal = object(r.seal, ['seal_id', 'receipt_id', 'shipment_id', 'request_id', 'request_hash', 'sealed_at']);
    if (r.schema_version !== '1.0' || r.lookup_status !== 'sealed' || seal.receipt_id !== expected.receipt_id || seal.shipment_id !== expected.shipment_id || seal.request_id !== expected.command.request_id || seal.request_hash !== expected.request_hash) fail();
    return { status: 'sealed' as const, seal_id: id(seal.seal_id), sealed_at: timestamp(seal.sealed_at) };
  }
  return { status: 'posted' as const, inbound: result(value, expected) };
}
