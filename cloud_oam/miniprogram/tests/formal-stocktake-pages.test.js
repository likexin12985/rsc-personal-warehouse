const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

function loadPage(relativePath, stubs) {
  const savedModules = []
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
      for (const [resolved, saved] of savedModules) {
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
  role_codes: ['technician']
}
const CONTEXT = {
  person_id: USER.person_id,
  access_mode: 'active',
  authorization_version: 4,
  role_codes: ['technician'],
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

test('technician submits one complete scope count to exact formal anchors then rereads', async (context) => {
  const posts = []
  let reloads = 0
  global.wx = {
    showToast() {},
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
        if (pathname === `/v1/stocktakes/opening/${TASK_ID}`) return taskDetail()
        throw new Error(`unexpected ${pathname}`)
      },
      async post(pathname, body, options) {
        posts.push({ pathname, body, options })
        return countWriteResult()
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
  assert.equal(instance.data.selectedScopeId, SCOPE_ID)
  assert.equal(instance.data.detail.scopes[0].quantityLabel, '盲盘中隐藏')

  instance.setData({
    draftObservations: [{
      material_identifier_raw: 'SKU-001',
      material_identifier_type: 'sku_code',
      condition_code: 'new',
      availability_bucket: 'available',
      counted_qty: '2.000',
      lot_no_raw: null,
      serial_no_raw: null,
      serial_identifier_type: null,
      count_method: 'manual',
      reason_code: null,
      remark: ''
    }]
  })
  instance.load = async () => {
    reloads += 1
    instance._lastWriteIntentState = 'confirmed'
    return true
  }
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.deepEqual(posts, [{
    pathname: `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/scopes/${SCOPE_ID}/count`,
    body: {
      physical_observations: [{
        material_identifier_raw: 'SKU-001',
        material_identifier_type: 'sku_code',
        condition_code: 'new',
        availability_bucket: 'available',
        counted_qty: '2.000',
        lot_no_raw: null,
        serial_no_raw: null,
        serial_identifier_type: null,
        count_method: 'manual',
        reason_code: null,
        remark: ''
      }],
      zero_confirmed: false
    },
    options: {
      idempotencyKey: IDEMPOTENCY_KEY,
      requestId: REQUEST_ID
    }
  }])
  assert.equal(reloads, 1)
  assert.deepEqual(instance.data.draftObservations, [])
})

test('count POST 200 keeps coordinates and never claims success when reread fails', async (context) => {
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
        return taskDetail()
      },
      async post() {
        failDetailRead = true
        return countWriteResult()
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
  const observation = {
    material_identifier_raw: 'SKU-001',
    material_identifier_type: 'sku_code',
    condition_code: 'new',
    availability_bucket: 'available',
    counted_qty: '2.000',
    lot_no_raw: null,
    serial_no_raw: null,
    serial_identifier_type: null,
    count_method: 'manual',
    reason_code: null,
    remark: ''
  }
  instance.setData({ taskId: TASK_ID })
  await instance.load()
  instance.setData({ draftObservations: [observation] })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.ok(instance._pendingWriteIntent)
  assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
  assert.deepEqual(instance.data.draftObservations, [observation])
  assert.equal(toasts.some((toast) => toast.icon === 'success'), false)
  assert.match(toasts.at(-1).title, /回读失败/)
})

test('accepted count waits for its exact same-round projection and never posts again', async (context) => {
  const toasts = []
  let postCalls = 0
  let projection = 'pending'
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
        if (!postCalls || projection === 'pending') return taskDetail()
        if (projection === 'unsealed') return completedButUnsealedCountDetail()
        if (projection === 'missing_timestamp') return completedWithoutTimestampCountDetail()
        if (projection === 'wrong_task_status') return conflictingTaskStatusCountDetail()
        if (projection === 'recount') return recountCountDetail()
        return reflectedCountDetail()
      },
      async post() {
        postCalls += 1
        return countWriteResult()
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })
  const observation = {
    material_identifier_raw: 'SKU-PROJECTION-PENDING',
    material_identifier_type: 'sku_code',
    condition_code: 'new',
    availability_bucket: 'available',
    counted_qty: '2.000',
    lot_no_raw: null,
    serial_no_raw: null,
    serial_identifier_type: null,
    count_method: 'manual',
    reason_code: null,
    remark: 'keep until the exact original round is visible'
  }
  const instance = pageInstance(loaded.definition)
  instance.setData({ taskId: TASK_ID })
  await instance.load()
  instance.setData({ draftObservations: [observation] })

  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.equal(postCalls, 1)
  assert.ok(instance._pendingWriteIntent.confirmedResponse)
  assert.equal(instance._lastWriteIntentState, 'projection_pending')
  assert.deepEqual(instance.data.draftObservations, [observation])
  assert.equal(instance.data.pendingWriteRetryable, false)
  assert.match(instance.data.pendingWriteMessage, /成功响应已严格确认/)
  assert.match(instance.data.pendingWriteMessage, /只允许刷新/)
  assert.equal(toasts.some((toast) => toast.icon === 'success'), false)

  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(postCalls, 1)

  projection = 'unsealed'
  await instance.load()
  assert.equal(instance._lastWriteIntentState, 'projection_pending')
  assert.ok(instance._pendingWriteIntent)
  assert.deepEqual(instance.data.draftObservations, [observation])

  projection = 'missing_timestamp'
  await instance.load()
  assert.equal(instance._lastWriteIntentState, 'projection_pending')
  assert.ok(instance._pendingWriteIntent)
  assert.deepEqual(instance.data.draftObservations, [observation])

  projection = 'wrong_task_status'
  await instance.load()
  assert.equal(instance._lastWriteIntentState, 'projection_pending')
  assert.ok(instance._pendingWriteIntent)
  assert.deepEqual(instance.data.draftObservations, [observation])

  projection = 'recount'
  await instance.load()
  assert.equal(instance._lastWriteIntentState, 'projection_pending')
  assert.ok(instance._pendingWriteIntent)
  assert.deepEqual(instance.data.draftObservations, [observation])

  projection = 'matched'
  await instance.load()
  assert.equal(instance._lastWriteIntentState, 'confirmed')
  assert.equal(instance._pendingWriteIntent, null)
  assert.deepEqual(instance.data.draftObservations, [])
  assert.equal(postCalls, 1)
})

test('a count invocation lease permits only one dialog and one in-flight POST', async (context) => {
  const modalCallbacks = []
  let finishPost
  let postCalls = 0
  let keyCalls = 0
  global.wx = {
    showToast() {},
    showModal(options) { modalCallbacks.push(options.success) }
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
        return postCalls ? reflectedCountDetail() : taskDetail()
      },
      post() {
        postCalls += 1
        return new Promise((resolve) => { finishPost = resolve })
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
  instance.setData({
    draftObservations: [{
      material_identifier_raw: 'SKU-DOUBLE-TAP',
      material_identifier_type: 'sku_code',
      condition_code: 'new',
      availability_bucket: 'available',
      counted_qty: '1.000',
      lot_no_raw: null,
      serial_no_raw: null,
      serial_identifier_type: null,
      count_method: 'manual',
      reason_code: null,
      remark: ''
    }]
  })

  const first = instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  const second = instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(modalCallbacks.length, 1)
  await second

  modalCallbacks[0]({ confirm: true })
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(postCalls, 1)
  assert.equal(keyCalls, 1)

  assert.equal(postCalls, 1)
  assert.equal(keyCalls, 1)

  finishPost(countWriteResult())
  await first
  assert.equal(postCalls, 1)
  assert.equal(instance._pendingWriteIntent, null)
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

test('uncertain count retry rereads first and reuses the exact saved coordinates', async (context) => {
  const posts = []
  let idempotencyCalls = 0
  let requestCalls = 0
  let reads = 0
  global.wx = {
    showToast() {},
    showModal(options) { options.success({ confirm: true }) }
  }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/formal-stocktake-detail/index', {
    '../utils/api': {
      createIdempotencyKey() {
        idempotencyCalls += 1
        return IDEMPOTENCY_KEY
      },
      createRequestId() {
        requestCalls += 1
        return REQUEST_ID
      },
      async get(pathname) {
        reads += 1
        if (pathname === '/auth/me') return USER
        if (pathname === '/access/context') return CONTEXT
        return posts.length >= 2 ? reflectedCountDetail() : taskDetail()
      },
      async post(pathname, body, options) {
        posts.push({ pathname, body: JSON.parse(JSON.stringify(body)), options })
        if (posts.length === 1) {
          const error = new Error('network timeout')
          error.status = 0
          throw error
        }
        return countWriteResult(true)
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
  instance.setData({
    draftObservations: [{
      material_identifier_raw: 'SKU-001',
      material_identifier_type: 'sku_code',
      condition_code: 'new',
      availability_bucket: 'available',
      counted_qty: '2.000',
      lot_no_raw: null,
      serial_no_raw: null,
      serial_identifier_type: null,
      count_method: 'manual',
      reason_code: null,
      remark: ''
    }]
  })

  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(reads, 6)
  assert.ok(instance._pendingWriteIntent)
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.equal(posts.length, 2)
  assert.deepEqual(posts[0], posts[1])
  assert.equal(idempotencyCalls, 1)
  assert.equal(requestCalls, 1)
  assert.equal(instance._pendingWriteIntent, null)
})

test('count timeout followed by an unmatched completed scope stays pending and keeps its draft', async (context) => {
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
          return timedOut ? reflectedCountDetail() : taskDetail()
        }
        throw new Error(`unexpected ${pathname}`)
      },
      async post() {
        postCalls += 1
        timedOut = true
        const error = new Error('network timeout')
        error.status = 0
        throw error
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
  const observation = {
    material_identifier_raw: 'SKU-OTHER-COMPLETION',
    material_identifier_type: 'sku_code',
    condition_code: 'new',
    availability_bucket: 'available',
    counted_qty: '3.000',
    lot_no_raw: null,
    serial_no_raw: null,
    serial_identifier_type: null,
    count_method: 'manual',
    reason_code: null,
    remark: 'must survive unknown result'
  }
  instance.setData({ taskId: TASK_ID })
  await instance.load()
  instance.setData({ draftObservations: [observation] })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.equal(postCalls, 1)
  assert.equal(keyCalls, 1)
  assert.equal(requestCalls, 1)
  assert.ok(instance._pendingWriteIntent)
  assert.equal(instance._pendingWriteIntent.idempotencyKey, IDEMPOTENCY_KEY)
  assert.equal(instance._pendingWriteIntent.requestId, REQUEST_ID)
  assert.deepEqual(instance.data.draftObservations, [observation])
  assert.equal(instance.data.writePending, true)
  assert.equal(instance.data.pendingWriteRetryable, false)
  assert.equal(instance.data.selectedScopeId, '')
  assert.match(instance.data.pendingWriteMessage, /不能认定原请求成功/)
  assert.match(instance.data.pendingWriteMessage, /联系管理员/)
  assert.match(instance.data.pendingWriteMessage, /对象和追踪 ID 进行只读核验/)
  assert.equal(Object.prototype.hasOwnProperty.call(instance.data, 'pendingWriteIdempotencyKey'), false)
  assert.equal(JSON.stringify(instance.data).includes(IDEMPOTENCY_KEY), false)
  assert.equal(instance.data.pendingWriteMessage.includes(IDEMPOTENCY_KEY), false)
  assert.doesNotMatch(instance.data.pendingWriteMessage, /Idempotency-Key|幂等键/)
  assert.equal(toasts.some((toast) => toast.icon === 'success'), false)

  instance.removeObservation({ currentTarget: { dataset: { index: 0 } } })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(postCalls, 1)
  assert.equal(keyCalls, 1)
  assert.deepEqual(instance.data.draftObservations, [observation])

  const wxml = fs.readFileSync(
    path.join(__dirname, '../pages/formal-stocktake-detail/index.wxml'),
    'utf8'
  )
  const pageSource = fs.readFileSync(
    path.join(__dirname, '../pages/formal-stocktake-detail/index.js'),
    'utf8'
  )
  assert.doesNotMatch(pageSource, /pendingWriteIdempotencyKey/)
  assert.doesNotMatch(wxml, /Idempotency-Key|pendingWriteIdempotencyKey/)
  assert.equal(wxml.includes(IDEMPOTENCY_KEY), false)
  assert.match(wxml, /X-Request-ID: \{\{pendingWriteRequestId\}\}/)
})

test('first direct exact count no-effect rejection clears coordinates before a new intent', async (context) => {
  const keys = [`wxidem-${'c'.repeat(36)}`, `wxidem-${'d'.repeat(36)}`]
  const posts = []
  let keyIndex = 0
  global.wx = {
    showToast() {},
    showModal(options) { options.success({ confirm: true }) }
  }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/formal-stocktake-detail/index', {
    '../utils/api': {
      createIdempotencyKey: () => keys[keyIndex++],
      createRequestId: () => REQUEST_ID,
      async get(pathname) {
        if (pathname === '/auth/me') return USER
        if (pathname === '/access/context') return CONTEXT
        return posts.length >= 2 ? reflectedCountDetail() : taskDetail()
      },
      async post(_pathname, _body, options) {
        posts.push(options)
        if (posts.length === 1) {
          throw responseRejection(
            412,
            'precondition_failed',
            'opening_count_state_invalid'
          )
        }
        return countWriteResult()
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
  const observation = {
    material_identifier_raw: 'SKU-001',
    material_identifier_type: 'sku_code',
    condition_code: 'new',
    availability_bucket: 'available',
    counted_qty: '1.000',
    lot_no_raw: null,
    serial_no_raw: null,
    serial_identifier_type: null,
    count_method: 'manual',
    reason_code: null,
    remark: ''
  }
  instance.setData({ draftObservations: [observation] })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })
  assert.equal(instance._pendingWriteIntent, null)
  assert.equal(keyIndex, 1)
  instance.setData({ draftObservations: [observation] })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.deepEqual(posts.map((row) => row.idempotencyKey), keys)
  assert.equal(instance._pendingWriteIntent, null)
})

test('count and terminal rejection matrix retains every non-whitelisted first POST', async () => {
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
  for (const kind of ['count', 'post', 'close']) {
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

test('exact response-backed header rejections allow fresh coordinates for count and terminal writes', async () => {
  for (const kind of ['count', 'post', 'close']) {
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
    ['count', 'opening_count_state_invalid'],
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
      assert.equal(modals.length, 1)
      if (resolution === 'cancel') modals[0].success({ confirm: false })
      else modals[0].fail()
      await first

      assert.equal(instance._activeWriteInvocation, null)
      assert.equal(instance._pendingWriteIntent, undefined)
      assert.equal(keyCalls, 0)
      assert.equal(postCalls, 0)

      const second = invokeWriteKind(instance, kind)
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

test('a first strong rejection cannot clear a different current intent reference', async (context) => {
  const replacementKey = `wxidem-${'9'.repeat(36)}`
  let instance
  let replacementIntent
  global.wx = {
    showToast() {},
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
        return taskDetail()
      },
      async post() {
        replacementIntent = Object.assign({}, instance._pendingWriteIntent, {
          idempotencyKey: replacementKey
        })
        instance._pendingWriteIntent = replacementIntent
        throw responseRejection(412, 'precondition_failed', 'opening_count_state_invalid')
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })
  instance = pageInstance(loaded.definition)
  instance.setData({ taskId: TASK_ID })
  await instance.load()
  await invokeWriteKind(instance, 'count')

  assert.equal(instance._pendingWriteIntent, replacementIntent)
  assert.equal(instance._pendingWriteIntent.idempotencyKey, replacementKey)
  assert.equal(instance.data.writePending, true)
})

test('an unknown POST followed by a whitelisted rejection never clears original coordinates', async () => {
  for (const [kind, status, category, code] of [
    ['count', 412, 'precondition_failed', 'opening_count_state_invalid'],
    ['post', 412, 'precondition_failed', 'opening_finalize_state_invalid'],
    ['close', 412, 'precondition_failed', 'opening_close_reconciliation_pending'],
    ['count', 400, 'invalid_request', 'idempotency_key_invalid'],
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
    ['count', 'opening_count_state_invalid'],
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
    ['count', 'opening_count_state_invalid'],
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
