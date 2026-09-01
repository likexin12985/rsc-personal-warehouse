const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const UNSIGNED_DECIMAL = /^(?:0|[1-9][0-9]*)\.[0-9]{3}$/
const SIGNED_DECIMAL = /^-?(?:0|[1-9][0-9]*)\.[0-9]{3}$/
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

const TASK_STATUSES = [
  'draft',
  'issued',
  'frozen',
  'counting',
  'submitted',
  'region_review',
  'hq_review',
  'approved',
  'recount_required',
  'posted',
  'closed',
  'cancelled'
]
const ROUND_STATUSES = ['counting', 'submitted', 'superseded']
const ROUND_TYPES = ['initial', 'recount']
const EVIDENCE_STATUSES = ['not_started', 'counting_hidden', 'sealed']
const ACTIONS = [
  'count',
  'review_region',
  'review_headquarters',
  'open_recount',
  'post',
  'close'
]
const DIFFERENCE_TYPES = [
  'missing',
  'excess',
  'wrong_location',
  'wrong_condition',
  'wrong_lot',
  'wrong_serial',
  'control_unassigned'
]
const REVIEW_STAGES = ['region', 'headquarters']
const REVIEW_DECISIONS = ['approve', 'recount', 'reject']
const COUNT_RESULT_TASK_STATUSES = [
  'counting',
  'submitted',
  'region_review',
  'hq_review',
  'approved',
  'recount_required',
  'posted',
  'closed'
]

const STATUS_LABELS = {
  draft: '草稿',
  issued: '已下发',
  frozen: '已冻结',
  counting: '盘点中',
  submitted: '已提交',
  region_review: '待区域复核',
  hq_review: '待总部复核',
  approved: '已批准待过账',
  recount_required: '需要复盘',
  posted: '已过账待关闭',
  closed: '已关闭',
  cancelled: '已取消'
}

const ACTION_LABELS = {
  count: '提交实盘',
  review_region: '区域复核',
  review_headquarters: '总部复核',
  open_recount: '打开复盘',
  post: '期初过账',
  close: '关闭任务'
}

const DIFFERENCE_LABELS = {
  missing: '缺失',
  excess: '多余',
  wrong_location: '错位置',
  wrong_condition: '错成色',
  wrong_lot: '错批次',
  wrong_serial: '错 SN',
  control_unassigned: 'OAM 控制数待核实'
}

function contractError(code, message) {
  const error = new Error(message)
  error.code = code
  error.status = 409
  return error
}

function fail(code, message) {
  throw contractError(code, message)
}

function objectValue(value, field) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail('stocktake_contract_object_invalid', `${field} 不是有效对象`)
  }
  return value
}

function own(object, field) {
  if (!Object.prototype.hasOwnProperty.call(object, field)) {
    fail('stocktake_contract_field_missing', `正式盘点响应缺少字段 ${field}`)
  }
  return object[field]
}

function enumValue(value, allowed, field) {
  if (typeof value !== 'string' || !allowed.includes(value)) {
    fail('stocktake_contract_enum_unknown', `正式盘点响应包含未知 ${field}`)
  }
  return value
}

