import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  createOpeningCountRecoveryAdapter,
  recoverOpeningCountCommand,
  validateOpeningCountCommandStatus,
  validateOpeningCountRecoveredProjection,
  type OpeningCountRecoveryAdapter,
} from "./openingCountRecovery";
import {
  createOpeningCountRecoveryStore,
  type OpeningCountLockManager,
  type OpeningCountRecoveryStore,
  type OpeningCountSentinel,
} from "./openingCountRecoveryStore";
import type { FormalStocktakeAccess } from "./formalStocktakeAdapter";

const TASK_ID = "10000000-0000-4000-8000-000000000001";
const ROUND_ID = "20000000-0000-4000-8000-000000000002";
const NEXT_ROUND_ID = "21000000-0000-4000-8000-000000000002";
const SCOPE_ID = "30000000-0000-4000-8000-000000000003";
const PERSON_ID = "40000000-0000-4000-8000-000000000004";
const OTHER_PERSON_ID = "41000000-0000-4000-8000-000000000004";
const COMPLETION_ID = "50000000-0000-4000-8000-000000000005";
const REGION_ID = "60000000-0000-4000-8000-000000000006";
const LOCATION_ID = "70000000-0000-4000-8000-000000000007";
const OWNER_ID = "80000000-0000-4000-8000-000000000008";
const ASSIGNMENT_ID = "90000000-0000-4000-8000-000000000009";
const COMPLETED_AT = "2026-09-05T01:00:00Z";

const sentinel: OpeningCountSentinel = Object.freeze({
  v: 1,
  kind: "opening_scope_count",
  task_id: TASK_ID,
  round_id: ROUND_ID,
  round_no: 1,
  scope_id: SCOPE_ID,
  actor_person_id: PERSON_ID,
  actor_authorization_version: 7,
  trace_request_id: "opening-count-recovery-0001",
});

type Identity = Readonly<{ person_id: string; authorization_version: number }>;

const identity: Identity = Object.freeze({
  person_id: PERSON_ID,
  authorization_version: 7,
});

const access: FormalStocktakeAccess = Object.freeze({
  schema_version: "1.0",
  person_id: PERSON_ID,
  authorization_version: 7,
  can_read: true,
  can_count: true,
  can_manage: false,
  can_review_region: false,
  can_review_headquarters: false,
  can_post: false,
  can_reconcile: false,
  can_close: false,
});

class MemoryStorage {
  readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

class FakeLocks implements OpeningCountLockManager {
  readonly held = new Set<string>();
  readonly calls: Readonly<{ name: string; options: unknown }>[] = [];

  async request<T>(
    name: string,
    options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>,
  ): Promise<T> {
    (this.calls as { name: string; options: unknown }[]).push({ name, options });
    if (this.held.has(name)) return callback(null);
    this.held.add(name);
    try { return await callback(Object.freeze({ name })); } finally { this.held.delete(name); }
  }
}

function browserFixture() {
  const storage = new MemoryStorage();
  const locks = new FakeLocks();
  return { storage, locks };
}

async function seedPending(store: OpeningCountRecoveryStore, value = sentinel): Promise<void> {
  await store.withTaskLease(value.task_id, async (lease) => { lease.persist(value); });
}

function confirmedStatus(
  command: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    round_id: ROUND_ID,
    scope_id: SCOPE_ID,
    actor_person_id: PERSON_ID,
    actor_authorization_version: 7,
    trace_request_id: sentinel.trace_request_id,
    lookup_status: "confirmed",
    command: {
      completion_id: COMPLETION_ID,
      completed_at: COMPLETED_AT,
      round_no: 1,
      scope_completed: true,
      caused_round_submission: true,
      ...command,
    },
  };
}

function notObservedStatus(): Record<string, unknown> {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    round_id: ROUND_ID,
    scope_id: SCOPE_ID,
    actor_person_id: PERSON_ID,
    actor_authorization_version: 7,
    trace_request_id: sentinel.trace_request_id,
    lookup_status: "not_observed",
    command: null,
  };
}

