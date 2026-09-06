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
  return { values, hooks, storage, coordinator, store: recovery.createFormalStocktakePostRecoveryStore({ storage, coordinator }) }
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

test('pending lookup filters by canonical task and ignores unrelated storage', async () => {
  const f = fixture()
  const alphaTask = 'a0000000-0000-4000-8000-000000000001'
  for (const task_id of [TASK, alphaTask]) {
    await f.store.withTaskLease(task_id, async (lease) => lease.persist({ ...marker, task_id }))
  }
  f.values.set('login-settings', 'not a recovery marker')
  assert.deepEqual(f.store.readPending().values.map((value) => value.task_id).sort(), [TASK, alphaTask].sort())
  assert.deepEqual(f.store.readPending(alphaTask.toUpperCase()).values.map((value) => value.task_id), [alphaTask])
  assert.deepEqual(f.store.readPending(OTHER), { kind: 'missing' })
  assert.deepEqual(f.store.readPending(null), { kind: 'corrupt' })
})

test('coordinator excludes the same task across stores but allows another task', async () => {
  const f = fixture()
  const second = recovery.createFormalStocktakePostRecoveryStore({ storage: f.storage, coordinator: f.coordinator })
  let release
  const hold = new Promise((resolve) => { release = resolve })
  const first = f.store.withTaskLease(TASK, async () => hold)
  let secondRan = false
  await assert.rejects(() => second.withTaskLease(TASK, async () => { secondRan = true }), /正在核验/)
  assert.equal(secondRan, false)
  assert.equal(await second.withTaskLease(OTHER, async () => 'independent'), 'independent')
  release(); await first
  assert.equal(await second.withTaskLease(TASK, async () => 'released'), 'released')
})

test('completed and rejected leases cannot read, persist or clear through a later lease', async () => {
  for (const reject of [false, true]) {
    const f = fixture()
    let expired
    const operation = f.store.withTaskLease(TASK, async (lease) => {
      expired = lease
      lease.persist(marker)
      if (reject) throw new Error('page unloaded')
    })
    if (reject) await assert.rejects(operation, /page unloaded/)
    else await operation
    await f.store.withTaskLease(TASK, async (lease) => {
      assert.throws(() => expired.read(), /协调已结束/)
      assert.throws(() => expired.persist(marker), /协调已结束/)
      assert.throws(() => expired.clearExact(marker), /协调已结束/)
      assert.equal(lease.read().kind, 'valid')
    })
    assert.equal(f.values.get(recovery.STORAGE_PREFIX + TASK), JSON.stringify(marker))
  }
})

test('directory failure latches globally across stores even after storage recovers', async () => {
  const f = fixture()
  const second = recovery.createFormalStocktakePostRecoveryStore({ storage: f.storage, coordinator: f.coordinator })
  f.hooks.info = () => { throw new Error('directory unavailable') }
  assert.deepEqual(f.store.readPending(TASK), { kind: 'unavailable' })
  delete f.hooks.info
  assert.deepEqual(second.read(OTHER), { kind: 'unavailable' })
  assert.deepEqual(second.readPending(), { kind: 'unavailable' })
  let ran = false
  await assert.rejects(() => second.withTaskLease(OTHER, async () => { ran = true }), /持久恢复或页面协调不可用/)
  assert.equal(ran, false)
  assert.equal(f.values.size, 0)
})

test('a directory failure discovered by a direct read blocks all task leases', async () => {
  const f = fixture()
  f.hooks.info = () => ({ keys: ['duplicate', 'duplicate'] })
  assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
  delete f.hooks.info
  await assert.rejects(() => f.store.withTaskLease(OTHER, async () => assert.fail('fault must be sticky')), /持久恢复或页面协调不可用/)
})

test('global directory fault invalidates an already active lease before it persists', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async (lease) => {
    f.hooks.info = () => { throw new Error('directory unavailable') }
    assert.equal(f.store.readPending().kind, 'unavailable')
    delete f.hooks.info
    assert.throws(() => lease.persist(marker), /协调已结束或存储异常/)
  })
  assert.equal(f.values.size, 0)
})

test('pending scans reject an added marker even when filtering another task', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async (lease) => lease.persist(marker))
  f.hooks.get = (key) => {
    f.values.set(recovery.STORAGE_PREFIX + OTHER, JSON.stringify({ ...marker, task_id: OTHER }))
    return f.values.get(key) || ''
  }
  assert.deepEqual(f.store.readPending(TASK), { kind: 'unavailable' })
  delete f.hooks.get
  await assert.rejects(() => f.store.withTaskLease(OTHER, async () => assert.fail('unstable scan must block')), /持久恢复或页面协调不可用/)
})

