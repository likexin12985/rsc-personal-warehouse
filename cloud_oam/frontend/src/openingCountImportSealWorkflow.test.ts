import { describe, expect, it, vi } from "vitest";
import { createOpeningImportClient, type OpeningImportSeal } from "./openingCountImportClient";
import { createOpeningImportWorkflow } from "./openingCountImportWorkflow";
import { createOpeningImportRecoveryStore, openingImportSealRecordBinding, validateOpeningImportRecord,
  type OpeningImportRecord, type OpeningImportSealLease, OPENING_IMPORT_SEALABLE_PHASES } from "./openingCountImportRecoveryStore";
import { createOpeningCountRecoveryStore, OPENING_IMPORT_RECORD_PREFIX, withNoPendingOpeningCount,
  type OpeningCountLockManager } from "./openingCountRecoveryStore";

const id = (n: number) => `96000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const original = { person_id: id(4), authorization_version: 7 }, reviewer = { person_id: id(9), authorization_version: 12 };
function fixture(phase: OpeningImportRecord["phase"] = "intent_requested") {
  const record = validateOpeningImportRecord({ v: 1, task_id: id(1), round_id: id(2), scope_id: id(3),
    actor_person_id: original.person_id, actor_authorization_version: original.authorization_version,
    source_sha256: "ab".repeat(32), size_bytes: 12, upload_key: "opening-upload-123456789", import_key: "opening-import-123456789",
    file_id: ["prepared", "intent_requested", "seal_requested"].includes(phase) ? null : id(5),
    job_id: ["job_bound", "confirmation_requested", "cancellation_requested"].includes(phase) ? id(6) : null, phase });
  const storageKey = OPENING_IMPORT_RECORD_PREFIX + record.task_id;
  const values = new Map([[storageKey, JSON.stringify(record)]]), held = new Set<string>();
  const storage = { getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
    removeItem: vi.fn((key: string) => { values.delete(key); }) };
  const locks: OpeningCountLockManager = { async request(name, _options, work) {
    if (held.has(name)) return work(null); held.add(name);
    try { return await work({}); } finally { held.delete(name); }
  } };
  const permissions = { schema_version: "1.0" as const, ...reviewer, can_read: true, can_manage: true, can_count: false,
    can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: false, can_close: false };
  const access = { loadIdentity: vi.fn(async () => reviewer), loadAccess: vi.fn(async () => permissions),
    detail: vi.fn(async (): Promise<never> => { throw new Error("no counting authority"); }) };
  let response: OpeningImportSeal = { ...openingImportSealRecordBinding(record), source_file_id: id(5),
    schema_version: "rsc.opening_import_seal.v1", seal_id: id(11), terminal_audit_id: id(12),
    reviewer_person_id: reviewer.person_id, reviewer_authorization_version: reviewer.authorization_version,
    permanent_nonexecution: true, automatic_retry_allowed: false, source_object_may_exist: true };
  const read = vi.fn(async () => response);
  const write = vi.fn(async (path: string) => {
    expect(path).toBe("/v1/stocktakes/opening/imports/opening-count/command-seals");
    expect(JSON.parse(values.get(storageKey)!).phase).toBe("seal_requested");
    expect(storage.getItem).toHaveBeenCalled();
    throw new Error("COMMIT succeeded, acknowledgement lost");
  });
  const objectFetch = vi.fn(), store = createOpeningImportRecoveryStore({ storage, locks });
  let live = true;
  const dependencies = { store, access, client: createOpeningImportClient({ read, write, objectFetch }), isCurrent: () => live };
  const workflow = createOpeningImportWorkflow(reviewer, dependencies);
  return { record, values, storageKey, storage, locks, store, workflow, dependencies, read, write, objectFetch, access, permissions,
    respond(patch: Partial<OpeningImportSeal>) { response = { ...response, ...patch }; }, leave() { live = false; } };
}

describe("durable one-shot manager seal workflow", () => {
  it.each(OPENING_IMPORT_SEALABLE_PHASES)("recovers a lost seal response from %s and clears only after a fresh proof", async phase => {
    const f = fixture(phase), shown = await f.workflow.requestSeal(f.record.task_id, f.record);
    expect(shown.record.phase).toBe("seal_requested");
    expect(shown.record.file_id).toBe(f.record.file_id);
    expect(shown.proof.actor_person_id).toBe(original.person_id);
    expect(shown.proof.reviewer_person_id).toBe(reviewer.person_id);
    expect(f.storage.removeItem).not.toHaveBeenCalled(); expect(f.write).toHaveBeenCalledTimes(1);
    await expect(withNoPendingOpeningCount(f.record.task_id, async () => true, createOpeningCountRecoveryStore(f))).rejects.toThrow();
    const reload = createOpeningImportWorkflow(reviewer, f.dependencies);
    await expect(reload.requestSeal(f.record.task_id, shown.record)).rejects.toThrow();
    expect(await reload.inspectSeal(f.record.task_id)).toEqual(shown);
    expect(await reload.finishSeal(f.record.task_id, shown)).toEqual(shown.proof);
    expect(f.read).toHaveBeenCalledTimes(3); expect(f.write).toHaveBeenCalledTimes(1);
    expect(f.values.size).toBe(0); expect(f.storage.removeItem).toHaveBeenCalledTimes(1);
    expect(await withNoPendingOpeningCount(f.record.task_id, async () => true, createOpeningCountRecoveryStore(f))).toBe(true);
    expect(f.objectFetch).not.toHaveBeenCalled(); expect(f.access.detail).not.toHaveBeenCalled();
  });

  it.each(["404", "timeout", "403", "409 accepted"])("preserves the durable marker and never retries after %s", async failure => {
    const f = fixture(); f.read.mockRejectedValue(new Error(failure));
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow(failure);
    const persisted = f.values.get(f.storageKey);
    expect(JSON.parse(persisted!).phase).toBe("seal_requested");
    const reload = createOpeningImportWorkflow(reviewer, f.dependencies);
    await expect(reload.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    await expect(reload.inspectSeal(f.record.task_id)).rejects.toThrow(failure);
    expect(f.values.get(f.storageKey)).toBe(persisted);
    expect(f.write).toHaveBeenCalledTimes(1); expect(f.storage.removeItem).not.toHaveBeenCalled();
  });

  it.each(["prepared", "job_bound", "confirmation_requested", "cancellation_requested", "seal_requested"] as const)("never sends a seal for %s", async phase => {
    const f = fixture(phase);
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    expect(f.write).not.toHaveBeenCalled(); expect(f.values.get(f.storageKey)).toBe(JSON.stringify(f.record));
  });

  it.each(["write-throws", "readback-mismatch", "silent-write-loss"])("blocks POST when durable marker storage fails: %s", async failure => {
    const f = fixture();
    f.storage.setItem.mockImplementation((key, value) => {
      if (failure === "write-throws") throw new Error("quota");
      if (failure === "readback-mismatch") f.values.set(key, JSON.stringify({ ...JSON.parse(value), import_key: "different-original-key" }));
    });
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    expect(f.write).not.toHaveBeenCalled(); expect(f.read).not.toHaveBeenCalled();
    expect(f.store.read(f.record.task_id).kind).toBe("unavailable");
  });

  it.each(["identity", "version", "read", "manage", "view", "record"])("rejects changed %s before writing", async failure => {
    const f = fixture();
    if (failure === "identity") f.access.loadIdentity.mockResolvedValue({ ...reviewer, person_id: id(20) });
    if (failure === "version") f.access.loadAccess.mockResolvedValue({ ...f.permissions, authorization_version: 13 });
    if (failure === "read") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_read: false });
    if (failure === "manage") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_manage: false });
    if (failure === "view") f.leave();
    if (failure === "record") f.values.set(f.storageKey, JSON.stringify({ ...f.record, upload_key: "replacement-source-key" }));
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    expect(f.write).not.toHaveBeenCalled(); expect(f.read).not.toHaveBeenCalled(); expect(f.storage.removeItem).not.toHaveBeenCalled();
  });

  it.each(["identity", "permission", "view", "record", "audit", "404"])("does not clear if %s changes during final reread", async failure => {
    const f = fixture(), shown = await f.workflow.requestSeal(f.record.task_id, f.record);
    const get = f.read.getMockImplementation()!;
    f.read.mockImplementation(async () => {
      if (failure === "identity") f.access.loadIdentity.mockResolvedValue({ ...reviewer, authorization_version: 13 });
      if (failure === "permission") f.access.loadAccess.mockResolvedValue({ ...f.permissions, can_manage: false });
      if (failure === "view") f.leave();
      if (failure === "record") f.values.set(f.storageKey, JSON.stringify({ ...shown.record, size_bytes: 99 }));
      if (failure === "audit") f.respond({ terminal_audit_id: id(20) });
      if (failure === "404") throw new Error("404");
      return get();
    });
    await expect(f.workflow.finishSeal(f.record.task_id, shown)).rejects.toThrow();
    expect(f.storage.removeItem).not.toHaveBeenCalled(); expect(f.values.size).toBe(1); expect(f.write).toHaveBeenCalledTimes(1);
  });

  it("uses the original shared task lock and revokes escaped seal leases", async () => {
    const f = fixture();
    await f.store.withTaskLease(f.record.task_id, original, async () => {
      await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    });
    expect(f.write).not.toHaveBeenCalled();
    let escaped!: OpeningImportSealLease;
    await f.store.withSealLease(f.record.task_id, reviewer, async lease => { escaped = lease; });
    expect(() => escaped.request(f.record)).toThrow();
    expect(() => escaped.read()).toThrow();
  });

  it("does not let the original owner resume a sealed request or emit another upload", async () => {
    const f = fixture(); await f.workflow.requestSeal(f.record.task_id, f.record);
    const owner = createOpeningImportWorkflow(original, f.dependencies);
    await expect(owner.resume(f.record.task_id)).rejects.toThrow(/永久停止/);
    expect(f.write).toHaveBeenCalledTimes(1); expect(f.objectFetch).not.toHaveBeenCalled();
  });

  it("retains an accepted job and uses positive management terminal evidence to clear the marker", async () => {
    const f = fixture("job_requested"); f.read.mockRejectedValueOnce(new Error("409 accepted"));
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    const positive = { ...openingImportSealRecordBinding(f.record), schema_version: "rsc.opening_import_management_recovery.v1",
      reviewer_person_id: reviewer.person_id, reviewer_authorization_version: reviewer.authorization_version,
      job_id: id(6), status: "succeeded", completion_id: id(15), terminal_audit_id: id(16), terminal_verified: true, automatic_retry_allowed: false };
    f.read.mockResolvedValue(positive as unknown as OpeningImportSeal);
    const shown = await f.workflow.inspectManagement(f.record.task_id);
    expect(shown.review.status).toBe("succeeded"); expect(f.values.size).toBe(1);
    await f.workflow.finishManagement(f.record.task_id, shown);
    expect(f.values.size).toBe(0); expect(f.write).toHaveBeenCalledTimes(1);
  });

  it("binds an accepted active job for original-owner cancellation while retaining the block", async () => {
    const f = fixture("job_requested"); f.read.mockRejectedValueOnce(new Error("409 accepted"));
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    const positive = { ...openingImportSealRecordBinding(f.record), schema_version: "rsc.opening_import_management_recovery.v1",
      reviewer_person_id: reviewer.person_id, reviewer_authorization_version: reviewer.authorization_version,
      job_id: id(6), status: "awaiting_confirmation", completion_id: null, terminal_audit_id: null,
      terminal_verified: false, automatic_retry_allowed: false };
    f.read.mockResolvedValue(positive as unknown as OpeningImportSeal);
    const shown = await f.workflow.inspectManagement(f.record.task_id);
    const bound = await f.workflow.bindAcceptedSeal(f.record.task_id, shown);
    expect(bound.phase).toBe("seal_conflict"); expect(bound.job_id).toBe(id(6)); expect(bound.file_id).toBe(id(5));
    expect(f.values.size).toBe(1); expect(f.storage.removeItem).not.toHaveBeenCalled(); expect(f.write).toHaveBeenCalledTimes(1);
    await expect(f.workflow.requestSeal(f.record.task_id, bound)).rejects.toThrow();
    await expect(withNoPendingOpeningCount(f.record.task_id, async () => true, createOpeningCountRecoveryStore(f))).rejects.toThrow();
    expect(f.write).toHaveBeenCalledTimes(1);
  });

  it.each(["record", "identity", "job", "status"])("refuses accepted-job binding if %s changes after inspection", async failure => {
    const f = fixture("job_requested"); f.read.mockRejectedValueOnce(new Error("409 accepted"));
    await expect(f.workflow.requestSeal(f.record.task_id, f.record)).rejects.toThrow();
    const positive = { ...openingImportSealRecordBinding(f.record), schema_version: "rsc.opening_import_management_recovery.v1",
      reviewer_person_id: reviewer.person_id, reviewer_authorization_version: reviewer.authorization_version,
      job_id: id(6), status: "queued", completion_id: null, terminal_audit_id: null, terminal_verified: false, automatic_retry_allowed: false };
    f.read.mockResolvedValue(positive as unknown as OpeningImportSeal);
    const shown = await f.workflow.inspectManagement(f.record.task_id);
    if (failure === "record") f.values.set(f.storageKey, JSON.stringify({ ...shown.record, import_key: "different-import-key" }));
    if (failure === "identity") f.access.loadIdentity.mockResolvedValue({ ...reviewer, authorization_version: 13 });
    if (failure === "job") f.read.mockResolvedValue({ ...positive, job_id: id(20) } as unknown as OpeningImportSeal);
    if (failure === "status") f.read.mockResolvedValue({ ...positive, status: "prevalidating" } as unknown as OpeningImportSeal);
    await expect(f.workflow.bindAcceptedSeal(f.record.task_id, shown)).rejects.toThrow();
    expect(JSON.parse(f.values.get(f.storageKey)!).phase).toBe("seal_requested");
    expect(f.write).toHaveBeenCalledTimes(1); expect(f.storage.removeItem).not.toHaveBeenCalled();
  });

  it("latches storage failure after attempted cleanup instead of claiming success", async () => {
    const f = fixture(), shown = await f.workflow.requestSeal(f.record.task_id, f.record);
    f.storage.removeItem.mockImplementation(() => {});
    await expect(f.workflow.finishSeal(f.record.task_id, shown)).rejects.toThrow();
    expect(f.store.read(f.record.task_id).kind).toBe("unavailable"); expect(f.values.size).toBe(1);
  });
});
