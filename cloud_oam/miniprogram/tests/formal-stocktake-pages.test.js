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
  detail.scopes[0].completion_status = 'completed'
  detail.scopes[0].completed_at = '2026-08-31T01:50:00Z'
  detail.allowed_actions = []
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

test('definitive count rejection clears coordinates before a new intent', async (context) => {
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
          const error = new Error('version conflict')
          error.status = 409
          throw error
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
  instance.setData({ draftObservations: [observation] })
  await instance.submitCount({ currentTarget: { dataset: { zero: 'false' } } })

  assert.deepEqual(posts.map((row) => row.idempotencyKey), keys)
  assert.equal(instance._pendingWriteIntent, null)
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
