const api = require('./api')
const { projectAccess } = require('./formal-stocktake-adapter')
const { validateFormalStocktakeDetail } = require('./formal-stocktake-contract')
const { validateFormalStocktakeCountSentinel } = require('./formal-stocktake-count-recovery-store')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NO_STORE = Object.freeze({ method: 'GET', noRefresh: true, header: Object.freeze({ 'Cache-Control': 'no-store', Pragma: 'no-cache' }) })

function fail(message = '日常盘点范围计数恢复证据与原请求不一致，继续保持待核验') {
  const error = new Error(message)
  error.name = 'FormalStocktakeCountRecoveryError'
  error.status = 409
  error.code = 'formal_stocktake_count_recovery_unconfirmed'
  throw error
}

function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== fields.length || fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) fail()
  return value
}

function checkedIdentity(value) {
  const row = exact(value, ['person_id', 'authorization_version'])
  if (typeof row.person_id !== 'string' || !UUID.test(row.person_id) || !Number.isSafeInteger(row.authorization_version) || row.authorization_version < 1) fail()
  return Object.freeze({ person_id: row.person_id.toLowerCase(), authorization_version: row.authorization_version })
}

function sameIdentity(value, expected) {
  const current = checkedIdentity(value)
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) fail('登录人员或授权版本已变化，原范围计数继续保持待核验')
  return current
}

function activeIdentity(value, expected) {
  const row = exact(value, ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes'])
  const identity = sameIdentity({ person_id: row.person_id, authorization_version: row.authorization_version }, expected)
  if (row.account_status !== 'active' || row.employment_status !== 'active' || row.access_mode !== 'active') fail('当前身份不能核验日常盘点范围计数')
  for (const [field, limit] of [['name', 160], ['employee_no', 80], ['organization_code', 120], ['organization_name', 240]]) {
    if (typeof row[field] !== 'string' || !row[field] || row[field].trim() !== row[field] || row[field].length > limit || /[\u0000-\u001f\u007f]/.test(row[field])) fail()
  }
  if (!Array.isArray(row.role_codes) || !row.role_codes.length || new Set(row.role_codes).size !== row.role_codes.length
    || row.role_codes.some((role) => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(role))
    || !row.role_codes.some((role) => ['admin', 'provincial_manager', 'technician'].includes(role))) fail()
  return identity
}

function requireReadAndCount(access, expected) {
  if (!access || typeof access !== 'object') fail()
  sameIdentity({ person_id: access.person_id, authorization_version: access.authorization_version }, expected)
  if (access.schema_version !== '1.0' || access.can_read !== true || access.can_count !== true) fail('当前正式权限不能核验原范围计数')
}

function createFormalStocktakeCountRecoveryAdapter(expectedIdentity, transport = api) {
  const expected = checkedIdentity(expectedIdentity)
  if (!transport || typeof transport.request !== 'function') fail('日常盘点只读核验通道不可用')
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await transport.request('/auth/me', NO_STORE), expected) },
    async loadAccess() { return projectAccess(await transport.request('/access/context', NO_STORE), expected) },
    commandStatus(value) {
      const sentinel = validateFormalStocktakeCountSentinel(value)
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected)
      const query = `operation=${encodeURIComponent(sentinel.operation)}`
        + `&actor_person_id=${encodeURIComponent(sentinel.actor_person_id)}`
        + `&actor_authorization_version=${sentinel.actor_authorization_version}`
        + `&trace_request_id=${encodeURIComponent(sentinel.trace_request_id)}`
      return transport.request(`/v1/stocktakes/${sentinel.task_id}/rounds/${sentinel.round_id}/scopes/${sentinel.scope_id}/count-command-status?${query}`, NO_STORE)
    },
    detail(id) {
      if (typeof id !== 'string' || !UUID.test(id)) fail()
      return transport.request(`/v1/stocktakes/${id.toLowerCase()}`, NO_STORE)
    }
  })
}

// Bind recovery to the exact adapter/transport that created the write intent.
// This avoids silently switching to the module-global API session and keeps the
// full /auth/me validation used by the direct transport variant above.
function createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(expectedIdentity, adapter) {
  const expected = checkedIdentity(expectedIdentity)
  if (!adapter || typeof adapter.loadIdentityNoReplay !== 'function'
    || typeof adapter.loadAccessNoReplay !== 'function'
    || typeof adapter.detailNoReplay !== 'function'
    || typeof adapter.countCommandStatus !== 'function') fail('日常盘点只读核验通道不可用')
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await adapter.loadIdentityNoReplay(), expected) },
    async loadAccess() {
      const access = await adapter.loadAccessNoReplay()
      requireReadAndCount(access, expected)
      return access
    },
    commandStatus(value) {
      const sentinel = validateFormalStocktakeCountSentinel(value)
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected)
      return adapter.countCommandStatus(
        sentinel.task_id, sentinel.round_id, sentinel.scope_id, sentinel.operation,
        sentinel.actor_person_id, sentinel.actor_authorization_version, sentinel.trace_request_id
      )
    },
    detail(id) {
      if (typeof id !== 'string' || !UUID.test(id)) fail()
      return adapter.detailNoReplay(id.toLowerCase())
    }
  })
}

