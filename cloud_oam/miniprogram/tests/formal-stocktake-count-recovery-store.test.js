const assert = require('node:assert/strict')
const test = require('node:test')
const {
  STORAGE_PREFIX, validateFormalStocktakeCountSentinel,
  createFormalStocktakeCountCoordinator, createFormalStocktakeCountRecoveryStore
} = require('../utils/formal-stocktake-count-recovery-store')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'

function sentinel(overrides = {}) {
  return validateFormalStocktakeCountSentinel(Object.assign({
    v: 1, kind: 'formal_scope_count', task_id: TASK, round_id: ROUND, round_no: 2,
    scope_id: SCOPE, operation: 'recount_count', expected_task_version: 7,
    actor_person_id: PERSON, actor_authorization_version: 9,
    trace_request_id: 'wx-count-recovery-0001'
  }, overrides))
}

function storageFixture() {
  const values = new Map()
  return {
    values,
    getStorageInfoSync() { return { keys: [...values.keys()] } },
    getStorageSync(key) { return values.has(key) ? values.get(key) : '' },
    setStorageSync(key, value) { values.set(key, value) },
    removeStorageSync(key) { values.delete(key) }
  }
}

test('formal count sentinel rejects body-like or legacy coordinates', () => {
  assert.throws(() => validateFormalStocktakeCountSentinel({ ...sentinel(), body: {} }))
  assert.throws(() => validateFormalStocktakeCountSentinel({ ...sentinel(), kind: 'opening_scope_count' }))
  assert.throws(() => validateFormalStocktakeCountSentinel({ ...sentinel(), operation: 'post' }))
  for (const field of ['task_id', 'round_id', 'scope_id', 'actor_person_id']) {
    assert.throws(() => validateFormalStocktakeCountSentinel({ ...sentinel(), [field]: '00000000-0000-0000-0000-000000000000' }), field)
  }
})

test('formal count store persists and clears one exact scope marker', async () => {
  const storage = storageFixture()
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
  const value = sentinel({ operation: 'initial_count', round_no: 1, expected_task_version: 1 })
  await store.withScopeLease(value, async (lease) => {
    assert.equal(lease.read().kind, 'missing')
    lease.persist(value)
    assert.deepEqual(lease.read(), { kind: 'valid', value })
    assert.equal(store.readPending(TASK).values.length, 1)
    lease.clearExact(value)
    assert.equal(lease.read().kind, 'missing')
  })
  assert.equal([...storage.values.keys()].some((key) => key.startsWith(STORAGE_PREFIX)), false)
})

test('formal count store refuses overwrite and concurrent scope lease', async () => {
  const storage = storageFixture()
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
  const value = sentinel()
  let release
  const active = store.withScopeLease(value, async (lease) => {
    lease.persist(value)
    await new Promise((resolve) => { release = resolve })
  })
  await assert.rejects(() => store.withScopeLease(value, async () => {}), /正在核验/)
  release()
  await active
  await store.withScopeLease(value, async (lease) => {
    assert.equal(lease.read().kind, 'valid')
    assert.throws(() => lease.persist(sentinel({ trace_request_id: 'wx-count-recovery-0002' })), /禁止覆盖/)
  })
})

test('storage failure and corrupt marker stay blocking', async () => {
  const storage = storageFixture()
  const value = sentinel()
  storage.values.set(`${STORAGE_PREFIX}${TASK}:${ROUND}:${SCOPE}:recount_count`, '{bad')
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
  assert.equal(store.read(value).kind, 'corrupt')
  await assert.rejects(() => store.withScopeLease(value, async () => {}), /不可用|不可用/)
})

test('pending scan rejects a valid sentinel stored under the wrong physical key', () => {
  const storage = storageFixture()
  const value = sentinel()
  storage.values.set(`${STORAGE_PREFIX}${TASK}:${ROUND}:${SCOPE}:initial_count`, JSON.stringify(value))
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
  assert.equal(store.readPending(TASK).kind, 'corrupt')
})

test('scope-specific storage failure is visible to the task-level pending scan', async () => {
  const value = sentinel()
  const storage = storageFixture()
  storage.setStorageSync = () => { throw new Error('write failed') }
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
  await assert.rejects(() => store.withScopeLease(value, async (lease) => lease.persist(value)), /写后核验|停止发送请求/)
  assert.equal(store.readPending(TASK).kind, 'unavailable')
})

test('task-level scan latches a directory read failure even after storage recovers', () => {
  const storage = storageFixture()
  const coordinator = createFormalStocktakeCountCoordinator()
  let failDirectory = true
  storage.getStorageInfoSync = () => {
    if (failDirectory) throw new Error('directory unavailable')
    return { keys: [] }
  }
  const store = createFormalStocktakeCountRecoveryStore({ storage, coordinator })
  assert.equal(store.readPending(TASK).kind, 'unavailable')
  failDirectory = false
  assert.equal(store.readPending(TASK).kind, 'unavailable')
})
