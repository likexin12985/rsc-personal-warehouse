import { describe, expect, it, vi } from "vitest";

import { createFormalStocktakeIntentRegistry } from "./formalStocktakes";
import {
  FormalStocktakeReviewSubmissionPendingError,
  recoverFormalStocktakeReview,
  submitDurableFormalStocktakeReview,
  validateFormalStocktakeReviewCommandStatus,
  validateFormalStocktakeReviewRecoveredProjection,
} from "./formalStocktakeReviewRecovery";
import {
  createFormalStocktakeReviewRecoveryStore,
  validateFormalStocktakeReviewSentinel,
  type FormalStocktakeReviewLockManager,
  type FormalStocktakeReviewSentinel,
} from "./formalStocktakeReviewRecoveryStore";

const TASK = "10000000-0000-4000-8000-000000000001";
const ROUND = "50000000-0000-4000-8000-000000000001";
const PERSON = "01000000-0000-4000-8000-000000000001";
const REGION_REVIEW = "70000000-0000-4000-8000-000000000001";
const HQ_REVIEW = "80000000-0000-4000-8000-000000000001";
const TRACE = `web-review-${"b".repeat(30)}`;
const IDENTITY = { person_id: PERSON, authorization_version: 7 } as const;

class MemoryStorage {
  readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

const locks: FormalStocktakeReviewLockManager = {
  async request(_name, _options, callback) { return callback({}); },
};

const access = {
  schema_version: "1.0" as const,
  person_id: PERSON,
  authorization_version: 7,
  can_read: true,
  can_count: false,
  can_manage: false,
  can_review_region: true,
  can_review_headquarters: true,
  can_post: false,
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

function intent(stage: "region" | "headquarters" = "region") {
  return createFormalStocktakeIntentRegistry({ coordinateFactory: coordinates }).begin({
    action: stage === "region" ? "review_region" : "review_headquarters",
    taskId: TASK,
    roundId: ROUND,
    expectedTaskVersion: 5,
    body: {
      expected_task_version: 5,
      decision: "approve",
      items: [],
      comment: "",
    },
  });
}

function sentinel(stage: "region" | "headquarters" = "region"): FormalStocktakeReviewSentinel {
  return validateFormalStocktakeReviewSentinel({
    v: 1,
    kind: "formal_stocktake_review",
    task_id: TASK,
    round_id: ROUND,
    review_stage: stage,
    actor_person_id: PERSON,
    actor_authorization_version: 7,
    expected_task_version: 5,
    trace_request_id: TRACE,
  });
}

function status(stage: "region" | "headquarters", lookup: "not_observed" | "confirmed" = "not_observed") {
  if (lookup === "not_observed") return {
    schema_version: "1.0", task_id: TASK, round_id: ROUND, review_stage: stage,
    actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE,
    lookup_status: "not_observed", command: null,
  };
  return {
    schema_version: "1.0", task_id: TASK, round_id: ROUND, review_stage: stage,
    actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE,
    lookup_status: "confirmed",
    command: {
      review_id: stage === "region" ? REGION_REVIEW : HQ_REVIEW,
      task_id: TASK, round_id: ROUND, review_stage: stage, decision: "approve",
      resulting_task_status: stage === "region" ? "hq_review" : "approved",
      expected_task_version: 5, resulting_task_version: 6, task_version: 6,
      item_count: 0, pending_verification_count: 0, ready_for_posting: stage === "headquarters",
      reviewed_at: "2026-09-06T10:00:00+08:00",
    },
  };
}

function detail(stage: "region" | "headquarters", statusValue: "hq_review" | "approved" = stage === "region" ? "hq_review" : "approved", reviewer = PERSON) {
  const axes = {
    count_status: "submitted", difference_status: "evaluated", region_review_status: stage === "region" ? "approve" : "approve",
    headquarters_review_status: stage === "headquarters" ? "approve" : "pending", recount_status: "not_required",
    posting_status: "not_posted", reconciliation_status: "not_reconciled", closure_status: "open",
  };
  return {
    schema_version: "1.0", task_id: TASK, task_no: "ST-001", task_type: "personal", region_org_id: "20000000-0000-4000-8000-000000000001",
    status: statusValue, version: 6, blind_count: true, current_round_no: 1, cutoff_ledger_cursor: 10,
    cutoff_at: "2026-09-06T08:00:00+08:00", issued_at: "2026-09-06T07:00:00+08:00", frozen_at: "2026-09-06T08:00:00+08:00",
    submitted_at: "2026-09-06T09:00:00+08:00", posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: "", state_axes: axes,
    close_control: { latest_reconciliation: null, close_completion: null },
    scopes: [{ scope_id: "30000000-0000-4000-8000-000000000001", scope_no: 1, scope_mode: "location_all", owner_org_id: "20000000-0000-4000-8000-000000000001", location_id: "40000000-0000-4000-8000-000000000001", custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "visible", snapshot_accounts: [], allowed_actions: [] }],
    rounds: [{
      round_id: ROUND, round_no: 1, round_type: "initial", status: "submitted", started_at: "2026-09-06T08:00:00+08:00", submitted_at: "2026-09-06T09:00:00+08:00", submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true,
      difference_completion: { completion_id: "60000000-0000-4000-8000-000000000001", completed_at: "2026-09-06T09:15:00+08:00", visible_difference_count: 0, visible_pending_verification_count: 0, visible_total_affected_qty: "0.000", covers_all_task_scopes: true }, visible_differences: [],
      region_review: stage === "region" ? { review_id: REGION_REVIEW, review_stage: "region", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: reviewer, reviewed_at: "2026-09-06T10:00:00+08:00", visible_items: [], covers_all_task_scopes: true } : { review_id: REGION_REVIEW, review_stage: "region", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: PERSON, reviewed_at: "2026-09-06T09:30:00+08:00", visible_items: [], covers_all_task_scopes: true },
      headquarters_review: stage === "headquarters" ? { review_id: HQ_REVIEW, review_stage: "headquarters", decision: "approve", comment: null, comment_visible: false, reviewer_person_id: reviewer, reviewed_at: "2026-09-06T10:00:00+08:00", visible_items: [], covers_all_task_scopes: true } : null,
      recount_cause: null, posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: true, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: [],
    }], allowed_actions: [],
  };
}

function recoveryAdapter(overrides: Record<string, unknown> = {}) {
  return {
    loadAccess: vi.fn(async () => access), loadAccessNoReplay: vi.fn(async () => access), loadIdentityNoReplay: vi.fn(async () => identityResponse()),
    detail: vi.fn(async () => detail("region")), detailNoReplay: vi.fn(async () => detail("region")),
    reviewCommandStatus: vi.fn(async () => status("region")),
    execute: vi.fn(async (_intent: unknown, options?: { beforeWrite?: (context: unknown) => Promise<void> }) => { await options?.beforeWrite?.({}); throw new Error("response lost after commit"); }),
    ...overrides,
  } as any;
}

describe("formal non-opening stocktake review durable recovery", () => {
  it("validates exact anchors and keeps not_observed sticky", () => {
    expect(validateFormalStocktakeReviewCommandStatus(status("region"), sentinel())).toMatchObject({ lookup_status: "not_observed", command: null });
    expect(() => validateFormalStocktakeReviewCommandStatus({ ...status("region"), review_stage: "headquarters" }, sentinel())).toThrow();
    expect(() => validateFormalStocktakeReviewCommandStatus({ ...status("region", "confirmed"), command: { ...status("region", "confirmed").command, resulting_task_version: 7 } }, sentinel())).toThrow();
  });

  it("persists only coordinates and performs at most one POST", async () => {
    const storage = new MemoryStorage(); const store = createFormalStocktakeReviewRecoveryStore({ storage, locks }); const client = recoveryAdapter();
    await expect(submitDurableFormalStocktakeReview({ intent: intent(), expectedIdentity: IDENTITY, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakeReviewSubmissionPendingError);
    expect(client.execute).toHaveBeenCalledTimes(1); expect(client.reviewCommandStatus).toHaveBeenCalledTimes(1);
    const serialized = [...storage.values.values()][0]; expect(serialized).toContain(TRACE); expect(serialized).not.toContain("Idempotency-Key"); expect(serialized).not.toContain("webidem-"); expect(serialized).not.toContain("items"); expect(serialized).not.toContain("comment");
    await expect(submitDurableFormalStocktakeReview({ intent: intent(), expectedIdentity: IDENTITY, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakeReviewSubmissionPendingError);
    expect(client.execute).toHaveBeenCalledTimes(1); expect(client.reviewCommandStatus).toHaveBeenCalledTimes(2);
  });

  it("confirms the review owner graph and clears only the exact marker", async () => {
    const storage = new MemoryStorage(); const store = createFormalStocktakeReviewRecoveryStore({ storage, locks }); const marker = sentinel();
    await store.withTaskLease(TASK, async (lease) => { lease.persist(marker); });
    const client = recoveryAdapter({ reviewCommandStatus: vi.fn(async () => status("region", "confirmed")), detailNoReplay: vi.fn(async () => detail("region")) });
    const command = validateFormalStocktakeReviewCommandStatus(status("region", "confirmed"), marker).command!;
    expect(validateFormalStocktakeReviewRecoveredProjection(detail("region"), marker, command)).toMatchObject({ status: "hq_review" });
    await expect(store.withTaskLease(TASK, (lease) => recoverFormalStocktakeReview(lease, marker, client))).resolves.toMatchObject({ detail: { status: "hq_review" } });
    expect(store.read(TASK)).toEqual({ kind: "missing" }); expect(client.execute).not.toHaveBeenCalled();
  });

  it("retains the marker on identity or review-owner mismatch", async () => {
    const storage = new MemoryStorage(); const store = createFormalStocktakeReviewRecoveryStore({ storage, locks }); const marker = sentinel();
    await store.withTaskLease(TASK, async (lease) => { lease.persist(marker); });
    const bad = recoveryAdapter({ detailNoReplay: vi.fn(async () => detail("region", "hq_review", "02000000-0000-4000-8000-000000000001")) });
    await expect(store.withTaskLease(TASK, (lease) => recoverFormalStocktakeReview(lease, marker, bad))).rejects.toThrow();
    expect(store.read(TASK)).toMatchObject({ kind: "valid" });
  });

  it("fails closed when storage or cross-tab coordination is unavailable", async () => {
    const store = createFormalStocktakeReviewRecoveryStore({ storage: null, locks });
    await expect(store.withTaskLease(TASK, async () => undefined)).rejects.toThrow(/持久恢复/);
    const unavailableLock = { request: vi.fn(async (_name: string, _opts: unknown, callback: (lock: null) => Promise<unknown>) => callback(null)) } as any;
    const blocked = createFormalStocktakeReviewRecoveryStore({ storage: new MemoryStorage(), locks: unavailableLock });
    await expect(blocked.withTaskLease(TASK, async () => undefined)).rejects.toThrow(/其他页面/);
  });
});

