const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/formal-stocktake-contract')

const TASK = '10000000-0000-4000-8000-000000000001'
const REGION = '20000000-0000-4000-8000-000000000001'
const SCOPE = '30000000-0000-4000-8000-000000000001'
const LOCATION = '40000000-0000-4000-8000-000000000001'
const ROUND = '50000000-0000-4000-8000-000000000001'
const POSTING_COMPLETION = '60000000-0000-4000-8000-000000000001'
const RECONCILIATION_COMPLETION = '70000000-0000-4000-8000-000000000001'
const CLOSE_COMPLETION = '80000000-0000-4000-8000-000000000001'
const OTHER_COMPLETION = '80000000-0000-4000-8000-000000000002'
const RECONCILED_AT = '2026-09-01T09:10:00+08:00'
const CLOSED_AT = '2026-09-01T09:15:00+08:00'

function axes(overrides = {}) {
  return Object.assign({ count_status: 'not_started', difference_status: 'not_ready', region_review_status: 'not_ready', headquarters_review_status: 'not_ready', recount_status: 'not_required', posting_status: 'not_posted', reconciliation_status: 'not_reconciled', closure_status: 'open' }, overrides)
}

function scope() {
  return { scope_id: SCOPE, scope_no: 1, scope_mode: 'location_all', owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: null, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: 'not_started', snapshot_accounts: [], allowed_actions: [] }
}

function detail(overrides = {}) {
  return Object.assign({ schema_version: '1.0', task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', region_org_id: REGION, status: 'draft', version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: '', state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [scope()], rounds: [], allowed_actions: ['start'] }, overrides)
}

function page() {
  return { schema_version: '1.0', items: [{ task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', region_org_id: REGION, status: 'draft', version: 0, blind_count: true, current_round_no: 0, current_round_status: null, cutoff_ledger_cursor: null, cutoff_at: null, visible_scope_count: 1, current_round_visible_completed_scope_count: 0, freeze_status: 'not_started', state_axes: axes(), deadline: null, allowed_actions: ['start'] }], next_after_id: null }
}

function coordinates() {
  return { 'Idempotency-Key': `wxidem-${'a'.repeat(36)}`, 'X-Request-ID': `wxreq-${'b'.repeat(36)}` }
}

test('formal non-opening page and detail are strict and preserve posted/closed separation', () => {
  assert.equal(contract.validateFormalStocktakePage(page()).items[0].version, 0)
  const posted = detail({ status: 'posted', version: 8, posted_at: '2026-09-01T09:00:00+08:00', state_axes: axes({ posting_status: 'recorded' }), allowed_actions: [] })
  const checked = contract.validateFormalStocktakeDetail(posted)
  assert.equal(checked.status, 'posted')
  assert.equal(checked.closed_at, null)
  const malformed = page()
  malformed.items[0].legacy_status = 'closed'
  assert.throws(() => contract.validateFormalStocktakePage(malformed), /精确包含正式字段/)
})

test('list summaries reject terminal-axis and action drift', () => {
  const base = page()
  const row = base.items[0]
  assert.throws(() => contract.validateFormalStocktakePage(Object.assign({}, base, { items: [Object.assign({}, row, { status: 'closed', state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'stale', closure_status: 'closed' }), allowed_actions: [] })] })), /完整终态/)
  assert.throws(() => contract.validateFormalStocktakePage(Object.assign({}, base, { items: [Object.assign({}, row, { allowed_actions: ['close'] })] })), /非终态盘点摘要/)
  assert.throws(() => contract.validateFormalStocktakePage(Object.assign({}, base, { items: [Object.assign({}, row, { status: 'posted', state_axes: axes({ posting_status: 'recorded' }), allowed_actions: ['close'] })] })), /当前有效内部对账/)
  assert.throws(() => contract.validateFormalStocktakePage(Object.assign({}, base, { items: [Object.assign({}, row, { status: 'posted', state_axes: axes({ posting_status: 'recorded' }), allowed_actions: ['start'] })] })), /只能开放对账或关闭/)
})

test('quantity input becomes fixed three-decimal text and rejects exponent or negative values', () => {
  assert.equal(contract.fixedQuantityText('12.3'), '12.300')
  assert.equal(contract.fixedQuantityText('0'), '0.000')
  assert.throws(() => contract.fixedQuantityText('1e3'), /非指数/)
  assert.throws(() => contract.fixedQuantityText('0', true), /大于零/)
  assert.throws(() => contract.fixedQuantityText(1), /文本/)
})

test('one unresolved intent reuses path body and coordinates and blocks a different action', () => {
  let calls = 0
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory() { calls += 1; return coordinates() } })
  const input = { action: 'create_personal', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' } }
  const first = registry.begin(input)
  const retry = registry.begin(input)
  assert.equal(first, retry)
  assert.equal(calls, 1)
  assert.equal(first.path, '/v1/stocktakes/personal')
  assert.equal(contract.stocktakeIntentRetryState(first, null), 'retryable')
  assert.equal(Object.isFrozen(first.body), true)
  assert.throws(() => registry.begin({ action: 'create_personal', body: { blind_count: false, freeze_mode: 'hard', note: '' } }), /禁止生成新写坐标/)
  registry.complete(first)
  assert.equal(registry.current(), null)
})

test('intent coordinates match the API exact idempotency and request header limits', () => {
  const input = { action: 'create_personal', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' } }
  const begin = (keyLength, requestLength) => contract.createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ 'Idempotency-Key': 'a'.repeat(keyLength), 'X-Request-ID': 'b'.repeat(requestLength) }) }).begin(input)
  assert.throws(() => begin(15, 8), /坐标/)
  assert.throws(() => begin(129, 8), /坐标/)
  assert.throws(() => begin(16, 7), /坐标/)
  assert.throws(() => begin(16, 161), /坐标/)
  assert.deepEqual(begin(16, 8).headers, { 'Idempotency-Key': 'a'.repeat(16), 'X-Request-ID': 'b'.repeat(8) })
})

