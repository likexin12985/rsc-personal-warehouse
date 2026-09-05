import { describe, expect, it, vi } from "vitest";

import { createFormalStocktakeIntentRegistry } from "./formalStocktakes";
import {
  FormalStocktakePostSubmissionPendingError,
  recoverFormalStocktakePost,
  submitDurableFormalStocktakePost,
  validateFormalStocktakePostCommandStatus,
  validateFormalStocktakePostRecoveredProjection,
} from "./formalStocktakePostRecovery";
import {
  createFormalStocktakePostRecoveryStore,
  validateFormalStocktakePostSentinel,
  type FormalStocktakePostLockManager,
  type FormalStocktakePostSentinel,
} from "./formalStocktakePostRecoveryStore";

const TASK = "10000000-0000-4000-8000-000000000001";
const PERSON = "01000000-0000-4000-8000-000000000001";
const TRACE = `web-${"b".repeat(36)}`;
const IDENTITY = { person_id: PERSON, authorization_version: 7 } as const;

class MemoryStorage {
  readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

const locks: FormalStocktakePostLockManager = {
  async request(_name, _options, callback) { return callback({}); },
};

const access = {
  schema_version: "1.0" as const,
  person_id: PERSON,
  authorization_version: 7,
  can_read: true,
  can_count: false,
  can_manage: false,
  can_review_region: false,
  can_review_headquarters: false,
  can_post: true,
  can_reconcile: false,
  can_close: false,
};

function identityResponse() {
  return {
    person_id: PERSON,
    name: "总部管理员",
    employee_no: "HQ001",
    organization_code: "HQ",
    organization_name: "蔚来总部",
    account_status: "active",
    employment_status: "active",
    access_mode: "active",
    authorization_version: 7,
    role_codes: ["admin"],
  };
}

function coordinates() {
  return { "Idempotency-Key": `webidem-${"a".repeat(36)}`, "X-Request-ID": TRACE };
}

function intent() {
  return createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({
    action: "post",
    taskId: TASK,
    expectedTaskVersion: 5,
    body: { expected_task_version: 5 },
  });
}

function sentinel(): FormalStocktakePostSentinel {
  return validateFormalStocktakePostSentinel({
    v: 1,
    kind: "formal_stocktake_post",
    task_id: TASK,
    expected_task_version: 5,
    actor_person_id: PERSON,
    actor_authorization_version: 7,
    trace_request_id: TRACE,
  });
}

function status(value: "not_observed" | "confirmed" = "not_observed") {
  return value === "not_observed"
    ? { schema_version: "1.0", task_id: TASK, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE, operation: "post_differences", lookup_status: "not_observed", command: null }
    : { schema_version: "1.0", task_id: TASK, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE, operation: "post_differences", lookup_status: "confirmed", command: null };
}

function recoveryAdapter(overrides: Record<string, unknown> = {}) {
  return {
    loadAccess: vi.fn(async () => access),
    loadAccessNoReplay: vi.fn(async () => access),
    loadIdentityNoReplay: vi.fn(async () => identityResponse()),
    detail: vi.fn(async () => { throw new Error("detail must not be read for not_observed"); }),
    detailNoReplay: vi.fn(async () => { throw new Error("detail must not be read for not_observed"); }),
    postingCommandStatus: vi.fn(async () => status()),
    execute: vi.fn(async (_intent: unknown, options?: { beforeWrite?: (context: unknown) => Promise<void> }) => {
      await options?.beforeWrite?.({});
      throw new Error("response lost after commit");
    }),
    ...overrides,
  } as any;
}

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

describe("formal daily stocktake post durable recovery", () => {
  it("validates exact status anchors and rejects a replay-shaped confirmed response", () => {
    expect(validateFormalStocktakePostCommandStatus(status(), sentinel())).toMatchObject({ lookup_status: "not_observed", command: null });
    expect(() => validateFormalStocktakePostCommandStatus({ ...status(), operation: "post" }, sentinel())).toThrow();
    expect(() => validateFormalStocktakePostCommandStatus({ ...status("confirmed"), command: {} }, sentinel())).toThrow();
  });

  it("persists only public coordinates and keeps not_observed sticky without detail or POST replay", async () => {
    const storage = new MemoryStorage();
    const store = createFormalStocktakePostRecoveryStore({ storage, locks });
    const client = recoveryAdapter();
    await expect(submitDurableFormalStocktakePost({ intent: intent(), expectedIdentity: IDENTITY, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakePostSubmissionPendingError);
    expect(client.execute).toHaveBeenCalledTimes(1);
    expect(client.postingCommandStatus).toHaveBeenCalledTimes(1);
    expect(client.detailNoReplay).not.toHaveBeenCalled();
    const serialized = [...storage.values.values()][0];
    expect(serialized).toContain("task_id");
    expect(serialized).toContain(TRACE);
    expect(serialized).not.toContain("Idempotency-Key");
    expect(serialized).not.toContain("webidem-");
    expect(serialized).not.toContain("body");

    await expect(submitDurableFormalStocktakePost({ intent: intent(), expectedIdentity: IDENTITY, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakePostSubmissionPendingError);
    expect(client.execute).toHaveBeenCalledTimes(1);
    expect(client.postingCommandStatus).toHaveBeenCalledTimes(2);
  });

  it("recovery uses the no-replay identity/access/detail methods", async () => {
    const storage = new MemoryStorage();
    const store = createFormalStocktakePostRecoveryStore({ storage, locks });
    const client = recoveryAdapter();
    const marker = sentinel();
    await store.withTaskLease(TASK, async (lease) => { lease.persist(marker); });
    await expect(store.withTaskLease(TASK, (lease) => recoverFormalStocktakePost(lease, marker, client))).rejects.toThrow(/不能据此重新提交/);
    expect(client.loadIdentityNoReplay).toHaveBeenCalled();
    expect(client.loadAccessNoReplay).toHaveBeenCalled();
    expect(client.postingCommandStatus).toHaveBeenCalled();
    expect(client.detailNoReplay).not.toHaveBeenCalled();
  });

  it("confirms a closed current projection from the historical post proof and clears only the exact marker", async () => {
    const storage = new MemoryStorage();
    const store = createFormalStocktakePostRecoveryStore({ storage, locks });
    const marker = sentinel();
    await store.withTaskLease(TASK, async (lease) => { lease.persist(marker); });
    const client = recoveryAdapter({
      postingCommandStatus: vi.fn(async () => confirmedStatus()),
      detailNoReplay: vi.fn(async () => closedDetail()),
    });
    expect(validateFormalStocktakePostRecoveredProjection(closedDetail(), marker, validateFormalStocktakePostCommandStatus(confirmedStatus(), marker).command!)).toMatchObject({ status: "closed", version: 8 });
    await expect(store.withTaskLease(TASK, (lease) => recoverFormalStocktakePost(lease, marker, client))).resolves.toMatchObject({ detail: { status: "closed", version: 8 } });
    expect(store.read(TASK)).toEqual({ kind: "missing" });
    expect(client.execute).not.toHaveBeenCalled();
  });

  it("allows separate valid markers for separate tasks and reports them without collapsing to corruption", async () => {
    const storage = new MemoryStorage();
    const store = createFormalStocktakePostRecoveryStore({ storage, locks });
    const second = validateFormalStocktakePostSentinel({ ...sentinel(), task_id: "10000000-0000-4000-8000-000000000002", trace_request_id: `web-${"c".repeat(36)}` });
    await store.withTaskLease(TASK, async (lease) => { lease.persist(sentinel()); });
    await store.withTaskLease(second.task_id, async (lease) => { lease.persist(second); });
    expect(store.readPending()).toMatchObject({ kind: "valid", values: [sentinel(), second] });
  });
});
