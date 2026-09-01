const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DECIMAL_QUANTITY = /^(?:0|[1-9][0-9]*)\.[0-9]{3}$/
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

const PROJECTION_STATUSES = ['not_initialized', 'ready']
const OPENING_STATUSES = ['not_established', 'established']
const QUANTITY_STATUSES = [
  'opening_not_established',
  'material_filter_required'
]
const SCOPE_TYPES = ['national', 'organization', 'person']
const LOCATION_TYPES = ['headquarters', 'region', 'personal', 'transit', 'quarantine']
const TRACKING_MODES = ['none', 'lot', 'serial', 'lot_and_serial']
const CONDITIONS = ['new', 'used', 'damaged', 'scrapped']
const AVAILABILITY_BUCKETS = [
  'available',
  'reserved',
  'picking',
  'outbound',
  'in_transit',
  'arrived_pending',
  'frozen',
  'return_pending',
  'scrap_pending'
]
const SUMMARY_QUANTITY_FIELDS = [
  'physical_in_stock_qty',
  'available_qty',
  'reserved_qty',
  'committed_qty',
  'frozen_qty',
  'physical_in_transit_qty'
]

const CONDITION_LABELS = {
  new: '新件',
  used: '旧件',
  damaged: '坏件',
  scrapped: '已报废'
}

const AVAILABILITY_LABELS = {
  available: '可用',
  reserved: '已占用',
  picking: '待拣货',
  outbound: '待出库',
  in_transit: '在途',
  arrived_pending: '到货待验',
  frozen: '冻结',
  return_pending: '待退回',
  scrap_pending: '待报废'
}

function contractError(code, message) {
  const error = new Error(message)
  error.code = code
  error.status = 409
  return error
}

function fail(code, message) {
  throw contractError(code, message)
}

function objectValue(value, field) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail('inventory_contract_object_invalid', `${field} 不是有效对象`)
  }
  return value
}

function own(object, field) {
  if (!Object.prototype.hasOwnProperty.call(object, field)) {
    fail('inventory_contract_field_missing', `正式库存响应缺少字段 ${field}`)
  }
  return object[field]
}

function enumValue(value, allowed, field) {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    fail('inventory_contract_enum_unknown', `正式库存响应包含未知 ${field}`)
  }
  return value
}

function textValue(value, field) {
  if (typeof value !== 'string' || !value.trim()) {
    fail('inventory_contract_text_invalid', `正式库存响应中的 ${field} 无效`)
  }
  return value
}

function uuidValue(value, field) {
  if (typeof value !== 'string' || !UUID.test(value)) {
    fail('inventory_contract_uuid_invalid', `正式库存响应中的 ${field} 无效`)
  }
  return value.toLowerCase()
}

function nonnegativeInteger(value, field) {
  if (!Number.isSafeInteger(value) || value < 0) {
    fail('inventory_contract_integer_invalid', `正式库存响应中的 ${field} 无效`)
  }
  return value
}

function timestampValue(value, field, nullable = false) {
  if (value === null && nullable) return null
  if (
    typeof value !== 'string' ||
    !ISO_TIMESTAMP.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) {
    fail('inventory_contract_timestamp_invalid', `正式库存响应中的 ${field} 无效`)
  }
  return value
}

function decimalValue(value, field) {
  if (typeof value !== 'string' || !DECIMAL_QUANTITY.test(value)) {
    fail('inventory_contract_decimal_invalid', `正式库存响应中的 ${field} 无效`)
  }
  return value
}

function nullablePair(object, idField, textField) {
  const id = own(object, idField)
  const label = own(object, textField)
  if (id === null && label === null) return
  uuidValue(id, idField)
  textValue(label, textField)
}

