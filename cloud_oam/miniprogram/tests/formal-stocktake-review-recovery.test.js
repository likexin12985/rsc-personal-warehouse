const assert = require('node:assert/strict')
const test = require('node:test')

const {
  createFormalStocktakeReviewRecoveryAdapter,
  validateFormalStocktakeReviewCommandStatus
} = require('../utils/formal-stocktake-review-recovery')
const {
  createFormalStocktakeReviewCoordinator,
  createFormalStocktakeReviewRecoveryStore,
  validateFormalStocktakeReviewSentinel,
  STORAGE_PREFIX
} = require('../utils/formal-stocktake-review-recovery-store')
const {
  submitDurableFormalStocktakeReview,
  FormalStocktakeReviewSubmissionPendingError
} = require('../utils/formal-stocktake-review-submission')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'
const REVIEW = '50000000-0000-4000-8000-000000000001'
const TRACE = 'wx-review-recovery-0001'

function marker(overrides = {}) {
  return validateFormalStocktakeReviewSentinel(Object.assign({
    v: 1, kind: 'formal_stocktake_review', task_id: TASK, round_id: ROUND,
    review_stage: 'region', actor_person_id: PERSON,
    actor_authorization_version: 9, expected_task_version: 7,
    trace_request_id: TRACE
  }, overrides))
}

function status(overrides = {}) {
  return Object.assign({
    schema_version: '1.0', task_id: TASK, round_id: ROUND, review_stage: 'region',
    actor_person_id: PERSON, actor_authorization_version: 9,
    trace_request_id: TRACE, lookup_status: 'not_observed', command: null
  }, overrides)
}

function intent() {
  return {
    method: 'POST', action: 'review_region', path: `/v1/stocktakes/${TASK}/rounds/${ROUND}/reviews/region`,
    taskId: TASK, roundId: ROUND, expectedTaskVersion: 7,
    body: { expected_task_version: 7, decision: 'approve', items: [], comment: '' },
    headers: { 'X-Request-ID': TRACE, 'Idempotency-Key': `wxidem-${'a'.repeat(36)}` }
  }
}

function fakeStorage() {
  const values = new Map()
  return {
    getStorageInfoSync: () => ({ keys: [...values.keys()] }),
    getStorageSync: (key) => values.get(key) || '',
    setStorageSync: (key, value) => values.set(key, value),
    removeStorageSync: (key) => values.delete(key),
    values
  }
}

function identityResponse() {
  return {
    person_id: PERSON, name: '工程师', employee_no: 'E1', organization_code: 'ORG', organization_name: '区域',
    account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9,
    role_codes: ['technician']
  }
}

function accessResponse() {
  return {
    schema_version: '1.0', person_id: PERSON, authorization_version: 9,
    can_read: true, can_count: false, can_manage: false, can_review_region: true,
    can_review_headquarters: false, can_reconcile: false, can_close: false
  }
}

test('review command status preserves not_observed and validates immutable coordinates', () => {
  const parsed = validateFormalStocktakeReviewCommandStatus(status(), marker())
  assert.equal(parsed.lookup_status, 'not_observed')
  assert.throws(() => validateFormalStocktakeReviewCommandStatus(status({ command: {} }), marker()))
  assert.throws(() => validateFormalStocktakeReviewCommandStatus(status({ review_stage: 'headquarters' }), marker()))
})

