const { validatePersonalWarehouse, conditionLabel } = require('./inventory-contract')
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const STATUS = { pending: '待处理', active: '处理中', completed: '已完成', closed: '已关闭', cancelled: '已取消', inactive: '非有效工单' }
const ORDER_FIELDS = ['work_order_id', 'work_order_no', 'status', 'engineer_person_id', 'organization_id', 'source_system', 'source_external_id', 'source_version', 'source_updated_at', 'synced_at', 'freshness', 'can_operate']
function fail() { throw new Error('本人工单或物料数据校验失败，请刷新后重试。') }
function uuid(value) { if (typeof value !== 'string' || !UUID.test(value)) fail(); return value.toLowerCase() }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(fields.slice().sort())) fail()
  return value
}
function text(value) { if (typeof value !== 'string' || !value.trim()) fail(); return value }
function timestamp(value) { if (typeof value !== 'string' || !TIME.test(value) || !Number.isFinite(Date.parse(value))) fail(); return Date.parse(value) }
function units(value) { if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(value)) fail(); return BigInt(value.replace('.', '')) }
function identity(value, person, version) {
  if (value.schema_version !== '1.0' || uuid(value.person_id) !== uuid(person) || !Number.isSafeInteger(version) || version < 1 || value.authorization_version !== version) fail()
}
function order(value, person) {
  const row = exact(value, ORDER_FIELDS)
  uuid(row.work_order_id); uuid(row.organization_id)
  if (uuid(row.engineer_person_id) !== uuid(person) || row.source_system !== 'starcharge_oam' || !Object.prototype.hasOwnProperty.call(STATUS, row.status) || !['fresh', 'stale'].includes(row.freshness) || typeof row.can_operate !== 'boolean') fail()
  for (const key of ['work_order_no', 'source_external_id', 'source_version']) text(row[key])
  if (timestamp(row.source_updated_at) > timestamp(row.synced_at)) fail()
  if (row.can_operate && (row.status !== 'active' || row.freshness !== 'fresh')) fail()
  return Object.assign({}, row, { statusLabel: STATUS[row.status], freshnessLabel: row.freshness === 'fresh' ? '来源已校验' : '同步已过期' })
}
function validateMyWorkOrders(value, person, version, after = null) {
  const row = exact(value, ['schema_version', 'person_id', 'authorization_version', 'queried_at', 'items', 'next_after_id'])
  identity(row, person, version)
  const queried = timestamp(row.queried_at)
  if (!Array.isArray(row.items) || row.items.length > 20) fail()
  let previous = after === null ? '' : uuid(after)
  const items = row.items.map(raw => {
    const item = order(raw, person), id = uuid(item.work_order_id)
    if (id <= previous || timestamp(item.synced_at) > queried) fail()
    previous = id
    return item
  })
  const next = row.next_after_id === null ? null : uuid(row.next_after_id)
  if (next !== null && (!items.length || next !== previous)) fail()
  return { items, next }
}
function validateMaterialOptions(value, person, version, workOrderId) {
  const row = exact(value, ['schema_version', 'projection_status', 'opening_balance_status', 'projected_at', 'ledger_cursor', 'person_id', 'location_id', 'location_code', 'location_name', 'location_status', 'custody_effective_from', 'items', 'work_order', 'authorization_version'])
  identity(row, person, version)
  validatePersonalWarehouse(row, person)
  const selectedOrder = order(row.work_order, person)
  if (uuid(selectedOrder.work_order_id) !== uuid(workOrderId)) fail()
  const seenSerials = new Set()
  const items = row.items.map(raw => {
    if (!['available', 'reserved'].includes(raw.availability_bucket) || !['new', 'used', 'damaged'].includes(raw.condition_code)) fail()
    const quantity = units(raw.selectable_quantity)
    if (quantity <= 0n || quantity > units(raw.quantity) || (raw.availability_bucket === 'available' && quantity !== units(raw.quantity))) fail()
    const expected = !selectedOrder.can_operate ? [] : raw.availability_bucket === 'available' ? ['occupy'] : ['consume', 'release', 'replace']
    if (!Array.isArray(raw.allowed_actions) || JSON.stringify(raw.allowed_actions) !== JSON.stringify(expected) || !Array.isArray(raw.serials)) fail()
    const serials = raw.serials.map(sn => {
      exact(sn, ['serial_id', 'serial_no']); const id = uuid(sn.serial_id); text(sn.serial_no)
      if (seenSerials.has(id)) fail()
      seenSerials.add(id)
      return { serial_id: id, serial_no: sn.serial_no }
    })
    const tracked = ['serial', 'lot_and_serial'].includes(raw.tracking_mode)
    if ((tracked && BigInt(serials.length) * 1000n !== quantity) || (!tracked && serials.length)) fail()
    return Object.assign({}, raw, { serials, conditionLabel: conditionLabel(raw.condition_code), quantityLabel: raw.availability_bucket === 'reserved' ? '本工单剩余占用' : '本人可用库存' })
  })
  return { workOrder: selectedOrder, items, locationName: row.location_name, openingEstablished: row.opening_balance_status === 'established' }
}
module.exports = { uuid, validateMyWorkOrders, validateMaterialOptions }