function validateProjection(payload) {
  const object = objectValue(payload, 'response')
  if (own(object, 'schema_version') !== '1.0') {
    fail('inventory_contract_version_unknown', '正式库存响应版本不受支持')
  }
  const projectionStatus = enumValue(
    own(object, 'projection_status'),
    PROJECTION_STATUSES,
    'projection_status'
  )
  const openingStatus = enumValue(
    own(object, 'opening_balance_status'),
    OPENING_STATUSES,
    'opening_balance_status'
  )
  const ledgerCursor = nonnegativeInteger(own(object, 'ledger_cursor'), 'ledger_cursor')
  const projectedAt = timestampValue(own(object, 'projected_at'), 'projected_at', true)
  if (ledgerCursor === 0 && projectedAt !== null) {
    fail('inventory_contract_cursor_time_mismatch', '空账本游标不能带有投影时间')
  }
  if (ledgerCursor > 0 && projectedAt === null) {
    fail('inventory_contract_cursor_time_mismatch', '非空账本游标缺少投影时间')
  }
  if (projectionStatus === 'not_initialized' && ledgerCursor !== 0) {
    fail('inventory_contract_projection_mismatch', '未初始化投影不能包含账本流水')
  }
  if (openingStatus === 'established' && projectionStatus !== 'ready') {
    fail('inventory_contract_opening_mismatch', '期初已建立但投影未就绪')
  }
  return { object, projectionStatus, openingStatus, ledgerCursor, projectedAt }
}

function validateInventorySummary(payload) {
  const projection = validateProjection(payload)
  const object = projection.object
  const scopes = own(object, 'scopes')
  if (!Array.isArray(scopes) || !scopes.length) {
    fail('inventory_contract_scope_missing', '正式库存响应缺少有效数据范围')
  }
  const seenScopes = new Set()
  scopes.forEach((scope, index) => {
    const row = objectValue(scope, `scopes[${index}]`)
    const type = enumValue(own(row, 'scope_type'), SCOPE_TYPES, 'scope_type')
    const id = textValue(own(row, 'scope_id'), 'scope_id')
    if (type === 'national' && id !== '*') {
      fail('inventory_contract_scope_invalid', '全国库存范围必须使用固定标识')
    }
    if (type !== 'national') uuidValue(id, 'scope_id')
    const key = `${type}:${id}`
    if (seenScopes.has(key)) fail('inventory_contract_scope_duplicate', '库存范围存在重复')
    seenScopes.add(key)
  })

  const quantityStatus = enumValue(
    own(object, 'quantity_status'),
    QUANTITY_STATUSES,
    'quantity_status'
  )
  const quantityValues = SUMMARY_QUANTITY_FIELDS.map((field) => own(object, field))
  if (projection.openingStatus === 'not_established') {
    if (
      quantityStatus !== 'opening_not_established' ||
      quantityValues.some((value) => value !== null)
    ) {
      fail('inventory_contract_unopened_quantity', '期初未建立时不得返回库存数量')
    }
  } else if (quantityStatus === 'material_filter_required') {
    if (quantityValues.some((value) => value !== null)) {
      fail('inventory_contract_cross_material_total', '未限定物料时不得返回汇总数量')
    }
  } else {
    fail('inventory_contract_quantity_state_mismatch', '库存数量状态与期初状态不一致')
  }
  if (
    own(object, 'expected_supply_status') !== 'not_available' ||
    own(object, 'expected_supply_qty') !== null
  ) {
    fail('inventory_contract_expected_supply_invalid', '预计供应状态尚不可用')
  }
  return object
}

function validateAccount(account, responseCursor, openingStatus) {
  const row = objectValue(account, 'inventory account')
  ;[
    'stock_account_id',
    'owner_org_id',
    'location_owner_org_id',
    'location_id',
    'material_id'
  ].forEach((field) => uuidValue(own(row, field), field))
  ;[
    'owner_org_code',
    'owner_org_name',
    'location_owner_org_code',
    'location_owner_org_name',
    'location_code',
    'location_name',
    'sku_code',
    'material_name',
    'base_unit'
  ].forEach((field) => textValue(own(row, field), field))
  enumValue(own(row, 'location_type'), LOCATION_TYPES, 'location_type')
  enumValue(own(row, 'tracking_mode'), TRACKING_MODES, 'tracking_mode')
  enumValue(own(row, 'condition_code'), CONDITIONS, 'condition_code')
  enumValue(
    own(row, 'availability_bucket'),
    AVAILABILITY_BUCKETS,
    'availability_bucket'
  )
  const parentId = own(row, 'location_parent_id')
  if (parentId !== null) uuidValue(parentId, 'location_parent_id')
  nullablePair(row, 'custodian_person_id', 'custodian_person_name')
  nullablePair(row, 'lot_id', 'lot_no')
  const trackingMode = row.tracking_mode
  if (
    (trackingMode === 'lot' || trackingMode === 'lot_and_serial') !==
    (row.lot_id !== null)
  ) {
    fail('inventory_contract_lot_mismatch', '批次字段与追踪策略不一致')
  }
  const balanceVersion = nonnegativeInteger(own(row, 'balance_version'), 'balance_version')
  const ledgerCursor = nonnegativeInteger(own(row, 'ledger_cursor'), 'ledger_cursor')
  if (ledgerCursor > responseCursor) {
    fail('inventory_contract_cursor_ahead', '账户游标超过响应账本游标')
  }
  if (ledgerCursor === 0 && balanceVersion !== 0) {
    fail('inventory_contract_balance_version_mismatch', '空账户游标不能带有余额版本')
  }
  const quantityStatus = enumValue(
    own(row, 'quantity_status'),
    ['opening_not_established', 'available'],
    'quantity_status'
  )
  const quantity = own(row, 'quantity')
  if (openingStatus === 'not_established') {
    if (quantityStatus !== 'opening_not_established' || quantity !== null) {
      fail('inventory_contract_unopened_account_quantity', '期初未建立时账户数量必须隐藏')
    }
  } else if (quantityStatus === 'available') {
    decimalValue(quantity, 'quantity')
  } else {
    fail('inventory_contract_account_quantity_state', '账户数量状态与期初状态不一致')
  }
  return row
}

