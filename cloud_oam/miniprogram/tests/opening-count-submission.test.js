const assert = require('node:assert/strict')
const test = require('node:test')

const {
  OpeningCountSubmissionPendingError,
  submitDurableOpeningScopeCount
} = require('../utils/opening-count-submission')
const {
  STORAGE_PREFIX,
  createOpeningCountCoordinator,
  createOpeningCountRecoveryStore
} = require('../utils/opening-count-recovery-store')

const id = (prefix) => `${prefix}0000000-0000-4000-8000-000000000001`
const TASK = id('1')
const ROUND = id('2')
const SCOPE = id('3')
const PERSON = id('4')
const COMPLETION = id('5')
const NEXT_ROUND = id('6')
const OTHER_SCOPE = id('7')
const OTHER_PERSON = id('8')
const COMPLETED = '2026-09-05T01:00:00Z'
const REQUEST_ID = `wxreq-${'a'.repeat(36)}`
const IDEMPOTENCY_KEY = `wxidem-${'b'.repeat(36)}`
const STORAGE_KEY = STORAGE_PREFIX + TASK
const expectedIdentity = Object.freeze({ person_id: PERSON, authorization_version: 7 })
const originalSentinel = Object.freeze({
  v: 1,
  kind: 'opening_scope_count',
  task_id: TASK,
  round_id: ROUND,
  round_no: 1,
  scope_id: SCOPE,
  actor_person_id: PERSON,
  actor_authorization_version: 7,
  trace_request_id: 'wxreq-cccccccccccccccccccccccccccccccccccc'
})

function emptyInput() {
  return { physical_observations: [], zero_confirmed: true }
}

function observation(overrides = {}) {
  return Object.assign({
    material_identifier_type: 'unknown',
    material_identifier_raw: 'FIELD-MATERIAL-001',
    condition_code: 'new',
    availability_bucket: 'available',
    counted_qty: '1.000'
  }, overrides)
}

function activeIdentity() {
  return {
    person_id: PERSON,
    name: '工程师',
    employee_no: 'EMP001',
    organization_code: 'ORG001',
    organization_name: '测试组织',
    account_status: 'active',
    employment_status: 'active',
    access_mode: 'active',
    authorization_version: 7,
    role_codes: ['technician']
  }
}

function access() {
  return {
    person_id: PERSON,
    account_status: 'active',
    employment_status: 'active',
    authorization_version: 7,
    access_mode: 'active',
    role_codes: ['technician'],
    assignments: [],
    permissions: ['read', 'count'].map((action) => ({
      resource: 'stocktake', action, field_code: ''
    }))
  }
}

function beforeDetail() {
  return {
    schema_version: '1.0',
    task_id: TASK,
    task_no: 'OPENING-DURABLE-001',
    region_org_id: id('9'),
    status: 'counting',
    blind_count: true,
    task_version: 3,
    deadline: '2026-09-08T00:00:00Z',
    cutoff_at: '2026-09-04T00:00:00Z',
    current_round: {
      round_id: ROUND,
      round_no: 1,
      round_type: 'initial',
      status: 'counting',
      started_at: '2026-09-05T00:00:00Z',
      submitted_at: null
    },
    evidence_status: 'counting_hidden',
    scopes: [{
      scope_id: SCOPE,
      scope_no: 1,
      location_id: id('a'),
      owner_org_id: id('b'),
      assigned_to_me: true,
      completion_status: 'pending',
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
      completed_at: null
    }],
    observations: [],
    differences: [],
    reviews: [],
    allowed_actions: ['count']
  }
}

function afterDetail() {
  const source = beforeDetail()
  return Object.assign({}, source, {
    status: 'submitted',
    task_version: 4,
    evidence_status: 'sealed',
    current_round: Object.assign({}, source.current_round, {
      status: 'submitted', submitted_at: COMPLETED
    }),
    scopes: [Object.assign({}, source.scopes[0], {
      completion_status: 'completed',
      zero_confirmed: true,
      count_line_count: 0,
      observation_line_count: 0,
      serial_count: 0,
      total_counted_qty: '0.000',
      completed_at: COMPLETED
    })],
    allowed_actions: ['review_region']
  })
}

function laterRoundDetail() {
  const source = beforeDetail()
  return Object.assign({}, source, {
    task_version: 8,
    current_round: Object.assign({}, source.current_round, {
      round_id: NEXT_ROUND,
      round_no: 2,
      round_type: 'recount',
      started_at: '2026-09-05T02:00:00Z'
    })
  })
}

