const assert = require('node:assert/strict')
const test = require('node:test')
const recovery = require('../utils/opening-count-recovery-store')

const MODULE_PATH = require.resolve('../utils/opening-count-recovery-store')
const uuid = (prefix) => `${prefix}0000000-0000-4000-8000-000000000001`
const TASK = uuid('1'), ROUND = uuid('2'), SCOPE = uuid('3'), PERSON = uuid('4'), OTHER_TASK = uuid('5')
const sentinel = Object.freeze({ v: 1, kind: 'opening_scope_count', task_id: TASK,
  round_id: ROUND, round_no: 1, scope_id: SCOPE, actor_person_id: PERSON,
  actor_authorization_version: 7, trace_request_id: 'wxreq-0123456789abcdef0123456789abcdef0123' })
const KEY = recovery.STORAGE_PREFIX + TASK
const OTHER = Object.freeze({ ...sentinel, task_id: OTHER_TASK })

function fixture() {
  const values = new Map(), calls = [], hooks = {}
  const storage = {
    getStorageInfoSync() {
      calls.push(['info'])
      if (hooks.info) return hooks.info()
      return { keys: Array.from(values.keys()), currentSize: 0, limitSize: 10240 }
    },
    getStorageSync(key) {
      calls.push(['get', key])
      if (hooks.get) return hooks.get(key)
      return values.has(key) ? values.get(key) : ''
    },
    setStorageSync(key, value) {
      calls.push(['set', key, value])
      if (hooks.set) return hooks.set(key, value)
      values.set(key, value)
    },
    removeStorageSync(key) {
      calls.push(['remove', key])
      if (hooks.remove) return hooks.remove(key)
      values.delete(key)
    }
  }
  const coordinator = recovery.createOpeningCountCoordinator()
  const makeStore = () => recovery.createOpeningCountRecoveryStore({ storage, coordinator })
  return { values, calls, hooks, storage, coordinator, makeStore, store: makeStore() }
}
function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
async function persist(f, value = sentinel) {
  await f.store.withTaskLease(value.task_id, async (lease) => { lease.persist(value) })
}
function freshModule(context, wxValue) {
  const previous = global.wx
  const previousModule = require.cache[MODULE_PATH]
  global.wx = wxValue
  delete require.cache[MODULE_PATH]
  const fresh = require('../utils/opening-count-recovery-store')
  context.after(() => {
    if (previous === undefined) delete global.wx
    else global.wx = previous
    delete require.cache[MODULE_PATH]
    if (previousModule) require.cache[MODULE_PATH] = previousModule
  })
  return fresh
}

test('sentinel contract is the exact PC public field set in canonical order', () => {
  const checked = recovery.validateOpeningCountSentinel(Object.fromEntries(Object.entries(sentinel).reverse()))
  assert.deepEqual(checked, sentinel)
  assert.deepEqual(Object.keys(checked), Object.keys(sentinel))
  assert.ok(Object.isFrozen(checked))
  assert.notEqual(checked, sentinel)
  assert.equal(recovery.STORAGE_PREFIX, 'rsc_oam_mini_opening_count_sentinel_v1:')
})

for (const field of Object.keys(sentinel)) {
  test(`missing sentinel field ${field} is rejected`, () => {
    const value = { ...sentinel }
    delete value[field]
    assert.throws(() => recovery.validateOpeningCountSentinel(value))
  })
}
for (const field of ['idempotency_key', 'body', 'counted_qty', 'request_hash', 'actor_user_id', 'created_at']) {
  test(`forbidden sentinel field ${field} is rejected, not retained or silently dropped`, () => {
    assert.throws(() => recovery.validateOpeningCountSentinel({ ...sentinel, [field]: 'sensitive' }))
  })
}
for (const [field, value] of [
  ['v', '1'], ['v', 2], ['kind', 'count'], ['round_no', 0], ['round_no', -1],
  ['round_no', 1.5], ['round_no', true], ['round_no', '1'], ['round_no', Number.MAX_SAFE_INTEGER + 1],
  ['actor_authorization_version', 0], ['actor_authorization_version', '7'],
  ['actor_authorization_version', Infinity], ['actor_authorization_version', Number.MAX_SAFE_INTEGER + 1],
  ['trace_request_id', 'short'], ['trace_request_id', 'a'.repeat(161)], ['trace_request_id', 'invalid trace'],
  ['trace_request_id', 'bad\nrequest'], ['trace_request_id', '-invalid-start'],
  ['task_id', '00000000-0000-0000-0000-000000000000'], ['round_id', 'not-a-uuid'],
  ['scope_id', 'A0000000-0000-4000-8000-000000000001'], ['actor_person_id', null]
]) {
  test(`strict sentinel rejects ${field}=${String(value)}`, () => {
    assert.throws(() => recovery.validateOpeningCountSentinel({ ...sentinel, [field]: value }))
  })
}

