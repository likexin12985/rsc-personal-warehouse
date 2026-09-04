const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/stocktake-contract')

const TASK_ID = '10000000-0000-4000-8000-000000000001'
const ROUND_ID = '20000000-0000-4000-8000-000000000001'
const SCOPE_ID = '30000000-0000-4000-8000-000000000001'
const LOCATION_ID = '40000000-0000-4000-8000-000000000001'
const ORG_ID = '50000000-0000-4000-8000-000000000001'
const DIFFERENCE_ID = '60000000-0000-4000-8000-000000000001'
const MATERIAL_ID = '70000000-0000-4000-8000-000000000001'
const POSTING_ID = '80000000-0000-4000-8000-000000000001'
const TRANSACTION_ID = '90000000-0000-4000-8000-000000000001'

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function hiddenSummary() {
  return {
    task_id: TASK_ID,
    task_no: 'OPEN-JS-20260831-001',
    region_org_id: ORG_ID,
    status: 'counting',
    blind_count: true,
    current_round_no: 1,
    current_round_status: 'counting',
    visible_scope_count: 1,
    completed_scope_count: 0,
    evidence_status: 'counting_hidden',
    difference_count: null,
    task_version: 1,
    deadline: null,
    allowed_actions: ['count']
  }
}

function hiddenDetail() {
  return {
    schema_version: '1.0',
    task_id: TASK_ID,
    task_no: 'OPEN-JS-20260831-001',
    region_org_id: ORG_ID,
    status: 'counting',
    blind_count: true,
    task_version: 1,
    deadline: null,
    cutoff_at: '2026-08-31T01:00:00Z',
    current_round: {
      round_id: ROUND_ID,
      round_no: 1,
      round_type: 'initial',
      status: 'counting',
      started_at: '2026-08-31T01:00:00Z',
      submitted_at: null
    },
    evidence_status: 'counting_hidden',
    scopes: [{
      scope_id: SCOPE_ID,
      scope_no: 1,
      location_id: LOCATION_ID,
      owner_org_id: ORG_ID,
      assigned_to_me: true,
      completion_status: 'pending',
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
      completed_at: null
    }],
    differences: [],
    reviews: [],
    allowed_actions: ['count']
  }
}

function sealedDetail() {
  const detail = hiddenDetail()
  detail.status = 'submitted'
  detail.task_version = 2
  detail.current_round.status = 'submitted'
  detail.current_round.submitted_at = '2026-08-31T02:00:00Z'
  detail.evidence_status = 'sealed'
  detail.scopes[0].completion_status = 'completed'
  detail.scopes[0].zero_confirmed = false
  detail.scopes[0].count_line_count = 1
  detail.scopes[0].observation_line_count = 0
  detail.scopes[0].serial_count = 0
  detail.scopes[0].total_counted_qty = '2.000'
  detail.scopes[0].completed_at = '2026-08-31T01:50:00Z'
  detail.differences = [{
    difference_id: DIFFERENCE_ID,
    difference_no: 1,
    scope_id: SCOPE_ID,
    difference_type: 'excess',
    material_id: MATERIAL_ID,
    book_qty: '0.000',
    counted_qty: '2.000',
    difference_qty: '2.000',
    affected_qty: '2.000',
    reason_code: 'opening_physical_excess',
    evidence_required: true
  }]
  detail.allowed_actions = ['review_region']
  return detail
}

test('blind counting list and detail expose progress but no count or difference quantity', () => {
  const page = {
    schema_version: '1.0',
    items: [hiddenSummary()],
    next_after_id: null
  }
  assert.equal(contract.validateOpeningStocktakePage(page), page)
  const detail = hiddenDetail()
  assert.equal(contract.validateOpeningStocktakeDetail(detail, TASK_ID), detail)
  assert.equal(detail.scopes[0].total_counted_qty, null)
  assert.deepEqual(detail.differences, [])
})

test('blind counting rejects scope facts, difference counts and difference rows', () => {
  const leakedSummary = hiddenSummary()
  leakedSummary.difference_count = 0
  assert.throws(
    () => contract.validateOpeningStocktakePage({
      schema_version: '1.0',
      items: [leakedSummary],
      next_after_id: null
    }),
    (error) => error.code === 'stocktake_contract_blind_summary_leak'
  )

  for (const mutate of [
    (detail) => { detail.scopes[0].total_counted_qty = '0.000' },
    (detail) => { detail.scopes[0].zero_confirmed = true },
    (detail) => { detail.differences = sealedDetail().differences }
  ]) {
    const leaked = hiddenDetail()
    mutate(leaked)
    assert.throws(
      () => contract.validateOpeningStocktakeDetail(leaked, TASK_ID),
      (error) => [
        'stocktake_contract_blind_scope_leak',
        'stocktake_contract_blind_difference_leak'
      ].includes(error.code)
    )
  }
})

test('sealed submission accepts exact decimal difference facts', () => {
  const detail = sealedDetail()
  const validated = contract.validateOpeningStocktakeDetail(detail, TASK_ID)
  assert.equal(validated.scopes[0].total_counted_qty, '2.000')
  assert.equal(validated.differences[0].difference_qty, '2.000')
})

