const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const QUANTITY = /^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/
const TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const STATUS = { pending_handover: '待交运', shipped: '已发运', in_transit: '运输中', exception: '发运异常' }

function fail() { throw new Error('本人收货数据未通过校验，请刷新后重试。') }
function uuid(value) { if (typeof value !== 'string' || !UUID.test(value)) fail(); return value.toLowerCase() }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(fields.slice().sort())) fail()
  return value
}
function text(value) { if (typeof value !== 'string' || !value.trim()) fail(); return value }
function units(value) { if (typeof value !== 'string' || !QUANTITY.test(value)) fail(); return BigInt(value.replace('.', '')) }

function validateMyReceiving(value, requestId, personId, afterId = null) {
  const r = exact(value, ['schema_version', 'request_id', 'request_no', 'request_version', 'person_id', 'packages', 'next_after_id'])
  if (r.schema_version !== '1.0' || uuid(r.request_id) !== uuid(requestId) || uuid(r.person_id) !== uuid(personId) || !Number.isSafeInteger(r.request_version) || r.request_version < 1) fail()
  text(r.request_no)
  if (!Array.isArray(r.packages) || r.packages.length > 20) fail()
  let previous = afterId === null ? '' : uuid(afterId)
  const lineIds = new Set()
  const packages = r.packages.map((raw) => {
    const p = exact(raw, ['shipment_id', 'shipment_no', 'shipment_status', 'carrier', 'tracking_no', 'shipped_at', 'target_location_id', 'target_location_name', 'lines'])
    const id = uuid(p.shipment_id)
    if (id <= previous) fail()
    previous = id
    uuid(p.target_location_id)
    for (const key of ['shipment_no', 'carrier', 'tracking_no', 'target_location_name']) text(p[key])
    if (!Object.prototype.hasOwnProperty.call(STATUS, p.shipment_status) || typeof p.shipped_at !== 'string' || !TIME.test(p.shipped_at) || !Number.isFinite(Date.parse(p.shipped_at))) fail()
    if (!Array.isArray(p.lines) || !p.lines.length || p.lines.length > 100) fail()
    const lines = p.lines.map((rawLine) => {
      const l = exact(rawLine, ['shipment_line_id', 'request_line_id', 'sku_code', 'material_name', 'base_unit', 'shipped_qty', 'accepted_qty', 'rejected_qty', 'unconfirmed_qty', 'has_exception'])
      const lineId = uuid(l.shipment_line_id)
      if (lineIds.has(lineId)) fail()
      lineIds.add(lineId)
      uuid(l.request_line_id)
      for (const key of ['sku_code', 'material_name', 'base_unit']) text(l[key])
      const shipped = units(l.shipped_qty), accepted = units(l.accepted_qty), rejected = units(l.rejected_qty), unconfirmed = units(l.unconfirmed_qty)
      if (shipped <= 0n || accepted + rejected + unconfirmed !== shipped || typeof l.has_exception !== 'boolean' || (rejected > 0n && !l.has_exception)) fail()
      return Object.assign({}, l, { shipment_line_id: lineId })
    })
    return Object.assign({}, p, { shipment_id: id, lines, statusLabel: STATUS[p.shipment_status] })
  })
  const next = r.next_after_id === null ? null : uuid(r.next_after_id)
  if (next !== null && (!packages.length || next !== previous)) fail()
  return { requestId: uuid(r.request_id), requestNo: r.request_no, requestVersion: r.request_version, personId: uuid(r.person_id), packages, nextAfterId: next }
}

module.exports = { uuid, validateMyReceiving }
