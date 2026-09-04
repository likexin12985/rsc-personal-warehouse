const api = require('./api')
const { validateOpeningStocktakeCountWriteResult } = require('./stocktake-contract')
const { getOpeningCountRecoveryStore, validateOpeningCountSentinel } = require('./opening-count-recovery-store')
const { createOpeningCountRecoveryAdapter, recoverOpeningCountCommand, validateOpeningCountDetail } = require('./opening-count-recovery')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const RESULT_FIELDS = ['schema_version', 'task_id', 'round_id', 'scope_id', 'task_status', 'round_status',
  'scope_completed', 'round_sealed', 'has_pending_verification', 'replayed']
const OBSERVATION_FIELDS = ['material_identifier_raw', 'material_identifier_type', 'condition_code',
  'availability_bucket', 'counted_qty', 'material_id', 'lot_id', 'lot_no_raw', 'serial_id',
  'serial_no_raw', 'serial_identifier_type', 'count_method', 'reason_code', 'remark']

function fail(message) { const error = new Error(message); error.status = 409; throw error }
function record(value) { return value && typeof value === 'object' && !Array.isArray(value) }
function freeze(value) {
  if (record(value) || Array.isArray(value)) { Object.values(value).forEach(freeze); Object.freeze(value) }
  return value
}
function text(value, limit, allowEmpty = false) {
  return typeof value === 'string' && value === value.trim() && (allowEmpty || value.length > 0) && value.length <= limit
}
function nullableText(value, limit) { return value === undefined || value === null || text(value, limit) }
function preparedBody(input) {
  let body
  try { body = JSON.parse(JSON.stringify(input)) } catch (_) { fail('盘点正文无效，未发送请求') }
  if (!record(body) || Object.keys(body).sort().join('|') !== 'physical_observations|zero_confirmed'
    || typeof body.zero_confirmed !== 'boolean' || !Array.isArray(body.physical_observations)
    || body.physical_observations.length > 10000
    || body.zero_confirmed !== (body.physical_observations.length === 0)) fail('盘点正文或零库存确认无效，未发送请求')
  const dimensions = new Set()
  for (const row of body.physical_observations) {
    if (!record(row) || Object.keys(row).some((key) => !OBSERVATION_FIELDS.includes(key))
      || !text(row.material_identifier_raw, 300)
      || !['sku_code', 'qr_code', 'external_code', 'unknown'].includes(row.material_identifier_type)
      || !['new', 'used', 'damaged', 'scrapped'].includes(row.condition_code)
      || !['available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending'].includes(row.availability_bucket)
      || typeof row.counted_qty !== 'string' || !/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?$/.test(row.counted_qty)
      || row.counted_qty.split('.')[0].length > 15
      || /^0(?:\.0{1,3})?$/.test(row.counted_qty)
      || !nullableText(row.lot_no_raw, 160) || !nullableText(row.serial_no_raw, 200) || !nullableText(row.reason_code, 80)
      || (row.remark !== undefined && !text(row.remark, 4000, true))
      || (row.count_method !== undefined && !['manual', 'scan', 'import'].includes(row.count_method))
      || ['material_id', 'lot_id', 'serial_id'].some((key) => row[key] !== undefined && row[key] !== null && (typeof row[key] !== 'string' || !UUID.test(row[key])))) {
      fail('实盘明细不符合正式契约，未发送请求')
    }
    const hasSerial = row.serial_no_raw !== undefined && row.serial_no_raw !== null
    const hasSerialType = row.serial_identifier_type !== undefined && row.serial_identifier_type !== null
    if (hasSerial !== hasSerialType || (hasSerialType && !['serial_no', 'qr_code', 'unknown'].includes(row.serial_identifier_type))
      || (hasSerial && !/^1(?:\.0{1,3})?$/.test(row.counted_qty))
      || (row.serial_id != null && !hasSerial) || (row.lot_id != null && !row.lot_no_raw)) fail('实物标识绑定无效，未发送请求')
    // Only identical explicit raw/master dimensions are deduplicated here.
    // Server-side aliases and resolved identifiers are never guessed or merged.
    const dimension = JSON.stringify(['material_identifier_raw', 'material_identifier_type',
      'condition_code', 'availability_bucket', 'material_id', 'lot_id', 'lot_no_raw',
      'serial_id', 'serial_no_raw', 'serial_identifier_type'].map((key) => row[key] == null ? null : row[key]))
    if (dimensions.has(dimension)) fail('同一现场维度必须合并数量后提交，未发送请求')
    dimensions.add(dimension)
  }
  return freeze(body)
}

class OpeningCountSubmissionPendingError extends Error {
  constructor(sentinel) {
    super('原盘点仍待核验，请保留恢复记录；不能据此重新提交')
    this.name = 'OpeningCountSubmissionPendingError'
    this.status = 409
    this.task_id = sentinel.task_id
    this.round_id = sentinel.round_id
    this.scope_id = sentinel.scope_id
    this.trace_request_id = sentinel.trace_request_id
  }
}

