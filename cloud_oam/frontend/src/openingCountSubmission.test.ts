import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  OpeningCountSubmissionPendingError, submitDurableOpeningScopeCount,
} from "./openingCountSubmission";
import {
  createOpeningCountRecoveryStore, type OpeningCountLockManager,
  type OpeningCountSentinel,
} from "./openingCountRecoveryStore";

const id = (prefix: string) => `${prefix}0000000-0000-4000-8000-000000000001`;
const TASK = id("1"), ROUND = id("2"), SCOPE = id("3"), PERSON = id("4"), COMPLETION = id("5");
const NEXT_ROUND = id("6"), OTHER_SCOPE = id("7"), OTHER_PERSON = id("8");
const COMPLETED = "2026-09-05T01:00:00Z";
const KEY = `cloud-oam-opening-count-sentinel-v1:${TASK}`;
const identity = Object.freeze({ person_id: PERSON, authorization_version: 7 });
const emptyInput = () => ({ physical_observations: [], zero_confirmed: true });
const originalSentinel: OpeningCountSentinel = Object.freeze({
  v: 1, kind: "opening_scope_count", task_id: TASK, round_id: ROUND, round_no: 1,
  scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 7,
  trace_request_id: "web-opening-durable-old-0001",
});
afterEach(() => { vi.unstubAllGlobals(); });

function activeIdentity() {
  return { ...identity, name: "工程师", employee_no: "EMP001", organization_code: "ORG001",
    organization_name: "测试组织", account_status: "active", employment_status: "active",
    access_mode: "active", role_codes: ["technician"] };
}
function access() {
  return { ...identity, account_status: "active", employment_status: "active", access_mode: "active",
    role_codes: ["technician"], assignments: [],
    permissions: ["read", "count"].map((action) => ({ resource: "stocktake", action, field_code: "" })) };
}
function before() {
  return { schema_version: "1.0", task_id: TASK, task_no: "OPENING-DURABLE-001", region_org_id: id("9"),
    status: "counting", blind_count: true, task_version: 3, deadline: "2026-09-08T00:00:00Z",
    cutoff_at: "2026-09-04T00:00:00Z",
    current_round: { round_id: ROUND, round_no: 1, round_type: "initial", status: "counting",
      started_at: "2026-09-05T00:00:00Z", submitted_at: null },
    evidence_status: "counting_hidden",
    scopes: [{ scope_id: SCOPE, scope_no: 1, location_id: id("a"), owner_org_id: id("b"),
      assigned_to_me: true, completion_status: "pending", zero_confirmed: null,
      count_line_count: null, observation_line_count: null, serial_count: null,
      total_counted_qty: null, completed_at: null }],
    observations: [], differences: [], reviews: [], allowed_actions: ["count"] };
}
function after() {
  const source = before();
  return { ...source, status: "submitted", task_version: 4, evidence_status: "sealed",
    current_round: { ...source.current_round, status: "submitted", submitted_at: COMPLETED },
    scopes: [{ ...source.scopes[0], completion_status: "completed", zero_confirmed: true,
      count_line_count: 0, observation_line_count: 0, serial_count: 0, total_counted_qty: "0.000",
      completed_at: COMPLETED }], allowed_actions: ["review_region"] };
}
function later() {
  const source = before();
  return { ...source, task_version: 8, current_round: { ...source.current_round,
    round_id: NEXT_ROUND, round_no: 2, round_type: "recount", started_at: "2026-09-05T02:00:00Z" } };
}
function postResult() {
  return { schema_version: "1.0", task_id: TASK, round_id: ROUND, scope_id: SCOPE,
    task_status: "submitted", round_status: "submitted", scope_completed: true,
    round_sealed: true, has_pending_verification: false, replayed: false };
}
function status(trace: string, confirmed = true) {
  return { schema_version: "1.0", task_id: TASK, round_id: ROUND, scope_id: SCOPE,
    actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: trace,
    lookup_status: confirmed ? "confirmed" : "not_observed", command: confirmed
      ? { completion_id: COMPLETION, completed_at: COMPLETED, round_no: 1,
        scope_completed: true, caused_round_submission: true } : null };
}