test('count intent keeps canonical formal path and fixed decimal body without legacy routes', () => {
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const round = '50000000-0000-4000-8000-000000000001'
  const intent = registry.begin({ action: 'submit_initial_count', taskId: TASK, roundId: round, scopeId: SCOPE, expectedTaskVersion: 1, body: { count_mode: 'blind', account_counts: [], physical_observations: [{ material_id: null, material_identifier_raw: 'SKU-001', material_identifier_type: 'sku_code', condition_code: 'new', availability_bucket: 'available', counted_qty: '2.5', lot_id: null, lot_no_raw: null, serial_id: null, serial_no_raw: null, serial_identifier_type: null, count_method: 'manual', reason_code: null, remark: '' }], evidence_file_ids: [], zero_confirmed: false } })
  assert.equal(intent.path, `/v1/stocktakes/${TASK}/rounds/${round}/scopes/${SCOPE}/initial-count`)
  assert.equal(intent.body.physical_observations[0].counted_qty, '2.500')
  assert.equal(intent.path.includes('/stocktakes/opening'), false)
  assert.equal(intent.path.startsWith('/stocktakes'), false)
})

test('write response and exact create reread are independently validated', () => {
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const intent = registry.begin({ action: 'create_personal', body: { blind_count: true, freeze_mode: 'cutoff_replay', note: '' } })
  const result = contract.validateFormalStocktakeWriteResult(intent, { schema_version: '1.0', task_id: TASK, task_no: 'ST-SELF-001', task_type: 'personal', status: 'draft', task_version: 0, scope_count: 1, idempotency_replayed: false })
  assert.doesNotThrow(() => contract.confirmFormalStocktakeWrite(intent, result, contract.validateFormalStocktakeDetail(detail())))
  assert.throws(() => contract.confirmFormalStocktakeWrite(intent, result, contract.validateFormalStocktakeDetail(detail({ task_no: 'ST-OTHER' }))), /精确回读/)
})

