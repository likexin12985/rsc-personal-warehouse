const assert = require('node:assert/strict')
const test = require('node:test')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'

function intent() {
  return {
    method: 'POST', action: 'submit_recount_count', path: `/v1/stocktakes/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/recount-count`,
    taskId: TASK, roundId: ROUND, scopeId: SCOPE, expectedTaskVersion: 7,
    body: { count_mode: 'blind', account_counts: [], physical_observations: [], evidence_file_ids: [], zero_confirmed: true },
    headers: { 'X-Request-ID': 'wx-count-recovery-0001', 'Idempotency-Key': `wxidem-${'a'.repeat(36)}` }
  }
}

test('durable count persists before one POST and leaves marker on not_observed', async () => {
  const apiPath = require.resolve('../utils/api')
  const originalApi = require.cache[apiPath]
  const requests = []
  require.cache[apiPath] = { id: apiPath, filename: apiPath, loaded: true, exports: {
    get: async () => ({}), post: async () => ({}),
    async request(path, options) {
      requests.push([path, options])
      if (path === '/auth/me') return { person_id: PERSON, name: '工程师', employee_no: 'E1', organization_code: 'ORG', organization_name: '区域', account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9, role_codes: ['technician'] }
      if (path === '/access/context') return { person_id: PERSON, authorization_version: 9, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['technician'], assignments: [], permissions: [{ resource: 'stocktake', action: 'read', field_code: '' }, { resource: 'stocktake', action: 'count', field_code: '' }] }
      if (path.includes('count-command-status')) return { schema_version: '1.0', task_id: TASK, round_id: ROUND, scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 9, trace_request_id: 'wx-count-recovery-0001', operation: 'recount_count', lookup_status: 'not_observed', command: null }
      throw new Error(`unexpected ${path}`)
    }
  } }
  delete require.cache[require.resolve('../utils/formal-stocktake-count-recovery')]
  delete require.cache[require.resolve('../utils/formal-stocktake-count-submission')]
  const { submitDurableFormalStocktakeCount, FormalStocktakeCountSubmissionPendingError } = require('../utils/formal-stocktake-count-submission')
  const { createFormalStocktakeCountCoordinator, createFormalStocktakeCountRecoveryStore } = require('../utils/formal-stocktake-count-recovery-store')
  const storage = new Map()
  const fakeStorage = { getStorageInfoSync: () => ({ keys: [...storage.keys()] }), getStorageSync: (key) => storage.get(key) || '', setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: (key) => storage.delete(key) }
  const store = createFormalStocktakeCountRecoveryStore({ storage: fakeStorage, coordinator: createFormalStocktakeCountCoordinator() })
  let posts = 0
  const adapter = {
    async loadIdentityNoReplay() { return { person_id: PERSON, name: '工程师', employee_no: 'E1', organization_code: 'ORG', organization_name: '区域', account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9, role_codes: ['technician'] } },
    async loadAccessNoReplay() { return { schema_version: '1.0', person_id: PERSON, authorization_version: 9, can_read: true, can_count: true } },
    async detailNoReplay() {},
    async countCommandStatus() { return { schema_version: '1.0', task_id: TASK, round_id: ROUND, scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 9, trace_request_id: 'wx-count-recovery-0001', operation: 'recount_count', lookup_status: 'not_observed', command: null } },
    async execute(value, options) { assert.equal(value.path, intent().path); await options.beforeWrite(); posts += 1; throw Object.assign(new Error('network'), { status: 503 }) }
  }
  try {
    await assert.rejects(() => submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: { person_id: PERSON, authorization_version: 9 }, roundNo: 2, adapter, store }), (error) => error instanceof FormalStocktakeCountSubmissionPendingError)
    assert.equal(posts, 1)
    assert.equal(store.readPending(TASK).kind, 'valid')
    assert.equal(requests.length, 0, 'recovery must reuse the passed adapter, not the module-global transport')
  } finally {
    if (originalApi) require.cache[apiPath] = originalApi
    else delete require.cache[apiPath]
    delete require.cache[require.resolve('../utils/formal-stocktake-count-recovery')]
    delete require.cache[require.resolve('../utils/formal-stocktake-count-submission')]
  }
})
