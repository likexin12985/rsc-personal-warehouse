const api = require('./api')
const { projectAccess } = require('./formal-stocktake-adapter')
const { validateOpeningStocktakeDetail } = require('./stocktake-contract')
const { validateOpeningCountSentinel } = require('./opening-count-recovery-store')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const DETAIL_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const QUANTITY = /^(?:0|[1-9]\d*)\.\d{3}$/
const DISPOSITIONS = ['resolved_existing_master', 'pending_verification', 'requires_recount']
const NO_STORE = Object.freeze({ method: 'GET', noRefresh: true,
  header: Object.freeze({ 'Cache-Control': 'no-store', Pragma: 'no-cache' }) })

function fail(message = '盘点恢复证据与原请求不一致，继续保持待核验') {
  const error = new Error(message)
  error.name = 'OpeningCountRecoveryError'
  error.status = 409
  error.code = 'opening_count_recovery_unconfirmed'
  throw error
}
function object(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail()
  return value
}
function exact(value, fields) {
  const row = object(value)
  if (Object.keys(row).length !== fields.length || fields.some((key) => !Object.prototype.hasOwnProperty.call(row, key))) fail()
  return row
}
function checkedIdentity(value) {
  const row = exact(value, ['person_id', 'authorization_version'])
  if (typeof row.person_id !== 'string' || !UUID.test(row.person_id)
    || !Number.isSafeInteger(row.authorization_version) || row.authorization_version < 1) fail()
  return Object.freeze({ person_id: row.person_id, authorization_version: row.authorization_version })
}
function sameIdentity(value, expected) {
  const current = checkedIdentity(value)
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) {
    fail('登录人员或权限已变化，原盘点继续保持待核验')
  }
}
function activeIdentity(value, expected) {
  const row = exact(value, ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name',
    'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes'])
  const identity = checkedIdentity({ person_id: row.person_id, authorization_version: row.authorization_version })
  sameIdentity(identity, expected)
  if (row.account_status !== 'active' || row.employment_status !== 'active' || row.access_mode !== 'active') fail('当前身份不能核验盘点')
  for (const [field, limit] of [['name', 160], ['employee_no', 80], ['organization_code', 120], ['organization_name', 240]]) {
    const text = row[field]
    if (typeof text !== 'string' || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail()
  }
  const roles = row.role_codes
  if (!Array.isArray(roles) || !roles.length || new Set(roles).size !== roles.length
    || roles.some((role) => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(role))
    || !roles.some((role) => ['admin', 'provincial_manager', 'technician'].includes(role))) fail()
  // Fresh private identity fields are validated but never returned or persisted.
  return identity
}
function requireReadAndCount(access, expected) {
  object(access)
  sameIdentity({ person_id: access.person_id, authorization_version: access.authorization_version }, expected)
  if (access.schema_version !== '1.0' || access.can_read !== true || access.can_count !== true) {
    fail('当前正式权限不能核验原盘点范围，继续保留恢复记录')
  }
}

// Compare integral UTC seconds and microseconds separately. Multiplying a date
// into one microsecond Number would lose precision; no runtime integer extension
// is required in the WeChat client.
function instant(value) {
  if (typeof value !== 'string') fail()
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value)
  if (!match) fail()
  const base = Date.parse(`${match[1]}Z`)
  if (!Number.isFinite(base) || new Date(base).toISOString().slice(0, 19) !== match[1]) fail()
  const hours = Number(match[5] || 0)
  const minutes = Number(match[6] || 0)
  if (hours > 23 || minutes > 59) fail()
  const offsetSeconds = (hours * 60 + minutes) * 60 * (match[4] === '-' ? -1 : 1)
  const seconds = base / 1000 - offsetSeconds
  if (!Number.isSafeInteger(seconds)) fail()
  return [seconds, Number(((match[2] || '') + '000000').slice(0, 6))]
}
function laterThan(left, right) {
  const a = instant(left)
  const b = instant(right)
  return a[0] > b[0] || (a[0] === b[0] && a[1] > b[1])
}