test('pending scans do not mistake a disappeared marker for no pending command', async () => {
  const f = fixture()
  const key = recovery.STORAGE_PREFIX + TASK
  f.values.set(key, JSON.stringify(marker))
  let calls = 0
  f.hooks.info = () => {
    calls += 1
    if (calls === 2) f.values.delete(key)
    return { keys: [...f.values.keys()] }
  }
  assert.deepEqual(f.store.readPending(), { kind: 'unavailable' })
  delete f.hooks.info
  assert.deepEqual(f.store.readPending(), { kind: 'unavailable' })
})

test('malformed recovery directory keys latch a global fault without exposing payloads', async () => {
  for (const suffix of ['invalid-id', '00000000-0000-0000-0000-000000000000', 'A0000000-0000-4000-8000-000000000001']) {
    const f = fixture()
    f.values.set(recovery.STORAGE_PREFIX + suffix, JSON.stringify(marker))
    assert.deepEqual(f.store.readPending(OTHER), { kind: 'corrupt' })
    f.values.clear()
    assert.deepEqual(f.store.read(TASK), { kind: 'unavailable' })
    await assert.rejects(() => f.store.withTaskLease(OTHER, async () => assert.fail('malformed directory must block')), /持久恢复或页面协调不可用/)
  }
})

test('marker corruption is sticky for its task and cannot disappear into a fresh submission', async () => {
  for (const raw of [null, '', '{invalid', JSON.stringify({ ...marker, task_id: OTHER })]) {
    const f = fixture()
    f.values.set(recovery.STORAGE_PREFIX + TASK, raw)
    assert.deepEqual(f.store.read(TASK), { kind: 'corrupt' })
    f.values.clear()
    assert.deepEqual(f.store.readPending(TASK), { kind: 'unavailable' })
    assert.deepEqual(f.store.readPending(), { kind: 'unavailable' })
    await assert.rejects(() => f.store.withTaskLease(TASK, async () => assert.fail('task fault must block')), /持久恢复或页面协调不可用/)
    assert.equal(await f.store.withTaskLease(OTHER, async () => 'unrelated task'), 'unrelated task')
  }
})

test('write success without an exact persisted readback latches a shared task fault', async () => {
  for (const replace of [false, true]) {
    const f = fixture()
    const second = recovery.createFormalStocktakePostRecoveryStore({ storage: f.storage, coordinator: f.coordinator })
    f.hooks.set = (key) => {
      if (replace) f.values.set(key, JSON.stringify({ ...marker, trace_request_id: 'other-command-0001' }))
    }
    await assert.rejects(() => f.store.withTaskLease(TASK, async (lease) => lease.persist(marker)), /写后核验失败/)
    delete f.hooks.set
    assert.deepEqual(second.read(TASK), { kind: 'unavailable' })
    await assert.rejects(() => second.withTaskLease(TASK, async () => assert.fail('write mismatch must block')), /持久恢复或页面协调不可用/)
  }
})

test('clear removes only an exact marker and verifies absence', async () => {
  const f = fixture()
  await f.store.withTaskLease(TASK, async (lease) => lease.persist(marker))
  await f.store.withTaskLease(TASK, async (lease) => {
    assert.throws(() => lease.clearExact({ ...marker, actor_authorization_version: 10 }), /坐标不匹配/)
    assert.equal(lease.read().kind, 'valid')
    lease.clearExact(marker)
    assert.equal(lease.read().kind, 'missing')
  })
  assert.equal(f.values.size, 0)
  assert.deepEqual(f.store.readPending(), { kind: 'missing' })
})

test('failed or inconsistent clears keep the task blocked even if the key disappears', async () => {
  for (const mode of ['throw-before', 'throw-after', 'no-op', 'replace']) {
    const f = fixture()
    const second = recovery.createFormalStocktakePostRecoveryStore({ storage: f.storage, coordinator: f.coordinator })
    await f.store.withTaskLease(TASK, async (lease) => lease.persist(marker))
    f.hooks.remove = (key) => {
      if (mode === 'throw-before') throw new Error('remove failed')
      if (mode === 'no-op') return
      f.values.delete(key)
      if (mode === 'throw-after') throw new Error('remove result unknown')
      f.values.set(key, JSON.stringify({ ...marker, trace_request_id: 'other-command-0001' }))
    }
    await assert.rejects(() => f.store.withTaskLease(TASK, async (lease) => lease.clearExact(marker)), /清理未确认/)
    delete f.hooks.remove
    assert.deepEqual(second.read(TASK), { kind: 'unavailable' })
    await assert.rejects(() => second.withTaskLease(TASK, async () => assert.fail('unconfirmed clear must block')), /持久恢复或页面协调不可用/)
  }
})