test('detail rejects cross-scope leakage, duplicate reviews and target mismatch', () => {
  const leakedScope = sealedDetail()
  leakedScope.differences[0].scope_id = '30000000-0000-4000-8000-000000000099'
  assert.throws(
    () => contract.validateOpeningStocktakeDetail(leakedScope, TASK_ID),
    (error) => error.code === 'stocktake_contract_difference_scope_leak'
  )

  const duplicateReview = sealedDetail()
  duplicateReview.reviews = [
    { stage: 'region', decision: 'approve', reviewed_at: '2026-08-31T03:00:00Z' },
    { stage: 'region', decision: 'approve', reviewed_at: '2026-08-31T03:01:00Z' }
  ]
  assert.throws(
    () => contract.validateOpeningStocktakeDetail(duplicateReview, TASK_ID),
    (error) => error.code === 'stocktake_contract_review_duplicate'
  )

  assert.throws(
    () => contract.validateOpeningStocktakeDetail(
      sealedDetail(),
      '10000000-0000-4000-8000-000000000099'
    ),
    (error) => error.code === 'stocktake_contract_task_mismatch'
  )
})

test('actions cannot collapse count, review, post and close into the wrong state', () => {
  const invalid = clone(sealedDetail())
  invalid.allowed_actions = ['post', 'close']
  assert.throws(
    () => contract.validateOpeningStocktakeDetail(invalid, TASK_ID),
    (error) => error.code === 'stocktake_contract_action_state_mismatch'
  )
  assert.equal(contract.stocktakeStatusLabel('posted'), '已过账待关闭')
  assert.equal(contract.stocktakeActionLabel('post'), '期初过账')
  assert.equal(contract.stocktakeActionLabel('close'), '关闭任务')
  assert.equal(contract.stocktakeDifferenceLabel('control_unassigned'), 'OAM 控制数待核实')
})

test('start is rejected because it is not an existing-task allowed action', () => {
  const invalid = hiddenDetail()
  invalid.allowed_actions = ['start']
  assert.throws(
    () => contract.validateOpeningStocktakeDetail(invalid, TASK_ID),
    (error) => error.code === 'stocktake_contract_enum_unknown'
  )
  assert.equal(contract.stocktakeActionLabel('start'), '未知操作')
})

test('regional review remains legal in submitted and region_review task states', () => {
  for (const status of ['submitted', 'region_review']) {
    const detail = sealedDetail()
    detail.status = status
    detail.allowed_actions = ['review_region']
    assert.equal(contract.validateOpeningStocktakeDetail(detail, TASK_ID), detail)
  }
})

test('count write confirmation requires the exact task, round and scope anchors', () => {
  const result = {
    schema_version: '1.0',
    task_id: TASK_ID,
    round_id: ROUND_ID,
    scope_id: SCOPE_ID,
    task_status: 'submitted',
    round_status: 'submitted',
    scope_completed: true,
    round_sealed: true,
    has_pending_verification: false,
    replayed: true
  }
  assert.equal(
    contract.validateOpeningStocktakeCountWriteResult(
      result,
      TASK_ID,
      ROUND_ID,
      SCOPE_ID
    ),
    result
  )

  const wrongScope = clone(result)
  wrongScope.scope_id = '30000000-0000-4000-8000-000000000099'
  assert.throws(
    () => contract.validateOpeningStocktakeCountWriteResult(
      wrongScope,
      TASK_ID,
      ROUND_ID,
      SCOPE_ID
    ),
    (error) => error.code === 'stocktake_write_result_anchor_mismatch'
  )

  const incomplete = clone(result)
  incomplete.scope_completed = false
  assert.throws(
    () => contract.validateOpeningStocktakeCountWriteResult(
      incomplete,
      TASK_ID,
      ROUND_ID,
      SCOPE_ID
    ),
    (error) => error.code === 'stocktake_write_result_not_completed'
  )

  const unsealed = clone(result)
  unsealed.task_status = 'counting'
  unsealed.round_status = 'counting'
  unsealed.round_sealed = false
  assert.equal(
    contract.validateOpeningStocktakeCountWriteResult(
      unsealed,
      TASK_ID,
      ROUND_ID,
      SCOPE_ID
    ),
    unsealed
  )

  for (const mismatch of [
    Object.assign(clone(result), { round_status: 'counting' }),
    Object.assign(clone(result), { task_status: 'counting' }),
    Object.assign(clone(result), { round_sealed: false })
  ]) {
    assert.throws(
      () => contract.validateOpeningStocktakeCountWriteResult(
        mismatch,
        TASK_ID,
        ROUND_ID,
        SCOPE_ID
      ),
      (error) => error.code === 'stocktake_write_result_round_seal_mismatch'
    )
  }
})

test('terminal write confirmation requires exact action anchors and next version', () => {
  const post = {
    schema_version: '1.0',
    task_id: TASK_ID,
    round_id: ROUND_ID,
    posting_id: POSTING_ID,
    inventory_transaction_id: TRANSACTION_ID,
    resulting_task_status: 'posted',
    task_version: 8,
    total_quantity: '2.000',
    established_scope_count: 1,
    pending_control_difference_count: 0,
    ledger_cursor: 10,
    replayed: false
  }
  assert.equal(
    contract.validateOpeningStocktakeTerminalWriteResult(
      post,
      'post',
      TASK_ID,
      ROUND_ID,
      7
    ),
    post
  )

  const close = {
    schema_version: '1.0',
    task_id: TASK_ID,
    posting_id: POSTING_ID,
    inventory_transaction_id: TRANSACTION_ID,
    resulting_task_status: 'closed',
    task_version: 9,
    closed_at: '2026-08-31T04:00:00Z',
    replayed: true
  }
  assert.equal(
    contract.validateOpeningStocktakeTerminalWriteResult(
      close,
      'close',
      TASK_ID,
      null,
      8
    ),
    close
  )

  const staleVersion = clone(post)
  staleVersion.task_version = 9
  assert.throws(
    () => contract.validateOpeningStocktakeTerminalWriteResult(
      staleVersion,
      'post',
      TASK_ID,
      ROUND_ID,
      7
    ),
    (error) => error.code === 'stocktake_write_result_version_mismatch'
  )
})