function createOpeningCountRecoveryAdapter(expectedIdentity, transport = api) {
  const expected = checkedIdentity(expectedIdentity)
  // get(path, params) cannot carry request options. Only request can guarantee
  // no-store plus no automatic refresh write in this read-only recovery path.
  if (!transport || typeof transport.request !== 'function') fail('盘点只读核验通道不可用')
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await transport.request('/auth/me', NO_STORE), expected) },
    async loadAccess() { return projectAccess(await transport.request('/access/context', NO_STORE), expected) },
    commandStatus(value) {
      const sentinel = validateOpeningCountSentinel(value)
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected)
      const query = `actor_person_id=${encodeURIComponent(sentinel.actor_person_id)}`
        + `&actor_authorization_version=${sentinel.actor_authorization_version}`
        + `&trace_request_id=${encodeURIComponent(sentinel.trace_request_id)}`
      return transport.request(`/v1/stocktakes/opening/${sentinel.task_id}/rounds/${sentinel.round_id}/scopes/${sentinel.scope_id}/count-command-status?${query}`, NO_STORE)
    },
    detail(id) {
      if (typeof id !== 'string' || !UUID.test(id)) fail()
      return transport.request(`/v1/stocktakes/opening/${id}`, NO_STORE)
    }
  })
}

function validateOpeningCountCommandStatus(value, original) {
  const sentinel = validateOpeningCountSentinel(original)
  const row = exact(value, ['schema_version', 'task_id', 'round_id', 'scope_id', 'actor_person_id',
    'actor_authorization_version', 'trace_request_id', 'lookup_status', 'command'])
  if (row.schema_version !== '1.0' || row.task_id !== sentinel.task_id || row.round_id !== sentinel.round_id
    || row.scope_id !== sentinel.scope_id || row.actor_person_id !== sentinel.actor_person_id
    || row.actor_authorization_version !== sentinel.actor_authorization_version || row.trace_request_id !== sentinel.trace_request_id) fail()
  const anchors = { schema_version: '1.0', task_id: sentinel.task_id, round_id: sentinel.round_id,
    scope_id: sentinel.scope_id, actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id }
  if (row.lookup_status === 'not_observed') {
    if (row.command !== null) fail()
    return Object.freeze(Object.assign(anchors, { lookup_status: 'not_observed', command: null }))
  }
  if (row.lookup_status !== 'confirmed') fail()
  const command = exact(row.command, ['completion_id', 'completed_at', 'round_no', 'scope_completed', 'caused_round_submission'])
  if (typeof command.completion_id !== 'string' || !UUID.test(command.completion_id)
    || command.round_no !== sentinel.round_no || command.scope_completed !== true || typeof command.caused_round_submission !== 'boolean') fail()
  instant(command.completed_at)
  return Object.freeze(Object.assign(anchors, { lookup_status: 'confirmed', command: Object.freeze({
    completion_id: command.completion_id, completed_at: command.completed_at, round_no: sentinel.round_no,
    scope_completed: true, caused_round_submission: command.caused_round_submission
  }) }))
}

