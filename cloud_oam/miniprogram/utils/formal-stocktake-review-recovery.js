// Durable, read-only recovery for non-opening region/headquarters reviews.
// Only public coordinates are persisted; review payloads, comments and write
// credentials never enter storage. Once a marker is written, the only legal
// resolution path is the review command-status GET plus a fresh detail GET.
const { validateFormalStocktakeReviewSentinel } = require('./formal-stocktake-review-recovery-store')
const { validateFormalStocktakeDetail } = require('./formal-stocktake-contract')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const REVIEW_RESULTING = { region: ['hq_review', 'recount_required', 'approved'], headquarters: ['recount_required', 'approved'] }
const REVIEW_ACTION = { region: 'review_region', headquarters: 'review_headquarters' }
const NO_STORE = Object.freeze({ method: 'GET', noRefresh: true, header: Object.freeze({ 'Cache-Control': 'no-store', Pragma: 'no-cache' }) })

function fail(message = '日常盘点复核仍待只读核验，继续保持阻塞', status = 409) {
  const error = new Error(message)
  error.name = 'FormalStocktakeReviewRecoveryError'
  error.status = status
  error.code = 'formal_stocktake_review_recovery_unconfirmed'
  throw error
}
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length || fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) fail()
  return value
}
function identity(value) {
  const row = exact(value, ['person_id', 'authorization_version'])
  if (typeof row.person_id !== 'string' || !UUID.test(row.person_id) || !Number.isSafeInteger(row.authorization_version) || row.authorization_version < 1) fail()
  return Object.freeze({ person_id: row.person_id.toLowerCase(), authorization_version: row.authorization_version })
}
function sameIdentity(value, expected) {
  const current = identity(value)
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) fail('登录人员或授权版本已变化，原复核继续保持待核验')
  return current
}
function activeIdentity(value, expected) {
  const row = exact(value, ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes'])
  const current = sameIdentity({ person_id: row.person_id, authorization_version: row.authorization_version }, expected)
  if (row.account_status !== 'active' || row.employment_status !== 'active' || row.access_mode !== 'active') fail('当前身份不能核验盘点复核')
  for (const [field, limit] of [['name', 160], ['employee_no', 80], ['organization_code', 120], ['organization_name', 240]]) {
    if (typeof row[field] !== 'string' || !row[field] || row[field].trim() !== row[field] || row[field].length > limit || /[\u0000-\u001f\u007f]/.test(row[field])) fail()
  }
  if (!Array.isArray(row.role_codes) || !row.role_codes.length || new Set(row.role_codes).size !== row.role_codes.length
    || row.role_codes.some((role) => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(role))
    || !row.role_codes.some((role) => ['admin', 'provincial_manager', 'technician'].includes(role))) fail('当前身份角色不能核验盘点复核')
  return current
}
function requireReviewAccess(access, expected, stage) {
  if (!access || typeof access !== 'object') fail('当前正式权限不能核验盘点复核')
  sameIdentity({ person_id: access.person_id, authorization_version: access.authorization_version }, expected)
  if (!access || access.schema_version !== '1.0' || access.can_read !== true) fail('当前正式权限不能核验盘点复核')
  if (stage === 'region' ? access.can_review_region !== true : access.can_review_headquarters !== true) fail('当前正式权限不能核验该阶段盘点复核')
}
function createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter(expectedIdentity, adapter) {
  const expected = identity(expectedIdentity)
  if (!adapter || typeof adapter.loadIdentityNoReplay !== 'function' || typeof adapter.loadAccessNoReplay !== 'function' || typeof adapter.detailNoReplay !== 'function' || typeof adapter.reviewCommandStatus !== 'function') fail('日常盘点复核只读核验通道不可用', 503)
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await adapter.loadIdentityNoReplay(), expected) },
    async loadAccess(stage) {
      const access = await adapter.loadAccessNoReplay()
      requireReviewAccess(access, expected, stage)
      return access
    },
    commandStatus(value) {
      const sentinel = validateFormalStocktakeReviewSentinel(value)
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected)
      return adapter.reviewCommandStatus(sentinel.task_id, sentinel.round_id, sentinel.review_stage, sentinel.actor_person_id, sentinel.actor_authorization_version, sentinel.trace_request_id)
    },
    detail(id) {
      if (typeof id !== 'string' || !UUID.test(id)) fail()
      return adapter.detailNoReplay(id.toLowerCase())
    }
  })
}
function createFormalStocktakeReviewRecoveryAdapter(expectedIdentity, transport) {
  const expected = identity(expectedIdentity)
  if (!transport || typeof transport.request !== 'function') fail('日常盘点复核只读核验通道不可用', 503)
  return createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter(expected, {
    async loadIdentityNoReplay() { return transport.request('/auth/me', NO_STORE) },
    async loadAccessNoReplay() {
      const raw = await transport.request('/access/context', NO_STORE)
      if (raw && raw.schema_version === '1.0' && Object.prototype.hasOwnProperty.call(raw, 'can_read')) return raw
      // The raw access endpoint is also used by the production adapter. Keep
      // this direct transport helper equivalent to that adapter rather than
      // accepting an unprojected permission document as recovery proof.
      const { projectAccess } = require('./formal-stocktake-adapter')
      return projectAccess(raw, expected)
    },
    async detailNoReplay(id) { return transport.request(`/v1/stocktakes/${id}`, NO_STORE) },
    async reviewCommandStatus(taskId, roundId, stage, personId, version, trace) {
      const query = `actor_person_id=${encodeURIComponent(personId)}&actor_authorization_version=${version}&trace_request_id=${encodeURIComponent(trace)}`
      return transport.request(`/v1/stocktakes/${taskId}/rounds/${roundId}/reviews/${stage}/command-status?${query}`, NO_STORE)
    }
  })
}
function validateFormalStocktakeReviewCommandStatus(value, original) {
  const sentinel = validateFormalStocktakeReviewSentinel(original)
  const row = exact(value, ['schema_version', 'task_id', 'round_id', 'review_stage', 'actor_person_id', 'actor_authorization_version', 'trace_request_id', 'lookup_status', 'command'])
  if (row.schema_version !== '1.0' || String(row.task_id).toLowerCase() !== sentinel.task_id || String(row.round_id).toLowerCase() !== sentinel.round_id || row.review_stage !== sentinel.review_stage || String(row.actor_person_id).toLowerCase() !== sentinel.actor_person_id || row.actor_authorization_version !== sentinel.actor_authorization_version || row.trace_request_id !== sentinel.trace_request_id) fail()
  if (row.lookup_status === 'not_observed') { if (row.command !== null) fail(); return Object.freeze({ ...sentinel, lookup_status: 'not_observed', command: null }) }
  if (row.lookup_status !== 'confirmed') fail()
  const command = exact(row.command, ['review_id', 'task_id', 'round_id', 'review_stage', 'decision', 'resulting_task_status', 'expected_task_version', 'resulting_task_version', 'task_version', 'item_count', 'pending_verification_count', 'ready_for_posting', 'reviewed_at'])
  const expectedResulting = command.decision === 'approve' ? (sentinel.review_stage === 'region' ? 'hq_review' : 'approved') : 'recount_required'
  if (!UUID.test(command.review_id) || command.review_id === '00000000-0000-0000-0000-000000000000' || String(command.task_id).toLowerCase() !== sentinel.task_id || String(command.round_id).toLowerCase() !== sentinel.round_id || command.review_stage !== sentinel.review_stage || !['approve', 'recount', 'reject'].includes(command.decision) || command.resulting_task_status !== expectedResulting || !Number.isSafeInteger(command.expected_task_version) || command.expected_task_version !== sentinel.expected_task_version || !Number.isSafeInteger(command.resulting_task_version) || command.resulting_task_version !== sentinel.expected_task_version + 1 || command.task_version !== command.resulting_task_version || !Number.isSafeInteger(command.item_count) || command.item_count < 0 || !Number.isSafeInteger(command.pending_verification_count) || command.pending_verification_count < 0 || command.pending_verification_count > command.item_count || typeof command.ready_for_posting !== 'boolean' || command.ready_for_posting !== (sentinel.review_stage === 'headquarters' && command.decision === 'approve' && command.pending_verification_count === 0) || typeof command.reviewed_at !== 'string' || !TIMESTAMP.test(command.reviewed_at) || !Number.isFinite(Date.parse(command.reviewed_at))) fail()
  return Object.freeze({ ...sentinel, lookup_status: 'confirmed', command: Object.freeze({ ...command, review_id: command.review_id.toLowerCase(), task_id: sentinel.task_id, round_id: sentinel.round_id }) })
}
function validateFormalStocktakeReviewRecoveredProjection(value, sentinel, command) {
  const detail = validateFormalStocktakeDetail(value)
  if (detail.task_id !== sentinel.task_id || detail.version < command.task_version || detail.status === 'draft' || detail.status === 'cancelled') fail('当前盘点详情未承接原复核版本')
  const round = detail.rounds.find((item) => item.round_id === sentinel.round_id)
  if (!round) fail()
  const fact = sentinel.review_stage === 'region' ? round.region_review : round.headquarters_review
  if (!fact || fact.review_id !== command.review_id || fact.review_stage !== sentinel.review_stage || fact.decision !== command.decision || fact.reviewer_person_id !== sentinel.actor_person_id || !fact.covers_all_task_scopes || Date.parse(fact.reviewed_at) !== Date.parse(command.reviewed_at)) fail('当前盘点详情未确认原复核事实')
  if (round.visible_differences.length !== command.item_count || fact.visible_items.length !== command.item_count) fail('当前盘点详情复核范围与历史摘要不一致')
  const differences = new Set(round.visible_differences.map((item) => item.difference_id))
  const items = new Set(fact.visible_items.map((item) => item.difference_id))
  if (differences.size !== command.item_count || items.size !== command.item_count || [...differences].some((id) => !items.has(id))) fail('当前盘点详情复核逐项责任链不完整')
  const pending = round.visible_differences.filter((item) => item.posting_blocked_by_pending_verification).length
  if (pending !== command.pending_verification_count || command.ready_for_posting !== (command.review_stage === 'headquarters' && command.decision === 'approve' && pending === 0)) fail('当前盘点详情待核验摘要不一致')
  if (detail.version === command.task_version && detail.status !== command.resulting_task_status) fail('复核历史命令与当前任务状态不一致')
  return detail
}
async function recoverFormalStocktakeReview(lease, value, adapter, canCommit = () => true) {
  const sentinel = validateFormalStocktakeReviewSentinel(value)
  const stored = lease.read()
  if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail()
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }
  sameIdentity(await adapter.loadIdentity(), expected)
  await adapter.loadAccess(sentinel.review_stage)
  const status = validateFormalStocktakeReviewCommandStatus(await adapter.commandStatus(sentinel), sentinel)
  if (status.lookup_status !== 'confirmed') fail('暂未查到原盘点复核的确定结果，继续保留恢复记录；不能据此重新提交')
  const detail = validateFormalStocktakeReviewRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command)
  sameIdentity(await adapter.loadIdentity(), expected)
  await adapter.loadAccess(sentinel.review_stage)
  if (!canCommit()) fail('核验页面已变化，继续保留日常盘点复核恢复记录')
  lease.clearExact(sentinel)
  return Object.freeze({ command: status.command, detail })
}
function sentinelFromIntent(intent, expected) {
  if (!intent || intent.method !== 'POST' || !['review_region', 'review_headquarters'].includes(intent.action) || !intent.taskId || !intent.roundId || !Number.isSafeInteger(intent.expectedTaskVersion) || intent.expectedTaskVersion < 0 || !intent.headers || typeof intent.headers['X-Request-ID'] !== 'string' || !TRACE.test(intent.headers['X-Request-ID'])) fail('日常盘点复核意图无效')
  const body = exact(intent.body, ['expected_task_version', 'decision', 'items', 'comment'])
  if (body.expected_task_version !== intent.expectedTaskVersion || !['approve', 'recount', 'reject'].includes(body.decision) || !Array.isArray(body.items) || typeof body.comment !== 'string') fail('日常盘点复核正文坐标无效')
  return validateFormalStocktakeReviewSentinel({ v: 1, kind: 'formal_stocktake_review', task_id: intent.taskId, round_id: intent.roundId, review_stage: intent.action === 'review_region' ? 'region' : 'headquarters', actor_person_id: expected.person_id, actor_authorization_version: expected.authorization_version, expected_task_version: intent.expectedTaskVersion, trace_request_id: intent.headers['X-Request-ID'] })
}
module.exports = { createFormalStocktakeReviewRecoveryAdapter, createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter, validateFormalStocktakeReviewCommandStatus, validateFormalStocktakeReviewRecoveredProjection, recoverFormalStocktakeReview, sentinelFromIntent, REVIEW_ACTION }
