const assert = require('node:assert/strict')
const test = require('node:test')

const recovery = require('../utils/material-request-lifecycle-recovery')

const REQUEST_ID = `wxreq-${'a'.repeat(36)}`
const OTHER_REQUEST_ID = `wxreq-${'b'.repeat(36)}`

function storageFixture(initialValue = '') {
  const values = new Map()
  if (initialValue !== '') values.set(recovery.STORAGE_KEY, initialValue)
  const calls = []
  return {
    values,
    calls,
    storage: {
      getStorageSync(key) {
        calls.push(['get', key])
        return values.has(key) ? values.get(key) : ''
      },
      setStorageSync(key, value) {
        calls.push(['set', key, value])
        values.set(key, value)
      },
      removeStorageSync(key) {
        calls.push(['remove', key])
        values.delete(key)
      }
    }
  }
}

test('lifecycle sentinel persists and rereads only the approved minimal fields', () => {
  const fixture = storageFixture()
  const sentinel = recovery.persistLifecycleSentinel(REQUEST_ID, {
    storage: fixture.storage,
    now: () => new Date('2026-09-01T01:02:03.000Z')
  })

  assert.deepEqual(sentinel, {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: REQUEST_ID,
    created_at: '2026-09-01T01:02:03.000Z'
  })
  assert.deepEqual(Object.keys(sentinel).sort(), [
    'created_at', 'kind', 'v', 'x_request_id'
  ])
  assert.equal(JSON.stringify(sentinel).includes('Idempotency-Key'), false)
  assert.equal(JSON.stringify(sentinel).includes('reason'), false)
  assert.deepEqual(recovery.readLifecycleSentinel({ storage: fixture.storage }), sentinel)

  recovery.clearLifecycleSentinel(REQUEST_ID, { storage: fixture.storage })
  assert.equal(recovery.readLifecycleSentinel({ storage: fixture.storage }), null)
})

test('an existing sentinel is reused only for the same request id and is never overwritten', () => {
  const original = {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: REQUEST_ID,
    created_at: '2026-09-01T01:02:03.000Z'
  }
  const fixture = storageFixture(original)
  assert.deepEqual(
    recovery.persistLifecycleSentinel(REQUEST_ID, { storage: fixture.storage }),
    original
  )
  assert.throws(
    () => recovery.persistLifecycleSentinel(OTHER_REQUEST_ID, { storage: fixture.storage }),
    (error) => error.code === 'material_request_lifecycle_recovery_conflict'
  )
  assert.deepEqual(fixture.values.get(recovery.STORAGE_KEY), original)
  assert.equal(fixture.calls.some(([operation]) => operation === 'set'), false)
})

test('malformed or mismatched sentinels are retained and cannot be cleared', () => {
  const malformed = {
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: REQUEST_ID,
    created_at: '2026-09-01T01:02:03.000Z',
    reason: 'must never be persisted'
  }
  const fixture = storageFixture(malformed)
  assert.throws(
    () => recovery.readLifecycleSentinel({ storage: fixture.storage }),
    (error) => error.code === 'material_request_lifecycle_recovery_sentinel_invalid'
  )
  assert.throws(
    () => recovery.clearLifecycleSentinel(REQUEST_ID, { storage: fixture.storage }),
    (error) => error.code === 'material_request_lifecycle_recovery_sentinel_invalid'
  )
  assert.deepEqual(fixture.values.get(recovery.STORAGE_KEY), malformed)

  const validFixture = storageFixture({
    v: 1,
    kind: 'material_request_lifecycle',
    x_request_id: REQUEST_ID,
    created_at: '2026-09-01T01:02:03.000Z'
  })
  assert.throws(
    () => recovery.clearLifecycleSentinel(OTHER_REQUEST_ID, {
      storage: validFixture.storage
    }),
    (error) => error.code === 'material_request_lifecycle_recovery_conflict'
  )
  assert.equal(validFixture.values.has(recovery.STORAGE_KEY), true)
})
