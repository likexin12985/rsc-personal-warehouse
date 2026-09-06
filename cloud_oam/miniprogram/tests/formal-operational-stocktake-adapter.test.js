const assert = require('node:assert/strict')
const test = require('node:test')

const adapterModule = require('../utils/formal-stocktake-adapter')
const contract = require('../utils/formal-stocktake-contract')

const PERSON = '01000000-0000-4000-8000-000000000001'
const ASSIGNMENT = '02000000-0000-4000-8000-000000000001'
const TASK = '10000000-0000-4000-8000-000000000001'
const REGION = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const LOCATION = '40000000-0000-4000-8000-000000000001'
const ROUND = '50000000-0000-4000-8000-000000000001'
const ASSIGNEE_USER = 'engineer-001'
const POSTING_COMPLETION = '60000000-0000-4000-8000-000000000001'
const RECONCILIATION_COMPLETION = '70000000-0000-4000-8000-000000000001'
const CLOSE_COMPLETION = '80000000-0000-4000-8000-000000000001'
const RECONCILED_AT = '2026-09-01T09:10:00+08:00'
const CLOSED_AT = '2026-09-01T09:15:00+08:00'

function access(actions = ['read', 'count']) {
  return { person_id: PERSON, account_status: 'active', employment_status: 'active', authorization_version: 7, access_mode: 'active', role_codes: ['technician'], assignments: [{ assignment_id: ASSIGNMENT, role_code: 'technician', scope_type: 'person', scope_id: PERSON, valid_from: '2026-01-01T00:00:00+08:00', valid_to: null }], permissions: actions.map((action) => ({ resource: 'stocktake', action, field_code: '' })) }
}

function headquartersAccess(actions = ['read', 'reconcile', 'close']) {
  return { person_id: PERSON, account_status: 'active', employment_status: 'active', authorization_version: 7, access_mode: 'active', role_codes: ['admin'], assignments: [{ assignment_id: ASSIGNMENT, role_code: 'admin', scope_type: 'national', scope_id: '*', valid_from: '2026-01-01T00:00:00+08:00', valid_to: null }], permissions: actions.map((action) => ({ resource: 'stocktake', action, field_code: '' })) }
}

