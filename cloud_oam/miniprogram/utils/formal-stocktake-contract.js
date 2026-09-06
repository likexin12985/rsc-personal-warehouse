const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/
const SIGNED_QUANTITY = /^-?(?:0|[1-9]\d{0,14})\.\d{3}$/
const INPUT_QUANTITY = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/
const SAFE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/

const SCHEMA_VERSION = '1.0'
const TASK_TYPES = ['full', 'sample', 'ad_hoc', 'personal', 'termination']
const TASK_STATUSES = ['draft', 'issued', 'frozen', 'counting', 'submitted', 'region_review', 'hq_review', 'approved', 'recount_required', 'posted', 'closed', 'cancelled']
const ACTIONS = ['start', 'submit_initial_count', 'generate_initial_differences', 'review_region', 'review_headquarters', 'open_recount', 'submit_recount_count', 'generate_recount_differences', 'post', 'reconcile', 'close']
const ROUND_ACTIONS = ['generate_initial_differences', 'review_region', 'review_headquarters', 'open_recount', 'generate_recount_differences']
const CONDITIONS = ['new', 'used', 'damaged', 'scrapped']
const BUCKETS = ['available', 'reserved', 'picking', 'outbound', 'in_transit', 'arrived_pending', 'frozen', 'return_pending', 'scrap_pending']
const ROUND_STATUSES = ['counting', 'submitted', 'superseded']
const REVIEW_DECISIONS = ['approve', 'recount', 'reject']
const REVIEW_ITEM_DECISIONS = ['accept_for_posting', 'pending_verification', 'no_adjustment', 'recount', 'reject']
const DIFFERENCE_TYPES = ['missing', 'excess', 'wrong_location', 'wrong_condition', 'wrong_lot', 'wrong_serial']

function fail(message, code = 'formal_stocktake_contract_invalid') {
  const error = new Error(message)
  error.name = 'FormalStocktakeContractError'
  error.code = code
  error.status = 409
  throw error
}

