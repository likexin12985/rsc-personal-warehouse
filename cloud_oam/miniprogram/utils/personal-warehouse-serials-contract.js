const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SAFE_TEXT = /^[^\u0000-\u001f\u007f]{1,200}$/
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const TRACKING = ['serial', 'lot_and_serial']
const CONDITIONS = ['new', 'used', 'damaged', 'scrapped']
const BUCKETS = ['available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending']

function fail(message = '个人仓 SN 响应未通过核验，请刷新。') { throw new Error(message) }
function object(value) { if (!value || typeof value !== 'object' || Array.isArray(value)) fail(); return value }
function exact(value, keys) {
  const row = object(value)
  const actual = Object.keys(row).sort(), expected = keys.slice().sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail('个人仓 SN 响应字段不完整。')
  return row
}
function uuid(value) { if (typeof value !== 'string' || !UUID.test(value)) fail(); return value.toLowerCase() }
function text(value) { if (typeof value !== 'string' || !SAFE_TEXT.test(value) || value !== value.trim()) fail(); return value }
function integer(value) { if (!Number.isSafeInteger(value) || value < 0) fail(); return value }
function timestamp(value) {
  if (typeof value !== 'string' || !ISO_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail()
  return value
}

function validate(payload, expected = {}) {
  const raw = exact(payload, [
    'schema_version', 'projection_status', 'opening_balance_status', 'projected_at', 'ledger_cursor',
    'person_id', 'location_id', 'stock_account_id', 'material_id', 'sku_code', 'material_name',
    'base_unit', 'tracking_mode', 'condition_code', 'availability_bucket', 'lot_id', 'lot_no',
    'total_serials', 'items', 'next_after_id'
  ])
  if (raw.schema_version !== '1.0' || raw.projection_status !== 'ready' || raw.opening_balance_status !== 'established') fail('个人仓期初或库存投影尚未就绪。')
  const cursor = integer(raw.ledger_cursor)
  if (cursor === 0 && raw.projected_at !== null) fail('空账本不能带投影时间。')
  if (cursor > 0) timestamp(raw.projected_at)
  if (expected.ledgerCursor !== undefined && cursor !== expected.ledgerCursor) fail('个人仓 SN 跨页账本已变化，请刷新第一页。')
  const personId = uuid(raw.person_id), locationId = uuid(raw.location_id), accountId = uuid(raw.stock_account_id)
  if (expected.personId && personId !== uuid(expected.personId)) fail('SN 人员与当前身份不一致。')
  if (expected.accountId && accountId !== uuid(expected.accountId)) fail('SN 库存账户与当前明细不一致。')
  uuid(raw.material_id); text(raw.sku_code); text(raw.material_name); text(raw.base_unit)
  if (!TRACKING.includes(raw.tracking_mode) || !CONDITIONS.includes(raw.condition_code) || !BUCKETS.includes(raw.availability_bucket)) fail('SN 库存维度无效。')
  const lotId = raw.lot_id === null ? null : uuid(raw.lot_id)
  const lotNo = raw.lot_no === null ? null : text(raw.lot_no)
  if ((lotId === null) !== (lotNo === null)) fail('SN 批次字段不一致。')
  const total = integer(raw.total_serials)
  if (!Array.isArray(raw.items) || raw.items.length > 100 || raw.items.length > total) fail('个人仓 SN 分页大小无效。')
  const seen = new Set()
  const items = raw.items.map((value) => {
    const row = exact(value, ['serial_id', 'serial_no', 'lifecycle_status'])
    const id = uuid(row.serial_id), number = text(row.serial_no)
    if (seen.has(id) || row.lifecycle_status !== 'active') fail('个人仓 SN 重复或生命周期无效。')
    if (expected.afterId && id <= uuid(expected.afterId)) fail('个人仓 SN 分页游标顺序无效。')
    seen.add(id)
    return { serial_id: id, serial_no: number, lifecycle_status: row.lifecycle_status }
  })
  if (expected.serialNo !== undefined) {
    if (raw.next_after_id !== null || items.length > 1 || (items.length === 1 && items[0].serial_no !== expected.serialNo)) fail('扫描 SN 与精确查询结果不一致。')
  }
  const next = raw.next_after_id === null ? null : uuid(raw.next_after_id)
  if (next !== null && (!items.length || next !== items[items.length - 1].serial_id)) fail('个人仓 SN 下一页游标无效。')
  return Object.freeze({ ...raw, person_id: personId, location_id: locationId, stock_account_id: accountId, lot_id: lotId, lot_no: lotNo, items, next_after_id: next })
}

module.exports = { validate }
