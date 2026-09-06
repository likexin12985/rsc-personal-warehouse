import { describe, expect, it, vi } from "vitest";

import {
  FormalStocktakeCountSubmissionPendingError,
  recoverFormalStocktakeCount,
  validateFormalStocktakeCountCommandStatus,
} from "./formalStocktakeCountRecovery";
import { submitDurableFormalStocktakeCount } from "./formalStocktakeCountSubmission";
import {
  createFormalStocktakeCountRecoveryStore,
  validateFormalStocktakeCountSentinel,
  type FormalStocktakeCountLockManager,
  type FormalStocktakeCountRecoveryStore,
  type FormalStocktakeCountSentinel,
} from "./formalStocktakeCountRecoveryStore";
import type { FormalStocktakeAdapter, FormalStocktakeAccess } from "./formalStocktakeAdapter";
import { createFormalStocktakeIntentRegistry } from "./formalStocktakes";

const TASK = "10000000-0000-4000-8000-000000000001";
const ROUND = "20000000-0000-4000-8000-000000000002";
const SCOPE = "30000000-0000-4000-8000-000000000003";
const PERSON = "40000000-0000-4000-8000-000000000004";
const COMPLETION = "50000000-0000-4000-8000-000000000005";
const REGION = "60000000-0000-4000-8000-000000000006";
const LOCATION = "70000000-0000-4000-8000-000000000007";
const TRACE = `web-count-${"a".repeat(24)}`;
const IDENTITY = { person_id: PERSON, authorization_version: 7 } as const;

class MemoryStorage {
  readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

const locks: FormalStocktakeCountLockManager = {
  async request(_name, _options, callback) { return callback({}); },
};

const access: FormalStocktakeAccess = {
  schema_version: "1.0", person_id: PERSON, authorization_version: 7,
  can_read: true, can_count: true, can_manage: false,
  can_review_region: false, can_review_headquarters: false,
  can_post: false, can_reconcile: false, can_close: false,
};

function identityResponse(personId = PERSON, authorizationVersion = 7) {
  return {
    person_id: personId, name: "工程师", employee_no: "E001", organization_code: "ORG",
    organization_name: "区域", account_status: "active", employment_status: "active",
    access_mode: "active", authorization_version: authorizationVersion, role_codes: ["technician"],
  };
}

function sentinel(operation: "initial_count" | "recount_count" = "recount_count"): FormalStocktakeCountSentinel {
  return validateFormalStocktakeCountSentinel({
    v: 1, kind: "formal_scope_count", task_id: TASK, round_id: ROUND,
    round_no: operation === "initial_count" ? 1 : 2, scope_id: SCOPE, operation,
    actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE,
  });
}

function status(value: "not_observed" | "confirmed" = "not_observed", operation = "recount_count") {
  return value === "not_observed"
    ? { schema_version: "1.0", task_id: TASK, round_id: ROUND, scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE, operation, lookup_status: "not_observed", command: null }
    : { schema_version: "1.0", task_id: TASK, round_id: ROUND, scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: TRACE, operation, lookup_status: "confirmed", command: { completion_id: COMPLETION, round_no: 2, completed_at: "2026-09-06T01:00:00Z", scope_completed: true, caused_round_submission: false } };
}

function detailProjection() {
  return {
    schema_version: "1.0", task_id: TASK, task_no: "ST-COUNT-001", task_type: "ad_hoc", region_org_id: REGION,
    status: "counting", version: 8, blind_count: true, current_round_no: 2, cutoff_ledger_cursor: 10,
    cutoff_at: "2026-09-06T00:00:00Z", issued_at: "2026-09-06T00:00:00Z", frozen_at: "2026-09-06T00:00:00Z",
    submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: "",
    state_axes: { count_status: "counting", difference_status: "hidden_for_blind_counter", region_review_status: "not_ready", headquarters_review_status: "not_ready", recount_status: "counting", posting_status: "not_posted", reconciliation_status: "not_reconciled", closure_status: "open" },
    close_control: { latest_reconciliation: null, close_completion: null },
    scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: "location_all", owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "visible", snapshot_accounts: [], allowed_actions: [] }],
    rounds: [{ round_id: ROUND, round_no: 2, round_type: "recount", status: "counting", started_at: "2026-09-06T00:30:00Z", submitted_at: null, submission: null,
      visible_scope_completions: [{ completion_id: COMPLETION, scope_id: SCOPE, count_ledger_cursor: 12, count_line_count: 0, observation_line_count: 0, serial_count: 0, total_counted_qty: "0.000", zero_confirmed: true, completed_by_person_id: PERSON, completed_at: "2026-09-06T01:00:00Z" }],
      visible_count_lines: [], visible_observations: [], differences_visible: false, difference_completion: null, visible_differences: [], region_review: null, headquarters_review: null, recount_cause: null,
      posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: [] }],
    allowed_actions: [],
  };
}

function intent() {
  return createFormalStocktakeIntentRegistry({ coordinateFactory: () => ({ "Idempotency-Key": `webidem-${"b".repeat(36)}`, "X-Request-ID": TRACE }) }).begin({
    action: "submit_recount_count", taskId: TASK, roundId: ROUND, scopeId: SCOPE,
    expectedTaskVersion: 7,
    body: { count_mode: "blind", account_counts: [], physical_observations: [{ material_id: null, material_identifier_raw: "SKU-1", material_identifier_type: "sku_code", condition_code: "new", availability_bucket: "available", counted_qty: "1.000", lot_id: null, lot_no_raw: null, serial_id: null, serial_no_raw: null, serial_identifier_type: null, count_method: "manual", reason_code: null, remark: "" }], evidence_file_ids: [], zero_confirmed: false },
  });
}