test('open recount preserves the controlled opaque user id and never substitutes person_id', () => {
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const intent = registry.begin({ action: 'open_recount', taskId: TASK, roundId: ROUND, expectedTaskVersion: 5, body: { expected_task_version: 5, assignments: [{ scope_id: SCOPE, assignee_user_id: 'engineer-001' }], reason: '区域复核要求复盘' } })
  assert.equal(intent.path, `/v1/stocktakes/${TASK}/rounds/${ROUND}/recount`)
  assert.deepEqual(intent.body.assignments, [{ scope_id: SCOPE, assignee_user_id: 'engineer-001' }])
  assert.equal(JSON.stringify(intent.body).includes('assignee_person_id'), false)
})

test('reconcile keeps an independent frozen intent and confirms the exact completion projection', () => {
  const registry = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates })
  const input = { action: 'reconcile', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } }
  const intent = registry.begin(input)
  assert.equal(intent.path, `/v1/stocktakes/${TASK}/reconcile`)
  assert.deepEqual(intent.body, { expected_task_version: 8 })
  assert.equal(registry.begin(input), intent)
  const before = contract.validateFormalStocktakeDetail(detail({ status: 'posted', version: 8, posted_at: '2026-09-01T09:00:00+08:00', state_axes: axes({ posting_status: 'recorded' }), allowed_actions: ['reconcile'] }))
  assert.equal(contract.stocktakeIntentRetryState(intent, before), 'retryable')
  assert.throws(() => registry.begin({ action: 'close', taskId: TASK, expectedTaskVersion: 8, body: { expected_task_version: 8 } }), /禁止生成新写坐标/)

  const result = contract.validateFormalStocktakeWriteResult(intent, {
    schema_version: '1.0', completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION,
    reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'posted', task_version: 9,
    scope_count: 1, account_count: 2, scoped_account_count: 1, serial_count: 0, transaction_count: 1,
    movement_count: 1, book_total_qty: '3.000', physical_total_qty: '3.000', reconciled_at: RECONCILED_AT, replayed: false
  })
  const confirmed = contract.validateFormalStocktakeDetail(detail({
    status: 'posted', version: 9, posted_at: '2026-09-01T09:00:00+08:00',
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded' }),
    close_control: { latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }, close_completion: null },
    allowed_actions: ['reconcile', 'close']
  }))
  assert.doesNotThrow(() => contract.confirmFormalStocktakeWrite(intent, result, confirmed))
  assert.equal(contract.stocktakeIntentRetryState(intent, confirmed), 'retryable')
  const wrongCoordinate = contract.validateFormalStocktakeDetail(detail({
    status: 'posted', version: 9, posted_at: '2026-09-01T09:00:00+08:00',
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded' }),
    close_control: { latest_reconciliation: { completion_id: OTHER_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }, close_completion: null },
    allowed_actions: ['reconcile', 'close']
  }))
  assert.throws(() => contract.confirmFormalStocktakeWrite(intent, result, wrongCoordinate), /完成坐标/)
})

