const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const TRACKING_MODES = ['none', 'lot', 'serial', 'lot_and_serial']

function fail(message) {
  const error = new Error(message)
  error.status = 409
  error.responseReceived = false
  throw error
}

function exactObject(value, keys, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${name}不是有效对象`)
  }
  const actual = Object.keys(value).sort()
  const expected = keys.slice().sort()
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) fail(`${name}必须精确包含正式字段`)
  return value
}

function uuidValue(value, name) {
  if (
    typeof value !== 'string' ||
    !UUID.test(value) ||
    value.toLowerCase() === ZERO_UUID
  ) fail(`${name}无效`)
  return value.toLowerCase()
}

function textValue(value, name, maximum, allowEmpty = false) {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    value.length > maximum ||
    (!allowEmpty && !value.length) ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) fail(`${name}无效`)
  return value
}

function validateItem(value) {
  const object = exactObject(value, [
    'material_id', 'sku_code', 'name', 'specification', 'base_unit', 'tracking_mode',
    'quantity_scale', 'allow_fraction', 'source_updated_at'
  ], '正式物料目录项')
  if (!TRACKING_MODES.includes(object.tracking_mode)) fail('tracking_mode无效')
  if (
    !Number.isSafeInteger(object.quantity_scale) ||
    object.quantity_scale < 0 ||
    object.quantity_scale > 3
  ) fail('quantity_scale无效')
  if (typeof object.allow_fraction !== 'boolean') fail('allow_fraction无效')
  const updatedAt = textValue(object.source_updated_at, 'source_updated_at', 80)
  if (!AWARE_TIMESTAMP.test(updatedAt) || !Number.isFinite(Date.parse(updatedAt))) {
    fail('source_updated_at无效')
  }
  return Object.freeze({
    material_id: uuidValue(object.material_id, 'material_id'),
    sku_code: textValue(object.sku_code, 'sku_code', 80),
    name: textValue(object.name, 'name', 200),
    specification: textValue(object.specification, 'specification', 300, true),
    base_unit: textValue(object.base_unit, 'base_unit', 32),
    tracking_mode: object.tracking_mode,
    quantity_scale: object.quantity_scale,
    allow_fraction: object.allow_fraction,
    source_updated_at: updatedAt
  })
}

function validatePage(value) {
  const object = exactObject(
    value,
    ['schema_version', 'items', 'next_after_id'],
    '正式物料目录分页'
  )
  if (object.schema_version !== '1.0') fail('正式物料目录版本不受支持')
  if (!Array.isArray(object.items)) fail('正式物料目录items无效')
  if (object.items.length > 100) fail('正式物料目录分页超过服务端上限')
  const items = object.items.map(validateItem)
  if (
    new Set(items.map((item) => item.material_id)).size !== items.length ||
    new Set(items.map((item) => item.sku_code)).size !== items.length
  ) fail('正式物料目录分页包含重复物料或SKU')
  const nextAfterId = object.next_after_id === null
    ? null
    : uuidValue(object.next_after_id, 'next_after_id')
  if (nextAfterId && items.some((item) => item.material_id === nextAfterId)) {
    fail('正式物料目录游标指向当前页对象')
  }
  return Object.freeze({ schema_version: '1.0', items, next_after_id: nextAfterId })
}

function validateQuery(value) {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    value.length > 100 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) fail('物料检索词无效')
  return value
}

module.exports = { validateItem, validatePage, validateQuery }
