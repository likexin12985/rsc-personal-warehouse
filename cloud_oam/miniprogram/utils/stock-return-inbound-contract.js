// Client contract for the independent 0106 return-inbound posting boundary.
// This is intentionally separate from parcel acceptance and forward-demand
// inbound: it only moves quantities already accepted into the receiving
// region account.
const { uuid } = require('./work-order-query-contract')
const { canonical, utf8 } = require('./work-order-command')
const { sha256Hex } = require('./formal-file-upload')
const { amount, requestId, digest, integer } = require('./stock-return-contract')

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
function validateLine(raw) {
  exact(raw, ['receipt_line_id', 'shipment_line_id', 'source_account_id', 'target_account_id', 'material_id', 'condition_code', 'lot_id', 'accepted_qty', 'serial_ids'])
  uuid(raw.receipt_line_id); uuid(raw.shipment_line_id); uuid(raw.source_account_id); uuid(raw.target_account_id); uuid(raw.material_id)
  if (raw.lot_id !== null) uuid(raw.lot_id)
  amount(raw.accepted_qty)
  if (!['used', 'damaged'].includes(raw.condition_code)) fail()
  if (!Array.isArray(raw.serial_ids) || raw.serial_ids.length > 1000) fail()
  const serials = raw.serial_ids.map(uuid).sort()
  if (new Set(serials).size !== serials.length) fail()
  return { ...raw, receipt_line_id: uuid(raw.receipt_line_id), shipment_line_id: uuid(raw.shipment_line_id),
    source_account_id: uuid(raw.source_account_id), target_account_id: uuid(raw.target_account_id), material_id: uuid(raw.material_id),
    lot_id: raw.lot_id === null ? null : uuid(raw.lot_id), serial_ids: serials }
}
function validatePreview(raw, expected) {
  exact(raw, ['schema_version', 'planning_status', 'receipt_id', 'shipment_id', 'operator_person_id', 'authorization_version',
    'target_location_id', 'target_custody_assignment_id', 'receipt_plan_hash', 'plan_hash', 'reason', 'checked_at', 'ledger_cursor', 'lines'])
  if (raw.schema_version !== '1.0' || raw.planning_status !== 'inbound_preview_only'
    || uuid(raw.receipt_id) !== uuid(expected.receiptId) || uuid(raw.shipment_id) !== uuid(expected.shipmentId)
    || uuid(raw.operator_person_id) !== uuid(expected.personId)) fail()
  integer(raw.authorization_version, 1); uuid(raw.target_location_id); uuid(raw.target_custody_assignment_id)
  hash(raw.receipt_plan_hash); hash(raw.plan_hash); instant(raw.checked_at)
  if (!Number.isSafeInteger(raw.ledger_cursor) || raw.ledger_cursor < 0) fail()
  text(raw.reason, 500)
  if (!Array.isArray(raw.lines) || raw.lines.length > 100) fail()
  const lines = raw.lines.map(validateLine)
  if (new Set(lines.map(row => row.receipt_line_id)).size !== lines.length) fail()
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
module.exports = { KIND, ACTION, READ, requestHash, validatePreview, validateResult, validateLookup }
