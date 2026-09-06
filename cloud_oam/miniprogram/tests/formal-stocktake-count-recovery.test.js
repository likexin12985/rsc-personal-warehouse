const assert = require('node:assert/strict')
const test = require('node:test')
const {
  createFormalStocktakeCountRecoveryAdapter,
  validateFormalStocktakeCountCommandStatus
} = require('../utils/formal-stocktake-count-recovery')
const { validateFormalStocktakeCountSentinel } = require('../utils/formal-stocktake-count-recovery-store')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const PERSON = '40000000-0000-4000-8000-000000000001'
const sentinel = validateFormalStocktakeCountSentinel({
  v: 1, kind: 'formal_scope_count', task_id: TASK, round_id: ROUND, round_no: 2,
  scope_id: SCOPE, operation: 'recount_count', expected_task_version: 7,
  actor_person_id: PERSON, actor_authorization_version: 9,
  trace_request_id: 'wx-count-recovery-0001'
})

function status(overrides = {}) {
  return Object.assign({
    schema_version: '1.0', task_id: TASK, round_id: ROUND, scope_id: SCOPE,
    actor_person_id: PERSON, actor_authorization_version: 9,
    trace_request_id: sentinel.trace_request_id, operation: 'recount_count',
    lookup_status: 'not_observed', command: null
  }, overrides)
}

test('count command status preserves not_observed as a blocking result', () => {
  assert.equal(validateFormalStocktakeCountCommandStatus(status(), sentinel).lookup_status, 'not_observed')
  assert.throws(() => validateFormalStocktakeCountCommandStatus(status({ lookup_status: 'not_observed', command: {} }), sentinel))
})

test('count command status validates exact operation and completed fact', () => {
  const parsed = validateFormalStocktakeCountCommandStatus(status({
    lookup_status: 'confirmed',
    command: {
      completion_id: '50000000-0000-4000-8000-000000000001', round_no: 2,
      completed_at: '2026-09-06T08:00:00+08:00', scope_completed: true,
      caused_round_submission: false
    }
  }), sentinel)
  assert.equal(parsed.command.round_no, 2)
  assert.equal(parsed.command.scope_completed, true)
  assert.throws(() => validateFormalStocktakeCountCommandStatus(status({ operation: 'initial_count' }), sentinel))
  assert.throws(() => validateFormalStocktakeCountCommandStatus(status({ lookup_status: 'confirmed', command: { completion_id: '50000000-0000-4000-8000-000000000001', round_no: 2, completed_at: '2026-09-06T08:00:00+08:00', scope_completed: 1, caused_round_submission: false } }), sentinel))
})

test('count recovery adapter uses strict GET-only non-opening command-status coordinates', async () => {
  const calls = []
  const transport = { request: async (path, options) => { calls.push([path, options]); return status() } }
  const adapter = createFormalStocktakeCountRecoveryAdapter({ person_id: PERSON, authorization_version: 9 }, transport)
  const value = await adapter.commandStatus(sentinel)
  assert.equal(value.lookup_status, 'not_observed')
  assert.match(calls[0][0], new RegExp(`/v1/stocktakes/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/count-command-status\\?`))
  assert.match(calls[0][0], /operation=recount_count/)
  assert.match(calls[0][0], /actor_authorization_version=9/)
  assert.equal(calls[0][1].method, 'GET')
  assert.equal(calls[0][1].noRefresh, true)
})