class MemoryStorage {
  readonly values = new Map<string, string>();
  onGet?: () => void;
  onSet?: (value: string) => void;
  onRemove?: () => void;
  getItem(key: string) { this.onGet?.(); return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.onSet?.(value); this.values.set(key, value); }
  removeItem(key: string) { this.onRemove?.(); this.values.delete(key); }
}
class NativeLocks implements OpeningCountLockManager {
  readonly held = new Set<string>();
  async request<T>(name: string, options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>): Promise<T> {
    expect(options).toEqual({ mode: "exclusive", ifAvailable: true });
    if (this.held.has(name)) return callback(null);
    this.held.add(name);
    try { return await callback({ name }); } finally { this.held.delete(name); }
  }
}
type Kind = "identity" | "access" | "detail" | "status" | "post";
function fixture(seed = false) {
  const storage = new MemoryStorage(), locks = new NativeLocks();
  const makeStore = () => createOpeningCountRecoveryStore({ storage, locks });
  const store = makeStore();
  if (seed) storage.values.set(KEY, JSON.stringify(originalSentinel));
  const counts: Record<Kind, number> = { identity: 0, access: 0, detail: 0, status: 0, post: 0 };
  const handlers: Partial<Record<Kind, (n: number, init?: RequestInit) => unknown | Promise<unknown>>> = {};
  const calls: { kind: Kind; path: string; init?: RequestInit }[] = [];
  let trace = originalSentinel.trace_request_id;
  const requester = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
    const kind: Kind = init?.method === "POST" ? "post" : path === "/auth/me" ? "identity"
      : path === "/access/context" ? "access" : path.includes("/count-command-status?") ? "status" : "detail";
    calls.push({ kind, path, init });
    const n = ++counts[kind];
    if (kind === "post") {
      const stored = store.read(TASK);
      expect(stored.kind).toBe("valid");
      trace = new Headers(init?.headers).get("X-Request-ID")!;
      if (stored.kind === "valid") expect(stored.value.trace_request_id).toBe(trace);
      expect(locks.held.size).toBe(1);
    }
    if (handlers[kind]) return handlers[kind]!(n, init);
    if (kind === "identity") return activeIdentity();
    if (kind === "access") return access();
    if (kind === "detail") return counts.post || seed ? after() : before();
    if (kind === "post") return postResult();
    const query = new URLSearchParams(path.split("?")[1]);
    expect(query.get("trace_request_id")).toBe(trace);
    expect(query.get("actor_person_id")).toBe(PERSON);
    expect([...query.keys()].sort()).toEqual(["actor_authorization_version", "actor_person_id", "trace_request_id"]);
    return status(trace);
  });
  const submit = (overrides: Partial<Parameters<typeof submitDurableOpeningScopeCount>[0]> = {}) =>
    submitDurableOpeningScopeCount({ taskId: TASK, scopeId: SCOPE, input: emptyInput(),
      expectedIdentity: identity, store, requester, ...overrides });
  return { storage, locks, store, makeStore, counts, handlers, calls, requester, submit,
    getTrace: () => trace };
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}
function rejected(statusCode: number, category: string, code: string) {
  return new ApiError(statusCode, "SECRET-SERVER-BODY-DO-NOT-EXPOSE", { category, code });
}