test('missing keys and present-but-empty or malformed values remain distinct', () => {
  const f = fixture()
  assert.deepEqual(f.store.read(TASK), { kind: 'missing' })
  for (const raw of ['', null, undefined, false, 0, {}, sentinel, '{broken', 'null', '[]', JSON.stringify({ ...sentinel, key: 'secret' })]) {
    f.values.set(KEY, raw)
    assert.deepEqual(f.store.read(TASK), { kind: 'corrupt' })
    assert.equal(f.values.get(KEY), raw)
  }
  f.values.set(KEY, JSON.stringify(OTHER))
  assert.deepEqual(f.store.read(TASK), { kind: 'corrupt' })
})

test('persist writes only canonical JSON coordinates and verifies them before returning', async () => {
  const f = fixture()
  assert.ok(Object.isFrozen(f.store))
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.ok(Object.isFrozen(lease))
    lease.persist(sentinel)
    assert.equal(f.values.get(KEY), JSON.stringify(sentinel))
    assert.deepEqual(lease.read(), { kind: 'valid', value: sentinel })
    assert.ok(Object.isFrozen(lease.read().value))
    const calls = f.calls.slice(f.calls.findIndex(([kind]) => kind === 'set'))
    assert.deepEqual(calls.slice(0, 4).map(([kind]) => kind), ['set', 'info', 'get', 'info'])
    lease.persist(sentinel)
    assert.equal(f.calls.filter(([kind]) => kind === 'set').length, 1)
  })
  assert.deepEqual(Object.keys(JSON.parse(f.values.get(KEY))), Object.keys(sentinel))
  assert.doesNotMatch(f.values.get(KEY), /body|counted_qty|idempotency|hash|token|created_at/)
})

test('exact existing sentinel cannot acquire fresh-persist eligibility in a new lease or factory', async () => {
  const f = fixture()
  await persist(f)
  for (const store of [f.store, f.makeStore()]) {
    await store.withTaskLease(TASK, async (lease) => {
      assert.deepEqual(lease.read(), { kind: 'valid', value: sentinel })
      assert.throws(() => lease.persist(sentinel), /原盘点请求仍待核验/)
      assert.throws(() => lease.persist({ ...sentinel, trace_request_id: 'wxreq-new-request' }))
      assert.throws(() => lease.persist({ ...sentinel, actor_person_id: uuid('6') }))
    })
  }
  assert.equal(f.values.get(KEY), JSON.stringify(sentinel))
  assert.equal(f.calls.filter(([kind]) => kind === 'set').length, 1)
})

test('invalid or cross-task coordinates fail before any storage write', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.throws(() => lease.persist(OTHER))
    assert.throws(() => lease.persist({ ...sentinel, body: 'sensitive' }))
  })
  assert.equal(f.calls.filter(([kind]) => kind === 'set').length, 0)
  assert.equal(f.values.size, 0)
})

test('corrupt sentinel cannot be overwritten or cleared', async () => {
  const f = fixture()
  f.values.set(KEY, '')
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.deepEqual(lease.read(), { kind: 'corrupt' })
    assert.throws(() => lease.persist(sentinel))
    assert.throws(() => lease.clearExact(sentinel))
  })
  assert.equal(f.values.get(KEY), '')
  assert.equal(f.calls.some(([kind]) => kind === 'set' || kind === 'remove'), false)
})

test('clear requires every original coordinate and verifies actual key absence', async () => {
  const f = fixture()
  await persist(f)
  await f.store.withTaskLease(TASK, async (lease) => {
    for (const change of [{ task_id: OTHER_TASK }, { round_id: uuid('6') }, { round_no: 2 },
      { scope_id: uuid('6') }, { actor_person_id: uuid('6') }, { actor_authorization_version: 8 },
      { trace_request_id: 'another-valid-request' }]) {
      assert.throws(() => lease.clearExact({ ...sentinel, ...change }))
      assert.equal(f.values.get(KEY), JSON.stringify(sentinel))
    }
    lease.clearExact(Object.fromEntries(Object.entries(sentinel).reverse()))
    assert.deepEqual(lease.read(), { kind: 'missing' })
    assert.throws(() => lease.clearExact(sentinel))
  })
  assert.equal(f.calls.filter(([kind]) => kind === 'remove').length, 1)
})