async function requireAccess(adapter) {
  await adapter.loadIdentity()
  const access = await adapter.loadAccess()
  if (access.can_read !== true || access.can_count !== true) fail('当前正式权限不允许提交盘点')
}
function acceptedResult(value, sentinel) {
  if (!record(value) || Object.keys(value).length !== RESULT_FIELDS.length
    || RESULT_FIELDS.some((field) => !Object.prototype.hasOwnProperty.call(value, field))) fail('盘点提交响应契约无效')
  return validateOpeningStocktakeCountWriteResult(value, sentinel.task_id, sentinel.round_id, sentinel.scope_id)
}
function directRejection(error) {
  return error && error.responseReceived === true && (
    (error.status === 400 && error.category === 'invalid_request' && ['idempotency_key_invalid', 'x_request_id_invalid'].includes(error.code))
    || (error.status === 412 && error.category === 'precondition_failed' && error.code === 'opening_count_state_invalid'))
}
async function recover(lease, sentinel, adapter, canCommit, directResult = null) {
  try {
    const confirmed = await recoverOpeningCountCommand(lease, sentinel, adapter, canCommit)
    return Object.freeze({ recovered: directResult === null || directResult.replayed
      || directResult.round_sealed !== confirmed.command.caused_round_submission,
    command: confirmed.command, detail: confirmed.detail })
  } catch (_) { throw new OpeningCountSubmissionPendingError(sentinel) }
}

// One new POST or GET-only historical recovery. No legacy in-memory count intent.
async function submitDurableOpeningScopeCount(options) {
  const { taskId, scopeId, input, expectedIdentity, store = getOpeningCountRecoveryStore(),
    transport = api, canCommit = () => true, confirm = async () => true } = options
  return store.withTaskLease(taskId, async (lease) => {
    const existing = lease.read()
    if (existing.kind === 'corrupt' || existing.kind === 'unavailable') fail('盘点恢复记录不可用，已停止写入')
    const expected = Object.freeze(Object.assign({}, expectedIdentity))
    let adapter
    try { adapter = createOpeningCountRecoveryAdapter(expected, transport) } catch (_) {
      if (existing.kind === 'valid') throw new OpeningCountSubmissionPendingError(existing.value)
      fail('当前正式身份无效，已停止写入')
    }
    if (existing.kind === 'valid') return recover(lease, existing.value, adapter, canCommit)
    if (!transport || typeof transport.postNoReplay !== 'function'
      || typeof transport.createRequestId !== 'function' || typeof transport.createIdempotencyKey !== 'function') {
      fail('当前环境缺少单次发送能力，已停止写入')
    }
    await requireAccess(adapter)
    const before = validateOpeningCountDetail(await adapter.detail(taskId), taskId)
    const round = before.current_round
    const scope = before.scopes.find((row) => row.scope_id === scopeId)
    if (before.task_id !== taskId || !Number.isSafeInteger(before.task_version) || before.task_version < 1
      || before.status !== 'counting' || !before.allowed_actions.includes('count') || !round
      || round.status !== 'counting' || round.round_type !== (round.round_no === 1 ? 'initial' : 'recount')
      || !scope || !scope.assigned_to_me || scope.completion_status !== 'pending') fail('当前任务、轮次或范围不允许计数')
    const body = preparedBody(input)
    // The same service-context lease covers confirmation: a second page cannot
    // queue another modal and later acquire new request coordinates.
    if (!canCommit()) fail('当前盘点页面已变化，未发送请求')
    if (await confirm(Object.freeze({ task_id: taskId, round_id: round.round_id, scope_id: scopeId,
      zero_confirmed: body.zero_confirmed, observation_count: body.physical_observations.length })) !== true) return null
    await requireAccess(adapter)
    if (!canCommit()) fail('当前盘点页面已变化，未发送请求')
    const requestId = transport.createRequestId()
    const idempotencyKey = transport.createIdempotencyKey()
    if (!/^wxreq-[a-f0-9]{36}$/.test(requestId) || !/^wxidem-[a-f0-9]{36}$/.test(idempotencyKey)) fail('无法生成安全请求坐标')
    const coordinates = Object.freeze({ requestId, idempotencyKey })
    const sentinel = validateOpeningCountSentinel({ v: 1, kind: 'opening_scope_count', task_id: taskId,
      round_id: round.round_id, round_no: round.round_no, scope_id: scopeId,
      actor_person_id: expected.person_id, actor_authorization_version: expected.authorization_version,
      trace_request_id: requestId })
    const path = `/v1/stocktakes/opening/${taskId}/rounds/${round.round_id}/scopes/${scopeId}/count`
    lease.persist(sentinel)
    try {
      const stored = lease.read()
      if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
        throw new OpeningCountSubmissionPendingError(sentinel)
      }
    } catch (_) { throw new OpeningCountSubmissionPendingError(sentinel) }
    let raw
    try { raw = await transport.postNoReplay(path, body, coordinates) } catch (error) {
      // Only the first direct POST enters this branch; GET failures cannot clear history.
      if (directRejection(error)) {
        try {
          await requireAccess(adapter)
          if (!canCommit()) throw new OpeningCountSubmissionPendingError(sentinel)
          lease.clearExact(sentinel)
        } catch (_) { throw new OpeningCountSubmissionPendingError(sentinel) }
        const rejected = new Error('本次盘点请求已明确拒绝，未完成提交；请刷新后检查')
        Object.assign(rejected, { status: error.status, responseReceived: true, category: error.category, code: error.code })
        throw rejected
      }
      return recover(lease, sentinel, adapter, canCommit)
    }
    let result = null
    try { result = acceptedResult(raw, sentinel) } catch (_) { /* Only independent history can resolve malformed success. */ }
    return recover(lease, sentinel, adapter, canCommit, result)
  })
}

module.exports = { submitDurableOpeningScopeCount, OpeningCountSubmissionPendingError }
