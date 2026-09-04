const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

function loadPage(relativePath, stubs) {
  const savedModules = []
  const dependencies = ['../utils/opening-count-recovery-store', '../utils/opening-count-recovery',
    '../utils/opening-count-submission', '../utils/formal-stocktake-adapter']
  for (const dependency of dependencies) {
    const resolved = require.resolve(dependency)
    savedModules.push([resolved, require.cache[resolved]])
    delete require.cache[resolved]
  }
  if (global.wx && !global.wx.getStorageInfoSync) {
    const values = new Map()
    Object.assign(global.wx, { getStorageInfoSync: () => ({ keys: Array.from(values.keys()) }),
      getStorageSync: (key) => values.has(key) ? values.get(key) : '',
      setStorageSync: (key, value) => { values.set(key, value) }, removeStorageSync: (key) => { values.delete(key) } })
  }
  if (stubs['../utils/api']) {
    const fake = stubs['../utils/api']
    if (!fake.get) fake.get = async () => { throw new Error('unexpected test GET') }
    if (!fake.post) fake.post = async () => { throw new Error('unexpected test POST') }
    if (!fake.request) fake.request = (pathname, options) => {
      assert.equal(options.method, 'GET')
      assert.equal(options.noRefresh, true)
      return fake.get(pathname)
    }
    if (!fake.postNoReplay) fake.postNoReplay = (...args) => fake.post(...args)
  }
  if (stubs['../utils/session'] && !stubs['../utils/session'].getUser) stubs['../utils/session'].getUser = () => USER
  for (const [modulePath, exports] of Object.entries(stubs)) {
    const resolved = require.resolve(modulePath)
    savedModules.push([resolved, require.cache[resolved]])
    require.cache[resolved] = {
      id: resolved,
      filename: resolved,
      loaded: true,
      exports
    }
  }
  const resolvedPage = require.resolve(relativePath)
  delete require.cache[resolvedPage]
  let definition
  global.Page = (value) => { definition = value }
  require(resolvedPage)
  return {
    definition,
    restore() {
      delete require.cache[resolvedPage]
      for (const [resolved, saved] of savedModules.reverse()) {
        if (saved) require.cache[resolved] = saved
        else delete require.cache[resolved]
      }
      delete global.Page
    }
  }
}

function pageInstance(definition) {
  return Object.assign({}, definition, {
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(update) { Object.assign(this.data, update) }
  })
}

const USER = {
  person_id: '01000000-0000-4000-8000-000000000001',
  name: '盘点工程师',
  authorization_version: 4,
  role_codes: ['technician'], employee_no: 'TEST-001', organization_code: 'TEST-ORG', organization_name: '测试区域',
  account_status: 'active', employment_status: 'active', access_mode: 'active'
}
const CONTEXT = {
  person_id: USER.person_id,
  access_mode: 'active',
  authorization_version: 4,
  role_codes: ['technician'],
  account_status: 'active', employment_status: 'active',
  assignments: [{ assignment_id: '02000000-0000-4000-8000-000000000002', role_code: 'technician', scope_type: 'person',
    scope_id: USER.person_id, valid_from: '2026-08-01T00:00:00Z', valid_to: null }],
  permissions: [
    { resource: 'stocktake', action: 'read', field_code: '' },
    { resource: 'stocktake', action: 'count', field_code: '' }
  ]
}
const TASK_ID = '10000000-0000-4000-8000-000000000001'
const ROUND_ID = '20000000-0000-4000-8000-000000000001'
const SCOPE_ID = '30000000-0000-4000-8000-000000000001'
const ORG_ID = '40000000-0000-4000-8000-000000000001'
const LOCATION_ID = '50000000-0000-4000-8000-000000000001'
const POSTING_ID = '60000000-0000-4000-8000-000000000001'
const TRANSACTION_ID = '70000000-0000-4000-8000-000000000001'
const IDEMPOTENCY_KEY = `wxidem-${'a'.repeat(36)}`
const REQUEST_ID = `wxreq-${'b'.repeat(36)}`

function responseRejection(status, category, code, responseReceived = true) {
  const error = new Error(`rejected:${status}:${category || '-'}:${code || '-'}`)
  error.status = status
  error.responseReceived = responseReceived
  if (category !== undefined) error.category = category
  if (code !== undefined) error.code = code
  return error
}

function taskSummary() {
  return {
    task_id: TASK_ID,
    task_no: 'OPEN-JS-001',
    region_org_id: ORG_ID,
    status: 'counting',
    blind_count: true,
    current_round_no: 1,
    current_round_status: 'counting',
    visible_scope_count: 1,
    completed_scope_count: 0,
    evidence_status: 'counting_hidden',
    difference_count: null,
    task_version: 1,
    deadline: null,
    allowed_actions: ['count']
  }
}

function taskDetail() {
  return {
    schema_version: '1.0',
    task_id: TASK_ID,
    task_no: 'OPEN-JS-001',
    region_org_id: ORG_ID,
    status: 'counting',
    blind_count: true,
    task_version: 1,
    deadline: null,
    cutoff_at: '2026-08-31T01:00:00Z',
    current_round: {
      round_id: ROUND_ID,
      round_no: 1,
      round_type: 'initial',
      status: 'counting',
      started_at: '2026-08-31T01:00:00Z',
      submitted_at: null
    },
    evidence_status: 'counting_hidden',
    scopes: [{
      scope_id: SCOPE_ID,
      scope_no: 1,
      location_id: LOCATION_ID,
      owner_org_id: ORG_ID,
      assigned_to_me: true,
      completion_status: 'pending',
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
      completed_at: null
    }],
    differences: [],
    observations: [],
    reviews: [],
    allowed_actions: ['count']
  }
}

function terminalDetail(action) {
  const detail = taskDetail()
  detail.status = action === 'post' ? 'approved' : 'posted'
  detail.task_version = action === 'post' ? 7 : 8
  detail.current_round.status = 'submitted'
  detail.current_round.submitted_at = '2026-08-31T02:00:00Z'
  detail.evidence_status = 'sealed'
  Object.assign(detail.scopes[0], {
    completion_status: 'completed',
    zero_confirmed: false,
    count_line_count: 1,
    observation_line_count: 0,
    serial_count: 0,
    total_counted_qty: '2.000',
    completed_at: '2026-08-31T01:50:00Z'
  })
  detail.allowed_actions = [action]
  return detail
}

function reflectedCountDetail() {
  const detail = taskDetail()
  detail.status = 'submitted'
  detail.task_version = 2
  detail.current_round.status = 'submitted'
  detail.current_round.submitted_at = '2026-08-31T01:50:00Z'
  detail.evidence_status = 'sealed'
  detail.scopes[0].completion_status = 'completed'
  detail.scopes[0].completed_at = '2026-08-31T01:50:00Z'
  Object.assign(detail.scopes[0], { zero_confirmed: true, count_line_count: 0, observation_line_count: 0,
    serial_count: 0, total_counted_qty: '0.000' })
  detail.allowed_actions = []
  return detail
}

function completedButUnsealedCountDetail() {
  const detail = taskDetail()
  detail.scopes[0].completion_status = 'completed'
  detail.scopes[0].completed_at = '2026-08-31T01:45:00Z'
  return detail
}