test('clear cannot remove a marker that changed after its earlier read', async () => {
  const f = fixture()
  await persist(f)
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.equal(lease.read().kind, 'valid')
    const changed = JSON.stringify({ ...sentinel, trace_request_id: 'another-request-0001' })
    f.values.set(KEY, changed)
    assert.throws(() => lease.clearExact(sentinel))
    assert.equal(f.values.get(KEY), changed)
  })
  assert.equal(f.calls.some(([kind]) => kind === 'remove'), false)
})

for (const method of ['getStorageInfoSync', 'getStorageSync', 'setStorageSync', 'removeStorageSync']) {
  test(`missing ${method} capability fails closed, never using an in-memory fallback`, async () => {
    const f = fixture()
    f.storage[method] = undefined
    assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
    let called = false
    await assert.rejects(f.store.withTaskLease(TASK, async () => { called = true }))
    assert.equal(called, false)
    assert.equal(f.values.size, 0)
  })
}

for (const kind of ['missing_keys', 'non_array_keys', 'non_string_key', 'duplicate_keys', 'info_throw', 'read_throw']) {
  test(`storage ${kind} is unavailable and remains latched across factories`, async () => {
    const f = fixture()
    if (kind === 'missing_keys') f.hooks.info = () => ({ currentSize: 0 })
    if (kind === 'non_array_keys') f.hooks.info = () => ({ keys: '' })
    if (kind === 'non_string_key') f.hooks.info = () => ({ keys: [null] })
    if (kind === 'duplicate_keys') f.hooks.info = () => ({ keys: [KEY, KEY] })
    if (kind === 'info_throw') f.hooks.info = () => { throw new Error('PRIVATE-STORAGE-ERROR') }
    if (kind === 'read_throw') f.hooks.get = () => { throw new Error('PRIVATE-STORAGE-ERROR') }
    assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
    delete f.hooks.info
    delete f.hooks.get
    assert.deepEqual(f.makeStore().read(TASK), { kind: 'unavailable' })
    await assert.rejects(f.makeStore().withTaskLease(TASK, async () => assert.fail('must not run')), (error) => {
      assert.doesNotMatch(String(error), /PRIVATE/)
      assert.equal(error.responseReceived, false)
      return true
    })
    assert.deepEqual(f.makeStore().read(OTHER_TASK), { kind: 'missing' })
  })
}

test('metadata mismatch cannot turn a readable stored value into missing', () => {
  const f = fixture()
  f.values.set(KEY, JSON.stringify(sentinel))
  f.hooks.info = () => ({ keys: [] })
  assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
})

test('presence drift between the directory and value read fails closed', () => {
  const f = fixture()
  f.hooks.get = () => {
    f.values.set(KEY, JSON.stringify(sentinel))
    return ''
  }
  assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
  delete f.hooks.get
  assert.deepEqual(f.makeStore().read(TASK), { kind: 'unavailable' })
})

for (const kind of ['throws_before', 'throws_after', 'noop', 'wrong_value', 'readback_throw', 'directory_throw']) {
  test(`persist ${kind} is fault-latched before any subsequent operation`, async () => {
    const f = fixture()
    f.hooks.set = (key, value) => {
      if (kind === 'throws_before') throw new Error('PRIVATE-WRITE-ERROR')
      if (kind === 'noop') return
      f.values.set(key, kind === 'wrong_value' ? JSON.stringify({ ...sentinel, round_no: 2 }) : value)
      if (kind === 'throws_after') throw new Error('PRIVATE-WRITE-ERROR')
      if (kind === 'readback_throw') f.hooks.get = () => { throw new Error('PRIVATE-READ-ERROR') }
      if (kind === 'directory_throw') f.hooks.info = () => { throw new Error('PRIVATE-INFO-ERROR') }
    }
    let continued = false
    await assert.rejects(f.store.withTaskLease(TASK, async (lease) => {
      lease.persist(sentinel)
      continued = true
    }), (error) => !String(error).includes('PRIVATE'))
    assert.equal(continued, false)
    delete f.hooks.set
    delete f.hooks.get
    delete f.hooks.info
    assert.equal(f.store.read(TASK).kind, 'unavailable')
    assert.equal(f.makeStore().read(TASK).kind, 'unavailable')
    await assert.rejects(f.makeStore().withTaskLease(TASK, async (lease) => lease.persist(sentinel)))
    assert.equal(f.calls.filter(([operation]) => operation === 'set').length, 1)
  })
}

