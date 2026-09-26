import { describe, expect, it } from "vitest";
import { createOpeningImportRecoveryStore, validateOpeningImportRecord, type OpeningImportLease,
  type OpeningImportRecord } from "./openingCountImportRecoveryStore";
import { createOpeningCountRecoveryStore, OPENING_IMPORT_RECORD_PREFIX, withNoPendingOpeningCount,
  type OpeningCountLockManager } from "./openingCountRecoveryStore";
import type { OpeningImportStatus } from "./openingCountImportClient";

const id = (n: number) => `90000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const record: OpeningImportRecord = { v: 1, task_id: id(1), round_id: id(2), scope_id: id(3),
  actor_person_id: id(4), actor_authorization_version: 7, source_sha256: "ab".repeat(32), size_bytes: 4,
  upload_key: "opening-upload-" + "aa".repeat(18), import_key: "opening-import-" + "bb".repeat(18),
  file_id: null, job_id: null, phase: "prepared" };
const actor = { person_id: record.actor_person_id, authorization_version: record.actor_authorization_version };
const count = { v: 1 as const, kind: "opening_scope_count" as const, task_id: record.task_id,
  round_id: record.round_id, round_no: 1, scope_id: record.scope_id, actor_person_id: record.actor_person_id,
  actor_authorization_version: record.actor_authorization_version, trace_request_id: "original-count-trace-123" };
function fixture() {
  const values = new Map<string, string>(), held = new Set<string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); } };
  const locks: OpeningCountLockManager = { async request(name, options, callback) {
    expect(options).toEqual({ mode: "exclusive", ifAvailable: true });
    if (held.has(name)) return callback(null);
    held.add(name); try { return await callback({ name }); } finally { held.delete(name); }
  } };
  return { values, storage, locks };
}
function advance(lease: OpeningImportLease, before: OpeningImportRecord, phase: OpeningImportRecord["phase"], file_id = before.file_id, job_id = before.job_id) {
  return lease.advance(before, { phase, file_id, job_id });
}
function terminal(status: "succeeded" | "failed" | "cancelled"): OpeningImportStatus {
  return { job_id: id(6), status, completion_id: status === "succeeded" ? id(7) : null,
    row_count: 2, error_count: 0, error_file_available: false, replayed: true,
    failure_code: status === "cancelled" ? "opening_import_cancelled" : status === "failed" ? "opening_import_context_changed" : null };
}
function toJob(lease: OpeningImportLease) {
  lease.create(record);
  let next = advance(lease, record, "intent_requested");
  next = advance(lease, next, "source_available", id(5));
  next = advance(lease, next, "job_requested");
  return advance(lease, next, "job_bound", id(5), id(6));
}

describe("durable opening import recovery coordinates", () => {
  it("discards only an exact unsent draft under the shared task lease", async () => {
    const f = fixture(), store = createOpeningImportRecoveryStore(f);
    let escaped!: OpeningImportLease;
    await store.withTaskLease(record.task_id, actor, async lease => { lease.create(record); escaped = lease; });
    expect(() => escaped.discardPrepared(record)).toThrow();
    for (const changed of [{ ...actor, person_id: id(9) }, { ...actor, authorization_version: 8 }]) {
      await expect(store.withTaskLease(record.task_id, changed, async lease => lease.discardPrepared(record))).rejects.toThrow();
    }
    await store.withTaskLease(record.task_id, actor, async lease => {
      expect(() => lease.discardPrepared({ ...record, scope_id: id(9) })).toThrow();
      lease.discardPrepared(record);
    });
    expect(store.read(record.task_id)).toEqual({ kind: "missing" });
    expect(await withNoPendingOpeningCount(record.task_id, async () => true, createOpeningCountRecoveryStore(f))).toBe(true);
  });
  it("cannot discard any requested phase or use a stale prepared snapshot", async () => {
    const f = fixture(), store = createOpeningImportRecoveryStore(f);
    await store.withTaskLease(record.task_id, actor, async lease => {
      lease.create(record);
      let next = advance(lease, record, "intent_requested");
      expect(() => lease.discardPrepared(record)).toThrow();
      const reject = () => { expect(() => lease.discardPrepared(next)).toThrow(); expect(lease.read()).toEqual({ kind: "valid", value: next }); };
      reject();
      next = advance(lease, next, "source_bound", id(5)); reject();
      next = advance(lease, next, "upload_started"); reject();
      next = advance(lease, next, "source_available"); reject();
      next = advance(lease, next, "job_requested"); reject();
      next = advance(lease, next, "job_bound", id(5), id(6)); reject();
      next = advance(lease, next, "confirmation_requested"); reject();
      next = advance(lease, next, "cancellation_requested"); reject();
    });
  });
  it("failed draft deletion is retained and latches the store", async () => {
    const f = fixture(), store = createOpeningImportRecoveryStore(f);
    await store.withTaskLease(record.task_id, actor, async lease => {
      lease.create(record);
      f.storage.removeItem = () => {};
      expect(() => lease.discardPrepared(record)).toThrow();
      expect(() => lease.discardPrepared(record)).toThrow();
    });
    expect(store.read(record.task_id)).toEqual({ kind: "unavailable" });
    expect(createOpeningImportRecoveryStore(f).read(record.task_id)).toEqual({ kind: "valid", value: record });
  });
  it("retains opaque original coordinates across restart, with no file contents or signed URLs", async () => {
    const f = fixture();
    const store = createOpeningImportRecoveryStore(f);
    await store.withTaskLease(record.task_id, actor, async lease => lease.create(record));
    expect(createOpeningImportRecoveryStore(f).read(record.task_id)).toEqual({ kind: "valid", value: record });
    expect([...f.values.values()].join()).not.toMatch(/filename|password|token|cookie|https|preview|mobile|body/);
    await expect(store.withTaskLease(record.task_id, actor, async lease => lease.create(record))).rejects.toThrow();
  });
  it.each(["body", "filename", "download_url", "token", "preview"])("refuses extra %s data", key => {
    expect(() => validateOpeningImportRecord({ ...record, [key]: "private" })).toThrow();
  });
  it.each([
    { phase: "upload_started" }, { phase: "job_bound", file_id: id(5) }, { v: 2 },
    { actor_authorization_version: 0 }, { file_id: id(5) }, { size_bytes: 0 },
    { upload_key: record.import_key }, { job_id: id(6) },
  ])("rejects malformed phase and binding %#", change => {
    expect(() => validateOpeningImportRecord({ ...record, ...change })).toThrow();
  });
  it("marks upload before I/O and refuses a second upload after restart", async () => {
    const f = fixture();
    const store = createOpeningImportRecoveryStore(f);
    let bound!: OpeningImportRecord;
    await store.withTaskLease(record.task_id, actor, async lease => {
      lease.create(record);
      bound = advance(lease, advance(lease, record, "intent_requested"), "source_bound", id(5));
      advance(lease, bound, "upload_started");
    });
    const restarted = createOpeningImportRecoveryStore(f);
    await expect(restarted.withTaskLease(record.task_id, actor, async lease => {
      advance(lease, bound, "upload_started");
    })).rejects.toThrow();
    await restarted.withTaskLease(record.task_id, actor, async lease => {
      const current = lease.read(); if (current.kind !== "valid") throw new Error("missing");
      expect(current.value.phase).toBe("upload_started");
      expect(() => advance(lease, current.value, "source_bound")).toThrow();
      expect(advance(lease, current.value, "source_available").file_id).toBe(id(5));
    });
  });
  it("preserves pending confirmation after restart and does not grant replay permission", async () => {
    const f = fixture();
    let pending!: OpeningImportRecord;
    await createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async lease => {
      pending = advance(lease, toJob(lease), "confirmation_requested");
    });
    await createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async lease => {
      expect(() => advance(lease, pending, "confirmation_requested")).toThrow();
      expect(() => advance(lease, pending, "job_bound")).toThrow();
      expect(() => lease.clearTerminal(pending, { ...terminal("succeeded"), status: "awaiting_confirmation", completion_id: null })).toThrow();
      expect(advance(lease, pending, "cancellation_requested").phase).toBe("cancellation_requested");
    });
  });
  it("refuses changed file/job/identity/keys and arbitrary patch fields", async () => {
    const f = fixture();
    await createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async lease => {
      const job = toJob(lease);
      for (const bad of [{ ...job, import_key: record.upload_key }, { ...job, scope_id: id(9) },
        { ...job, round_id: id(9) }, { ...job, actor_person_id: id(9) }, { ...job, actor_authorization_version: 8 }]) {
        expect(() => advance(lease, bad, "confirmation_requested")).toThrow();
      }
      expect(() => advance(lease, job, "confirmation_requested", id(9))).toThrow();
      expect(() => advance(lease, job, "confirmation_requested", id(5), id(9))).toThrow();
      expect(() => lease.advance(job, { phase: "confirmation_requested", file_id: id(5), source_sha256: "aa".repeat(32) } as never)).toThrow();
    });
  });
  it("old identity cannot update or clear another person's record", async () => {
    const f = fixture(), store = createOpeningImportRecoveryStore(f);
    let job!: OpeningImportRecord;
    await store.withTaskLease(record.task_id, actor, async lease => { job = toJob(lease); });
    for (const changed of [{ ...actor, person_id: id(9) }, { ...actor, authorization_version: 8 }]) {
      await store.withTaskLease(record.task_id, changed, async lease => {
        expect(() => advance(lease, job, "confirmation_requested")).toThrow();
        expect(() => lease.clearTerminal(job, terminal("succeeded"))).toThrow();
      });
    }
    expect(store.read(record.task_id)).toEqual({ kind: "valid", value: job });
  });
  it.each(["succeeded", "failed", "cancelled"] as const)("clears only exact observed %s, never an unknown or foreign result", async status => {
    const f = fixture(), store = createOpeningImportRecoveryStore(f);
    await store.withTaskLease(record.task_id, actor, async lease => {
      const job = toJob(lease);
      expect(() => lease.clearTerminal(job, { ...terminal(status), job_id: id(9) })).toThrow();
      lease.clearTerminal(job, terminal(status));
      expect(lease.read()).toEqual({ kind: "missing" });
    });
  });
  it("unresolved import prevents manual count and all guarded commands, including corrupt records", async () => {
    for (const raw of [JSON.stringify(record), "{broken"]) {
      const f = fixture(), counts = createOpeningCountRecoveryStore(f);
      f.values.set(OPENING_IMPORT_RECORD_PREFIX + record.task_id, raw);
      await expect(counts.withTaskLease(record.task_id, async lease => lease.persist(count))).rejects.toThrow("导入");
      await expect(withNoPendingOpeningCount(record.task_id, async () => { throw new Error("must not execute"); }, counts)).rejects.toThrow("导入");
      expect(f.values.get(OPENING_IMPORT_RECORD_PREFIX + record.task_id)).toBe(raw);
    }
  });
  it("pending manual count prevents import creation and preserves its history", async () => {
    const f = fixture();
    await createOpeningCountRecoveryStore(f).withTaskLease(record.task_id, async lease => lease.persist(count));
    await expect(createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async lease => lease.create(record))).rejects.toThrow();
    expect(f.values.size).toBe(1);
  });
  it("shares the same native lock with manual counting while independent tasks remain available", async () => {
    const f = fixture(), imports = createOpeningImportRecoveryStore(f), counts = createOpeningCountRecoveryStore(f);
    await imports.withTaskLease(record.task_id, actor, async () => {
      await expect(withNoPendingOpeningCount(record.task_id, async () => true, counts)).rejects.toThrow("其他页面");
      expect(await withNoPendingOpeningCount(id(9), async () => true, counts)).toBe(true);
      await expect(createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async () => true)).rejects.toThrow("其他页面");
    });
  });
  it("readback failure latches the store and never permits later writes", async () => {
    const f = fixture();
    f.storage.setItem = () => { /* dropped write */ };
    const store = createOpeningImportRecoveryStore(f);
    await store.withTaskLease(record.task_id, actor, async lease => {
      expect(() => lease.create(record)).toThrow();
      expect(() => lease.create(record)).toThrow();
    });
    expect(store.read(record.task_id)).toEqual({ kind: "unavailable" });
  });
  it("denies escaped leases and unavailable storage or Web Locks", async () => {
    const f = fixture(); let escaped!: OpeningImportLease;
    await createOpeningImportRecoveryStore(f).withTaskLease(record.task_id, actor, async lease => { escaped = lease; });
    expect(() => escaped.create(record)).toThrow();
    for (const options of [{ ...f, storage: null }, { ...f, locks: null }]) {
      await expect(createOpeningImportRecoveryStore(options).withTaskLease(record.task_id, actor, async lease => lease.create(record))).rejects.toThrow();
    }
  });
  it("preserves corrupt or other-task records without auto-clear", async () => {
    for (const raw of ["{bad", JSON.stringify({ ...record, task_id: id(9) })]) {
      const f = fixture(); f.values.set(OPENING_IMPORT_RECORD_PREFIX + record.task_id, raw);
      const store = createOpeningImportRecoveryStore(f);
      expect(store.read(record.task_id)).toEqual({ kind: "corrupt" });
      await expect(store.withTaskLease(record.task_id, actor, async lease => lease.create(record))).rejects.toThrow();
      expect(f.values.get(OPENING_IMPORT_RECORD_PREFIX + record.task_id)).toBe(raw);
    }
  });
});
