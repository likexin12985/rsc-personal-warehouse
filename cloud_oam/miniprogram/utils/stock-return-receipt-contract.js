// Client contract for the dedicated return-parcel receiving command.  This
// intentionally does not reuse the forward-demand receipt contract: a return
// receipt is tied to one verified shipment and does not post inventory.
const { uuid } = require('./work-order-query-contract')
const { canonical, utf8 } = require('./work-order-command')
const { sha256Hex } = require('./formal-file-upload')
const { amount, reason, requestId, digest, list, integer } = require('./stock-return-contract')
const { fromUnits, units } = require('./my-receipt-command')
const { instant } = require('./stock-return-outbound-contract')

const KIND = 'stock_return'
const ACTION = 'receive_return'
const TYPES = ['shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected']
const READ = { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }

function fail(message = '退回包裹验收内容未通过核验，请刷新原包裹。') { throw new Error(message) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(fields.slice().sort())) fail()
  return value
}
function text(value, max = 500) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim() || value.length > max
    || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)) fail()
  utf8(value); return value
}
function time(value) { instant(value); return value }
function decimal(value) { amount(value); return value }
function serialProof(value) {
  exact(value, ['serial_id', 'sku_code', 'serial_no', 'qr_code'])
  uuid(value.serial_id); text(value.sku_code, 80); text(value.serial_no, 200); text(value.qr_code, 250)
  return { serial_id: value.serial_id.toLowerCase(), sku_code: value.sku_code, serial_no: value.serial_no, qr_code: value.qr_code }
}
function evidence(value) {
  exact(value, ['exception_type', 'description', 'evidence_file_id'])
  if (!TYPES.includes(value.exception_type)) fail()
  text(value.description, 1000); uuid(value.evidence_file_id)
  return { ...value, evidence_file_id: value.evidence_file_id.toLowerCase() }
}

function normalizeLine(value) {
  exact(value, ['shipment_line_id', 'accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty',
    'accepted_serial_verifications', 'damaged_serial_ids', 'rejected_serial_ids', 'shortage_serial_ids', 'exceptions'])
  const result = {
    shipment_line_id: uuid(value.shipment_line_id), accepted_qty: decimal(value.accepted_qty),
    rejected_qty: decimal(value.rejected_qty), damaged_qty: decimal(value.damaged_qty), shortage_qty: decimal(value.shortage_qty),
    accepted_serial_verifications: list(value.accepted_serial_verifications, 0, 1000).map(serialProof).sort((a, b) => a.serial_id.localeCompare(b.serial_id)),
    damaged_serial_ids: list(value.damaged_serial_ids, 0, 1000).map(uuid).sort(),
    rejected_serial_ids: list(value.rejected_serial_ids, 0, 1000).map(uuid).sort(),
    shortage_serial_ids: list(value.shortage_serial_ids, 0, 1000).map(uuid).sort(),
    exceptions: list(value.exceptions, 0, 5).map(evidence).sort((a, b) => a.exception_type.localeCompare(b.exception_type))
  }
  const accepted = units(result.accepted_qty), rejected = units(result.rejected_qty), shortage = units(result.shortage_qty), damaged = units(result.damaged_qty)
  const types = result.exceptions.map(row => row.exception_type)
  if (new Set(types).size !== types.length || new Set([...result.accepted_serial_verifications.map(row => row.serial_id), ...result.rejected_serial_ids, ...result.shortage_serial_ids]).size
      !== result.accepted_serial_verifications.length + result.rejected_serial_ids.length + result.shortage_serial_ids.length
    || damaged > accepted || accepted + rejected + shortage <= 0n
    || Boolean(shortage) !== types.includes('shortage') || Boolean(damaged) !== types.includes('damaged')
    || Boolean(rejected) !== types.some(type => ['rejected', 'wrong_material', 'wrong_serial'].includes(type))) fail('异常数量必须有对应说明和已确认凭证。')
  if (new Set(result.damaged_serial_ids).size !== result.damaged_serial_ids.length
      || result.damaged_serial_ids.some(id => !result.accepted_serial_verifications.some(row => row.serial_id === id))) fail('破损 SN 必须属于本次接受 SN。')
  return result
}

