/** Original receipt intent and preview matching. These commands never post inventory. */
import { canonical, digest, exception, fail, id, integer, list, micros, object, quantity, receipt, receiptLine, requestId, sameSource, source, sourceKeys, text, timestamp, unique, units, type History, type Identity, type Parcel } from './formalReturnReceiving';
import { hash } from './formalReturnInbound';
const PY_SPACE = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;
export function trimmed(value: unknown, max: number, reason = false): string {
  const s = text(value, max).replace(PY_SPACE, '');
  if (!s || (reason && /[\u0000-\u0008\u000b-\u001f]/.test(s))) fail('请填写明确原因或异常说明');
  return s;
}
function utc(value: unknown): string {
  const t = micros(value), seconds = t >= 0n ? t / 1000000n : (t - 999999n) / 1000000n;
  return new Date(Number(seconds * 1000n)).toISOString().slice(0, 19) + '.' + String(t - seconds * 1000000n).padStart(6, '0') + 'Z';
}
export function line(value: unknown) {
  const r = object(value, ['shipment_line_id', 'accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty', 'accepted_serial_verifications', 'damaged_serial_ids', 'rejected_serial_ids', 'shortage_serial_ids', 'exceptions']);
  const accepted_serial_verifications = list(r.accepted_serial_verifications, v => {
    const s = object(v, ['serial_id', 'sku_code', 'serial_no', 'qr_code']);
    return { serial_id: id(s.serial_id), sku_code: text(s.sku_code, 80), serial_no: text(s.serial_no, 200), qr_code: text(s.qr_code, 250) };
  }, 1000).sort((a, b) => a.serial_id < b.serial_id ? -1 : a.serial_id > b.serial_id ? 1 : 0);
  const damaged_serial_ids = list(r.damaged_serial_ids, id, 1000).sort(), rejected_serial_ids = list(r.rejected_serial_ids, id, 1000).sort(), shortage_serial_ids = list(r.shortage_serial_ids, id, 1000).sort();
  const exceptions = list(r.exceptions, v => { const e = exception(v); return { ...e, description: trimmed(e.description, 1000) }; }, 5).sort((a, b) => a.exception_type < b.exception_type ? -1 : a.exception_type > b.exception_type ? 1 : 0);
  unique([...accepted_serial_verifications.map(s => s.serial_id), ...rejected_serial_ids, ...shortage_serial_ids]); unique(damaged_serial_ids); unique(exceptions.map(e => e.exception_type));
  if (damaged_serial_ids.some(sn => !accepted_serial_verifications.some(s => s.serial_id === sn))) fail();
  const accepted_qty = quantity(r.accepted_qty), rejected_qty = quantity(r.rejected_qty), damaged_qty = quantity(r.damaged_qty), shortage_qty = quantity(r.shortage_qty), types = exceptions.map(e => e.exception_type);
  if (units(damaged_qty) > units(accepted_qty) || units(accepted_qty) + units(rejected_qty) + units(shortage_qty) <= 0n || Boolean(units(shortage_qty)) !== types.includes('shortage') || Boolean(units(damaged_qty)) !== types.includes('damaged') || Boolean(units(rejected_qty)) !== types.some(t => ['wrong_material', 'wrong_serial', 'rejected'].includes(t))) fail('异常数量需要对应说明和已上传的有效凭证');
  return { shipment_line_id: id(r.shipment_line_id), accepted_qty, rejected_qty, damaged_qty, shortage_qty, accepted_serial_verifications, damaged_serial_ids, rejected_serial_ids, shortage_serial_ids, exceptions };
}
export function input(value: unknown, person: string) {
  const r = object(value, ['operator_person_id', 'received_at', 'reason', 'lines']);
  if (id(r.operator_person_id) !== id(person)) fail('验收人必须是当前本人');
  const lines = list(r.lines, line, 100, 1).sort((a, b) => a.shipment_line_id < b.shipment_line_id ? -1 : a.shipment_line_id > b.shipment_line_id ? 1 : 0);
  unique(lines.map(l => l.shipment_line_id)); unique(lines.flatMap(l => [...l.accepted_serial_verifications.map(s => s.serial_id), ...l.rejected_serial_ids, ...l.shortage_serial_ids]));
  return { operator_person_id: id(r.operator_person_id), received_at: utc(r.received_at), reason: trimmed(r.reason, 500, true), lines };
}
export type Input = ReturnType<typeof input>;
export function command(value: unknown, person: string) {
  const r = object(value, ['operator_person_id', 'received_at', 'reason', 'lines', 'expected_plan_hash', 'request_id', 'idempotency_key']);
  const { expected_plan_hash, request_id, idempotency_key, ...body } = r;
  if (typeof idempotency_key !== 'string' || !/^[A-Za-z0-9._:-]{8,200}$/.test(idempotency_key)) fail();
  return { ...input(body, person), expected_plan_hash: digest(expected_plan_hash), request_id: requestId(request_id), idempotency_key };
}
export type Command = ReturnType<typeof command>;
export async function requestHash(shipment: string, value: Input): Promise<string> {
  return hash({ operation_type: 'receive_return', shipment_id: id(shipment), ...input(value, value.operator_person_id) });
}
export function checkSelection(value: Input, current: History): void {
  const body = input(value, current.person_id);
  if (micros(body.received_at) < micros(current.package.shipped_at)) fail();
  for (const row of body.lines) {
    const parcel = current.package.lines.find(l => l.shipment_line_id === row.shipment_line_id), remaining = current.lines.find(l => l.shipment_line_id === row.shipment_line_id);
    if (!parcel || !remaining || units(row.accepted_qty) + units(row.rejected_qty) + units(row.shortage_qty) > units(remaining.unconfirmed_qty)) fail('本次验收超过尚未确认的包裹数量');
    const groups = [row.accepted_serial_verifications.map(s => s.serial_id), row.rejected_serial_ids, row.shortage_serial_ids];
    if (parcel.serials.length ? groups.some((g, i) => BigInt(g.length) * 1000n !== units([row.accepted_qty, row.rejected_qty, row.shortage_qty][i])) || BigInt(row.damaged_serial_ids.length) * 1000n !== units(row.damaged_qty) : groups.flat().length > 0 || row.damaged_serial_ids.length > 0) fail();
    if (groups.flat().some(sn => !remaining.unconfirmed_serials.some(s => s.serial_id === sn))) fail('SN 已确认或不属于此包裹');
    if (row.accepted_serial_verifications.some(s => s.sku_code !== parcel.sku_code || !parcel.serials.some(old => old.serial_id === s.serial_id && old.serial_no === s.serial_no))) fail('SKU 或 SN 与原包裹不符');
  }
}
export async function preview(value: unknown, expected: Identity, body: Input, current: History) {
  checkSelection(body, current);
  const r = object(value, ['schema_version', 'planning_status', 'shipment_id', 'operation_id', ...sourceKeys(value), 'operator_person_id', 'authorization_version', 'received_at', 'reason', 'checked_at', 'ledger_cursor', 'package', 'request_hash', 'plan_hash', 'lines']);
  if (r.schema_version !== '1.0' || r.planning_status !== 'preview_only' || r.shipment_id !== current.package.shipment_id || r.operation_id !== current.package.operation_id || !sameSource(r, current.package) || r.operator_person_id !== expected.person_id || integer(r.authorization_version, 1) !== expected.authorization_version || expected.person_id !== current.person_id || expected.authorization_version !== current.authorization_version || canonical(r.package) !== canonical(current.package) || micros(r.received_at) !== micros(body.received_at) || r.reason !== body.reason || r.request_hash !== await requestHash(current.package.shipment_id, body)) fail('验收预检与原包裹或本次内容不一致');
  const checked_at = timestamp(r.checked_at); if (micros(checked_at) < micros(current.queried_at)) fail();
  const lines = list(r.lines, v => receiptLine(v, current.package), 100, 1); unique(lines.map(l => l.shipment_line_id));
  if (lines.length !== body.lines.length) fail();
  for (const row of lines) {
    const selected = body.lines.find(l => l.shipment_line_id === row.shipment_line_id), remaining = current.lines.find(l => l.shipment_line_id === row.shipment_line_id);
    if (!selected || !remaining || row.previously_accepted_qty !== remaining.accepted_qty || row.previously_rejected_qty !== remaining.rejected_qty || row.unconfirmed_qty !== remaining.unconfirmed_qty) fail();
    compareLine(row, selected);
  }
  return { schema_version: '1.0' as const, planning_status: 'preview_only' as const, shipment_id: current.package.shipment_id, operation_id: current.package.operation_id, ...source(current.package), operator_person_id: expected.person_id, authorization_version: expected.authorization_version, received_at: body.received_at, reason: body.reason, checked_at, ledger_cursor: integer(r.ledger_cursor), package: current.package, request_hash: digest(r.request_hash), plan_hash: digest(r.plan_hash), lines };
}
function compareLine(row: ReturnType<typeof receiptLine>, selected: ReturnType<typeof line>): void {
  if (['accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty'].some(k => row[k as keyof typeof row] !== selected[k as keyof typeof selected]) || canonical(row.exceptions.slice().sort((a, b) => a.exception_type < b.exception_type ? -1 : 1)) !== canonical(selected.exceptions)) fail();
  const pairs = (v: { serial_id: string; serial_no: string }[]) => [...v].sort((a, b) => a.serial_id < b.serial_id ? -1 : 1).map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no }));
  if (canonical(pairs(row.accepted_serials)) !== canonical(pairs(selected.accepted_serial_verifications)) || canonical(row.rejected_serials.map(s => s.serial_id).sort()) !== canonical(selected.rejected_serial_ids) || canonical(row.shortage_serials.map(s => s.serial_id).sort()) !== canonical(selected.shortage_serial_ids) || canonical([...row.damaged_serial_ids].sort()) !== canonical(selected.damaged_serial_ids)) fail();
}
export async function result(value: unknown, expected: Identity, parcel: Parcel, original: Command) {
  const c = command(original, expected.person_id), r = receipt(value, expected, parcel);
  const { expected_plan_hash, request_id, idempotency_key: _key, ...body } = c;
  if (r.request_id !== request_id || r.plan_hash !== expected_plan_hash || r.request_hash !== await requestHash(parcel.shipment_id, body) || micros(r.received_at) !== micros(c.received_at) || r.reason !== c.reason || r.lines.length !== c.lines.length) fail();
  for (const row of r.lines) { const selected = c.lines.find(l => l.shipment_line_id === row.shipment_line_id); if (!selected) fail(); compareLine(row, selected); }
  return r;
}
export async function lookup(value: unknown, expected: Identity, parcel: Parcel, original: Command) {
  if (value && typeof value === 'object' && Object.hasOwn(value, 'lookup_status')) {
    const r = object(value, ['schema_version', 'lookup_status', 'seal']);
    const s = object(r.seal, ['seal_id', 'operation_type', 'operator_person_id', ...sourceKeys(r.seal), 'operation_id', 'shipment_id', 'request_id', 'request_hash', 'sealed_at']);
    const c = command(original, expected.person_id), { expected_plan_hash: _p, request_id, idempotency_key: _k, ...body } = c;
    if (r.schema_version !== '1.0' || r.lookup_status !== 'sealed' || s.operation_type !== 'receive_return' || s.operator_person_id !== expected.person_id || s.operation_id !== parcel.operation_id || s.shipment_id !== parcel.shipment_id || !sameSource(s, parcel) || s.request_id !== request_id || s.request_hash !== await requestHash(parcel.shipment_id, body)) fail();
    return { status: 'sealed' as const, seal_id: id(s.seal_id), sealed_at: timestamp(s.sealed_at) };
  }
  return { status: 'accepted' as const, receipt: await result(value, expected, parcel, original) };
}
