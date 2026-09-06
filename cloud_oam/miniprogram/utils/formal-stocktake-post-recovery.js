// Non-opening posting: one durable coordinate, one POST at most, then only
// historical command-status and current-detail GETs can release its marker.
const { validateFormalStocktakeDetail, confirmFormalStocktakePostProjection } = require('./formal-stocktake-contract')

const { validateFormalStocktakePostSentinel, getFormalStocktakePostRecoveryStore } = require('./formal-stocktake-post-recovery-store')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/

function fail(message = '日常盘点过账仍待只读核验，继续保持待核验') {
  const error = new Error(message)
  error.name = 'FormalStocktakePostRecoveryError'
  error.status = 409
  error.code = 'formal_stocktake_post_recovery_unconfirmed'
  throw error
}

function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== fields.length
    || fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) fail()
  return value
}
function uuid(value, name) {
  if (typeof value !== 'string' || !UUID.test(value)) fail(`${name}无效`)
  return value.toLowerCase()
}
function positive(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) fail(`${name}无效`)
  return value
}
function nonNegative(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${name}无效`)
  return value
}
// Keep UTC seconds and microseconds separate; WeChat need not support BigInt.
function instant(value) {
  if (typeof value !== 'string') fail('时间戳无效')
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value)
  if (!match) fail('时间戳无效')
  const base = Date.parse(`${match[1]}Z`)
  const hours = Number(match[5] || 0); const minutes = Number(match[6] || 0)
  if (!Number.isFinite(base) || new Date(base).toISOString().slice(0, 19) !== match[1] || hours > 23 || minutes > 59) fail('时间戳无效')
  const seconds = base / 1000 - (hours * 60 + minutes) * 60 * (match[4] === '-' ? -1 : 1)
  if (!Number.isSafeInteger(seconds)) fail('时间戳无效')
  return [seconds, Number((match[2] || '').padEnd(6, '0'))]
}
function timestamp(value) { instant(value); return value }
function sameInstant(left, right) {
  const a = instant(left); const b = instant(right)
  return a[0] === b[0] && a[1] === b[1]
}

function validateFormalStocktakePostCommandStatus(value, original) {
  const sentinel = validateFormalStocktakePostSentinel(original)
  const row = exact(value, [
    'schema_version', 'task_id', 'actor_person_id', 'actor_authorization_version',
    'trace_request_id', 'operation', 'lookup_status', 'command',
  ])
  if (row.schema_version !== '1.0' || uuid(row.task_id, 'task_id') !== sentinel.task_id
    || uuid(row.actor_person_id, 'actor_person_id') !== sentinel.actor_person_id
    || positive(row.actor_authorization_version, 'actor_authorization_version') !== sentinel.actor_authorization_version
    || row.trace_request_id !== sentinel.trace_request_id || row.operation !== 'post_differences') fail()
  const anchors = { schema_version: '1.0', task_id: sentinel.task_id, actor_person_id: sentinel.actor_person_id, actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id, operation: 'post_differences' }
  if (row.lookup_status === 'not_observed') {
    if (row.command !== null) fail()
    return Object.freeze({ ...anchors, lookup_status: 'not_observed', command: null })
  }
  if (row.lookup_status === 'sealed_not_executed') {
    const command = exact(row.command, [
      'seal_id', 'task_id', 'expected_task_version', 'actor_person_id',
      'actor_authorization_version', 'trace_request_id', 'sealed_at',
    ])
    const checked = Object.freeze({
      seal_id: uuid(command.seal_id, 'seal_id'),
      task_id: uuid(command.task_id, 'command.task_id'),
      expected_task_version: nonNegative(command.expected_task_version, 'expected_task_version'),
      actor_person_id: uuid(command.actor_person_id, 'command.actor_person_id'),
      actor_authorization_version: positive(command.actor_authorization_version, 'command.actor_authorization_version'),
      trace_request_id: command.trace_request_id,
      sealed_at: timestamp(command.sealed_at, 'sealed_at'),
    })
    if (checked.task_id !== sentinel.task_id
      || checked.expected_task_version !== sentinel.expected_task_version
      || checked.actor_person_id !== sentinel.actor_person_id
      || checked.actor_authorization_version !== sentinel.actor_authorization_version
      || checked.trace_request_id !== sentinel.trace_request_id) fail('封存命令坐标与原过账意图不一致')
    return Object.freeze({ ...anchors, lookup_status: 'sealed_not_executed', command: checked })
  }
  if (row.lookup_status !== 'confirmed') fail()
  const command = exact(row.command, [
    'completion_id', 'task_id', 'terminal_round_id', 'resulting_task_status', 'task_version',
    'scope_count', 'difference_count', 'accepted_difference_count', 'no_adjustment_count',
    'transaction_count', 'movement_count', 'total_quantity', 'first_ledger_cursor',
    'last_ledger_cursor', 'posted_at',
  ])
  const checked = Object.freeze({
    completion_id: uuid(command.completion_id, 'completion_id'),
    task_id: uuid(command.task_id, 'command.task_id'),
    terminal_round_id: uuid(command.terminal_round_id, 'terminal_round_id'),
    resulting_task_status: command.resulting_task_status,
    task_version: positive(command.task_version, 'task_version'),
    scope_count: positive(command.scope_count, 'scope_count'),
    difference_count: nonNegative(command.difference_count, 'difference_count'),
    accepted_difference_count: nonNegative(command.accepted_difference_count, 'accepted_difference_count'),
    no_adjustment_count: nonNegative(command.no_adjustment_count, 'no_adjustment_count'),
    transaction_count: nonNegative(command.transaction_count, 'transaction_count'),
    movement_count: nonNegative(command.movement_count, 'movement_count'),
    total_quantity: command.total_quantity,
    first_ledger_cursor: command.first_ledger_cursor === null ? null : positive(command.first_ledger_cursor, 'first_ledger_cursor'),
    last_ledger_cursor: command.last_ledger_cursor === null ? null : positive(command.last_ledger_cursor, 'last_ledger_cursor'),
    posted_at: timestamp(command.posted_at, 'posted_at'),
  })
  if (checked.task_id !== sentinel.task_id || checked.resulting_task_status !== 'posted'
    || typeof checked.total_quantity !== 'string' || !QUANTITY.test(checked.total_quantity)
    || checked.accepted_difference_count + checked.no_adjustment_count !== checked.difference_count
    || checked.movement_count !== checked.accepted_difference_count
    || (checked.transaction_count === 0
      ? checked.first_ledger_cursor !== null || checked.last_ledger_cursor !== null || checked.movement_count !== 0 || checked.total_quantity !== '0.000'
      : checked.first_ledger_cursor === null || checked.last_ledger_cursor === null
        || checked.last_ledger_cursor - checked.first_ledger_cursor + 1 !== checked.transaction_count
        || checked.movement_count <= 0 || checked.total_quantity === '0.000')
    || checked.task_version !== sentinel.expected_task_version + 1) fail('盘点过账历史命令数量或版本无效')
  return Object.freeze({ ...anchors, lookup_status: 'confirmed', command: checked })
}

function validateFormalStocktakePostRecoveredProjection(value, sentinelValue, commandValue) {
  const sentinel = validateFormalStocktakePostSentinel(sentinelValue)
  const command = validateFormalStocktakePostCommandStatus({
    schema_version: '1.0', task_id: sentinel.task_id, actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id,
    operation: 'post_differences', lookup_status: 'confirmed', command: commandValue,
  }, sentinel).command
  const detail = validateFormalStocktakeDetail(value)
  if (detail.task_id !== sentinel.task_id || detail.version < command.task_version
    || detail.posted_at === null || !sameInstant(detail.posted_at, command.posted_at)) fail('当前盘点详情未承接原过账版本与时间')
  confirmFormalStocktakePostProjection(command, detail, sentinel.expected_task_version, true)
  return detail
}

function identity(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.person_id !== 'string'
    || !UUID.test(value.person_id) || !Number.isSafeInteger(value.authorization_version) || value.authorization_version < 1) fail('当前正式身份无效')
  return Object.freeze({ person_id: value.person_id.toLowerCase(), authorization_version: value.authorization_version })
}
function sameIdentity(value, expected) {
  const current = identity(value)
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) fail('登录人员或授权版本已变化，原过账继续保持待核验')
  return current
}
function activeIdentity(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('当前身份不能核验盘点过账')
  const fields = ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes']
  if (Object.keys(value).length !== fields.length || fields.some((field) => !Object.prototype.hasOwnProperty.call(value, field))) fail('正式身份字段不完整')
  const current = sameIdentity(value, expected)
  if (value.account_status !== 'active' || value.employment_status !== 'active' || value.access_mode !== 'active') fail('当前身份不能核验盘点过账')
  for (const [field, limit] of [['name', 160], ['employee_no', 80], ['organization_code', 120], ['organization_name', 240]]) {
    const text = value[field]
    if (typeof text !== 'string' || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail('当前身份字段无效')
  }
  const roles = value.role_codes
  if (!Array.isArray(roles) || !roles.length || new Set(roles).size !== roles.length
    || roles.some((role) => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(role))
    || !roles.includes('admin')) fail('当前身份角色不能核验盘点过账')
  return current
}
function requirePostAccess(access, expected) {
  sameIdentity(access, expected)
  if (!access || access.schema_version !== '1.0' || access.can_read !== true || access.can_post !== true) fail('当前正式权限不能核验盘点过账')
}
function sentinelFromIntent(intent, expected) {
  if (!intent || intent.method !== 'POST' || intent.action !== 'post' || !intent.taskId
    || !Number.isSafeInteger(intent.expectedTaskVersion) || intent.expectedTaskVersion < 0
    || !intent.headers || typeof intent.headers['X-Request-ID'] !== 'string' || !TRACE.test(intent.headers['X-Request-ID'])) fail('日常盘点过账意图无效')
  const body = exact(intent.body, ['expected_task_version'])
  if (intent.path !== `/v1/stocktakes/${uuid(intent.taskId, 'task_id')}/post-differences` || !/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/.test(intent.headers['Idempotency-Key'] || '') || (intent.roundId != null) || (intent.scopeId != null)) fail('过账写路径或坐标无效')
  if (body.expected_task_version !== intent.expectedTaskVersion) fail('过账版本坐标不一致')
  return validateFormalStocktakePostSentinel({ v: 1, kind: 'formal_stocktake_post', task_id: intent.taskId, expected_task_version: intent.expectedTaskVersion, actor_person_id: expected.person_id, actor_authorization_version: expected.authorization_version, trace_request_id: intent.headers['X-Request-ID'] })
}
function createFormalStocktakePostRecoveryAdapterFromFormalAdapter(expectedIdentity, adapter) {
  const expected = identity(expectedIdentity)
  if (!adapter || typeof adapter.loadIdentityNoReplay !== 'function' || typeof adapter.loadAccessNoReplay !== 'function' || typeof adapter.detailNoReplay !== 'function' || typeof adapter.postingCommandStatus !== 'function') fail('日常盘点过账只读核验通道不可用')
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await adapter.loadIdentityNoReplay(), expected) },
    async loadAccess() { const access = await adapter.loadAccessNoReplay(); requirePostAccess(access, expected); return access },
    async commandStatus(sentinel) {
      const checked = validateFormalStocktakePostSentinel(sentinel)
      sameIdentity({ person_id: checked.actor_person_id, authorization_version: checked.actor_authorization_version }, expected)
      return adapter.postingCommandStatus(checked.task_id, checked.actor_person_id, checked.actor_authorization_version, checked.trace_request_id)
    },
    async detail(taskId) {
      if (typeof taskId !== 'string' || !UUID.test(taskId)) fail('task_id 无效')
      return adapter.detailNoReplay(taskId.toLowerCase())
    }
  })
}
async function recoverFormalStocktakePost(lease, value, adapter, canCommit = () => true) {
  const sentinel = validateFormalStocktakePostSentinel(value)
  const stored = lease.read()
  if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail()
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }
  sameIdentity(await adapter.loadIdentity(), expected); requirePostAccess(await adapter.loadAccess(), expected)
  const status = validateFormalStocktakePostCommandStatus(await adapter.commandStatus(sentinel), sentinel)
  if (status.lookup_status !== 'confirmed') fail('暂未查到原盘点过账的确定结果；不能据此重新提交')
  const detail = validateFormalStocktakePostRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command)
  sameIdentity(await adapter.loadIdentity(), expected); requirePostAccess(await adapter.loadAccess(), expected)
  if (!canCommit()) fail('核验页面已变化，继续保留盘点过账恢复记录')
  lease.clearExact(sentinel)
  return Object.freeze({ command: status.command, detail })
}
class FormalStocktakePostSubmissionPendingError extends Error {
  constructor(sentinel) {
    super('原日常盘点过账仍待只读核验，请保留恢复记录；不能重新提交')
    this.name = 'FormalStocktakePostSubmissionPendingError'; this.status = 409; this.responseReceived = false
    this.task_id = sentinel.task_id; this.expected_task_version = sentinel.expected_task_version
    this.actor_person_id = sentinel.actor_person_id; this.actor_authorization_version = sentinel.actor_authorization_version; this.trace_request_id = sentinel.trace_request_id
  }
}
async function submitDurableFormalStocktakePost(options) {
  const { intent, expectedIdentity, adapter, store = getFormalStocktakePostRecoveryStore(), canCommit = () => true } = options || {}
  const expected = identity(expectedIdentity)
  if (!adapter || typeof adapter.execute !== 'function') fail('盘点过账单次提交通道不可用')
  if (!store || typeof store.withTaskLease !== 'function') fail('盘点过账持久恢复存储不可用')
  const sentinel = sentinelFromIntent(intent, expected)
  const recoveryAdapter = createFormalStocktakePostRecoveryAdapterFromFormalAdapter(expected, adapter)
  return store.withTaskLease(sentinel.task_id, async (lease) => {
    const existing = lease.read()
    if (existing.kind === 'corrupt' || existing.kind === 'unavailable') fail('盘点过账恢复记录不可用，已停止写入')
    if (existing.kind === 'valid') {
      try { return Object.freeze({ recovered: true, ...(await recoverFormalStocktakePost(lease, existing.value, recoveryAdapter, canCommit)) }) }
      catch (_) { throw new FormalStocktakePostSubmissionPendingError(existing.value) }
    }
    await recoveryAdapter.loadIdentity(); await recoveryAdapter.loadAccess()
    if (!canCommit()) fail('当前盘点页面已变化，未发送过账请求')
    let persisted = false
    const beforeWrite = async () => {
      if (persisted) throw new FormalStocktakePostSubmissionPendingError(sentinel)
      await recoveryAdapter.loadIdentity(); await recoveryAdapter.loadAccess()
      if (!canCommit()) fail('当前盘点页面已变化，未发送过账请求')
      lease.persist(sentinel)
      persisted = true
      const stored = lease.read()
      if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) throw new FormalStocktakePostSubmissionPendingError(sentinel)
      persisted = true
    }
    try { await adapter.execute(intent, { noReplay: true, beforeWrite }) } catch (error) { if (!persisted) throw error }
    try { return Object.freeze({ recovered: true, ...(await recoverFormalStocktakePost(lease, sentinel, recoveryAdapter, canCommit)) }) }
    catch (_) { throw new FormalStocktakePostSubmissionPendingError(sentinel) }
  })
}

module.exports = {
  validateFormalStocktakePostSentinel, validateFormalStocktakePostCommandStatus,
  validateFormalStocktakePostRecoveredProjection, createFormalStocktakePostRecoveryAdapterFromFormalAdapter,
  recoverFormalStocktakePost, submitDurableFormalStocktakePost, FormalStocktakePostSubmissionPendingError,
  sentinelFromIntent
}
