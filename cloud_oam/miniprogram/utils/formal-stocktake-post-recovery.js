// Read-only proof for a non-opening formal stocktake posting command.
// This module deliberately has no write path: it only validates the historical
// command-status payload and its exact projection in a fresh task detail.
const { validateFormalStocktakeDetail } = require('./formal-stocktake-contract')

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/

function fail(message = '日常盘点过账仍待只读核验，继续保持待核验') {
  const error = new Error(message)
  error.name = 'FormalStocktakePostRecoveryError'
  error.status = 409
  error.code = 'formal_stocktake_post_recovery_unconfirmed'
  throw error
}

function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== fields.length
    || fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) fail()
  return value
}
function uuid(value, name) {
  if (typeof value !== 'string' || !UUID.test(value)) fail(`${name}无效`)
  return value.toLowerCase()
}
function positive(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) fail(`${name}无效`)
  return value
}
function nonNegative(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${name}无效`)
  return value
}
function timestamp(value, name) {
  if (typeof value !== 'string' || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail(`${name}无效`)
  return value
}
function sameInstant(left, right) {
  return Date.parse(left) === Date.parse(right)
}

function validateFormalStocktakePostSentinel(value) {
  const row = exact(value, ['v', 'kind', 'task_id', 'expected_task_version', 'actor_person_id', 'actor_authorization_version', 'trace_request_id'])
  if (row.v !== 1 || row.kind !== 'formal_stocktake_post'
    || !Number.isSafeInteger(row.expected_task_version) || row.expected_task_version < 0
    || typeof row.trace_request_id !== 'string' || !TRACE.test(row.trace_request_id)) fail('过账恢复坐标无效')
  return Object.freeze({
    v: 1,
    kind: 'formal_stocktake_post',
    task_id: uuid(row.task_id, 'task_id'),
    expected_task_version: row.expected_task_version,
    actor_person_id: uuid(row.actor_person_id, 'actor_person_id'),
    actor_authorization_version: positive(row.actor_authorization_version, 'actor_authorization_version'),
    trace_request_id: row.trace_request_id,
  })
}

function validateFormalStocktakePostCommandStatus(value, original) {
  const sentinel = validateFormalStocktakePostSentinel(original)
  const row = exact(value, [
    'schema_version', 'task_id', 'actor_person_id', 'actor_authorization_version',
    'trace_request_id', 'operation', 'lookup_status', 'command',
  ])
  if (row.schema_version !== '1.0' || uuid(row.task_id, 'task_id') !== sentinel.task_id
    || uuid(row.actor_person_id, 'actor_person_id') !== sentinel.actor_person_id
    || positive(row.actor_authorization_version, 'actor_authorization_version') !== sentinel.actor_authorization_version
    || row.trace_request_id !== sentinel.trace_request_id || row.operation !== 'post_differences') fail()
  if (row.lookup_status === 'not_observed') {
    if (row.command !== null) fail()
    return Object.freeze({ ...sentinel, lookup_status: 'not_observed', command: null })
  }
  if (row.lookup_status !== 'confirmed') fail()
  const command = exact(row.command, [
    'completion_id', 'task_id', 'terminal_round_id', 'resulting_task_status', 'task_version',
    'scope_count', 'difference_count', 'accepted_difference_count', 'no_adjustment_count',
    'transaction_count', 'movement_count', 'total_quantity', 'first_ledger_cursor',
    'last_ledger_cursor', 'posted_at',
  ])
  const checked = Object.freeze({
    completion_id: uuid(command.completion_id, 'completion_id'),
    task_id: uuid(command.task_id, 'command.task_id'),
    terminal_round_id: uuid(command.terminal_round_id, 'terminal_round_id'),
    resulting_task_status: command.resulting_task_status,
    task_version: positive(command.task_version, 'task_version'),
    scope_count: positive(command.scope_count, 'scope_count'),
    difference_count: nonNegative(command.difference_count, 'difference_count'),
    accepted_difference_count: nonNegative(command.accepted_difference_count, 'accepted_difference_count'),
    no_adjustment_count: nonNegative(command.no_adjustment_count, 'no_adjustment_count'),
    transaction_count: nonNegative(command.transaction_count, 'transaction_count'),
    movement_count: nonNegative(command.movement_count, 'movement_count'),
    total_quantity: command.total_quantity,
    first_ledger_cursor: command.first_ledger_cursor === null ? null : positive(command.first_ledger_cursor, 'first_ledger_cursor'),
    last_ledger_cursor: command.last_ledger_cursor === null ? null : positive(command.last_ledger_cursor, 'last_ledger_cursor'),
    posted_at: timestamp(command.posted_at, 'posted_at'),
  })
  if (checked.task_id !== sentinel.task_id || checked.resulting_task_status !== 'posted'
    || !QUANTITY.test(checked.total_quantity)
    || checked.accepted_difference_count + checked.no_adjustment_count !== checked.difference_count
    || checked.movement_count !== checked.accepted_difference_count
    || (checked.transaction_count === 0
      ? checked.first_ledger_cursor !== null || checked.last_ledger_cursor !== null || checked.movement_count !== 0 || checked.total_quantity !== '0.000'
      : checked.first_ledger_cursor === null || checked.last_ledger_cursor === null
        || checked.last_ledger_cursor - checked.first_ledger_cursor + 1 !== checked.transaction_count
        || checked.movement_count <= 0 || checked.total_quantity === '0.000')
    || checked.task_version !== sentinel.expected_task_version + 1) fail('盘点过账历史命令数量或版本无效')
  return Object.freeze({ ...sentinel, lookup_status: 'confirmed', command: checked })
}

function validateFormalStocktakePostRecoveredProjection(value, sentinelValue, commandValue) {
  const sentinel = validateFormalStocktakePostSentinel(sentinelValue)
  const command = validateFormalStocktakePostCommandStatus({
    schema_version: '1.0', task_id: sentinel.task_id, actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id,
    operation: 'post_differences', lookup_status: 'confirmed', command: commandValue,
  }, sentinel).command
  const detail = validateFormalStocktakeDetail(value)
  if (detail.task_id !== sentinel.task_id || detail.version < command.task_version
    || detail.posted_at === null || !sameInstant(detail.posted_at, command.posted_at)) fail('当前盘点详情未承接原过账版本与时间')
  const round = detail.rounds.find((item) => item.round_id === command.terminal_round_id)
  if (!round || detail.scopes.length !== command.scope_count || detail.current_round_no !== round.round_no
    || detail.state_axes.posting_status !== 'recorded'
    || !['posted', 'closed'].includes(detail.status)
    || (detail.status === 'posted' && detail.closed_at !== null)
    || (detail.status === 'closed' && detail.closed_at === null)) fail('当前盘点详情未确认过账终态')
  const items = round.headquarters_review?.visible_items || []
  const differences = new Set(round.visible_differences.map((item) => item.difference_id))
  const reviewed = new Set(items.map((item) => item.difference_id))
  if (round.difference_completion?.visible_difference_count !== command.difference_count
    || round.difference_completion?.covers_all_task_scopes !== true
    || round.visible_differences.length !== command.difference_count
    || round.region_review?.decision !== 'approve' || round.region_review?.covers_all_task_scopes !== true
    || round.headquarters_review?.decision !== 'approve' || round.headquarters_review?.covers_all_task_scopes !== true
    || items.length !== command.difference_count || reviewed.size !== command.difference_count
    || [...differences].some((id) => !reviewed.has(id)) || [...reviewed].some((id) => !differences.has(id))
    || items.filter((item) => item.decision === 'accept_for_posting').length !== command.accepted_difference_count
    || items.filter((item) => item.decision === 'no_adjustment').length !== command.no_adjustment_count
    || round.posting.posting_fact_count !== command.movement_count
    || round.posting.inventory_transaction_count !== command.transaction_count
    || round.posting.visible_total_quantity !== command.total_quantity
    || round.posting.covers_all_task_scopes !== true
    || (command.movement_count > 0 && round.posting.status !== 'recorded')
    || (command.movement_count === 0 && round.posting.status !== 'not_posted')) fail('精确回读未确认独立盘点差异过账完成事实')
  return detail
}

module.exports = {
  validateFormalStocktakePostSentinel,
  validateFormalStocktakePostCommandStatus,
  validateFormalStocktakePostRecoveredProjection,
}