function postResult() {
  return {
    schema_version: '1.0',
    task_id: TASK,
    round_id: ROUND,
    scope_id: SCOPE,
    task_status: 'submitted',
    round_status: 'submitted',
    scope_completed: true,
    round_sealed: true,
    has_pending_verification: false,
    replayed: false
  }
}

function commandStatus(trace, confirmed = true) {
  return {
    schema_version: '1.0',
    task_id: TASK,
    round_id: ROUND,
    scope_id: SCOPE,
    actor_person_id: PERSON,
    actor_authorization_version: 7,
    trace_request_id: trace,
    lookup_status: confirmed ? 'confirmed' : 'not_observed',
    command: confirmed
      ? {
          completion_id: COMPLETION,
          completed_at: COMPLETED,
          round_no: 1,
          scope_completed: true,
          caused_round_submission: true
        }
      : null
  }
}

class MemoryStorage {
  constructor() {
    this.values = new Map()
    this.onInfo = null
    this.onGet = null
    this.onSet = null
    this.onRemove = null
  }

  getStorageInfoSync() {
    if (this.onInfo) this.onInfo()
    return { keys: [...this.values.keys()] }
  }

  getStorageSync(key) {
    if (this.onGet) this.onGet(key)
    return this.values.has(key) ? this.values.get(key) : ''
  }

  setStorageSync(key, value) {
    if (this.onSet) this.onSet(value)
    this.values.set(key, value)
  }

  removeStorageSync(key) {
    if (this.onRemove) this.onRemove()
    this.values.delete(key)
  }
}

