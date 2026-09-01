import { describe, expect, it } from "vitest";

import {
  confirmFormalStocktakeWrite,
  createFormalStocktakeIntentRegistry,
  fixedQuantityText,
  stocktakeIntentRetryState,
  validateFormalStocktakeDetail,
  validateFormalStocktakePage,
  validateFormalStocktakeWriteResult,
} from "./formalStocktakes";

const TASK = "10000000-0000-4000-8000-000000000001";
const REGION = "20000000-0000-4000-8000-000000000001";
const SCOPE = "30000000-0000-4000-8000-000000000001";
const LOCATION = "40000000-0000-4000-8000-000000000001";
const ROUND = "50000000-0000-4000-8000-000000000001";
const DIFFERENCE_COMPLETION = "60000000-0000-4000-8000-000000000001";
const REGION_REVIEW = "70000000-0000-4000-8000-000000000001";
const HEADQUARTERS_REVIEW = "80000000-0000-4000-8000-000000000001";
const POSTING_COMPLETION = "90000000-0000-4000-8000-000000000001";
const RECONCILIATION_COMPLETION = "a0000000-0000-4000-8000-000000000001";
const CLOSE_COMPLETION = "b0000000-0000-4000-8000-000000000001";

function axes(overrides: Record<string, unknown> = {}) {
  return { count_status: "not_started", difference_status: "not_ready", region_review_status: "not_ready", headquarters_review_status: "not_ready", recount_status: "not_required", posting_status: "not_posted", reconciliation_status: "not_reconciled", closure_status: "open", ...overrides };
}