function completedWithoutTimestampCountDetail() {
  const detail = reflectedCountDetail()
  detail.scopes[0].completed_at = null
  return detail
}

function conflictingTaskStatusCountDetail() {
  const detail = reflectedCountDetail()
  detail.status = 'issued'
  return detail
}

function recountCountDetail() {
  const detail = taskDetail()
  detail.task_version = 2
  detail.current_round = {
    round_id: '20000000-0000-4000-8000-000000000002',
    round_no: 2,
    round_type: 'recount',
    status: 'counting',
    started_at: '2026-08-31T02:30:00Z',
    submitted_at: null
  }
  return detail
}

function reflectedTerminalDetail(action) {
  const detail = terminalDetail(action)
  if (action === 'post') {
    detail.status = 'posted'
    detail.task_version = 8
    detail.allowed_actions = ['close']
  } else {
    detail.status = 'closed'
    detail.task_version = 9
    detail.allowed_actions = []
  }
  return detail
}

function conflictingTerminalDetail(action) {
  const detail = terminalDetail(action)
  if (action === 'post') {
    detail.status = 'closed'
    detail.task_version = 8
    detail.allowed_actions = []
  } else {
    detail.task_version = 9
  }
  return detail
}

function wrongRoundTerminalDetail(action) {
  const detail = reflectedTerminalDetail(action)
  detail.current_round.round_id = '20000000-0000-4000-8000-000000000099'
  return detail
}

function compatibleLaterTerminalDetail(action) {
  const detail = reflectedTerminalDetail(action)
  if (action === 'post') {
    detail.status = 'closed'
    detail.task_version = 9
    detail.allowed_actions = []
  } else {
    detail.task_version = 10
  }
  return detail
}

function countWriteResult(replayed = false) {
  return {
    schema_version: '1.0',
    task_id: TASK_ID,
    round_id: ROUND_ID,
    scope_id: SCOPE_ID,
    task_status: 'submitted',
    round_status: 'submitted',
    scope_completed: true,
    round_sealed: true,
    has_pending_verification: false,
    replayed
  }
}

function terminalWriteResult(action, replayed = false) {
  if (action === 'post') {
    return {
      schema_version: '1.0',
      task_id: TASK_ID,
      round_id: ROUND_ID,
      posting_id: POSTING_ID,
      inventory_transaction_id: TRANSACTION_ID,
      resulting_task_status: 'posted',
      task_version: 8,
      total_quantity: '2.000',
      established_scope_count: 1,
      pending_control_difference_count: 0,
      ledger_cursor: 11,
      replayed
    }
  }
  return {
    schema_version: '1.0',
    task_id: TASK_ID,
    posting_id: POSTING_ID,
    inventory_transaction_id: TRANSACTION_ID,
    resulting_task_status: 'closed',
    task_version: 9,
    closed_at: '2026-08-31T03:00:00Z',
    replayed
  }
}

function detailForWriteKind(kind) {
  return kind === 'count' ? taskDetail() : terminalDetail(kind)
}

function resultForWriteKind(kind, replayed = false) {
  return kind === 'count' ? countWriteResult(replayed) : terminalWriteResult(kind, replayed)
}

function reflectedDetailForWriteKind(kind) {
  return kind === 'count' ? reflectedCountDetail() : reflectedTerminalDetail(kind)
}

async function invokeWriteKind(instance, kind) {
  if (kind === 'count') {
    return instance.submitCount({ currentTarget: { dataset: { zero: 'true' } } })
  }
  return instance.terminalAction({ currentTarget: { dataset: { action: kind } } })
}