function validatePersonalWarehouse(payload, expectedPersonId) {
  const projection = validateProjection(payload)
  const object = projection.object
  const personId = uuidValue(own(object, 'person_id'), 'person_id')
  if (expectedPersonId && personId !== uuidValue(expectedPersonId, 'expected_person_id')) {
    fail('inventory_contract_person_mismatch', '个人仓响应与当前登录人员不一致')
  }
  const items = own(object, 'items')
  if (!Array.isArray(items)) fail('inventory_contract_items_invalid', '个人仓明细不是数组')

  const locationId = own(object, 'location_id')
  const locationCode = own(object, 'location_code')
  const locationName = own(object, 'location_name')
  const locationStatus = own(object, 'location_status')
  const custodyEffectiveFrom = own(object, 'custody_effective_from')
  if (locationId === null) {
    if (
      locationCode !== null ||
      locationName !== null ||
      locationStatus !== null ||
      custodyEffectiveFrom !== null ||
      items.length !== 0 ||
      projection.openingStatus !== 'not_established'
    ) {
      fail('inventory_contract_personal_location_partial', '未配置个人仓时不得返回部分库位事实')
    }
    return object
  }
  uuidValue(locationId, 'location_id')
  textValue(locationCode, 'location_code')
  textValue(locationName, 'location_name')
  if (locationStatus !== 'active') {
    fail('inventory_contract_personal_location_inactive', '个人仓库位不是有效状态')
  }
  timestampValue(custodyEffectiveFrom, 'custody_effective_from')

  if (projection.openingStatus === 'not_established' && items.length !== 0) {
    fail(
      'inventory_contract_unopened_items',
      '期初未建立时个人仓只能返回库位与保管事实'
    )
  }

  const seenAccounts = new Set()
  items.forEach((item) => {
    const row = validateAccount(item, projection.ledgerCursor, projection.openingStatus)
    if (row.location_id.toLowerCase() !== locationId.toLowerCase()) {
      fail('inventory_contract_location_leak', '个人仓响应包含其他库位账户')
    }
    if (row.location_type !== 'personal') {
      fail('inventory_contract_location_type_mismatch', '个人仓响应包含非个人库位')
    }
    if (
      row.custodian_person_id === null ||
      row.custodian_person_id.toLowerCase() !== personId
    ) {
      fail('inventory_contract_custodian_mismatch', '个人仓账户保管人与当前人员不一致')
    }
    const accountId = row.stock_account_id.toLowerCase()
    if (seenAccounts.has(accountId)) {
      fail('inventory_contract_account_duplicate', '个人仓响应包含重复账户')
    }
    seenAccounts.add(accountId)
  })
  return object
}

function conditionLabel(value) {
  return CONDITION_LABELS[value] || '未知成色'
}

function availabilityLabel(value) {
  return AVAILABILITY_LABELS[value] || '未知状态'
}

module.exports = {
  validateInventorySummary,
  validatePersonalWarehouse,
  conditionLabel,
  availabilityLabel
}
