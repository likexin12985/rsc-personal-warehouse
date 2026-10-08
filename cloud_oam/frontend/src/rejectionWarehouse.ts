/** Refusal returns: handed over, accepted and posted remain separate facts. */
import { amounts } from './formalReturnReceipt';
import { canonical, digest, fail, id, integer, list, micros, object, quantity, serials, text, timestamp, unique, units, type Identity } from './formalReturnReceiving';
import { validateCloseInput } from './materialRequestClosure';
import { progressTime } from './materialRequestRejection';
import { hash } from './formalReturnInbound';

export type Amounts = ReturnType<typeof amounts>;
const bool = (value: unknown): boolean => { if (typeof value !== 'boolean') fail(); return value; };
const condition = (value: unknown): 'new' | 'used' | 'damaged' => {
  if (value !== 'new' && value !== 'used' && value !== 'damaged') fail(); return value;
};
export function source(value: unknown, identity: Identity) {
  const r = object(value, ('return_id return_no request_id request_version registration_request_hash handover_id handover_request_hash handed_over_at carrier tracking_no quantity material_id sku_code material_name base_unit lot_id source_condition original_exception serials target_location_id target_location_name custody_assignment_id receiver_person_id authorization_version status tracking_mode quantity_scale allow_fraction').split(' '));
  if (r.receiver_person_id !== identity.person_id || integer(r.authorization_version, 1) !== identity.authorization_version || r.status !== 'handed_over') fail('当前仓库责任或身份已经变化');
  if (!['none', 'lot', 'serial', 'lot_and_serial'].includes(String(r.tracking_mode)) || !['shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected'].includes(String(r.original_exception))) fail();
  const sn = serials(r.serials), qty = units(r.quantity), tracked = ['serial', 'lot_and_serial'].includes(String(r.tracking_mode));
  if (qty <= 0n || (tracked ? BigInt(sn.length) * 1000n !== qty : sn.length > 0)) fail();
  const scale = integer(r.quantity_scale, 0, 3), fractional = bool(r.allow_fraction);
  if (qty % (10n ** BigInt(3 - scale)) || (!fractional && qty % 1000n) || (tracked && fractional)) fail();
  return { return_id: id(r.return_id), return_no: text(r.return_no), request_id: id(r.request_id), request_version: integer(r.request_version, 1),
    registration_request_hash: digest(r.registration_request_hash), handover_id: id(r.handover_id), handover_request_hash: digest(r.handover_request_hash),
    handed_over_at: timestamp(r.handed_over_at), carrier: text(r.carrier, 100), tracking_no: text(r.tracking_no, 100), quantity: quantity(r.quantity),
    material_id: id(r.material_id), sku_code: text(r.sku_code, 80), material_name: text(r.material_name), base_unit: text(r.base_unit),
    lot_id: r.lot_id === null ? null : id(r.lot_id), source_condition: condition(r.source_condition), original_exception: String(r.original_exception),
    serials: sn, target_location_id: id(r.target_location_id), target_location_name: text(r.target_location_name), custody_assignment_id: id(r.custody_assignment_id),
    receiver_person_id: id(r.receiver_person_id), authorization_version: integer(r.authorization_version, 1), status: 'handed_over' as const,
    tracking_mode: r.tracking_mode as 'none' | 'lot' | 'serial' | 'lot_and_serial', quantity_scale: scale, allow_fraction: fractional };
}
export type Source = ReturnType<typeof source>;
export function receiptInput(value: unknown) {
  const r = object(value, 'expected_request_version reason registration_request_hash handover_id handover_request_hash custody_assignment_id received_at amounts observed_sku_code'.split(' '));
  const base = validateCloseInput({ expected_request_version: r.expected_request_version, reason: r.reason });
  const a = amounts(r.amounts), sku = r.observed_sku_code === null ? null : text(r.observed_sku_code, 80);
  if (Boolean(units(a.accepted_qty)) !== (sku !== null) || (sku !== null && sku !== sku.trim())) fail('请按实物核对物料码');
  return { ...base, registration_request_hash: digest(r.registration_request_hash), handover_id: id(r.handover_id),
    handover_request_hash: digest(r.handover_request_hash), custody_assignment_id: id(r.custody_assignment_id),
    received_at: progressTime(timestamp(r.received_at)), amounts: a, observed_sku_code: sku };
}
export type ReceiptInput = ReturnType<typeof receiptInput>;
export function inboundInput(value: unknown) {
  const r = object(value, 'expected_request_version reason receipt_request_hash expected_plan_hash'.split(' '));
  return { ...validateCloseInput({ expected_request_version: r.expected_request_version, reason: r.reason }),
    receipt_request_hash: digest(r.receipt_request_hash), expected_plan_hash: digest(r.expected_plan_hash) };
}
export type InboundInput = ReturnType<typeof inboundInput>;
function checkAmounts(a: Amounts, s: Source) {
  const grouped = [a.accepted_serial_verifications.map(sn => sn.serial_id), a.rejected_serial_ids, a.shortage_serial_ids, a.damaged_serial_ids];
  const quantities = [a.accepted_qty, a.rejected_qty, a.shortage_qty, a.damaged_qty];
  if (quantities.some(q => units(q) % (10n ** BigInt(3 - s.quantity_scale)) || (!s.allow_fraction && units(q) % 1000n))) fail('验收数量不符合物料精度');
  if (grouped.some((g, i) => s.serials.length ? BigInt(g.length) * 1000n !== units(quantities[i]) : g.length > 0)) fail('验收 SN 数量不一致');
  if (grouped.flat().some(sid => !s.serials.some(sn => sn.serial_id === sid))) fail('SN 不属于该退回');
  if (a.accepted_serial_verifications.some(sn => sn.sku_code !== s.sku_code || !s.serials.some(old => old.serial_id === sn.serial_id && old.serial_no === sn.serial_no))) fail('实物物料码或 SN 不一致');
}
export function receipt(value: unknown, s: Source) {
  const r = object(value, 'receipt_id return_id request_id request_version receiver_person_id target_location_id custody_assignment_id handover_id received_at recorded_at amounts reason observed_sku_code request_hash replayed'.split(' '));
  const a = amounts(r.amounts); checkAmounts(a, s);
  if (r.return_id !== s.return_id || r.request_id !== s.request_id || r.target_location_id !== s.target_location_id || r.handover_id !== s.handover_id
      || integer(r.request_version, 1) > s.request_version || (units(a.accepted_qty) ? r.observed_sku_code !== s.sku_code : r.observed_sku_code !== null)
      || micros(r.received_at) < micros(s.handed_over_at) || micros(r.recorded_at) < micros(r.received_at)) fail();
  return { receipt_id: id(r.receipt_id), return_id: s.return_id, request_id: s.request_id, request_version: integer(r.request_version, 1),
    receiver_person_id: id(r.receiver_person_id), target_location_id: s.target_location_id, custody_assignment_id: id(r.custody_assignment_id),
    handover_id: s.handover_id, received_at: timestamp(r.received_at), recorded_at: timestamp(r.recorded_at), amounts: a,
    reason: text(r.reason), observed_sku_code: r.observed_sku_code as string | null, request_hash: digest(r.request_hash), replayed: bool(r.replayed) };
}
export type Receipt = ReturnType<typeof receipt>;
export function inbound(value: unknown, r: Receipt) {
  const v = object(value, 'inbound_id receipt_id return_id request_id inventory_transaction_id target_location_id request_hash plan_hash replayed'.split(' '));
  if (v.receipt_id !== r.receipt_id || v.return_id !== r.return_id || v.request_id !== r.request_id || v.target_location_id !== r.target_location_id || !units(r.amounts.accepted_qty)) fail();
  return { inbound_id: id(v.inbound_id), receipt_id: r.receipt_id, return_id: r.return_id, request_id: r.request_id,
    inventory_transaction_id: id(v.inventory_transaction_id), target_location_id: r.target_location_id,
    request_hash: digest(v.request_hash), plan_hash: digest(v.plan_hash), replayed: bool(v.replayed) };
}
export function detail(value: unknown, identity: Identity, returnId: string) {
  const r = object(value, 'schema_version source accepted_qty rejected_qty damaged_qty unconfirmed_qty posted_qty pending_inbound_qty unconfirmed_serials receive_permitted receipts'.split(' '));
  const s = source(r.source, identity); if (r.schema_version !== '1.0' || s.return_id !== id(returnId)) fail();
  const receipts = list(r.receipts, value => {
    const h = object(value, ['receipt', 'inbound', 'post_permitted']), fact = receipt(h.receipt, s);
    const posted = h.inbound === null ? null : inbound(h.inbound, fact), allowed = bool(h.post_permitted);
    if (allowed && (posted !== null || !units(fact.amounts.accepted_qty))) fail();
    return { receipt: fact, inbound: posted, post_permitted: allowed };
  }, 1000);
  unique(receipts.map(h => h.receipt.receipt_id)); unique(receipts.flatMap(h => h.inbound ? [h.inbound.inbound_id] : []));
  const used: string[] = []; let accepted = 0n, rejected = 0n, damaged = 0n, posted = 0n;
  for (const h of receipts) {
    const a = h.receipt.amounts; accepted += units(a.accepted_qty); rejected += units(a.rejected_qty); damaged += units(a.damaged_qty);
    if (h.inbound) posted += units(a.accepted_qty);
    used.push(...a.accepted_serial_verifications.map(sn => sn.serial_id), ...a.rejected_serial_ids);
  }
  unique(used); const remaining = serials(r.unconfirmed_serials), permitted = bool(r.receive_permitted);
  if (accepted !== units(r.accepted_qty) || rejected !== units(r.rejected_qty) || damaged !== units(r.damaged_qty)
      || posted !== units(r.posted_qty) || accepted + rejected + units(r.unconfirmed_qty) !== units(s.quantity)
      || accepted - posted !== units(r.pending_inbound_qty) || (permitted && !units(r.unconfirmed_qty))
      || canonical(remaining) !== canonical(s.serials.filter(sn => !used.includes(sn.serial_id)))) fail('验收、未确认与库存入账数量不一致');
  return { schema_version: '1.0' as const, source: s, accepted_qty: quantity(r.accepted_qty), rejected_qty: quantity(r.rejected_qty),
    damaged_qty: quantity(r.damaged_qty), unconfirmed_qty: quantity(r.unconfirmed_qty), posted_qty: quantity(r.posted_qty),
    pending_inbound_qty: quantity(r.pending_inbound_qty), unconfirmed_serials: remaining, receive_permitted: permitted, receipts };
}
export type Detail = ReturnType<typeof detail>;
export function inbox(value: unknown, identity: Identity, after: string | null = null) {
  const r = object(value, ['schema_version', 'person_id', 'authorization_version', 'items', 'next_after_id']);
  if (r.schema_version !== '1.0' || r.person_id !== identity.person_id || r.authorization_version !== identity.authorization_version) fail();
  let previous = after === null ? '' : id(after);
  const items = list(r.items, value => {
    const item = object(value, ['return_id', 'verification_status', 'message', 'detail']), returnId = id(item.return_id);
    if (returnId <= previous) fail(); previous = returnId;
    if (item.verification_status !== 'verified' && item.verification_status !== 'blocked') fail();
    if ((item.verification_status === 'verified') !== (item.detail !== null)) fail();
    return { return_id: returnId, verification_status: item.verification_status, message: text(item.message),
      detail: item.detail === null ? null : detail(item.detail, identity, returnId) };
  }, 20);
  const next = r.next_after_id === null ? null : id(r.next_after_id);
  if (next !== null && (!items.length || next !== previous)) fail();
  return { schema_version: '1.0' as const, ...identity, items, next_after_id: next };
}
export function preview(value: unknown, identity: Identity, current: Detail, receiptId: string) {
  const r = object(value, 'schema_version return_id receipt_id request_id request_version person_id authorization_version receipt_request_hash plan_hash target_location_id target_location_name sku_code material_name parts'.split(' '));
  const row = current.receipts.find(h => h.receipt.receipt_id === receiptId), s = current.source;
  if (!row || row.inbound || !row.post_permitted || r.schema_version !== '1.0' || r.person_id !== identity.person_id || r.authorization_version !== identity.authorization_version
      || r.return_id !== s.return_id || r.receipt_id !== receiptId || r.request_id !== s.request_id || r.request_version !== s.request_version
      || r.target_location_id !== s.target_location_id || r.target_location_name !== s.target_location_name || r.sku_code !== s.sku_code || r.material_name !== s.material_name
      || r.receipt_request_hash !== row.receipt.request_hash) fail('入账预览与原验收或当前仓库不一致');
  const a = row.receipt.amounts, expected = new Map<string, bigint>();
  expected.set(s.source_condition, units(a.accepted_qty) - units(a.damaged_qty));
  expected.set('damaged', (expected.get('damaged') ?? 0n) + units(a.damaged_qty));
  const parts = list(r.parts, value => {
    const p = object(value, ['condition_code', 'quantity', 'serials']), c = condition(p.condition_code), sn = serials(p.serials);
    if (units(p.quantity) <= 0n || expected.get(c) !== units(p.quantity)) fail('分成色入账数量不一致');
    const ids = a.accepted_serial_verifications.filter(sid => (a.damaged_serial_ids.includes(sid.serial_id) ? 'damaged' : s.source_condition) === c).map(sid => ({ serial_id: sid.serial_id, serial_no: sid.serial_no }));
    if (canonical(sn) !== canonical(ids)) fail('分成色入账 SN 不一致');
    return { condition_code: c, quantity: quantity(p.quantity), serials: sn };
  }, 2, 1);
  unique(parts.map(p => p.condition_code));
  if (parts.length !== [...expected.values()].filter(q => q > 0n).length) fail();
  return { schema_version: '1.0' as const, ...identity, return_id: s.return_id, receipt_id: receiptId, request_id: s.request_id, request_version: s.request_version,
    receipt_request_hash: row.receipt.request_hash, plan_hash: digest(r.plan_hash), target_location_id: s.target_location_id,
    target_location_name: s.target_location_name, sku_code: s.sku_code, material_name: s.material_name, parts };
}
export type Preview = ReturnType<typeof preview>;
export function checkSelection(input: ReceiptInput, current: Detail) {
  const c = receiptInput(input), s = current.source, a = c.amounts; checkAmounts(a, s);
  if (!current.receive_permitted || c.expected_request_version !== s.request_version || c.registration_request_hash !== s.registration_request_hash
      || c.handover_id !== s.handover_id || c.handover_request_hash !== s.handover_request_hash || c.custody_assignment_id !== s.custody_assignment_id
      || micros(c.received_at) < micros(s.handed_over_at) || (units(a.accepted_qty) && c.observed_sku_code !== s.sku_code)
      || units(a.accepted_qty) + units(a.rejected_qty) + units(a.shortage_qty) > units(current.unconfirmed_qty)) fail('验收内容与当前未确认实物不一致');
  const selected = [...a.accepted_serial_verifications.map(sn => sn.serial_id), ...a.rejected_serial_ids, ...a.shortage_serial_ids];
  if (selected.some(sid => !current.unconfirmed_serials.some(sn => sn.serial_id === sid))) fail('SN 已确认，请刷新');
}
export type Command = { kind: 'receipt'; input: ReceiptInput } | { kind: 'inbound'; receipt_id: string; input: InboundInput };
export function command(value: unknown): Command {
  if (value && typeof value === 'object' && 'kind' in value && value.kind === 'receipt') {
    const r = object(value, ['kind', 'input']); return { kind: 'receipt', input: receiptInput(r.input) };
  }
  const r = object(value, ['kind', 'receipt_id', 'input']); if (r.kind !== 'inbound') fail();
  return { kind: 'inbound', receipt_id: id(r.receipt_id), input: inboundInput(r.input) };
}
export const fingerprint = (value: Command) => hash(command(value).input);
