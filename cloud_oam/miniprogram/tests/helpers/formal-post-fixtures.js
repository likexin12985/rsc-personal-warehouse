// Synthetic non-opening posting proof shared by the mini recovery tests.
const TASK = "10000000-0000-4000-8000-000000000001";
const PERSON = "01000000-0000-4000-8000-000000000001";
const TRACE = `web-${"b".repeat(36)}`;
const IDENTITY = { person_id: PERSON, authorization_version: 7 };
function closedDetail() {
  const axes = { count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", recount_status: "not_required", posting_status: "recorded", reconciliation_status: "recorded", closure_status: "closed" };
  return {
    schema_version: "1.0", task_id: TASK, task_no: "ST-001", task_type: "personal", region_org_id: "20000000-0000-4000-8000-000000000001", status: "closed", version: 8, blind_count: true,
    current_round_no: 1, cutoff_ledger_cursor: 10, cutoff_at: "2026-09-01T08:00:00+08:00", issued_at: "2026-09-01T07:00:00+08:00", frozen_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", posted_at: "2026-09-01T10:30:00+08:00", closed_at: "2026-09-01T11:00:00+08:00", cancelled_at: null, deadline: null, note: "", state_axes: axes,
    close_control: {
      latest_reconciliation: { completion_id: "a0000000-0000-4000-8000-000000000001", reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 7, reconciled_at: "2026-09-01T10:45:00+08:00" },
      close_completion: { completion_id: "b0000000-0000-4000-8000-000000000001", reconciliation_completion_id: "a0000000-0000-4000-8000-000000000001", closed_task_version: 8, closed_at: "2026-09-01T11:00:00+08:00" },
    },
    scopes: [{ scope_id: "30000000-0000-4000-8000-000000000001", scope_no: 1, scope_mode: "location_all", owner_org_id: "20000000-0000-4000-8000-000000000001", location_id: "40000000-0000-4000-8000-000000000001", custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "visible", snapshot_accounts: [], allowed_actions: [] }],
    rounds: [{
      round_id: "50000000-0000-4000-8000-000000000001", round_no: 1, round_type: "initial", status: "submitted", started_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true,
      difference_completion: { completion_id: "60000000-0000-4000-8000-000000000001", completed_at: "2026-09-01T09:15:00+08:00", visible_difference_count: 0, visible_pending_verification_count: 0, visible_total_affected_qty: "0.000", covers_all_task_scopes: true }, visible_differences: [],
      region_review: { review_id: "70000000-0000-4000-8000-000000000001", review_stage: "region", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: PERSON, reviewed_at: "2026-09-01T09:30:00+08:00", visible_items: [], covers_all_task_scopes: true },
      headquarters_review: { review_id: "80000000-0000-4000-8000-000000000001", review_stage: "headquarters", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: PERSON, reviewed_at: "2026-09-01T10:00:00+08:00", visible_items: [], covers_all_task_scopes: true }, recount_cause: null,
      posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: true, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: [],
    }], allowed_actions: [],
  };
}

function confirmedStatus() {
  return {
    schema_version: "1.0", task_id: TASK, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE, operation: "post_differences", lookup_status: "confirmed",
    command: { completion_id: "90000000-0000-4000-8000-000000000001", task_id: TASK, terminal_round_id: "50000000-0000-4000-8000-000000000001", resulting_task_status: "posted", task_version: 6, scope_count: 1, difference_count: 0, accepted_difference_count: 0, no_adjustment_count: 0, transaction_count: 0, movement_count: 0, total_quantity: "0.000", first_ledger_cursor: null, last_ledger_cursor: null, posted_at: "2026-09-01T10:30:00+08:00" },
  };
}


function postedDetail() {
  const detail = closedDetail()
  detail.status = 'posted'; detail.version = 6; detail.closed_at = null
  detail.state_axes.reconciliation_status = 'not_reconciled'; detail.state_axes.closure_status = 'open'
  detail.close_control = { latest_reconciliation: null, close_completion: null }
  detail.allowed_actions = ['reconcile']
  return detail
}
function approvedDetail() {
  const detail = postedDetail()
  detail.status = 'approved'; detail.version = 5; detail.posted_at = null
  detail.state_axes.posting_status = 'not_posted'; detail.allowed_actions = ['post']
  return detail
}
function marker() { return { v: 1, kind: 'formal_stocktake_post', task_id: TASK, expected_task_version: 5, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE } }
function coordinates() { return { 'Idempotency-Key': `webidem-${'a'.repeat(36)}`, 'X-Request-ID': TRACE } }
function intent() { return require('../../utils/formal-stocktake-contract').createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: 'post', taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } }) }
function identityResponse() { return { ...IDENTITY, name: '总部管理员', employee_no: 'HQ001', organization_code: 'HQ', organization_name: '总部', account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin'] } }
function access() { return { schema_version: '1.0', ...IDENTITY, can_read: true, can_post: true } }
function writeResult() { const { posted_at, ...command } = confirmedStatus().command; return { schema_version: '1.0', ...command, replayed: false } }
module.exports = { TASK, PERSON, TRACE, IDENTITY, closedDetail, postedDetail, approvedDetail, confirmedStatus, marker, intent, identityResponse, access, coordinates, writeResult }
