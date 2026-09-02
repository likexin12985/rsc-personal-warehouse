const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const STATUSES = new Set(['pending', 'active'])
const SCHEMA_VERSION = '1.0'
const SOURCE_SYSTEM_CODE = 'starcharge_oam'

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

function positiveVersion(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) fail(`${name}无效`)
  return value
}

function textValue(value, name, maximum) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > maximum ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) fail(`${name}无效`)
  return value
}

function awareTime(value, name) {
  const checked = textValue(value, name, 80)
  if (!AWARE_TIMESTAMP.test(checked) || !Number.isFinite(Date.parse(checked))) {
    fail(`${name}无效`)
  }
  return checked
}

function validateContext(personId, authorizationVersion, expectedContext) {
  if (!expectedContext || typeof expectedContext !== 'object') {
    fail('正式工单选项缺少本地授权锚点')
  }
  const checkedPersonId = uuidValue(personId, 'person_id')
  const checkedAuthorizationVersion = positiveVersion(
    authorizationVersion,
    'authorization_version'
  )
  if (
    checkedPersonId !== uuidValue(expectedContext.person_id, 'expected_person_id') ||
    checkedAuthorizationVersion !== positiveVersion(
      expectedContext.authorization_version,
      'expected_authorization_version'
    )
  ) fail('正式工单选项的人员或授权版本已变化')
  return {
    person_id: checkedPersonId,
    authorization_version: checkedAuthorizationVersion
  }
}

function validateItem(value) {
  const object = exactObject(value, [
    'work_order_id', 'work_order_no', 'status', 'source_system_code',
    'source_external_id', 'source_version', 'source_updated_at', 'synced_at',
    'freshness_status'
  ], '正式工单选项')
  if (!STATUSES.has(object.status)) fail('正式工单选项状态无效')
  if (object.source_system_code !== SOURCE_SYSTEM_CODE) {
    fail('正式工单选项来源系统无效')
  }
  if (object.freshness_status !== 'fresh') {
    fail('正式工单选项新鲜度无效')
  }
  const sourceUpdatedAt = awareTime(object.source_updated_at, 'source_updated_at')
  const syncedAt = awareTime(object.synced_at, 'synced_at')
  if (Date.parse(sourceUpdatedAt) > Date.parse(syncedAt)) {
    fail('正式工单来源更新时间不能晚于本次同步时间')
  }
  return Object.freeze({
    work_order_id: uuidValue(object.work_order_id, 'work_order_id'),
    work_order_no: textValue(object.work_order_no, 'work_order_no', 100),
    status: object.status,
    source_system_code: SOURCE_SYSTEM_CODE,
    source_external_id: textValue(object.source_external_id, 'source_external_id', 250),
    source_version: textValue(object.source_version, 'source_version', 160),
    source_updated_at: sourceUpdatedAt,
    synced_at: syncedAt,
    freshness_status: 'fresh'
  })
}

function validatePage(value, expectedContext) {
  const object = exactObject(value, [
    'schema_version', 'person_id', 'authorization_version', 'items', 'next_after_id'
  ], '正式工单选项分页')
  if (object.schema_version !== SCHEMA_VERSION) {
    fail('正式工单选项分页版本不受支持')
  }
  const context = validateContext(
    object.person_id,
    object.authorization_version,
    expectedContext
  )
  if (!Array.isArray(object.items) || object.items.length > 100) {
    fail('正式工单选项items无效')
  }
  const items = object.items.map(validateItem)
  if (
    new Set(items.map((item) => item.work_order_id)).size !== items.length ||
    new Set(items.map((item) => item.work_order_no)).size !== items.length
  ) fail('正式工单选项分页包含重复工单')
  const nextAfterId = object.next_after_id === null
    ? null
    : uuidValue(object.next_after_id, 'next_after_id')
  if (nextAfterId && items.some((item) => item.work_order_id === nextAfterId)) {
    fail('正式工单选项游标指向当前页对象')
  }
  return Object.freeze({
    schema_version: SCHEMA_VERSION,
    person_id: context.person_id,
    authorization_version: context.authorization_version,
    items: Object.freeze(items),
    next_after_id: nextAfterId
  })
}

function validateDetail(value, expectedContext, expectedWorkOrderId) {
  const object = exactObject(value, [
    'schema_version', 'person_id', 'authorization_version', 'item'
  ], '正式工单选项详情')
  if (object.schema_version !== SCHEMA_VERSION) {
    fail('正式工单选项详情版本不受支持')
  }
  const context = validateContext(
    object.person_id,
    object.authorization_version,
    expectedContext
  )
  const item = validateItem(object.item)
  if (item.work_order_id !== uuidValue(expectedWorkOrderId, 'expected_work_order_id')) {
    fail('正式工单选项详情与目标工单不一致')
  }
  return Object.freeze({
    schema_version: SCHEMA_VERSION,
    person_id: context.person_id,
    authorization_version: context.authorization_version,
    item
  })
}

function validateQuery(value) {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    value.length > 100 ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) fail('工单检索词无效')
  return value
}

module.exports = {
  SCHEMA_VERSION,
  validateItem,
  validatePage,
  validateDetail,
  validateQuery,
  validateWorkOrderId: (value) => uuidValue(value, 'work_order_id')
}
