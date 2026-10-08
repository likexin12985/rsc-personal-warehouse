const { uuid } = require('./my-receiving-contract')
const { canonical, quantity, units, time, CONDITIONS, LABELS: CONDITION_LABELS } = require('./my-receipt-command')
const { sha256Hex } = require('./formal-file-upload')
const LABELS = { pending: '待入账', posted: '已入账', no_accepted: '无合格数量', blocked: '待核验' }
function fail() { throw new Error('本人入账数据未通过核验，请保留原记录并刷新。') }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('|') !== fields.slice().sort().join('|')) fail()
}
function text(value) { if (typeof value !== 'string' || !value.trim() || value.length > 1000 || /[\u0000-\u001f\u007f]/.test(value)) fail(); return value }
function hash(value) { if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail(); return value }
function version(value) { if (!Number.isSafeInteger(value) || value < 1) fail(); return value }
function validateCandidates(raw, requestId, personId, afterId = null) {
  exact(raw, ['schema_version', 'request_id', 'request_no', 'request_version', 'person_id', 'can_post', 'items', 'next_after_id'])
  if (raw.schema_version !== '1.0' || uuid(raw.request_id) !== uuid(requestId) || uuid(raw.person_id) !== uuid(personId)
    || typeof raw.can_post !== 'boolean' || !Array.isArray(raw.items) || raw.items.length > 20) fail()
  version(raw.request_version); text(raw.request_no)
  let previous = afterId ? uuid(afterId) : ''
  const items = raw.items.map(row => {
    exact(row, ['receipt_id', 'status', 'message', 'detail'])
    const id = uuid(row.receipt_id)
    if (id <= previous || !Object.prototype.hasOwnProperty.call(LABELS, row.status)) fail()
    previous = id; text(row.message)
    if (row.status === 'blocked') { if (row.detail !== null) fail(); return { ...row, receipt_id: id, statusLabel: LABELS[row.status] } }
    const d = row.detail
    exact(d, ['receipt_no', 'receipt_request_hash', 'shipment_id', 'shipment_no', 'target_location_name', 'received_at', 'lines', 'inbound_no', 'inventory_transaction_id', 'posted_at'])
    for (const field of ['receipt_no', 'shipment_no', 'target_location_name']) text(d[field])
    hash(d.receipt_request_hash); uuid(d.shipment_id); time(d.received_at)
    if (!Array.isArray(d.lines) || !d.lines.length || d.lines.length > 100) fail()
    const lineIds = new Set(), serialIds = new Set()
    let acceptedTotal = 0n
    for (const line of d.lines) {
      exact(line, ['receipt_line_id', 'sku_code', 'material_name', 'base_unit', 'accepted_qty', 'rejected_qty', 'condition', 'tracking_mode', 'accepted_serials', 'rejected_serials'])
      const lineId = uuid(line.receipt_line_id)
      if (lineIds.has(lineId)) fail()
      lineIds.add(lineId)
      for (const field of ['sku_code', 'material_name', 'base_unit']) text(line[field])
      const accepted = units(line.accepted_qty), rejected = units(line.rejected_qty)
      if (quantity(line.accepted_qty) !== line.accepted_qty || quantity(line.rejected_qty) !== line.rejected_qty || accepted + rejected <= 0n) fail()
      acceptedTotal += accepted
      if (!CONDITIONS.includes(line.condition) || !['none', 'lot', 'serial', 'lot_and_serial'].includes(line.tracking_mode)) fail()
      if (line.condition === 'normal' && rejected !== 0n) fail()
      if (['damaged', 'wrong_material', 'wrong_serial', 'rejected'].includes(line.condition) && accepted !== 0n) fail()
      const tracked = ['serial', 'lot_and_serial'].includes(line.tracking_mode)
      for (const [group, qty] of [[line.accepted_serials, accepted], [line.rejected_serials, rejected]]) {
        if (!Array.isArray(group) || group.length > 1000 || (tracked ? BigInt(group.length) * 1000n !== qty : group.length !== 0)) fail()
        for (const serial of group) {
          exact(serial, ['serial_id', 'serial_no'])
          const sid = uuid(serial.serial_id); text(serial.serial_no)
          if (serialIds.has(sid)) fail()
          serialIds.add(sid)
        }
      }
    }
    if ((row.status === 'no_accepted') !== (acceptedTotal === 0n)) fail()
    if (row.status === 'posted') { uuid(d.inventory_transaction_id); time(d.posted_at); text(d.inbound_no) }
    else if (d.inventory_transaction_id !== null || d.posted_at !== null || d.inbound_no !== null) fail()
    return { ...row, receipt_id: id, statusLabel: LABELS[row.status] }
  })
  const next = raw.next_after_id === null ? null : uuid(raw.next_after_id)
  if (next && (!items.length || next !== previous)) fail()
  return { requestId: uuid(raw.request_id), requestNo: raw.request_no, requestVersion: raw.request_version,
    personId: uuid(raw.person_id), canPost: raw.can_post, items, nextAfterId: next }
}
function payload(candidate, row) {
  if (!candidate.canPost || row.status !== 'pending' || !row.detail) fail()
  return { expected_request_version: version(candidate.requestVersion), receipt_id: uuid(row.receipt_id), receipt_request_hash: hash(row.detail.receipt_request_hash) }
}
function requestHash(requestId, personId, body) {
  exact(body, ['expected_request_version', 'receipt_id', 'receipt_request_hash'])
  version(body.expected_request_version); uuid(body.receipt_id); hash(body.receipt_request_hash)
  const value = canonical({ request_id: uuid(requestId), person_id: uuid(personId), command: body })
  return sha256Hex(Uint8Array.from(value, c => c.charCodeAt(0)))
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'request_id', 'request_version', 'current_request_version', 'person_id', 'receipt_id', 'receipt_request_hash',
    'shipment_id', 'inbound_order_id', 'inbound_no', 'inventory_transaction_id', 'posted_at', 'request_hash', 'idempotency_replayed'])
  if (raw.schema_version !== '1.0' || uuid(raw.request_id) !== marker.request_id || uuid(raw.person_id) !== marker.person_id
    || uuid(raw.receipt_id) !== marker.receipt_id || raw.receipt_request_hash !== marker.receipt_request_hash
    || raw.request_hash !== marker.request_hash || raw.request_version !== marker.expected_request_version + 1
    || version(raw.current_request_version) < raw.request_version || typeof raw.idempotency_replayed !== 'boolean') fail()
  for (const field of ['shipment_id', 'inbound_order_id', 'inventory_transaction_id']) uuid(raw[field])
  text(raw.inbound_no); time(raw.posted_at)
  return raw
}
function validateLookup(raw, marker) {
  exact(raw, ['schema_version', 'lookup_status', 'command'])
  if (raw.schema_version !== '1.0') fail()
  if (raw.lookup_status === 'not_observed' && raw.command === null) return null
  if (raw.lookup_status !== 'confirmed') fail()
  return validateResult(raw.command, marker)
}
module.exports = { conditionLabel: condition => CONDITION_LABELS[CONDITIONS.indexOf(condition)], canonical, hash, version, validateCandidates, payload, requestHash, validateResult, validateLookup }