for (const kind of ['throws_before', 'throws_after', 'noop', 'empty_still_present', 'readback_throw', 'directory_throw']) {
  test(`clear ${kind} cannot be mistaken for verified removal`, async () => {
    const f = fixture()
    await persist(f)
    f.hooks.remove = (key) => {
      if (kind === 'throws_before') throw new Error('PRIVATE-CLEAR-ERROR')
      if (kind === 'noop') return
      if (kind === 'empty_still_present') { f.values.set(key, ''); return }
      f.values.delete(key)
      if (kind === 'throws_after') throw new Error('PRIVATE-CLEAR-ERROR')
      if (kind === 'readback_throw') f.hooks.get = () => { throw new Error('PRIVATE-READ-ERROR') }
      if (kind === 'directory_throw') f.hooks.info = () => { throw new Error('PRIVATE-INFO-ERROR') }
    }
    await assert.rejects(f.store.withTaskLease(TASK, async (lease) => lease.clearExact(sentinel)),
      (error) => !String(error).includes('PRIVATE'))
    delete f.hooks.remove
    delete f.hooks.get
    delete f.hooks.info
    assert.equal(f.store.read(TASK).kind, 'unavailable')
    assert.equal(f.makeStore().read(TASK).kind, 'unavailable')
    await assert.rejects(f.makeStore().withTaskLease(TASK, async () => assert.fail('must not run')))
  })
}

test('two stores sharing one coordinator cannot overlap or queue same-task work', async () => {
  const f = fixture(), entered = deferred(), finish = deferred()
  const first = f.store.withTaskLease(TASK, async (lease) => {
    lease.persist(sentinel)
    entered.resolve()
    await finish.promise
    lease.clearExact(sentinel)
    return 'first'
  })
  await entered.promise
  let secondCalled = false
  await assert.rejects(f.makeStore().withTaskLease(TASK, async () => { secondCalled = true }), /同一小程序上下文/)
  finish.resolve()
  assert.equal(await first, 'first')
  assert.equal(secondCalled, false)
  assert.deepEqual(f.store.read(TASK), { kind: 'missing' })
  assert.equal(await f.makeStore().withTaskLease(TASK, async () => 'later explicit call'), 'later explicit call')
})

test('different tasks proceed concurrently in the same coordinator', async () => {
  const f = fixture(), entered = deferred(), finish = deferred()
  const first = f.store.withTaskLease(TASK, async (lease) => {
    lease.persist(sentinel); entered.resolve(); await finish.promise
    return 'first'
  })
  await entered.promise
  await f.makeStore().withTaskLease(OTHER_TASK, async (lease) => lease.persist(OTHER))
  assert.deepEqual(f.store.read(OTHER_TASK), { kind: 'valid', value: OTHER })
  finish.resolve()
  assert.equal(await first, 'first')
})

test('a failed callback releases the service lease but retains durable pending evidence', async () => {
  const f = fixture()
  await assert.rejects(f.store.withTaskLease(TASK, async (lease) => {
    lease.persist(sentinel)
    throw new Error('business uncertain')
  }))
  await f.makeStore().withTaskLease(TASK, async (lease) => {
    assert.deepEqual(lease.read(), { kind: 'valid', value: sentinel })
    assert.throws(() => lease.persist(sentinel))
  })
})

test('expired lease methods never operate, including during a later same-task lease', async () => {
  const f = fixture()
  let old
  await f.store.withTaskLease(TASK, async (lease) => { old = lease; lease.persist(sentinel) })
  for (const operation of [() => old.read(), () => old.persist(sentinel), () => old.clearExact(sentinel)]) assert.throws(operation)
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.throws(() => old.clearExact(sentinel))
    assert.deepEqual(lease.read(), { kind: 'valid', value: sentinel })
  })
})

test('reentrant same-task work rejects immediately instead of deadlocking', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async () => {
    await assert.rejects(f.makeStore().withTaskLease(TASK, async () => assert.fail('nested task')))
  })
})