describe("durable opening count submission", () => {
  it("default transport sends a physical count POST once on 401, without refreshing or replaying the write", async () => {
    const f = fixture();
    let posts = 0;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.replace(/^\/api/, "");
      if (init?.method === "POST") {
        expect(path).toBe(`/v1/stocktakes/opening/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/count`);
        expect(f.store.read(TASK).kind).toBe("valid");
        posts += 1;
        return new Response(JSON.stringify({ detail: { category: "unauthorized", code: "authentication_required" } }), { status: 401 });
      }
      if (posts > 0) return new Response("unavailable", { status: 503 });
      return new Response(JSON.stringify(path === "/auth/me" ? activeIdentity() : path === "/access/context" ? access() : before()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("BroadcastChannel", undefined);
    await expect(submitDurableOpeningScopeCount({ taskId: TASK, scopeId: SCOPE, input: emptyInput(),
      expectedIdentity: identity, store: f.store })).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(posts).toBe(1);
    expect(fetcher.mock.calls.some(([url]) => url.includes("/auth/refresh"))).toBe(false);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.requester).not.toHaveBeenCalled();
  });

  it("persists exact non-sensitive coordinates before one POST and clears only after independent historical and current evidence", async () => {
    const f = fixture();
    let persisted = "";
    f.storage.onSet = (value) => { persisted = value; expect(f.counts.post).toBe(0); };
    f.storage.onRemove = () => {
      expect(f.counts).toEqual({ identity: 4, access: 4, detail: 2, status: 1, post: 1 });
    };
    const result = await f.submit();
    expect(result.recovered).toBe(false);
    expect(result.command.completion_id).toBe(COMPLETION);
    expect(f.store.read(TASK)).toEqual({ kind: "missing" });
    expect(Object.keys(JSON.parse(persisted)).sort()).toEqual(Object.keys(originalSentinel).sort());
    const post = f.calls.find((row) => row.kind === "post")!;
    expect(persisted).not.toContain(new Headers(post.init?.headers).get("Idempotency-Key"));
    expect(persisted).not.toMatch(/physical_observations|zero_confirmed|counted_qty|sha256|request_body|actor_user_id/);
    expect(post.path).toBe(`/v1/stocktakes/opening/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/count`);
    expect(post.init?.body).toBe(JSON.stringify(emptyInput()));
    expect(f.calls.map((row) => row.kind)).toEqual([
      "identity", "access", "detail", "identity", "access", "post", "identity", "access", "status", "detail", "identity", "access",
    ]);
    expect(Object.isFrozen(result)).toBe(true);
    f.calls.filter((row) => row.kind !== "post").forEach((row) => {
      expect(row.init?.method ?? "GET").toBe("GET");
      expect(row.init?.cache).toBe("no-store");
      expect(new Headers(row.init?.headers).has("Idempotency-Key")).toBe(false);
    });
  });

  it("keeps an unknown POST pending then a new store instance recovers with GET only despite changed input", async () => {
    const f = fixture();
    f.handlers.post = () => { throw new Error("SECRET-BODY-AND-KEY"); };
    f.handlers.status = () => status(f.getTrace(), false);
    const error = await f.submit().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(JSON.stringify(error)).not.toMatch(/SECRET|physical_observations|Idempotency|cause/);
    expect(error).toMatchObject({ task_id: TASK, round_id: ROUND, scope_id: SCOPE, trace_request_id: f.getTrace() });
    expect(f.store.read(TASK).kind).toBe("valid");
    delete f.handlers.status;
    const result = await f.submit({ store: f.makeStore(), input: { physical_observations: [], zero_confirmed: false } });
    expect(result.recovered).toBe(true);
    expect(f.counts.post).toBe(1);
    expect(f.store.read(TASK).kind).toBe("missing");
  });

  it("recovers a lost POST response in the same call without retransmission", async () => {
    const f = fixture();
    f.handlers.post = () => { throw new TypeError("network uncertain"); };
    const result = await f.submit();
    expect(result.recovered).toBe(true);
    expect(f.counts.post).toBe(1);
  });

  it("does not evaluate a new form when a historical sentinel already exists", async () => {
    const f = fixture(true);
    const input = { get zero_confirmed(): boolean { throw new Error("must not inspect form"); }, physical_observations: [] };
    const result = await f.submit({ scopeId: OTHER_SCOPE, input });
    expect(result.recovered).toBe(true);
    expect(f.counts.post).toBe(0);
    expect(f.storage.values.size).toBe(0);
  });

  it("returns historical round-one completion separately from a pending round-two form", async () => {
    const f = fixture(true);
    f.handlers.detail = () => later();
    const result = await f.submit();
    expect(result.recovered).toBe(true);
    expect(result.command.round_no).toBe(1);
    expect(result.detail.current_round?.round_no).toBe(2);
    expect(result.detail.scopes[0].completion_status).toBe("pending");
    expect(f.counts.post).toBe(0);
  });

  it.each([
    [400, "invalid_request", "idempotency_key_invalid"],
    [400, "invalid_request", "x_request_id_invalid"],
    [412, "precondition_failed", "opening_count_state_invalid"],
  ])("clears only an exact first POST rejection %s/%s/%s", async (code, category, reason) => {
    const f = fixture();
    f.handlers.post = () => { throw rejected(code as number, category as string, reason as string); };
    const error = await f.submit().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).not.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(String(error)).not.toContain("SECRET");
    expect(f.store.read(TASK).kind).toBe("missing");
    expect(f.counts).toMatchObject({ post: 1, status: 0, detail: 1 });
  });

  it.each([
    [400, "invalid_request", "opening_count_state_invalid"],
    [412, "precondition_failed", "x_request_id_invalid"],
    [412, "invalid_request", "opening_count_state_invalid"],
    [409, "conflict", "opening_count_state_invalid"],
    [422, "invalid_request", "validation_error"],
    [401, "unauthorized", "authentication_required"],
    [503, "precondition_failed", "opening_count_state_invalid"],
  ])("keeps all other direct rejections pending %s/%s/%s", async (code, category, reason) => {
    const f = fixture();
    f.handlers.post = () => { throw rejected(code as number, category as string, reason as string); };
    f.handlers.status = () => status(f.getTrace(), false);
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(1);
  });

  it.each(["identity", "access"] as const)("keeps an exact rejection pending if %s changes before clearing", async (kind) => {
    const f = fixture();
    f.handlers.post = () => { throw rejected(412, "precondition_failed", "opening_count_state_invalid"); };
    f.handlers[kind] = (n) => kind === "identity"
      ? { ...activeIdentity(), person_id: n <= 2 ? PERSON : OTHER_PERSON }
      : { ...access(), authorization_version: n <= 2 ? 7 : 8 };
    await expect(f.submit({ canCommit: () => true })).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(1);
    expect(f.counts.status).toBe(0);
  });

  it("does not use a whitelisted GET failure during rejection readback to clear", async () => {
    const f = fixture();
    f.handlers.post = () => { throw rejected(412, "precondition_failed", "opening_count_state_invalid"); };
    f.handlers.identity = (n) => {
      if (n <= 2) return activeIdentity();
      throw rejected(400, "invalid_request", "x_request_id_invalid");
    };
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(1);
  });

  it.each(["status", "detail", "identity", "access"] as const)("cannot treat a later %s GET rejection as a POST rejection", async (kind) => {
    const f = fixture();
    f.handlers[kind] = (n) => {
      if ((kind === "identity" || kind === "access") && n <= 2) return kind === "identity" ? activeIdentity() : access();
      if (kind === "detail" && n === 1) return before();
      throw rejected(412, "precondition_failed", "opening_count_state_invalid");
    };
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(1);
  });

  it("requires an actual API response, not an error-shaped object, to release a sentinel", async () => {
    const f = fixture();
    f.handlers.post = () => { throw { status: 412, category: "precondition_failed", code: "opening_count_state_invalid", responseReceived: true }; };
    f.handlers.status = () => status(f.getTrace(), false);
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
  });

  it.each([
    { scope_completed: 1 }, { round_sealed: "true" }, { replayed: 0 }, { task_id: OTHER_PERSON },
    { scope_id: OTHER_SCOPE }, { round_id: NEXT_ROUND }, { round_status: "counting" },
    { schema_version: "2.0" }, { secret_payload: "must not be returned" },
  ])("treats a malformed or mismatched POST contract as only a recovery signal: %j", async (change) => {
    const f = fixture();
    f.handlers.post = () => ({ ...postResult(), ...change });
    const result = await f.submit();
    expect(result.recovered).toBe(true);
    expect(f.counts.post).toBe(1);
    expect(f.counts.status).toBe(1);
    expect(JSON.stringify(result)).not.toContain("secret_payload");
  });

  it("does not clear a successful-looking POST without historical confirmation", async () => {
    const f = fixture();
    f.handlers.status = () => status(f.getTrace(), false);
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.detail).toBe(1);
  });

  it("reports a replayed POST only as recovered historical completion", async () => {
    const f = fixture();
    f.handlers.post = () => ({ ...postResult(), replayed: true });
    await expect(f.submit()).resolves.toMatchObject({ recovered: true });
    expect(f.counts.post).toBe(1);
    expect(f.counts.status).toBe(1);
  });

  it("does not treat a well-formed but mismatching seal signal as a newly proved submit", async () => {
    const f = fixture();
    f.handlers.post = () => ({ ...postResult(), round_sealed: false, round_status: "counting", task_status: "counting" });
    const result = await f.submit();
    expect(result.recovered).toBe(true);
    expect(result.command.caused_round_submission).toBe(true);
  });

  it.each(["corrupt", "unavailable", "no_storage", "no_lock"] as const)("blocks %s before any transport", async (kind) => {
    const f = fixture();
    if (kind === "corrupt") f.storage.values.set(KEY, "{bad");
    if (kind === "unavailable") f.storage.onGet = () => { throw new Error("storage unavailable"); };
    const store = kind === "no_storage" ? createOpeningCountRecoveryStore({ storage: null, locks: f.locks })
      : kind === "no_lock" ? createOpeningCountRecoveryStore({ storage: f.storage, locks: null }) : f.store;
    await expect(f.submit({ store })).rejects.toThrow();
    expect(f.requester).not.toHaveBeenCalled();
  });

  it.each(["person", "authversion", "inactive", "read", "count"] as const)("blocks initial %s failure without a marker or POST", async (kind) => {
    const f = fixture();
    if (kind === "person") f.handlers.identity = () => ({ ...activeIdentity(), person_id: OTHER_PERSON });
    if (kind === "authversion") f.handlers.identity = () => ({ ...activeIdentity(), authorization_version: 8 });
    if (kind === "inactive") f.handlers.identity = () => ({ ...activeIdentity(), account_status: "disabled" });
    if (kind === "read" || kind === "count") f.handlers.access = () => ({ ...access(), permissions: access().permissions.filter((p) => p.action !== kind) });
    await expect(f.submit()).rejects.toThrow();
    expect(f.counts.post).toBe(0);
    expect(f.storage.values.size).toBe(0);
  });

  it.each(["identity", "access"] as const)("rechecks %s immediately before persistence", async (kind) => {
    const f = fixture();
    f.handlers[kind] = (n) => kind === "identity"
      ? { ...activeIdentity(), person_id: n === 1 ? PERSON : OTHER_PERSON }
      : { ...access(), authorization_version: n === 1 ? 7 : 8 };
    await expect(f.submit()).rejects.toThrow();
    expect(f.counts.post).toBe(0);
    expect(f.storage.values.size).toBe(0);
  });

  it("never clears another actor's pending command", async () => {
    const f = fixture(true);
    await expect(f.submit({ expectedIdentity: { ...identity, person_id: OTHER_PERSON } })).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.storage.values.get(KEY)).toBe(JSON.stringify(originalSentinel));
    expect(f.counts.post).toBe(0);
  });

  it("retains the sentinel when identity changes only after the POST", async () => {
    const f = fixture();
    f.handlers.identity = (n) => ({ ...activeIdentity(), authorization_version: n <= 2 ? 7 : 8 });
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.counts.post).toBe(1);
    expect(f.store.read(TASK).kind).toBe("valid");
  });

  it.each(["task", "version", "round", "scope", "assignment", "completed", "action"] as const)("rejects a fresh detail with invalid %s", async (kind) => {
    const f = fixture();
    f.handlers.detail = () => {
      const value = before();
      if (kind === "task") value.task_id = OTHER_PERSON;
      if (kind === "version") value.task_version = Number.MAX_SAFE_INTEGER + 1;
      if (kind === "round") value.current_round.round_type = "recount";
      if (kind === "scope") value.scopes[0].scope_id = OTHER_SCOPE;
      if (kind === "assignment") value.scopes[0].assigned_to_me = false;
      if (kind === "completed") { value.scopes[0].completion_status = "completed"; Object.assign(value.scopes[0], { completed_at: COMPLETED }); }
      if (kind === "action") value.allowed_actions = [];
      return value;
    };
    await expect(f.submit()).rejects.toThrow();
    expect(f.counts.post).toBe(0);
    expect(f.storage.values.size).toBe(0);
  });

  it("serializes and freezes the body before the second identity request", async () => {
    const f = fixture();
    const input = { physical_observations: [{ material_identifier_type: "unknown" as const,
      material_identifier_raw: "SECRET-MATERIAL", condition_code: "new" as const,
      availability_bucket: "available" as const, counted_qty: "2.000" }], zero_confirmed: false };
    const expectedBody = JSON.stringify(input);
    f.handlers.identity = (n) => { if (n === 2) input.physical_observations[0].counted_qty = "999.000"; return activeIdentity(); };
    await f.submit({ input });
    expect(f.calls.find((row) => row.kind === "post")?.init?.body).toBe(expectedBody);
  });

  it("finishes JSON serialization before persistence and sends nothing if serialization fails", async () => {
    const f = fixture();
    const input = { ...emptyInput(), toJSON() { throw new Error("SECRET-FORM"); } };
    const error = await f.submit({ input }).catch((value: unknown) => value);
    expect(String(error)).not.toContain("SECRET");
    expect(f.storage.values.size).toBe(0);
    expect(f.counts.post).toBe(0);
  });

  it("revalidates toJSON output instead of validating one body and sending another", async () => {
    const f = fixture();
    const input = { ...emptyInput(), toJSON() { return { ...emptyInput(), zero_confirmed: false }; } };
    await expect(f.submit({ input })).rejects.toThrow();
    expect(f.counts.post).toBe(0);
    expect(f.storage.values.size).toBe(0);
  });

  it("blocks page changes before persistence", async () => {
    const f = fixture();
    await expect(f.submit({ canCommit: () => false })).rejects.toThrow();
    expect(f.storage.values.size).toBe(0);
    expect(f.counts.post).toBe(0);
  });

  it("retains a marker when the page changes exactly at persistence without sending", async () => {
    const f = fixture();
    let current = true;
    f.storage.onSet = () => { current = false; };
    await expect(f.submit({ canCommit: () => current })).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(0);
  });

  it("blocks page changes during recovery without clearing", async () => {
    const f = fixture();
    await expect(f.submit({ canCommit: () => f.counts.post === 0 })).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.store.read(TASK).kind).toBe("valid");
    expect(f.counts.post).toBe(1);
  });

  it.each(["write", "reread", "remove"] as const)("fails closed on storage %s errors", async (kind) => {
    const f = fixture();
    if (kind === "write") f.storage.onSet = () => { throw new Error("storage write failed"); };
    if (kind === "reread") f.storage.onSet = () => { f.storage.onGet = () => { throw new Error("reread failed"); }; };
    if (kind === "remove") f.storage.onRemove = () => { throw new Error("clear failed"); };
    await expect(f.submit()).rejects.toThrow();
    expect(f.counts.post).toBe(kind === "remove" ? 1 : 0);
    expect(f.store.read(TASK).kind).toBe("unavailable");
    await expect(f.submit()).rejects.toThrow();
    expect(f.counts.post).toBe(kind === "remove" ? 1 : 0);
  });

  it("preserves pending evidence if an exact first rejection cannot be cleared", async () => {
    const f = fixture();
    f.handlers.post = () => { throw rejected(412, "precondition_failed", "opening_count_state_invalid"); };
    f.storage.onRemove = () => { throw new Error("clear failed"); };
    await expect(f.submit()).rejects.toBeInstanceOf(OpeningCountSubmissionPendingError);
    expect(f.storage.values.size).toBe(1);
    expect(f.counts.post).toBe(1);
  });

  it("holds the native lock through recovery and rejects a second instance without queuing", async () => {
    const f = fixture();
    const started = deferred(), finish = deferred();
    f.handlers.post = async () => { started.resolve(); await finish.promise; return postResult(); };
    const first = f.submit();
    await started.promise;
    await expect(f.submit({ store: f.makeStore() })).rejects.toThrow(/其他页面/);
    expect(f.counts.post).toBe(1);
    finish.resolve();
    await expect(first).resolves.toMatchObject({ recovered: false });
    expect(f.locks.held.size).toBe(0);
  });

  it("does not turn a concurrent second instance into a new POST after the first is rejected", async () => {
    const f = fixture();
    const started = deferred(), finish = deferred();
    f.handlers.post = async () => { started.resolve(); await finish.promise; throw rejected(412, "precondition_failed", "opening_count_state_invalid"); };
    const first = f.submit().catch((error: unknown) => error);
    await started.promise;
    await expect(f.submit({ store: f.makeStore() })).rejects.toThrow();
    finish.resolve();
    expect(await first).toBeInstanceOf(ApiError);
    expect(f.counts.post).toBe(1);
    expect(f.store.read(TASK).kind).toBe("missing");
  });

  it("keeps the native task lock while the successful POST is still awaiting historical proof", async () => {
    const f = fixture();
    const started = deferred(), finish = deferred();
    f.handlers.status = async () => { started.resolve(); await finish.promise; return status(f.getTrace()); };
    const first = f.submit();
    await started.promise;
    expect(f.store.read(TASK).kind).toBe("valid");
    await expect(f.submit({ store: f.makeStore() })).rejects.toThrow(/其他页面/);
    expect(f.counts.post).toBe(1);
    finish.resolve();
    await expect(first).resolves.toMatchObject({ recovered: false });
    expect(f.store.read(TASK).kind).toBe("missing");
  });
});
