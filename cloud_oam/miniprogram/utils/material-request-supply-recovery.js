// Persist only non-sensitive command anchors. Never store command bodies or keys.
const contract = require('./material-request-contract')
const STORAGE_KEY = 'rsc_oam_material_request_supply_sentinel'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const FIELDS = ['v', 'kind', 'trace_request_id', 'person_id', 'authorization_version',
  'request_id', 'request_version', 'revision_id', 'revision_no', 'action',
  'supply_task_id', 'task_version', 'created_at']

function fail(message) {
  const error = new Error(message)
  error.code = 'material_request_supply_recovery_blocked'
  error.status = 503
  error.responseReceived = false
  throw error
}

function canonical(value) {
  return JSON.stringify(FIELDS.map((field) => value[field]))
}

function validateSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== FIELDS.slice().sort().join('|')
    || value.v !== 1 || value.kind !== 'material_request_supply'
    || typeof value.trace_request_id !== 'string' || !/^wxreq-[a-f0-9]{36}$/.test(value.trace_request_id)
    || !['create_supply_task', 'update_supply_task', 'cancel_supply_task'].includes(value.action)
    || !['person_id', 'request_id', 'revision_id'].every((key) => typeof value[key] === 'string' && UUID.test(value[key]))
    || !Number.isSafeInteger(value.authorization_version) || value.authorization_version < 1
    || !Number.isSafeInteger(value.request_version) || value.request_version < 0
    || !Number.isSafeInteger(value.revision_no) || value.revision_no < 1
    || typeof value.created_at !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.created_at)
    || !Number.isFinite(Date.parse(value.created_at))) fail('供给恢复标记无效；已保留原值并停止写入')
  if (value.action === 'create_supply_task') {
    if (value.supply_task_id !== null || value.task_version !== null) fail('新建供给恢复锚点无效')
  } else if (typeof value.supply_task_id !== 'string' || !UUID.test(value.supply_task_id)
    || !Number.isSafeInteger(value.task_version) || value.task_version < 0) fail('供给任务恢复锚点无效')
  return Object.freeze(Object.assign({}, value))
}

function storage(options) {
  const store = options.storage || (typeof wx === 'undefined' ? null : wx)
  if (!store || !['getStorageSync', 'setStorageSync', 'removeStorageSync']
    .every((key) => typeof store[key] === 'function')) fail('本地恢复存储不可用；已停止供给写入')
  return store
}

function read(options = {}) {
  const store = storage(options)
  let raw
  try { raw = store.getStorageSync(STORAGE_KEY) } catch (_) { fail('无法读取供给恢复标记') }
  return raw === '' || raw === null || raw === undefined ? null : validateSentinel(raw)
}

function persist(value, options = {}) {
  const checked = validateSentinel(value)
  const existing = read(options)
  if (existing) {
    if (canonical(existing) !== canonical(checked)) fail('已有供给操作结果未确认；禁止覆盖或生成新坐标')
    return existing
  }
  const store = storage(options)
  try { store.setStorageSync(STORAGE_KEY, checked) } catch (_) { fail('无法持久化供给恢复标记') }
  const reread = read(options)
  if (!reread || canonical(reread) !== canonical(checked)) fail('供给恢复标记回读不一致')
  return reread
}

function clear(expected, options = {}) {
  const checked = validateSentinel(expected)
  const existing = read(options)
  if (!existing || canonical(existing) !== canonical(checked)) fail('供给恢复标记已变化；禁止清除')
  try { storage(options).removeStorageSync(STORAGE_KEY) } catch (_) { fail('供给恢复标记清除失败') }
  if (read(options) !== null) fail('无法确认供给恢复标记已清除')
}

async function verifyIdentity(checked, adapter) {
  const identity = await adapter.loadIdentity()
  const access = await adapter.loadAccess(identity)
  if (identity.person_id !== checked.person_id || identity.authorization_version !== checked.authorization_version
    || access.person_id !== checked.person_id || access.authorization_version !== checked.authorization_version
    || !access.can_read || !access.can_manage_supply) fail('供给未决操作身份或权限已变化；保留原坐标')
  return access
}

async function recover(sentinel, adapter, options = {}) {
  const checked = validateSentinel(sentinel)
  const access = await verifyIdentity(checked, adapter)
  const lookup = contract.validateMaterialRequestSupplyCommandStatus(await adapter.supplyCommandStatus(checked.trace_request_id))
  if (lookup.lookup_status !== 'confirmed') return { status: 'pending', access, detail: null }
  const command = lookup.command
  if (command.request_id !== checked.request_id || command.request_version !== checked.request_version + 1
    || command.revision_id !== checked.revision_id || command.revision_no !== checked.revision_no
    || command.action !== checked.action
    || (checked.supply_task_id !== null && (command.supply_task_id !== checked.supply_task_id
      || command.task_version !== checked.task_version + 1))) fail('供给历史命令与原请求锚点不一致')
  const detail = contract.validateMaterialRequestDetail(await adapter.detail(checked.request_id))
  const task = detail.supply_tasks.find((row) => row.id === command.supply_task_id)
  if (detail.request_id !== checked.request_id || detail.request_version < command.request_version
    || detail.current_revision_id !== checked.revision_id || !task || task.task_no !== command.task_no
    || task.version < command.task_version
    || (task.version === command.task_version && task.status !== command.task_status)) {
    fail('供给当前详情不能验证历史命令；保持未决状态')
  }
  const freshAccess = await verifyIdentity(checked, adapter)
  // The page must check its generation before synchronously clearing the marker.
  // Read-only recovery must not erase durable state if its caller was unloaded.
  return { status: 'confirmed', access: freshAccess, detail, command }
}

module.exports = { STORAGE_KEY, validateSentinel, read, persist, clear, verifyIdentity, recover }