test('confirmed review status enforces resulting status, versions, and pending count', () => {
  const parsed = validateFormalStocktakeReviewCommandStatus(status({
    lookup_status: 'confirmed',
    command: {
      review_id: REVIEW, task_id: TASK, round_id: ROUND, review_stage: 'region', decision: 'approve',
      resulting_task_status: 'hq_review', expected_task_version: 7, resulting_task_version: 8,
      task_version: 8, item_count: 2, pending_verification_count: 1,
      ready_for_posting: false, reviewed_at: '2026-09-06T08:00:00+08:00'
    }
  }), marker())
  assert.equal(parsed.command.resulting_task_version, 8)
  assert.throws(() => validateFormalStocktakeReviewCommandStatus(status({
    lookup_status: 'confirmed',
    command: {
      review_id: REVIEW, task_id: TASK, round_id: ROUND, review_stage: 'region', decision: 'approve',
      resulting_task_status: 'approved', expected_task_version: 7, resulting_task_version: 8,
      task_version: 8, item_count: 0, pending_verification_count: 1,
      ready_for_posting: false, reviewed_at: '2026-09-06T08:00:00+08:00'
    }
  }), marker()))
})

test('review recovery adapter uses an exact GET-only command-status route', async () => {
  const calls = []
  const transport = {
    async request(path, options) {
      calls.push([path, options])
      if (path === '/auth/me') return identityResponse()
      if (path === '/access/context') return accessResponse()
      return status()
    }
  }
  const adapter = createFormalStocktakeReviewRecoveryAdapter({ person_id: PERSON, authorization_version: 9 }, transport)
  await adapter.loadIdentity()
  await adapter.loadAccess('region')
  const result = await adapter.commandStatus(marker())
  assert.equal(result.lookup_status, 'not_observed')
  const call = calls.at(-1)
  assert.match(call[0], new RegExp(`/v1/stocktakes/${TASK}/rounds/${ROUND}/reviews/region/command-status\\?`))
  assert.match(call[0], /actor_person_id=/)
  assert.match(call[0], /actor_authorization_version=9/)
  assert.match(call[0], /trace_request_id=/)
  assert.equal(call[1].method, 'GET')
  assert.equal(call[1].noRefresh, true)
  assert.equal(Object.prototype.hasOwnProperty.call(call[1], 'body'), false)
})

test('durable review persists before one POST and never replays after not_observed', async () => {
  const storage = fakeStorage()
  const store = createFormalStocktakeReviewRecoveryStore({ storage, coordinator: createFormalStocktakeReviewCoordinator() })
  let posts = 0
  const adapter = {
    async loadIdentityNoReplay() { return identityResponse() },
    async loadAccessNoReplay() { return accessResponse() },
    async detailNoReplay() { throw new Error('detail must not be requested for not_observed') },
    async reviewCommandStatus() { return status() },
    async execute(value, options) {
      assert.equal(value.path, intent().path)
      posts += 1
      await options.beforeWrite()
      throw Object.assign(new Error('network'), { status: 503 })
    }
  }
  await assert.rejects(
    () => submitDurableFormalStocktakeReview({ intent: intent(), expectedIdentity: { person_id: PERSON, authorization_version: 9 }, adapter, store }),
    (error) => error instanceof FormalStocktakeReviewSubmissionPendingError
  )
  assert.equal(posts, 1)
  const pending = store.readPending(TASK)
  assert.equal(pending.kind, 'valid')
  assert.equal(pending.values.length, 1)
  assert.equal(storage.values.get(STORAGE_PREFIX + `${TASK}:${ROUND}:region`).includes('comment'), false)
  await assert.rejects(
    () => submitDurableFormalStocktakeReview({ intent: intent(), expectedIdentity: { person_id: PERSON, authorization_version: 9 }, adapter, store }),
    (error) => error instanceof FormalStocktakeReviewSubmissionPendingError
  )
  assert.equal(posts, 1, 'a durable marker permits only GET recovery, never a second POST')
})

test('review marker cannot be cleared with a different coordinate', async () => {
  const storage = fakeStorage()
  const store = createFormalStocktakeReviewRecoveryStore({ storage, coordinator: createFormalStocktakeReviewCoordinator() })
  const value = marker()
  await store.withTaskLease(value, async (lease) => {
    lease.persist(value)
    assert.throws(() => lease.clearExact(marker({ review_stage: 'headquarters' })))
    assert.equal(lease.read().kind, 'valid')
  })
})
