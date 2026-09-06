const assert = require('node:assert/strict')
const test = require('node:test')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'
const EXPECTED = { person_id: PERSON, authorization_version: 9 }

function intent(action = 'submit_recount_count') {
  return {
    method: 'POST', action,
    path: `/v1/stocktakes/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/${action === 'submit_recount_count' ? 'recount-count' : 'initial-count'}`,
    taskId: TASK, roundId: ROUND, scopeId: SCOPE, expectedTaskVersion: 7,
    body: { count_mode: 'blind', account_counts: [], physical_observations: [], evidence_file_ids: [], zero_confirmed: true },
    headers: { 'X-Request-ID': 'wx-count-recovery-0002', 'Idempotency-Key': `wxidem-${'b'.repeat(36)}` }
  }
}

function storeFixture() {
  const { createFormalStocktakeCountCoordinator, createFormalStocktakeCountRecoveryStore } = require('../utils/formal-stocktake-count-recovery-store')
  const values = new Map()
  const storage = {
    getStorageInfoSync: () => ({ keys: [...values.keys()] }),
    getStorageSync: (key) => values.get(key) || '',
    setStorageSync: (key, value) => values.set(key, value),
    removeStorageSync: (key) => values.delete(key)
  }
  return createFormalStocktakeCountRecoveryStore({ storage, coordinator: createFormalStocktakeCountCoordinator() })
}

function adapterFixture(error) {
  let posts = 0
  return {
    get posts() { return posts },
    async loadIdentityNoReplay() { return { person_id: PERSON, name: '工程师', employee_no: 'E1', organization_code: 'ORG', organization_name: '区域', account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9, role_codes: ['technician'] } },
    async loadAccessNoReplay() { return { schema_version: '1.0', person_id: PERSON, authorization_version: 9, can_read: true, can_count: true } },
    async detailNoReplay() {},
    async countCommandStatus() {
      return {
        schema_version: '1.0', task_id: TASK, round_id: ROUND, scope_id: SCOPE,
        actor_person_id: PERSON, actor_authorization_version: 9,
        trace_request_id: 'wx-count-recovery-0002', operation: 'recount_count',
        lookup_status: 'not_observed', command: null
      }
    },
    async execute(value, options) {
      assert.equal(value.path, intent().path)
      await options.beforeWrite()
      posts += 1
      throw error
    }
  }
}

test('count intent requires an explicit action-compatible positive round number', () => {
  const { sentinelFromIntent } = require('../utils/formal-stocktake-count-submission')
  assert.throws(() => sentinelFromIntent(intent(), EXPECTED), /意图无效/)
  assert.throws(() => sentinelFromIntent(intent(), EXPECTED, 1), /轮次与动作不一致/)
  assert.throws(() => sentinelFromIntent(intent('submit_initial_count'), EXPECTED, 2), /轮次与动作不一致/)
  assert.equal(sentinelFromIntent(intent(), EXPECTED, 2).operation, 'recount_count')
})

test('no-replay execution fails before persistence when single-send transport is absent', async () => {
  const { createFormalStocktakeAdapter } = require('../utils/formal-stocktake-adapter')
  let reads = 0
  let posts = 0
  let persisted = 0
  const adapter = createFormalStocktakeAdapter({
    expectedIdentityProvider: () => EXPECTED,
    transport: {
      async get() { reads += 1; return {} },
      async post() { posts += 1; return {} }
    }
  })
  await assert.rejects(
    () => adapter.execute(intent(), { noReplay: true, beforeWrite: () => { persisted += 1 } }),
    /单次发送通道不可用/
  )
  assert.equal(reads, 0)
  assert.equal(posts, 0)
  assert.equal(persisted, 0)
})

test('adapter-bound recovery reduces a complete private identity response to the exact public pair', async () => {
  const { createFormalStocktakeCountRecoveryAdapterFromFormalAdapter } = require('../utils/formal-stocktake-count-recovery')
  const recovery = createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(EXPECTED, {
    async loadIdentityNoReplay() {
      return { person_id: PERSON, name: '工程师', employee_no: 'E1', organization_code: 'ORG', organization_name: '区域', account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9, role_codes: ['technician'] }
    },
    async loadAccessNoReplay() { return { schema_version: '1.0', person_id: PERSON, authorization_version: 9, can_read: true, can_count: true } },
    async detailNoReplay() {},
    async countCommandStatus() {}
  })
  assert.deepEqual(await recovery.loadIdentity(), EXPECTED)
})

test('definitive response-backed validation rejection clears only its durable marker', async () => {
  const { submitDurableFormalStocktakeCount } = require('../utils/formal-stocktake-count-submission')
  const store = storeFixture()
  const adapter = adapterFixture(Object.assign(new Error('invalid command'), {
    status: 422, responseReceived: true, category: 'invalid_request', code: 'stocktake_count_command_invalid'
  }))
  await assert.rejects(
    () => submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: EXPECTED, roundNo: 2, adapter, store }),
    (error) => error.status === 422 && error.responseReceived === true
  )
  assert.equal(adapter.posts, 1)
  assert.equal(store.readPending(TASK).kind, 'missing')
})

test('state/precondition rejection remains durable until historical GET proof', async () => {
  const { submitDurableFormalStocktakeCount, FormalStocktakeCountSubmissionPendingError } = require('../utils/formal-stocktake-count-submission')
  const store = storeFixture()
  const adapter = adapterFixture(Object.assign(new Error('state changed'), {
    status: 412, responseReceived: true, category: 'precondition_failed', code: 'stocktake_count_state_invalid'
  }))
  await assert.rejects(
    () => submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: EXPECTED, roundNo: 2, adapter, store }),
    (error) => error instanceof FormalStocktakeCountSubmissionPendingError
  )
  assert.equal(adapter.posts, 1)
  assert.equal(store.readPending(TASK).kind, 'valid')
})