function detail(overrides: Record<string, unknown> = {}) {
  return { schema_version: "1.0", task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", region_org_id: REGION, status: "draft", version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: "", state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: "location_all", owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: null, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "not_started", snapshot_accounts: [], allowed_actions: [] }], rounds: [], allowed_actions: ["start"], ...overrides };
}

function page() {
  return { schema_version: "1.0", items: [{ task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", region_org_id: REGION, status: "draft", version: 0, blind_count: true, current_round_no: 0, current_round_status: null, cutoff_ledger_cursor: null, cutoff_at: null, visible_scope_count: 1, current_round_visible_completed_scope_count: 0, freeze_status: "not_started", state_axes: axes(), deadline: null, allowed_actions: ["start"] }], next_after_id: null };
}

function coordinates() {
  return { "Idempotency-Key": `webidem-${"a".repeat(36)}`, "X-Request-ID": `web-${"b".repeat(36)}` };
}

function postableDetail(posted = false) {
  const reviewedAt = "2026-09-01T10:00:00+08:00";
  return detail({
    status: posted ? "posted" : "approved",
    version: posted ? 6 : 5,
    current_round_no: 1,
    cutoff_ledger_cursor: 10,
    cutoff_at: "2026-09-01T08:00:00+08:00",
    issued_at: "2026-09-01T07:00:00+08:00",
    frozen_at: "2026-09-01T08:00:00+08:00",
    submitted_at: "2026-09-01T09:00:00+08:00",
    posted_at: posted ? "2026-09-01T10:30:00+08:00" : null,
    state_axes: axes({ count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", posting_status: posted ? "recorded" : "not_posted" }),
    scopes: [{ ...detail().scopes[0], snapshot_visibility: "visible", allowed_actions: [] }],
    rounds: [{
      round_id: ROUND,
      round_no: 1,
      round_type: "initial",
      status: "submitted",
      started_at: "2026-09-01T08:00:00+08:00",
      submitted_at: "2026-09-01T09:00:00+08:00",
      submission: null,
      visible_scope_completions: [],
      visible_count_lines: [],
      visible_observations: [],
      differences_visible: true,
      difference_completion: { completion_id: DIFFERENCE_COMPLETION, completed_at: "2026-09-01T09:15:00+08:00", visible_difference_count: 0, visible_pending_verification_count: 0, visible_total_affected_qty: "0.000", covers_all_task_scopes: true },
      visible_differences: [],
      region_review: { review_id: REGION_REVIEW, review_stage: "region", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: REGION, reviewed_at: reviewedAt, visible_items: [], covers_all_task_scopes: true },
      headquarters_review: { review_id: HEADQUARTERS_REVIEW, review_stage: "headquarters", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: REGION, reviewed_at: reviewedAt, visible_items: [], covers_all_task_scopes: true },
      recount_cause: null,
      posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: posted, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null },
      allowed_actions: [],
    }],
    allowed_actions: posted ? [] : ["post"],
  });
}

function zeroDifferencePostResult() {
  return {
    schema_version: "1.0",
    completion_id: POSTING_COMPLETION,
    task_id: TASK,
    terminal_round_id: ROUND,
    resulting_task_status: "posted",
    task_version: 6,
    scope_count: 1,
    difference_count: 0,
    accepted_difference_count: 0,
    no_adjustment_count: 0,
    transaction_count: 0,
    movement_count: 0,
    total_quantity: "0.000",
    first_ledger_cursor: null,
    last_ledger_cursor: null,
    replayed: false,
  };
}

function reconciledDetail() {
  return {
    ...postableDetail(true),
    version: 7,
    state_axes: axes({ count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", posting_status: "recorded", reconciliation_status: "recorded" }),
    close_control: {
      latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 7, reconciled_at: "2026-09-01T10:45:00+08:00" },
      close_completion: null,
    },
    allowed_actions: ["reconcile", "close"],
  };
}

function reconciliationResult() {
  return { schema_version: "1.0", completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, resulting_task_status: "posted", task_version: 7, scope_count: 1, account_count: 2, scoped_account_count: 1, serial_count: 0, transaction_count: 3, movement_count: 3, book_total_qty: "2.000", physical_total_qty: "2.000", reconciled_at: "2026-09-01T10:45:00+08:00", replayed: false };
}

function closedDetail() {
  return {
    ...reconciledDetail(),
    status: "closed",
    version: 8,
    closed_at: "2026-09-01T11:00:00+08:00",
    state_axes: axes({ count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", posting_status: "recorded", reconciliation_status: "recorded", closure_status: "closed" }),
    close_control: {
      ...reconciledDetail().close_control,
      close_completion: { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 8, closed_at: "2026-09-01T11:00:00+08:00" },
    },
    allowed_actions: [],
  };
}

function closeResult() {
  return { schema_version: "1.0", completion_id: CLOSE_COMPLETION, task_id: TASK, reconciliation_completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, resulting_task_status: "closed", task_version: 8, closed_at: "2026-09-01T11:00:00+08:00", replayed: false };
}

describe("formal non-opening stocktake contract", () => {
  it("strictly validates list/detail and keeps posted separate from closed", () => {
    expect(validateFormalStocktakePage(page()).items[0].version).toBe(0);
    const posted = validateFormalStocktakeDetail(detail({ status: "posted", version: 8, posted_at: "2026-09-01T09:00:00+08:00", state_axes: axes({ posting_status: "recorded" }), allowed_actions: [] }));
    expect(posted.status).toBe("posted");
    expect(posted.closed_at).toBeNull();
    expect(() => validateFormalStocktakePage({ ...page(), legacy: true })).toThrow(/精确包含正式字段/);
  });

  it("rejects terminal-axis and action drift in list summaries", () => {
    const base = page();
    const row = base.items[0];
    expect(() => validateFormalStocktakePage({ ...base, items: [{ ...row, status: "closed", state_axes: axes({ posting_status: "recorded", reconciliation_status: "stale", closure_status: "closed" }), allowed_actions: [] }] })).toThrow(/完整终态/);
    expect(() => validateFormalStocktakePage({ ...base, items: [{ ...row, status: "draft", allowed_actions: ["close"] }] })).toThrow(/非终态盘点摘要/);
    expect(() => validateFormalStocktakePage({ ...base, items: [{ ...row, status: "posted", state_axes: axes({ posting_status: "recorded" }), allowed_actions: ["close"] }] })).toThrow(/当前有效内部对账/);
    expect(() => validateFormalStocktakePage({ ...base, items: [{ ...row, status: "posted", state_axes: axes({ posting_status: "recorded" }), allowed_actions: ["start"] }] })).toThrow(/只能开放对账或关闭/);
  });

  it("normalizes every submitted quantity to fixed 18,3-compatible text", () => {
    expect(fixedQuantityText("12.3")).toBe("12.300");
    expect(fixedQuantityText("0")).toBe("0.000");
    expect(() => fixedQuantityText("1e3")).toThrow(/非指数/);
    expect(() => fixedQuantityText("0", true)).toThrow(/大于零/);
    expect(() => fixedQuantityText(1 as unknown as string)).toThrow(/文本/);
  });

  it("reuses one unresolved write intent and blocks new coordinates", () => {
    let calls = 0;
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: () => { calls += 1; return coordinates(); } });
    const input = { action: "create_personal" as const, body: { blind_count: true, freeze_mode: "cutoff_replay" as const, note: "" } };
    const first = registry.begin(input);
    expect(registry.begin(input)).toBe(first);
    expect(calls).toBe(1);
    expect(first.path).toBe("/v1/stocktakes/personal");
    expect(stocktakeIntentRetryState(first, null)).toBe("retryable");
    expect(Object.isFrozen(first.body)).toBe(true);
    expect(() => registry.begin({ action: "create_personal", body: { blind_count: false, freeze_mode: "hard", note: "" } })).toThrow(/禁止生成新写坐标/);
  });

  it("matches the API's exact idempotency and request coordinate boundaries", () => {
    const input = { action: "create_personal" as const, body: { blind_count: true, freeze_mode: "cutoff_replay" as const, note: "" } };
    expect(() => createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": "a".repeat(15), "X-Request-ID": "b".repeat(8) }) }).begin(input)).toThrow(/坐标/);
    expect(() => createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": "a".repeat(129), "X-Request-ID": "b".repeat(8) }) }).begin(input)).toThrow(/坐标/);
    expect(() => createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": "a".repeat(16), "X-Request-ID": "b".repeat(7) }) }).begin(input)).toThrow(/坐标/);
    expect(() => createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": "a".repeat(16), "X-Request-ID": "b".repeat(161) }) }).begin(input)).toThrow(/坐标/);
    expect(createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": "a".repeat(16), "X-Request-ID": "b".repeat(8) }) }).begin(input).headers).toEqual({ "Idempotency-Key": "a".repeat(16), "X-Request-ID": "b".repeat(8) });
  });

  it("builds only the canonical count path and freezes fixed-decimal physical facts", () => {
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const round = "50000000-0000-4000-8000-000000000001";
    const intent = registry.begin({ action: "submit_initial_count", taskId: TASK, roundId: round, scopeId: SCOPE, expectedTaskVersion: 1, body: { count_mode: "blind", account_counts: [], physical_observations: [{ material_id: null, material_identifier_raw: "SKU-001", material_identifier_type: "sku_code", condition_code: "new", availability_bucket: "available", counted_qty: "2.5", lot_id: null, lot_no_raw: null, serial_id: null, serial_no_raw: null, serial_identifier_type: null, count_method: "manual", reason_code: null, remark: "" }], evidence_file_ids: [], zero_confirmed: false } });
    expect(intent.path).toBe(`/v1/stocktakes/${TASK}/rounds/${round}/scopes/${SCOPE}/initial-count`);
    expect((intent.body.physical_observations as Array<{ counted_qty: string }>)[0].counted_qty).toBe("2.500");
    expect(intent.path).not.toContain("opening");
  });

  it("requires exact create response and exact task reread before completion", () => {
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const intent = registry.begin({ action: "create_personal", body: { blind_count: true, freeze_mode: "cutoff_replay", note: "" } });
    const result = validateFormalStocktakeWriteResult(intent, { schema_version: "1.0", task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", status: "draft", task_version: 0, scope_count: 1, idempotency_replayed: false });
    expect(() => confirmFormalStocktakeWrite(intent, result, validateFormalStocktakeDetail(detail()))).not.toThrow();
    expect(() => confirmFormalStocktakeWrite(intent, result, validateFormalStocktakeDetail(detail({ task_no: "ST-OTHER" })))).toThrow(/精确回读/);
  });

  it("keeps a controlled recount assignee_user_id distinct from person_id", () => {
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const intent = registry.begin({ action: "open_recount", taskId: TASK, roundId: ROUND, expectedTaskVersion: 5, body: { expected_task_version: 5, assignments: [{ scope_id: SCOPE, assignee_user_id: "engineer-001" }], reason: "区域复核要求复盘" } });
    expect(intent.path).toBe(`/v1/stocktakes/${TASK}/rounds/${ROUND}/recount`);
    expect(intent.body.assignments).toEqual([{ scope_id: SCOPE, assignee_user_id: "engineer-001" }]);
    expect(JSON.stringify(intent.body)).not.toContain("assignee_person_id");
  });

  it("builds one immutable approved-to-posted intent and confirms the exact zero-difference completion", () => {
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const input = { action: "post" as const, taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } };
    const intent = registry.begin(input);
    expect(registry.begin(input)).toBe(intent);
    expect(intent.path).toBe(`/v1/stocktakes/${TASK}/post-differences`);
    expect(intent.body).toEqual({ expected_task_version: 5 });
    expect(intent.path).not.toContain("opening");
    expect(intent.path).not.toContain("close");
    expect(stocktakeIntentRetryState(intent, validateFormalStocktakeDetail(postableDetail()))).toBe("retryable");

    const result = validateFormalStocktakeWriteResult(intent, zeroDifferencePostResult());
    expect(() => confirmFormalStocktakeWrite(intent, result, validateFormalStocktakeDetail(postableDetail(true)))).not.toThrow();
    expect(stocktakeIntentRetryState(intent, validateFormalStocktakeDetail(postableDetail(true)))).toBe("handoff_required");
  });

  it("rejects posting completion count, cursor, version and close-state mismatches", () => {
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "post", taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } });
    expect(() => validateFormalStocktakeWriteResult(intent, { ...zeroDifferencePostResult(), task_version: 7 })).toThrow(/版本/);
    expect(() => validateFormalStocktakeWriteResult(intent, { ...zeroDifferencePostResult(), difference_count: 1 })).toThrow(/数量不守恒/);
    expect(() => validateFormalStocktakeWriteResult(intent, { ...zeroDifferencePostResult(), difference_count: 1, accepted_difference_count: 1, transaction_count: 2, movement_count: 1, total_quantity: "1.000", first_ledger_cursor: 20, last_ledger_cursor: 20 })).toThrow(/流水游标/);

    const result = validateFormalStocktakeWriteResult(intent, zeroDifferencePostResult());
    const reconciliationId = "a0000000-0000-4000-8000-000000000001";
    const closeId = "b0000000-0000-4000-8000-000000000001";
    const closed = validateFormalStocktakeDetail({
      ...postableDetail(true),
      status: "closed",
      version: 8,
      closed_at: "2026-09-01T11:00:00+08:00",
      state_axes: axes({ count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", posting_status: "recorded", reconciliation_status: "recorded", closure_status: "closed" }),
      close_control: {
        latest_reconciliation: { completion_id: reconciliationId, reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 7, reconciled_at: "2026-09-01T10:45:00+08:00" },
        close_completion: { completion_id: closeId, reconciliation_completion_id: reconciliationId, closed_task_version: 8, closed_at: "2026-09-01T11:00:00+08:00" },
      },
    });
    expect(() => confirmFormalStocktakeWrite(intent, result, closed)).toThrow(/精确回读/);
  });

  it("keeps reconcile and close as separate versioned intents with exact completion rereads", () => {
    const reconcileRegistry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const reconcile = reconcileRegistry.begin({ action: "reconcile", taskId: TASK, expectedTaskVersion: 6, body: { expected_task_version: 6 } });
    expect(reconcile.path).toBe(`/v1/stocktakes/${TASK}/reconcile`);
    expect(reconcile.path).not.toContain("close");
    const reconcileResult = validateFormalStocktakeWriteResult(reconcile, reconciliationResult());
    expect(() => confirmFormalStocktakeWrite(reconcile, reconcileResult, validateFormalStocktakeDetail(reconciledDetail()))).not.toThrow();
    expect(stocktakeIntentRetryState(reconcile, validateFormalStocktakeDetail(reconciledDetail()))).toBe("retryable");

    const closeRegistry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const close = closeRegistry.begin({ action: "close", taskId: TASK, expectedTaskVersion: 7, body: { expected_task_version: 7 } });
    expect(close.path).toBe(`/v1/stocktakes/${TASK}/close`);
    const accepted = validateFormalStocktakeWriteResult(close, closeResult());
    expect(() => confirmFormalStocktakeWrite(close, accepted, validateFormalStocktakeDetail(closedDetail()))).not.toThrow();
    expect(stocktakeIntentRetryState(close, validateFormalStocktakeDetail(closedDetail()))).toBe("retryable");
  });

  it("fails closed on reconciliation totals, completion anchors and stale retry state", () => {
    const reconcile = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "reconcile", taskId: TASK, expectedTaskVersion: 6, body: { expected_task_version: 6 } });
    expect(() => validateFormalStocktakeWriteResult(reconcile, { ...reconciliationResult(), physical_total_qty: "1.000" })).toThrow(/账物总量/);
    const accepted = validateFormalStocktakeWriteResult(reconcile, reconciliationResult());
    expect(() => confirmFormalStocktakeWrite(reconcile, accepted, validateFormalStocktakeDetail({ ...reconciledDetail(), close_control: { ...reconciledDetail().close_control, latest_reconciliation: { ...reconciledDetail().close_control.latest_reconciliation, completion_id: "a0000000-0000-4000-8000-000000000002" } } }))).toThrow(/内部对账完成事实/);
    const stale = validateFormalStocktakeDetail({ ...reconciledDetail(), state_axes: { ...reconciledDetail().state_axes, reconciliation_status: "stale" }, allowed_actions: ["reconcile"] });
    const close = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "close", taskId: TASK, expectedTaskVersion: 7, body: { expected_task_version: 7 } });
    expect(stocktakeIntentRetryState(close, stale)).toBe("handoff_required");
    expect(() => validateFormalStocktakeDetail({ ...reconciledDetail(), state_axes: { ...reconciledDetail().state_axes, posting_status: "not_posted" } })).toThrow(/独立过账完成事实/);
    expect(() => validateFormalStocktakeDetail({ ...closedDetail(), close_control: { ...closedDetail().close_control, close_completion: { ...closedDetail().close_control.close_completion, reconciliation_completion_id: "a0000000-0000-4000-8000-000000000002" } } })).toThrow(/最新内部对账/);
  });

  it("rejects terminal facts on pre-post states and any residual action on closed tasks", () => {
    expect(() => validateFormalStocktakeDetail({ ...closedDetail(), state_axes: { ...closedDetail().state_axes, reconciliation_status: "stale" } })).toThrow(/当前对账事实/);
    expect(() => validateFormalStocktakeDetail({ ...detail(), version: 1, state_axes: axes({ reconciliation_status: "recorded" }), close_control: { latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 1, reconciled_at: "2026-09-01T10:45:00+08:00" }, close_completion: null } })).toThrow(/非终态盘点/);
    expect(() => validateFormalStocktakeDetail({ ...closedDetail(), allowed_actions: ["reconcile"] })).toThrow(/不得再暴露写动作/);
    expect(() => validateFormalStocktakeDetail({ ...closedDetail(), scopes: [{ ...closedDetail().scopes[0], allowed_actions: ["submit_initial_count"] }] })).toThrow(/不得再暴露写动作/);
    expect(() => validateFormalStocktakeDetail({ ...reconciledDetail(), allowed_actions: ["start"] })).toThrow(/只能开放独立对账或关闭/);
    expect(() => validateFormalStocktakeDetail({ ...reconciledDetail(), posted_at: null })).toThrow(/过账状态、时间/);
    expect(() => validateFormalStocktakeDetail({ ...closedDetail(), close_control: { ...closedDetail().close_control, close_completion: { ...closedDetail().close_control.close_completion, closed_at: "2026-09-01T10:40:00+08:00" } }, closed_at: "2026-09-01T10:40:00+08:00" })).toThrow(/晚于内部对账/);
  });
});