function detailUuid(value, nullable = false) {
  if (value === null && nullable) return null
  if (typeof value !== 'string' || !DETAIL_UUID.test(value)) fail()
  return value.toLowerCase()
}
function nullableText(value) {
  if (value === null) return null
  if (typeof value !== 'string' || !value || value !== value.trim()) fail()
  return value
}
function choice(value, values) { if (!values.includes(value)) fail(); return value }
function observation(value) {
  const row = object(value)
  ;['observation_id', 'difference_id', 'scope_id'].forEach((key) => detailUuid(row[key]))
  if (!Number.isSafeInteger(row.observation_no) || row.observation_no < 1) fail()
  choice(row.material_identifier_type, ['sku_code', 'qr_code', 'external_code', 'unknown'])
  nullableText(row.material_identifier_raw)
  choice(row.condition_code, ['new', 'used', 'damaged', 'scrapped'])
  choice(row.availability_bucket, ['available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending'])
  if (typeof row.counted_qty !== 'string' || !QUANTITY.test(row.counted_qty) || row.counted_qty === '0.000') fail()
  choice(row.verification_status, ['verified', 'pending_verification'])
  const material = detailUuid(row.material_id, true)
  const lot = detailUuid(row.lot_id, true)
  const serial = detailUuid(row.serial_id, true)
  const lotNo = nullableText(row.lot_no_raw)
  const serialNo = nullableText(row.serial_no_raw)
  const serialType = row.serial_identifier_type === null ? null : choice(row.serial_identifier_type, ['serial_no', 'qr_code', 'unknown'])
  if (row.verification_status === 'verified' && (material === null || (lotNo !== null && lot === null) || (serialNo !== null && serial === null))) fail()
  if ((lot !== null && lotNo === null) || (serialNo === null) !== (serialType === null) || (serial !== null && serialNo === null)
    || (serialNo !== null && row.counted_qty !== '1.000')) fail()
  if (row.disposition !== null) {
    const disposition = object(row.disposition)
    detailUuid(disposition.disposition_id)
    choice(disposition.disposition, DISPOSITIONS)
    const resolvedMaterial = detailUuid(disposition.resolved_material_id, true)
    const resolvedLot = detailUuid(disposition.resolved_lot_id, true)
    const resolvedSerial = detailUuid(disposition.resolved_serial_id, true)
    const reason = nullableText(disposition.reason_code)
    if (reason === null || reason.length > 80) fail()
    instant(disposition.decided_at)
    if (disposition.disposition === 'resolved_existing_master' ? resolvedMaterial === null
      : resolvedMaterial !== null || resolvedLot !== null || resolvedSerial !== null) fail()
  }
  if (!Array.isArray(row.allowed_dispositions)
    || ![[], ['pending_verification', 'requires_recount'], DISPOSITIONS].some((allowed) => JSON.stringify(allowed) === JSON.stringify(row.allowed_dispositions))) fail()
  if (row.verification_status !== 'pending_verification' && (row.disposition !== null || row.allowed_dispositions.length)
    || row.disposition !== null && row.allowed_dispositions.length) fail()
  return row
}

