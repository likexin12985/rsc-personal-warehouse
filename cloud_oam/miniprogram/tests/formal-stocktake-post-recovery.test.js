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

test('post recovery keeps an unobserved command sticky', () => {
  const parsed = validateFormalStocktakePostCommandStatus(status(), sentinel())
  assert.equal(parsed.lookup_status, 'not_observed')
  assert.equal(parsed.command, null)
  assert.throws(() => validateFormalStocktakePostCommandStatus(status({ command: {} }), sentinel()))
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