function completedScope(completedAt: string = COMPLETED_AT) {
  return {
    scope_id: SCOPE_ID,
    scope_no: 1,
    location_id: LOCATION_ID,
    owner_org_id: OWNER_ID,
    assigned_to_me: true,
    completion_status: "completed",
    zero_confirmed: true,
    count_line_count: 0,
    observation_line_count: 0,
    serial_count: 0,
    total_counted_qty: "0.000",
    completed_at: completedAt,
  };
}

function submittedDetail(submittedAt: string = COMPLETED_AT) {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    task_no: "OPENING-RECOVERY-0001",
    region_org_id: REGION_ID,
    status: "submitted",
    blind_count: true,
    task_version: 4,
    deadline: "2026-09-08T00:00:00Z",
    cutoff_at: "2026-09-04T00:00:00Z",
    current_round: {
      round_id: ROUND_ID,
      round_no: 1,
      round_type: "initial",
      status: "submitted",
      started_at: "2026-09-05T00:00:00Z",
      submitted_at: submittedAt,
    },
    evidence_status: "sealed",
    scopes: [completedScope()],
    observations: [],
    differences: [],
    reviews: [],
    allowed_actions: ["review_region"],
  };
}

function countingDetail(completedAt: string = COMPLETED_AT) {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    task_no: "OPENING-RECOVERY-0001",
    region_org_id: REGION_ID,
    status: "counting",
    blind_count: true,
    task_version: 3,
    deadline: "2026-09-08T00:00:00Z",
    cutoff_at: "2026-09-04T00:00:00Z",
    current_round: {
      round_id: ROUND_ID,
      round_no: 1,
      round_type: "initial",
      status: "counting",
      started_at: "2026-09-05T00:00:00Z",
      submitted_at: null,
    },
    evidence_status: "counting_hidden",
    scopes: [{
      ...completedScope(completedAt),
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
    }],
    observations: [],
    differences: [],
    reviews: [],
    allowed_actions: ["count"],
  };
}