function validateFormalStocktakeCountCommandStatus(value, original) {
  const sentinel = validateFormalStocktakeCountSentinel(original)
  const row = exact(value, ['schema_version', 'task_id', 'round_id', 'scope_id', 'actor_person_id', 'actor_authorization_version', 'trace_request_id', 'operation', 'lookup_status', 'command'])
  if (row.schema_version !== '1.0' || String(row.task_id).toLowerCase() !== sentinel.task_id
    || String(row.round_id).toLowerCase() !== sentinel.round_id || String(row.scope_id).toLowerCase() !== sentinel.scope_id
    || String(row.actor_person_id).toLowerCase() !== sentinel.actor_person_id || row.actor_authorization_version !== sentinel.actor_authorization_version
    || row.trace_request_id !== sentinel.trace_request_id || row.operation !== sentinel.operation) fail()
  const anchors = { schema_version: '1.0', task_id: sentinel.task_id, round_id: sentinel.round_id, scope_id: sentinel.scope_id, actor_person_id: sentinel.actor_person_id, actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id, operation: sentinel.operation }
  if (row.lookup_status === 'not_observed') {
    if (row.command !== null) fail()
    return Object.freeze(Object.assign(anchors, { lookup_status: 'not_observed', command: null }))
  }
  if (row.lookup_status !== 'confirmed') fail()
  const command = exact(row.command, ['completion_id', 'round_no', 'completed_at', 'scope_completed', 'caused_round_submission'])
  if (typeof command.completion_id !== 'string' || !UUID.test(command.completion_id) || command.round_no !== sentinel.round_no || command.scope_completed !== true
    || typeof command.caused_round_submission !== 'boolean' || typeof command.completed_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(command.completed_at) || !Number.isFinite(Date.parse(command.completed_at))) fail()
  return Object.freeze(Object.assign(anchors, { lookup_status: 'confirmed', command: Object.freeze({ completion_id: command.completion_id.toLowerCase(), round_no: command.round_no, completed_at: command.completed_at, scope_completed: true, caused_round_submission: command.caused_round_submission }) }))
}

function validateFormalStocktakeCountRecoveredProjection(value, sentinel, command) {
  const detail = validateFormalStocktakeDetail(value)
  if (detail.task_id !== sentinel.task_id || detail.version < sentinel.expected_task_version + 1) fail('当前盘点详情未承接原范围计数版本')
  const round = detail.rounds.find((item) => item.round_id === sentinel.round_id)
  const scope = detail.scopes.find((item) => item.scope_id === sentinel.scope_id)
  if (!round || !scope || round.round_no !== sentinel.round_no || round.round_type !== (sentinel.operation === 'initial_count' ? 'initial' : 'recount') || scope.assigned_to_me !== true) fail()
  const completion = round.visible_scope_completions.find((item) => item.scope_id === sentinel.scope_id)
  if (!completion || completion.completion_id !== command.completion_id || completion.completed_by_person_id !== sentinel.actor_person_id || completion.completed_at !== command.completed_at) fail('当前盘点详情未确认原范围完成事实')
  if (command.caused_round_submission && (round.status !== 'submitted' || round.submitted_at !== command.completed_at)) fail('轮次封存事实与历史范围完成不一致')
  if (!command.caused_round_submission && round.status !== 'counting' && round.status !== 'submitted') fail()
  return detail
}

async function recoverFormalStocktakeCount(lease, value, adapter, canCommit = () => true) {
  const sentinel = validateFormalStocktakeCountSentinel(value)
  const stored = lease.read()
  if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail()
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }
  sameIdentity(await adapter.loadIdentity(), expected)
  requireReadAndCount(await adapter.loadAccess(), expected)
  const status = validateFormalStocktakeCountCommandStatus(await adapter.commandStatus(sentinel), sentinel)
  if (status.lookup_status !== 'confirmed') fail('暂未查到原范围计数的确定结果，继续保留恢复记录；不能据此重新提交')
  const detail = validateFormalStocktakeCountRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command)
  sameIdentity(await adapter.loadIdentity(), expected)
  requireReadAndCount(await adapter.loadAccess(), expected)
  if (!canCommit()) fail('核验页面已变化，继续保留日常盘点恢复记录')
  lease.clearExact(sentinel)
  return Object.freeze({ command: status.command, detail })
}

module.exports = {
  createFormalStocktakeCountRecoveryAdapter,
  createFormalStocktakeCountRecoveryAdapterFromFormalAdapter,
  validateFormalStocktakeCountCommandStatus,
  validateFormalStocktakeCountRecoveredProjection,
  recoverFormalStocktakeCount,
  requireReadAndCount
}
