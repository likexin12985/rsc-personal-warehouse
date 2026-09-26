import { describe, expect, it, vi } from "vitest";
import { createOpeningImportClient, type OpeningImportManagementReview } from "./openingCountImportClient";
import { createOpeningImportWorkflow } from "./openingCountImportWorkflow";
import { createOpeningImportRecoveryStore, openingImportRecordBinding, validateOpeningImportRecord,
  type OpeningImportManagementLease, type OpeningImportRecord } from "./openingCountImportRecoveryStore";
import { createOpeningCountRecoveryStore, OPENING_IMPORT_RECORD_PREFIX, withNoPendingOpeningCount,
  type OpeningCountLockManager } from "./openingCountRecoveryStore";

const id = (n: number) => `94000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const original = { person_id: id(4), authorization_version: 7 };
const reviewer = { person_id: id(9), authorization_version: 12 };
const record: OpeningImportRecord = validateOpeningImportRecord({ v: 1, task_id: id(1), round_id: id(2), scope_id: id(3),
  actor_person_id: original.person_id, actor_authorization_version: original.authorization_version,
  source_sha256: "ab".repeat(32), size_bytes: 12, upload_key: "opening-upload-123456789", import_key: "opening-import-123456789",
  file_id: id(5), job_id: id(6), phase: "confirmation_requested" });
const storageKey = OPENING_IMPORT_RECORD_PREFIX + record.task_id;
function fixture(phase: OpeningImportRecord["phase"] = record.phase) {
  const current = validateOpeningImportRecord({ ...record, phase,
    file_id: ["prepared", "intent_requested"].includes(phase) ? null : record.file_id,
    job_id: ["job_bound", "confirmation_requested", "cancellation_requested"].includes(phase) ? record.job_id : null });
  const values = new Map([[storageKey, JSON.stringify(current)]]), held = new Set<string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: vi.fn((key: string) => { values.delete(key); }) };
  const locks: OpeningCountLockManager = { async request(name, _options, work) {
    if (held.has(name)) return work(null); held.add(name);
    try { return await work({}); } finally { held.delete(name); }
  } };
  const permissions = { schema_version: "1.0" as const, ...reviewer, can_read: true, can_manage: true, can_count: false,
    can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: false, can_close: false };
  const access = { loadIdentity: vi.fn(async () => reviewer), loadAccess: vi.fn(async () => permissions),
    detail: vi.fn(async (): Promise<never> => { throw new Error("Management must use scope-authorized inspection"); }) };
  let response: OpeningImportManagementReview = { ...openingImportRecordBinding(record),
    schema_version: "rsc.opening_import_management_recovery.v1", reviewer_person_id: reviewer.person_id,
    reviewer_authorization_version: reviewer.authorization_version, job_id: id(6), status: "succeeded",
    completion_id: id(7), terminal_audit_id: id(8), terminal_verified: true, automatic_retry_allowed: false };
  const read = vi.fn(async (path: string, init?: RequestInit) => {
    expect(path).toContain("/jobs/management-recovery?");
    const url = new URL(path, "https://example.test");
    expect(Object.fromEntries(url.searchParams)).toEqual({ task_id: record.task_id, round_id: record.round_id,
      scope_id: record.scope_id, source_file_id: record.file_id });
    expect(init?.cache).toBe("no-store");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(record.import_key);
    return response;
  });
  const write = vi.fn(async (): Promise<never> => { throw new Error("No business writes allowed"); });
  const objectFetch = vi.fn(async (): Promise<never> => { throw new Error("No private object access allowed"); });
  const store = createOpeningImportRecoveryStore({ storage, locks });
  let live = true;
  const workflow = createOpeningImportWorkflow(reviewer, { store, access, client: createOpeningImportClient({ read, write, objectFetch }), isCurrent: () => live });
  return { values, storage, locks, store, workflow, read, write, objectFetch, access, permissions, current,
    respond(patch: Partial<OpeningImportManagementReview>) { response = { ...response, ...patch }; },
    leave() { live = false; },
    expectNoWrites() { expect(write).not.toHaveBeenCalled(); expect(objectFetch).not.toHaveBeenCalled(); expect(access.detail).not.toHaveBeenCalled(); } };
}

describe("management inspection and exact terminal recovery with real protocol and shared storage", () => {
  it.each(["succeeded", "failed", "cancelled"] as const)("separates the reviewer from the original user and explicitly clears verified %s only", async status => {
    const f = fixture(); f.respond({ status, completion_id: status === "succeeded" ? id(7) : null });
    const shown = await f.workflow.inspectManagement(record.task_id);
    expect(shown.record.actor_person_id).toBe(original.person_id); expect(shown.review.reviewer_person_id).toBe(reviewer.person_id);
    expect(f.values.size).toBe(1); expect(f.storage.removeItem).not.toHaveBeenCalled();
    await expect(withNoPendingOpeningCount(record.task_id, async () => true, createOpeningCountRecoveryStore(f))).rejects.toThrow();
    await expect(f.workflow.resume(record.task_id)).rejects.toThrow();
    expect((await f.workflow.finishManagement(record.task_id, shown)).status).toBe(status);
    expect(f.read).toHaveBeenCalledTimes(2); expect(f.access.loadIdentity).toHaveBeenCalledTimes(4);
    expect(f.storage.removeItem).toHaveBeenCalledTimes(1); expect(f.values.size).toBe(0);
    expect(await withNoPendingOpeningCount(record.task_id, async () => true, createOpeningCountRecoveryStore(f))).toBe(true);
    f.expectNoWrites();
  });
  it("recovers an acknowledged-lost original job without binding or replaying a POST", async () => {
    const f = fixture("job_requested"), shown = await f.workflow.inspectManagement(record.task_id);
    expect(shown.record.job_id).toBeNull(); expect(shown.review.job_id).toBe(id(6));
    expect(f.values.get(storageKey)).toBe(JSON.stringify(f.current));
    await f.workflow.finishManagement(record.task_id, shown);
    expect(f.values.size).toBe(0); f.expectNoWrites();
  });
  it.each(["queued", "prevalidating", "awaiting_confirmation"] as const)("does not clear %s or grant retry", async status => {
    const f = fixture(); f.respond({ status, completion_id: null, terminal_audit_id: null, terminal_verified: false });
    const shown = await f.workflow.inspectManagement(record.task_id);
    await expect(f.workflow.finishManagement(record.task_id, shown)).rejects.toThrow();
    expect(f.values.size).toBe(1); expect(f.storage.removeItem).not.toHaveBeenCalled(); f.expectNoWrites();
  });
  it.each(["prepared", "intent_requested", "source_bound", "upload_started", "source_available"] as const)("never treats %s as a terminal job", async phase => {
    const f = fixture(phase);
    await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow();
    expect(f.read).not.toHaveBeenCalled(); expect(f.values.size).toBe(1); f.expectNoWrites();
  });
  it.each(["missing", "timeout", "forbidden"])("keeps the exact record after %s instead of inferring non-existence", async fault => {
    const f = fixture(); f.read.mockRejectedValue(new Error(fault));
    await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow();
    expect(f.values.get(storageKey)).toBe(JSON.stringify(f.current)); f.expectNoWrites();
  });
  it.each(["person", "version", "read", "manage", "view"])("refuses stale %s before management GET", async fault => {
    const f = fixture();
    if (fault === "person") f.access.loadIdentity.mockResolvedValue({ ...reviewer, person_id: id(10) });
    if (fault === "version") f.access.loadAccess.mockResolvedValue({ ...f.permissions, authorization_version: 13 });
    if (fault === "read") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_read: false });
    if (fault === "manage") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_manage: false });
    if (fault === "view") f.leave();
    await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow();
    expect(f.read).not.toHaveBeenCalled(); expect(f.values.size).toBe(1); f.expectNoWrites();
  });
  it.each(["identity", "access", "view", "record", "audit", "404"])("rechecks the displayed proof and retains the record if %s changes during finish", async fault => {
    const f = fixture(), shown = await f.workflow.inspectManagement(record.task_id);
    const get = f.read.getMockImplementation()!;
    f.read.mockImplementation(async (...args) => {
      if (fault === "identity") f.access.loadIdentity.mockResolvedValue({ ...reviewer, authorization_version: 13 });
      if (fault === "access") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_manage: false });
      if (fault === "view") f.leave();
      if (fault === "record") f.values.set(storageKey, JSON.stringify({ ...record, import_key: "opening-import-replacement" }));
      if (fault === "audit") f.respond({ terminal_audit_id: id(10) });
      if (fault === "404") throw new Error("not found");
      return get(...args);
    });
    await expect(f.workflow.finishManagement(record.task_id, shown)).rejects.toThrow();
    expect(f.storage.removeItem).not.toHaveBeenCalled(); expect(f.values.size).toBe(1); f.expectNoWrites();
  });
  it("serializes manager inspection with manual counting and original import leases", async () => {
    const f = fixture();
    await f.store.withTaskLease(record.task_id, original, async () => {
      await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow();
    });
    expect(f.read).not.toHaveBeenCalled();
    await f.store.withManagementLease(record.task_id, reviewer, async () => {
      await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow();
      await expect(withNoPendingOpeningCount(record.task_id, async () => true, createOpeningCountRecoveryStore(f))).rejects.toThrow();
    });
    expect(f.storage.removeItem).not.toHaveBeenCalled();
  });
  it("cannot reuse an escaped management lease or substitute a mismatched proof", async () => {
    const f = fixture(), shown = await f.workflow.inspectManagement(record.task_id);
    let escaped!: OpeningImportManagementLease;
    await f.store.withManagementLease(record.task_id, reviewer, async lease => {
      escaped = lease;
      expect(Object.keys(lease).sort()).toEqual(["clearTerminal", "read"]);
      expect(() => lease.clearTerminal(shown.record, { ...shown.review, source_sha256: "cd".repeat(32) })).toThrow();
      expect(() => lease.clearTerminal(shown.record, { ...shown.review, reviewer_authorization_version: 13 })).toThrow();
    });
    expect(() => escaped.clearTerminal(shown.record, shown.review)).toThrow();
    expect(() => escaped.read()).toThrow(); expect(f.values.size).toBe(1);
  });
  it.each(["noop", "throw"])("latches failed local removal (%s) and does not report unblocked", async fault => {
    const f = fixture(), shown = await f.workflow.inspectManagement(record.task_id);
    f.storage.removeItem.mockImplementation(() => { if (fault === "throw") throw new Error("storage denied"); });
    await expect(f.workflow.finishManagement(record.task_id, shown)).rejects.toThrow();
    expect(f.store.read(record.task_id)).toEqual({ kind: "unavailable" }); expect(f.values.size).toBe(1);
    await expect(f.workflow.inspectManagement(record.task_id)).rejects.toThrow(); f.expectNoWrites();
  });
});