function axes(overrides = {}) { return Object.assign({ count_status: 'not_started', difference_status: 'not_ready', region_review_status: 'not_ready', headquarters_review_status: 'not_ready', recount_status: 'not_required', posting_status: 'not_posted', reconciliation_status: 'not_reconciled', closure_status: 'open' }, overrides) }
function detail(allowed = ['start']) { return { schema_version: '1.0', task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', region_org_id: REGION, status: 'draft', version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: '', state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: 'location_all', owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: 'not_started', snapshot_accounts: [], allowed_actions: [] }], rounds: [], allowed_actions: allowed } }
function postedDetail(version, allowed, latest = null) { return Object.assign(detail(allowed), { status: 'posted', version, posted_at: '2026-09-01T09:00:00+08:00', state_axes: axes({ posting_status: 'recorded', reconciliation_status: latest ? (latest.reconciled_task_version === version ? 'recorded' : 'stale') : 'not_reconciled' }), close_control: { latest_reconciliation: latest, close_completion: null } }) }
function recountDetail() { const base = detail([]); return Object.assign(base, { status: 'recount_required', version: 5, current_round_no: 1, cutoff_ledger_cursor: 10, cutoff_at: '2026-09-01T08:00:00+08:00', issued_at: '2026-09-01T07:00:00+08:00', frozen_at: '2026-09-01T08:00:00+08:00', submitted_at: '2026-09-01T09:00:00+08:00', state_axes: Object.assign(axes(), { count_status: 'submitted', difference_status: 'evaluated', region_review_status: 'recount', recount_status: 'required' }), scopes: [Object.assign({}, base.scopes[0], { snapshot_visibility: 'visible' })], rounds: [{ round_id: ROUND, round_no: 1, round_type: 'initial', status: 'submitted', started_at: '2026-09-01T08:00:00+08:00', submitted_at: '2026-09-01T09:00:00+08:00', submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true, difference_completion: null, visible_differences: [], region_review: null, headquarters_review: null, recount_cause: null, posting: { status: 'not_posted', posting_ids: [], posting_fact_count: 0, visible_total_quantity: '0.000', covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: ['open_recount'] }] }) }
function emptyPage() { return { schema_version: '1.0', items: [], next_after_id: null } }
function coordinates() { return { 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` } }

function transport(handler) {
  const calls = []
  return {
    calls,
    async get(path) { calls.push({ method: 'GET', path }); return handler(path, 'GET') },
    async post(path, data, options) { calls.push({ method: 'POST', path, data, options }); return handler(path, 'POST') }
  }
}

function adapter(fake, identity = { person_id: PERSON, authorization_version: 7 }) {
  return adapterModule.createFormalStocktakeAdapter({ transport: fake, expectedIdentityProvider: () => identity })
}

test('access projection requires matching active identity and exact stocktake read/count grants', async () => {
  const fake = transport(() => access())
  const projected = await adapter(fake).loadAccess()
  assert.deepEqual(projected, { schema_version: '1.0', person_id: PERSON, authorization_version: 7, can_read: true, can_count: true, can_manage: false, can_review_region: false, can_review_headquarters: false, can_reconcile: false, can_close: false })
  await assert.rejects(adapter(fake, { person_id: PERSON, authorization_version: 8 }).loadAccess(), /授权版本/)
})

test('terminal capabilities require one national admin assignment and stay independent', () => {
  const reconcileOnly = adapterModule.projectAccess(headquartersAccess(['read', 'reconcile']), { person_id: PERSON, authorization_version: 7 })
  assert.equal(reconcileOnly.can_reconcile, true)
  assert.equal(reconcileOnly.can_close, false)
  const technician = adapterModule.projectAccess(access(['read', 'reconcile', 'close']), { person_id: PERSON, authorization_version: 7 })
  assert.equal(technician.can_reconcile, false)
  assert.equal(technician.can_close, false)
})

test('reads only canonical formal stocktake paths and validates responses', async () => {
  const fake = transport((path) => path === '/access/context' ? access() : path.includes('?') ? emptyPage() : detail())
  const client = adapter(fake)
  await client.list(null)
  await client.detail(TASK.toUpperCase())
  assert.deepEqual(fake.calls.map((call) => call.path), ['/v1/stocktakes?limit=50', `/v1/stocktakes/${TASK}`])
})

test('assignee option page strictly keeps server user and person identities separate', async () => {
  const fake = transport((path) => {
    if (path.startsWith('/v1/stocktake-options/assignees?')) return { schema_version: '1.0', region_org_id: REGION, location_id: LOCATION, items: [{ assignee_user_id: ASSIGNEE_USER, person_id: PERSON, name: '工程师', employee_no: 'E001', role_codes: ['technician'] }], next_after_person_id: null, authorization_version: 7 }
    return access(['read', 'manage'])
  })
  const page = await adapter(fake).listAssignees(REGION, LOCATION, null)
  assert.equal(page.items[0].assignee_user_id, ASSIGNEE_USER)
  assert.equal(page.items[0].person_id, PERSON)
  assert.match(fake.calls[0].path, /^\/v1\/stocktake-options\/assignees\?/)

  const malformed = transport(() => ({ schema_version: '1.0', region_org_id: REGION, location_id: LOCATION, items: [{ person_id: PERSON, name: '工程师', employee_no: 'E001', role_codes: ['technician'] }], next_after_person_id: null, authorization_version: 7 }))
  await assert.rejects(adapter(malformed).listAssignees(REGION, LOCATION, null), /精确包含正式字段/)
})

test('personal create sends exact intent coordinates and confirms with fresh access plus exact detail', async () => {
  const fake = transport((path, method) => {
    if (path === '/access/context') return access()
    if (method === 'POST') return { schema_version: '1.0', task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', status: 'draft', task_version: 0, scope_count: 1, idempotency_replayed: false }
    return detail()
  })
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const intent = registry.begin({ action: 'create_personal', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' } })
  const completed = await adapter(fake).execute(intent)
  assert.equal(completed.detail.task_id, TASK)
  const write = fake.calls.find((call) => call.method === 'POST')
  assert.equal(write.path, intent.path)
  assert.equal(write.data, intent.body)
  assert.equal(write.options.header, intent.headers)
  assert.equal(write.options.idempotencyKey, intent.headers['Idempotency-Key'])
  assert.equal(fake.calls.filter((call) => call.path === '/access/context').length, 2)
})

test('permission plus detail allowed_actions are both required before a write is sent', async () => {
  const fake = transport((path) => path === '/access/context' ? access() : detail([]))
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const intent = registry.begin({ action: 'start', taskId: TASK, expectedTaskVersion: 0, body: { expected_version: 0 } })
  await assert.rejects(adapter(fake).execute(intent), /allowed_actions/)
  assert.equal(fake.calls.some((call) => call.method === 'POST'), false)
})

test('no-replay validates request coordinates before the durable beforeWrite hook', async () => {
  let posts = 0
  let persisted = 0
  const fake = transport((path, method) => {
    if (path === '/access/context') return access()
    if (method === 'POST') { posts += 1; return {} }
    return detail()
  })
  fake.postNoReplay = async () => { posts += 1; return {} }
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const original = registry.begin({ action: 'create_personal', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' } })
  const malformed = Object.assign({}, original, { headers: Object.assign({}, original.headers, { 'Idempotency-Key': 'bad' }) })
  await assert.rejects(
    adapter(fake).execute(malformed, { noReplay: true, beforeWrite: () => { persisted += 1 } }),
    /盘点写坐标无效/
  )
  assert.equal(posts, 0)
  assert.equal(persisted, 0)
})

test('legacy and opening coordinates cannot be injected into an intent', async () => {
  const fake = transport(() => access())
  const forged = { action: 'create_personal', method: 'POST', path: '/stocktakes', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' }, headers: coordinates(), taskId: null, roundId: null, scopeId: null, expectedTaskVersion: null, signature: 'forged' }
  await assert.rejects(adapter(fake).execute(forged), /路径或动作/)
  assert.equal(fake.calls.length, 0)
})

test('recount user must still exist in the fresh authorization-bound scope directory before POST', async () => {
  const fake = transport((path, method) => {
    if (path === '/access/context') return access(['read', 'manage'])
    if (path === `/v1/stocktakes/${TASK}`) return recountDetail()
    if (path.startsWith('/v1/stocktake-options/assignees?')) return { schema_version: '1.0', region_org_id: REGION, location_id: LOCATION, items: [{ assignee_user_id: 'engineer-002', person_id: PERSON, name: '其他工程师', employee_no: 'E002', role_codes: ['technician'] }], next_after_person_id: null, authorization_version: 7 }
    if (method === 'POST') throw new Error('POST must not be reached')
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'open_recount', taskId: TASK, roundId: ROUND, expectedTaskVersion: 5, body: { expected_task_version: 5, assignments: [{ scope_id: SCOPE, assignee_user_id: ASSIGNEE_USER }], reason: '区域复核要求复盘' } })
  await assert.rejects(adapter(fake).execute(intent), /不在当前范围正式受控目录/)
  assert.equal(fake.calls.some((call) => call.method === 'POST'), false)
})

test('reconcile sends the exact frozen command and confirms access continuity plus close_control', async () => {
  let detailReads = 0
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  const fake = transport((path, method) => {
    if (path === '/access/context') return headquartersAccess()
    if (path === `/v1/stocktakes/${TASK}`) { detailReads += 1; return detailReads === 1 ? postedDetail(8, ['reconcile']) : postedDetail(9, ['reconcile', 'close'], latest) }
    if (method === 'POST') return { schema_version: '1.0', completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'posted', task_version: 9, scope_count: 1, account_count: 2, scoped_account_count: 1, serial_count: 0, transaction_count: 1, movement_count: 1, book_total_qty: '3.000', physical_total_qty: '3.000', reconciled_at: RECONCILED_AT, replayed: false }
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'reconcile', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } })
  const completed = await adapter(fake).execute(intent)
  assert.equal(completed.detail.close_control.latest_reconciliation.completion_id, RECONCILIATION_COMPLETION)
  const write = fake.calls.find((call) => call.method === 'POST')
  assert.equal(write.path, `/v1/stocktakes/${TASK}/reconcile`)
  assert.equal(write.data, intent.body)
  assert.equal(write.options.header, intent.headers)
  assert.equal(fake.calls.filter((call) => call.path === '/access/context').length, 2)
})

test('close is separately gated and confirms the exact close completion binding', async () => {
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  let detailReads = 0
  const fake = transport((path, method) => {
    if (path === '/access/context') return headquartersAccess()
    if (path === `/v1/stocktakes/${TASK}`) {
      detailReads += 1
      if (detailReads === 1) return postedDetail(9, ['close'], latest)
      return Object.assign(postedDetail(10, [], latest), { status: 'closed', closed_at: CLOSED_AT, state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded', closure_status: 'closed' }), close_control: { latest_reconciliation: latest, close_completion: { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 10, closed_at: CLOSED_AT } } })
    }
    if (method === 'POST') return { schema_version: '1.0', completion_id: CLOSE_COMPLETION, task_id: TASK, reconciliation_completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'closed', task_version: 10, closed_at: CLOSED_AT, replayed: false }
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'close', taskId: TASK, expectedTaskVersion: 9, body: { expected_task_version: 9 } })
  const completed = await adapter(fake).execute(intent)
  assert.equal(completed.detail.status, 'closed')
  assert.equal(fake.calls.find((call) => call.method === 'POST').path, `/v1/stocktakes/${TASK}/close`)

  const missingPermission = transport((path) => path === '/access/context' ? headquartersAccess(['read', 'reconcile']) : postedDetail(9, ['close'], latest))
  await assert.rejects(adapter(missingPermission).execute(intent), /权限/)
  assert.equal(missingPermission.calls.some((call) => call.method === 'POST'), false)
})

test('an uncertain reconcile retries only with the original path, body and coordinates', async () => {
  let postCalls = 0
  let detailReads = 0
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  const fake = transport((path, method) => {
    if (path === '/access/context') return headquartersAccess(['read', 'reconcile'])
    if (path === `/v1/stocktakes/${TASK}`) { detailReads += 1; return postCalls > 1 ? postedDetail(9, [], latest) : postedDetail(8, ['reconcile']) }
    if (method === 'POST') {
      postCalls += 1
      if (postCalls === 1) { const error = new Error('网关超时'); error.status = 503; throw error }
      return { schema_version: '1.0', completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'posted', task_version: 9, scope_count: 1, account_count: 1, scoped_account_count: 1, serial_count: 0, transaction_count: 0, movement_count: 0, book_total_qty: '0.000', physical_total_qty: '0.000', reconciled_at: RECONCILED_AT, replayed: true }
    }
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'reconcile', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } })
  await assert.rejects(adapter(fake).execute(intent), (error) => error.write_result_uncertain === true && error.stocktake_retry_state === 'retryable')
  await adapter(fake).execute(intent)
  const writes = fake.calls.filter((call) => call.method === 'POST')
  assert.equal(writes.length, 2)
  assert.equal(writes[0].path, writes[1].path)
  assert.equal(writes[0].data, writes[1].data)
  assert.equal(writes[0].options.header, writes[1].options.header)
})

test('a lost successful reconcile response replays the same intent after exact advanced-detail proof', async () => {
  let serverAdvanced = false
  let postCalls = 0
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  const fake = transport((path, method) => {
    if (path === '/access/context') return headquartersAccess(['read', 'reconcile'])
    if (path === `/v1/stocktakes/${TASK}`) return serverAdvanced ? postedDetail(9, [], latest) : postedDetail(8, ['reconcile'])
    if (method === 'POST') {
      postCalls += 1
      serverAdvanced = true
      if (postCalls === 1) { const error = new Error('成功响应丢失'); error.status = 503; throw error }
      return { schema_version: '1.0', completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'posted', task_version: 9, scope_count: 1, account_count: 1, scoped_account_count: 1, serial_count: 0, transaction_count: 0, movement_count: 0, book_total_qty: '0.000', physical_total_qty: '0.000', reconciled_at: RECONCILED_AT, replayed: true }
    }
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'reconcile', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } })
  await assert.rejects(adapter(fake).execute(intent), (error) => error.write_result_uncertain === true && error.stocktake_retry_state === 'retryable')
  const completed = await adapter(fake).execute(intent)
  assert.equal(completed.result.replayed, true)
  const writes = fake.calls.filter((call) => call.method === 'POST')
  assert.equal(writes.length, 2)
  assert.equal(writes[0].path, writes[1].path)
  assert.equal(writes[0].data, writes[1].data)
  assert.equal(writes[0].options.header, writes[1].options.header)
})

test('a lost successful close response replays only against the exact closed completion proof', async () => {
  let serverAdvanced = false
  let postCalls = 0
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  const closed = Object.assign(postedDetail(10, [], latest), { status: 'closed', closed_at: CLOSED_AT, state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded', closure_status: 'closed' }), close_control: { latest_reconciliation: latest, close_completion: { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 10, closed_at: CLOSED_AT } } })
  const fake = transport((path, method) => {
    if (path === '/access/context') return headquartersAccess(['read', 'close'])
    if (path === `/v1/stocktakes/${TASK}`) return serverAdvanced ? closed : postedDetail(9, ['close'], latest)
    if (method === 'POST') {
      postCalls += 1
      serverAdvanced = true
      if (postCalls === 1) { const error = new Error('关闭响应丢失'); error.status = 503; throw error }
      return { schema_version: '1.0', completion_id: CLOSE_COMPLETION, task_id: TASK, reconciliation_completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'closed', task_version: 10, closed_at: CLOSED_AT, replayed: true }
    }
    throw new Error(`unexpected path ${path}`)
  })
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'close', taskId: TASK, expectedTaskVersion: 9, body: { expected_task_version: 9 } })
  await assert.rejects(adapter(fake).execute(intent), (error) => error.write_result_uncertain === true && error.stocktake_retry_state === 'retryable')
  const completed = await adapter(fake).execute(intent)
  assert.equal(completed.detail.state_axes.closure_status, 'closed')
  const writes = fake.calls.filter((call) => call.method === 'POST')
  assert.equal(writes.length, 2)
  assert.equal(writes[0].path, writes[1].path)
  assert.equal(writes[0].data, writes[1].data)
  assert.equal(writes[0].options.header, writes[1].options.header)
})