function fixture(seed = false) {
  const storage = new MemoryStorage()
  const coordinator = createOpeningCountCoordinator()
  const makeStore = () => createOpeningCountRecoveryStore({ storage, coordinator })
  const store = makeStore()
  if (seed) storage.values.set(STORAGE_KEY, JSON.stringify(originalSentinel))
  const counts = { identity: 0, access: 0, detail: 0, status: 0, post: 0 }
  const coordinateCounts = { request: 0, idempotency: 0 }
  const handlers = {}
  const calls = []
  let trace = originalSentinel.trace_request_id
  const transport = {
    async request(path, options) {
      const kind = path === '/auth/me'
        ? 'identity'
        : path === '/access/context'
          ? 'access'
          : path.includes('/count-command-status?')
            ? 'status'
            : 'detail'
      calls.push({ kind, path, options })
      const n = ++counts[kind]
      if (handlers[kind]) return handlers[kind](n, options)
      if (kind === 'identity') return activeIdentity()
      if (kind === 'access') return access()
      if (kind === 'detail') return counts.post || seed ? afterDetail() : beforeDetail()
      const query = new URLSearchParams(path.split('?')[1])
      assert.equal(query.get('trace_request_id'), trace)
      assert.equal(query.get('actor_person_id'), PERSON)
      assert.deepEqual([...query.keys()].sort(), [
        'actor_authorization_version', 'actor_person_id', 'trace_request_id'
      ])
      return commandStatus(trace)
    },
    async postNoReplay(path, data, options) {
      calls.push({ kind: 'post', path, data, options })
      const n = ++counts.post
      const stored = store.read(TASK)
      assert.equal(stored.kind, 'valid')
      trace = options.requestId
      if (stored.kind === 'valid') assert.equal(stored.value.trace_request_id, trace)
      if (handlers.post) return handlers.post(n, data, options)
      return postResult()
    },
    createRequestId() {
      coordinateCounts.request += 1
      return REQUEST_ID
    },
    createIdempotencyKey() {
      coordinateCounts.idempotency += 1
      return IDEMPOTENCY_KEY
    }
  }
  const submit = (overrides = {}) => submitDurableOpeningScopeCount(Object.assign({
    taskId: TASK,
    scopeId: SCOPE,
    input: emptyInput(),
    expectedIdentity,
    store,
    transport
  }, overrides))
  return {
    storage,
    coordinator,
    store,
    makeStore,
    counts,
    coordinateCounts,
    handlers,
    calls,
    transport,
    submit,
    getTrace: () => trace
  }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function rejected(status, category, code) {
  const error = new Error('SECRET-SERVER-BODY-DO-NOT-EXPOSE')
  Object.assign(error, { status, category, code, responseReceived: true })
  return error
}

test('persists minimal coordinates before one POST and clears only after independent evidence', async () => {
  const f = fixture()
  let persisted = ''
  f.storage.onSet = (value) => {
    persisted = value
    assert.equal(f.counts.post, 0)
  }
  f.storage.onRemove = () => {
    assert.deepEqual(f.counts, { identity: 4, access: 4, detail: 2, status: 1, post: 1 })
  }

  const result = await f.submit()

  assert.equal(result.recovered, false)
  assert.equal(result.command.completion_id, COMPLETION)
  assert.deepEqual(f.store.read(TASK), { kind: 'missing' })
  assert.deepEqual(Object.keys(JSON.parse(persisted)).sort(), Object.keys(originalSentinel).sort())
  assert.equal(persisted.includes(IDEMPOTENCY_KEY), false)
  assert.doesNotMatch(persisted, /physical_observations|zero_confirmed|counted_qty|sha256|request_body|actor_user_id/)
  const post = f.calls.find((row) => row.kind === 'post')
  assert.ok(post)
  assert.equal(post.path, `/v1/stocktakes/opening/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/count`)
  assert.deepEqual(post.data, emptyInput())
  assert.equal(Object.isFrozen(post.data), true)
  assert.equal(Object.isFrozen(post.data.physical_observations), true)
  assert.deepEqual(post.options, { requestId: REQUEST_ID, idempotencyKey: IDEMPOTENCY_KEY })
  assert.deepEqual(f.coordinateCounts, { request: 1, idempotency: 1 })
  assert.deepEqual(f.calls.map((row) => row.kind), [
    'identity', 'access', 'detail', 'identity', 'access', 'post',
    'identity', 'access', 'status', 'detail', 'identity', 'access'
  ])
  assert.equal(Object.isFrozen(result), true)
  for (const call of f.calls.filter((row) => row.kind !== 'post')) {
    assert.deepEqual(call.options, {
      method: 'GET',
      noRefresh: true,
      header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
    })
    assert.equal(Object.prototype.hasOwnProperty.call(call.options.header, 'Idempotency-Key'), false)
  }
})

test('an unknown POST stays pending and another store instance recovers with GET only', async () => {
  const f = fixture()
  f.handlers.post = () => { throw new Error('SECRET-BODY-AND-KEY') }
  f.handlers.status = () => commandStatus(f.getTrace(), false)

  const error = await f.submit().catch((value) => value)

  assert.ok(error instanceof OpeningCountSubmissionPendingError)
  assert.doesNotMatch(JSON.stringify(error), /SECRET|physical_observations|Idempotency|cause/)
  assert.equal(error.task_id, TASK)
  assert.equal(error.round_id, ROUND)
  assert.equal(error.scope_id, SCOPE)
  assert.equal(error.trace_request_id, REQUEST_ID)
  assert.equal(Object.prototype.hasOwnProperty.call(error, 'cause'), false)
  assert.equal(f.store.read(TASK).kind, 'valid')

  delete f.handlers.status
  const result = await f.submit({
    store: f.makeStore(),
    input: { physical_observations: [], zero_confirmed: false }
  })
  assert.equal(result.recovered, true)
  assert.equal(f.counts.post, 1)
  assert.equal(f.store.read(TASK).kind, 'missing')
  assert.deepEqual(f.coordinateCounts, { request: 1, idempotency: 1 })
})

test('recovers a lost POST response in the same call without retransmission', async () => {
  const f = fixture()
  f.handlers.post = () => { throw new TypeError('network uncertain') }
  const result = await f.submit()
  assert.equal(result.recovered, true)
  assert.equal(f.counts.post, 1)
})

test('an existing sentinel performs GET-only recovery without reading the new form', async () => {
  const f = fixture(true)
  const input = {
    get zero_confirmed() { throw new Error('must not inspect form') },
    physical_observations: []
  }
  const result = await f.submit({ scopeId: OTHER_SCOPE, input })
  assert.equal(result.recovered, true)
  assert.equal(f.counts.post, 0)
  assert.equal(f.storage.values.size, 0)
  assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
})

test('returns historical round-one completion separately from a pending round-two detail', async () => {
  const f = fixture(true)
  f.handlers.detail = () => laterRoundDetail()
  const result = await f.submit()
  assert.equal(result.recovered, true)
  assert.equal(result.command.round_no, 1)
  assert.equal(result.detail.current_round.round_no, 2)
  assert.equal(result.detail.scopes[0].completion_status, 'pending')
  assert.equal(f.counts.post, 0)
})

test('only exact first-POST rejection triples clear the sentinel', async (t) => {
  const cases = [
    [400, 'invalid_request', 'idempotency_key_invalid'],
    [400, 'invalid_request', 'x_request_id_invalid'],
    [412, 'precondition_failed', 'opening_count_state_invalid']
  ]
  for (const [status, category, code] of cases) {
    await t.test(`${status}/${category}/${code}`, async () => {
      const f = fixture()
      f.handlers.post = () => { throw rejected(status, category, code) }
      const error = await f.submit().catch((value) => value)
      assert.equal(error instanceof OpeningCountSubmissionPendingError, false)
      assert.equal(error.status, status)
      assert.equal(error.category, category)
      assert.equal(error.code, code)
      assert.doesNotMatch(String(error), /SECRET/)
      assert.equal(f.store.read(TASK).kind, 'missing')
      assert.deepEqual(f.counts, { identity: 3, access: 3, detail: 1, status: 0, post: 1 })
    })
  }
})

test('all other first-POST rejections remain pending', async (t) => {
  const cases = [
    [400, 'invalid_request', 'opening_count_state_invalid'],
    [412, 'precondition_failed', 'x_request_id_invalid'],
    [412, 'invalid_request', 'opening_count_state_invalid'],
    [409, 'conflict', 'opening_count_state_invalid'],
    [422, 'invalid_request', 'validation_error'],
    [401, 'unauthorized', 'authentication_required'],
    [503, 'precondition_failed', 'opening_count_state_invalid']
  ]
  for (const [status, category, code] of cases) {
    await t.test(`${status}/${category}/${code}`, async () => {
      const f = fixture()
      f.handlers.post = () => { throw rejected(status, category, code) }
      f.handlers.status = () => commandStatus(f.getTrace(), false)
      await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
      assert.equal(f.store.read(TASK).kind, 'valid')
      assert.equal(f.counts.post, 1)
    })
  }
})

test('identity, access, or page drift before clearing keeps an exact rejection pending', async (t) => {
  for (const kind of ['identity', 'access', 'page']) {
    await t.test(kind, async () => {
      const f = fixture()
      f.handlers.post = () => { throw rejected(412, 'precondition_failed', 'opening_count_state_invalid') }
      if (kind === 'identity') {
        f.handlers.identity = (n) => Object.assign(activeIdentity(), {
          person_id: n <= 2 ? PERSON : OTHER_PERSON
        })
      } else if (kind === 'access') {
        f.handlers.access = (n) => Object.assign(access(), {
          authorization_version: n <= 2 ? 7 : 8
        })
      }
      const canCommit = kind === 'page' ? () => f.counts.post === 0 : () => true
      await assert.rejects(f.submit({ canCommit }), OpeningCountSubmissionPendingError)
      assert.equal(f.store.read(TASK).kind, 'valid')
      assert.equal(f.counts.post, 1)
      assert.equal(f.counts.status, 0)
    })
  }
})

test('a whitelisted GET failure during rejection readback never clears history', async () => {
  const f = fixture()
  f.handlers.post = () => { throw rejected(412, 'precondition_failed', 'opening_count_state_invalid') }
  f.handlers.identity = (n) => {
    if (n <= 2) return activeIdentity()
    throw rejected(400, 'invalid_request', 'x_request_id_invalid')
  }
  await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
  assert.equal(f.store.read(TASK).kind, 'valid')
  assert.equal(f.counts.post, 1)
})

test('later GET rejections cannot be mistaken for the first POST rejection', async (t) => {
  for (const kind of ['status', 'detail', 'identity', 'access']) {
    await t.test(kind, async () => {
      const f = fixture()
      f.handlers[kind] = (n) => {
        if (kind === 'identity' && n <= 2) return activeIdentity()
        if (kind === 'access' && n <= 2) return access()
        if (kind === 'detail' && n === 1) return beforeDetail()
        throw rejected(412, 'precondition_failed', 'opening_count_state_invalid')
      }
      await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
      assert.equal(f.store.read(TASK).kind, 'valid')
      assert.equal(f.counts.post, 1)
    })
  }
})

test('an error-shaped success object cannot release the sentinel', async () => {
  const f = fixture()
  f.handlers.post = () => ({
    status: 412,
    category: 'precondition_failed',
    code: 'opening_count_state_invalid',
    responseReceived: true
  })
  f.handlers.status = () => commandStatus(f.getTrace(), false)
  await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
  assert.equal(f.store.read(TASK).kind, 'valid')
})

test('malformed or mismatched POST contracts are only recovery signals', async (t) => {
  const changes = [
    { scope_completed: 1 },
    { round_sealed: 'true' },
    { replayed: 0 },
    { task_id: OTHER_PERSON },
    { scope_id: OTHER_SCOPE },
    { round_id: NEXT_ROUND },
    { round_status: 'counting' },
    { schema_version: '2.0' },
    { secret_payload: 'must not be returned' }
  ]
  for (const change of changes) {
    await t.test(JSON.stringify(change), async () => {
      const f = fixture()
      f.handlers.post = () => Object.assign(postResult(), change)
      const result = await f.submit()
      assert.equal(result.recovered, true)
      assert.equal(f.counts.post, 1)
      assert.equal(f.counts.status, 1)
      assert.equal(JSON.stringify(result).includes('secret_payload'), false)
    })
  }
})

test('a successful-looking POST stays pending without historical confirmation', async () => {
  const f = fixture()
  f.handlers.status = () => commandStatus(f.getTrace(), false)
  await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
  assert.equal(f.store.read(TASK).kind, 'valid')
  assert.equal(f.counts.detail, 1)
})

test('replayed and mismatching seal responses are reported only as recovered history', async (t) => {
  await t.test('replayed', async () => {
    const f = fixture()
    f.handlers.post = () => Object.assign(postResult(), { replayed: true })
    const result = await f.submit()
    assert.equal(result.recovered, true)
  })
  await t.test('seal mismatch', async () => {
    const f = fixture()
    f.handlers.post = () => Object.assign(postResult(), {
      round_sealed: false,
      round_status: 'counting',
      task_status: 'counting'
    })
    const result = await f.submit()
    assert.equal(result.recovered, true)
    assert.equal(result.command.caused_round_submission, true)
  })
})

test('initial identity and permission failures create neither marker nor POST', async (t) => {
  for (const kind of ['person', 'authversion', 'inactive', 'read', 'count']) {
    await t.test(kind, async () => {
      const f = fixture()
      if (kind === 'person') f.handlers.identity = () => Object.assign(activeIdentity(), { person_id: OTHER_PERSON })
      if (kind === 'authversion') f.handlers.identity = () => Object.assign(activeIdentity(), { authorization_version: 8 })
      if (kind === 'inactive') f.handlers.identity = () => Object.assign(activeIdentity(), { account_status: 'disabled' })
      if (kind === 'read' || kind === 'count') {
        f.handlers.access = () => Object.assign(access(), {
          permissions: access().permissions.filter((permission) => permission.action !== kind)
        })
      }
      await assert.rejects(f.submit())
      assert.equal(f.counts.post, 0)
      assert.equal(f.storage.values.size, 0)
    })
  }
})

test('identity and access are rechecked immediately before persistence', async (t) => {
  for (const kind of ['identity', 'access']) {
    await t.test(kind, async () => {
      const f = fixture()
      f.handlers[kind] = (n) => kind === 'identity'
        ? Object.assign(activeIdentity(), { person_id: n === 1 ? PERSON : OTHER_PERSON })
        : Object.assign(access(), { authorization_version: n === 1 ? 7 : 8 })
      await assert.rejects(f.submit())
      assert.equal(f.counts.post, 0)
      assert.equal(f.storage.values.size, 0)
    })
  }
})

test('fresh detail must identify the exact authorized pending scope', async (t) => {
  for (const kind of ['task', 'version', 'round', 'scope', 'assignment', 'completed', 'action']) {
    await t.test(kind, async () => {
      const f = fixture()
      f.handlers.detail = () => {
        const value = beforeDetail()
        if (kind === 'task') value.task_id = OTHER_PERSON
        if (kind === 'version') value.task_version = Number.MAX_SAFE_INTEGER + 1
        if (kind === 'round') value.current_round.round_type = 'recount'
        if (kind === 'scope') value.scopes[0].scope_id = OTHER_SCOPE
        if (kind === 'assignment') value.scopes[0].assigned_to_me = false
        if (kind === 'completed') {
          value.scopes[0].completion_status = 'completed'
          value.scopes[0].completed_at = COMPLETED
        }
        if (kind === 'action') value.allowed_actions = []
        return value
      }
      await assert.rejects(f.submit())
      assert.equal(f.counts.post, 0)
      assert.equal(f.storage.values.size, 0)
    })
  }
})

test('serializes and deeply freezes the exact body before the second identity check', async () => {
  const f = fixture()
  const input = {
    physical_observations: [{
      material_identifier_type: 'unknown',
      material_identifier_raw: 'SECRET-MATERIAL',
      condition_code: 'new',
      availability_bucket: 'available',
      counted_qty: '2.000'
    }],
    zero_confirmed: false
  }
  f.handlers.identity = (n) => {
    if (n === 2) input.physical_observations[0].counted_qty = '999.000'
    return activeIdentity()
  }
  await f.submit({ input })
  const post = f.calls.find((row) => row.kind === 'post')
  assert.equal(post.data.physical_observations[0].counted_qty, '2.000')
  assert.equal(Object.isFrozen(post.data), true)
  assert.equal(Object.isFrozen(post.data.physical_observations), true)
  assert.equal(Object.isFrozen(post.data.physical_observations[0]), true)
})

test('quantity overflow, multi-piece SN, and duplicate explicit dimensions stop before coordinates', async (t) => {
  const duplicateDimension = observation({
    material_id: id('c'),
    lot_id: id('d'),
    lot_no_raw: 'LOT-001',
    counted_qty: '1.000',
    remark: 'first quantity'
  })
  const cases = {
    'numeric(18,3) overflow': [observation({ counted_qty: '1000000000000000.000' })],
    'SN quantity greater than one': [observation({
      counted_qty: '2.000',
      serial_no_raw: 'SN-001',
      serial_identifier_type: 'serial_no'
    })],
    'same raw and master dimension twice': [
      duplicateDimension,
      Object.assign({}, duplicateDimension, { counted_qty: '2.000', remark: 'different remark' })
    ]
  }
  for (const [name, physicalObservations] of Object.entries(cases)) {
    await t.test(name, async () => {
      const f = fixture()
      await assert.rejects(f.submit({
        input: { physical_observations: physicalObservations, zero_confirmed: false }
      }))
      assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
      assert.equal(f.storage.values.size, 0)
      assert.equal(f.counts.post, 0)
    })
  }
})

test('numeric upper bound, minimum increment, and one-piece SN remain valid', async (t) => {
  const cases = {
    '999999999999999.999': observation({ counted_qty: '999999999999999.999' }),
    '0.001': observation({ counted_qty: '0.001' }),
    'SN 1.000': observation({
      counted_qty: '1.000',
      serial_no_raw: 'SN-LEGAL-001',
      serial_identifier_type: 'serial_no'
    })
  }
  for (const [name, value] of Object.entries(cases)) {
    await t.test(name, async () => {
      const f = fixture()
      const result = await f.submit({
        input: { physical_observations: [value], zero_confirmed: false }
      })
      assert.equal(result.recovered, false)
      assert.equal(f.counts.post, 1)
      assert.deepEqual(f.coordinateCounts, { request: 1, idempotency: 1 })
      assert.equal(f.store.read(TASK).kind, 'missing')
      const post = f.calls.find((row) => row.kind === 'post')
      assert.deepEqual(post.data.physical_observations, [value])
    })
  }
})

test('serialization failures and altered toJSON output stop before persistence', async (t) => {
  await t.test('serialization failure is sanitized', async () => {
    const f = fixture()
    const input = Object.assign(emptyInput(), {
      toJSON() { throw new Error('SECRET-FORM') }
    })
    const error = await f.submit({ input }).catch((value) => value)
    assert.doesNotMatch(String(error), /SECRET/)
    assert.equal(f.storage.values.size, 0)
    assert.equal(f.counts.post, 0)
  })
  await t.test('toJSON is revalidated', async () => {
    const f = fixture()
    const input = Object.assign(emptyInput(), {
      toJSON() { return { physical_observations: [], zero_confirmed: false } }
    })
    await assert.rejects(f.submit({ input }))
    assert.equal(f.storage.values.size, 0)
    assert.equal(f.counts.post, 0)
  })
})

test('page changes before, at, and after persistence fail closed at the right boundary', async (t) => {
  await t.test('before persistence', async () => {
    const f = fixture()
    await assert.rejects(f.submit({ canCommit: () => false }))
    assert.equal(f.storage.values.size, 0)
    assert.equal(f.counts.post, 0)
  })
  await t.test('exactly at persistence', async () => {
    const f = fixture()
    let current = true
    f.storage.onSet = () => { current = false }
    await assert.rejects(f.submit({ canCommit: () => current }), OpeningCountSubmissionPendingError)
    assert.equal(f.store.read(TASK).kind, 'valid')
    assert.equal(f.counts.post, 0)
  })
  await t.test('during recovery', async () => {
    const f = fixture()
    await assert.rejects(
      f.submit({ canCommit: () => f.counts.post === 0 }),
      OpeningCountSubmissionPendingError
    )
    assert.equal(f.store.read(TASK).kind, 'valid')
    assert.equal(f.counts.post, 1)
  })
})

test('storage write, reread, and clear failures latch across store instances', async (t) => {
  for (const kind of ['write', 'reread', 'remove']) {
    await t.test(kind, async () => {
      const f = fixture()
      if (kind === 'write') f.storage.onSet = () => { throw new Error('storage write failed') }
      if (kind === 'reread') {
        f.storage.onSet = () => {
          f.storage.onGet = () => { throw new Error('reread failed') }
        }
      }
      if (kind === 'remove') f.storage.onRemove = () => { throw new Error('clear failed') }
      await assert.rejects(f.submit())
      assert.equal(f.counts.post, kind === 'remove' ? 1 : 0)
      assert.equal(f.store.read(TASK).kind, 'unavailable')
      await assert.rejects(f.submit({ store: f.makeStore() }))
      assert.equal(f.counts.post, kind === 'remove' ? 1 : 0)
    })
  }
})

test('an exact rejection that cannot clear remains a pending command', async () => {
  const f = fixture()
  f.handlers.post = () => { throw rejected(412, 'precondition_failed', 'opening_count_state_invalid') }
  f.storage.onRemove = () => { throw new Error('clear failed') }
  await assert.rejects(f.submit(), OpeningCountSubmissionPendingError)
  assert.equal(f.storage.values.size, 1)
  assert.equal(f.counts.post, 1)
})

test('corrupt or unavailable recovery facilities block before transport', async (t) => {
  for (const kind of ['corrupt', 'unavailable', 'no_storage', 'no_coordinator']) {
    await t.test(kind, async () => {
      const f = fixture()
      if (kind === 'corrupt') f.storage.values.set(STORAGE_KEY, '{bad')
      if (kind === 'unavailable') f.storage.onGet = () => { throw new Error('storage unavailable') }
      const store = kind === 'no_storage'
        ? createOpeningCountRecoveryStore({ storage: null, coordinator: f.coordinator })
        : kind === 'no_coordinator'
          ? createOpeningCountRecoveryStore({ storage: f.storage, coordinator: null })
          : f.store
      await assert.rejects(f.submit({ store }))
      assert.equal(f.calls.length, 0)
      assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
    })
  }
})

test('missing single-send or coordinate capabilities block before any GET or marker', async () => {
  const f = fixture()
  const transport = { request: f.transport.request.bind(f.transport) }
  await assert.rejects(f.submit({ transport }))
  assert.equal(f.calls.length, 0)
  assert.equal(f.storage.values.size, 0)
})

test('invalid generated coordinates block before persistence and POST', async (t) => {
  for (const kind of ['request', 'idempotency']) {
    await t.test(kind, async () => {
      const f = fixture()
      if (kind === 'request') f.transport.createRequestId = () => 'bad-request'
      else f.transport.createIdempotencyKey = () => 'bad-idempotency'
      await assert.rejects(f.submit())
      assert.equal(f.storage.values.size, 0)
      assert.equal(f.counts.post, 0)
    })
  }
})

test('confirmation cancellation returns null without coordinates, marker, or POST', async () => {
  const f = fixture()
  let confirmations = 0
  const result = await f.submit({
    confirm: async (meta) => {
      confirmations += 1
      assert.equal(Object.isFrozen(meta), true)
      assert.deepEqual(meta, {
        task_id: TASK,
        round_id: ROUND,
        scope_id: SCOPE,
        zero_confirmed: true,
        observation_count: 0
      })
      assert.doesNotMatch(JSON.stringify(meta), /counted_qty|material_identifier|Idempotency|requestId/)
      return false
    }
  })
  assert.equal(result, null)
  assert.equal(confirmations, 1)
  assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
  assert.equal(f.storage.values.size, 0)
  assert.equal(f.counts.post, 0)
  assert.deepEqual(f.counts, { identity: 1, access: 1, detail: 1, status: 0, post: 0 })
})

test('confirmation failure has no durable or transport side effect', async () => {
  const f = fixture()
  await assert.rejects(f.submit({
    confirm: async () => { throw new Error('confirmation unavailable') }
  }), /confirmation unavailable/)
  assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
  assert.equal(f.storage.values.size, 0)
  assert.equal(f.counts.post, 0)
})

test('existing historical marker bypasses confirmation entirely', async () => {
  const f = fixture(true)
  let confirmations = 0
  const result = await f.submit({
    confirm: async () => {
      confirmations += 1
      throw new Error('must not confirm historical recovery')
    }
  })
  assert.equal(result.recovered, true)
  assert.equal(confirmations, 0)
  assert.equal(f.counts.post, 0)
})

test('the task lease prevents a second store from opening a queued confirmation', async () => {
  const f = fixture()
  const opened = deferred()
  const finish = deferred()
  let confirmations = 0
  const first = f.submit({
    confirm: async () => {
      confirmations += 1
      opened.resolve()
      await finish.promise
      return false
    }
  })
  await opened.promise
  await assert.rejects(f.submit({
    store: f.makeStore(),
    confirm: async () => {
      confirmations += 1
      return true
    }
  }), /正在核验|重复提交/)
  assert.equal(confirmations, 1)
  assert.equal(f.counts.post, 0)
  finish.resolve()
  assert.equal(await first, null)
  assert.equal(f.storage.values.size, 0)
})

test('page drift before or while confirming stops before coordinates and persistence', async (t) => {
  await t.test('before confirmation', async () => {
    const f = fixture()
    let confirmations = 0
    await assert.rejects(f.submit({
      canCommit: () => false,
      confirm: async () => { confirmations += 1; return true }
    }))
    assert.equal(confirmations, 0)
    assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
    assert.equal(f.storage.values.size, 0)
    assert.equal(f.counts.post, 0)
  })
  await t.test('while confirmation is open', async () => {
    const f = fixture()
    let current = true
    const opened = deferred()
    const finish = deferred()
    const pending = f.submit({
      canCommit: () => current,
      confirm: async () => {
        opened.resolve()
        await finish.promise
        return true
      }
    })
    await opened.promise
    current = false
    finish.resolve()
    await assert.rejects(pending)
    assert.deepEqual(f.coordinateCounts, { request: 0, idempotency: 0 })
    assert.equal(f.storage.values.size, 0)
    assert.equal(f.counts.post, 0)
  })
})

test('the service-context lease covers POST and recovery and never queues another instance', async () => {
  const f = fixture()
  const postStarted = deferred()
  const postFinish = deferred()
  f.handlers.post = async () => {
    postStarted.resolve()
    await postFinish.promise
    return postResult()
  }
  const first = f.submit()
  await postStarted.promise
  await assert.rejects(f.submit({ store: f.makeStore() }), /正在核验|重复提交/)
  assert.equal(f.counts.post, 1)
  postFinish.resolve()
  const result = await first
  assert.equal(result.recovered, false)

  const statusStarted = deferred()
  const statusFinish = deferred()
  const second = fixture()
  second.handlers.status = async () => {
    statusStarted.resolve()
    await statusFinish.promise
    return commandStatus(second.getTrace())
  }
  const proving = second.submit()
  await statusStarted.promise
  assert.equal(second.store.read(TASK).kind, 'valid')
  await assert.rejects(second.submit({ store: second.makeStore() }), /正在核验|重复提交/)
  assert.equal(second.counts.post, 1)
  statusFinish.resolve()
  await proving
  assert.equal(second.store.read(TASK).kind, 'missing')
})

test('a concurrent second instance cannot become a new POST after the first is rejected', async () => {
  const f = fixture()
  const started = deferred()
  const finish = deferred()
  f.handlers.post = async () => {
    started.resolve()
    await finish.promise
    throw rejected(412, 'precondition_failed', 'opening_count_state_invalid')
  }
  const first = f.submit().catch((error) => error)
  await started.promise
  await assert.rejects(f.submit({ store: f.makeStore() }), /正在核验|重复提交/)
  finish.resolve()
  const error = await first
  assert.equal(error instanceof OpeningCountSubmissionPendingError, false)
  assert.equal(error.code, 'opening_count_state_invalid')
  assert.equal(f.counts.post, 1)
  assert.equal(f.store.read(TASK).kind, 'missing')
})