function laterRoundPendingDetail(startedAt = "2026-09-05T02:00:00Z") {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    task_no: "OPENING-RECOVERY-0001",
    region_org_id: REGION_ID,
    status: "counting",
    blind_count: true,
    task_version: 8,
    deadline: "2026-09-08T00:00:00Z",
    cutoff_at: "2026-09-04T00:00:00Z",
    current_round: {
      round_id: NEXT_ROUND_ID,
      round_no: 2,
      round_type: "recount",
      status: "counting",
      started_at: startedAt,
      submitted_at: null,
    },
    evidence_status: "counting_hidden",
    scopes: [{
      scope_id: SCOPE_ID,
      scope_no: 1,
      location_id: LOCATION_ID,
      owner_org_id: OWNER_ID,
      assigned_to_me: true,
      completion_status: "pending",
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
      completed_at: null,
    }],
    observations: [],
    differences: [],
    reviews: [],
    allowed_actions: ["count"],
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function recoveryAdapter(options: Readonly<{
  status?: unknown;
  detail?: unknown;
  identities?: readonly Identity[];
  accesses?: readonly FormalStocktakeAccess[];
  statusError?: unknown;
  detailError?: unknown;
}> = {}) {
  const events: string[] = [];
  let identityIndex = 0;
  let accessIndex = 0;
  const identities = options.identities ?? [identity, identity];
  const accesses = options.accesses ?? [access, access];
  const adapter: OpeningCountRecoveryAdapter = Object.freeze({
    async loadIdentity() {
      events.push("GET /auth/me");
      return identities[Math.min(identityIndex++, identities.length - 1)];
    },
    async loadAccess() {
      events.push("GET /access/context");
      return accesses[Math.min(accessIndex++, accesses.length - 1)];
    },
    async commandStatus() {
      events.push("GET count-command-status");
      if (options.statusError !== undefined) throw options.statusError;
      return options.status ?? confirmedStatus();
    },
    async detail() {
      events.push("GET opening-detail");
      if (options.detailError !== undefined) throw options.detailError;
      return options.detail ?? submittedDetail();
    },
  });
  return { adapter, events };
}

async function recover(
  store: OpeningCountRecoveryStore,
  adapter: OpeningCountRecoveryAdapter,
  canCommit: () => boolean = () => true,
) {
  return store.withTaskLease(sentinel.task_id, async (lease) => (
    recoverOpeningCountCommand(lease, sentinel, adapter, canCommit)
  ));
}

function expectPending(store: OpeningCountRecoveryStore): void {
  expect(store.read(TASK_ID)).toEqual({ kind: "valid", value: sentinel });
}

function rawSelf(personId = PERSON_ID, authorizationVersion = 7) {
  return {
    person_id: personId,
    name: "测试工程师",
    employee_no: "EMP-0001",
    organization_code: "ORG-001",
    organization_name: "测试区域公司",
    account_status: "active",
    employment_status: "active",
    access_mode: "active",
    authorization_version: authorizationVersion,
    role_codes: ["technician"],
  };
}

function rawAccess() {
  return {
    person_id: PERSON_ID,
    account_status: "active",
    employment_status: "active",
    authorization_version: 7,
    access_mode: "active",
    role_codes: ["technician"],
    assignments: [{
      assignment_id: ASSIGNMENT_ID,
      role_code: "technician",
      scope_type: "person",
      scope_id: PERSON_ID,
      valid_from: "2026-09-01T00:00:00Z",
      valid_to: null,
    }],
    permissions: [
      { resource: "stocktake", action: "read", field_code: "" },
      { resource: "stocktake", action: "count", field_code: "" },
    ],
  };
}

describe("opening count historical command recovery", () => {
  it("keeps an unknown historical command blocked and never issues a POST", async () => {
    const browser = browserFixture();
    const writer = createOpeningCountRecoveryStore(browser);
    await seedPending(writer);
    const restarted = createOpeningCountRecoveryStore(browser);
    const candidate = recoveryAdapter({ status: notObservedStatus() });

    await expect(recover(restarted, candidate.adapter)).rejects.toThrow("不能据此重新提交");

    expectPending(restarted);
    expect(candidate.events).toEqual([
      "GET /auth/me", "GET /access/context", "GET count-command-status",
    ]);
    expect(candidate.events.every((event) => event.startsWith("GET "))).toBe(true);
  });

  it("clears only a confirmed same-round completion with exact timestamps", async () => {
    const browser = browserFixture();
    const writer = createOpeningCountRecoveryStore(browser);
    await seedPending(writer);
    const restarted = createOpeningCountRecoveryStore(browser);
    const candidate = recoveryAdapter();

    const result = await recover(restarted, candidate.adapter);

    expect(result.command).toEqual((confirmedStatus().command));
    expect(result.detail.current_round?.round_id).toBe(ROUND_ID);
    expect(restarted.read(TASK_ID)).toEqual({ kind: "missing" });
    expect(candidate.events).toEqual([
      "GET /auth/me", "GET /access/context", "GET count-command-status", "GET opening-detail",
      "GET /auth/me", "GET /access/context",
    ]);
  });

  it("accepts a non-sealing completion after another scope later sealed the same round", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const detail = submittedDetail("2026-09-05T01:05:00Z");
    const candidate = recoveryAdapter({
      status: confirmedStatus({ caused_round_submission: false }),
      detail,
    });

    const result = await recover(store, candidate.adapter);

    expect(result.command.caused_round_submission).toBe(false);
    expect(store.read(TASK_ID)).toEqual({ kind: "missing" });
  });

  it("accepts an exact non-sealing completion while the same round remains counting", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const candidate = recoveryAdapter({
      status: confirmedStatus({ caused_round_submission: false }),
      detail: countingDetail(),
    });

    const result = await recover(store, candidate.adapter);

    expect(result.command.caused_round_submission).toBe(false);
    expect(result.detail.current_round).toMatchObject({ round_id: ROUND_ID, status: "counting" });
    expect(result.detail.scopes[0]).toMatchObject({ completion_status: "completed", completed_at: COMPLETED_AT });
    expect(store.read(TASK_ID)).toEqual({ kind: "missing" });
  });

  it("retains a same-round completion that predates the round start", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const detail = submittedDetail();
    detail.current_round.started_at = "2026-09-05T01:00:01Z";
    const candidate = recoveryAdapter({ detail });

    await expect(recover(store, candidate.adapter)).rejects.toThrow();
    expectPending(store);
  });

  it("retains a non-sealing completion that occurs after the round was submitted", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const candidate = recoveryAdapter({
      status: confirmedStatus({ caused_round_submission: false }),
      detail: submittedDetail("2026-09-05T00:59:59Z"),
    });

    await expect(recover(store, candidate.adapter)).rejects.toThrow();
    expectPending(store);
  });

  it.each([
    "2026-02-30T01:00:00Z", "2026-09-05T01:00:00+24:00", "2026-09-05T01:00:00+08:60",
  ])("rejects invalid calendar or zone in historical completion %s", (completedAt) => {
    expect(() => validateOpeningCountCommandStatus(confirmedStatus({ completed_at: completedAt }), sentinel)).toThrow();
  });

  it("does not truncate microseconds when comparing a later round start", async () => {
    const store = createOpeningCountRecoveryStore(browserFixture());
    await seedPending(store);
    const detail = laterRoundPendingDetail();
    detail.current_round.started_at = "2026-09-05T01:00:00.000001Z";
    const candidate = recoveryAdapter({ detail,
      status: confirmedStatus({ completed_at: "2026-09-05T01:00:00.000002Z" }),
    });
    await expect(recover(store, candidate.adapter)).rejects.toThrow();
    expectPending(store);
  });

  it("compares different timestamp offsets by instant across historical rounds", async () => {
    const store = createOpeningCountRecoveryStore(browserFixture());
    await seedPending(store);
    const detail = laterRoundPendingDetail();
    detail.current_round.started_at = "2026-09-05T09:00:00.000002+08:00";
    const candidate = recoveryAdapter({ detail,
      status: confirmedStatus({ completed_at: "2026-09-05T01:00:00.000001Z" }),
    });
    await expect(recover(store, candidate.adapter)).resolves.toMatchObject({ detail: { task_id: TASK_ID } });
    expect(store.read(TASK_ID)).toEqual({ kind: "missing" });
  });

  it("keeps historical completion separate from a pending scope in a later recount round", async () => {
    const browser = browserFixture();
    const firstProcess = createOpeningCountRecoveryStore(browser);
    await seedPending(firstProcess);
    const restarted = createOpeningCountRecoveryStore(browser);
    const candidate = recoveryAdapter({
      status: confirmedStatus({ caused_round_submission: false }),
      detail: laterRoundPendingDetail(),
    });

    const result = await recover(restarted, candidate.adapter);

    expect(result.command.round_no).toBe(1);
    expect(result.detail.current_round?.round_no).toBe(2);
    expect(result.detail.scopes[0].completion_status).toBe("pending");
    expect(restarted.read(TASK_ID)).toEqual({ kind: "missing" });
  });

  it.each([
    ["scope completion timestamp", () => {
      const value = submittedDetail();
      value.scopes[0].completed_at = "2026-09-05T01:00:00+00:00";
      return value;
    }],
    ["sealing submission timestamp", () => submittedDetail("2026-09-05T01:00:01Z")],
    ["same-number round id", () => {
      const value = submittedDetail();
      value.current_round.round_id = NEXT_ROUND_ID;
      return value;
    }],
    ["initial round type", () => {
      const value = submittedDetail();
      value.current_round.round_type = "recount";
      return value;
    }],
    ["superseded current round", () => {
      const value = submittedDetail();
      value.current_round.status = "superseded";
      return value;
    }],
    ["later round reuses historical id", () => {
      const value = laterRoundPendingDetail();
      value.current_round.round_id = ROUND_ID;
      return value;
    }],
    ["later round begins before the historical completion", () => laterRoundPendingDetail("2026-09-05T00:59:59Z")],
    ["later round has the initial type", () => {
      const value = laterRoundPendingDetail();
      value.current_round.round_type = "initial";
      return value;
    }],
  ])("retains the marker for conflicting %s evidence", async (_name, detailFactory) => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const candidate = recoveryAdapter({ detail: detailFactory() });

    await expect(recover(store, candidate.adapter)).rejects.toThrow();
    expectPending(store);
  });

  it("retains the marker when task or visible scope projection does not match", async () => {
    for (const detail of [
      { ...submittedDetail(), task_id: "a0000000-0000-4000-8000-00000000000a" },
      { ...submittedDetail(), scopes: [] },
    ]) {
      const browser = browserFixture();
      const store = createOpeningCountRecoveryStore(browser);
      await seedPending(store);
      await expect(recover(store, recoveryAdapter({ detail }).adapter)).rejects.toThrow();
      expectPending(store);
    }
  });

  it("does not query or replace a corrupt persisted record", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const [key] = browser.storage.values.keys();
    browser.storage.values.set(key, "{corrupt");
    const candidate = recoveryAdapter();

    await expect(recover(store, candidate.adapter)).rejects.toThrow("继续保持待核验");

    expect(candidate.events).toEqual([]);
    expect(browser.storage.values.get(key)).toBe("{corrupt");
    expect(store.read(TASK_ID)).toEqual({ kind: "corrupt" });
  });

  it.each([
    ["identity before lookup", [
      { person_id: OTHER_PERSON_ID, authorization_version: 7 }, identity,
    ], [access, access]],
    ["identity after lookup", [
      identity, { person_id: OTHER_PERSON_ID, authorization_version: 7 },
    ], [access, access]],
    ["authorization before lookup", [
      { person_id: PERSON_ID, authorization_version: 8 }, identity,
    ], [access, access]],
    ["authorization after lookup", [
      identity, { person_id: PERSON_ID, authorization_version: 8 },
    ], [access, access]],
    ["permission before lookup", [identity, identity], [
      { ...access, can_count: false }, access,
    ]],
    ["permission after lookup", [identity, identity], [
      access, { ...access, can_read: false, can_count: false },
    ]],
  ])("retains the marker when %s changes", async (_name, identities, accesses) => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const candidate = recoveryAdapter({ identities, accesses });

    await expect(recover(store, candidate.adapter)).rejects.toThrow();
    expectPending(store);
  });

  it("retains the marker when the page generation changes before commit", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    let generation = 1;
    const captured = generation;
    const candidate = recoveryAdapter();
    const adapter: OpeningCountRecoveryAdapter = {
      ...candidate.adapter,
      async loadAccess() {
        const result = await candidate.adapter.loadAccess();
        if (candidate.events.filter((event) => event === "GET /access/context").length === 2) generation += 1;
        return result;
      },
    };

    await expect(recover(store, adapter, () => generation === captured)).rejects.toThrow("页面已变化");
    expectPending(store);
  });

  it("retains the exact marker and original HTTP error for every failed status lookup", async () => {
    for (const status of [401, 403, 404, 409, 412, 429, 500, 503]) {
      const browser = browserFixture();
      const store = createOpeningCountRecoveryStore(browser);
      await seedPending(store);
      const failure = new ApiError(status, `status lookup failed ${status}`, {
        code: "opening_count_command_status_failed",
        category: "service_unavailable",
      });
      const candidate = recoveryAdapter({ statusError: failure });

      await expect(recover(store, candidate.adapter)).rejects.toBe(failure);
      expectPending(store);
      expect(candidate.events).toEqual([
        "GET /auth/me", "GET /access/context", "GET count-command-status",
      ]);
    }
  });

  it("retains the exact marker and original HTTP error when the detail reread fails", async () => {
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const failure = new ApiError(503, "detail unavailable", {
      code: "opening_stocktake_read_unavailable",
      category: "service_unavailable",
    });
    const candidate = recoveryAdapter({ detailError: failure });

    await expect(recover(store, candidate.adapter)).rejects.toBe(failure);
    expectPending(store);
    expect(candidate.events).toEqual([
      "GET /auth/me", "GET /access/context", "GET count-command-status", "GET opening-detail",
    ]);
  });

  it("fails closed when exact cleanup cannot be confirmed after all read evidence succeeds", async () => {
    const durable = new MemoryStorage();
    const locks = new FakeLocks();
    const storage = {
      getItem: (key: string) => durable.getItem(key),
      setItem: (key: string, value: string) => durable.setItem(key, value),
      removeItem() { throw new Error("storage removal failed"); },
    };
    const store = createOpeningCountRecoveryStore({ storage, locks });
    await seedPending(store);
    const candidate = recoveryAdapter();

    await expect(recover(store, candidate.adapter)).rejects.toThrow("清理未确认");

    expect(durable.values.size).toBe(1);
    expect([...durable.values.values()]).toEqual([JSON.stringify(sentinel)]);
    expect(store.read(TASK_ID)).toEqual({ kind: "unavailable" });
  });

  it("does not allow a second tab to enter recovery while the first status read is pending", async () => {
    const browser = browserFixture();
    const firstStore = createOpeningCountRecoveryStore(browser);
    const secondStore = createOpeningCountRecoveryStore(browser);
    await seedPending(firstStore);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    let entered = false;
    const firstCandidate = recoveryAdapter();
    const firstAdapter: OpeningCountRecoveryAdapter = {
      ...firstCandidate.adapter,
      async commandStatus(value) {
        entered = true;
        await gate;
        return firstCandidate.adapter.commandStatus(value);
      },
    };
    const first = recover(firstStore, firstAdapter);
    await expect.poll(() => entered).toBe(true);
    let secondEntered = false;

    await expect(secondStore.withTaskLease(TASK_ID, async () => { secondEntered = true; })).rejects.toThrow("其他页面");
    expect(secondEntered).toBe(false);
    release();
    await first;
  });
});