function adapterFixture(overrides: Partial<Record<string, unknown>> = {}): FormalStocktakeAdapter {
  let posts = 0;
  const adapter = {
    loadAccess: vi.fn(async () => access),
    loadAccessNoReplay: vi.fn(async () => access),
    loadIdentityNoReplay: vi.fn(async () => identityResponse()),
    detail: vi.fn(async () => detailProjection()),
    detailNoReplay: vi.fn(async () => detailProjection()),
    countCommandStatus: vi.fn(async () => status()),
    execute: vi.fn(async (_value: unknown, options?: { beforeWrite?: () => Promise<void> }) => {
      await options?.beforeWrite?.(); posts += 1; throw new Error("response lost after commit");
    }),
    list: vi.fn(), listRegions: vi.fn(), listLocations: vi.fn(), listAssignees: vi.fn(),
    get posts() { return posts; },
    ...overrides,
  } as unknown as FormalStocktakeAdapter & { posts: number };
  return adapter;
}

function storeFixture(storage = new MemoryStorage()): FormalStocktakeCountRecoveryStore {
  return createFormalStocktakeCountRecoveryStore({ storage, locks });
}

describe("Web daily stocktake count durable recovery", () => {
  it("validates operation/anchors and keeps not_observed as unknown", () => {
    const marker = sentinel();
    expect(validateFormalStocktakeCountCommandStatus(status(), marker)).toMatchObject({ lookup_status: "not_observed", command: null });
    expect(() => validateFormalStocktakeCountCommandStatus({ ...status(), operation: "initial_count" }, marker)).toThrow();
    expect(() => validateFormalStocktakeCountCommandStatus({ ...status(), lookup_status: "not_observed", command: {} }, marker)).toThrow();
  });

  it("persists only minimal coordinates, sends one POST, and leaves not_observed sticky", async () => {
    const storage = new MemoryStorage(); const store = storeFixture(storage); const client = adapterFixture();
    await expect(submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: IDENTITY, roundNo: 2, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakeCountSubmissionPendingError);
    expect((client as any).posts).toBe(1);
    expect(client.countCommandStatus).toHaveBeenCalledTimes(1);
    const serialized = [...storage.values.values()][0];
    expect(serialized).toContain("task_id"); expect(serialized).toContain("round_id"); expect(serialized).toContain("operation");
    expect(serialized).not.toContain("Idempotency-Key"); expect(serialized).not.toContain("webidem-"); expect(serialized).not.toContain("physical_observations");
    await expect(submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: IDENTITY, roundNo: 2, adapter: client, store })).rejects.toBeInstanceOf(FormalStocktakeCountSubmissionPendingError);
    expect((client as any).posts).toBe(1);
    expect(client.countCommandStatus).toHaveBeenCalledTimes(2);
  });

  it("retains the marker when identity changes and performs no POST", async () => {
    const store = storeFixture(); const marker = sentinel();
    await store.withScopeLease(marker, async (lease) => { lease.persist(marker); });
    const client = adapterFixture({ loadIdentityNoReplay: vi.fn(async () => identityResponse("41000000-0000-4000-8000-000000000004", 7)) });
    await expect(store.withScopeLease(marker, (lease) => recoverFormalStocktakeCount(lease, marker, {
      loadIdentity: async () => client.loadIdentityNoReplay!(), loadAccess: async () => access, commandStatus: async () => status(), detail: async () => detailProjection(),
    }))).rejects.toThrow();
    expect(store.read(marker)).toMatchObject({ kind: "valid" });
    expect(client.execute).not.toHaveBeenCalled();
  });

  it("fails closed on corrupted storage before a POST", async () => {
    const storage = new MemoryStorage();
    storage.setItem("cloud-oam-formal-stocktake-count-sentinel-v1:" + `${TASK}:${ROUND}:${SCOPE}:recount_count`, "{broken");
    const store = storeFixture(storage); const client = adapterFixture();
    await expect(submitDurableFormalStocktakeCount({ intent: intent(), expectedIdentity: IDENTITY, roundNo: 2, adapter: client, store })).rejects.toThrow(/持久恢复记录不可用/);
    expect((client as any).posts).toBe(0);
  });

  it("clears exactly one marker only after confirmed read-only proof", async () => {
    const store = storeFixture(); const marker = sentinel();
    await store.withScopeLease(marker, async (lease) => { lease.persist(marker); });
    const client = adapterFixture({ countCommandStatus: vi.fn(async () => status("confirmed")) });
    const recovered = await store.withScopeLease(marker, (lease) => recoverFormalStocktakeCount(lease, marker, {
      loadIdentity: async () => IDENTITY, loadAccess: async () => access,
      commandStatus: async () => client.countCommandStatus!(TASK, ROUND, SCOPE, "recount_count", PERSON, 7, TRACE),
      detail: async () => detailProjection(),
    }));
    expect(recovered.command.completion_id).toBe(COMPLETION);
    expect(store.read(marker)).toEqual({ kind: "missing" });
  });
});
