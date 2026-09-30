/** Recipient projections. Acceptance, remaining parcels and inventory posting stay separate. */
export type Identity = { person_id: string; authorization_version: number };
export type LossOrigin = { origin_kind: 'loss_report'; loss_operation_id: string; loss_line_id: string; headquarters_decision_id: string; disposition_id: string };
export type Source = { origin: LossOrigin; work_order_id?: never } | { work_order_id: string; origin?: never };
export function fail(message = '退回接收记录未通过核验，请保留原请求并刷新'): never { throw new Error(message); }
export function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) fail();
  return row;
}
export function id(value: unknown): string {
  if (typeof value !== 'string' || !/^(?!00000000-0000-0000-0000-000000000000$)[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)) fail();
  return value;
}
export function text(value: unknown, max = 1000): string {
  if (typeof value !== 'string' || !value.trim() || [...value].length > max || [...value].some(c => {
    const n = c.codePointAt(0)!; return n === 0 || (n >= 0xd800 && n <= 0xdfff);
  })) fail();
  return value;
}
export function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || Number(value) < min || Number(value) > max) fail();
  return value as number;
}
export function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(); return value;
}
export function requestId(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{8,160}$/.test(value)) fail(); return value;
}
export function quantity(value: unknown): string {
  if (typeof value !== 'string' || !/^(?:0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value)) fail(); return value;
}
export function units(value: unknown): bigint { return BigInt(quantity(value).replace('.', '')); }
export function list<T>(value: unknown, parse: (v: unknown) => T, max = 100, min = 0): T[] {
  if (!Array.isArray(value) || value.length < min || value.length > max) fail(); return value.map(parse);
}
export function unique(values: string[]): void { if (new Set(values).size !== values.length) fail(); }
export function canonical(value: unknown): string {
  function ordered(v: unknown): unknown {
    if (Array.isArray(v)) return v.map(ordered);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, x]) => [k, ordered(x)]));
    return v;
  }
  return JSON.stringify(ordered(value));
}
/** Preserve PostgreSQL microsecond ordering; Date.parse alone loses sub-ms evidence. */
export function micros(value: unknown): bigint {
  const s = text(value, 40), m = /^(\d{4}-\d\d-\d\d)T(\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/.exec(s);
  if (!m) fail();
  const wall = `${m[1]}T${m[2]}`, utc = Date.parse(`${wall}Z`), date = Date.parse(`${wall}${m[4]}`);
  if (!Number.isFinite(date) || !Number.isFinite(utc) || new Date(utc).toISOString().slice(0, 19) !== wall) fail();
  return BigInt(date) * 1000n + BigInt((m[3] ?? '').padEnd(6, '0'));
}
export function timestamp(value: unknown): string { micros(value); return value as string; }
function notBefore(a: string, b: string): void { if (micros(a) < micros(b)) fail(); }
export function sourceKeys(value: unknown): string[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const loss = Object.hasOwn(value, 'origin'), work = Object.hasOwn(value, 'work_order_id');
  if (loss === work) fail('退回来源缺失或混杂，已停止操作'); return loss ? ['origin'] : ['work_order_id'];
}
export function source(value: unknown): Source {
  const keys = sourceKeys(value), r = value as Record<string, unknown>;
  if (keys[0] === 'work_order_id') return { work_order_id: id(r.work_order_id) };
  const o = object(r.origin, ['origin_kind', 'loss_operation_id', 'loss_line_id', 'headquarters_decision_id', 'disposition_id']);
  if (o.origin_kind !== 'loss_report') fail();
  return { origin: { origin_kind: 'loss_report', loss_operation_id: id(o.loss_operation_id), loss_line_id: id(o.loss_line_id), headquarters_decision_id: id(o.headquarters_decision_id), disposition_id: id(o.disposition_id) } };
}
export function sameSource(a: unknown, b: unknown): boolean { return canonical(source(a)) === canonical(source(b)); }
const BASE = ['shipment_line_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no'];
function metadata(r: Record<string, unknown>, loss: boolean) {
  if (!(loss ? ['new', 'used', 'damaged'] : ['used', 'damaged']).includes(String(r.condition_code))) fail();
  if ((r.lot_id === null) !== (r.lot_no === null)) fail();
  return { shipment_line_id: id(r.shipment_line_id), material_id: id(r.material_id), sku_code: text(r.sku_code, 80), material_name: text(r.material_name), base_unit: text(r.base_unit, 100),
    condition_code: r.condition_code as 'new' | 'used' | 'damaged', lot_id: r.lot_id === null ? null : id(r.lot_id), lot_no: r.lot_no === null ? null : text(r.lot_no, 160) };
}
export function serials(value: unknown) {
  const result = list(value, v => { const s = object(v, ['serial_id', 'serial_no']); return { serial_id: id(s.serial_id), serial_no: text(s.serial_no, 200) }; }, 1000);
  unique(result.map(s => s.serial_id)); return result;
}
function basis(r: Record<string, unknown>, expected: Identity) {
  if (r.schema_version !== '1.0' || id(r.person_id) !== id(expected.person_id) || integer(r.authorization_version, 1) !== integer(expected.authorization_version, 1)) fail('接收身份或授权版本已变化');
  return { schema_version: '1.0' as const, person_id: id(r.person_id), authorization_version: integer(r.authorization_version, 1), ledger_cursor: integer(r.ledger_cursor), queried_at: timestamp(r.queried_at) };
}
export function parcel(value: unknown, expected: Identity, shipment?: string) {
  const r = object(value, ['verification_status', 'shipment_id', 'shipment_no', 'operation_id', 'operation_no', ...sourceKeys(value), 'sender_person_id', 'receiver_person_id', 'target_location_id', 'target_location_name', 'custody_assignment_id', 'carrier', 'tracking_no', 'shipped_at', 'recorded_at', 'lines']);
  const origin = source(r);
  if (r.verification_status !== 'verified' || id(r.receiver_person_id) !== id(expected.person_id) || (shipment !== undefined && id(r.shipment_id) !== id(shipment))) fail();
  const shipped_at = timestamp(r.shipped_at), recorded_at = timestamp(r.recorded_at); notBefore(recorded_at, shipped_at);
  const lines = list(r.lines, v => {
    const row = object(v, [...BASE, 'outbound_no', 'shipped_quantity', 'serials']), sn = serials(row.serials), qty = units(row.shipped_quantity);
    if (qty <= 0n || (sn.length && BigInt(sn.length) * 1000n !== qty)) fail();
    return { ...metadata(row, 'origin' in origin), outbound_no: text(row.outbound_no, 100), shipped_quantity: quantity(row.shipped_quantity), serials: sn };
  }, 100, 1);
  unique(lines.map(l => l.shipment_line_id)); unique(lines.flatMap(l => l.serials.map(s => s.serial_id)));
  return { verification_status: 'verified' as const, ...origin, shipment_id: id(r.shipment_id), shipment_no: text(r.shipment_no, 100), operation_id: id(r.operation_id), operation_no: text(r.operation_no, 100),
    sender_person_id: id(r.sender_person_id), receiver_person_id: id(r.receiver_person_id), target_location_id: id(r.target_location_id), target_location_name: text(r.target_location_name, 300), custody_assignment_id: id(r.custody_assignment_id), carrier: text(r.carrier, 100), tracking_no: text(r.tracking_no, 100), shipped_at, recorded_at, lines };
}
export type Parcel = ReturnType<typeof parcel>;
export function directory(value: unknown, expected: Identity, limit = 10, after: string | null = null) {
  integer(limit, 1, 20); let previous = after === null ? null : id(after);
  const r = object(value, ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'items', 'next_after_id']), base = basis(r, expected);
  const items = list(r.items, v => {
    if (!v || typeof v !== 'object') fail();
    const key = id((v as Record<string, unknown>).shipment_id); if (previous !== null && key <= previous) fail(); previous = key;
    if ((v as Record<string, unknown>).verification_status === 'unavailable') {
      const blocked = object(v, ['verification_status', 'shipment_id', 'code', 'message']);
      if (blocked.code !== 'stock_return_receiving_verification_required') fail();
      return { verification_status: 'unavailable' as const, shipment_id: key, code: 'stock_return_receiving_verification_required' as const, message: text(blocked.message, 500) };
    }
    const result = parcel(v, expected); notBefore(base.queried_at, result.recorded_at); return result;
  }, limit);
  const next = r.next_after_id === null ? null : id(r.next_after_id);
  if (next !== null && (next !== previous || items.length !== limit)) fail();
  return { ...base, items, next_after_id: next };
}
export function detail(value: unknown, expected: Identity, shipment: string) {
  const r = object(value, ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'package']), base = basis(r, expected);
  const result = parcel(r.package, expected, shipment); notBefore(base.queried_at, result.recorded_at); return { ...base, package: result };
}
export const EXCEPTION_TYPES = ['shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected'] as const;
export function exception(value: unknown) {
  const r = object(value, ['exception_type', 'description', 'evidence_file_id']);
  if (!EXCEPTION_TYPES.includes(r.exception_type as typeof EXCEPTION_TYPES[number])) fail();
  return { exception_type: r.exception_type as typeof EXCEPTION_TYPES[number], description: text(r.description), evidence_file_id: id(r.evidence_file_id) };
}
const AMOUNTS = ['shipped_qty', 'previously_accepted_qty', 'previously_rejected_qty', 'unconfirmed_qty', 'accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty'] as const;
export function receiptLine(value: unknown, original: Parcel) {
  const r = object(value, [...BASE, ...AMOUNTS, 'accepted_serials', 'damaged_serial_ids', 'rejected_serials', 'shortage_serials', 'exceptions']);
  const meta = metadata(r, 'origin' in original), sourceLine = original.lines.find(l => l.shipment_line_id === meta.shipment_line_id);
  if (!sourceLine || BASE.some(k => r[k] !== sourceLine[k as keyof typeof sourceLine]) || r.shipped_qty !== sourceLine.shipped_quantity) fail();
  const q = Object.fromEntries(AMOUNTS.map(k => [k, quantity(r[k])])) as Record<typeof AMOUNTS[number], string>;
  const total = units(q.shipped_qty), before = units(q.previously_accepted_qty) + units(q.previously_rejected_qty), accepted = units(q.accepted_qty), rejected = units(q.rejected_qty), damaged = units(q.damaged_qty), shortage = units(q.shortage_qty);
  if (total <= 0n || before > total || units(q.unconfirmed_qty) !== total - before || accepted + rejected + shortage <= 0n || accepted + rejected + shortage > total - before || damaged > accepted) fail();
  const exceptions = list(r.exceptions, exception, 5), types = exceptions.map(e => e.exception_type); unique(types);
  if (Boolean(shortage) !== types.includes('shortage') || Boolean(damaged) !== types.includes('damaged') || Boolean(rejected) !== types.some(t => ['wrong_material', 'wrong_serial', 'rejected'].includes(t))) fail();
  const accepted_serials = serials(r.accepted_serials), rejected_serials = serials(r.rejected_serials), shortage_serials = serials(r.shortage_serials), damaged_serial_ids = list(r.damaged_serial_ids, id, 1000);
  const groups = [accepted_serials, rejected_serials, shortage_serials], all = groups.flat(); unique(all.map(s => s.serial_id)); unique(damaged_serial_ids);
  if (damaged_serial_ids.some(k => !accepted_serials.some(s => s.serial_id === k)) || all.some(s => !sourceLine.serials.some(old => canonical(old) === canonical(s)))) fail();
  if (sourceLine.serials.length ? groups.some((g, i) => BigInt(g.length) * 1000n !== [accepted, rejected, shortage][i]) || BigInt(damaged_serial_ids.length) * 1000n !== damaged : all.length > 0 || damaged_serial_ids.length > 0) fail();
  return { ...meta, ...q, accepted_serials, damaged_serial_ids, rejected_serials, shortage_serials, exceptions };
}
export function receipt(value: unknown, expected: Identity, original: Parcel) {
  const r = object(value, ['schema_version', 'receipt_id', 'receipt_no', 'shipment_id', 'operation_id', ...sourceKeys(value), 'operator_person_id', 'status', 'received_at', 'recorded_at', 'reason', 'request_id', 'request_hash', 'plan_hash', 'target_location_id', 'target_custody_assignment_id', 'lines']);
  if (r.schema_version !== '1.0' || id(r.operator_person_id) !== id(expected.person_id) || !sameSource(r, original) || ['shipment_id', 'operation_id', 'target_location_id'].some(k => r[k] !== original[k as keyof Parcel]) || r.target_custody_assignment_id !== original.custody_assignment_id) fail();
  const received_at = timestamp(r.received_at), recorded_at = timestamp(r.recorded_at);
  notBefore(recorded_at, received_at); notBefore(received_at, original.shipped_at); notBefore(recorded_at, original.recorded_at);
  const lines = list(r.lines, v => receiptLine(v, original), 100, 1); unique(lines.map(l => l.shipment_line_id));
  unique(lines.flatMap(l => [...l.accepted_serials, ...l.rejected_serials, ...l.shortage_serials].map(s => s.serial_id)));
  const status = lines.some(l => l.exceptions.length) ? 'exception' as const : 'accepted' as const; if (r.status !== status) fail();
  return { schema_version: '1.0' as const, receipt_id: id(r.receipt_id), receipt_no: text(r.receipt_no, 100), shipment_id: original.shipment_id, operation_id: original.operation_id, ...source(r), operator_person_id: id(r.operator_person_id), status, received_at, recorded_at, reason: text(r.reason, 500), request_id: requestId(r.request_id), request_hash: digest(r.request_hash), plan_hash: digest(r.plan_hash), target_location_id: original.target_location_id, target_custody_assignment_id: original.custody_assignment_id, lines };
}
export type Receipt = ReturnType<typeof receipt>;
export function history(value: unknown, expected: Identity, shipment: string) {
  const r = object(value, ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'package', 'receipts', 'lines']), base = basis(r, expected), original = parcel(r.package, expected, shipment);
  notBefore(base.queried_at, original.recorded_at);
  const totals = new Map(original.lines.map(l => [l.shipment_line_id, { accepted: 0n, rejected: 0n, damaged: 0n, seen: new Set<string>() }]));
  let previous = original.recorded_at;
  const receipts = list(r.receipts, v => {
    const item = receipt(v, expected, original); notBefore(item.recorded_at, previous); notBefore(base.queried_at, item.recorded_at); previous = item.recorded_at;
    for (const line of item.lines) {
      const sum = totals.get(line.shipment_line_id)!;
      if (units(line.previously_accepted_qty) !== sum.accepted || units(line.previously_rejected_qty) !== sum.rejected) fail();
      sum.accepted += units(line.accepted_qty); sum.rejected += units(line.rejected_qty); sum.damaged += units(line.damaged_qty);
      if ([...line.accepted_serials, ...line.rejected_serials, ...line.shortage_serials].some(sn => sum.seen.has(sn.serial_id))) fail();
      // Shortage observations leave the SN available for a later acceptance.
      for (const sn of [...line.accepted_serials, ...line.rejected_serials]) sum.seen.add(sn.serial_id);
    }
    return item;
  }, 1000);
  unique(receipts.map(r => r.receipt_id)); unique(receipts.map(r => r.request_id));
  const lines = list(r.lines, v => {
    const row = object(v, ['shipment_line_id', 'shipped_qty', 'accepted_qty', 'rejected_qty', 'damaged_qty', 'unconfirmed_qty', 'unconfirmed_serials']), key = id(row.shipment_line_id), sum = totals.get(key), src = original.lines.find(l => l.shipment_line_id === key);
    if (!sum || !src || row.shipped_qty !== src.shipped_quantity || units(row.accepted_qty) !== sum.accepted || units(row.rejected_qty) !== sum.rejected || units(row.damaged_qty) !== sum.damaged || units(row.unconfirmed_qty) !== units(src.shipped_quantity) - sum.accepted - sum.rejected) fail();
    const remaining = serials(row.unconfirmed_serials);
    if (canonical(remaining) !== canonical(src.serials.filter(sn => !sum.seen.has(sn.serial_id)))) fail();
    return { shipment_line_id: key, shipped_qty: quantity(row.shipped_qty), accepted_qty: quantity(row.accepted_qty), rejected_qty: quantity(row.rejected_qty), damaged_qty: quantity(row.damaged_qty), unconfirmed_qty: quantity(row.unconfirmed_qty), unconfirmed_serials: remaining };
  }, 100, 1);
  unique(lines.map(l => l.shipment_line_id)); if (lines.length !== totals.size) fail();
  return { ...base, package: original, receipts, lines };
}
export type History = ReturnType<typeof history>;