test('withNoPendingOpeningCount blocks valid and corrupt sentinels without clearing', async () => {
  const f = fixture()
  assert.equal(await recovery.withNoPendingOpeningCount(TASK, async () => 'allowed', f.store), 'allowed')
  for (const raw of [JSON.stringify(sentinel), '']) {
    f.values.set(KEY, raw)
    await assert.rejects(recovery.withNoPendingOpeningCount(TASK, async () => assert.fail('must not run'), f.store))
    assert.equal(f.values.get(KEY), raw)
  }
})

test('withNoPendingOpeningCount holds the same lease for the entire other operation', async () => {
  const f = fixture(), entered = deferred(), finish = deferred()
  const other = recovery.withNoPendingOpeningCount(TASK, async () => { entered.resolve(); await finish.promise; return 'done' }, f.store)
  await entered.promise
  await assert.rejects(f.makeStore().withTaskLease(TASK, async (lease) => lease.persist(sentinel)))
  finish.resolve()
  assert.equal(await other, 'done')
})

test('withNoPendingOpeningCount fails closed on unavailable storage', async () => {
  const f = fixture()
  f.hooks.info = () => { throw new Error('unavailable') }
  await assert.rejects(recovery.withNoPendingOpeningCount(TASK, async () => assert.fail('must not run'), f.store))
})

test('invalid task identifiers and forged or missing coordinators never run callbacks', async () => {
  const f = fixture()
  for (const id of ['', 'not-a-uuid', '00000000-0000-0000-0000-000000000000', 'A0000000-0000-4000-8000-000000000001']) {
    assert.throws(() => f.store.read(id))
    await assert.rejects(f.store.withTaskLease(id, async () => assert.fail('must not run')))
  }
  for (const coordinator of [null, {}, false, { active: new Map(), storageFaults: new Set() }]) {
    const store = recovery.createOpeningCountRecoveryStore({ storage: f.storage, coordinator })
    assert.deepEqual(store.read(TASK), { kind: 'unavailable' })
    await assert.rejects(store.withTaskLease(TASK, async () => assert.fail('must not run')))
  }
})

test('default singleton and additional factories share service-context leases and fault latches', async (context) => {
  const f = fixture(), fresh = freshModule(context, f.storage)
  const first = fresh.getOpeningCountRecoveryStore(), second = fresh.createOpeningCountRecoveryStore()
  assert.equal(first, fresh.getOpeningCountRecoveryStore())
  await first.withTaskLease(TASK, async () => {
    await assert.rejects(second.withTaskLease(TASK, async () => assert.fail('must not run')))
  })
  f.hooks.get = () => { throw new Error('storage failed') }
  assert.equal(first.read(TASK).kind, 'unavailable')
  delete f.hooks.get
  assert.equal(second.read(TASK).kind, 'unavailable')
  assert.equal(fresh.createOpeningCountRecoveryStore().read(TASK).kind, 'unavailable')
})

test('service restart loses no pending coordinates and cannot resurrect a persist permission', async (context) => {
  const f = fixture()
  await persist(f)
  const fresh = freshModule(context, f.storage)
  const restarted = fresh.getOpeningCountRecoveryStore()
  assert.deepEqual(restarted.read(TASK), { kind: 'valid', value: sentinel })
  await restarted.withTaskLease(TASK, async (lease) => {
    assert.throws(() => lease.persist(sentinel))
    lease.clearExact(sentinel)
  })
  assert.equal(restarted.read(TASK).kind, 'missing')
  assert.equal(f.values.has(KEY), false)
})

test('restart after write-then-throw preserves the marker, not the old key, payload or fault reset permission', async (context) => {
  const f = fixture()
  f.hooks.set = (key, value) => { f.values.set(key, value); throw new Error('crash-after-write') }
  await assert.rejects(persist(f))
  assert.equal(f.store.read(TASK).kind, 'unavailable')
  delete f.hooks.set
  const restarted = freshModule(context, f.storage).getOpeningCountRecoveryStore()
  assert.deepEqual(restarted.read(TASK), { kind: 'valid', value: sentinel })
  await restarted.withTaskLease(TASK, async (lease) => assert.throws(() => lease.persist(sentinel)))
  assert.deepEqual(Object.keys(JSON.parse(f.values.get(KEY))), Object.keys(sentinel))
})

test('default store fails closed without wx or storage-info capability', async (context) => {
  const fresh = freshModule(context, undefined)
  const store = fresh.getOpeningCountRecoveryStore()
  assert.equal(store.read(TASK).kind, 'unavailable')
  await assert.rejects(store.withTaskLease(TASK, async () => assert.fail('must not run')))
})
