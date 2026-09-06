const assert = require('node:assert/strict')
const test = require('node:test')
const {
  validateFormalStocktakePostSentinel,
  validateFormalStocktakePostCommandStatus,
} = require('../utils/formal-stocktake-post-recovery')

const TASK = '10000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'
const ROUND = '50000000-0000-4000-8000-000000000001'
const COMPLETION = '60000000-0000-4000-8000-000000000001'
const TRACE = 'wx-post-recovery-0001'

function sentinel(overrides = {}) {
  return validateFormalStocktakePostSentinel(Object.assign({
    v: 1, kind: 'formal_stocktake_post', task_id: TASK, expected_task_version: 7,
    actor_person_id: PERSON, actor_authorization_version: 9, trace_request_id: TRACE,
  }, overrides))
}
function status(overrides = {}) {
  return Object.assign({
    schema_version: '1.0', task_id: TASK, actor_person_id: PERSON,
    actor_authorization_version: 9, trace_request_id: TRACE,
    operation: 'post_differences', lookup_status: 'not_observed', command: null,
  }, overrides)
}
function command(overrides = {}) {
  return Object.assign({
    completion_id: COMPLETION, task_id: TASK, terminal_round_id: ROUND,
    resulting_task_status: 'posted', task_version: 8, scope_count: 1,
    difference_count: 2, accepted_difference_count: 1, no_adjustment_count: 1,
    transaction_count: 1, movement_count: 1, total_quantity: '2.000',
    first_ledger_cursor: 100, last_ledger_cursor: 100,
    posted_at: '2026-09-06T08:00:00+08:00',
  }, overrides)
}

function sealedCommand(overrides = {}) {
  return Object.assign({
    seal_id: '70000000-0000-4000-8000-000000000001', task_id: TASK,
    expected_task_version: 7, actor_person_id: PERSON,
    actor_authorization_version: 9, trace_request_id: TRACE,
    sealed_at: '2026-09-06T08:01:00+08:00',
  }, overrides)
}

test('post recovery keeps an unobserved command sticky', () => {
  const parsed = validateFormalStocktakePostCommandStatus(status(), sentinel())
  assert.equal(parsed.lookup_status, 'not_observed')
  assert.equal(parsed.command, null)
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ command: {} }), sentinel()))
})

test('sealed_not_executed status exposes only the minimal seal proof', () => {
  const parsed = validateFormalStocktakePostCommandStatus(status({ lookup_status: 'sealed_not_executed', command: sealedCommand() }), sentinel())
  assert.equal(parsed.lookup_status, 'sealed_not_executed')
  assert.deepEqual(Object.keys(parsed.command).sort(), ['actor_authorization_version', 'actor_person_id', 'expected_task_version', 'seal_id', 'sealed_at', 'task_id', 'trace_request_id'])
  for (const tamper of [
    { task_id: '10000000-0000-4000-8000-000000000002' },
    { expected_task_version: 8 },
    { actor_person_id: '40000000-0000-4000-8000-000000000002' },
    { actor_authorization_version: 10 },
    { trace_request_id: 'wx-other-trace-0001' },
    { request_sha256: 'a'.repeat(64) },
    { role_assignment_id: '80000000-0000-4000-8000-000000000001' },
  ]) {
    assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'sealed_not_executed', command: sealedCommand(tamper) }), sentinel()))
  }
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'sealed_not_executed', command: null }), sentinel()))
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: sealedCommand() }), sentinel()))
})

test('confirmed post status enforces immutable coordinates and arithmetic', () => {
  const parsed = validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command() }), sentinel())
  assert.equal(parsed.command.task_id, TASK)
  assert.equal(parsed.command.task_version, 8)
  assert.equal(parsed.command.total_quantity, '2.000')
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({ task_version: 9 }) }), sentinel()), /版本/)
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({ accepted_difference_count: 2 }) }), sentinel()))
})

test('zero transaction proof must carry zero movement and quantity', () => {
  const parsed = validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({
    difference_count: 1, accepted_difference_count: 0, no_adjustment_count: 1,
    transaction_count: 0, movement_count: 0, total_quantity: '0.000',
    first_ledger_cursor: null, last_ledger_cursor: null,
  }) }), sentinel())
  assert.equal(parsed.command.transaction_count, 0)
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({
    transaction_count: 0, movement_count: 0, total_quantity: '1.000', first_ledger_cursor: null, last_ledger_cursor: null,
  }) }), sentinel()))
})

test('post status rejects extra write-side fields and cursor gaps', () => {
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({ replayed: false }) }), sentinel()))
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ lookup_status: 'confirmed', command: command({ last_ledger_cursor: 102 }) }), sentinel()))
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ task_id: '10000000-0000-4000-8000-000000000002', lookup_status: 'confirmed', command: command() }), sentinel()))
})

test('durable post persists before one no-replay POST and keeps marker on not_observed', async () => {
  const { createFormalStocktakePostRecoveryStore, createFormalStocktakePostCoordinator } = require('../utils/formal-stocktake-post-recovery-store')
  const values = new Map()
  const storage = {
    getStorageInfoSync: () => ({ keys: [...values.keys()] }),
    getStorageSync: (key) => values.get(key) || '',
    setStorageSync: (key, value) => values.set(key, value),
    removeStorageSync: (key) => values.delete(key),
  }
  const store = createFormalStocktakePostRecoveryStore({ storage, coordinator: createFormalStocktakePostCoordinator() })
  let posts = 0
  const identity = { person_id: PERSON, name: '总部', employee_no: 'E1', organization_code: 'HQ', organization_name: '总部', account_status: 'active', employment_status: 'active', access_mode: 'active', authorization_version: 9, role_codes: ['admin'] }
  const adapter = {
    async loadIdentityNoReplay() { return identity },
    async loadAccessNoReplay() { return { schema_version: '1.0', person_id: PERSON, authorization_version: 9, can_read: true, can_post: true } },
    async postingCommandStatus() { return status() },
    async detailNoReplay() { throw new Error('detail must not be queried for not_observed') },
    async execute(intent, options) { posts += 1; await options.beforeWrite(); const error = new Error('network'); error.status = 503; throw error },
  }
  const intent = { method: 'POST', action: 'post', path: `/v1/stocktakes/${TASK}/post-differences`, taskId: TASK, expectedTaskVersion: 7, body: { expected_task_version: 7 }, headers: { 'X-Request-ID': TRACE, 'Idempotency-Key': `wxidem-${'a'.repeat(36)}` } }
  await assert.rejects(() => require('../utils/formal-stocktake-post-recovery').submitDurableFormalStocktakePost({ intent, expectedIdentity: { person_id: PERSON, authorization_version: 9 }, adapter, store }), /仍待只读核验/)
  assert.equal(posts, 1)
  assert.equal(store.read(TASK).kind, 'valid')
})

test('recovered projection accepts a later closed task only after the exact post round proof', () => {
  const fixture = require('./helpers/formal-post-fixtures')
  const sentinel = fixture.marker()
  const parsed = validateFormalStocktakePostCommandStatus(fixture.confirmedStatus(), sentinel)
  const recovery = require('../utils/formal-stocktake-post-recovery')
  const detail = recovery.validateFormalStocktakePostRecoveredProjection(fixture.closedDetail(), sentinel, parsed.command)
  assert.equal(detail.status, 'closed')
  assert.equal(detail.version, 8)
  assert.throws(() => recovery.validateFormalStocktakePostRecoveredProjection(fixture.closedDetail(), sentinel, { ...parsed.command, posted_at: '2026-09-01T10:31:00+08:00' }))
})
