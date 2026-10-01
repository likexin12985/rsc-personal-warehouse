// Client contract for the independent 0106 return-inbound posting boundary.
// This is intentionally separate from parcel acceptance and forward-demand
// inbound: it only moves quantities already accepted into the receiving
// region account.
const { uuid } = require('./work-order-query-contract')
const { canonical, utf8 } = require('./work-order-command')
const { sha256Hex } = require('./formal-file-upload')
const { amount, requestId, digest, integer } = require('./stock-return-contract')

const provenance = require('./loss-return-origin')
const KIND = 'stock_return_inbound'
const ACTION = 'receive_return'
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }
function fail(message = '退回入账结果与原请求不一致，请保留恢复记录。') { throw new Error(message) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(fields.slice().sort())) fail()
  return value
}
function hash(value) { digest(value); return value }
function text(value, max = 500) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim() || value.length > max
    || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) fail()
  return value
}
function instant(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) fail()
  return value
}
function requestHash(receiptId, planHash, traceRequestId) {
  const value = { receipt_id: uuid(receiptId), request_id: requestId(traceRequestId), plan_hash: hash(planHash) }
  return sha256Hex(utf8(canonical(value)))
}
function validateLine(raw, loss = false) {
  exact(raw, ['receipt_line_id', 'shipment_line_id', 'source_account_id', 'target_account_id', 'material_id', 'condition_code', 'lot_id', 'accepted_qty', 'serial_ids'])
  uuid(raw.receipt_line_id); uuid(raw.shipment_line_id); uuid(raw.source_account_id); uuid(raw.target_account_id); uuid(raw.material_id)
  if (raw.lot_id !== null) uuid(raw.lot_id)
  if (amount(raw.accepted_qty) <= 0n) fail()
  if (!(loss ? ['new', 'used', 'damaged'] : ['used', 'damaged']).includes(raw.condition_code)) fail()
  if (!Array.isArray(raw.serial_ids) || raw.serial_ids.length > 1000) fail()
  const serials = raw.serial_ids.map(uuid).sort()
  if (new Set(serials).size !== serials.length) fail()
  return { ...raw, receipt_line_id: uuid(raw.receipt_line_id), shipment_line_id: uuid(raw.shipment_line_id),
    source_account_id: uuid(raw.source_account_id), target_account_id: uuid(raw.target_account_id), material_id: uuid(raw.material_id),
    lot_id: raw.lot_id === null ? null : uuid(raw.lot_id), serial_ids: serials }
}
function validatePreview(raw, expected) {
  exact(raw, ['schema_version', 'planning_status', 'receipt_id', 'shipment_id', 'operator_person_id', 'authorization_version',
    'target_location_id', 'target_custody_assignment_id', 'receipt_plan_hash', 'plan_hash', 'reason', 'checked_at', 'ledger_cursor', 'lines', ...(provenance.isLoss(raw) ? ['origin'] : [])])
  if (!['1.0', '2.0'].includes(raw.schema_version) || raw.planning_status !== 'inbound_preview_only'
    || uuid(raw.receipt_id) !== uuid(expected.receiptId) || uuid(raw.shipment_id) !== uuid(expected.shipmentId)
    || uuid(raw.operator_person_id) !== uuid(expected.personId)
    || raw.authorization_version !== integer(expected.authorizationVersion, 1)) fail()
  integer(raw.authorization_version, 1); uuid(raw.target_location_id); uuid(raw.target_custody_assignment_id)
  hash(raw.receipt_plan_hash); hash(raw.plan_hash); instant(raw.checked_at)
  if (!Number.isSafeInteger(raw.ledger_cursor) || raw.ledger_cursor < 0) fail()
  text(raw.reason, 500)
  if (!Array.isArray(raw.lines) || !raw.lines.length || raw.lines.length > 200) fail()
  if (provenance.isLoss(raw)) provenance.origin(raw.origin)
  const lines = raw.lines.map(line => validateLine(line, provenance.isLoss(raw)))
  const keys = lines.map(row => raw.schema_version === '1.0' ? row.receipt_line_id : row.receipt_line_id + ':' + row.condition_code)
  if (new Set(keys).size !== lines.length || new Set(lines.map(row => row.receipt_line_id)).size > 100) fail()
  const serials = lines.flatMap(row => row.serial_ids)
  if (new Set(serials).size !== serials.length) fail()
  const origins = new Map(), targets = new Map()
  for (const row of lines) {
    if (row.source_account_id === row.target_account_id) fail()
    const origin = canonical([row.shipment_line_id, row.source_account_id, row.material_id, row.lot_id])
    const target = canonical([row.material_id, row.lot_id, row.condition_code])
    if ((origins.has(row.receipt_line_id) && origins.get(row.receipt_line_id) !== origin)
      || (targets.has(row.target_account_id) && targets.get(row.target_account_id) !== target)) fail()
    origins.set(row.receipt_line_id, origin); targets.set(row.target_account_id, target)
  }
  // Bind the displayed partitions to the separately verified immutable receipt.
  // Version two is never accepted from identifiers alone.
  const receipt = expected.receipt
  if (raw.schema_version === '2.0' && !receipt) fail('请重新读取原验收，再核验破损入库份额。')
  if (receipt) {
    if (receipt.receipt_id !== raw.receipt_id || receipt.shipment_id !== raw.shipment_id
      || receipt.plan_hash !== raw.receipt_plan_hash || receipt.target_location_id !== raw.target_location_id
      || receipt.target_custody_assignment_id !== raw.target_custody_assignment_id
      || Date.parse(raw.checked_at) < Date.parse(receipt.recorded_at)
      || provenance.isLoss(raw) !== provenance.isLoss(receipt)
      || provenance.isLoss(raw) && !provenance.sameSource(raw, receipt)) fail()
    for (const row of lines) {
      const original = receipt.lines.find(line => line.shipment_line_id === row.shipment_line_id)
      if (!original || row.material_id !== original.material_id || row.lot_id !== original.lot_id
        || ![original.condition_code, 'damaged'].includes(row.condition_code)) fail()
      if (original.accepted_serials.length ? amount(row.accepted_qty) !== BigInt(row.serial_ids.length) * 1000n : row.serial_ids.length) fail()
    }
    for (const original of receipt.lines) {
      const parts = lines.filter(row => row.shipment_line_id === original.shipment_line_id)
      if (parts.length > 2 || new Set(parts.map(row => row.receipt_line_id)).size > 1
        || new Set(parts.map(row => row.source_account_id)).size > 1
        || new Set(parts.map(row => row.condition_code)).size !== parts.length
        || parts.reduce((sum, row) => sum + amount(row.accepted_qty), 0n) !== amount(original.accepted_qty)) fail()
      const damaged = original.condition_code === 'damaged' ? amount(original.accepted_qty) : amount(original.damaged_qty)
      if (parts.filter(row => row.condition_code === 'damaged').reduce((sum, row) => sum + amount(row.accepted_qty), 0n) !== damaged) fail()
      for (const part of parts) {
        const wanted = original.accepted_serials.filter(sn => original.condition_code === 'damaged'
          || (part.condition_code === 'damaged') === original.damaged_serial_ids.includes(sn.serial_id)).map(sn => sn.serial_id).sort()
        if (canonical(part.serial_ids) !== canonical(wanted)) fail()
      }
      if (canonical(parts.flatMap(row => row.serial_ids).sort()) !== canonical(original.accepted_serials.map(sn => sn.serial_id).sort())) fail()
    }
  }
  return Object.freeze({ ...raw, receipt_id: uuid(raw.receipt_id), shipment_id: uuid(raw.shipment_id),
    operator_person_id: uuid(raw.operator_person_id), target_location_id: uuid(raw.target_location_id),
    target_custody_assignment_id: uuid(raw.target_custody_assignment_id), lines })
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'inbound_id', 'inbound_no', 'receipt_id', 'shipment_id', 'target_location_id',
    'target_custody_assignment_id', 'status', 'posting_transaction_id', 'request_id', 'request_hash', 'plan_hash', 'replayed'])
  if (raw.schema_version !== '1.0' || raw.status !== 'posted' || uuid(raw.receipt_id) !== marker.receipt_id
    || uuid(raw.shipment_id) !== marker.shipment_id || raw.request_id !== marker.trace_request_id
    || raw.request_hash !== marker.request_hash || raw.plan_hash !== marker.plan_hash || typeof raw.replayed !== 'boolean') fail()
  uuid(raw.inbound_id); uuid(raw.target_location_id); uuid(raw.target_custody_assignment_id); uuid(raw.posting_transaction_id)
  text(raw.inbound_no, 100); requestId(raw.request_id); hash(raw.request_hash); hash(raw.plan_hash)
  return raw
}
function validateLookup(raw, marker) {
  if (raw && raw.lookup_status === 'sealed') {
    exact(raw, ['schema_version', 'lookup_status', 'seal'])
    exact(raw.seal, ['seal_id', 'receipt_id', 'shipment_id', 'request_id', 'request_hash', 'sealed_at'])
    if (raw.schema_version !== '1.0' || uuid(raw.seal.receipt_id) !== marker.receipt_id
      || uuid(raw.seal.shipment_id) !== marker.shipment_id || raw.seal.request_id !== marker.trace_request_id
      || raw.seal.request_hash !== marker.request_hash) fail()
    uuid(raw.seal.seal_id); requestId(raw.seal.request_id); hash(raw.seal.request_hash); instant(raw.seal.sealed_at)
    return raw
  }
  return validateResult(raw, marker)
}
function validateState(raw, expected) {
  exact(raw, ['schema_version', 'receipt_id', 'shipment_id', 'operator_person_id', 'authorization_version',
    'status', 'inbound', 'ledger_cursor', 'checked_at'])
  if (raw.schema_version !== '1.0' || uuid(raw.receipt_id) !== uuid(expected.receiptId)
    || uuid(raw.shipment_id) !== uuid(expected.shipmentId) || uuid(raw.operator_person_id) !== uuid(expected.personId)
    || raw.authorization_version !== integer(expected.authorizationVersion, 1) || !['not_posted', 'posted'].includes(raw.status)) fail()
  integer(raw.ledger_cursor); instant(raw.checked_at)
  if (raw.status === 'not_posted') { if (raw.inbound !== null) fail() }
  else {
    exact(raw.inbound, ['inbound_id', 'inbound_no', 'target_location_id', 'posting_transaction_id', 'posted_at'])
    uuid(raw.inbound.inbound_id); text(raw.inbound.inbound_no, 100); uuid(raw.inbound.target_location_id)
    uuid(raw.inbound.posting_transaction_id); instant(raw.inbound.posted_at)
    if (Date.parse(raw.inbound.posted_at) > Date.parse(raw.checked_at)) fail()
  }
  return raw
}
module.exports = { KIND, ACTION, READ, requestHash, validatePreview, validateResult, validateLookup, validateState }
