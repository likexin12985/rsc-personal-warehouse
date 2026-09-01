import { describe, expect, it, vi } from "vitest";

import { createFormalStocktakeAdapter } from "./formalStocktakeAdapter";
import { createFormalStocktakeIntentRegistry } from "./formalStocktakes";

const PERSON = "01000000-0000-4000-8000-000000000001";
const ASSIGNMENT = "02000000-0000-4000-8000-000000000001";
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
const ASSIGNEE_USER = "engineer-001";

function access(actions = ["read", "count"]) {
  return { person_id: PERSON, account_status: "active", employment_status: "active", authorization_version: 7, access_mode: "active", role_codes: ["technician"], assignments: [{ assignment_id: ASSIGNMENT, role_code: "technician", scope_type: "person", scope_id: PERSON, valid_from: "2026-01-01T00:00:00+08:00", valid_to: null }], permissions: actions.map((action) => ({ resource: "stocktake", action, field_code: "" })) };
}
function headquartersAccess(actions = ["read", "post_difference"]) {
  return { person_id: PERSON, account_status: "active", employment_status: "active", authorization_version: 7, access_mode: "active", role_codes: ["admin"], assignments: [{ assignment_id: ASSIGNMENT, role_code: "admin", scope_type: "national", scope_id: "*", valid_from: "2026-01-01T00:00:00+08:00", valid_to: null }], permissions: actions.map((action) => ({ resource: "stocktake", action, field_code: "" })) };
}
function axes() { return { count_status: "not_started", difference_status: "not_ready", region_review_status: "not_ready", headquarters_review_status: "not_ready", recount_status: "not_required", posting_status: "not_posted", reconciliation_status: "not_reconciled", closure_status: "open" }; }
function detail(allowed = ["start"]) { return { schema_version: "1.0", task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", region_org_id: REGION, status: "draft", version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: "", state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: "location_all", owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "not_started", snapshot_accounts: [], allowed_actions: [] }], rounds: [], allowed_actions: allowed }; }
function recountDetail() { return { ...detail([]), status: "recount_required", version: 5, current_round_no: 1, cutoff_ledger_cursor: 10, cutoff_at: "2026-09-01T08:00:00+08:00", issued_at: "2026-09-01T07:00:00+08:00", frozen_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", state_axes: { ...axes(), count_status: "submitted", difference_status: "evaluated", region_review_status: "recount", recount_status: "required" }, scopes: [{ ...detail([]).scopes[0], snapshot_visibility: "visible" }], rounds: [{ round_id: ROUND, round_no: 1, round_type: "initial", status: "submitted", started_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true, difference_completion: null, visible_differences: [], region_review: null, headquarters_review: null, recount_cause: null, posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: ["open_recount"] }] }; }
function postableDetail(posted = false) {
  return {
    ...detail(posted ? [] : ["post"]),
    status: posted ? "posted" : "approved",
    version: posted ? 6 : 5,
    current_round_no: 1,
    cutoff_ledger_cursor: 10,
    cutoff_at: "2026-09-01T08:00:00+08:00",
    issued_at: "2026-09-01T07:00:00+08:00",
    frozen_at: "2026-09-01T08:00:00+08:00",
    submitted_at: "2026-09-01T09:00:00+08:00",
    posted_at: posted ? "2026-09-01T10:30:00+08:00" : null,
    state_axes: { ...axes(), count_status: "submitted", difference_status: "evaluated", region_review_status: "approve", headquarters_review_status: "approve", posting_status: posted ? "recorded" : "not_posted" },
    scopes: [{ ...detail([]).scopes[0], snapshot_visibility: "visible" }],
    rounds: [{ round_id: ROUND, round_no: 1, round_type: "initial", status: "submitted", started_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true, difference_completion: { completion_id: DIFFERENCE_COMPLETION, completed_at: "2026-09-01T09:15:00+08:00", visible_difference_count: 0, visible_pending_verification_count: 0, visible_total_affected_qty: "0.000", covers_all_task_scopes: true }, visible_differences: [], region_review: { review_id: REGION_REVIEW, review_stage: "region", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: PERSON, reviewed_at: "2026-09-01T09:30:00+08:00", visible_items: [], covers_all_task_scopes: true }, headquarters_review: { review_id: HEADQUARTERS_REVIEW, review_stage: "headquarters", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: PERSON, reviewed_at: "2026-09-01T10:00:00+08:00", visible_items: [], covers_all_task_scopes: true }, recount_cause: null, posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: posted, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: [] }],
  };
}
function postResult() { return { schema_version: "1.0", completion_id: POSTING_COMPLETION, task_id: TASK, terminal_round_id: ROUND, resulting_task_status: "posted", task_version: 6, scope_count: 1, difference_count: 0, accepted_difference_count: 0, no_adjustment_count: 0, transaction_count: 0, movement_count: 0, total_quantity: "0.000", first_ledger_cursor: null, last_ledger_cursor: null, replayed: false }; }
function postedForReconcile() { return { ...postableDetail(true), allowed_actions: ["reconcile"] }; }
function reconciledDetail() { return { ...postableDetail(true), version: 7, state_axes: { ...postableDetail(true).state_axes, reconciliation_status: "recorded" }, close_control: { latest_reconciliation: { completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 7, reconciled_at: "2026-09-01T10:45:00+08:00" }, close_completion: null }, allowed_actions: ["reconcile", "close"] }; }
function closedDetail() { return { ...reconciledDetail(), status: "closed", version: 8, closed_at: "2026-09-01T11:00:00+08:00", state_axes: { ...reconciledDetail().state_axes, closure_status: "closed" }, close_control: { ...reconciledDetail().close_control, close_completion: { completion_id: CLOSE_COMPLETION, reconciliation_completion_id: RECONCILIATION_COMPLETION, closed_task_version: 8, closed_at: "2026-09-01T11:00:00+08:00" } }, allowed_actions: [] }; }
function reconciliationResult() { return { schema_version: "1.0", completion_id: RECONCILIATION_COMPLETION, task_id: TASK, posting_completion_id: POSTING_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, resulting_task_status: "posted", task_version: 7, scope_count: 1, account_count: 2, scoped_account_count: 1, serial_count: 0, transaction_count: 3, movement_count: 3, book_total_qty: "2.000", physical_total_qty: "2.000", reconciled_at: "2026-09-01T10:45:00+08:00", replayed: false }; }
function closeResult() { return { schema_version: "1.0", completion_id: CLOSE_COMPLETION, task_id: TASK, reconciliation_completion_id: RECONCILIATION_COMPLETION, reconciliation_no: 1, reconciliation_ledger_cursor: 20, resulting_task_status: "closed", task_version: 8, closed_at: "2026-09-01T11:00:00+08:00", replayed: false }; }
function coordinates() { return { "Idempotency-Key": `webidem-${"a".repeat(36)}`, "X-Request-ID": `web-${"b".repeat(36)}` }; }

describe("formal stocktake PC adapter", () => {
  it("projects only matching active stocktake grants", async () => {
    const requester = vi.fn(async (_path: string, _init?: RequestInit) => access());
    const projected = await createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).loadAccess();
    expect(projected).toEqual({ schema_version: "1.0", person_id: PERSON, authorization_version: 7, can_read: true, can_count: true, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: false, can_close: false });
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 8 }, requester).loadAccess()).rejects.toThrow(/授权版本/);
  });

  it("requires one national admin assignment and the dedicated posting permission", async () => {
    const allowed = vi.fn(async () => headquartersAccess());
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, allowed).loadAccess()).resolves.toMatchObject({ can_read: true, can_post: true });

    const missingPermission = vi.fn(async () => headquartersAccess(["read"]));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, missingPermission).loadAccess()).resolves.toMatchObject({ can_post: false });

    const nonNational = vi.fn(async () => ({ ...headquartersAccess(), assignments: [{ assignment_id: ASSIGNMENT, role_code: "admin", scope_type: "person", scope_id: PERSON, valid_from: "2026-01-01T00:00:00+08:00", valid_to: null }] }));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, nonNational).loadAccess()).resolves.toMatchObject({ can_post: false });
  });

  it("projects independent national reconcile and close permissions", async () => {
    const allowed = vi.fn(async () => headquartersAccess(["read", "reconcile", "close"]));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, allowed).loadAccess()).resolves.toMatchObject({ can_post: false, can_reconcile: true, can_close: true });
    const nonNational = vi.fn(async () => ({ ...headquartersAccess(["read", "reconcile", "close"]), assignments: [{ assignment_id: ASSIGNMENT, role_code: "admin", scope_type: "person", scope_id: PERSON, valid_from: "2026-01-01T00:00:00+08:00", valid_to: null }] }));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, nonNational).loadAccess()).resolves.toMatchObject({ can_reconcile: false, can_close: false });
  });

  it("uses canonical no-store reads and validates the response", async () => {
    const requester = vi.fn(async (path: string, _init?: RequestInit) => path === "/access/context" ? access() : path.includes("?") ? { schema_version: "1.0", items: [], next_after_id: null } : detail());
    const adapter = createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester);
    await adapter.list(null); await adapter.detail(TASK.toUpperCase());
    expect(requester.mock.calls.map((call) => call[0])).toEqual(["/v1/stocktakes?limit=50", `/v1/stocktakes/${TASK}`]);
    expect(requester.mock.calls[0][1]).toEqual({
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    });
  });

  it("validates both assignee identities from the authorization-bound option page", async () => {
    const requester = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path === "/access/context") return access(["read", "manage"]);
      if (path.startsWith("/v1/stocktake-options/assignees?")) return { schema_version: "1.0", region_org_id: REGION, location_id: LOCATION, items: [{ assignee_user_id: ASSIGNEE_USER, person_id: PERSON, name: "工程师", employee_no: "E001", role_codes: ["technician"] }], next_after_person_id: null, authorization_version: 7 };
      throw new Error(`unexpected path ${path}`);
    });
    const page = await createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).listAssignees(REGION, LOCATION, null);
    expect(page.items[0]).toMatchObject({ assignee_user_id: ASSIGNEE_USER, person_id: PERSON });
    expect(requester.mock.calls[0][1]).toEqual({
      cache: "no-store",
      headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
    });

    const malformed = vi.fn(async (_path: string, _init?: RequestInit): Promise<unknown> => ({ schema_version: "1.0", region_org_id: REGION, location_id: LOCATION, items: [{ person_id: PERSON, name: "工程师", employee_no: "E001", role_codes: ["technician"] }], next_after_person_id: null, authorization_version: 7 }));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, malformed).listAssignees(REGION, LOCATION, null)).rejects.toThrow(/精确包含正式字段/);
  });

  it("sends exact create intent then reloads identity and exact task detail", async () => {
    const requester = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/access/context") return access();
      if (init?.method === "POST") return { schema_version: "1.0", task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", status: "draft", task_version: 0, scope_count: 1, idempotency_replayed: false };
      return detail();
    });
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const intent = registry.begin({ action: "create_personal", body: { blind_count: true, freeze_mode: "cutoff_replay", note: "" } });
    const completed = await createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).execute(intent);
    expect(completed.detail.task_id).toBe(TASK);
    const write = requester.mock.calls.find((call) => call[1]?.method === "POST")!;
    expect(write[0]).toBe(intent.path);
    expect(write[1]?.headers).toBe(intent.headers);
    expect(write[1]?.body).toBe(JSON.stringify(intent.body));
    expect(requester.mock.calls.filter((call) => call[0] === "/access/context")).toHaveLength(2);
  });

  it("requires both permission and detail allowed_actions before transport", async () => {
    const requester = vi.fn(async (path: string, _init?: RequestInit) => path === "/access/context" ? access() : detail([]));
    const registry = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates });
    const intent = registry.begin({ action: "start", taskId: TASK, expectedTaskVersion: 0, body: { expected_version: 0 } });
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).execute(intent)).rejects.toThrow(/allowed_actions/);
    expect(requester.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
  });

  it("posts only after both HQ gates, reuses exact coordinates, and confirms the posted reread", async () => {
    let detailReads = 0;
    const requester = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/access/context") return headquartersAccess();
      if (path === `/v1/stocktakes/${TASK}`) {
        detailReads += 1;
        return postableDetail(detailReads > 1);
      }
      if (path === `/v1/stocktakes/${TASK}/post-differences` && init?.method === "POST") return postResult();
      throw new Error(`unexpected path ${path}`);
    });
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "post", taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } });
    const completed = await createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).execute(intent);
    expect(completed.detail.status).toBe("posted");
    const writes = requester.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe(intent.path);
    expect(writes[0][1]?.headers).toBe(intent.headers);
    expect(writes[0][1]?.body).toBe(JSON.stringify({ expected_task_version: 5 }));
    expect(requester.mock.calls.filter((call) => call[0] === "/access/context")).toHaveLength(2);
  });

  it("does not transport posting when either the HQ permission or task allowed_action is absent", async () => {
    const permissionDenied = vi.fn(async (path: string, _init?: RequestInit) => path === "/access/context" ? headquartersAccess(["read"]) : postableDetail());
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "post", taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } });
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, permissionDenied).execute(intent)).rejects.toThrow(/权限/);
    expect(permissionDenied.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);

    const actionDenied = vi.fn(async (path: string, _init?: RequestInit) => path === "/access/context" ? headquartersAccess() : postableDetail(true));
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, actionDenied).execute(intent)).rejects.toThrow(/allowed_actions/);
    expect(actionDenied.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
  });

  it("rereads first after an uncertain posting result and retries only the same intent coordinates", async () => {
    let detailReads = 0;
    let writes = 0;
    const events: string[] = [];
    const requester = vi.fn(async (path: string, init?: RequestInit) => {
      events.push(`${init?.method ?? "GET"} ${path}`);
      if (path === "/access/context") return headquartersAccess();
      if (path === `/v1/stocktakes/${TASK}`) {
        detailReads += 1;
        return postableDetail(detailReads >= 4);
      }
      if (path === `/v1/stocktakes/${TASK}/post-differences` && init?.method === "POST") {
        writes += 1;
        if (writes === 1) throw new Error("transport outcome unknown");
        return postResult();
      }
      throw new Error(`unexpected path ${path}`);
    });
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "post", taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } });
    const client = createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester);

    await expect(client.execute(intent)).rejects.toMatchObject({ write_result_uncertain: true, stocktake_retry_state: "retryable" });
    expect(events.slice(0, 4)).toEqual(["GET /access/context", `GET /v1/stocktakes/${TASK}`, `POST /v1/stocktakes/${TASK}/post-differences`, `GET /v1/stocktakes/${TASK}`]);
    await expect(client.execute(intent)).resolves.toMatchObject({ detail: { status: "posted", version: 6 } });
    const postCalls = requester.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(postCalls).toHaveLength(2);
    expect(postCalls[0][1]?.headers).toBe(intent.headers);
    expect(postCalls[1][1]?.headers).toBe(intent.headers);
    expect(postCalls[0][1]?.body).toBe(postCalls[1][1]?.body);
  });

  it("transports reconcile and close separately and confirms each persisted completion", async () => {
    let state: "posted" | "reconciled" | "closed" = "posted";
    const requester = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
      if (path === "/access/context") return headquartersAccess(["read", "reconcile", "close"]);
      if (path === `/v1/stocktakes/${TASK}`) return state === "posted" ? postedForReconcile() : state === "reconciled" ? reconciledDetail() : closedDetail();
      if (path === `/v1/stocktakes/${TASK}/reconcile` && init?.method === "POST") { state = "reconciled"; return reconciliationResult(); }
      if (path === `/v1/stocktakes/${TASK}/close` && init?.method === "POST") { state = "closed"; return closeResult(); }
      throw new Error(`unexpected path ${path}`);
    });
    const client = createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester);
    const reconcile = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "reconcile", taskId: TASK, expectedTaskVersion: 6, body: { expected_task_version: 6 } });
    await expect(client.execute(reconcile)).resolves.toMatchObject({ detail: { status: "posted", version: 7, state_axes: { reconciliation_status: "recorded", closure_status: "open" } } });
    const close = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "close", taskId: TASK, expectedTaskVersion: 7, body: { expected_task_version: 7 } });
    await expect(client.execute(close)).resolves.toMatchObject({ detail: { status: "closed", version: 8, state_axes: { closure_status: "closed" } } });
    expect(requester.mock.calls.filter((call) => call[1]?.method === "POST").map((call) => call[0])).toEqual([`/v1/stocktakes/${TASK}/reconcile`, `/v1/stocktakes/${TASK}/close`]);
  });

  it("rereads terminal completion after a lost response and replays only the same reconcile coordinates", async () => {
    let reconciled = false;
    let writes = 0;
    const requester = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
      if (path === "/access/context") return headquartersAccess(["read", "reconcile"]);
      if (path === `/v1/stocktakes/${TASK}`) return reconciled ? reconciledDetail() : postedForReconcile();
      if (path === `/v1/stocktakes/${TASK}/reconcile` && init?.method === "POST") {
        writes += 1;
        reconciled = true;
        if (writes === 1) throw new Error("response lost after commit");
        return { ...reconciliationResult(), replayed: true };
      }
      throw new Error(`unexpected path ${path}`);
    });
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "reconcile", taskId: TASK, expectedTaskVersion: 6, body: { expected_task_version: 6 } });
    const client = createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester);

    await expect(client.execute(intent)).rejects.toMatchObject({ write_result_uncertain: true, stocktake_retry_state: "retryable" });
    await expect(client.execute(intent)).resolves.toMatchObject({ result: { replayed: true }, detail: { version: 7 } });
    const postCalls = requester.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(postCalls).toHaveLength(2);
    expect(postCalls[0][1]?.headers).toBe(intent.headers);
    expect(postCalls[1][1]?.headers).toBe(intent.headers);
    expect(postCalls[0][1]?.body).toBe(postCalls[1][1]?.body);
  });

  it("rereads terminal completion after a lost response and replays only the same close coordinates", async () => {
    let closed = false;
    let writes = 0;
    const requester = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
      if (path === "/access/context") return headquartersAccess(["read", "close"]);
      if (path === `/v1/stocktakes/${TASK}`) return closed ? closedDetail() : reconciledDetail();
      if (path === `/v1/stocktakes/${TASK}/close` && init?.method === "POST") {
        writes += 1;
        closed = true;
        if (writes === 1) throw new Error("response lost after commit");
        return { ...closeResult(), replayed: true };
      }
      throw new Error(`unexpected path ${path}`);
    });
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "close", taskId: TASK, expectedTaskVersion: 7, body: { expected_task_version: 7 } });
    const client = createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester);

    await expect(client.execute(intent)).rejects.toMatchObject({ write_result_uncertain: true, stocktake_retry_state: "retryable" });
    await expect(client.execute(intent)).resolves.toMatchObject({ result: { replayed: true }, detail: { status: "closed", version: 8 } });
    const postCalls = requester.mock.calls.filter((call) => call[1]?.method === "POST");
    expect(postCalls).toHaveLength(2);
    expect(postCalls[0][1]?.headers).toBe(intent.headers);
    expect(postCalls[1][1]?.headers).toBe(intent.headers);
    expect(postCalls[0][1]?.body).toBe(postCalls[1][1]?.body);
  });

  it("rejects injected legacy or opening coordinates before any request", async () => {
    const requester = vi.fn();
    const forged = { action: "create_personal", method: "POST", path: "/stocktakes", body: {}, headers: coordinates(), taskId: null, roundId: null, scopeId: null, expectedTaskVersion: null, signature: "forged" } as any;
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).execute(forged)).rejects.toThrow(/路径或坐标/);
    expect(requester).not.toHaveBeenCalled();
  });

  it("fails before POST when a forged recount user is absent from the fresh scope directory", async () => {
    const requester = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
      if (path === "/access/context") return access(["read", "manage"]);
      if (path === `/v1/stocktakes/${TASK}`) return recountDetail();
      if (path.startsWith("/v1/stocktake-options/assignees?")) return { schema_version: "1.0", region_org_id: REGION, location_id: LOCATION, items: [{ assignee_user_id: "engineer-002", person_id: PERSON, name: "其他工程师", employee_no: "E002", role_codes: ["technician"] }], next_after_person_id: null, authorization_version: 7 };
      if (init?.method === "POST") throw new Error("POST must not be reached");
      throw new Error(`unexpected path ${path}`);
    });
    const intent = createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({ action: "open_recount", taskId: TASK, roundId: ROUND, expectedTaskVersion: 5, body: { expected_task_version: 5, assignments: [{ scope_id: SCOPE, assignee_user_id: ASSIGNEE_USER }], reason: "区域复核要求复盘" } });
    await expect(createFormalStocktakeAdapter({ person_id: PERSON, authorization_version: 7 }, requester).execute(intent)).rejects.toThrow(/不在当前范围正式受控目录/);
    expect(requester.mock.calls.some((call) => call[1]?.method === "POST")).toBe(false);
  });
});