function objectValue(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${name}不是有效对象`)
  return value
}

function exact(value, keys, name) {
  const object = objectValue(value, name)
  const actual = Object.keys(object).sort()
  const expected = keys.slice().sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(`${name}必须精确包含正式字段`)
  return object
}

function enumValue(value, allowed, name) {
  if (typeof value !== 'string' || !allowed.includes(value)) fail(`${name}包含未知值`)
  return value
}

function uuidValue(value, name) {
  if (typeof value !== 'string' || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) fail(`${name}无效`)
  return value.toLowerCase()
}

function nullableUuid(value, name) { return value === null ? null : uuidValue(value, name) }

function integer(value, name, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) fail(`${name}无效`)
  return value
}

function bool(value, name) {
  if (typeof value !== 'boolean') fail(`${name}无效`)
  return value
}

function text(value, name, allowEmpty = true) {
  if (typeof value !== 'string' || value !== value.trim() || (!allowEmpty && !value)) fail(`${name}无效`)
  return value
}

function nullableText(value, name) { return value === null ? null : text(value, name, false) }

function timestamp(value, name) {
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail(`${name}无效`)
  return value
}

function nullableTimestamp(value, name) { return value === null ? null : timestamp(value, name) }

function quantity(value, name, signed = false) {
  if (typeof value !== 'string' || !(signed ? SIGNED_QUANTITY : QUANTITY).test(value)) fail(`${name}必须是固定三位小数文本`)
  return value
}

function uniqueArray(value, parser, name, key = (item) => String(item)) {
  if (!Array.isArray(value)) fail(`${name}必须是数组`)
  const rows = value.map(parser)
  const keys = rows.map(key)
  if (new Set(keys).size !== keys.length) fail(`${name}包含重复项`)
  return rows
}

function actions(value, allowed = ACTIONS) {
  return uniqueArray(value, (item) => enumValue(item, allowed, 'allowed_action'), 'allowed_actions')
}

function stateAxes(value) {
  const row = exact(value, ['count_status', 'difference_status', 'region_review_status', 'headquarters_review_status', 'recount_status', 'posting_status', 'reconciliation_status', 'closure_status'], '盘点状态轴')
  return Object.freeze({
    count_status: enumValue(row.count_status, ['not_started', 'counting', 'submitted'], 'count_status'),
    difference_status: enumValue(row.difference_status, ['not_ready', 'not_evaluated', 'evaluated', 'hidden_for_blind_counter'], 'difference_status'),
    region_review_status: enumValue(row.region_review_status, ['not_ready', 'pending'].concat(REVIEW_DECISIONS), 'region_review_status'),
    headquarters_review_status: enumValue(row.headquarters_review_status, ['not_ready', 'pending'].concat(REVIEW_DECISIONS), 'headquarters_review_status'),
    recount_status: enumValue(row.recount_status, ['not_required', 'required', 'counting', 'submitted'], 'recount_status'),
    posting_status: enumValue(row.posting_status, ['not_posted', 'recorded'], 'posting_status'),
    reconciliation_status: enumValue(row.reconciliation_status, ['not_reconciled', 'recorded', 'stale'], 'reconciliation_status'),
    closure_status: enumValue(row.closure_status, ['open', 'closed'], 'closure_status')
  })
}

function summary(value) {
  const row = exact(value, ['task_id', 'task_no', 'task_type', 'region_org_id', 'status', 'version', 'blind_count', 'current_round_no', 'current_round_status', 'cutoff_ledger_cursor', 'cutoff_at', 'visible_scope_count', 'current_round_visible_completed_scope_count', 'freeze_status', 'state_axes', 'deadline', 'allowed_actions'], '盘点任务摘要')
  const cursor = row.cutoff_ledger_cursor === null ? null : integer(row.cutoff_ledger_cursor, 'cutoff_ledger_cursor')
  const cutoffAt = nullableTimestamp(row.cutoff_at, 'cutoff_at')
  if ((cursor === null) !== (cutoffAt === null)) fail('盘点截止游标与时间不一致')
  const scopeCount = integer(row.visible_scope_count, 'visible_scope_count', 1)
  const completed = integer(row.current_round_visible_completed_scope_count, 'completed_scope_count')
  if (completed > scopeCount) fail('已完成范围数超过可见范围数')
  const status = enumValue(row.status, TASK_STATUSES, 'status')
  const axes = stateAxes(row.state_axes)
  const allowed = actions(row.allowed_actions)
  if (status === 'closed' && (axes.posting_status !== 'recorded' || axes.reconciliation_status !== 'recorded' || axes.closure_status !== 'closed' || allowed.length)) fail('已关闭盘点摘要必须是无写动作的完整终态')
  if (status === 'posted' && (axes.posting_status !== 'recorded' || axes.closure_status !== 'open')) fail('已过账盘点摘要状态轴不完整')
  if (status === 'posted' && allowed.some((action) => action !== 'reconcile' && action !== 'close')) fail('已过账盘点摘要只能开放对账或关闭动作')
  if (status !== 'posted' && status !== 'closed' && (axes.posting_status !== 'not_posted' || axes.reconciliation_status !== 'not_reconciled' || axes.closure_status !== 'open' || allowed.includes('reconcile') || allowed.includes('close'))) fail('非终态盘点摘要不得暴露终态事实或动作')
  if (allowed.includes('close') && (status !== 'posted' || axes.reconciliation_status !== 'recorded')) fail('关闭动作只能由当前有效内部对账开放')
  if (allowed.includes('reconcile') && status !== 'posted') fail('内部对账动作只能由已过账任务开放')
  return Object.freeze({ task_id: uuidValue(row.task_id, 'task_id'), task_no: text(row.task_no, 'task_no', false), task_type: enumValue(row.task_type, TASK_TYPES, 'task_type'), region_org_id: uuidValue(row.region_org_id, 'region_org_id'), status, version: integer(row.version, 'version'), blind_count: bool(row.blind_count, 'blind_count'), current_round_no: integer(row.current_round_no, 'current_round_no'), current_round_status: row.current_round_status === null ? null : enumValue(row.current_round_status, ROUND_STATUSES, 'current_round_status'), cutoff_ledger_cursor: cursor, cutoff_at: cutoffAt, visible_scope_count: scopeCount, current_round_visible_completed_scope_count: completed, freeze_status: enumValue(row.freeze_status, ['not_started', 'active', 'released', 'cancelled', 'mixed'], 'freeze_status'), state_axes: axes, deadline: nullableTimestamp(row.deadline, 'deadline'), allowed_actions: Object.freeze(allowed) })
}

function validatePage(value) {
  const row = exact(value, ['schema_version', 'items', 'next_after_id'], '正式盘点列表')
  if (row.schema_version !== SCHEMA_VERSION) fail('正式盘点 schema_version 不受支持')
  return Object.freeze({ schema_version: SCHEMA_VERSION, items: Object.freeze(uniqueArray(row.items, summary, 'items', (item) => item.task_id)), next_after_id: nullableUuid(row.next_after_id, 'next_after_id') })
}

function freezeFact(value) {
  if (value === null) return null
  const row = exact(value, ['freeze_id', 'freeze_mode', 'status', 'valid_from', 'valid_to', 'version'], '冻结事实')
  const status = enumValue(row.status, ['active', 'released', 'cancelled'], 'freeze.status')
  const from = timestamp(row.valid_from, 'valid_from')
  const to = nullableTimestamp(row.valid_to, 'valid_to')
  if ((status === 'active') !== (to === null)) fail('冻结状态与有效期不一致')
  return Object.freeze({ freeze_id: uuidValue(row.freeze_id, 'freeze_id'), freeze_mode: enumValue(row.freeze_mode, ['hard', 'cutoff_replay'], 'freeze_mode'), status, valid_from: from, valid_to: to, version: integer(row.version, 'freeze.version') })
}

function snapshotAccount(value) {
  const row = exact(value, ['stock_account_id', 'material_id', 'condition_code', 'availability_bucket', 'lot_id', 'book_qty', 'expected_serial_ids'], '账面账户快照')
  return Object.freeze({ stock_account_id: uuidValue(row.stock_account_id, 'stock_account_id'), material_id: uuidValue(row.material_id, 'material_id'), condition_code: enumValue(row.condition_code, CONDITIONS, 'condition_code'), availability_bucket: enumValue(row.availability_bucket, BUCKETS, 'availability_bucket'), lot_id: nullableUuid(row.lot_id, 'lot_id'), book_qty: quantity(row.book_qty, 'book_qty'), expected_serial_ids: Object.freeze(uniqueArray(row.expected_serial_ids, (id) => uuidValue(id, 'serial_id'), 'expected_serial_ids')) })
}

function scope(value) {
  const row = exact(value, ['scope_id', 'scope_no', 'scope_mode', 'owner_org_id', 'location_id', 'custodian_person_id_snapshot', 'material_id', 'condition_code', 'availability_bucket', 'assigned_to_me', 'freeze', 'snapshot_visibility', 'snapshot_accounts', 'allowed_actions'], '盘点范围')
  const visibility = enumValue(row.snapshot_visibility, ['not_started', 'hidden', 'visible'], 'snapshot_visibility')
  const accounts = uniqueArray(row.snapshot_accounts, snapshotAccount, 'snapshot_accounts', (item) => item.stock_account_id)
  if (visibility !== 'visible' && accounts.length) fail('隐藏账面不能返回账户快照')
  return Object.freeze({ scope_id: uuidValue(row.scope_id, 'scope_id'), scope_no: integer(row.scope_no, 'scope_no', 1), scope_mode: enumValue(row.scope_mode, ['location_all', 'filtered'], 'scope_mode'), owner_org_id: uuidValue(row.owner_org_id, 'owner_org_id'), location_id: uuidValue(row.location_id, 'location_id'), custodian_person_id_snapshot: nullableUuid(row.custodian_person_id_snapshot, 'custodian_person_id_snapshot'), material_id: nullableUuid(row.material_id, 'material_id'), condition_code: row.condition_code === null ? null : enumValue(row.condition_code, CONDITIONS, 'condition_code'), availability_bucket: row.availability_bucket === null ? null : enumValue(row.availability_bucket, BUCKETS, 'availability_bucket'), assigned_to_me: bool(row.assigned_to_me, 'assigned_to_me'), freeze: freezeFact(row.freeze), snapshot_visibility: visibility, snapshot_accounts: Object.freeze(accounts), allowed_actions: Object.freeze(actions(row.allowed_actions, ['submit_initial_count', 'submit_recount_count'])) })
}

function completion(value) {
  const row = exact(value, ['completion_id', 'scope_id', 'count_ledger_cursor', 'count_line_count', 'observation_line_count', 'serial_count', 'total_counted_qty', 'zero_confirmed', 'completed_by_person_id', 'completed_at'], '范围计数完成事实')
  return Object.freeze({ completion_id: uuidValue(row.completion_id, 'completion_id'), scope_id: uuidValue(row.scope_id, 'scope_id'), count_ledger_cursor: row.count_ledger_cursor === null ? null : integer(row.count_ledger_cursor, 'count_ledger_cursor'), count_line_count: integer(row.count_line_count, 'count_line_count'), observation_line_count: integer(row.observation_line_count, 'observation_line_count'), serial_count: integer(row.serial_count, 'serial_count'), total_counted_qty: quantity(row.total_counted_qty, 'total_counted_qty'), zero_confirmed: bool(row.zero_confirmed, 'zero_confirmed'), completed_by_person_id: uuidValue(row.completed_by_person_id, 'completed_by_person_id'), completed_at: timestamp(row.completed_at, 'completed_at') })
}

function countLine(value) {
  const row = exact(value, ['count_line_id', 'scope_id', 'stock_account_id', 'material_id', 'counted_qty', 'count_method', 'reason_code', 'remark', 'counted_by_me', 'counted_at', 'counted_serial_ids', 'book_qty', 'expected_serial_ids'], '盘点计数行')
  const book = row.book_qty === null ? null : quantity(row.book_qty, 'book_qty')
  const expected = row.expected_serial_ids === null ? null : uniqueArray(row.expected_serial_ids, (id) => uuidValue(id, 'expected_serial_id'), 'expected_serial_ids')
  if ((book === null) !== (expected === null)) fail('计数行账面证据必须同时隐藏')
  return Object.freeze({ count_line_id: uuidValue(row.count_line_id, 'count_line_id'), scope_id: uuidValue(row.scope_id, 'scope_id'), stock_account_id: uuidValue(row.stock_account_id, 'stock_account_id'), material_id: uuidValue(row.material_id, 'material_id'), counted_qty: quantity(row.counted_qty, 'counted_qty'), count_method: enumValue(row.count_method, ['scan', 'manual', 'import'], 'count_method'), reason_code: nullableText(row.reason_code, 'reason_code'), remark: text(row.remark, 'remark'), counted_by_me: bool(row.counted_by_me, 'counted_by_me'), counted_at: timestamp(row.counted_at, 'counted_at'), counted_serial_ids: Object.freeze(uniqueArray(row.counted_serial_ids, (id) => uuidValue(id, 'serial_id'), 'counted_serial_ids')), book_qty: book, expected_serial_ids: expected === null ? null : Object.freeze(expected) })
}

function observation(value) {
  const row = exact(value, ['observation_id', 'scope_id', 'observation_no', 'owner_org_id', 'location_id', 'custodian_person_id_snapshot', 'material_id', 'material_identifier_raw', 'material_identifier_type', 'condition_code', 'availability_bucket', 'lot_id', 'lot_no_raw', 'serial_id', 'serial_no_raw', 'serial_identifier_type', 'counted_qty', 'verification_status', 'requires_verification', 'count_method', 'reason_code', 'remark', 'counted_by_me', 'counted_at', 'disposition'], '实物观察行')
  const verification = enumValue(row.verification_status, ['verified', 'pending_verification'], 'verification_status')
  if (bool(row.requires_verification, 'requires_verification') !== (verification === 'pending_verification')) fail('观察核验状态不一致')
  let disposition = null
  if (row.disposition !== null) {
    const item = exact(row.disposition, ['disposition_id', 'disposition', 'resolved_material_id', 'resolved_lot_id', 'resolved_serial_id', 'reason_code', 'comment', 'decided_at'], '观察处置事实')
    disposition = Object.freeze({ disposition_id: uuidValue(item.disposition_id, 'disposition_id'), disposition: enumValue(item.disposition, ['resolved_existing_master', 'pending_verification', 'requires_recount'], 'disposition'), resolved_material_id: nullableUuid(item.resolved_material_id, 'resolved_material_id'), resolved_lot_id: nullableUuid(item.resolved_lot_id, 'resolved_lot_id'), resolved_serial_id: nullableUuid(item.resolved_serial_id, 'resolved_serial_id'), reason_code: text(item.reason_code, 'reason_code', false), comment: text(item.comment, 'comment'), decided_at: timestamp(item.decided_at, 'decided_at') })
  }
  return Object.freeze({ observation_id: uuidValue(row.observation_id, 'observation_id'), scope_id: uuidValue(row.scope_id, 'scope_id'), observation_no: integer(row.observation_no, 'observation_no', 1), owner_org_id: uuidValue(row.owner_org_id, 'owner_org_id'), location_id: uuidValue(row.location_id, 'location_id'), custodian_person_id_snapshot: nullableUuid(row.custodian_person_id_snapshot, 'custodian_person_id_snapshot'), material_id: nullableUuid(row.material_id, 'material_id'), material_identifier_raw: text(row.material_identifier_raw, 'material_identifier_raw', false), material_identifier_type: enumValue(row.material_identifier_type, ['sku_code', 'qr_code', 'external_code', 'unknown'], 'material_identifier_type'), condition_code: enumValue(row.condition_code, CONDITIONS, 'condition_code'), availability_bucket: enumValue(row.availability_bucket, BUCKETS, 'availability_bucket'), lot_id: nullableUuid(row.lot_id, 'lot_id'), lot_no_raw: nullableText(row.lot_no_raw, 'lot_no_raw'), serial_id: nullableUuid(row.serial_id, 'serial_id'), serial_no_raw: nullableText(row.serial_no_raw, 'serial_no_raw'), serial_identifier_type: row.serial_identifier_type === null ? null : enumValue(row.serial_identifier_type, ['serial_no', 'qr_code', 'unknown'], 'serial_identifier_type'), counted_qty: quantity(row.counted_qty, 'counted_qty'), verification_status: verification, requires_verification: row.requires_verification, count_method: enumValue(row.count_method, ['scan', 'manual', 'import'], 'count_method'), reason_code: nullableText(row.reason_code, 'reason_code'), remark: text(row.remark, 'remark'), counted_by_me: bool(row.counted_by_me, 'counted_by_me'), counted_at: timestamp(row.counted_at, 'counted_at'), disposition })
}

function difference(value) {
  const row = exact(value, ['difference_id', 'scope_id', 'difference_no', 'difference_type', 'material_id', 'expected_account_id', 'observed_account_id', 'observed_line_id', 'serial_id', 'book_qty', 'counted_qty', 'difference_qty', 'affected_qty', 'reason_code', 'reason_text', 'evidence_required', 'posting_blocked_by_pending_verification'], '盘点差异')
  return Object.freeze({ difference_id: uuidValue(row.difference_id, 'difference_id'), scope_id: uuidValue(row.scope_id, 'scope_id'), difference_no: integer(row.difference_no, 'difference_no', 1), difference_type: enumValue(row.difference_type, DIFFERENCE_TYPES, 'difference_type'), material_id: nullableUuid(row.material_id, 'material_id'), expected_account_id: nullableUuid(row.expected_account_id, 'expected_account_id'), observed_account_id: nullableUuid(row.observed_account_id, 'observed_account_id'), observed_line_id: nullableUuid(row.observed_line_id, 'observed_line_id'), serial_id: nullableUuid(row.serial_id, 'serial_id'), book_qty: quantity(row.book_qty, 'book_qty'), counted_qty: quantity(row.counted_qty, 'counted_qty'), difference_qty: quantity(row.difference_qty, 'difference_qty', true), affected_qty: quantity(row.affected_qty, 'affected_qty'), reason_code: nullableText(row.reason_code, 'reason_code'), reason_text: text(row.reason_text, 'reason_text'), evidence_required: bool(row.evidence_required, 'evidence_required'), posting_blocked_by_pending_verification: bool(row.posting_blocked_by_pending_verification, 'posting_blocked_by_pending_verification') })
}

function review(value) {
  if (value === null) return null
  const row = exact(value, ['review_id', 'review_stage', 'decision', 'comment', 'comment_visible', 'reviewer_person_id', 'reviewed_at', 'visible_items', 'covers_all_task_scopes'], '盘点复核事实')
  const comment = row.comment === null ? null : text(row.comment, 'review.comment')
  if (bool(row.comment_visible, 'comment_visible') !== (comment !== null)) fail('复核意见可见性不一致')
  const items = uniqueArray(row.visible_items, (value) => {
    const item = exact(value, ['difference_id', 'decision', 'comment'], '复核逐项决定')
    return Object.freeze({ difference_id: uuidValue(item.difference_id, 'difference_id'), decision: enumValue(item.decision, REVIEW_ITEM_DECISIONS, 'item.decision'), comment: text(item.comment, 'item.comment') })
  }, 'visible_items', (item) => item.difference_id)
  return Object.freeze({ review_id: uuidValue(row.review_id, 'review_id'), review_stage: enumValue(row.review_stage, ['region', 'headquarters'], 'review_stage'), decision: enumValue(row.decision, REVIEW_DECISIONS, 'decision'), comment, comment_visible: row.comment_visible, reviewer_person_id: uuidValue(row.reviewer_person_id, 'reviewer_person_id'), reviewed_at: timestamp(row.reviewed_at, 'reviewed_at'), visible_items: Object.freeze(items), covers_all_task_scopes: bool(row.covers_all_task_scopes, 'covers_all_task_scopes') })
}

function simpleSubmission(value) {
  if (value === null) return null
  const row = exact(value, ['submission_id', 'submitted_at', 'visible_scope_count', 'visible_zero_scope_count', 'visible_count_line_count', 'visible_observation_line_count', 'visible_serial_count', 'visible_total_counted_qty', 'covers_all_task_scopes'], '轮次提交事实')
  return Object.freeze({ submission_id: uuidValue(row.submission_id, 'submission_id'), submitted_at: timestamp(row.submitted_at, 'submitted_at'), visible_scope_count: integer(row.visible_scope_count, 'visible_scope_count'), visible_zero_scope_count: integer(row.visible_zero_scope_count, 'visible_zero_scope_count'), visible_count_line_count: integer(row.visible_count_line_count, 'visible_count_line_count'), visible_observation_line_count: integer(row.visible_observation_line_count, 'visible_observation_line_count'), visible_serial_count: integer(row.visible_serial_count, 'visible_serial_count'), visible_total_counted_qty: quantity(row.visible_total_counted_qty, 'visible_total_counted_qty'), covers_all_task_scopes: bool(row.covers_all_task_scopes, 'covers_all_task_scopes') })
}

function diffCompletion(value) {
  if (value === null) return null
  const row = exact(value, ['completion_id', 'completed_at', 'visible_difference_count', 'visible_pending_verification_count', 'visible_total_affected_qty', 'covers_all_task_scopes'], '差异完成事实')
  return Object.freeze({ completion_id: uuidValue(row.completion_id, 'completion_id'), completed_at: timestamp(row.completed_at, 'completed_at'), visible_difference_count: integer(row.visible_difference_count, 'visible_difference_count'), visible_pending_verification_count: integer(row.visible_pending_verification_count, 'visible_pending_verification_count'), visible_total_affected_qty: quantity(row.visible_total_affected_qty, 'visible_total_affected_qty'), covers_all_task_scopes: bool(row.covers_all_task_scopes, 'covers_all_task_scopes') })
}

function recountCause(value) {
  if (value === null) return null
  const row = exact(value, ['recount_case_id', 'source_round_id', 'source_difference_completion_id', 'trigger_review_id', 'next_round_no', 'visible_scope_count', 'covers_all_task_scopes', 'reason', 'reason_visible', 'opened_by_person_id', 'opened_at', 'assignments'], '复盘原因事实')
  const assignments = uniqueArray(row.assignments, (value) => {
    const item = exact(value, ['assignment_id', 'scope_id', 'assignee_person_id', 'assigned_to_me', 'assigned_at'], '复盘分配事实')
    return Object.freeze({ assignment_id: uuidValue(item.assignment_id, 'assignment_id'), scope_id: uuidValue(item.scope_id, 'scope_id'), assignee_person_id: uuidValue(item.assignee_person_id, 'assignee_person_id'), assigned_to_me: bool(item.assigned_to_me, 'assigned_to_me'), assigned_at: timestamp(item.assigned_at, 'assigned_at') })
  }, 'assignments', (item) => item.scope_id)
  const reason = row.reason === null ? null : text(row.reason, 'reason', false)
  if (bool(row.reason_visible, 'reason_visible') !== (reason !== null)) fail('复盘原因可见性不一致')
  return Object.freeze({ recount_case_id: uuidValue(row.recount_case_id, 'recount_case_id'), source_round_id: uuidValue(row.source_round_id, 'source_round_id'), source_difference_completion_id: uuidValue(row.source_difference_completion_id, 'source_difference_completion_id'), trigger_review_id: uuidValue(row.trigger_review_id, 'trigger_review_id'), next_round_no: integer(row.next_round_no, 'next_round_no', 2), visible_scope_count: integer(row.visible_scope_count, 'visible_scope_count'), covers_all_task_scopes: bool(row.covers_all_task_scopes, 'covers_all_task_scopes'), reason, reason_visible: row.reason_visible, opened_by_person_id: uuidValue(row.opened_by_person_id, 'opened_by_person_id'), opened_at: timestamp(row.opened_at, 'opened_at'), assignments: Object.freeze(assignments) })
}

function posting(value) {
  const row = exact(value, ['status', 'posting_ids', 'posting_fact_count', 'visible_total_quantity', 'covers_all_task_scopes', 'inventory_transaction_count', 'first_posted_at', 'last_posted_at'], '盘点过账事实')
  const status = enumValue(row.status, ['not_posted', 'recorded'], 'posting.status')
  const ids = uniqueArray(row.posting_ids, (id) => uuidValue(id, 'posting_id'), 'posting_ids')
  const count = integer(row.posting_fact_count, 'posting_fact_count')
  const first = nullableTimestamp(row.first_posted_at, 'first_posted_at')
  const last = nullableTimestamp(row.last_posted_at, 'last_posted_at')
  if (count !== ids.length || ((status === 'not_posted') !== (count === 0)) || ((first === null) !== (last === null))) fail('过账状态与不可变事实不一致')
  return Object.freeze({ status, posting_ids: Object.freeze(ids), posting_fact_count: count, visible_total_quantity: quantity(row.visible_total_quantity, 'visible_total_quantity'), covers_all_task_scopes: bool(row.covers_all_task_scopes, 'covers_all_task_scopes'), inventory_transaction_count: integer(row.inventory_transaction_count, 'inventory_transaction_count'), first_posted_at: first, last_posted_at: last })
}

function round(value) {
  const row = exact(value, ['round_id', 'round_no', 'round_type', 'status', 'started_at', 'submitted_at', 'submission', 'visible_scope_completions', 'visible_count_lines', 'visible_observations', 'differences_visible', 'difference_completion', 'visible_differences', 'region_review', 'headquarters_review', 'recount_cause', 'posting', 'allowed_actions'], '盘点轮次')
  const status = enumValue(row.status, ROUND_STATUSES, 'round.status')
  const submittedAt = nullableTimestamp(row.submitted_at, 'submitted_at')
  if ((status !== 'counting') !== (submittedAt !== null)) fail('轮次提交状态与时间不一致')
  const visible = bool(row.differences_visible, 'differences_visible')
  const differences = uniqueArray(row.visible_differences, difference, 'visible_differences', (item) => item.difference_id)
  const completionFact = diffCompletion(row.difference_completion)
  const region = review(row.region_review)
  const headquarters = review(row.headquarters_review)
  if (!visible && (differences.length || completionFact || region || headquarters)) fail('盲盘隐藏阶段不能暴露差异或复核事实')
  return Object.freeze({ round_id: uuidValue(row.round_id, 'round_id'), round_no: integer(row.round_no, 'round_no', 1), round_type: enumValue(row.round_type, ['initial', 'recount'], 'round_type'), status, started_at: timestamp(row.started_at, 'started_at'), submitted_at: submittedAt, submission: simpleSubmission(row.submission), visible_scope_completions: Object.freeze(uniqueArray(row.visible_scope_completions, completion, 'scope_completions', (item) => item.scope_id)), visible_count_lines: Object.freeze(uniqueArray(row.visible_count_lines, countLine, 'count_lines', (item) => item.count_line_id)), visible_observations: Object.freeze(uniqueArray(row.visible_observations, observation, 'observations', (item) => item.observation_id)), differences_visible: visible, difference_completion: completionFact, visible_differences: Object.freeze(differences), region_review: region, headquarters_review: headquarters, recount_cause: recountCause(row.recount_cause), posting: posting(row.posting), allowed_actions: Object.freeze(actions(row.allowed_actions, ROUND_ACTIONS)) })
}

function closeControl(value) {
  const row = exact(value, ['latest_reconciliation', 'close_completion'], '盘点对账关闭控制事实')
  let latest = null
  if (row.latest_reconciliation !== null) {
    const item = exact(row.latest_reconciliation, ['completion_id', 'reconciliation_no', 'reconciliation_ledger_cursor', 'reconciled_task_version', 'reconciled_at'], '最新盘点对账事实')
    latest = Object.freeze({
      completion_id: uuidValue(item.completion_id, 'reconciliation.completion_id'),
      reconciliation_no: integer(item.reconciliation_no, 'reconciliation_no', 1),
      reconciliation_ledger_cursor: integer(item.reconciliation_ledger_cursor, 'reconciliation_ledger_cursor'),
      reconciled_task_version: integer(item.reconciled_task_version, 'reconciled_task_version', 1),
      reconciled_at: timestamp(item.reconciled_at, 'reconciled_at')
    })
  }
  let close = null
  if (row.close_completion !== null) {
    const item = exact(row.close_completion, ['completion_id', 'reconciliation_completion_id', 'closed_task_version', 'closed_at'], '盘点关闭完成事实')
    close = Object.freeze({
      completion_id: uuidValue(item.completion_id, 'close.completion_id'),
      reconciliation_completion_id: uuidValue(item.reconciliation_completion_id, 'reconciliation_completion_id'),
      closed_task_version: integer(item.closed_task_version, 'closed_task_version', 1),
      closed_at: timestamp(item.closed_at, 'close.closed_at')
    })
    if (!latest || close.reconciliation_completion_id !== latest.completion_id || close.closed_task_version !== latest.reconciled_task_version + 1) {
      fail('盘点关闭完成事实未绑定最新对账完成坐标')
    }
  }
  return Object.freeze({ latest_reconciliation: latest, close_completion: close })
}

function validateDetail(value) {
  const row = exact(value, ['schema_version', 'task_id', 'task_no', 'task_type', 'region_org_id', 'status', 'version', 'blind_count', 'current_round_no', 'cutoff_ledger_cursor', 'cutoff_at', 'issued_at', 'frozen_at', 'submitted_at', 'posted_at', 'closed_at', 'cancelled_at', 'deadline', 'note', 'state_axes', 'close_control', 'scopes', 'rounds', 'allowed_actions'], '正式盘点详情')
  if (row.schema_version !== SCHEMA_VERSION) fail('正式盘点 schema_version 不受支持')
  const cursor = row.cutoff_ledger_cursor === null ? null : integer(row.cutoff_ledger_cursor, 'cutoff_ledger_cursor')
  const cutoffAt = nullableTimestamp(row.cutoff_at, 'cutoff_at')
  if ((cursor === null) !== (cutoffAt === null)) fail('盘点截止游标与时间不一致')
  const scopes = uniqueArray(row.scopes, scope, 'scopes', (item) => item.scope_id)
  if (!scopes.length) fail('正式盘点详情必须包含可见范围')
  const rounds = uniqueArray(row.rounds, round, 'rounds', (item) => item.round_id)
  const currentRoundNo = integer(row.current_round_no, 'current_round_no')
  if (!currentRoundNo && rounds.length) fail('草稿任务不能暴露轮次')
  const status = enumValue(row.status, TASK_STATUSES, 'status')
  const version = integer(row.version, 'version')
  const axes = stateAxes(row.state_axes)
  const control = closeControl(row.close_control)
  const postedAt = nullableTimestamp(row.posted_at, 'posted_at')
  const closedAt = nullableTimestamp(row.closed_at, 'closed_at')
  const allowed = actions(row.allowed_actions)
  if ((status === 'closed') !== (closedAt !== null) || (status === 'closed') !== (axes.closure_status === 'closed') || (status === 'closed') !== (control.close_completion !== null)) fail('盘点任务、关闭轴与关闭完成事实不一致')
  if ((control.latest_reconciliation === null) !== (axes.reconciliation_status === 'not_reconciled')) fail('盘点对账轴与最新对账完成事实不一致')
  if (axes.reconciliation_status === 'recorded' && control.latest_reconciliation.reconciled_task_version !== (status === 'closed' ? version - 1 : version)) fail('盘点对账轴与对账任务版本不一致')
  if (status === 'posted' && axes.reconciliation_status === 'stale' && control.latest_reconciliation && control.latest_reconciliation.reconciled_task_version > version) fail('过期内部对账版本不能晚于任务版本')
  if (control.close_completion && (control.close_completion.closed_task_version !== version || control.close_completion.closed_at !== closedAt)) fail('盘点关闭完成事实与详情版本或时间不一致')
  if (status !== 'posted' && status !== 'closed' && (axes.posting_status !== 'not_posted' || axes.reconciliation_status !== 'not_reconciled' || axes.closure_status !== 'open' || control.latest_reconciliation !== null || control.close_completion !== null || allowed.includes('reconcile') || allowed.includes('close'))) fail('非终态盘点不得携带过账、对账、关闭事实或终态动作')
  if (((status === 'posted' || status === 'closed') !== (postedAt !== null)) || ((status === 'posted' || status === 'closed') && axes.posting_status !== 'recorded')) fail('任务过账状态、时间与独立过账完成事实不一致')
  if (control.latest_reconciliation && (postedAt === null || Date.parse(control.latest_reconciliation.reconciled_at) <= Date.parse(postedAt))) fail('内部对账时间必须晚于过账时间')
  if (control.close_completion && control.latest_reconciliation && Date.parse(control.close_completion.closed_at) <= Date.parse(control.latest_reconciliation.reconciled_at)) fail('关闭时间必须晚于内部对账时间')
  if (status === 'posted' && (allowed.some((action) => action !== 'reconcile' && action !== 'close') || scopes.some((item) => item.allowed_actions.length) || rounds.some((item) => item.allowed_actions.length))) fail('已过账任务只能开放独立对账或关闭动作')
  if (status === 'closed' && (axes.reconciliation_status !== 'recorded' || control.latest_reconciliation === null || control.latest_reconciliation.reconciled_task_version !== version - 1 || allowed.length || scopes.some((item) => item.allowed_actions.length) || rounds.some((item) => item.allowed_actions.length))) fail('已关闭任务必须承接当前对账事实且不得再暴露写动作')
  if (status === 'posted' && allowed.includes('close') && axes.reconciliation_status !== 'recorded') fail('关闭动作只能由当前有效内部对账开放')
  return Object.freeze({ schema_version: SCHEMA_VERSION, task_id: uuidValue(row.task_id, 'task_id'), task_no: text(row.task_no, 'task_no', false), task_type: enumValue(row.task_type, TASK_TYPES, 'task_type'), region_org_id: uuidValue(row.region_org_id, 'region_org_id'), status, version, blind_count: bool(row.blind_count, 'blind_count'), current_round_no: currentRoundNo, cutoff_ledger_cursor: cursor, cutoff_at: cutoffAt, issued_at: nullableTimestamp(row.issued_at, 'issued_at'), frozen_at: nullableTimestamp(row.frozen_at, 'frozen_at'), submitted_at: nullableTimestamp(row.submitted_at, 'submitted_at'), posted_at: postedAt, closed_at: closedAt, cancelled_at: nullableTimestamp(row.cancelled_at, 'cancelled_at'), deadline: nullableTimestamp(row.deadline, 'deadline'), note: text(row.note, 'note'), state_axes: axes, close_control: control, scopes: Object.freeze(scopes), rounds: Object.freeze(rounds), allowed_actions: Object.freeze(allowed) })
}

function fixedQuantityText(value, positive = false) {
  if (typeof value !== 'string') fail('数量必须以十进制文本提交')
  const checked = value.trim()
  if (!INPUT_QUANTITY.test(checked)) fail('数量必须是非指数十进制文本，最多三位小数')
  const parts = checked.split('.')
  const result = `${parts[0]}.${(parts[1] || '').padEnd(3, '0')}`
  if (positive && result === '0.000') fail('实物观察数量必须大于零')
  return result
}

function countBody(value) {
  const row = exact(value, ['count_mode', 'account_counts', 'physical_observations', 'evidence_file_ids', 'zero_confirmed'], '盘点计数命令')
  const mode = enumValue(row.count_mode, ['blind', 'open'], 'count_mode')
  const accounts = uniqueArray(row.account_counts, (value) => {
    const item = exact(value, ['stock_account_id', 'counted_qty', 'count_method', 'serial_ids', 'book_qty_confirmation', 'reason_code', 'remark'], '账户计数')
    const confirmation = item.book_qty_confirmation === null ? null : fixedQuantityText(item.book_qty_confirmation)
    if ((mode === 'open') !== (confirmation !== null)) fail('明盘必须确认账面数，盲盘不得回传账面数')
    return Object.freeze({ stock_account_id: uuidValue(item.stock_account_id, 'stock_account_id'), counted_qty: fixedQuantityText(item.counted_qty), count_method: enumValue(item.count_method, ['scan', 'manual', 'import'], 'count_method'), serial_ids: Object.freeze(uniqueArray(item.serial_ids, (id) => uuidValue(id, 'serial_id'), 'serial_ids')), book_qty_confirmation: confirmation, reason_code: item.reason_code === null ? null : text(item.reason_code, 'reason_code', false), remark: text(item.remark, 'remark') })
  }, 'account_counts', (item) => item.stock_account_id)
  if (!Array.isArray(row.physical_observations)) fail('physical_observations必须是数组')
  const observations = row.physical_observations.map((value) => {
    const item = exact(value, ['material_id', 'material_identifier_raw', 'material_identifier_type', 'condition_code', 'availability_bucket', 'counted_qty', 'lot_id', 'lot_no_raw', 'serial_id', 'serial_no_raw', 'serial_identifier_type', 'count_method', 'reason_code', 'remark'], '实物观察')
    return Object.freeze({ material_id: nullableUuid(item.material_id, 'material_id'), material_identifier_raw: text(item.material_identifier_raw, 'material_identifier_raw', false), material_identifier_type: enumValue(item.material_identifier_type, ['sku_code', 'qr_code', 'external_code', 'unknown'], 'material_identifier_type'), condition_code: enumValue(item.condition_code, CONDITIONS, 'condition_code'), availability_bucket: enumValue(item.availability_bucket, BUCKETS, 'availability_bucket'), counted_qty: fixedQuantityText(item.counted_qty, true), lot_id: nullableUuid(item.lot_id, 'lot_id'), lot_no_raw: nullableText(item.lot_no_raw, 'lot_no_raw'), serial_id: nullableUuid(item.serial_id, 'serial_id'), serial_no_raw: nullableText(item.serial_no_raw, 'serial_no_raw'), serial_identifier_type: item.serial_identifier_type === null ? null : enumValue(item.serial_identifier_type, ['serial_no', 'qr_code', 'unknown'], 'serial_identifier_type'), count_method: enumValue(item.count_method, ['scan', 'manual', 'import'], 'count_method'), reason_code: item.reason_code === null ? null : text(item.reason_code, 'reason_code', false), remark: text(item.remark, 'remark') })
  })
  const evidence = uniqueArray(row.evidence_file_ids, (id) => uuidValue(id, 'evidence_file_id'), 'evidence_file_ids')
  const zero = bool(row.zero_confirmed, 'zero_confirmed')
  if (zero && (accounts.length || observations.length)) fail('零库存确认不能与计数明细并存')
  if (!zero && !accounts.length && !observations.length) fail('非零计数必须包含明细')
  return Object.freeze({ count_mode: mode, account_counts: Object.freeze(accounts), physical_observations: Object.freeze(observations), evidence_file_ids: Object.freeze(evidence), zero_confirmed: zero })
}

function commandPath(input) {
  if (input.action === 'create_personal') return '/v1/stocktakes/personal'
  const task = uuidValue(input.taskId, 'task_id')
  if (input.action === 'start') return `/v1/stocktakes/${task}/start`
  if (input.action === 'reconcile') return `/v1/stocktakes/${task}/reconcile`
  if (input.action === 'close') return `/v1/stocktakes/${task}/close`
  if (input.action === 'post') return `/v1/stocktakes/${task}/post-differences`
  const round = uuidValue(input.roundId, 'round_id')
  if (input.action === 'submit_initial_count') return `/v1/stocktakes/${task}/rounds/${round}/scopes/${uuidValue(input.scopeId, 'scope_id')}/initial-count`
  if (input.action === 'submit_recount_count') return `/v1/stocktakes/${task}/rounds/${round}/scopes/${uuidValue(input.scopeId, 'scope_id')}/recount-count`
  if (input.action === 'generate_initial_differences') return `/v1/stocktakes/${task}/rounds/${round}/differences`
  if (input.action === 'generate_recount_differences') return `/v1/stocktakes/${task}/rounds/${round}/recount-differences`
  if (input.action === 'review_region') return `/v1/stocktakes/${task}/rounds/${round}/reviews/region`
  if (input.action === 'review_headquarters') return `/v1/stocktakes/${task}/rounds/${round}/reviews/headquarters`
  return `/v1/stocktakes/${task}/rounds/${round}/recount`
}

function commandBody(input) {
  const body = objectValue(input.body, '正式盘点命令')
  if (input.action === 'create_personal') {
    const row = exact(body, ['blind_count', 'freeze_mode', 'note'], '个人自盘创建命令')
    return { blind_count: bool(row.blind_count, 'blind_count'), freeze_mode: enumValue(row.freeze_mode, ['hard', 'cutoff_replay'], 'freeze_mode'), note: text(row.note, 'note') }
  }
  if (input.action === 'start') {
    const row = exact(body, ['expected_version'], '盘点启动命令')
    if (integer(row.expected_version, 'expected_version') !== input.expectedTaskVersion) fail('启动版本与写意图不一致')
    return { expected_version: row.expected_version }
  }
  if (input.action === 'reconcile' || input.action === 'close' || input.action === 'post') {
    const row = exact(body, ['expected_task_version'], input.action === 'reconcile' ? '盘点内部对账命令' : input.action === 'close' ? '盘点关闭命令' : '盘点过账命令')
    if (integer(row.expected_task_version, 'expected_task_version') !== input.expectedTaskVersion) fail(input.action === 'post' ? '过账版本与写意图不一致' : '终态操作版本与写意图不一致')
    return { expected_task_version: row.expected_task_version }
  }
  if (input.action === 'submit_initial_count' || input.action === 'submit_recount_count') return countBody(body)
  if (input.action === 'generate_initial_differences' || input.action === 'generate_recount_differences') {
    const row = exact(body, ['expected_task_version'], '差异生成命令')
    if (integer(row.expected_task_version, 'expected_task_version') !== input.expectedTaskVersion) fail('差异版本与写意图不一致')
    return { expected_task_version: row.expected_task_version }
  }
  if (input.action === 'open_recount') {
    const row = exact(body, ['expected_task_version', 'assignments', 'reason'], '开复盘命令')
    if (integer(row.expected_task_version, 'expected_task_version') !== input.expectedTaskVersion) fail('开复盘版本与写意图不一致')
    const assignments = uniqueArray(row.assignments, (value) => {
      const item = exact(value, ['scope_id', 'assignee_user_id'], '复盘分配')
      const assigneeUserId = text(item.assignee_user_id, 'assignee_user_id', false)
      if (assigneeUserId.length > 160) fail('assignee_user_id 无效')
      return Object.freeze({ scope_id: uuidValue(item.scope_id, 'scope_id'), assignee_user_id: assigneeUserId })
    }, 'assignments', (item) => item.scope_id)
    if (!assignments.length) fail('开复盘至少选择一个范围')
    return { expected_task_version: row.expected_task_version, assignments, reason: text(row.reason, 'reason', false) }
  }
  const row = exact(body, ['expected_task_version', 'decision', 'items', 'comment'], '盘点复核命令')
  if (integer(row.expected_task_version, 'expected_task_version') !== input.expectedTaskVersion) fail('复核版本与写意图不一致')
  const decision = enumValue(row.decision, REVIEW_DECISIONS, 'decision')
  const comment = text(row.comment, 'comment')
  if (decision !== 'approve' && !comment) fail('复盘或驳回必须填写意见')
  const items = uniqueArray(row.items, (value) => {
    const item = exact(value, ['difference_id', 'decision', 'comment'], '复核逐项决定')
    return Object.freeze({ difference_id: uuidValue(item.difference_id, 'difference_id'), decision: enumValue(item.decision, REVIEW_ITEM_DECISIONS, 'item.decision'), comment: text(item.comment, 'item.comment') })
  }, 'items', (item) => item.difference_id)
  return { expected_task_version: row.expected_task_version, decision, items, comment }
}

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    Object.values(value).forEach(deepFreeze)
  }
  return value
}

function createIntentRegistry(options = {}) {
  let pending = null
  return {
    begin(input) {
      const path = commandPath(input)
      const body = deepFreeze(JSON.parse(JSON.stringify(commandBody(input))))
      const signature = `${input.action}\n${path}\n${JSON.stringify(body)}`
      if (pending) {
        if (pending.signature !== signature) fail('上一笔盘点写结果尚未精确确认，禁止生成新写坐标')
        return pending
      }
      if (typeof options.coordinateFactory !== 'function') fail('盘点写坐标生成器不可用')
      const headers = options.coordinateFactory()
      if (!headers || !SAFE_IDEMPOTENCY_KEY.test(headers['Idempotency-Key'] || '') || !SAFE_REQUEST_ID.test(headers['X-Request-ID'] || '')) fail('盘点写坐标无效')
      pending = deepFreeze({ action: input.action, method: 'POST', path, body, headers: Object.assign({}, headers), taskId: input.action === 'create_personal' ? null : uuidValue(input.taskId, 'task_id'), roundId: Object.prototype.hasOwnProperty.call(input, 'roundId') ? uuidValue(input.roundId, 'round_id') : null, scopeId: Object.prototype.hasOwnProperty.call(input, 'scopeId') ? uuidValue(input.scopeId, 'scope_id') : null, expectedTaskVersion: input.action === 'create_personal' ? null : input.expectedTaskVersion, signature })
      return pending
    },
    current() { return pending },
    complete(intent) { if (pending !== intent) fail('只能完成当前盘点写意图'); pending = null }
  }
}

function resultBase(value, keys, name) {
  const row = exact(value, ['schema_version'].concat(keys), name)
  if (row.schema_version !== SCHEMA_VERSION) fail(`${name} schema_version 不受支持`)
  return row
}

function validateWriteResult(intent, value) {
  let row
  if (intent.action === 'create_personal') {
    row = resultBase(value, ['task_id', 'task_no', 'task_type', 'status', 'task_version', 'scope_count', 'idempotency_replayed'], '个人自盘创建结果')
    if (row.task_type !== 'personal' || row.status !== 'draft' || row.task_version !== 0) fail('个人自盘创建结果无效')
    uuidValue(row.task_id, 'task_id'); text(row.task_no, 'task_no', false); integer(row.scope_count, 'scope_count', 1); bool(row.idempotency_replayed, 'idempotency_replayed')
  } else if (intent.action === 'start') {
    row = resultBase(value, ['task_id', 'task_type', 'status', 'task_version', 'cutoff_ledger_cursor', 'initial_round_id', 'scope_count', 'snapshot_line_count', 'active_freeze_count', 'idempotency_replayed'], '盘点启动结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || row.status !== 'counting') fail('盘点启动结果锚点无效')
    enumValue(row.task_type, TASK_TYPES, 'task_type'); integer(row.task_version, 'task_version', 1); integer(row.cutoff_ledger_cursor, 'cutoff_ledger_cursor'); uuidValue(row.initial_round_id, 'initial_round_id')
    const scopeCount = integer(row.scope_count, 'scope_count', 1)
    if (integer(row.active_freeze_count, 'active_freeze_count', 1) !== scopeCount) fail('启动结果冻结范围不完整')
    integer(row.snapshot_line_count, 'snapshot_line_count'); bool(row.idempotency_replayed, 'idempotency_replayed')
  } else if (intent.action === 'reconcile') {
    row = resultBase(value, ['completion_id', 'task_id', 'posting_completion_id', 'reconciliation_no', 'reconciliation_ledger_cursor', 'resulting_task_status', 'task_version', 'scope_count', 'account_count', 'scoped_account_count', 'serial_count', 'transaction_count', 'movement_count', 'book_total_qty', 'physical_total_qty', 'reconciled_at', 'replayed'], '盘点内部对账结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || row.resulting_task_status !== 'posted') fail('盘点内部对账结果锚点无效')
    uuidValue(row.completion_id, 'completion_id'); uuidValue(row.posting_completion_id, 'posting_completion_id')
    integer(row.reconciliation_no, 'reconciliation_no', 1); integer(row.reconciliation_ledger_cursor, 'reconciliation_ledger_cursor')
    if (integer(row.task_version, 'task_version', 1) !== intent.expectedTaskVersion + 1) fail('盘点内部对账结果版本无效')
    integer(row.scope_count, 'scope_count', 1)
    const accountCount = integer(row.account_count, 'account_count')
    const scopedAccountCount = integer(row.scoped_account_count, 'scoped_account_count')
    if (scopedAccountCount > accountCount) fail('盘点内部对账范围账户数量无效')
    integer(row.serial_count, 'serial_count'); integer(row.transaction_count, 'transaction_count'); integer(row.movement_count, 'movement_count')
    if (quantity(row.book_total_qty, 'book_total_qty') !== quantity(row.physical_total_qty, 'physical_total_qty')) fail('盘点内部对账账物总量不一致')
    timestamp(row.reconciled_at, 'reconciled_at'); bool(row.replayed, 'replayed')
  } else if (intent.action === 'close') {
    row = resultBase(value, ['completion_id', 'task_id', 'reconciliation_completion_id', 'reconciliation_no', 'reconciliation_ledger_cursor', 'resulting_task_status', 'task_version', 'closed_at', 'replayed'], '盘点关闭结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || row.resulting_task_status !== 'closed') fail('盘点关闭结果锚点无效')
    uuidValue(row.completion_id, 'completion_id'); uuidValue(row.reconciliation_completion_id, 'reconciliation_completion_id')
    integer(row.reconciliation_no, 'reconciliation_no', 1); integer(row.reconciliation_ledger_cursor, 'reconciliation_ledger_cursor')
    if (integer(row.task_version, 'task_version', 1) !== intent.expectedTaskVersion + 1) fail('盘点关闭结果版本无效')
    timestamp(row.closed_at, 'closed_at'); bool(row.replayed, 'replayed')
  } else if (intent.action === 'post') {
    row = resultBase(value, ['completion_id', 'task_id', 'terminal_round_id', 'resulting_task_status', 'task_version', 'scope_count', 'difference_count', 'accepted_difference_count', 'no_adjustment_count', 'transaction_count', 'movement_count', 'total_quantity', 'first_ledger_cursor', 'last_ledger_cursor', 'replayed'], '盘点过账结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || row.resulting_task_status !== 'posted') fail('盘点过账结果锚点无效')
    uuidValue(row.completion_id, 'completion_id'); uuidValue(row.terminal_round_id, 'terminal_round_id')
    if (integer(row.task_version, 'task_version', 1) !== intent.expectedTaskVersion + 1) fail('盘点过账结果版本无效')
    integer(row.scope_count, 'scope_count', 1); integer(row.difference_count, 'difference_count'); integer(row.accepted_difference_count, 'accepted_difference_count'); integer(row.no_adjustment_count, 'no_adjustment_count'); integer(row.transaction_count, 'transaction_count'); integer(row.movement_count, 'movement_count'); quantity(row.total_quantity, 'total_quantity')
    if (row.accepted_difference_count + row.no_adjustment_count !== row.difference_count || row.movement_count !== row.accepted_difference_count) fail('盘点过账结果数量关系无效')
    if (row.first_ledger_cursor !== null) integer(row.first_ledger_cursor, 'first_ledger_cursor', 1)
    if (row.last_ledger_cursor !== null) integer(row.last_ledger_cursor, 'last_ledger_cursor', 1)
    if (row.transaction_count === 0 && (row.first_ledger_cursor !== null || row.last_ledger_cursor !== null || row.movement_count !== 0 || row.total_quantity !== '0.000')) fail('盘点过账零流水结果无效')
    if (row.transaction_count > 0 && (row.first_ledger_cursor === null || row.last_ledger_cursor === null || row.last_ledger_cursor - row.first_ledger_cursor + 1 !== row.transaction_count || row.movement_count <= 0 || row.total_quantity === '0.000')) fail('盘点过账流水摘要无效')
    bool(row.replayed, 'replayed')
  } else if (intent.action === 'submit_initial_count' || intent.action === 'submit_recount_count') {
    const recount = intent.action === 'submit_recount_count'
    row = resultBase(value, ['task_id', 'round_id', 'scope_id'].concat(recount ? ['recount_case_id'] : []).concat(['task_status', 'round_status', 'task_version', 'scope_completed', 'round_submitted']).concat(recount ? ['count_ledger_cursor'] : []).concat(['evidence_file_count', 'replayed']), recount ? '复盘计数结果' : '初盘计数结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || uuidValue(row.round_id, 'round_id') !== intent.roundId || uuidValue(row.scope_id, 'scope_id') !== intent.scopeId) fail('盘点计数结果锚点无效')
    if (recount) { uuidValue(row.recount_case_id, 'recount_case_id'); integer(row.count_ledger_cursor, 'count_ledger_cursor') }
    text(row.task_status, 'task_status', false); enumValue(row.round_status, recount ? ['counting', 'submitted'] : ROUND_STATUSES, 'round_status'); integer(row.task_version, 'task_version'); bool(row.scope_completed, 'scope_completed'); bool(row.round_submitted, 'round_submitted'); integer(row.evidence_file_count, 'evidence_file_count'); bool(row.replayed, 'replayed')
  } else if (intent.action === 'generate_initial_differences' || intent.action === 'generate_recount_differences') {
    const recount = intent.action === 'generate_recount_differences'
    row = resultBase(value, ['task_id', 'round_id'].concat(recount ? ['recount_case_id'] : []).concat(['completion_id', 'task_status', 'round_status', 'task_version', 'difference_status', 'difference_count', 'physical_difference_count', 'pending_observation_difference_count', 'total_affected_qty', 'difference_manifest_sha256']).concat(recount ? ['selected_scope_count'] : []).concat(['replayed']), recount ? '复盘差异结果' : '初盘差异结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || uuidValue(row.round_id, 'round_id') !== intent.roundId || row.difference_status !== 'evaluated') fail('盘点差异结果锚点无效')
    if (recount) { uuidValue(row.recount_case_id, 'recount_case_id'); integer(row.selected_scope_count, 'selected_scope_count', 1) }
    uuidValue(row.completion_id, 'completion_id'); text(row.task_status, 'task_status', false); enumValue(row.round_status, recount ? ['submitted'] : ['submitted', 'superseded'], 'round_status'); integer(row.task_version, 'task_version'); integer(row.difference_count, 'difference_count'); integer(row.physical_difference_count, 'physical_difference_count'); integer(row.pending_observation_difference_count, 'pending_observation_difference_count'); quantity(row.total_affected_qty, 'total_affected_qty')
    if (typeof row.difference_manifest_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(row.difference_manifest_sha256)) fail('差异清单摘要无效')
    bool(row.replayed, 'replayed')
  } else if (intent.action === 'open_recount') {
    row = resultBase(value, ['recount_case_id', 'task_id', 'source_round_id', 'next_round_id', 'next_round_no', 'scope_count', 'assignment_count', 'resulting_task_status', 'task_version', 'replayed'], '开复盘结果')
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || uuidValue(row.source_round_id, 'source_round_id') !== intent.roundId || row.resulting_task_status !== 'counting') fail('开复盘结果锚点无效')
    uuidValue(row.recount_case_id, 'recount_case_id'); uuidValue(row.next_round_id, 'next_round_id'); integer(row.next_round_no, 'next_round_no', 2); integer(row.scope_count, 'scope_count', 1); integer(row.assignment_count, 'assignment_count', 1); integer(row.task_version, 'task_version'); bool(row.replayed, 'replayed')
  } else {
    row = resultBase(value, ['review_id', 'task_id', 'round_id', 'review_stage', 'decision', 'resulting_task_status', 'task_version', 'item_count', 'pending_verification_count', 'ready_for_posting', 'replayed'], '盘点复核结果')
    const stage = intent.action === 'review_region' ? 'region' : 'headquarters'
    if (uuidValue(row.task_id, 'task_id') !== intent.taskId || uuidValue(row.round_id, 'round_id') !== intent.roundId || row.review_stage !== stage) fail('盘点复核结果锚点无效')
    uuidValue(row.review_id, 'review_id'); enumValue(row.decision, REVIEW_DECISIONS, 'decision'); text(row.resulting_task_status, 'resulting_task_status', false); integer(row.task_version, 'task_version'); integer(row.item_count, 'item_count'); integer(row.pending_verification_count, 'pending_verification_count'); bool(row.ready_for_posting, 'ready_for_posting'); bool(row.replayed, 'replayed')
  }
  return Object.freeze(Object.assign({}, row))
}

function confirmWrite(intent, result, detail) {
  if (detail.task_id !== uuidValue(result.task_id, 'result.task_id') || detail.version !== integer(result.task_version, 'task_version')) fail('精确回读未确认同一任务版本')
  if (intent.action === 'create_personal') {
    if (detail.task_type !== 'personal' || detail.task_no !== result.task_no || detail.status !== 'draft') fail('精确回读未确认个人自盘草稿')
    return
  }
  if (intent.action === 'reconcile') {
    const latest = detail.close_control && detail.close_control.latest_reconciliation
    if (
      detail.status !== 'posted' || detail.posted_at === null || detail.closed_at !== null ||
      detail.state_axes.posting_status !== 'recorded' || detail.state_axes.reconciliation_status !== 'recorded' || detail.state_axes.closure_status !== 'open' ||
      !latest || detail.close_control.close_completion !== null ||
      latest.completion_id !== uuidValue(result.completion_id, 'completion_id') ||
      latest.reconciliation_no !== integer(result.reconciliation_no, 'reconciliation_no', 1) ||
      latest.reconciliation_ledger_cursor !== integer(result.reconciliation_ledger_cursor, 'reconciliation_ledger_cursor') ||
      latest.reconciled_task_version !== integer(result.task_version, 'task_version', 1) ||
      latest.reconciled_at !== timestamp(result.reconciled_at, 'reconciled_at')
    ) fail('精确回读未确认同一盘点内部对账完成坐标')
    return
  }
  if (intent.action === 'close') {
    const control = detail.close_control
    const latest = control && control.latest_reconciliation
    const close = control && control.close_completion
    if (
      detail.status !== 'closed' || detail.posted_at === null || detail.closed_at !== timestamp(result.closed_at, 'closed_at') ||
      detail.state_axes.posting_status !== 'recorded' || detail.state_axes.reconciliation_status !== 'recorded' || detail.state_axes.closure_status !== 'closed' ||
      !latest || !close ||
      latest.completion_id !== uuidValue(result.reconciliation_completion_id, 'reconciliation_completion_id') ||
      latest.reconciliation_no !== integer(result.reconciliation_no, 'reconciliation_no', 1) ||
      latest.reconciliation_ledger_cursor !== integer(result.reconciliation_ledger_cursor, 'reconciliation_ledger_cursor') ||
      latest.reconciled_task_version !== intent.expectedTaskVersion ||
      close.completion_id !== uuidValue(result.completion_id, 'completion_id') ||
      close.reconciliation_completion_id !== result.reconciliation_completion_id.toLowerCase() ||
      close.closed_task_version !== integer(result.task_version, 'task_version', 1) ||
      close.closed_at !== result.closed_at
    ) fail('精确回读未确认同一盘点关闭完成坐标')
    return
  }
  if (intent.action === 'post') {
    confirmPostProjection(result, detail, intent.expectedTaskVersion, false)
    return
  }
  const roundId = intent.action === 'start' ? uuidValue(result.initial_round_id, 'initial_round_id') : intent.roundId
  const current = detail.rounds.find((row) => row.round_id === roundId)
  if (intent.action === 'start') {
    if (!current || detail.status !== 'counting' || detail.cutoff_ledger_cursor !== result.cutoff_ledger_cursor) fail('精确回读未确认盘点启动事实')
    return
  }
  if (!current) fail('精确回读缺少目标轮次')
  if (intent.action === 'submit_initial_count' || intent.action === 'submit_recount_count') {
    if (!current.visible_scope_completions.some((row) => row.scope_id === intent.scopeId)) fail('精确回读未确认范围完成事实')
    return
  }
  if (intent.action === 'generate_initial_differences' || intent.action === 'generate_recount_differences') {
    if (!current.difference_completion || current.difference_completion.completion_id !== result.completion_id) fail('精确回读未确认差异完成事实')
    return
  }
  if (intent.action === 'review_region' || intent.action === 'review_headquarters') {
    const fact = intent.action === 'review_region' ? current.region_review : current.headquarters_review
    if (!fact || fact.review_id !== result.review_id || fact.decision !== result.decision) fail('精确回读未确认独立复核事实')
    return
  }
  if (detail.current_round_no !== result.next_round_no || !detail.rounds.some((row) => row.round_id === result.next_round_id && row.recount_cause && row.recount_cause.recount_case_id === result.recount_case_id)) fail('精确回读未确认复盘事实')
}

function confirmPostProjection(
  result,
  detail,
  expectedTaskVersion,
  allowAdvancedVersion = true,
) {
  integer(expectedTaskVersion, "expected_task_version")
  const taskId = uuidValue(result.task_id, "result.task_id");
  const taskVersion = integer(result.task_version, "result.task_version", 1);
  if (
    detail.task_id !== taskId
    || taskVersion !== expectedTaskVersion + 1
    || (!allowAdvancedVersion && detail.version !== taskVersion)
    || (allowAdvancedVersion && detail.version < taskVersion)
  ) fail("盘点过账历史证据与当前任务版本不一致");
  const terminalRoundId = uuidValue(result.terminal_round_id, "result.terminal_round_id");
  const terminalRound = detail.rounds.find((row) => row.round_id === terminalRoundId);
  const expectedScopeCount = integer(result.scope_count, "result.scope_count", 1);
  const differenceCount = integer(result.difference_count, "result.difference_count");
  const acceptedCount = integer(result.accepted_difference_count, "result.accepted_difference_count");
  const noAdjustmentCount = integer(result.no_adjustment_count, "result.no_adjustment_count");
  const movementCount = integer(result.movement_count, "result.movement_count");
  const transactionCount = integer(result.transaction_count, "result.transaction_count");
  const totalQuantity = quantity(result.total_quantity, "result.total_quantity");
  const headquartersItems = terminalRound?.headquarters_review?.visible_items ?? [];
  const visibleDifferenceIds = new Set(terminalRound?.visible_differences.map((item) => item.difference_id) ?? []);
  const headquartersDifferenceIds = new Set(headquartersItems.map((item) => item.difference_id));
  const statusAllowed = detail.status === "posted"
    || (allowAdvancedVersion && detail.status === "closed");
  if (
    !terminalRound
    || terminalRound.status !== "submitted"
    || acceptedCount + noAdjustmentCount !== differenceCount
    || movementCount !== acceptedCount
    || (detail.status === "closed" && detail.version < taskVersion + 2)
    || !statusAllowed
    || detail.posted_at === null
    || (detail.status === "posted" && detail.closed_at !== null)
    || (detail.status === "closed" && detail.closed_at === null)
    || detail.state_axes.posting_status !== "recorded"
    || detail.current_round_no !== terminalRound.round_no
    || detail.scopes.length !== expectedScopeCount
    || terminalRound.difference_completion?.visible_difference_count !== differenceCount
    || terminalRound.difference_completion?.covers_all_task_scopes !== true
    || terminalRound.visible_differences.length !== differenceCount
    || terminalRound.visible_differences.some((item) => item.posting_blocked_by_pending_verification)
    || terminalRound.region_review?.review_stage !== "region"
    || terminalRound.headquarters_review?.review_stage !== "headquarters"
    || terminalRound.region_review?.decision !== "approve"
    || terminalRound.region_review?.covers_all_task_scopes !== true
    || terminalRound.headquarters_review?.decision !== "approve"
    || terminalRound.headquarters_review?.covers_all_task_scopes !== true
    || headquartersItems.length !== differenceCount
    || headquartersDifferenceIds.size !== differenceCount
    || [...visibleDifferenceIds].some((differenceId) => !headquartersDifferenceIds.has(differenceId))
    || headquartersItems.some((item) => !visibleDifferenceIds.has(item.difference_id))
    || headquartersItems.filter((item) => item.decision === "accept_for_posting").length !== acceptedCount
    || headquartersItems.filter((item) => item.decision === "no_adjustment").length !== noAdjustmentCount
    || terminalRound.posting.posting_fact_count !== movementCount
    || terminalRound.posting.inventory_transaction_count !== transactionCount
    || terminalRound.posting.visible_total_quantity !== totalQuantity
    || !terminalRound.posting.covers_all_task_scopes
    || (movementCount > 0 && terminalRound.posting.status !== "recorded")
    || (movementCount === 0 && terminalRound.posting.status !== "not_posted")
    || detail.allowed_actions.includes("post")
  ) fail("精确回读未确认独立盘点差异过账完成事实");
}

function retryState(intent, detail) {
  if (intent.action === 'create_personal') return 'retryable'
  if (!detail || !intent.taskId || detail.task_id !== intent.taskId) return 'handoff_required'
  if (intent.action === 'reconcile' || intent.action === 'close') {
    if (detail.version === intent.expectedTaskVersion) return detail.allowed_actions.includes(intent.action) ? 'retryable' : 'handoff_required'
    if (detail.version !== intent.expectedTaskVersion + 1) return 'handoff_required'
    if (intent.action === 'reconcile') {
      const latest = detail.close_control && detail.close_control.latest_reconciliation
      return detail.status === 'posted' && detail.posted_at !== null && detail.closed_at === null &&
        detail.state_axes.posting_status === 'recorded' && detail.state_axes.reconciliation_status === 'recorded' && detail.state_axes.closure_status === 'open' &&
        latest && latest.reconciled_task_version === detail.version && detail.close_control.close_completion === null
        ? 'retryable' : 'handoff_required'
    }
    const close = detail.close_control && detail.close_control.close_completion
    return detail.status === 'closed' && detail.closed_at !== null && detail.state_axes.closure_status === 'closed' &&
      close && close.closed_task_version === detail.version && close.closed_at === detail.closed_at
      ? 'retryable' : 'handoff_required'
  }
  if (detail.version !== intent.expectedTaskVersion) return 'handoff_required'
  if (intent.action === 'start') return detail.allowed_actions.includes('start') ? 'retryable' : 'handoff_required'
  if (intent.action === 'post') return 'handoff_required'
  if (intent.action === 'submit_initial_count' || intent.action === 'submit_recount_count') {
    const scope = detail.scopes.find((row) => row.scope_id === intent.scopeId)
    return scope && scope.allowed_actions.includes(intent.action) ? 'retryable' : 'handoff_required'
  }
  const round = detail.rounds.find((row) => row.round_id === intent.roundId)
  return round && round.allowed_actions.includes(intent.action) ? 'retryable' : 'handoff_required'
}

module.exports = {
  SCHEMA_VERSION,
  ACTIONS,
  TASK_STATUSES,
  validateFormalStocktakePage: validatePage,
  validateFormalStocktakeDetail: validateDetail,
  validateFormalStocktakeWriteResult: validateWriteResult,
  confirmFormalStocktakeWrite: confirmWrite,
  confirmFormalStocktakePostProjection: confirmPostProjection,
  createFormalStocktakeIntentRegistry: createIntentRegistry,
  stocktakeIntentRetryState: retryState,
  fixedQuantityText,
  formalStocktakeLabels: {
    taskType: { full: '全盘', sample: '抽盘', ad_hoc: '临时盘点', personal: '个人自盘', termination: '离职盘点' },
    status: { draft: '草稿', issued: '已下发', frozen: '已冻结', counting: '盘点中', submitted: '已提交', region_review: '待区域复核', hq_review: '待总部复核', approved: '复核通过', recount_required: '要求复盘', posted: '已过账', closed: '已关闭', cancelled: '已取消' },
    difference: { missing: '盘亏', excess: '盘盈', wrong_location: '库位不符', wrong_condition: '成色不符', wrong_lot: '批次不符', wrong_serial: 'SN 不符' }
  }
}
