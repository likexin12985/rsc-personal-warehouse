/** Loss sender read contracts; physical departure and carrier handover are distinct. */
import { canonical, digest, fail, id, integer, list, micros, object, quantity, serials, text, timestamp, unique, units,
  type Identity } from './formalReturnReceiving';
const ORIGIN_KEYS = ['origin_kind', 'operation_id', 'requester_id', 'submitted_by_user_id', 'request_hash', 'plan_hash', 'posting_transaction_id', 'submitted_at', 'loss_operation_id', 'loss_line_id', 'headquarters_decision_id', 'disposition_id'];
export function origin(value: unknown, identity: Identity, operationId?: string) {
  const r = object(value, ORIGIN_KEYS);
  if (r.origin_kind !== 'loss_report' || id(r.requester_id) !== id(identity.person_id) || (operationId !== undefined && r.operation_id !== id(operationId))) fail('报损退回来源或本人身份不一致');
  const operation_id = id(r.operation_id), loss_operation_id = id(r.loss_operation_id);
  if (operation_id === loss_operation_id) fail('报损单与派生退回单不能混用');
  return { origin_kind: 'loss_report' as const, operation_id, requester_id: id(r.requester_id),
    submitted_by_user_id: text(r.submitted_by_user_id, 36), request_hash: digest(r.request_hash), plan_hash: digest(r.plan_hash),
    posting_transaction_id: id(r.posting_transaction_id), submitted_at: timestamp(r.submitted_at), loss_operation_id,
    loss_line_id: id(r.loss_line_id), headquarters_decision_id: id(r.headquarters_decision_id), disposition_id: id(r.disposition_id) };
}
const DESTINATION_KEYS = ['source_location_id', 'target_location_id', 'target_location_code', 'target_location_name',
  'transit_location_id', 'transit_location_code', 'transit_location_name', 'region_org_id', 'custody_assignment_id',
  'custodian_person_id', 'custody_effective_from'];