describe("opening count status and projection validators", () => {
  it.each([
    ["task_id", "a0000000-0000-4000-8000-00000000000a"],
    ["round_id", NEXT_ROUND_ID],
    ["scope_id", "b0000000-0000-4000-8000-00000000000b"],
    ["actor_person_id", OTHER_PERSON_ID],
    ["actor_authorization_version", 8],
    ["trace_request_id", "opening-count-recovery-wrong"],
    ["schema_version", "2.0"],
  ])("rejects a confirmed response with the wrong top-level %s", (field, value) => {
    expect(() => validateOpeningCountCommandStatus({ ...confirmedStatus(), [field]: value }, sentinel)).toThrow();
  });

  it.each([
    ["completion_id", "00000000-0000-0000-0000-000000000000"],
    ["completed_at", "2026-09-05 01:00:00"],
    ["round_no", 2],
    ["scope_completed", false],
    ["caused_round_submission", "true"],
  ])("rejects a confirmed response with an invalid command %s", (field, value) => {
    expect(() => validateOpeningCountCommandStatus(confirmedStatus({ [field]: value }), sentinel)).toThrow();
  });

  it("rejects missing, extra, contradictory and unknown response shapes", () => {
    const missing = clone(confirmedStatus());
    delete missing.scope_id;
    const extraTop = { ...confirmedStatus(), request_hash: "a".repeat(64) };
    const extraCommand = confirmedStatus();
    (extraCommand.command as Record<string, unknown>).idempotency_key = "never-echo-this-key";
    const contradictory = { ...notObservedStatus(), command: confirmedStatus().command };
    const unknown = { ...notObservedStatus(), lookup_status: "unknown" };
    for (const value of [missing, extraTop, extraCommand, contradictory, unknown]) {
      expect(() => validateOpeningCountCommandStatus(value, sentinel)).toThrow("继续保持待核验");
    }
  });

  it("distinguishes an exact historical completion from the current pending recount scope", () => {
    const status = validateOpeningCountCommandStatus(
      confirmedStatus({ caused_round_submission: false }), sentinel,
    );
    if (status.lookup_status !== "confirmed") throw new Error("expected confirmed fixture");
    const detail = validateOpeningCountRecoveredProjection(
      laterRoundPendingDetail(), sentinel, status.command,
    );
    expect(detail.current_round?.round_no).toBe(2);
    expect(detail.scopes[0]).toMatchObject({ scope_id: SCOPE_ID, completion_status: "pending", completed_at: null });
  });
});

