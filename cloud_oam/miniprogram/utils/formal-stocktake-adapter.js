const api = require('./api')
const session = require('./session')
const contract = require('./formal-stocktake-contract')

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/
const SAFE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const OPERATIONAL_ROLES = ['admin', 'provincial_manager', 'technician']
const SUPPORTED_ROLES = OPERATIONAL_ROLES.concat(['star_headquarters_approver'])

function adapterError(message, status = 409) {
  const error = new Error(message)
  error.name = 'FormalStocktakeAdapterError'
  error.status = status
  return error
}

function fail(message, status) { throw adapterError(message, status) }

function exact(value, keys, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${name}不是有效对象`)
  const actual = Object.keys(value).sort()
  const expected = keys.slice().sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(`${name}必须精确包含正式字段`)
  return value
}

function uuidValue(value, name) {
  if (typeof value !== 'string' || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) fail(`${name}无效`)
  return value.toLowerCase()
}

function nullableUuid(value, name) { return value === null ? null : uuidValue(value, name) }

function positive(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) fail(`${name}无效`)
  return value
}

function text(value, name, allowEmpty = false) {
  if (typeof value !== 'string' || value !== value.trim() || (!allowEmpty && !value)) fail(`${name}无效`)
  return value
}

function timestamp(value, name) {
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail(`${name}无效`)
  return value
}

function projectAccess(value, expected) {
  const row = exact(value, ['person_id', 'account_status', 'employment_status', 'authorization_version', 'access_mode', 'role_codes', 'assignments', 'permissions'], '正式访问上下文')
  if (!expected || typeof expected !== 'object') fail('当前登录身份不可用', 401)
  const personId = uuidValue(row.person_id, 'person_id')
  const version = positive(row.authorization_version, 'authorization_version')
  if (personId !== uuidValue(expected.person_id, 'expected.person_id') || version !== positive(expected.authorization_version, 'expected.authorization_version')) fail('登录身份或授权版本与正式盘点上下文不一致')
  if (row.account_status !== 'active' || row.employment_status !== 'active' || row.access_mode !== 'active') fail('当前身份不是正式盘点允许的有效在职状态', 403)
  if (!Array.isArray(row.role_codes) || !row.role_codes.length) fail('正式盘点角色缺失', 403)
  const roles = row.role_codes.map((role) => text(role, 'role_code'))
  if (new Set(roles).size !== roles.length || roles.some((role) => !SUPPORTED_ROLES.includes(role)) || !roles.some((role) => OPERATIONAL_ROLES.includes(role))) fail('正式盘点角色无效', 403)
  if (!Array.isArray(row.assignments) || !Array.isArray(row.permissions)) fail('正式盘点授权结构无效')
  const assignments = row.assignments.map((value) => {
    const item = exact(value, ['assignment_id', 'role_code', 'scope_type', 'scope_id', 'valid_from', 'valid_to'], '正式授权范围')
    const id = uuidValue(item.assignment_id, 'assignment_id')
    const role = text(item.role_code, 'assignment.role_code')
    const scopeType = text(item.scope_type, 'scope_type')
    const scopeId = text(item.scope_id, 'scope_id')
    if (!SUPPORTED_ROLES.includes(role) || !['national', 'organization', 'person'].includes(scopeType)) fail('正式授权范围角色或类型无效')
    if (scopeType === 'national') {
      if (scopeId !== '*') fail('全国授权范围必须使用 *')
    } else {
      uuidValue(scopeId, 'scope_id')
    }
    timestamp(item.valid_from, 'valid_from')
    if (item.valid_to !== null) timestamp(item.valid_to, 'valid_to')
    return Object.freeze({ assignment_id: id, role_code: role, scope_type: scopeType, scope_id: scopeId })
  })
  if (new Set(assignments.map((item) => item.assignment_id)).size !== assignments.length) fail('正式授权范围重复')
  const permissions = row.permissions.map((value) => {
    const item = exact(value, ['resource', 'action', 'field_code'], '正式权限')
    return `${text(item.resource, 'resource')}\u0000${text(item.action, 'action')}\u0000${text(item.field_code, 'field_code', true)}`
  })
  if (new Set(permissions).size !== permissions.length) fail('正式权限重复')
  const has = (action) => permissions.includes(`stocktake\u0000${action}\u0000`)
  const canRead = has('read')
  const nationalHeadquartersAdminCount = assignments.filter((item) => item.role_code === 'admin' && item.scope_type === 'national' && item.scope_id === '*').length
  const isHeadquartersAdmin = roles.includes('admin') && nationalHeadquartersAdminCount === 1
  return Object.freeze({ schema_version: contract.SCHEMA_VERSION, person_id: personId, authorization_version: version, can_read: canRead, can_count: canRead && has('count'), can_manage: canRead && has('manage'), can_review_region: canRead && has('review_region'), can_review_headquarters: canRead && has('review_headquarters'), can_reconcile: canRead && isHeadquartersAdmin && has('reconcile'), can_close: canRead && isHeadquartersAdmin && has('close') })
}

function assigneeOptionPage(value, expected, regionOrgId, locationId) {
  const row = exact(value, ['schema_version', 'region_org_id', 'location_id', 'items', 'next_after_person_id', 'authorization_version'], '盘点人员受控目录')
  if (row.schema_version !== contract.SCHEMA_VERSION || positive(row.authorization_version, 'authorization_version') !== positive(expected.authorization_version, 'expected.authorization_version')) fail('盘点人员目录授权版本不连续')
  if (uuidValue(row.region_org_id, 'region_org_id') !== uuidValue(regionOrgId, 'expected.region_org_id') || uuidValue(row.location_id, 'location_id') !== uuidValue(locationId, 'expected.location_id')) fail('盘点人员目录范围锚点不一致')
  if (!Array.isArray(row.items)) fail('盘点人员目录必须是数组')
  const items = row.items.map((value) => {
    const item = exact(value, ['assignee_user_id', 'person_id', 'name', 'employee_no', 'role_codes'], '盘点人员选择项')
    const assigneeUserId = text(item.assignee_user_id, 'assignee_user_id')
    if (assigneeUserId.length > 160 || !Array.isArray(item.role_codes) || !item.role_codes.length) fail('盘点人员选择项无效')
    const roleCodes = item.role_codes.map((role) => {
      const checked = text(role, 'assignee.role_code')
      if (!OPERATIONAL_ROLES.includes(checked)) fail('盘点人员角色无效')
      return checked
    })
    if (new Set(roleCodes).size !== roleCodes.length) fail('盘点人员角色重复')
    return Object.freeze({ assignee_user_id: assigneeUserId, person_id: uuidValue(item.person_id, 'person_id'), name: text(item.name, 'name'), employee_no: text(item.employee_no, 'employee_no'), role_codes: Object.freeze(roleCodes) })
  })
  if (new Set(items.map((item) => item.person_id)).size !== items.length || new Set(items.map((item) => item.assignee_user_id)).size !== items.length) fail('盘点人员目录包含重复标识')
  return Object.freeze({ items: Object.freeze(items), next_after_person_id: nullableUuid(row.next_after_person_id, 'next_after_person_id') })
}

function expectedPath(intent) {
  if (intent.action === 'create_personal') return intent.path === '/v1/stocktakes/personal'
  if (!intent.taskId) return false
  if (intent.action === 'start') return intent.path === `/v1/stocktakes/${intent.taskId}/start`
  if (intent.action === 'reconcile') return intent.path === `/v1/stocktakes/${intent.taskId}/reconcile`
  if (intent.action === 'close') return intent.path === `/v1/stocktakes/${intent.taskId}/close`
  if (!intent.roundId) return false
  const root = `/v1/stocktakes/${intent.taskId}/rounds/${intent.roundId}`
  if (intent.action === 'submit_initial_count') return intent.scopeId && intent.path === `${root}/scopes/${intent.scopeId}/initial-count`
  if (intent.action === 'submit_recount_count') return intent.scopeId && intent.path === `${root}/scopes/${intent.scopeId}/recount-count`
  if (intent.action === 'generate_initial_differences') return intent.path === `${root}/differences`
  if (intent.action === 'generate_recount_differences') return intent.path === `${root}/recount-differences`
  if (intent.action === 'review_region') return intent.path === `${root}/reviews/region`
  if (intent.action === 'review_headquarters') return intent.path === `${root}/reviews/headquarters`
  return intent.path === `${root}/recount`
}

function permission(access, action) {
  if (['create_personal', 'start', 'submit_initial_count', 'submit_recount_count'].includes(action)) return access.can_count
  if (action === 'review_region') return access.can_review_region
  if (action === 'review_headquarters') return access.can_review_headquarters
  if (action === 'reconcile') return access.can_reconcile
  if (action === 'close') return access.can_close
  return access.can_manage
}

function detailAllows(detail, intent) {
  if (intent.action === 'create_personal') return true
  if (detail.task_id !== intent.taskId) return false
  if (intent.action === 'reconcile' || intent.action === 'close') return contract.stocktakeIntentRetryState(intent, detail) === 'retryable'
  if (detail.version !== intent.expectedTaskVersion) return false
  if (intent.action === 'start') return detail.allowed_actions.includes('start')
  if (intent.action === 'submit_initial_count' || intent.action === 'submit_recount_count') {
    const scope = detail.scopes.find((row) => row.scope_id === intent.scopeId)
    return Boolean(scope && scope.allowed_actions.includes(intent.action))
  }
  const round = detail.rounds.find((row) => row.round_id === intent.roundId)
  return Boolean(round && round.allowed_actions.includes(intent.action))
}

function uncertain(error) {
  if (!error || !Number.isInteger(error.status)) return true
  return error.status === 0 || error.status === 408 || error.status === 425 || error.status >= 500
}

function writeOptions(intent) {
  if (!intent.headers || !SAFE_IDEMPOTENCY_KEY.test(intent.headers['Idempotency-Key'] || '') || !SAFE_REQUEST_ID.test(intent.headers['X-Request-ID'] || '')) fail('盘点写坐标无效')
  return { header: intent.headers, idempotencyKey: intent.headers['Idempotency-Key'], requestId: intent.headers['X-Request-ID'] }
}

async function verifyRecountAssignees(adapter, detail, intent) {
  const body = exact(intent.body, ['expected_task_version', 'assignments', 'reason'], '开复盘写意图')
  if (positive(body.expected_task_version, 'expected_task_version') !== intent.expectedTaskVersion || !Array.isArray(body.assignments) || !body.assignments.length) fail('开复盘写意图无效')
  const availableByLocation = new Map()
  for (const value of body.assignments) {
    const assignment = exact(value, ['scope_id', 'assignee_user_id'], '开复盘范围分配')
    const scope = detail.scopes.find((item) => item.scope_id === uuidValue(assignment.scope_id, 'scope_id'))
    if (!scope) fail('开复盘范围不在当前详情内')
    const assigneeUserId = text(assignment.assignee_user_id, 'assignee_user_id')
    if (assigneeUserId.length > 160) fail('assignee_user_id 无效')
    let available = availableByLocation.get(scope.location_id)
    if (!available) {
      available = new Set()
      let cursor = null
      const seen = new Set()
      for (let pageNo = 0; pageNo < 100; pageNo += 1) {
        const page = await adapter.listAssignees(detail.region_org_id, scope.location_id, cursor)
        page.items.forEach((item) => available.add(item.assignee_user_id))
        if (!page.next_after_person_id) { cursor = null; break }
        if (seen.has(page.next_after_person_id)) fail('盘点人员目录分页游标重复')
        seen.add(page.next_after_person_id); cursor = page.next_after_person_id
      }
      if (cursor) fail('盘点人员目录分页超出安全上限')
      availableByLocation.set(scope.location_id, available)
    }
    if (!available.has(assigneeUserId)) fail('复盘人员不在当前范围正式受控目录内', 403)
  }
}

function createFormalStocktakeAdapter(options = {}) {
  const transport = options.transport || api
  const expectedIdentityProvider = options.expectedIdentityProvider || session.getUser
  if (!transport || typeof transport.get !== 'function' || typeof transport.post !== 'function' || typeof expectedIdentityProvider !== 'function') fail('正式盘点 transport 配置无效', 503)
  const adapter = {
    async loadAccess() { return projectAccess(await transport.get('/access/context'), expectedIdentityProvider()) },
    async list(afterId = null) {
      const suffix = afterId === null ? '' : `&after_id=${uuidValue(afterId, 'after_id')}`
      return contract.validateFormalStocktakePage(await transport.get(`/v1/stocktakes?limit=50${suffix}`))
    },
    async detail(taskId) { return contract.validateFormalStocktakeDetail(await transport.get(`/v1/stocktakes/${uuidValue(taskId, 'task_id')}`)) },
    async listAssignees(regionOrgId, locationId, afterPersonId = null) {
      const region = uuidValue(regionOrgId, 'region_org_id')
      const location = uuidValue(locationId, 'location_id')
      const suffix = afterPersonId === null ? '' : `&after_person_id=${uuidValue(afterPersonId, 'after_person_id')}`
      return assigneeOptionPage(await transport.get(`/v1/stocktake-options/assignees?region_org_id=${region}&location_id=${location}&limit=100${suffix}`), expectedIdentityProvider(), region, location)
    },
    async execute(intent) {
      if (!intent || intent.method !== 'POST' || !expectedPath(intent)) fail('盘点写意图路径或动作无效')
      const access = await adapter.loadAccess()
      if (!access.can_read || !permission(access, intent.action)) fail('当前正式权限不允许该盘点动作', 403)
      if (intent.taskId) {
        const before = await adapter.detail(intent.taskId)
        if (!detailAllows(before, intent)) fail('详情 allowed_actions 与当前权限未同时授权该动作')
        if (intent.action === 'open_recount') await verifyRecountAssignees(adapter, before, intent)
      }
      let raw
      try {
        raw = await transport.post(intent.path, intent.body, writeOptions(intent))
      } catch (error) {
        if (!uncertain(error)) throw error
        let state = intent.action === 'create_personal' ? 'retryable' : 'handoff_required'
        if (intent.taskId) {
          try { state = contract.stocktakeIntentRetryState(intent, await adapter.detail(intent.taskId)) } catch (_) {}
        }
        error.write_result_uncertain = true
        error.stocktake_retry_state = state
        throw error
      }
      try {
        const result = contract.validateFormalStocktakeWriteResult(intent, raw)
        const confirmedAccess = await adapter.loadAccess()
        if (confirmedAccess.person_id !== access.person_id || confirmedAccess.authorization_version !== access.authorization_version) fail('盘点写入前后身份或 authorization_version 不连续')
        const detail = await adapter.detail(result.task_id)
        contract.confirmFormalStocktakeWrite(intent, result, detail)
        return Object.freeze({ result, detail })
      } catch (error) {
        error.write_result_uncertain = true
        error.stocktake_retry_state = 'handoff_required'
        throw error
      }
    }
  }
  return Object.freeze(adapter)
}

const formalStocktakeAdapter = createFormalStocktakeAdapter()

function createIntentRegistry() {
  return contract.createFormalStocktakeIntentRegistry({
    coordinateFactory() {
      return { 'Idempotency-Key': api.createIdempotencyKey(), 'X-Request-ID': api.createRequestId() }
    }
  })
}

module.exports = {
  projectAccess,
  createFormalStocktakeAdapter,
  formalStocktakeAdapter,
  createFormalStocktakeIntentRegistry: createIntentRegistry
}