function textValue(value, field) {
  if (typeof value !== 'string' || !value.trim()) {
    fail('stocktake_contract_text_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value
}

function uuidValue(value, field) {
  if (typeof value !== 'string' || !UUID.test(value)) {
    fail('stocktake_contract_uuid_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value.toLowerCase()
}

function integerValue(value, field, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) {
    fail('stocktake_contract_integer_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value
}

function booleanValue(value, field) {
  if (typeof value !== 'boolean') {
    fail('stocktake_contract_boolean_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value
}

function timestampValue(value, field, nullable = false) {
  if (value === null && nullable) return null
  if (
    typeof value !== 'string' ||
    !ISO_TIMESTAMP.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) {
    fail('stocktake_contract_timestamp_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value
}

function decimalValue(value, field, signed = false) {
  const pattern = signed ? SIGNED_DECIMAL : UNSIGNED_DECIMAL
  if (typeof value !== 'string' || !pattern.test(value)) {
    fail('stocktake_contract_decimal_invalid', `正式盘点响应中的 ${field} 无效`)
  }
  return value
}

function actionValues(value) {
  if (!Array.isArray(value)) {
    fail('stocktake_contract_actions_invalid', '正式盘点响应中的 allowed_actions 无效')
  }
  const actions = value.map((action) => enumValue(action, ACTIONS, 'allowed_action'))
  if (new Set(actions).size !== actions.length) {
    fail('stocktake_contract_actions_duplicate', '正式盘点响应包含重复可用操作')
  }
  return actions
}

function validateActionState(status, roundStatus, actions) {
  const requiredState = {
    count: status === 'counting' && roundStatus === 'counting',
    review_region: ['submitted', 'region_review'].includes(status) && roundStatus === 'submitted',
    review_headquarters: status === 'hq_review' && roundStatus === 'submitted',
    open_recount: status === 'recount_required' && roundStatus === 'submitted',
    post: status === 'approved' && roundStatus === 'submitted',
    close: status === 'posted'
  }
  for (const action of actions) {
    if (Object.prototype.hasOwnProperty.call(requiredState, action) && !requiredState[action]) {
      fail('stocktake_contract_action_state_mismatch', `操作 ${action} 与盘点状态不一致`)
    }
  }
}

function validateSummary(value) {
  const row = objectValue(value, 'task summary')
  uuidValue(own(row, 'task_id'), 'task_id')
  textValue(own(row, 'task_no'), 'task_no')
  uuidValue(own(row, 'region_org_id'), 'region_org_id')
  const status = enumValue(own(row, 'status'), TASK_STATUSES, 'task status')
  const blind = booleanValue(own(row, 'blind_count'), 'blind_count')
  integerValue(own(row, 'current_round_no'), 'current_round_no')
  const roundStatus = own(row, 'current_round_status')
  if (roundStatus !== null) enumValue(roundStatus, ROUND_STATUSES, 'round status')
  const visible = integerValue(own(row, 'visible_scope_count'), 'visible_scope_count')
  const completed = integerValue(own(row, 'completed_scope_count'), 'completed_scope_count')
  if (completed > visible) {
    fail('stocktake_contract_scope_progress_invalid', '已完成盘点范围超过可见范围')
  }
  const evidence = enumValue(
    own(row, 'evidence_status'),
    EVIDENCE_STATUSES,
    'evidence_status'
  )
  const differenceCount = own(row, 'difference_count')
  if (differenceCount !== null) integerValue(differenceCount, 'difference_count')
  integerValue(own(row, 'task_version'), 'task_version')
  timestampValue(own(row, 'deadline'), 'deadline', true)
  const actions = actionValues(own(row, 'allowed_actions'))
  validateActionState(status, roundStatus, actions)
  if (blind && status === 'counting') {
    if (evidence !== 'counting_hidden' || differenceCount !== null) {
      fail('stocktake_contract_blind_summary_leak', '盲盘进行中不得返回差异数量')
    }
  }
  return row
}

function validateScope(value, hidden) {
  const row = objectValue(value, 'scope')
  uuidValue(own(row, 'scope_id'), 'scope_id')
  integerValue(own(row, 'scope_no'), 'scope_no', 1)
  uuidValue(own(row, 'location_id'), 'location_id')
  uuidValue(own(row, 'owner_org_id'), 'owner_org_id')
  booleanValue(own(row, 'assigned_to_me'), 'assigned_to_me')
  enumValue(own(row, 'completion_status'), ['pending', 'completed'], 'completion_status')
  const nullableFacts = [
    'zero_confirmed',
    'count_line_count',
    'observation_line_count',
    'serial_count',
    'total_counted_qty'
  ]
  if (hidden && nullableFacts.some((field) => own(row, field) !== null)) {
    fail('stocktake_contract_blind_scope_leak', '盲盘进行中不得返回实盘数量证据')
  }
  if (!hidden) {
    const zeroConfirmed = own(row, 'zero_confirmed')
    if (zeroConfirmed !== null) booleanValue(zeroConfirmed, 'zero_confirmed')
    for (const field of ['count_line_count', 'observation_line_count', 'serial_count']) {
      const candidate = own(row, field)
      if (candidate !== null) integerValue(candidate, field)
    }
    const total = own(row, 'total_counted_qty')
    if (total !== null) decimalValue(total, 'total_counted_qty')
  }
  timestampValue(own(row, 'completed_at'), 'completed_at', true)
  if (row.completion_status === 'pending' && row.completed_at !== null) {
    fail('stocktake_contract_scope_completion_invalid', '未完成范围不能包含完成时间')
  }
  return row
}

function validateDifference(value, scopeIds) {
  const row = objectValue(value, 'difference')
  uuidValue(own(row, 'difference_id'), 'difference_id')
  integerValue(own(row, 'difference_no'), 'difference_no', 1)
  const scopeId = own(row, 'scope_id')
  if (scopeId !== null && !scopeIds.has(uuidValue(scopeId, 'scope_id'))) {
    fail('stocktake_contract_difference_scope_leak', '差异引用了不可见盘点范围')
  }
  enumValue(own(row, 'difference_type'), DIFFERENCE_TYPES, 'difference_type')
  const materialId = own(row, 'material_id')
  if (materialId !== null) uuidValue(materialId, 'material_id')
  decimalValue(own(row, 'book_qty'), 'book_qty')
  decimalValue(own(row, 'counted_qty'), 'counted_qty')
  decimalValue(own(row, 'difference_qty'), 'difference_qty', true)
  decimalValue(own(row, 'affected_qty'), 'affected_qty')
  const reasonCode = own(row, 'reason_code')
  if (reasonCode !== null) textValue(reasonCode, 'reason_code')
  booleanValue(own(row, 'evidence_required'), 'evidence_required')
  return row
}

function validateReview(value) {
  const row = objectValue(value, 'review')
  enumValue(own(row, 'stage'), REVIEW_STAGES, 'review stage')
  enumValue(own(row, 'decision'), REVIEW_DECISIONS, 'review decision')
  timestampValue(own(row, 'reviewed_at'), 'reviewed_at')
  return row
}

function validateOpeningStocktakePage(payload) {
  const page = objectValue(payload, 'response')
  if (own(page, 'schema_version') !== '1.0') {
    fail('stocktake_contract_version_unknown', '正式盘点响应版本不受支持')
  }
  const items = own(page, 'items')
  if (!Array.isArray(items)) fail('stocktake_contract_items_invalid', '正式盘点任务列表无效')
  const seen = new Set()
  items.forEach((item) => {
    const row = validateSummary(item)
    const id = row.task_id.toLowerCase()
    if (seen.has(id)) fail('stocktake_contract_task_duplicate', '正式盘点任务列表存在重复')
    seen.add(id)
  })
  const next = own(page, 'next_after_id')
  if (next !== null) uuidValue(next, 'next_after_id')
  return page
}

function validateOpeningStocktakeDetail(payload, expectedTaskId = '') {
  const detail = objectValue(payload, 'response')
  if (own(detail, 'schema_version') !== '1.0') {
    fail('stocktake_contract_version_unknown', '正式盘点响应版本不受支持')
  }
  const taskId = uuidValue(own(detail, 'task_id'), 'task_id')
  if (expectedTaskId && taskId !== uuidValue(expectedTaskId, 'expected_task_id')) {
    fail('stocktake_contract_task_mismatch', '正式盘点详情与目标任务不一致')
  }
  textValue(own(detail, 'task_no'), 'task_no')
  uuidValue(own(detail, 'region_org_id'), 'region_org_id')
  const status = enumValue(own(detail, 'status'), TASK_STATUSES, 'task status')
  const blind = booleanValue(own(detail, 'blind_count'), 'blind_count')
  integerValue(own(detail, 'task_version'), 'task_version')
  timestampValue(own(detail, 'deadline'), 'deadline', true)
  timestampValue(own(detail, 'cutoff_at'), 'cutoff_at', true)
  const currentRound = own(detail, 'current_round')
  let roundStatus = null
  if (currentRound !== null) {
    const round = objectValue(currentRound, 'current_round')
    uuidValue(own(round, 'round_id'), 'round_id')
    integerValue(own(round, 'round_no'), 'round_no', 1)
    enumValue(own(round, 'round_type'), ROUND_TYPES, 'round_type')
    roundStatus = enumValue(own(round, 'status'), ROUND_STATUSES, 'round status')
    timestampValue(own(round, 'started_at'), 'started_at')
    const submittedAt = timestampValue(own(round, 'submitted_at'), 'submitted_at', true)
    if ((roundStatus === 'submitted') !== (submittedAt !== null)) {
      fail('stocktake_contract_round_submission_invalid', '盘点轮次状态与提交时间不一致')
    }
  }
  const evidence = enumValue(
    own(detail, 'evidence_status'),
    EVIDENCE_STATUSES,
    'evidence_status'
  )
  const hidden = blind && status === 'counting'
  if (hidden && evidence !== 'counting_hidden') {
    fail('stocktake_contract_blind_state_invalid', '盲盘进行中必须保持隐藏证据状态')
  }
  const scopes = own(detail, 'scopes')
  if (!Array.isArray(scopes)) fail('stocktake_contract_scopes_invalid', '正式盘点范围列表无效')
  const scopeIds = new Set()
  scopes.forEach((scope) => {
    const row = validateScope(scope, hidden)
    const id = row.scope_id.toLowerCase()
    if (scopeIds.has(id)) fail('stocktake_contract_scope_duplicate', '正式盘点范围存在重复')
    scopeIds.add(id)
  })
  const differences = own(detail, 'differences')
  if (!Array.isArray(differences)) fail('stocktake_contract_differences_invalid', '正式盘点差异列表无效')
  if (hidden && differences.length) {
    fail('stocktake_contract_blind_difference_leak', '盲盘进行中不得返回差异明细')
  }
  const differenceIds = new Set()
  differences.forEach((difference) => {
    const row = validateDifference(difference, scopeIds)
    const id = row.difference_id.toLowerCase()
    if (differenceIds.has(id)) fail('stocktake_contract_difference_duplicate', '正式盘点差异存在重复')
    differenceIds.add(id)
  })
  const reviews = own(detail, 'reviews')
  if (!Array.isArray(reviews)) fail('stocktake_contract_reviews_invalid', '正式盘点复核列表无效')
  const reviewStages = new Set()
  reviews.forEach((review) => {
    const row = validateReview(review)
    if (reviewStages.has(row.stage)) fail('stocktake_contract_review_duplicate', '正式盘点复核阶段重复')
    reviewStages.add(row.stage)
  })
  const actions = actionValues(own(detail, 'allowed_actions'))
  validateActionState(status, roundStatus, actions)
  return detail
}

function requireExpectedUuid(actual, expected, field) {
  const checkedActual = uuidValue(actual, field)
  if (checkedActual !== uuidValue(expected, `expected_${field}`)) {
    fail(
      'stocktake_write_result_anchor_mismatch',
      `正式盘点写响应中的 ${field} 与原请求不一致`
    )
  }
  return checkedActual
}

function validateOpeningStocktakeCountWriteResult(
  payload,
  expectedTaskId,
  expectedRoundId,
  expectedScopeId
) {
  const result = objectValue(payload, 'count write result')
  if (own(result, 'schema_version') !== '1.0') {
    fail('stocktake_write_result_version_unknown', '正式盘点实盘写响应版本不受支持')
  }
  requireExpectedUuid(own(result, 'task_id'), expectedTaskId, 'task_id')
  requireExpectedUuid(own(result, 'round_id'), expectedRoundId, 'round_id')
  requireExpectedUuid(own(result, 'scope_id'), expectedScopeId, 'scope_id')
  enumValue(own(result, 'task_status'), COUNT_RESULT_TASK_STATUSES, 'task_status')
  enumValue(own(result, 'round_status'), ROUND_STATUSES, 'round_status')
  if (!booleanValue(own(result, 'scope_completed'), 'scope_completed')) {
    fail(
      'stocktake_write_result_not_completed',
      '正式盘点实盘写响应未确认目标范围完成'
    )
  }
  booleanValue(own(result, 'round_sealed'), 'round_sealed')
  booleanValue(
    own(result, 'has_pending_verification'),
    'has_pending_verification'
  )
  booleanValue(own(result, 'replayed'), 'replayed')
  return result
}

function validateOpeningStocktakeTerminalWriteResult(
  payload,
  action,
  expectedTaskId,
  expectedRoundId,
  expectedVersion
) {
  const result = objectValue(payload, 'terminal write result')
  if (!['post', 'close'].includes(action)) {
    fail('stocktake_write_result_action_invalid', '正式盘点终态写操作无效')
  }
  if (own(result, 'schema_version') !== '1.0') {
    fail('stocktake_write_result_version_unknown', '正式盘点终态写响应版本不受支持')
  }
  requireExpectedUuid(own(result, 'task_id'), expectedTaskId, 'task_id')
  const resultingStatus = enumValue(
    own(result, 'resulting_task_status'),
    [action === 'post' ? 'posted' : 'closed'],
    'resulting_task_status'
  )
  const taskVersion = integerValue(own(result, 'task_version'), 'task_version')
  const checkedExpectedVersion = integerValue(expectedVersion, 'expected_task_version')
  if (taskVersion !== checkedExpectedVersion + 1) {
    fail(
      'stocktake_write_result_version_mismatch',
      '正式盘点终态写响应版本与原请求不一致'
    )
  }
  uuidValue(own(result, 'posting_id'), 'posting_id')
  const inventoryTransactionId = own(result, 'inventory_transaction_id')
  if (inventoryTransactionId !== null) {
    uuidValue(inventoryTransactionId, 'inventory_transaction_id')
  }
  booleanValue(own(result, 'replayed'), 'replayed')

  if (resultingStatus === 'posted') {
    requireExpectedUuid(own(result, 'round_id'), expectedRoundId, 'round_id')
    decimalValue(own(result, 'total_quantity'), 'total_quantity')
    integerValue(own(result, 'established_scope_count'), 'established_scope_count', 1)
    integerValue(
      own(result, 'pending_control_difference_count'),
      'pending_control_difference_count'
    )
    integerValue(own(result, 'ledger_cursor'), 'ledger_cursor')
  } else {
    timestampValue(own(result, 'closed_at'), 'closed_at')
  }
  return result
}

function stocktakeStatusLabel(value) {
  return STATUS_LABELS[value] || '未知状态'
}

function stocktakeActionLabel(value) {
  return ACTION_LABELS[value] || '未知操作'
}

function stocktakeDifferenceLabel(value) {
  return DIFFERENCE_LABELS[value] || '未知差异'
}

module.exports = {
  validateOpeningStocktakePage,
  validateOpeningStocktakeDetail,
  validateOpeningStocktakeCountWriteResult,
  validateOpeningStocktakeTerminalWriteResult,
  stocktakeStatusLabel,
  stocktakeActionLabel,
  stocktakeDifferenceLabel
}
