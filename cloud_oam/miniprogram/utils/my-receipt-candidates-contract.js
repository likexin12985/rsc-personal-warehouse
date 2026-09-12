const { uuid } = require('./my-receiving-contract')
const QUANTITY = /^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/
const TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const BLOCKED = {
  permission_required: '当前账号没有本人验收权限。',
  request_not_approved: '需求当前状态不能新增验收。',
  pending_handover: '包裹尚未完成交运。',
  complete: '本包裹已全部登记验收，个人仓入账请另行查看。'
}

function fail() { throw new Error('待验收明细未通过校验，请刷新。') }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(fields.slice().sort())) fail()
  return value
}
function text(value, maximum = 250) {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim() || value.length > maximum || /[\u0000-\u001f\u007f]/.test(value)) fail()
  return value
}
function units(value) { if (typeof value !== 'string' || !QUANTITY.test(value)) fail(); return BigInt(value.replace('.', '')) }
function time(value) { if (typeof value !== 'string' || !TIME.test(value) || !Number.isFinite(Date.parse(value))) fail(); return value }

function validateCandidates(value, requestId, shipmentId, personId) {
  const r = exact(value, ['schema_version', 'request_id', 'request_no', 'request_version', 'person_id', 'shipment_id', 'shipment_no', 'shipped_at', 'target_location_name', 'checked_at', 'can_receive', 'blocked_reason', 'lines'])
  if (r.schema_version !== '1.0' || uuid(r.request_id) !== uuid(requestId) || uuid(r.shipment_id) !== uuid(shipmentId) || uuid(r.person_id) !== uuid(personId) || !Number.isSafeInteger(r.request_version) || r.request_version < 1) fail()
  for (const key of ['request_no', 'shipment_no', 'target_location_name']) text(r[key])
  time(r.shipped_at); time(r.checked_at)
  if (typeof r.can_receive !== 'boolean' || (r.blocked_reason !== null && !Object.prototype.hasOwnProperty.call(BLOCKED, r.blocked_reason)) || r.can_receive !== (r.blocked_reason === null)) fail()
  if (!Array.isArray(r.lines) || !r.lines.length || r.lines.length > 100) fail()
  const lineIds = new Set(), serialIds = new Set(), qrCodes = new Set()
  const lines = r.lines.map((raw) => {
    const l = exact(raw, ['shipment_line_id', 'request_line_id', 'sku_code', 'material_name', 'base_unit', 'shipped_qty', 'accepted_qty', 'rejected_qty', 'unconfirmed_qty', 'has_exception', 'lot_no', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'remaining_serials'])
    const id = uuid(l.shipment_line_id)
    if (lineIds.has(id)) fail()
    lineIds.add(id); uuid(l.request_line_id)
    for (const key of ['sku_code', 'material_name', 'base_unit']) text(l[key])
    if (!['none', 'lot', 'serial', 'lot_and_serial'].includes(l.tracking_mode) || !Number.isSafeInteger(l.quantity_scale) || l.quantity_scale < 0 || l.quantity_scale > 3 || typeof l.allow_fraction !== 'boolean' || typeof l.has_exception !== 'boolean') fail()
    if (l.lot_no !== null) text(l.lot_no, 160)
    if (['lot', 'lot_and_serial'].includes(l.tracking_mode) && l.lot_no === null) fail()
    const quantities = ['shipped_qty', 'accepted_qty', 'rejected_qty', 'unconfirmed_qty'].map(key => units(l[key]))
    const [shipped, accepted, rejected, remaining] = quantities
    if (shipped <= 0n || accepted + rejected + remaining !== shipped || (rejected > 0n && !l.has_exception)) fail()
    const factor = 10n ** BigInt(3 - l.quantity_scale)
    if (quantities.some(q => q % factor !== 0n || (!l.allow_fraction && q % 1000n !== 0n))) fail()
    const tracked = ['serial', 'lot_and_serial'].includes(l.tracking_mode)
    if (!Array.isArray(l.remaining_serials) || l.remaining_serials.length > 1000 || (tracked ? BigInt(l.remaining_serials.length) * 1000n !== remaining : l.remaining_serials.length !== 0)) fail()
    const serials = l.remaining_serials.map((rawSerial) => {
      const s = exact(rawSerial, ['serial_id', 'serial_no', 'qr_code'])
      const serialId = uuid(s.serial_id)
      text(s.serial_no, 200); text(s.qr_code)
      if (serialIds.has(serialId) || qrCodes.has(s.qr_code)) fail()
      serialIds.add(serialId); qrCodes.add(s.qr_code)
      return { serial_id: serialId, serial_no: s.serial_no, qr_code: s.qr_code }
    })
    return Object.assign({}, l, { shipment_line_id: id, remaining_serials: serials, tracked })
  })
  const complete = lines.every(l => units(l.unconfirmed_qty) === 0n)
  if ((r.can_receive && complete) || (r.blocked_reason === 'complete' && !complete)) fail()
  return { requestId: uuid(r.request_id), shipmentId: uuid(r.shipment_id), personId: uuid(r.person_id), requestVersion: r.request_version,
    requestNo: r.request_no, shipmentNo: r.shipment_no, targetLocationName: r.target_location_name,
    checkedAt: r.checked_at, canReceive: r.can_receive, blockedMessage: BLOCKED[r.blocked_reason] || '', lines }
}

function matchCandidateScan(candidate, code) {
  // Match only the exact QR or SN string returned for this package. Never parse
  // URLs, infer an SKU, or choose the first of multiple same-number materials.
  text(code)
  const matches = candidate.lines.flatMap(line => line.remaining_serials
    .filter(s => s.qr_code === code || s.serial_no === code)
    .map(s => ({ shipmentLineId: line.shipment_line_id, serialId: s.serial_id })))
  if (matches.length !== 1) throw new Error(matches.length ? '该 SN 对应多条明细，请扫描物料二维码。' : '扫描结果不属于本包裹的待验收 SN，请核对。')
  return matches[0]
}

module.exports = { validateCandidates, matchCandidateScan }