export function destination(value: unknown) {
  const r = object(value, DESTINATION_KEYS);
  const source_location_id = id(r.source_location_id), target_location_id = id(r.target_location_id), transit_location_id = id(r.transit_location_id);
  unique([source_location_id, target_location_id, transit_location_id]);
  return { source_location_id, target_location_id, transit_location_id, target_location_code: text(r.target_location_code, 100),
    target_location_name: text(r.target_location_name, 300), transit_location_code: text(r.transit_location_code, 100),
    transit_location_name: text(r.transit_location_name, 300), region_org_id: id(r.region_org_id),
    custody_assignment_id: id(r.custody_assignment_id), custodian_person_id: id(r.custodian_person_id),
    custody_effective_from: timestamp(r.custody_effective_from) };
}
const BASIS_KEYS = ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at'];
function basis(r: Record<string, unknown>, identity: Identity) {
  if (r.schema_version !== '1.0' || id(r.person_id) !== id(identity.person_id)
      || integer(r.authorization_version, 1) !== integer(identity.authorization_version, 1)) fail('当前发件身份或授权版本已变化');
  return { schema_version: '1.0' as const, person_id: id(r.person_id), authorization_version: integer(r.authorization_version, 1),
    ledger_cursor: integer(r.ledger_cursor), queried_at: timestamp(r.queried_at) };
}
function item(r: Record<string, unknown>, identity: Identity, operationId?: string) {
  return { operation_no: text(r.operation_no, 100), origin: origin(r.origin, identity, operationId),
    reason: text(r.reason, 500), destination: destination(r.destination) };
}
function chronological(later: string, earlier: string) { if (micros(later) < micros(earlier)) fail('报损退回时间顺序不一致'); }
export function directory(value: unknown, identity: Identity, limit = 20, after: string | null = null, snapshotHash?: string) {
  integer(limit, 1, 50);
  const r = object(value, [...BASIS_KEYS, 'snapshot_hash', 'items', 'next_after_id']), base = basis(r, identity);
  const snapshot_hash = digest(r.snapshot_hash);
  if (snapshotHash !== undefined && snapshot_hash !== digest(snapshotHash)) fail('目录快照已变化，请重新读取');
  let previous = after === null ? null : id(after);
  if (after !== null && snapshotHash === undefined) fail('继续分页必须保留原目录快照');
  const items = list(r.items, value => {
    const row = item(object(value, ['operation_no', 'origin', 'reason', 'destination']), identity);
    if (previous !== null && row.origin.operation_id <= previous) fail('退回目录存在重复或倒序');
    previous = row.origin.operation_id;
    chronological(base.queried_at, row.origin.submitted_at);
    return row;
  }, limit);
  const next_after_id = r.next_after_id === null ? null : id(r.next_after_id);
  if (next_after_id !== null && (items.length !== limit || next_after_id !== previous)) fail('退回目录分页游标不一致');
  return { ...base, snapshot_hash, items, next_after_id };
}
const META_KEYS = ['operation_line_id', 'source_loss_line_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no'];
function metadata(r: Record<string, unknown>, source: ReturnType<typeof origin>) {
  if (id(r.source_loss_line_id) !== source.loss_line_id || !['new','used','damaged'].includes(String(r.condition_code))
      || (r.lot_id === null) !== (r.lot_no === null)) fail('原报损物料来源不一致');
  return { operation_line_id: id(r.operation_line_id), source_loss_line_id: id(r.source_loss_line_id),
    material_id: id(r.material_id), sku_code: text(r.sku_code, 80), material_name: text(r.material_name), base_unit: text(r.base_unit, 100),
    condition_code: r.condition_code as 'new'|'used'|'damaged', lot_id: r.lot_id === null ? null : id(r.lot_id),
    lot_no: r.lot_no === null ? null : text(r.lot_no,160) };
}
export function detail(value: unknown, identity: Identity, operationId: string) {
  const r = object(value, [...BASIS_KEYS, 'operation_no', 'origin', 'reason', 'destination', 'loss_operation_no', 'loss_submitted_at', 'line']);
  const base = basis(r, identity), original = item(r, identity, operationId);
  const line = object(r.line, [...META_KEYS, 'return_quantity', 'selected_serials']);
  const selected_serials = serials(line.selected_serials), return_quantity = quantity(line.return_quantity);
  if (units(return_quantity) <= 0n || (selected_serials.length > 0 && BigInt(selected_serials.length)*1000n !== units(return_quantity))) fail('原退回数量与 SN 不一致');
  const loss_submitted_at = timestamp(r.loss_submitted_at);
  chronological(original.origin.submitted_at, loss_submitted_at); chronological(base.queried_at, original.origin.submitted_at);
  return { ...base, ...original, loss_operation_no: text(r.loss_operation_no,100), loss_submitted_at,
    line: { ...metadata(line, original.origin), return_quantity, selected_serials } };
}
export type Detail = ReturnType<typeof detail>;
const POLICY_KEYS = ['tracking_mode', 'quantity_scale', 'allow_fraction', 'serials'];
function policy(r: Record<string, unknown>, remaining: bigint) {
  if (!['none','lot','serial','lot_and_serial'].includes(String(r.tracking_mode)) || typeof r.allow_fraction !== 'boolean') fail('当前物料策略不完整');
  const tracking_mode = r.tracking_mode as 'none'|'lot'|'serial'|'lot_and_serial', quantity_scale = integer(r.quantity_scale,0,3);
  const sn = serials(r.serials), tracked = tracking_mode === 'serial' || tracking_mode === 'lot_and_serial';
  if (tracked ? BigInt(sn.length)*1000n !== remaining : sn.length > 0) fail('剩余 SN 与数量不一致');
  if (['lot','lot_and_serial'].includes(tracking_mode) && r.lot_id === null) fail('批次策略缺少原批次');
  return { tracking_mode, quantity_scale, allow_fraction: r.allow_fraction, serials: sn,
    step: 10n ** BigInt(3-(r.allow_fraction && !tracked ? quantity_scale : 0)) };
}
function optionsBasis(value: unknown, identity: Identity, original: Detail) {
  const r = object(value, [...BASIS_KEYS, 'operation_id', 'operation_no', 'destination', 'origin', 'lines']);
  const base = basis(r,identity), proof = origin(r.origin,identity,original.origin.operation_id), route = destination(r.destination);
  if (r.operation_id !== original.origin.operation_id || r.operation_no !== original.operation_no
      || canonical(proof) !== canonical(original.origin)) fail('可选明细未绑定原退回单');
  for (const key of ['source_location_id','target_location_id','transit_location_id','region_org_id'] as const) {
    if (route[key] !== original.destination[key]) fail('退回运输路径已变化');
  }
  chronological(base.queried_at, proof.submitted_at); chronological(base.queried_at, route.custody_effective_from);
  return { r, header: { ...base, operation_id: id(r.operation_id), operation_no: text(r.operation_no,100), origin: proof, destination: route } };
}
function originalSerials(selected: ReturnType<typeof serials>, original: Detail) {
  if (selected.some(sn => !original.line.selected_serials.some(old => canonical(old) === canonical(sn)))) fail('剩余 SN 不属于原报损退回明细');
}
function selectedBudget(selectable: unknown, remaining: bigint, held: bigint, step: bigint) {
  const value = quantity(selectable), limit = remaining < held ? remaining : held;
  if (units(value) !== limit / step * step) fail('可选数量与剩余、实际库存或数量精度不一致');
  return value;
}
function originalLine(meta: ReturnType<typeof metadata>, original: Detail) {
  for (const key of ['operation_line_id','source_loss_line_id','material_id','condition_code','lot_id'] as const) {
    if (meta[key] !== original.line[key]) fail('可选行替换了原退回物料');
  }
}
export function outboundOptions(value: unknown, identity: Identity, original: Detail) {
  const { r,header } = optionsBasis(value,identity,original);
  const lines = list(r.lines,value => {
    const row = object(value,[...META_KEYS,...POLICY_KEYS,'source_stock_account_id','return_quantity','departed_quantity','remaining_quantity','held_quantity','selectable_quantity']);
    const meta = metadata(row,header.origin); originalLine(meta,original);
    const remaining = units(row.remaining_quantity), held = units(row.held_quantity), p = policy(row,remaining);
    if (row.return_quantity !== original.line.return_quantity || units(row.return_quantity)-units(row.departed_quantity) !== remaining) fail('出库数量不守恒');
    originalSerials(p.serials,original);
    const { step, ...view } = p;
    return { ...meta,...view,source_stock_account_id:id(row.source_stock_account_id),return_quantity:quantity(row.return_quantity),
      departed_quantity:quantity(row.departed_quantity),remaining_quantity:quantity(row.remaining_quantity),held_quantity:quantity(row.held_quantity),
      selectable_quantity:selectedBudget(row.selectable_quantity,remaining,held,step) };
  },100,1);
  unique(lines.map(r=>r.operation_line_id)); unique(lines.flatMap(r=>r.serials.map(s=>s.serial_id)));
  return {...header,lines};
}
export function shipmentOptions(value: unknown, identity: Identity, original: Detail) {
  const {r,header}=optionsBasis(value,identity,original);
  const lines=list(r.lines,value=>{
    const row=object(value,[...META_KEYS,...POLICY_KEYS,'outbound_id','outbound_no','outbound_at','outbound_line_id','transit_stock_account_id',
      'outbound_quantity','shipped_quantity','unshipped_quantity','in_transit_quantity','unassigned_quantity','selectable_quantity']);
    const meta=metadata(row,header.origin);originalLine(meta,original);
    const remaining=units(row.unshipped_quantity), held=units(row.in_transit_quantity),unassigned=units(row.unassigned_quantity),p=policy(row,remaining);
    if (units(row.outbound_quantity)<=0n || units(row.outbound_quantity)-units(row.shipped_quantity)!==remaining || unassigned>held) fail('发运数量不守恒');
    const outbound_at=timestamp(row.outbound_at);chronological(outbound_at,original.origin.submitted_at);chronological(header.queried_at,outbound_at);
    originalSerials(p.serials,original);
    if (['lot','lot_and_serial'].includes(p.tracking_mode)!==(meta.lot_id!==null)) fail('发运批次与当前策略不一致');
    const {step,...view}=p;
    return {...meta,...view,outbound_id:id(row.outbound_id),outbound_no:text(row.outbound_no,100),outbound_at,
      outbound_line_id:id(row.outbound_line_id),transit_stock_account_id:id(row.transit_stock_account_id),outbound_quantity:quantity(row.outbound_quantity),
      shipped_quantity:quantity(row.shipped_quantity),unshipped_quantity:quantity(row.unshipped_quantity),in_transit_quantity:quantity(row.in_transit_quantity),
      unassigned_quantity:quantity(row.unassigned_quantity),selectable_quantity:selectedBudget(row.selectable_quantity,remaining,unassigned,step)};
  },10000);
  unique(lines.map(r=>r.outbound_line_id));unique(lines.flatMap(r=>r.serials.map(s=>s.serial_id)));
  if(lines.reduce((sum,r)=>sum+units(r.outbound_quantity),0n)>units(original.line.return_quantity))fail('累计出库超过原退回数量');
  return {...header,lines};
}