describe("opening count recovery HTTP adapter", () => {
  it("uses only the exact no-store identity, access, status and detail GET endpoints", async () => {
    const calls: { path: string; init: RequestInit | undefined }[] = [];
    const requester = async (path: string, init?: RequestInit): Promise<unknown> => {
      calls.push({ path, init });
      if (path === "/auth/me") return rawSelf();
      if (path === "/access/context") return rawAccess();
      if (path.includes("count-command-status")) return confirmedStatus();
      if (path === `/v1/stocktakes/opening/${TASK_ID}`) return submittedDetail();
      throw new Error(`unexpected path: ${path}`);
    };
    const adapter = createOpeningCountRecoveryAdapter(identity, requester);

    expect(await adapter.loadIdentity()).toEqual(identity);
    expect(await adapter.loadAccess()).toMatchObject({
      schema_version: "1.0", person_id: PERSON_ID, authorization_version: 7,
      can_read: true, can_count: true,
    });
    await adapter.commandStatus(sentinel);
    await adapter.detail(TASK_ID);

    const expectedStatusPath = `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/scopes/${SCOPE_ID}/count-command-status?`
      + `actor_person_id=${PERSON_ID}&actor_authorization_version=7&trace_request_id=${sentinel.trace_request_id}`;
    expect(calls.map((call) => call.path)).toEqual([
      "/auth/me", "/access/context", expectedStatusPath, `/v1/stocktakes/opening/${TASK_ID}`,
    ]);
    for (const { init } of calls) {
      expect(init?.method ?? "GET").toBe("GET");
      expect(init?.cache).toBe("no-store");
      const headers = new Headers(init?.headers);
      expect(headers.get("Cache-Control")).toBe("no-store");
      expect(headers.get("Pragma")).toBe("no-cache");
      expect(headers.has("Idempotency-Key")).toBe(false);
      expect(init?.body).toBeUndefined();
    }
  });

  it("does not send a status request when the persisted identity differs from the adapter identity", async () => {
    const calls: string[] = [];
    const adapter = createOpeningCountRecoveryAdapter(identity, async (path) => {
      calls.push(path);
      return null;
    });
    expect(() => adapter.commandStatus({ ...sentinel, actor_person_id: OTHER_PERSON_ID })).toThrow("人员或权限已变化");
    expect(calls).toEqual([]);
  });

  it("preserves HTTP failures without introducing write calls", async () => {
    const failure = new ApiError(503, "read service unavailable", {
      code: "opening_count_command_status_evidence_invalid",
      category: "service_unavailable",
    });
    const calls: { path: string; init?: RequestInit }[] = [];
    const adapter = createOpeningCountRecoveryAdapter(identity, async (path, init) => {
      calls.push({ path, init });
      throw failure;
    });

    await expect(adapter.commandStatus(sentinel)).rejects.toBe(failure);
    expect(calls).toHaveLength(1);
    expect(calls[0].init?.method).toBe("GET");
  });
});

describe("opening count recovery sensitive-data boundary", () => {
  it("stores and emits no raw idempotency key, request body or request hash", async () => {
    const rawKey = "opening-count-secret-idempotency-key-0001";
    const rawBody = "secret physical observation body";
    const rawHash = "f".repeat(64);
    const browser = browserFixture();
    const store = createOpeningCountRecoveryStore(browser);
    await seedPending(store);
    const response = {
      ...confirmedStatus(),
      idempotency_key: rawKey,
      request_body: rawBody,
      request_hash: rawHash,
    };
    const candidate = recoveryAdapter({ status: response });
    let thrown: unknown;
    try { await recover(store, candidate.adapter); } catch (error) { thrown = error; }

    expect(thrown).toBeInstanceOf(ApiError);
    const observable = [
      ...browser.storage.values.values(),
      ...candidate.events,
      String(thrown),
      JSON.stringify(thrown),
      thrown instanceof Error ? thrown.stack ?? "" : "",
    ].join("\n");
    expect(observable).not.toContain(rawKey);
    expect(observable).not.toContain(rawBody);
    expect(observable).not.toContain(rawHash);
    expect(observable).not.toMatch(/idempotency_key|request_body|request_hash/);
    expectPending(store);
  });
});
