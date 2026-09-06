const assert = require('node:assert/strict')
const test = require('node:test')
const recovery = require('../utils/formal-stocktake-post-recovery-store')

const TASK = '10000000-0000-4000-8000-000000000001'
const OTHER = '10000000-0000-4000-8000-000000000002'
const PERSON = '40000000-0000-4000-8000-000000000001'
const marker = Object.freeze({ v: 1, kind: 'formal_stocktake_post', task_id: TASK,
  expected_task_version: 7, actor_person_id: PERSON, actor_authorization_version: 9,
  trace_request_id: 'wx-post-recovery-0001' })

function fixture() {
  const values = new Map(); const hooks = {}
  const storage = {
    getStorageInfoSync() { if (hooks.info) return hooks.info(); return { keys: [...values.keys()] } },
    getStorageSync(key) { if (hooks.get) return hooks.get(key); return values.has(key) ? values.get(key) : '' },
    setStorageSync(key, value) { if (hooks.set) return hooks.set(key, value); values.set(key, value) },
    removeStorageSync(key) { if (hooks.remove) return hooks.remove(key); values.delete(key) },
  }
  const coordinator = recovery.createFormalStocktakePostCoordinator()
  return { values, hooks, store: recovery.createFormalStocktakePostRecoveryStore({ storage, coordinator }) }
}

test('sentinel stores only the exact public coordinate fields', () => {
  const value = recovery.validateFormalStocktakePostSentinel({ ...marker })
  assert.deepEqual(Object.keys(value), ['v', 'kind', 'task_id', 'expected_task_version', 'actor_person_id', 'actor_authorization_version', 'trace_request_id'])
  assert.ok(Object.isFrozen(value))
  assert.equal(recovery.STORAGE_PREFIX, 'rsc_oam_mini_formal_post_sentinel_v1:')
  assert.throws(() => recovery.validateFormalStocktakePostSentinel({ ...marker, body: 'secret' }))
})

test('read and persist round trip canonical coordinates', async () => {
  const f = fixture(); const key = recovery.STORAGE_PREFIX + TASK
  assert.deepEqual(f.store.read(TASK), { kind: 'missing' })
  await f.store.withTaskLease(TASK, async (lease) => {
    lease.persist(marker)
    assert.deepEqual(lease.read(), { kind: 'valid', value: recovery.validateFormalStocktakePostSentinel(marker) })
  })
  assert.equal(f.values.get(key), JSON.stringify(marker))
  assert.equal(f.store.readPending().values.length, 1)
})

test('existing marker cannot be overwritten by a new lease or person', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async (lease) => lease.persist(marker))
  await assert.rejects(() => f.store.withTaskLease(TASK, async (lease) => lease.persist({ ...marker, trace_request_id: 'wx-post-recovery-new' })), /禁止覆盖/)
  assert.equal(f.values.size, 1)
})

test('same task lease is exclusive and releases after callback', async () => {
  const f = fixture(); let release
  const hold = new Promise((resolve) => { release = resolve })
  const first = f.store.withTaskLease(TASK, async () => hold)
  await new Promise((resolve) => setImmediate(resolve))
  await assert.rejects(() => f.store.withTaskLease(TASK, async () => 'second'), /正在核验/)
  release(); await first
  assert.equal(await f.store.withTaskLease(TASK, async () => 'later'), 'later')
})

test('storage write fault latches unavailable and blocks retry', async () => {
  const f = fixture(); f.hooks.set = () => { throw new Error('quota') }
  await assert.rejects(() => f.store.withTaskLease(TASK, async (lease) => lease.persist(marker)), /写后核验失败/)
  assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
  await assert.rejects(() => f.store.withTaskLease(TASK, async () => 'must not run'), /持久恢复或页面协调不可用|同一盘点过账正在核验/)
})

test('cross-task marker cannot be persisted under another task lease', async () => {
  const f = fixture()
  await assert.rejects(() => f.store.withTaskLease(TASK, async (lease) => lease.persist({ ...marker, task_id: OTHER })), /当前任务不一致/)
})
