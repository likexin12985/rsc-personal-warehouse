// Receiver-only parcel and acceptance projections. Never infer stock posting.
const { uuid } = require('./work-order-query-contract')
const { exact, text } = require('./work-order-replacement-command')
const { amount, list, integer, digest, requestId, reason } = require('./stock-return-contract')
const { instant } = require('./stock-return-outbound-contract')
const { canonical, utf8 } = require('./work-order-command')
const BASE = ['shipment_line_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no']
const TYPES = ['shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected']
function fail() { throw new Error('退回包裹或累计验收记录未通过核验，请刷新原记录。') }
function earlier(a, b) { return instant(a) < instant(b) }
function unique(rows, key) { const values = rows.map(row => row[key]); if (new Set(values).size !== values.length) fail() }
function serials(rows) {
  for (const sn of list(rows)) { exact(sn, ['serial_id', 'serial_no']); uuid(sn.serial_id); text(sn.serial_no, 200) }
  unique(rows, 'serial_id'); return rows
}
function metadata(row) {
  uuid(row.shipment_line_id); uuid(row.material_id)
  text(row.sku_code, 80); text(row.material_name, 1000); text(row.base_unit, 100)
  if (!['used', 'damaged'].includes(row.condition_code)) fail()
  if (row.lot_id === null) { if (row.lot_no !== null) fail() } else { uuid(row.lot_id); text(row.lot_no, 160) }
}
function identity(raw, expected) {
  if (raw.schema_version !== '1.0' || uuid(raw.person_id) !== uuid(expected.personId)
    || raw.authorization_version !== integer(expected.authorizationVersion, 1)) fail()
  integer(raw.ledger_cursor); instant(raw.queried_at)
}
function parcel(raw, expected) {
  exact(raw, ['verification_status', 'shipment_id', 'shipment_no', 'operation_id', 'operation_no', 'work_order_id',
    'sender_person_id', 'receiver_person_id', 'target_location_id', 'target_location_name', 'custody_assignment_id',
    'carrier', 'tracking_no', 'shipped_at', 'recorded_at', 'lines'])
  if (raw.verification_status !== 'verified' || uuid(raw.receiver_person_id) !== uuid(expected.personId)
    || expected.shipmentId && raw.shipment_id !== uuid(expected.shipmentId)) fail()
  for (const key of ['shipment_id', 'operation_id', 'work_order_id', 'sender_person_id', 'target_location_id', 'custody_assignment_id']) uuid(raw[key])
  for (const key of ['shipment_no', 'operation_no', 'carrier', 'tracking_no']) text(raw[key], 100)
  text(raw.target_location_name, 300)
  if (earlier(raw.recorded_at, raw.shipped_at)) fail()
  for (const row of list(raw.lines, 1, 100)) {
    exact(row, BASE.concat('outbound_no', 'shipped_quantity', 'serials')); metadata(row); text(row.outbound_no, 100)
    const qty = amount(row.shipped_quantity, 1n); serials(row.serials)
    if (row.serials.length && BigInt(row.serials.length) * 1000n !== qty) fail()
  }
  unique(raw.lines, 'shipment_line_id'); unique(raw.lines.flatMap(row => row.serials), 'serial_id')
  return raw
}
function validateDirectory(raw, expected, afterId = null) {
  exact(raw, ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'items', 'next_after_id'])
  identity(raw, expected); let previous = afterId === null ? null : uuid(afterId)
  for (const row of list(raw.items, 0, 10)) {
    const id = uuid(row.shipment_id)
    if (previous !== null && id <= previous) fail(); previous = id
    if (row.verification_status === 'unavailable') {
      exact(row, ['verification_status', 'shipment_id', 'code', 'message'])
      if (row.code !== 'stock_return_receiving_verification_required') fail(); text(row.message, 500)
    } else { parcel(row, expected); if (earlier(raw.queried_at, row.recorded_at)) fail() }
  }
  if (raw.next_after_id !== null && (uuid(raw.next_after_id) !== previous || raw.items.length !== 10)) fail()
  return raw
}
function receipt(raw, expected, original) {
  exact(raw, ['schema_version', 'receipt_id', 'receipt_no', 'shipment_id', 'operation_id', 'work_order_id', 'operator_person_id', 'status',
    'received_at', 'recorded_at', 'reason', 'request_id', 'request_hash', 'plan_hash', 'target_location_id', 'target_custody_assignment_id', 'lines'])
  if (raw.schema_version !== '1.0' || uuid(raw.operator_person_id) !== uuid(expected.personId)
    || uuid(raw.shipment_id) !== uuid(expected.shipmentId) || raw.reason !== reason(raw.reason)) fail()
  uuid(raw.receipt_id); text(raw.receipt_no, 100); requestId(raw.request_id); digest(raw.request_hash); digest(raw.plan_hash)
  for (const key of ['operation_id', 'work_order_id', 'target_location_id', 'target_custody_assignment_id']) uuid(raw[key])
  if (earlier(raw.recorded_at, raw.received_at)) fail()
  if (original && (['shipment_id', 'operation_id', 'work_order_id', 'target_location_id'].some(key => raw[key] !== original[key])
    || raw.target_custody_assignment_id !== original.custody_assignment_id || earlier(raw.received_at, original.shipped_at)
    || earlier(raw.recorded_at, original.recorded_at))) fail()
  let abnormal = false
  for (const row of list(raw.lines, 1, 100)) {
    exact(row, BASE.concat('shipped_qty', 'previously_accepted_qty', 'previously_rejected_qty', 'unconfirmed_qty',
      'accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty', 'accepted_serials', 'damaged_serial_ids', 'rejected_serials', 'shortage_serials', 'exceptions'))
    metadata(row)
    const total = amount(row.shipped_qty, 1n), before = amount(row.previously_accepted_qty) + amount(row.previously_rejected_qty)
    const accepted = amount(row.accepted_qty), rejected = amount(row.rejected_qty), damaged = amount(row.damaged_qty), shortage = amount(row.shortage_qty)
    if (before > total || amount(row.unconfirmed_qty) !== total - before || accepted + rejected + shortage <= 0n
      || accepted + rejected + shortage > total - before || damaged > accepted) fail()
    const types = list(row.exceptions, 0, 5).map(value => {
      exact(value, ['exception_type', 'description', 'evidence_file_id'])
      if (!TYPES.includes(value.exception_type) || typeof value.description !== 'string' || !value.description.trim()
        || [...value.description].length > 1000) fail()
      utf8(value.description); uuid(value.evidence_file_id); return value.exception_type
    })
    if (new Set(types).size !== types.length || Boolean(shortage) !== types.includes('shortage') || Boolean(damaged) !== types.includes('damaged')
      || Boolean(rejected) !== types.some(value => ['rejected', 'wrong_material', 'wrong_serial'].includes(value))) fail()
    abnormal ||= types.length > 0
    const groups = [serials(row.accepted_serials), serials(row.rejected_serials), serials(row.shortage_serials)]
    const all = groups.flat(); unique(all, 'serial_id')
    const damagedIds = list(row.damaged_serial_ids).map(uuid)
    if (new Set(damagedIds).size !== damagedIds.length || damagedIds.some(id => !row.accepted_serials.some(sn => sn.serial_id === id))) fail()
    const source = original && original.lines.find(line => line.shipment_line_id === row.shipment_line_id)
    if (original && (!source || BASE.some(key => row[key] !== source[key]) || row.shipped_qty !== source.shipped_quantity
      || all.some(sn => !source.serials.some(old => canonical(old) === canonical(sn))))) fail()
    const tracked = source ? source.serials.length > 0 : all.length > 0
    if (tracked && (groups.some((group, i) => BigInt(group.length) * 1000n !== [accepted, rejected, shortage][i])
      || BigInt(damagedIds.length) * 1000n !== damaged) || !tracked && (all.length || damagedIds.length)) fail()
  }
  unique(raw.lines, 'shipment_line_id')
  unique(raw.lines.flatMap(row => [...row.accepted_serials, ...row.rejected_serials, ...row.shortage_serials]), 'serial_id')
  if (raw.status !== (abnormal ? 'exception' : 'accepted')) fail()
  return raw
}
function validateHistory(raw, expected) {
  exact(raw, ['schema_version', 'person_id', 'authorization_version', 'ledger_cursor', 'queried_at', 'package', 'receipts', 'lines'])
  identity(raw, expected); parcel(raw.package, expected)
  if (earlier(raw.queried_at, raw.package.recorded_at)) fail()
  const totals = new Map(raw.package.lines.map(row => [row.shipment_line_id, { accepted: 0n, rejected: 0n, damaged: 0n, seen: new Set() }]))
  let previous = raw.package.recorded_at
  for (const item of list(raw.receipts, 0, 1000)) {
    receipt(item, expected, raw.package)
    if (earlier(item.recorded_at, previous) || earlier(raw.queried_at, item.recorded_at)) fail(); previous = item.recorded_at
    for (const row of item.lines) {
      const sum = totals.get(row.shipment_line_id)
      if (amount(row.previously_accepted_qty) !== sum.accepted || amount(row.previously_rejected_qty) !== sum.rejected) fail()
      sum.accepted += amount(row.accepted_qty); sum.rejected += amount(row.rejected_qty); sum.damaged += amount(row.damaged_qty)
      for (const sn of [...row.accepted_serials, ...row.rejected_serials, ...row.shortage_serials]) if (sum.seen.has(sn.serial_id)) fail()
      // Shortage is an observation, so a later receipt may accept that same SN.
      for (const sn of [...row.accepted_serials, ...row.rejected_serials]) sum.seen.add(sn.serial_id)
    }
  }
  unique(raw.receipts, 'receipt_id'); unique(raw.receipts, 'request_id')
  list(raw.lines, 1, 100); unique(raw.lines, 'shipment_line_id')
  if (raw.lines.length !== totals.size) fail()
  for (const row of raw.lines) {
    exact(row, ['shipment_line_id', 'shipped_qty', 'accepted_qty', 'rejected_qty', 'damaged_qty', 'unconfirmed_qty', 'unconfirmed_serials'])
    const source = raw.package.lines.find(line => line.shipment_line_id === uuid(row.shipment_line_id)), sum = totals.get(row.shipment_line_id)
    if (!source || row.shipped_qty !== source.shipped_quantity || amount(row.accepted_qty) !== sum.accepted
      || amount(row.rejected_qty) !== sum.rejected || amount(row.damaged_qty) !== sum.damaged
      || amount(row.unconfirmed_qty) !== amount(row.shipped_qty) - sum.accepted - sum.rejected) fail()
    serials(row.unconfirmed_serials)
    const remaining = source.serials.filter(sn => !sum.seen.has(sn.serial_id))
    if (canonical(row.unconfirmed_serials) !== canonical(remaining)) fail()
  }
  return raw
}
module.exports = { parcel, receipt, validateDirectory, validateHistory }