test('close is a separate posted-to-closed intent bound to the latest reconciliation completion', () => {
  const intent = contract.createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'close', taskId: TASK, expectedTaskVersion: 9, body: { expected_task_version: 9 } })
  assert.equal(intent.path, `/v1/stocktakes/${TASK}/close`)
  const result = contract.validateFormalStocktakeWriteResult(intent, {
    schema_version: '1.0', completion_id: CLOSE_COMPLETION, task_id: TASK, reconciliation_completion_id: RECONCILIATION_COMPLETION,
    reconciliation_no: 1, reconciliation_ledger_cursor: 81, resulting_task_status: 'closed', task_version: 10, closed_at: CLOSED_AT, replayed: false
  })
  const confirmed = contract.validateFormalStocktakeDetail(detail({
    status: 'closed', version: 10, posted_at: '2026-09-01T09:00:00+08:00', closed_at: CLOSED_AT,
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded', closure_status: 'closed' }),
    close_control: {
      latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT },
      close_completion: { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 10, closed_at: CLOSED_AT }
    },
    allowed_actions: []
  }))
  assert.doesNotThrow(() => contract.confirmFormalStocktakeWrite(intent, result, confirmed))
  assert.equal(contract.stocktakeIntentRetryState(intent, confirmed), 'retryable')
  const postedOnly = contract.validateFormalStocktakeDetail(detail({ status: 'posted', version: 10, posted_at: '2026-09-01T09:00:00+08:00', state_axes: axes({ posting_status: 'recorded' }), allowed_actions: [] }))
  assert.throws(() => contract.confirmFormalStocktakeWrite(intent, result, postedOnly), /精确回读/)
  assert.equal(contract.stocktakeIntentRetryState(intent, postedOnly), 'handoff_required')
  assert.throws(() => contract.validateFormalStocktakeDetail(detail({
    status: 'closed', version: 10, posted_at: '2026-09-01T09:00:00+08:00', closed_at: CLOSED_AT,
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded', closure_status: 'closed' }),
    close_control: { latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }, close_completion: null },
    allowed_actions: []
  })), /关闭/)
})

test('reconciliation stale stays independent from posting and closure axes', () => {
  const checked = contract.validateFormalStocktakeDetail(detail({
    status: 'posted', version: 10, posted_at: '2026-09-01T09:00:00+08:00',
    state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'stale', closure_status: 'open' }),
    close_control: { latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }, close_completion: null },
    allowed_actions: ['reconcile']
  }))
  assert.equal(checked.state_axes.posting_status, 'recorded')
  assert.equal(checked.state_axes.reconciliation_status, 'stale')
  assert.equal(checked.state_axes.closure_status, 'open')
})

test('pre-post states reject terminal facts and closed tasks reject every residual action', () => {
  const latest = { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 81, reconciled_task_version: 9, reconciled_at: RECONCILED_AT }
  const close = { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 10, closed_at: CLOSED_AT }
  const closed = detail({ status: 'closed', version: 10, posted_at: '2026-09-01T09:00:00+08:00', closed_at: CLOSED_AT, state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded', closure_status: 'closed' }), close_control: { latest_reconciliation: latest, close_completion: close }, allowed_actions: [] })
  assert.throws(() => contract.validateFormalStocktakeDetail(Object.assign({}, closed, { state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'stale', closure_status: 'closed' }) })), /当前对账事实/)
  assert.throws(() => contract.validateFormalStocktakeDetail(detail({ version: 1, state_axes: axes({ reconciliation_status: 'recorded' }), close_control: { latest_reconciliation: Object.assign({}, latest, { reconciled_task_version: 1 }), close_completion: null } })), /非终态盘点/)
  assert.throws(() => contract.validateFormalStocktakeDetail(Object.assign({}, closed, { allowed_actions: ['reconcile'] })), /不得再暴露写动作/)
  assert.throws(() => contract.validateFormalStocktakeDetail(Object.assign({}, closed, { scopes: [Object.assign({}, scope(), { allowed_actions: ['submit_initial_count'] })] })), /不得再暴露写动作/)
  assert.throws(() => contract.validateFormalStocktakeDetail(detail({ status: 'posted', version: 9, posted_at: '2026-09-01T09:00:00+08:00', state_axes: axes({ posting_status: 'recorded', reconciliation_status: 'recorded' }), close_control: { latest_reconciliation: latest, close_completion: null }, allowed_actions: ['start'] })), /只能开放独立对账或关闭/)
  assert.throws(() => contract.validateFormalStocktakeDetail(Object.assign({}, closed, { posted_at: null })), /过账状态、时间/)
  assert.throws(() => contract.validateFormalStocktakeDetail(Object.assign({}, closed, { closed_at: '2026-09-01T09:05:00+08:00', close_control: { latest_reconciliation: latest, close_completion: Object.assign({}, close, { closed_at: '2026-09-01T09:05:00+08:00' }) } })), /晚于内部对账/)
})