function deferred() { let resolve; const promise = new Promise((done) => { resolve = done }); return { promise, resolve } }
async function until(predicate) { for (let i = 0; i < 100 && !predicate(); i += 1) await new Promise(setImmediate); assert(predicate()) }
async function durableFixture(context, options = {}) {
  const values = options.values || new Map()
  const state = { current: taskDetail(), user: JSON.parse(JSON.stringify(USER)), context: JSON.parse(JSON.stringify(CONTEXT)),
    sessionUser: USER, history: false, posts: [], requests: [], modals: [], toasts: [], keyCalls: 0, requestCalls: 0, ...options }
  global.wx = { showToast: (value) => state.toasts.push(value),
    showModal(value) { state.modals.push(value); if (!state.holdModal) value.success({ confirm: state.confirm !== false }) },
    getStorageInfoSync: () => ({ keys: Array.from(values.keys()) }), getStorageSync: (key) => values.has(key) ? values.get(key) : '',
    setStorageSync: (key, value) => { values.set(key, value) }, removeStorageSync: (key) => { values.delete(key) } }
  global.getApp = () => ({ setUser(user) { state.sessionUser = user; return true } })
  function historyResult(pathname) {
    const query = new URL(`https://test.invalid${pathname}`).searchParams
    return { schema_version: '1.0', task_id: TASK_ID, round_id: ROUND_ID, scope_id: SCOPE_ID,
      actor_person_id: USER.person_id, actor_authorization_version: USER.authorization_version, trace_request_id: query.get('trace_request_id'),
      lookup_status: state.history ? 'confirmed' : 'not_observed', command: state.history
        ? { completion_id: POSTING_ID, round_no: 1, completed_at: '2026-08-31T01:50:00Z', scope_completed: true, caused_round_submission: true } : null }
  }
  const apiStub = {
    createIdempotencyKey() { state.keyCalls += 1; return `wxidem-${String(state.keyCalls).padStart(36, 'a')}` },
    createRequestId() { state.requestCalls += 1; return `wxreq-${String(state.requestCalls).padStart(36, 'b')}` },
    async get(pathname) {
      if (pathname === '/auth/me') return state.user
      if (pathname === '/access/context') return state.context
      if (pathname.includes('count-command-status')) {
        if (state.statusWait) await state.statusWait
        if (state.statusError) throw state.statusError
        const result = historyResult(pathname)
        return state.statusMutate ? state.statusMutate(result) : result
      }
      if (state.readError) throw state.readError
      return JSON.parse(JSON.stringify(state.current))
    },
    async request(pathname, init) {
      state.requests.push({ pathname, init })
      assert.deepEqual(init, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
      return apiStub.get(pathname)
    },
    async postNoReplay(pathname, body, coordinates) {
      state.posts.push({ pathname, body, coordinates })
      if (state.onPost) return state.onPost(state, pathname, body, coordinates)
      state.history = true; state.current = reflectedCountDetail(); return countWriteResult()
    },
    async post(pathname) { state.posts.push({ pathname, legacy: true }); throw new Error('unexpected legacy count POST') }
  }
  const loaded = loadPage('../pages/formal-stocktake-detail/index', {
    '../utils/api': apiStub, '../utils/session': { ensureLogin: () => true, getUser: () => state.sessionUser }
  })
  const instance = pageInstance(loaded.definition)
  instance.setData({ taskId: TASK_ID })
  await instance.load()
  const store = require('../utils/opening-count-recovery-store').getOpeningCountRecoveryStore()
  const cleanup = () => { loaded.restore(); delete global.wx; delete global.getApp }
  if (context) context.after(cleanup)
  return { instance, state, values, store, cleanup, loaded, apiStub,
    count: () => invokeWriteKind(instance, 'count'), recover: () => instance.recoverCount(),
    async restart() { const next = pageInstance(loaded.definition); next.setData({ taskId: TASK_ID }); await next.load(); return next } }
}
function unknownError() { return responseRejection(0, undefined, undefined, false) }
function pending(f) {
  assert.equal(f.store.read(TASK_ID).kind, 'valid')
  assert.equal(f.instance.data.countRecoveryBlocked, true)
  assert.equal(f.instance._pendingWriteIntent == null, true)
  const rendered = JSON.stringify(f.instance.data)
  assert(!rendered.includes('wxidem-')); assert(!rendered.includes('idempotencyKey'))
  assert(!rendered.includes('confirmedResponse')); assert(!rendered.includes('physical_observations'))
}

async function durableCountCase(context, scenario) {
  if (scenario === 'success') {
    const f = await durableFixture(context)
    f.instance.setData({ materialIdentifier: 'SKU-001', quantity: '2' }); f.instance.addObservation()
    await f.instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
    assert.equal(f.state.posts.length, 1); assert(!f.state.posts[0].legacy)
    assert.equal(f.state.posts[0].pathname, `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/scopes/${SCOPE_ID}/count`)
    assert.equal(f.state.posts[0].body.physical_observations[0].counted_qty, '2')
    assert.equal(f.store.read(TASK_ID).kind, 'missing'); assert.equal(f.instance.data.draftObservations.length, 0)
    assert.match(f.instance.data.countRecoveryNotice, /原范围计数与当前任务均已核验/)
    return
  }
  if (scenario === 'read-failure') {
    const f = await durableFixture(context, { onPost(state) {
      state.history = true; state.current = reflectedCountDetail(); state.readError = new Error('read failed'); return countWriteResult()
    } })
    await f.count(); pending(f); assert(!f.state.toasts.some((toast) => toast.icon === 'success'))
    f.state.readError = null
    await f.instance.load(); await f.count(); assert.equal(f.state.posts.length, 1)
    await f.recover(); assert.equal(f.store.read(TASK_ID).kind, 'missing')
    return
  }
  if (scenario === 'accepted-projection') {
    const f = await durableFixture(context, { onPost(state) { state.history = true; return countWriteResult() } })
    await f.count(); pending(f); await f.count(); assert.equal(f.state.posts.length, 1)
    f.state.current = completedWithoutTimestampCountDetail(); await f.recover(); pending(f)
    f.state.current = conflictingTaskStatusCountDetail(); await f.recover(); pending(f)
    f.state.current = recountCountDetail(); await f.recover()
    assert.equal(f.store.read(TASK_ID).kind, 'missing')
    assert.equal(f.instance.data.detail.current_round.round_no, 2)
    assert.equal(f.instance.data.detail.scopes[0].completion_status, 'pending')
    assert.match(f.instance.data.countRecoveryNotice, /历史提交已核验/)
    return
  }
  if (scenario === 'concurrent') {
    const wait = deferred()
    const f = await durableFixture(context, { holdModal: true, onPost: async () => { await wait.promise; throw unknownError() } })
    const other = await f.restart()
    const first = f.count(); await until(() => f.state.modals.length === 1)
    await Promise.all([f.count(), invokeWriteKind(other, 'count')])
    assert.equal(f.state.modals.length, 1); assert.equal(f.state.keyCalls, 0)
    f.state.modals[0].success({ confirm: true }); await until(() => f.state.posts.length === 1)
    await f.count(); assert.equal(f.state.posts.length, 1)
    wait.resolve(); await first; pending(f)
    return
  }
  if (scenario === 'unknown' || scenario === 'unmatched-completed') {
    const f = await durableFixture(context, { onPost(state) { if (scenario === 'unmatched-completed') state.current = reflectedCountDetail(); throw unknownError() } })
    f.instance.setData({ materialIdentifier: 'KEEP-SKU', quantity: '2' }); f.instance.addObservation()
    await f.instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } }); pending(f)
    assert.equal(f.instance.data.draftObservations[0].material_identifier_raw, 'KEEP-SKU')
    await f.count(); await f.recover(); pending(f)
    assert.equal(f.state.posts.length, 1); assert.equal(f.state.keyCalls, 1)
    const reloaded = await f.restart()
    assert.equal(reloaded._pendingWriteIntent == null, true); assert.equal(reloaded.data.countRecoveryBlocked, true)
    await reloaded.recoverCount(); assert.equal(f.state.posts.length, 1)
    return
  }
  if (scenario === 'first-rejection') {
    for (const [status, category, code] of [[412, 'precondition_failed', 'opening_count_state_invalid'],
      [400, 'invalid_request', 'idempotency_key_invalid'], [400, 'invalid_request', 'x_request_id_invalid']]) {
      const f = await durableFixture(null, { onPost(state) {
        if (state.posts.length === 1) throw responseRejection(status, category, code)
        state.history = true; state.current = reflectedCountDetail(); return countWriteResult()
      } })
      try {
        await f.count(); assert.equal(f.store.read(TASK_ID).kind, 'missing')
        await f.count(); assert.equal(f.state.posts.length, 2); assert.equal(f.state.keyCalls, 2)
        assert.notEqual(f.state.posts[0].coordinates.idempotencyKey, f.state.posts[1].coordinates.idempotencyKey)
      } finally { f.cleanup() }
    }
    return
  }
  if (scenario === 'replacement-marker') {
    const f = await durableFixture(context, { onPost(state) {
      const keys = wx.getStorageInfoSync().keys; const original = JSON.parse(wx.getStorageSync(keys[0]))
      original.trace_request_id = 'different-valid-request-0001'; wx.setStorageSync(keys[0], JSON.stringify(original))
      throw responseRejection(412, 'precondition_failed', 'opening_count_state_invalid')
    } })
    await f.count(); pending(f)
    assert.equal(f.store.read(TASK_ID).value.trace_request_id, 'different-valid-request-0001')
  }
}

