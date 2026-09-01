const STORAGE_KEY = 'rsc_oam_material_request_lifecycle_sentinel'
const SENTINEL_KIND = 'material_request_lifecycle'
const SENTINEL_VERSION = 1
const SAFE_REQUEST_ID = /^wxreq-[a-f0-9]{36}$/
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

function recoveryError(message, code = 'material_request_lifecycle_recovery_storage_error') {
  const error = new Error(message)
  error.status = 503
  error.code = code
  error.responseReceived = false
  return error
}

function exactObject(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw recoveryError(
      '需求终止恢复哨兵已损坏，已保留原值并阻止新的撤回或取消',
      'material_request_lifecycle_recovery_sentinel_invalid'
    )
  }
  const actual = Object.keys(value).sort()
  const expected = keys.slice().sort()
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw recoveryError(
      '需求终止恢复哨兵字段不完整或含未知字段，已保留并失败关闭',
      'material_request_lifecycle_recovery_sentinel_invalid'
    )
  }
  return value
}

function validateLifecycleSentinel(value) {
  const object = exactObject(value, ['v', 'kind', 'x_request_id', 'created_at'])
  if (object.v !== SENTINEL_VERSION || object.kind !== SENTINEL_KIND) {
    throw recoveryError(
      '需求终止恢复哨兵版本或类型无效，已保留并失败关闭',
      'material_request_lifecycle_recovery_sentinel_invalid'
    )
  }
  if (typeof object.x_request_id !== 'string' || !SAFE_REQUEST_ID.test(object.x_request_id)) {
    throw recoveryError(
      '需求终止恢复哨兵的请求标识无效，已保留并失败关闭',
      'material_request_lifecycle_recovery_sentinel_invalid'
    )
  }
  if (
    typeof object.created_at !== 'string' ||
    !AWARE_TIMESTAMP.test(object.created_at) ||
    !Number.isFinite(Date.parse(object.created_at))
  ) {
    throw recoveryError(
      '需求终止恢复哨兵时间无效，已保留并失败关闭',
      'material_request_lifecycle_recovery_sentinel_invalid'
    )
  }
  return Object.freeze({
    v: SENTINEL_VERSION,
    kind: SENTINEL_KIND,
    x_request_id: object.x_request_id,
    created_at: object.created_at
  })
}

function storageApi(options = {}) {
  const storage = options.storage || (typeof wx !== 'undefined' ? wx : null)
  if (
    !storage ||
    typeof storage.getStorageSync !== 'function' ||
    typeof storage.setStorageSync !== 'function' ||
    typeof storage.removeStorageSync !== 'function'
  ) {
    throw recoveryError('小程序本地存储不可用，已阻止需求撤回或取消')
  }
  return storage
}

function hasStoredValue(value) {
  return value !== '' && value !== null && value !== undefined
}

function canonicalValue(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalValue).join(',')}]`
  return `{${Object.keys(value).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalValue(value[key])}`
  ).join(',')}}`
}

function readLifecycleSentinel(options = {}) {
  const storage = storageApi(options)
  let raw
  try {
    raw = storage.getStorageSync(STORAGE_KEY)
  } catch (_) {
    throw recoveryError('无法读取需求终止恢复哨兵，已失败关闭')
  }
  return hasStoredValue(raw) ? validateLifecycleSentinel(raw) : null
}

function persistLifecycleSentinel(xRequestId, options = {}) {
  if (typeof xRequestId !== 'string' || !SAFE_REQUEST_ID.test(xRequestId)) {
    throw recoveryError(
      '不能为无效请求标识创建需求终止恢复哨兵',
      'material_request_lifecycle_recovery_request_id_invalid'
    )
  }
  const storage = storageApi(options)
  let existing
  try {
    existing = storage.getStorageSync(STORAGE_KEY)
  } catch (_) {
    throw recoveryError('无法确认是否已有需求终止恢复哨兵，已失败关闭')
  }
  if (hasStoredValue(existing)) {
    const checked = validateLifecycleSentinel(existing)
    if (checked.x_request_id !== xRequestId) {
      throw recoveryError(
        '已有另一笔需求终止操作等待核验，禁止覆盖恢复哨兵或生成新坐标',
        'material_request_lifecycle_recovery_conflict'
      )
    }
    return checked
  }

  const now = typeof options.now === 'function' ? options.now() : new Date()
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw recoveryError('无法生成需求终止恢复哨兵时间')
  }
  const sentinel = Object.freeze({
    v: SENTINEL_VERSION,
    kind: SENTINEL_KIND,
    x_request_id: xRequestId,
    created_at: now.toISOString()
  })
  try {
    storage.setStorageSync(STORAGE_KEY, sentinel)
    const reread = storage.getStorageSync(STORAGE_KEY)
    const checked = validateLifecycleSentinel(reread)
    if (canonicalValue(checked) !== canonicalValue(sentinel)) {
      throw recoveryError('需求终止恢复哨兵回读不一致，已失败关闭')
    }
    return checked
  } catch (error) {
    if (error && error.code) throw error
    throw recoveryError('无法确认需求终止恢复哨兵已持久化，已失败关闭')
  }
}

function clearLifecycleSentinel(xRequestId, options = {}) {
  if (typeof xRequestId !== 'string' || !SAFE_REQUEST_ID.test(xRequestId)) {
    throw recoveryError(
      '不能清理无效请求标识对应的需求终止恢复哨兵',
      'material_request_lifecycle_recovery_request_id_invalid'
    )
  }
  const storage = storageApi(options)
  let checked
  try {
    const raw = storage.getStorageSync(STORAGE_KEY)
    if (!hasStoredValue(raw)) {
      throw recoveryError(
        '需求终止恢复哨兵已丢失，不能确认本地恢复状态',
        'material_request_lifecycle_recovery_sentinel_missing'
      )
    }
    checked = validateLifecycleSentinel(raw)
    if (checked.x_request_id !== xRequestId) {
      throw recoveryError(
        '需求终止恢复哨兵与待清理请求不一致，已保留并失败关闭',
        'material_request_lifecycle_recovery_conflict'
      )
    }
    storage.removeStorageSync(STORAGE_KEY)
    if (hasStoredValue(storage.getStorageSync(STORAGE_KEY))) {
      throw recoveryError('无法确认需求终止恢复哨兵已清理，已失败关闭')
    }
  } catch (error) {
    if (error && error.code) throw error
    throw recoveryError('无法安全清理需求终止恢复哨兵，已失败关闭')
  }
  return checked
}

module.exports = {
  STORAGE_KEY,
  SENTINEL_KIND,
  SENTINEL_VERSION,
  validateLifecycleSentinel,
  readLifecycleSentinel,
  persistLifecycleSentinel,
  clearLifecycleSentinel
}
