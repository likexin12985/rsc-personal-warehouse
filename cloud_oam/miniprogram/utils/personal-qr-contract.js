const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TYPES = new Set(['serial', 'material', 'lot', 'location'])
const ACTIONS = new Set(['view_personal_serials', 'view_personal_warehouse'])

function fail(message) { throw new Error(message) }
function text(value, max, label) {
  if (typeof value !== 'string' || !value || value !== value.trim() || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) fail(`${label}无效`)
  return value
}
function nullableText(value, max, label) {
  if (value === null || value === undefined) return null
  return text(value, max, label)
}
function uuid(value, label) {
  if (typeof value !== 'string' || !UUID.test(value)) fail(`${label}无效`)
  return value.toLowerCase()
}
function exact(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('二维码解析响应无效')
  const keys = Object.keys(value).sort()
  const expected = allowed.slice().sort()
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) fail('二维码解析响应字段不完整')
}

function validate(value) {
  exact(value, ['schema_version', 'object_type', 'object_id', 'display_name', 'sku_code', 'material_name', 'lot_no', 'serial_no', 'stock_account_id', 'location_id', 'ledger_cursor', 'actions'])
  if (value.schema_version !== '1.0' || !TYPES.has(value.object_type)) fail('二维码解析版本或对象类型无效')
  const result = {
    objectType: value.object_type,
    objectId: uuid(value.object_id, '对象标识'),
    displayName: text(value.display_name, 300, '对象摘要'),
    skuCode: nullableText(value.sku_code, 80, '物料编码'),
    materialName: nullableText(value.material_name, 200, '物料名称'),
    lotNo: nullableText(value.lot_no, 160, '批次'),
    serialNo: nullableText(value.serial_no, 200, 'SN'),
    stockAccountId: value.stock_account_id === null ? null : uuid(value.stock_account_id, '库存明细'),
    locationId: value.location_id === null ? null : uuid(value.location_id, '库位'),
    ledgerCursor: value.ledger_cursor === null ? null : value.ledger_cursor,
    actions: value.actions
  }
  if (result.ledgerCursor !== null && (!Number.isSafeInteger(result.ledgerCursor) || result.ledgerCursor < 0)) fail('账本游标无效')
  if (!Array.isArray(result.actions) || !result.actions.length || result.actions.some(action => typeof action !== 'string' || !ACTIONS.has(action))) fail('二维码可用操作无效')
  if (new Set(result.actions).size !== result.actions.length) fail('二维码可用操作重复')
  if (result.objectType === 'serial') {
    if (!result.skuCode || !result.materialName || !result.serialNo || !result.stockAccountId || !result.locationId || result.ledgerCursor === null || !result.actions.includes('view_personal_serials')) fail('SN 二维码响应缺少个人仓范围证明')
  } else if (result.objectType === 'location') {
    if (!result.locationId || result.ledgerCursor === null || !result.actions.includes('view_personal_warehouse')) fail('个人仓二维码响应缺少库位范围证明')
  } else if (!result.skuCode || !result.materialName || !result.actions.includes('view_personal_warehouse')) {
    fail('物料二维码响应缺少目录字段')
  }
  return result
}

module.exports = { validate }
