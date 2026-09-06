// Persist only non-sensitive allocation anchors. Never store keys or payloads.
const contract = require('./material-request-contract')
const STORAGE_KEY = 'rsc_oam_material_request_allocation_sentinel'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const FIELDS = ['v', 'kind', 'trace_request_id', 'person_id', 'authorization_version', 'request_id',
  'request_line_id', 'request_version', 'source_stock_account_id', 'allocated_qty',
  'source_balance_version', 'source_ledger_cursor']

function fail(message) {
  const error = new Error(message)
  error.code = 'material_request_allocation_recovery_blocked'
  error.status = 503
  error.responseReceived = false
  throw error
}
function canonical(value) { return JSON.stringify(FIELDS.map((field) => value[field])) }
function validateSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== FIELDS.slice().sort().join('|')
    || value.v !== 1 || value.kind !== 'material_request_allocation'
    || typeof value.trace_request_id !== 'string' || !/^wxreq-[a-f0-9]{36}$/.test(value.trace_request_id)
    || !['person_id', 'request_id', 'request_line_id', 'source_stock_account_id'].every((key) => typeof value[key] === 'string' && UUID.test(value[key]))
    || typeof value.allocated_qty !== 'string' || !/^[1-9]\d{0,14}\.\d{3}$/.test(value.allocated_qty)
    || !Number.isSafeInteger(value.authorization_version) || value.authorization_version < 1
    || !Number.isSafeInteger(value.request_version) || value.request_version < 0
    || !Number.isSafeInteger(value.source_balance_version) || value.source_balance_version < 0
    || !Number.isSafeInteger(value.source_ledger_cursor) || value.source_ledger_cursor < 0) fail('分配恢复标记无效；已保留原值并停止写入')
  return Object.freeze(Object.assign({}, value))
}
function storage(options) {
  const store = options.storage || (typeof wx === 'undefined' ? null : wx)
  if (!store || !['getStorageSync', 'setStorageSync', 'removeStorageSync'].every((key) => typeof store[key] === 'function')) fail('本地恢复存储不可用；已停止分配写入')
  return store
}
function read(options = {}) {
  let raw
  try { raw = storage(options).getStorageSync(STORAGE_KEY) } catch (_) { fail('无法读取分配恢复标记') }
  return raw === '' || raw === null || raw === undefined ? null : validateSentinel(raw)
}
function persist(value, options = {}) {
  const checked = validateSentinel(value)
  const existing = read(options)
  if (existing) {
    if (canonical(existing) !== canonical(checked)) fail('已有分配操作结果未确认；禁止覆盖或生成新坐标')
    return existing
  }
  try { storage(options).setStorageSync(STORAGE_KEY, checked) } catch (_) { fail('无法持久化分配恢复标记') }
  const reread = read(options)
  if (!reread || canonical(reread) !== canonical(checked)) fail('分配恢复标记回读不一致')
  return reread
}
function clear(expected, options = {}) {
  const checked = validateSentinel(expected)
  if (!read(options) || canonical(read(options)) !== canonical(checked)) fail('分配恢复标记已变化；禁止清除')
  try { storage(options).removeStorageSync(STORAGE_KEY) } catch (_) { fail('分配恢复标记清除失败') }
  if (read(options) !== null) fail('无法确认分配恢复标记已清除')
}
async function recover(sentinel, adapter) {
  const checked = validateSentinel(sentinel)
  const identity = await adapter.loadIdentity()
  const access = await adapter.loadAccess(identity)
  if (identity.person_id !== checked.person_id || identity.authorization_version !== checked.authorization_version
    || access.person_id !== checked.person_id || access.authorization_version !== checked.authorization_version
    || !access.can_read || !access.can_read_allocation_options) fail('分配未决操作身份或权限已变化；保留原坐标')
  const lookup = contract.validateMaterialRequestAllocationCommandStatus(await adapter.allocationCommandStatus(checked.trace_request_id))
  if (lookup.lookup_status !== 'confirmed') return { status: 'pending', access, detail: null }
  const command = lookup.command
  if (command.request_id !== checked.request_id || command.request_line_id !== checked.request_line_id
    || command.request_version !== checked.request_version + 1 || command.source_stock_account_id !== checked.source_stock_account_id
    || command.source_balance_version !== checked.source_balance_version
    || command.source_ledger_cursor !== checked.source_ledger_cursor
    || command.allocated_qty !== checked.allocated_qty) fail('分配历史命令与原请求锚点不一致')
  const detail = contract.validateMaterialRequestDetail(await adapter.detail(checked.request_id))
  const line = detail.lines.find((item) => item.request_line_id === checked.request_line_id)
  if (detail.request_id !== checked.request_id || detail.request_version < command.request_version || !line
    || line.revision_id !== command.revision_id || line.revision_no !== command.revision_no
    || !['approved', 'partially_approved'].includes(line.status)) fail('分配当前详情不能验证历史命令；保持未决状态')
  const freshIdentity = await adapter.loadIdentity()
  const freshAccess = await adapter.loadAccess(freshIdentity)
  if (freshIdentity.person_id !== checked.person_id || freshIdentity.authorization_version !== checked.authorization_version
    || !freshAccess.can_read || !freshAccess.can_read_allocation_options
    || freshAccess.person_id !== checked.person_id || freshAccess.authorization_version !== checked.authorization_version) fail('核验期间分配身份或权限发生变化；保留原坐标')
  return { status: 'confirmed', access: freshAccess, detail, command }
}

module.exports = { STORAGE_KEY, validateSentinel, read, persist, clear, recover }