function normalizeInput(value, shipmentId, operatorPersonId) {
  exact(value, ['operator_person_id', 'received_at', 'reason', 'lines'])
  const lines = list(value.lines, 1, 100).map(normalizeLine).sort((a, b) => a.shipment_line_id.localeCompare(b.shipment_line_id))
  if (new Set(lines.map(row => row.shipment_line_id)).size !== lines.length) fail()
  const receivedAt = new Date(time(value.received_at)).toISOString().replace(/(\.\d{3})Z$/, '$1000Z')
  return { operator_person_id: uuid(value.operator_person_id), received_at: receivedAt, reason: reason(value.reason), lines,
    shipment_id: uuid(shipmentId), operation_type: 'receive_return' }
}
function requestHash(shipmentId, value) {
  const normalized = normalizeInput(value, shipmentId, value.operator_person_id)
  return sha256Hex(utf8(canonical(normalized)))
}
function payload(value, shipmentId) {
  const normalized = normalizeInput(value, shipmentId, value.operator_person_id)
  delete normalized.shipment_id; delete normalized.operation_type
  return normalized
}
function scanProof(line, serialId, materialCode, serialNo, qrCode) {
  if (!line || !line.serials || !line.serials.some(row => row.serial_id === serialId && row.serial_no === serialNo)) fail('SN 不属于当前退回包裹。')
  text(materialCode, 80); text(serialNo, 200); text(qrCode, 250)
  if (materialCode !== line.sku_code) fail('物料码与退回明细不一致。')
  return { serial_id: serialId, sku_code: materialCode, serial_no: serialNo, qr_code: qrCode }
}
function buildLines(history, drafts) {
  const rows = []
  for (const line of history.package.lines) {
    const draft = drafts && drafts[line.shipment_line_id]
    if (!draft) continue
    const choices = draft.serials || {}
    const accepted = Object.keys(choices).filter(id => choices[id] === 'accepted')
    const rejected = Object.keys(choices).filter(id => choices[id] === 'rejected')
    const shortage = Object.keys(choices).filter(id => choices[id] === 'shortage')
    const proofs = accepted.map(id => draft.proofs && draft.proofs[id]).filter(Boolean)
    const tracked = line.serials.length > 0
    const acceptedQty = tracked ? fromUnits(BigInt(accepted.length) * 1000n) : decimal(draft.accepted_qty || '0.000')
    const rejectedQty = tracked ? fromUnits(BigInt(rejected.length) * 1000n) : decimal(draft.rejected_qty || '0.000')
    const shortageQty = tracked ? fromUnits(BigInt(shortage.length) * 1000n) : decimal(draft.shortage_qty || '0.000')
    const damagedQty = decimal(draft.damaged_qty || '0.000')
    if (proofs.length !== accepted.length) fail('已接受 SN 尚未完成三码核验。')
    const exceptionTypes = {}
    for (const type of TYPES) {
      const item = draft.exceptions && draft.exceptions[type]
      if (item && item.evidence_file_id) exceptionTypes[type] = { exception_type: type, description: item.description || '', evidence_file_id: item.evidence_file_id }
    }
    if (acceptedQty !== '0.000' || rejectedQty !== '0.000' || shortageQty !== '0.000') rows.push({
      shipment_line_id: line.shipment_line_id, accepted_qty: acceptedQty, rejected_qty: rejectedQty, damaged_qty: damagedQty, shortage_qty: shortageQty,
      accepted_serial_verifications: proofs, damaged_serial_ids: (draft.damaged_serial_ids || []).slice(),
      rejected_serial_ids: rejected, shortage_serial_ids: shortage, exceptions: Object.values(exceptionTypes)
    })
  }
  if (!rows.length) fail('请填写至少一条本次验收明细。')
  return rows
}
function validatePreview(raw, input, history, shipmentId) {
  exact(raw, ['schema_version', 'planning_status', 'shipment_id', 'operation_id', 'work_order_id', 'operator_person_id', 'authorization_version', 'received_at', 'reason', 'checked_at', 'ledger_cursor', 'package', 'request_hash', 'plan_hash', 'lines'])
  if (raw.schema_version !== '1.0' || raw.planning_status !== 'preview_only' || uuid(raw.shipment_id) !== uuid(shipmentId)
    || uuid(raw.operator_person_id) !== uuid(input.operator_person_id) || raw.request_hash !== requestHash(shipmentId, input)) fail()
  uuid(raw.operation_id); uuid(raw.work_order_id); integer(raw.authorization_version, 1); time(raw.received_at); time(raw.checked_at); integer(raw.ledger_cursor); digest(raw.request_hash); digest(raw.plan_hash)
  if (canonical(raw.package) !== canonical(history.package)) fail('预检返回的原包裹已变化，请刷新。')
  list(raw.lines, 1, 100); return raw
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'receipt_id', 'receipt_no', 'shipment_id', 'operation_id', 'work_order_id', 'operator_person_id', 'status', 'received_at', 'recorded_at', 'reason', 'request_id', 'request_hash', 'plan_hash', 'target_location_id', 'target_custody_assignment_id', 'lines'])
  if (raw.schema_version !== '1.0' || uuid(raw.shipment_id) !== marker.shipment_id || uuid(raw.operator_person_id) !== marker.person_id || raw.request_id !== marker.trace_request_id || raw.request_hash !== marker.request_hash || raw.plan_hash !== marker.plan_hash) fail()
  uuid(raw.receipt_id); text(raw.receipt_no, 100); time(raw.received_at); time(raw.recorded_at); requestId(raw.request_id); digest(raw.request_hash); digest(raw.plan_hash)
  return raw
}
function validateLookup(raw, marker) {
  if (raw && !Object.prototype.hasOwnProperty.call(raw, 'lookup_status')) return validateResult(raw, marker)
  if (raw.lookup_status === 'sealed') { exact(raw, ['schema_version', 'lookup_status', 'seal']); if (raw.schema_version !== '1.0') fail(); return raw }
  exact(raw, ['schema_version', 'lookup_status', 'command'])
  if (raw.schema_version !== '1.0' || raw.lookup_status !== 'confirmed') fail()
  return validateResult(raw.command, marker)
}
module.exports = { KIND, ACTION, READ, TYPES, canonical, payload, requestHash, buildLines, scanProof, validatePreview, validateResult, validateLookup }
