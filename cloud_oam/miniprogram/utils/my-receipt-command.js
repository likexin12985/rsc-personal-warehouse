const { uuid } = require('./my-receiving-contract')
const { sha256Hex } = require('./formal-file-upload')
const CONDITIONS = ['normal', 'shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected']
const LABELS = ['正常', '短少', '破损', '错料', '错 SN', '拒收']
const LINE_FIELDS = ['shipment_line_id', 'accepted_qty', 'rejected_qty', 'condition', 'accepted_serial_ids', 'rejected_serial_ids', 'exception_evidence_file_id']

function fail(message = '验收内容或原请求结果不一致，请保留恢复记录。') { throw new Error(message) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('|') !== fields.slice().sort().join('|')) fail()
}
function quantity(value) {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?$/.test(value)) fail('数量应为非负数，最多三位小数。')
  const [whole, fraction = ''] = value.split('.')
  return `${whole}.${fraction.padEnd(3, '0')}`
}
function units(value) { return BigInt(quantity(value).replace('.', '')) }
function fromUnits(value) { return `${value / 1000n}.${String(value % 1000n).padStart(3, '0')}` }
function time(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) fail('验收时间无效。')
  return value
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}
function validatePayload(value) {
  exact(value, ['expected_request_version', 'shipment_id', 'received_at', 'lines'])
  if (!Number.isSafeInteger(value.expected_request_version) || value.expected_request_version < 1 || !Array.isArray(value.lines) || !value.lines.length || value.lines.length > 100) fail()
  const ids = new Set(), serials = new Set()
  const lines = value.lines.map(raw => {
    exact(raw, LINE_FIELDS)
    const id = uuid(raw.shipment_line_id)
    if (ids.has(id) || !CONDITIONS.includes(raw.condition)) fail()
    ids.add(id)
    const accepted = quantity(raw.accepted_qty), rejected = quantity(raw.rejected_qty)
    if (units(accepted) + units(rejected) <= 0n) fail('请选择需要登记验收的数量或 SN。')
    if (raw.condition === 'normal' && (units(rejected) > 0n || raw.exception_evidence_file_id !== null)) fail('正常验收不能包含拒收或异常文件。')
    if (['damaged', 'wrong_material', 'wrong_serial', 'rejected'].includes(raw.condition) && units(accepted) > 0n) fail('破损、错料、错 SN 或拒收不能计入合格数量。')
    const proof = raw.exception_evidence_file_id === null ? null : uuid(raw.exception_evidence_file_id)
    if (raw.condition !== 'normal' && !proof) fail('请先上传并核验异常凭证。')
    const checkedSerials = key => {
      if (!Array.isArray(raw[key]) || raw[key].length > 1000) fail()
      return raw[key].map(s => { const serial = uuid(s); if (serials.has(serial)) fail('SN 重复。'); serials.add(serial); return serial }).sort()
    }
    return { shipment_line_id: id, accepted_qty: accepted, rejected_qty: rejected, condition: raw.condition,
      accepted_serial_ids: checkedSerials('accepted_serial_ids'), rejected_serial_ids: checkedSerials('rejected_serial_ids'), exception_evidence_file_id: proof }
  }).sort((a, b) => a.shipment_line_id.localeCompare(b.shipment_line_id))
  return { expected_request_version: value.expected_request_version, shipment_id: uuid(value.shipment_id), received_at: time(value.received_at), lines }
}
function requestHash(requestId, personId, payload) {
  const serialized = canonical(Object.assign(validatePayload(payload), { request_id: uuid(requestId), person_id: uuid(personId) }))
  // This command contains only reviewed ASCII identifiers, enums and numbers.
  if (/[^\x00-\x7f]/.test(serialized)) fail()
  return sha256Hex(Uint8Array.from(serialized, character => character.charCodeAt(0)))
}
function buildCommand(candidate, drafts, receivedAt, now = Date.now()) {
  if (!candidate.canReceive || !drafts || typeof drafts !== 'object') fail('当前包裹不能新增验收。')
  if (Date.parse(time(receivedAt)) < Date.parse(time(candidate.shippedAt)) || Date.parse(receivedAt) > now) fail('验收时间应在交运后，且不能晚于当前时间。')
  const lines = []
  for (const line of candidate.lines) {
    const draft = drafts[line.shipment_line_id]
    if (!draft) continue
    const choices = draft.serials || {}
    const remaining = new Set(line.remaining_serials.map(serial => serial.serial_id))
    if (Object.keys(choices).some(id => !remaining.has(id) || !['accepted', 'rejected'].includes(choices[id]))) fail('已选 SN 不在最新待验收明细中，请重新核对。')
    const acceptedIds = Object.keys(choices).filter(id => choices[id] === 'accepted')
    const rejectedIds = Object.keys(choices).filter(id => choices[id] === 'rejected')
    const accepted = line.tracked ? fromUnits(BigInt(acceptedIds.length) * 1000n) : quantity(draft.accepted || '0')
    const rejected = line.tracked ? fromUnits(BigInt(rejectedIds.length) * 1000n) : quantity(draft.rejected || '0')
    const total = units(accepted) + units(rejected)
    if (total === 0n) continue
    if (total > units(line.unconfirmed_qty)) fail('本次验收数量超过包裹尚未验收数量。')
    const factor = 10n ** BigInt(3 - line.quantity_scale)
    if ([accepted, rejected].some(q => units(q) % factor !== 0n || (!line.allow_fraction && units(q) % 1000n !== 0n))) fail('验收数量不符合物料的小数精度规则。')
    if (!line.tracked && Object.keys(choices).length) fail()
    lines.push({ shipment_line_id: line.shipment_line_id, accepted_qty: accepted, rejected_qty: rejected,
      condition: draft.condition, accepted_serial_ids: acceptedIds, rejected_serial_ids: rejectedIds,
      exception_evidence_file_id: draft.evidenceFileId || null })
  }
  return validatePayload({ expected_request_version: candidate.requestVersion, shipment_id: candidate.shipmentId, received_at: receivedAt, lines })
}
function validateResult(raw, marker) {
  exact(raw, ['schema_version', 'request_id', 'person_id', 'receipt_id', 'receipt_no', 'shipment_id', 'received_at', 'status', 'request_hash', 'lines', 'idempotency_replayed'])
  if (raw.schema_version !== '1.0' || uuid(raw.request_id) !== marker.request_id || uuid(raw.person_id) !== marker.person_id || uuid(raw.shipment_id) !== marker.shipment_id
    || raw.request_hash !== marker.request_hash || typeof raw.idempotency_replayed !== 'boolean'
    || typeof raw.receipt_no !== 'string' || !raw.receipt_no.trim() || raw.receipt_no.length > 160 || /[\u0000-\u001f\u007f]/.test(raw.receipt_no)
    || Date.parse(time(raw.received_at)) !== Date.parse(marker.received_at)
    || (raw.received_at.match(/\.(\d+)/)?.[1] || '').padEnd(6, '0').slice(3) !== (marker.received_at.match(/\.(\d+)/)?.[1] || '').padEnd(6, '0').slice(3)
    || !Array.isArray(raw.lines) || !raw.lines.length || raw.lines.length > 100) fail()
  uuid(raw.receipt_id)
  const receiptIds = new Set()
  const lines = raw.lines.map(line => {
    exact(line, LINE_FIELDS.concat('receipt_line_id'))
    const id = uuid(line.receipt_line_id)
    if (receiptIds.has(id)) fail()
    receiptIds.add(id)
    const { receipt_line_id, ...payloadLine } = line
    return payloadLine
  })
  const payload = validatePayload({ expected_request_version: marker.expected_request_version,
    shipment_id: marker.shipment_id, received_at: marker.received_at, lines })
  if (requestHash(marker.request_id, marker.person_id, payload) !== marker.request_hash
    || raw.status !== (payload.lines.every(line => line.condition === 'normal') ? 'accepted' : 'exception')) fail()
  return raw
}
function validateLookup(raw, marker) {
  exact(raw, ['schema_version', 'lookup_status', 'command'])
  if (raw.schema_version !== '1.0') fail()
  if (raw.lookup_status === 'not_observed' && raw.command === null) return null
  if (raw.lookup_status !== 'confirmed') fail()
  return validateResult(raw.command, marker)
}

module.exports = { CONDITIONS, LABELS, quantity, units, fromUnits, canonical, validatePayload, requestHash, buildCommand, validateResult, validateLookup, time }