// The older mini detail contract validates basic fields, but not observation
// evidence or all sealed/hidden invariants. Recovery must prove those too before
// it can remove the durable barrier, matching the PC recovery boundary.
function validateOpeningCountDetail(value, expectedTaskId) {
  const detail = validateOpeningStocktakeDetail(value, expectedTaskId)
  const round = detail.current_round
  const sealed = detail.evidence_status === 'sealed'
  if (detail.evidence_status === 'not_started') {
    if (round !== null || !['draft', 'issued', 'frozen', 'cancelled'].includes(detail.status)) fail()
  } else if (detail.evidence_status === 'counting_hidden') {
    if (detail.status !== 'counting' || !round || round.status !== 'counting') fail()
  } else if (!['submitted', 'region_review', 'hq_review', 'approved', 'recount_required', 'posted', 'closed'].includes(detail.status)
    || !round || round.status !== 'submitted') fail()
  for (const scope of detail.scopes) {
    const facts = ['zero_confirmed', 'count_line_count', 'observation_line_count', 'serial_count', 'total_counted_qty'].map((key) => scope[key])
    if (!sealed && facts.some((fact) => fact !== null)) fail()
    if (scope.completion_status === 'completed' && scope.completed_at === null) fail()
    if (sealed && (scope.completion_status === 'completed' ? facts.some((fact) => fact === null) : facts.some((fact) => fact !== null))) fail()
  }
  if (!Array.isArray(detail.observations) || (!sealed && (detail.observations.length || detail.differences.length))) fail()
  const scopes = new Set(detail.scopes.map((scope) => scope.scope_id.toLowerCase()))
  const differences = new Map(detail.differences.map((difference) => [difference.difference_id.toLowerCase(), difference]))
  const ids = new Set()
  const bound = new Set()
  let previousNo = 0
  for (const value of detail.observations) {
    const row = observation(value)
    const id = row.observation_id.toLowerCase()
    const scope = row.scope_id.toLowerCase()
    const differenceId = row.difference_id.toLowerCase()
    if (ids.has(id) || row.observation_no <= previousNo || !scopes.has(scope) || bound.has(differenceId)) fail()
    ids.add(id); bound.add(differenceId); previousNo = row.observation_no
    const difference = differences.get(differenceId)
    if (!difference || difference.scope_id === null || difference.scope_id.toLowerCase() !== scope
      || difference.difference_type !== 'excess' || difference.book_qty !== '0.000'
      || difference.counted_qty !== row.counted_qty || difference.difference_qty !== row.counted_qty || difference.affected_qty !== row.counted_qty
      || difference.evidence_required !== true || detailUuid(difference.material_id, true) !== detailUuid(row.material_id, true)
      || difference.reason_code !== (row.verification_status === 'pending_verification' ? 'opening_pending_verification' : 'opening_unexpected_dimension')) fail()
  }
  if (detail.observations.some((row) => row.allowed_dispositions.length)
    && (detail.status !== 'submitted' || !round || round.status !== 'submitted' || detail.reviews.length)) fail()
  if (detail.allowed_actions.some((action) => action.startsWith('review_'))
    && detail.observations.some((row) => row.verification_status === 'pending_verification' && row.disposition === null)) fail()
  return detail
}

function validateOpeningCountRecoveredProjection(value, sentinel, command) {
  const detail = validateOpeningCountDetail(value, sentinel.task_id)
  const round = detail.current_round
  const scope = detail.scopes.find((row) => row.scope_id === sentinel.scope_id)
  if (detail.task_id !== sentinel.task_id || !round || !scope || round.round_no < sentinel.round_no) fail()
  if (round.round_type !== (round.round_no === 1 ? 'initial' : 'recount') || round.status === 'superseded') fail()
  if (round.round_no === sentinel.round_no) {
    if (round.round_id !== sentinel.round_id || scope.completion_status !== 'completed'
      || laterThan(round.started_at, command.completed_at)
      || (round.status === 'submitted' && (!round.submitted_at || laterThan(command.completed_at, round.submitted_at)))
      || scope.completed_at !== command.completed_at || (command.caused_round_submission
        && (round.status !== 'submitted' || round.submitted_at !== command.completed_at))) fail()
  } else if (round.round_id === sentinel.round_id || laterThan(command.completed_at, round.started_at)) fail()
  return detail
}

async function recoverOpeningCountCommand(lease, value, adapter, canCommit = () => true) {
  const sentinel = validateOpeningCountSentinel(value)
  const stored = lease.read()
  if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail()
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }
  sameIdentity(await adapter.loadIdentity(), expected)
  requireReadAndCount(await adapter.loadAccess(), expected)
  const status = validateOpeningCountCommandStatus(await adapter.commandStatus(sentinel), sentinel)
  if (status.lookup_status !== 'confirmed') fail('暂未查到原盘点的确定结果，继续保留恢复记录；不能据此重新提交')
  const detail = validateOpeningCountRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command)
  sameIdentity(await adapter.loadIdentity(), expected)
  requireReadAndCount(await adapter.loadAccess(), expected)
  if (!canCommit()) fail('核验页面已变化，继续保留盘点恢复记录')
  lease.clearExact(sentinel)
  return Object.freeze({ command: status.command, detail })
}

module.exports = { createOpeningCountRecoveryAdapter, validateOpeningCountCommandStatus, validateOpeningCountDetail,
  validateOpeningCountRecoveredProjection, recoverOpeningCountCommand }
