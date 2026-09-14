const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SHA_TEXT = /^[^\u0000-\u001f\u007f]{1,200}$/
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const DECIMAL = /^-?(?:0|[1-9][0-9]*)\.[0-9]{3}$/
const PROJECTION = ['not_initialized', 'ready']
const OPENING = ['not_established', 'established']
const MOVEMENT_TYPES = ['opening', 'transfer', 'reserve', 'release', 'pick', 'outbound', 'transit', 'inbound', 'freeze', 'unfreeze', 'consume', 'return', 'scrap', 'stocktake_gain', 'stocktake_loss', 'status_change', 'reversal']
const CONDITIONS = ['new', 'used', 'damaged', 'scrapped']
const BUCKETS = ['available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending']

function fail(message = '个人仓流水响应未通过核验，请刷新。') { throw new Error(message) }
function object(value) { if (!value || typeof value !== 'object' || Array.isArray(value)) fail(); return value }
function exact(value, keys) {
  const row = object(value)
  const actual = Object.keys(row).sort(), expected = keys.slice().sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail('个人仓流水响应字段不完整。')
  return row
}
function text(value) { if (typeof value !== 'string' || !SHA_TEXT.test(value) || !value.trim()) fail(); return value }
function uuid(value) { if (typeof value !== 'string' || !UUID.test(value)) fail(); return value.toLowerCase() }
function timestamp(value, nullable = false) {
  if (nullable && value === null) return null
  if (typeof value !== 'string' || !ISO_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail()
  return value
}
function decimal(value) { if (typeof value !== 'string' || !DECIMAL.test(value)) fail(); return value }
function integer(value, field) { if (!Number.isSafeInteger(value) || value < 0) fail(`个人仓流水 ${field} 无效。`); return value }

function validate(payload, expected = {}) {
  const raw = exact(payload, ['schema_version', 'projection_status', 'opening_balance_status', 'projected_at', 'ledger_cursor', 'person_id', 'location_id', 'items', 'next_after_cursor'])
  if (raw.schema_version !== '1.0' || !PROJECTION.includes(raw.projection_status) || !OPENING.includes(raw.opening_balance_status)) fail()
  const cursor = integer(raw.ledger_cursor, '账本游标')
  if (expected.ledgerCursor !== undefined && cursor !== expected.ledgerCursor) fail('个人仓流水跨页账本已变化，请刷新第一页。')
  timestamp(raw.projected_at, true)
  if (cursor === 0 && raw.projected_at !== null) fail('空账本不能带投影时间。')
  if (cursor > 0 && raw.projected_at === null) fail('非空账本缺少投影时间。')
  if (raw.opening_balance_status === 'established' && raw.projection_status !== 'ready') fail('期初状态与投影状态不一致。')
  const personId = uuid(raw.person_id)
  if (expected.personId && personId !== uuid(expected.personId)) fail('流水人员与当前身份不一致。')
  const locationId = raw.location_id === null ? null : uuid(raw.location_id)
  if (expected.locationId !== undefined && (locationId || null) !== (expected.locationId ? uuid(expected.locationId) : null)) fail('流水库位与当前个人仓不一致。')
  if (!Array.isArray(raw.items) || raw.items.length > 50) fail('个人仓流水分页大小无效。')
  const seen = new Set()
  const items = raw.items.map((value) => {
    const row = exact(value, ['transaction_id', 'transaction_no', 'ledger_cursor', 'movement_type', 'status', 'source_document_type', 'source_document_id', 'effective_at', 'posted_at', 'changes'])
    const id = uuid(row.transaction_id)
    if (seen.has(id) || row.status !== 'posted') fail('个人仓流水存在重复或未知状态。')
    seen.add(id)
    const itemCursor = integer(row.ledger_cursor, '交易游标')
    if (itemCursor > cursor || (expected.afterCursor && itemCursor >= expected.afterCursor)) fail('个人仓流水游标顺序无效。')
    text(row.transaction_no); text(row.source_document_type); text(row.source_document_id)
    if (!MOVEMENT_TYPES.includes(row.movement_type)) fail('个人仓流水动作无效。')
    timestamp(row.effective_at); timestamp(row.posted_at)
    if (!Array.isArray(row.changes) || !row.changes.length || row.changes.length > 100) fail('个人仓流水缺少库存维度变化。')
    const changes = row.changes.map((value) => {
      const change = exact(value, ['movement_id', 'line_no', 'stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'availability_bucket', 'direction', 'quantity', 'serial_count'])
      uuid(change.movement_id); uuid(change.stock_account_id); uuid(change.material_id)
      if (!Number.isSafeInteger(change.line_no) || change.line_no < 1 || !['in', 'out'].includes(change.direction)) fail('个人仓流水方向或行号无效。')
      text(change.sku_code); text(change.material_name); text(change.base_unit)
      if (!CONDITIONS.includes(change.condition_code) || !BUCKETS.includes(change.availability_bucket)) fail('个人仓流水成色或状态无效。')
      decimal(change.quantity)
      if (change.quantity.startsWith('-') || change.quantity === '0.000' || !Number.isSafeInteger(change.serial_count) || change.serial_count < 0) fail('个人仓流水数量或 SN 数量无效。')
      return change
    })
    return { ...row, transaction_id: id, changes }
  })
  const next = raw.next_after_cursor === null ? null : integer(raw.next_after_cursor, '下一页游标')
  if (next !== null && (!items.length || next !== items[items.length - 1].ledger_cursor)) fail('个人仓流水下一页游标无效。')
  if (raw.opening_balance_status === 'not_established' && items.length) fail('期初未建立时不得返回个人仓流水。')
  return Object.freeze({ ...raw, person_id: personId, location_id: locationId, items, next_after_cursor: next })
}

module.exports = { validate }
