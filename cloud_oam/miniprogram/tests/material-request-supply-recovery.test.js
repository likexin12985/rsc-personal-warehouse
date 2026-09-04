const assert = require('node:assert/strict')
const test = require('node:test')
const recovery = require('../utils/material-request-supply-recovery')

function sentinel() {
  return { v: 1, kind: 'material_request_supply', trace_request_id: `wxreq-${'a'.repeat(36)}`,
    person_id: '10000000-0000-4000-8000-000000000001', authorization_version: 7,
    request_id: '20000000-0000-4000-8000-000000000001', request_version: 6,
    revision_id: '30000000-0000-4000-8000-000000000001', revision_no: 1,
    action: 'create_supply_task', supply_task_id: null, task_version: null,
    created_at: '2026-09-05T00:00:00.000Z' }
}

function fixture(initial = '') {
  let value = initial
  return { storage: {
    getStorageSync: () => value,
    setStorageSync: (_key, next) => { value = next },
    removeStorageSync: () => { value = '' }
  } }
}

test('supply recovery stores only exact non-sensitive anchors and rereads changes', () => {
  const store = fixture()
  const value = sentinel()
  assert.deepEqual(recovery.persist(value, store), value)
  assert.deepEqual(recovery.read(store), value)
  assert.deepEqual(recovery.persist(value, store), value)
  assert.throws(() => recovery.persist(Object.assign({}, value, { request_version: 8 }), store))
  for (const secretField of ['body', 'reference_no', 'note', 'token', 'idempotency_key']) {
    assert.throws(() => recovery.validateSentinel(Object.assign({}, value, { [secretField]: 'private' })))
  }
  recovery.clear(value, store)
  assert.equal(recovery.read(store), null)
})

test('corrupt, changed and inaccessible storage fails closed without overwrites', () => {
  const bad = Object.assign({}, sentinel(), { extra: true })
  const store = fixture(bad)
  assert.throws(() => recovery.read(store))
  assert.throws(() => recovery.persist(sentinel(), store))
  assert.throws(() => recovery.clear(sentinel(), store))
  assert.equal(store.storage.getStorageSync(), bad)
  const lostWrite = fixture()
  lostWrite.storage.setStorageSync = () => {}
  assert.throws(() => recovery.persist(sentinel(), lostWrite))
  const failedRead = fixture()
  failedRead.storage.getStorageSync = () => { throw new Error('disk failure') }
  assert.throws(() => recovery.read(failedRead))
})

test('not-observed remains pending and performs no mutation or detail lookup', async () => {
  const value = sentinel()
  const store = fixture(value)
  const identity = { person_id: value.person_id, authorization_version: 7 }
  const calls = []
  const result = await recovery.recover(value, {
    loadIdentity: async () => identity,
    loadAccess: async () => Object.assign({}, identity, { can_read: true, can_manage_supply: true }),
    supplyCommandStatus: async (trace) => {
      calls.push(trace)
      return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
    },
    detail: async () => { throw new Error('must not query detail') },
    mutate: async () => { throw new Error('must never retry a write') }
  }, store)
  assert.equal(result.status, 'pending')
  assert.deepEqual(calls, [value.trace_request_id])
  assert.deepEqual(recovery.read(store), value)
})

test('identity or authorization drift stops before command lookup and retains sentinel', async () => {
  for (const changes of [{ person_id: '40000000-0000-4000-8000-000000000001' },
    { authorization_version: 8 }, { can_manage_supply: false }, { can_read: false }]) {
    const value = sentinel()
    const store = fixture(value)
    const identity = { person_id: value.person_id, authorization_version: 7 }
    await assert.rejects(recovery.recover(value, {
      loadIdentity: async () => identity,
      loadAccess: async () => Object.assign({}, identity, { can_read: true, can_manage_supply: true }, changes),
      supplyCommandStatus: async () => { throw new Error('lookup should not execute') }
    }, store), /身份或权限已变化/)
    assert.deepEqual(recovery.read(store), value)
  }
})