test('formal stocktake list verifies identity and reads only the v1 namespace', async (context) => {
  const calls = []
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/formal-stocktakes/index', {
    '../utils/api': {
      createIdempotencyKey: () => IDEMPOTENCY_KEY,
      createRequestId: () => REQUEST_ID,
      async get(pathname) {
        calls.push(pathname)
        if (pathname === '/auth/me') return USER
        if (pathname === '/access/context') return CONTEXT
        if (pathname === '/v1/stocktakes/opening') {
          return { schema_version: '1.0', items: [taskSummary()], next_after_id: null }
        }
        throw new Error(`unexpected ${pathname}`)
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(calls.sort(), [
    '/access/context',
    '/auth/me',
    '/v1/stocktakes/opening'
  ])
  assert.equal(instance.data.accessAllowed, true)
  assert.equal(instance.data.tasks[0].statusLabel, '盘点中')
  assert.equal(instance.data.tasks[0].progressLabel, '0/1')
  assert.equal(instance.data.tasks[0].difference_count, null)
})

test('formal stocktake list clears all rows when blind response leaks quantity evidence', async (context) => {
  let toast = ''
  const leaked = taskSummary()
  leaked.difference_count = 0
  global.wx = { showToast: ({ title }) => { toast = title } }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/formal-stocktakes/index', {
    '../utils/api': {
      async get(pathname) {
        if (pathname === '/auth/me') return USER
        if (pathname === '/access/context') return CONTEXT
        return { schema_version: '1.0', items: [leaked], next_after_id: null }
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })
  const instance = pageInstance(loaded.definition)
  await instance.load()
  assert.equal(instance.data.accessAllowed, false)
  assert.deepEqual(instance.data.tasks, [])
  assert.match(toast, /盲盘/)
})

test('technician submits one durable scope count and confirms historical evidence', async (context) => {
  await durableCountCase(context, 'success')
})

test('count POST 200 retains its durable marker until independent history and detail confirm', async (context) => {
  await durableCountCase(context, 'read-failure')
})

test('accepted count verifies history without replay and keeps later-round completion independent', async (context) => {
  await durableCountCase(context, 'accepted-projection')
})

test('a durable count lease spans confirmation across pages and permits one POST', async (context) => {
  await durableCountCase(context, 'concurrent')
})

test('non-blind counting never renders blind wording for unavailable quantities', async (context) => {
  const visible = taskDetail()
  visible.blind_count = false
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/formal-stocktake-detail/index', {
    '../utils/api': {
      async get(pathname) {
        if (pathname === '/auth/me') return USER
        if (pathname === '/access/context') return CONTEXT
        return visible
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })
  const instance = pageInstance(loaded.definition)
  instance.setData({ taskId: TASK_ID })
  await instance.load()

  assert.equal(instance.data.detail.blind_count, false)
  assert.equal(instance.data.detail.scopes[0].quantityLabel, '尚未封存数量')
  const wxml = fs.readFileSync(
    path.join(__dirname, '../pages/formal-stocktake-detail/index.wxml'),
    'utf8'
  )
  assert.match(
    wxml,
    /detail\.blind_count && detail\.evidence_status === 'counting_hidden'/
  )
})

for (const [quantity, serial, accepted] of [
  ['0.001', false, true],
  ['999999999999999.999', false, true],
  ['1000000000000000', false, false],
  ['1000000000000000.001', false, false],
  ['0.000', false, false],
  ['1', true, true],
  ['1.0', true, true],
  ['1.00', true, true],
  ['1.000', true, true],
  ['0.001', true, false],
  ['0.999', true, false],
  ['1.001', true, false]
]) {
  test(`draft numeric(18,3) and exact one-piece SN boundary: ${quantity}, SN=${serial}`, async (context) => {
    const f = await durableFixture(context)
    f.instance.setData({ materialIdentifier: 'BOUNDARY-SKU', quantity, serialNo: serial ? 'SN-001' : '' })
    f.instance.addObservation()
    assert.equal(f.instance.data.draftObservations.length, accepted ? 1 : 0)
    if (accepted) assert.equal(f.instance.data.draftObservations[0].counted_qty, quantity)
    else {
      assert.equal(f.instance.data.quantity, quantity)
      assert.equal(f.state.toasts.length, 1)
    }
    assert.equal(f.state.posts.length, 0)
    assert.equal(f.state.keyCalls, 0)
    assert.equal(f.store.read(TASK_ID).kind, 'missing')
  })
}

test('unknown count locks every draft input, picker, and scanner in the rendered controls', () => {
  const wxml = fs.readFileSync(path.join(__dirname, '../pages/formal-stocktake-detail/index.wxml'), 'utf8')
  for (const handler of ['changeIdentifierType', 'bindMaterial', 'scanMaterial', 'changeCondition', 'changeAvailability',
    'bindQuantity', 'bindLot', 'bindSerial', 'scanSerial', 'bindRemark']) {
    const element = wxml.match(new RegExp(`<[^>]+bind(?:input|change|tap)="${handler}"[^>]*>`))
    assert.ok(element, `control for ${handler} exists`)
    assert.match(element[0], /disabled="\{\{[^}]*countRecoveryBlocked[^}]*\}\}"/)
  }
})

test('uncertain count permits only read-only historical recovery and survives a restored page', async (context) => {
  await durableCountCase(context, 'unknown')
})

test('count timeout plus an unmatched completed scope retains durable history and draft', async (context) => {
  await durableCountCase(context, 'unmatched-completed')
})

test('first direct exact count no-effect rejection clears only its original durable marker', async (context) => {
  await durableCountCase(context, 'first-rejection')
})

test('terminal rejection matrix retains every non-whitelisted first POST', async () => {
  const cases = [
    ['401', (code) => responseRejection(401, 'precondition_failed', code)],
    ['403', (code) => responseRejection(403, 'precondition_failed', code)],
    ['404', (code) => responseRejection(404, 'precondition_failed', code)],
    ['408', (code) => responseRejection(408, 'precondition_failed', code)],
    ['409 idempotency', () => responseRejection(409, 'conflict', 'idempotency_conflict')],
    ['422', (code) => responseRejection(422, 'precondition_failed', code)],
    ['425', (code) => responseRejection(425, 'precondition_failed', code)],
    ['429', (code) => responseRejection(429, 'precondition_failed', code)],
    ['500', (code) => responseRejection(500, 'precondition_failed', code)],
    ['503', (code) => responseRejection(503, 'precondition_failed', code)],
    ['504', (code) => responseRejection(504, 'precondition_failed', code)],
    ['missing response proof', (code) => responseRejection(412, 'precondition_failed', code, false)],
    ['non-boolean response proof', (code) => responseRejection(412, 'precondition_failed', code, 1)],
    ['missing metadata', () => responseRejection(412, undefined, undefined)],
    ['wrong category', (code) => responseRejection(412, 'invalid_request', code)],
    ['unknown code', () => responseRejection(412, 'precondition_failed', 'unknown_rejection')],
    ['database guard', () => responseRejection(412, 'precondition_failed', 'database_guard_rejected')],
    ['wrong action code', (_code, kind) => responseRejection(
      412,
      'precondition_failed',
      kind === 'count'
        ? 'opening_finalize_state_invalid'
        : (kind === 'post'
            ? 'opening_close_reconciliation_pending'
            : 'opening_count_state_invalid')
    )],
    ['header code wrong status', () => responseRejection(412, 'invalid_request', 'idempotency_key_invalid')],
    ['header code wrong category', () => responseRejection(400, 'precondition_failed', 'x_request_id_invalid')],
    ['unknown header code', () => responseRejection(400, 'invalid_request', 'unknown_rejection')]
  ]
  for (const kind of ['post', 'close']) {
    const exactCode = kind === 'count'
      ? 'opening_count_state_invalid'
      : 'opening_finalize_state_invalid'
    for (const [label, buildError] of cases) {
      const error = buildError(exactCode, kind)
      const posts = []
      let keyCalls = 0
      global.wx = {
        showToast() {},
        showModal(options) { options.success({ confirm: true }) }
      }
      global.getApp = () => ({ setUser: () => true })
      const loaded = loadPage('../pages/formal-stocktake-detail/index', {
        '../utils/api': {
          createIdempotencyKey() {
            keyCalls += 1
            return IDEMPOTENCY_KEY
          },
          createRequestId: () => REQUEST_ID,
          async get(pathname) {
            if (pathname === '/auth/me') return USER
            if (pathname === '/access/context') return CONTEXT
            return detailForWriteKind(kind)
          },
          async post(_pathname, _body, options) {
            posts.push(options)
            throw error
          }
        },
        '../utils/session': { ensureLogin: () => true }
      })
      try {
        const instance = pageInstance(loaded.definition)
        instance.setData({ taskId: TASK_ID })
        await instance.load()
        await invokeWriteKind(instance, kind)
        const originalIntent = instance._pendingWriteIntent
        await invokeWriteKind(instance, kind)

        assert.equal(posts.length, 2, `${kind}/${label} must remain replayable`)
        assert.equal(keyCalls, 1, `${kind}/${label} must reuse its original coordinates`)
        assert.equal(instance._pendingWriteIntent, originalIntent, `${kind}/${label} must retain the same intent`)
        assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
        assert.equal(instance._pendingWriteIntent.postAttempted, true)
        assert.equal(instance.data.writePending, true)
        assert.deepEqual(posts[0], posts[1])
      } finally {
        loaded.restore()
        delete global.wx
        delete global.getApp
      }
    }
  }
})

test('exact terminal no-effect rejections clear only the first intent and allow new coordinates', async () => {
  for (const [action, code] of [
    ['post', 'opening_finalize_state_invalid'],
    ['close', 'opening_finalize_state_invalid'],
    ['close', 'opening_close_reconciliation_pending']
  ]) {
    const keys = [`wxidem-${'c'.repeat(36)}`, `wxidem-${'d'.repeat(36)}`]
    const requests = [`wxreq-${'e'.repeat(36)}`, `wxreq-${'f'.repeat(36)}`]
    const posts = []
    let keyIndex = 0
    let requestIndex = 0
    global.wx = {
      showToast() {},
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey: () => keys[keyIndex++],
        createRequestId: () => requests[requestIndex++],
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return posts.length >= 2
            ? reflectedTerminalDetail(action)
            : terminalDetail(action)
        },
        async post(_pathname, _body, options) {
          posts.push(options)
          if (posts.length === 1) {
            throw responseRejection(412, 'precondition_failed', code)
          }
          return terminalWriteResult(action)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await invokeWriteKind(instance, action)
      assert.equal(instance._pendingWriteIntent, null)
      assert.equal(keyIndex, 1)
      assert.equal(requestIndex, 1)

      await invokeWriteKind(instance, action)
      assert.deepEqual(posts.map((row) => row.idempotencyKey), keys)
      assert.deepEqual(posts.map((row) => row.requestId), requests)
      assert.equal(instance._pendingWriteIntent, null)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('exact response-backed header rejections allow fresh coordinates for terminal writes', async () => {
  for (const kind of ['post', 'close']) {
    for (const code of ['idempotency_key_invalid', 'x_request_id_invalid']) {
      const keys = [`wxidem-${'c'.repeat(36)}`, `wxidem-${'d'.repeat(36)}`]
      const requests = [`wxreq-${'e'.repeat(36)}`, `wxreq-${'f'.repeat(36)}`]
      const posts = []
      let keyIndex = 0
      let requestIndex = 0
      global.wx = {
        showToast() {},
        showModal(options) { options.success({ confirm: true }) }
      }
      global.getApp = () => ({ setUser: () => true })
      const loaded = loadPage('../pages/formal-stocktake-detail/index', {
        '../utils/api': {
          createIdempotencyKey: () => keys[keyIndex++],
          createRequestId: () => requests[requestIndex++],
          async get(pathname) {
            if (pathname === '/auth/me') return USER
            if (pathname === '/access/context') return CONTEXT
            return posts.length >= 2
              ? reflectedDetailForWriteKind(kind)
              : detailForWriteKind(kind)
          },
          async post(_pathname, _body, options) {
            posts.push(options)
            if (posts.length === 1) {
              throw responseRejection(400, 'invalid_request', code)
            }
            return resultForWriteKind(kind)
          }
        },
        '../utils/session': { ensureLogin: () => true }
      })
      try {
        const instance = pageInstance(loaded.definition)
        instance.setData({ taskId: TASK_ID })
        await instance.load()
        await invokeWriteKind(instance, kind)
        assert.equal(instance._pendingWriteIntent, null, `${kind}/${code} must clear its first intent`)
        await invokeWriteKind(instance, kind)

        assert.deepEqual(posts.map((row) => row.idempotencyKey), keys)
        assert.deepEqual(posts.map((row) => row.requestId), requests)
        assert.equal(instance._pendingWriteIntent, null)
      } finally {
        loaded.restore()
        delete global.wx
        delete global.getApp
      }
    }
  }
})

test('an overlapping invocation cannot keep a stale dialog past a first strong rejection', async () => {
  for (const [kind, code] of [
    ['post', 'opening_finalize_state_invalid'],
    ['close', 'opening_close_reconciliation_pending']
  ]) {
    const modalCallbacks = []
    let postCalls = 0
    global.wx = {
      showToast() {},
      showModal(options) { modalCallbacks.push(options.success) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey: () => IDEMPOTENCY_KEY,
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return detailForWriteKind(kind)
        },
        async post() {
          postCalls += 1
          throw responseRejection(412, 'precondition_failed', code)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      const first = invokeWriteKind(instance, kind)
      const overlapping = invokeWriteKind(instance, kind)

      assert.equal(modalCallbacks.length, 1)
      await overlapping
      modalCallbacks[0]({ confirm: true })
      await first

      assert.equal(postCalls, 1)
      assert.equal(instance._pendingWriteIntent, null)
      assert.equal(instance._activeWriteInvocation, null)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('cancelled and failed dialogs release the invocation lease without creating coordinates', async () => {
  for (const [kind, resolution] of [
    ['count', 'cancel'],
    ['post', 'fail']
  ]) {
    const modals = []
    let keyCalls = 0
    let postCalls = 0
    global.wx = {
      showToast() {},
      showModal(options) { modals.push(options) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return detailForWriteKind(kind)
        },
        async post() { postCalls += 1 }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      const first = invokeWriteKind(instance, kind)
      await until(() => modals.length === 1)
      assert.equal(modals.length, 1)
      if (resolution === 'cancel') modals[0].success({ confirm: false })
      else modals[0].fail()
      await first

      assert.equal(instance._activeWriteInvocation, null)
      assert.equal(instance._pendingWriteIntent, undefined)
      assert.equal(keyCalls, 0)
      assert.equal(postCalls, 0)

      const second = invokeWriteKind(instance, kind)
      await until(() => modals.length === 2)
      assert.equal(modals.length, 2)
      modals[1].success({ confirm: false })
      await second
      assert.equal(instance._activeWriteInvocation, null)
      assert.equal(postCalls, 0)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('a first strong rejection cannot clear a different durable count marker', async (context) => {
  await durableCountCase(context, 'replacement-marker')
})

test('durable count retains every non-whitelisted rejection and a later strong-looking GET never permits POST replay', async () => {
  const cases = [
    ...[0, 401, 403, 404, 408, 409, 422, 425, 429, 500, 503, 504].map((status) => [status, 'precondition_failed', 'opening_count_state_invalid', true]),
    [412, 'precondition_failed', 'opening_count_state_invalid', false],
    [412, 'precondition_failed', 'opening_count_state_invalid', 1],
    [412, undefined, undefined, true], [412, 'invalid_request', 'opening_count_state_invalid', true],
    [412, 'precondition_failed', 'unknown_rejection', true], [412, 'precondition_failed', 'database_guard_rejected', true],
    [412, 'precondition_failed', 'opening_finalize_state_invalid', true],
    [412, 'invalid_request', 'idempotency_key_invalid', true],
    [400, 'precondition_failed', 'x_request_id_invalid', true], [400, 'invalid_request', 'unknown_rejection', true]
  ]
  for (const [status, category, code, received] of cases) {
    const f = await durableFixture(null, { onPost() { throw responseRejection(status, category, code, received) } })
    try {
      await f.count(); pending(f)
      const original = JSON.stringify(f.store.read(TASK_ID))
      f.state.statusError = responseRejection(412, 'precondition_failed', 'opening_count_state_invalid')
      await f.recover(); await f.count()
      assert.equal(f.state.posts.length, 1); assert.equal(f.state.keyCalls, 1)
      assert.equal(JSON.stringify(f.store.read(TASK_ID)), original)
    } finally { f.cleanup() }
  }
})

test('strong-looking count response-contract and detail failures never clear durable history', async () => {
  for (const stage of ['result', 'detail']) {
    const strong = responseRejection(412, 'precondition_failed', 'opening_count_state_invalid')
    const f = await durableFixture(null, { onPost(state) {
      if (stage === 'detail') { state.history = true; state.readError = strong; return countWriteResult() }
      const deceptive = {}
      Object.defineProperty(deceptive, 'schema_version', { enumerable: true, get() { throw strong } })
      return deceptive
    } })
    try { await f.count(); pending(f); await f.count(); assert.equal(f.state.posts.length, 1) } finally { f.cleanup() }
  }
})

test('pending count blocks post and close before modal or transport even without a rendered storage update', async () => {
  for (const action of ['post', 'close']) {
    const f = await durableFixture(null, { onPost() { throw unknownError() } })
    try {
      await f.count(); pending(f)
      f.state.current = terminalDetail(action); await f.instance.load()
      // DOM flags are advisory; the task lease must still stop a stale event.
      f.instance.data.countRecoveryBlocked = false
      const before = f.state.modals.length
      await invokeWriteKind(f.instance, action)
      assert.equal(f.state.modals.length, before); assert.equal(f.state.posts.length, 1)
      pending(f)
    } finally { f.cleanup() }
  }
})

test('count confirmation cancellation and modal failure generate no coordinates or persisted marker', async () => {
  for (const result of ['cancel', 'fail']) {
    const f = await durableFixture(null, { holdModal: true })
    try {
      const run = f.count(); await until(() => f.state.modals.length === 1)
      if (result === 'cancel') f.state.modals[0].success({ confirm: false }); else f.state.modals[0].fail()
      await run
      assert.equal(f.state.posts.length, 0); assert.equal(f.state.keyCalls, 0)
      assert.equal(f.store.read(TASK_ID).kind, 'missing'); assert.equal(f.instance._activeWriteInvocation, null)
    } finally { f.cleanup() }
  }
})

test('a hidden page cannot use a late confirmation to send its original count', async (context) => {
  const f = await durableFixture(context, { holdModal: true })
  const run = f.count(); await until(() => f.state.modals.length === 1)
  f.instance.onHide(); const before = JSON.stringify(f.instance.data)
  f.state.modals[0].success({ confirm: true }); await run
  assert.equal(f.state.posts.length, 0); assert.equal(f.state.keyCalls, 0)
  assert.equal(JSON.stringify(f.instance.data), before)
})

test('hidden unloaded reloaded and identity-switched pages cannot clear late confirmed count history', async () => {
  for (const change of ['hide', 'unload', 'reload', 'identity']) {
    const wait = deferred()
    const f = await durableFixture(null, { statusWait: wait.promise })
    try {
      const run = f.count(); await until(() => f.state.requests.some((call) => call.pathname.includes('count-command-status')))
      if (change === 'hide') f.instance.onHide()
      if (change === 'unload') f.instance.onUnload()
      if (change === 'reload') await f.instance.load()
      if (change === 'identity') f.state.sessionUser = Object.assign({}, USER, { authorization_version: 5 })
      const before = JSON.stringify(f.instance.data)
      wait.resolve(); await run
      assert.equal(f.store.read(TASK_ID).kind, 'valid'); assert.equal(f.state.posts.length, 1)
      assert(!f.state.toasts.some((toast) => toast.icon === 'success'))
      if (change === 'hide' || change === 'unload') assert.equal(JSON.stringify(f.instance.data), before)
    } finally { f.cleanup() }
  }
})

test('a changed actor sees only a generic block and cannot inherit the original count draft', async (context) => {
  const f = await durableFixture(context, { onPost() { throw unknownError() } })
  f.instance.setData({ materialIdentifier: 'OLD-PERSON-SKU', quantity: '2' }); f.instance.addObservation()
  await f.instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  pending(f)
  f.state.user = Object.assign({}, USER, { person_id: LOCATION_ID, authorization_version: 8 })
  f.state.context = Object.assign({}, CONTEXT, { person_id: LOCATION_ID, authorization_version: 8 })
  f.state.sessionUser = f.state.user
  await f.instance.load()
  assert.equal(f.instance.data.countRecoveryCanCheck, false)
  assert.equal(f.instance.data.countRecoveryTarget, ''); assert.equal(f.instance.data.countRecoveryTrace, '')
  assert(!JSON.stringify(f.instance.data).includes('OLD-PERSON-SKU'))
  await f.recover(); await f.count(); assert.equal(f.state.posts.length, 1)
  assert.equal(f.store.read(TASK_ID).kind, 'valid')
})

test('an unsent draft cannot move into a later round of the same scope', async (context) => {
  const f = await durableFixture(context)
  f.instance.setData({ materialIdentifier: 'OLD-ROUND-SKU', quantity: '2' }); f.instance.addObservation()
  f.state.current = recountCountDetail(); await f.instance.load()
  assert.equal(f.instance.data.draftObservations.length, 0)
  assert(!JSON.stringify(f.instance.data).includes('OLD-ROUND-SKU'))
  assert.equal(f.state.posts.length, 0)
})

test('legacy volatile count intent is neither replayed nor cleared from a current detail', async (context) => {
  const f = await durableFixture(context)
  const original = { kind: 'count', taskId: TASK_ID, roundId: ROUND_ID, scopeId: SCOPE_ID,
    idempotencyKey: IDEMPOTENCY_KEY, requestId: REQUEST_ID, body: { physical_observations: [], zero_confirmed: true },
    confirmedResponse: { idempotencyKey: IDEMPOTENCY_KEY, requestId: REQUEST_ID, responseSignature: JSON.stringify(countWriteResult()) } }
  f.instance._pendingWriteIntent = original
  f.state.current = reflectedCountDetail()
  await f.instance.load()
  assert.equal(f.instance._pendingWriteIntent, original)
  assert.equal(f.instance.data.pendingWriteRetryable, false)
  await f.count(); await f.recover()
  assert.equal(f.state.posts.length, 0); assert.equal(f.state.keyCalls, 0)
  assert.equal(f.store.read(TASK_ID).kind, 'missing')
})

test('unavailable or corrupt storage blocks count without coordinates, confirmation, or fallback', async () => {
  for (const condition of ['unavailable', 'corrupt']) {
    const f = await durableFixture(null)
    try {
      if (condition === 'unavailable') delete wx.getStorageInfoSync
      else f.values.set(`rsc_oam_mini_opening_count_sentinel_v1:${TASK_ID}`, '{corrupt')
      await f.count()
      assert.equal(f.state.posts.length, 0); assert.equal(f.state.keyCalls, 0); assert.equal(f.state.modals.length, 0)
      assert.equal(f.instance.data.countRecoveryCanCheck, false); assert.equal(f.instance.data.countRecoveryBlocked, true)
      assert.equal(f.instance.data.countRecoveryTrace, '')
    } finally { f.cleanup() }
  }
})

test('fresh preflight cannot submit old draft content into a newer server round before the page reloads', async (context) => {
  const f = await durableFixture(context)
  f.instance.setData({ materialIdentifier: 'OLD-ROUND-SKU', quantity: '2' }); f.instance.addObservation()
  f.state.current = recountCountDetail()
  await f.instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(f.state.modals.length, 0); assert.equal(f.state.posts.length, 0); assert.equal(f.state.keyCalls, 0)
  assert.match(f.state.toasts.at(-1).title, /轮次或范围已变化/)
})

test('a delayed scan cannot mutate a hidden or switched-identity page draft', async (context) => {
  const f = await durableFixture(context)
  const callbacks = []; wx.scanCode = (options) => callbacks.push(options)
  f.instance.scanMaterial(); f.instance.scanSerial()
  f.instance.onHide()
  const before = JSON.stringify(f.instance.data)
  callbacks[0].success({ result: 'LATE-SKU' }); callbacks[1].success({ result: 'LATE-SN' })
  assert.equal(JSON.stringify(f.instance.data), before)
})

test('an unknown POST followed by a whitelisted rejection never clears original coordinates', async () => {
  for (const [kind, status, category, code] of [
    ['post', 412, 'precondition_failed', 'opening_finalize_state_invalid'],
    ['close', 412, 'precondition_failed', 'opening_close_reconciliation_pending'],
    ['post', 400, 'invalid_request', 'x_request_id_invalid'],
    ['close', 400, 'invalid_request', 'idempotency_key_invalid']
  ]) {
    const posts = []
    let keyCalls = 0
    global.wx = {
      showToast() {},
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return detailForWriteKind(kind)
        },
        async post(_pathname, _body, options) {
          posts.push(options)
          if (posts.length === 1) {
            const error = new Error('network outcome unknown')
            error.status = 0
            error.responseReceived = false
            throw error
          }
          throw responseRejection(status, category, code)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await invokeWriteKind(instance, kind)
      const originalIntent = instance._pendingWriteIntent
      await invokeWriteKind(instance, kind)

      assert.equal(posts.length, 2)
      assert.equal(keyCalls, 1)
      assert.equal(instance._pendingWriteIntent, originalIntent)
      assert.deepEqual(posts[0], posts[1])
      assert.equal(instance.data.writePending, true)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('a strong-looking result-contract error is never treated as the direct POST rejection', async () => {
  for (const [kind, code] of [
    ['post', 'opening_finalize_state_invalid'],
    ['close', 'opening_close_reconciliation_pending']
  ]) {
    const contractError = responseRejection(412, 'precondition_failed', code)
    const deceptiveResponse = {}
    Object.defineProperty(deceptiveResponse, 'schema_version', {
      enumerable: true,
      get() { throw contractError }
    })
    let postCalls = 0
    let keyCalls = 0
    global.wx = {
      showToast() {},
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return detailForWriteKind(kind)
        },
        async post() {
          postCalls += 1
          return deceptiveResponse
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await invokeWriteKind(instance, kind)

      assert.equal(postCalls, 1)
      assert.equal(keyCalls, 1)
      assert.ok(instance._pendingWriteIntent)
      assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
      assert.equal(instance._pendingWriteIntent.confirmedResponse, undefined)
      assert.equal(instance.data.writePending, true)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('a strong-looking detail-read rejection cannot clear an accepted POST response', async () => {
  for (const [kind, code] of [
    ['post', 'opening_finalize_state_invalid'],
    ['close', 'opening_close_reconciliation_pending']
  ]) {
    const readError = responseRejection(412, 'precondition_failed', code)
    const toasts = []
    let postCalls = 0
    let keyCalls = 0
    global.wx = {
      showToast(options) { toasts.push(options) },
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (postCalls) throw readError
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return detailForWriteKind(kind)
        },
        async post() {
          postCalls += 1
          return resultForWriteKind(kind)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await invokeWriteKind(instance, kind)

      assert.equal(postCalls, 1)
      assert.equal(keyCalls, 1)
      assert.ok(instance._pendingWriteIntent)
      assert.ok(instance._pendingWriteIntent.confirmedResponse)
      assert.equal(instance.data.writePending, true)
      assert.equal(instance.data.pendingWriteRetryable, false)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('uncertain post and close retries preserve independent exact coordinates', async (context) => {
  for (const action of ['post', 'close']) {
    const posts = []
    let keyCalls = 0
    global.wx = {
      showToast() {},
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return posts.length >= 2
            ? reflectedTerminalDetail(action)
            : terminalDetail(action)
        },
        async post(pathname, body, options) {
          posts.push({ pathname, body, options })
          if (posts.length === 1) {
            const error = new Error('gateway timeout')
            error.status = 504
            throw error
          }
          return terminalWriteResult(action, true)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    const instance = pageInstance(loaded.definition)
    instance.setData({ taskId: TASK_ID })
    await instance.load()
    await instance.terminalAction({ currentTarget: { dataset: { action } } })
    assert.ok(instance._pendingWriteIntent)
    await instance.terminalAction({ currentTarget: { dataset: { action } } })

    assert.equal(posts.length, 2)
    assert.deepEqual(posts[0], posts[1])
    assert.equal(keyCalls, 1)
    assert.equal(instance._pendingWriteIntent, null)
    loaded.restore()
    delete global.wx
    delete global.getApp
  }
})

test('terminal timeout followed by an unmatched posted or closed state never self-confirms', async () => {
  for (const action of ['post', 'close']) {
    const toasts = []
    let postCalls = 0
    let keyCalls = 0
    let requestCalls = 0
    let timedOut = false
    global.wx = {
      showToast(options) { toasts.push(options) },
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId() {
          requestCalls += 1
          return REQUEST_ID
        },
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          if (pathname === `/v1/stocktakes/opening/${TASK_ID}`) {
            return timedOut
              ? reflectedTerminalDetail(action)
              : terminalDetail(action)
          }
          throw new Error(`unexpected ${pathname}`)
        },
        async post() {
          postCalls += 1
          timedOut = true
          const error = new Error('gateway timeout')
          error.status = 504
          throw error
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await instance.terminalAction({ currentTarget: { dataset: { action } } })

      assert.equal(postCalls, 1)
      assert.equal(keyCalls, 1)
      assert.equal(requestCalls, 1)
      assert.ok(instance._pendingWriteIntent)
      assert.equal(instance._pendingWriteIntent.kind, action)
      assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
      assert.equal(instance.data.writePending, true)
      assert.equal(instance.data.pendingWriteRetryable, false)
      assert.match(instance.data.pendingWriteMessage, /不能认定原请求成功/)
      assert.match(instance.data.pendingWriteMessage, /联系管理员/)
      assert.match(instance.data.pendingWriteMessage, /对象和追踪 ID 进行只读核验/)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)

      await instance.terminalAction({
        currentTarget: { dataset: { action: 'close' } }
      })
      assert.equal(postCalls, 1)
      assert.equal(keyCalls, 1)
      assert.equal(instance._pendingWriteIntent.kind, action)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('terminal POST 200 keeps coordinates and never claims success when reread fails', async () => {
  for (const action of ['post', 'close']) {
    const toasts = []
    let failDetailRead = false
    global.wx = {
      showToast(options) { toasts.push(options) },
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey: () => IDEMPOTENCY_KEY,
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          if (failDetailRead) throw new Error('reread unavailable')
          return terminalDetail(action)
        },
        async post() {
          failDetailRead = true
          return terminalWriteResult(action)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()
      await instance.terminalAction({ currentTarget: { dataset: { action } } })

      assert.ok(instance._pendingWriteIntent)
      assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)
      assert.match(toasts.at(-1).title, /回读失败/)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('accepted post and close wait for a compatible current projection without replay', async () => {
  for (const action of ['post', 'close']) {
    const toasts = []
    let postCalls = 0
    let projection = 'stale'
    global.wx = {
      showToast(options) { toasts.push(options) },
      showModal(options) { options.success({ confirm: true }) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey: () => IDEMPOTENCY_KEY,
        createRequestId: () => REQUEST_ID,
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          if (!postCalls || projection === 'stale') return terminalDetail(action)
          if (projection === 'conflict') return conflictingTerminalDetail(action)
          if (projection === 'wrong_round') return wrongRoundTerminalDetail(action)
          return compatibleLaterTerminalDetail(action)
        },
        async post() {
          postCalls += 1
          return terminalWriteResult(action)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()

      await instance.terminalAction({ currentTarget: { dataset: { action } } })

      assert.equal(postCalls, 1)
      assert.ok(instance._pendingWriteIntent.confirmedResponse)
      assert.equal(instance._lastWriteIntentState, 'projection_pending')
      assert.equal(instance.data.pendingWriteRetryable, false)
      assert.match(instance.data.pendingWriteMessage, /成功响应已严格确认/)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)

      await instance.terminalAction({ currentTarget: { dataset: { action } } })
      assert.equal(postCalls, 1)

      projection = 'conflict'
      await instance.load()
      assert.equal(instance._lastWriteIntentState, 'projection_pending')
      assert.ok(instance._pendingWriteIntent)

      projection = 'wrong_round'
      await instance.load()
      assert.equal(instance._lastWriteIntentState, 'projection_pending')
      assert.ok(instance._pendingWriteIntent)

      projection = 'matched'
      await instance.load()
      assert.equal(instance._lastWriteIntentState, 'confirmed')
      assert.equal(instance._pendingWriteIntent, null)
      assert.equal(postCalls, 1)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('a terminal invocation lease permits only one dialog and never replays an accepted response', async () => {
  for (const action of ['post', 'close']) {
    const modalCallbacks = []
    const toasts = []
    let postCalls = 0
    let keyCalls = 0
    let requestCalls = 0
    global.wx = {
      showToast(options) { toasts.push(options) },
      showModal(options) { modalCallbacks.push(options.success) }
    }
    global.getApp = () => ({ setUser: () => true })
    const loaded = loadPage('../pages/formal-stocktake-detail/index', {
      '../utils/api': {
        createIdempotencyKey() {
          keyCalls += 1
          return IDEMPOTENCY_KEY
        },
        createRequestId() {
          requestCalls += 1
          return REQUEST_ID
        },
        async get(pathname) {
          if (pathname === '/auth/me') return USER
          if (pathname === '/access/context') return CONTEXT
          return terminalDetail(action)
        },
        async post() {
          postCalls += 1
          return terminalWriteResult(action)
        }
      },
      '../utils/session': { ensureLogin: () => true }
    })
    try {
      const instance = pageInstance(loaded.definition)
      instance.setData({ taskId: TASK_ID })
      await instance.load()

      const first = instance.terminalAction({ currentTarget: { dataset: { action } } })
      const second = instance.terminalAction({ currentTarget: { dataset: { action } } })
      assert.equal(modalCallbacks.length, 1)
      await second

      modalCallbacks[0]({ confirm: true })
      await first
      assert.equal(postCalls, 1)
      assert.equal(instance._lastWriteIntentState, 'projection_pending')
      assert.ok(instance._pendingWriteIntent.confirmedResponse)

      assert.equal(postCalls, 1)
      assert.equal(keyCalls, 1)
      assert.equal(requestCalls, 1)

      await instance.terminalAction({
        currentTarget: { dataset: { action: action === 'post' ? 'close' : 'post' } }
      })
      assert.equal(modalCallbacks.length, 1)
      assert.equal(postCalls, 1)
      assert.equal(toasts.some((toast) => toast.icon === 'success'), false)
    } finally {
      loaded.restore()
      delete global.wx
      delete global.getApp
    }
  }
})

test('post and close remain separate versioned terminal actions', async (context) => {
  const posts = []
  global.wx = {
    showToast() {},
    showModal(options) { options.success({ confirm: true }) }
  }
  const loaded = loadPage('../pages/formal-stocktake-detail/index', {
    '../utils/api': {
      createIdempotencyKey: () => IDEMPOTENCY_KEY,
      createRequestId: () => REQUEST_ID,
      async post(pathname, body, options) {
        posts.push({ pathname, body, options })
        return terminalWriteResult('post')
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
  })
  const instance = pageInstance(loaded.definition)
  instance.setData({
    taskId: TASK_ID,
    detail: {
      task_id: TASK_ID,
      task_version: 7,
      canPost: true,
      canClose: false
    }
  })
  instance.load = async () => {
    instance._lastWriteIntentState = 'confirmed'
    return true
  }
  instance._verifiedIdentity = { person_id: USER.person_id, authorization_version: USER.authorization_version }
  await instance.terminalAction({ currentTarget: { dataset: { action: 'post' } } })
  await instance.terminalAction({ currentTarget: { dataset: { action: 'close' } } })
  assert.deepEqual(posts, [{
    pathname: `/v1/stocktakes/opening/${TASK_ID}/post`,
    body: { expected_version: 7 },
    options: {
      idempotencyKey: IDEMPOTENCY_KEY,
      requestId: REQUEST_ID
    }
  }])
})
