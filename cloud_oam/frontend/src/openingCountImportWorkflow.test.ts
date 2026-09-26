import { describe, expect, it, vi } from "vitest";
import { createOpeningImportWorkflow } from "./openingCountImportWorkflow";
import { createOpeningImportClient, type OpeningImportStatus } from "./openingCountImportClient";
import { createOpeningImportRecoveryStore } from "./openingCountImportRecoveryStore";
import { createOpeningCountRecoveryStore, withNoPendingOpeningCount, type OpeningCountLockManager } from "./openingCountRecoveryStore";
import { validateOpeningStocktakeTaskDetail } from "./formalOpeningStocktake";

const id = (n: number) => `91000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const TASK = id(1), ROUND = id(2), SCOPE = id(3), PERSON = id(4), FILE = id(5), JOB = id(6);
const actor = { person_id: PERSON, authorization_version: 7 };
const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
function file() { return Object.assign(new Blob(["PK fixture content"], { type: MIME }), { name: "count.xlsx" }); }
function detail() { return validateOpeningStocktakeTaskDetail({ schema_version: "1.0", task_id: TASK, task_no: "IMPORT-WORKFLOW",
  region_org_id: id(9), status: "counting", blind_count: true, task_version: 3, deadline: null,
  cutoff_at: "2026-09-01T00:00:00Z", current_round: { round_id: ROUND, round_no: 1, round_type: "initial", status: "counting",
    started_at: "2026-09-01T01:00:00Z", submitted_at: null }, evidence_status: "counting_hidden",
  scopes: [{ scope_id: SCOPE, scope_no: 1, location_id: id(8), owner_org_id: id(9), assigned_to_me: true,
    completion_status: "pending", completed_at: null, zero_confirmed: null, count_line_count: null,
    observation_line_count: null, serial_count: null, total_counted_qty: null }], observations: [], differences: [], reviews: [], allowed_actions: ["count"] }); }
function fixture() {
  const values = new Map<string, string>(), held = new Set<string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  const locks: OpeningCountLockManager = { async request(name, _options, work) {
    if (held.has(name)) return work(null); held.add(name);
    try { return await work({}); } finally { held.delete(name); }
  } };
  const access = { loadIdentity: vi.fn(async () => actor), loadAccess: vi.fn(async () => ({ schema_version: "1.0" as const,
    ...actor, can_read: true, can_count: true, can_manage: false, can_review_region: false, can_review_headquarters: false,
    can_post: false, can_reconcile: false, can_close: false })), detail: vi.fn(async () => detail()) };
  let state: OpeningImportStatus = { job_id: JOB, status: "awaiting_confirmation", completion_id: null, row_count: 2,
    error_count: 0, error_file_available: false, failure_code: null, replayed: false };
  let sourceHash = "", requestKey = "", headFailure = false, createFailure = false, confirmFailure = false,
    live = true, corruptReview = false, recoveryMissing = false, commitConfirm = true;
  const writes: string[] = [], reads: string[] = [], events: string[] = [];
  const read = vi.fn(async (path: string) => {
    reads.push(path);
    if (path.endsWith("/capabilities")) return { available: true };
    if (path.endsWith("/recovery")) { if (recoveryMissing) throw new Error("not found"); return { ...state, replayed: true }; }
    if (path.endsWith("/review")) return { job_id: JOB, task_id: corruptReview ? id(10) : TASK, round_id: ROUND,
      scope_id: SCOPE, actor_person_id: PERSON, authorization_version: 7, source_file_id: FILE, source_sha256: sourceHash,
      size_bytes: file().size, original_filename: "count.xlsx", can_confirm: state.status === "awaiting_confirmation", result: state };
    throw new Error(path);
  });
  const write = vi.fn(async (path: string, init?: RequestInit) => {
    writes.push(path);
    if (path.endsWith("/source-upload-intents")) {
      sourceHash = JSON.parse(String(init?.body)).sha256;
      return { file_id: FILE, status: "pending", replayed: false, upload: { method: "PUT", url: "https://private.example.test/upload?signature=opaque",
        expires_at: new Date(Date.now() + 600000).toISOString(), headers: { "Content-Type": MIME, "x-oss-meta-sha256": sourceHash,
          "x-oss-meta-file-id": FILE, "x-oss-forbid-overwrite": "true" } } };
    }
    if (path.endsWith("/complete")) { if (headFailure) throw new Error("HEAD unknown"); return { file_id: FILE, status: "available", replayed: false }; }
    if (path.endsWith("/jobs")) {
      requestKey = new Headers(init?.headers).get("Idempotency-Key")!;
      expect(JSON.parse(String(init?.body))).toEqual({ source_file_id: FILE, task_id: TASK, round_id: ROUND, scope_id: SCOPE });
      if (createFailure) throw new Error("lost create acknowledgement"); return state;
    }
    if (path.endsWith("/confirm")) {
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(requestKey);
      expect(init?.body).toBe("{}");
      if (commitConfirm) state = { ...state, status: "succeeded", completion_id: id(7) };
      if (confirmFailure) throw new Error("lost confirm acknowledgement"); return state;
    }
    if (path.endsWith("/cancel")) { state = { ...state, status: "cancelled", failure_code: "opening_import_cancelled" }; return state; }
    throw new Error(path);
  });
  const objectFetch = vi.fn(async () => {
    const saved = JSON.parse([...values.values()][0]);
    expect(saved.phase).toBe("upload_started"); events.push("put-after-persist");
    throw new Error("lost PUT acknowledgement");
  });
  const client = createOpeningImportClient({ read, write, objectFetch });
  const make = () => createOpeningImportWorkflow(actor, { client, access,
    store: createOpeningImportRecoveryStore({ storage, locks }), isCurrent: () => live });
  return { make, storage, locks, values, access, client, writes, reads, events, objectFetch,
    options(value: { headFailure?: boolean; createFailure?: boolean; confirmFailure?: boolean; live?: boolean; corruptReview?: boolean; recoveryMissing?: boolean; commitConfirm?: boolean }) {
      if (value.headFailure !== undefined) headFailure = value.headFailure;
      if (value.createFailure !== undefined) createFailure = value.createFailure;
      if (value.confirmFailure !== undefined) confirmFailure = value.confirmFailure;
      if (value.live !== undefined) live = value.live;
      if (value.corruptReview !== undefined) corruptReview = value.corruptReview;
      if (value.recoveryMissing !== undefined) recoveryMissing = value.recoveryMissing;
      if (value.commitConfirm !== undefined) commitConfirm = value.commitConfirm;
    } };
}

describe("opening import complete workflow with durable store and real protocol client", () => {
  it("allows only original-task cancellation after an accepted job defeats a stop request", async () => {
    const f = fixture(), owner = f.make();
    await owner.start(TASK, ROUND, SCOPE, file());
    const [key, raw] = [...f.values.entries()][0];
    f.values.set(key, JSON.stringify({ ...JSON.parse(raw), phase: "seal_conflict" }));
    const shown = await f.make().resume(TASK);
    expect(shown.record.phase).toBe("seal_conflict");
    const before = f.writes.length;
    await expect(f.make().decide(TASK, shown.review, "confirm")).rejects.toThrow();
    expect(f.writes).toHaveLength(before);
    const cancelled = await f.make().decide(TASK, shown.review, "cancel");
    expect(cancelled.review.result.status).toBe("cancelled");
    expect(f.writes.slice(before)).toEqual([`/v1/stocktakes/opening/imports/opening-count/jobs/${JOB}/cancel`]);
    await f.make().finish(TASK); expect(f.values.size).toBe(0);
  });
  async function prepareInterruptedDraft() {
    const f = fixture();
    f.access.detail.mockResolvedValueOnce(detail()).mockRejectedValueOnce(new Error("detail unavailable before intent"));
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    expect(JSON.parse([...f.values.values()][0]).phase).toBe("prepared");
    expect(f.writes).toHaveLength(0);
    return f;
  }
  it("can abandon an unsent draft after reload without count permission or source requests", async () => {
    const f = await prepareInterruptedDraft();
    f.access.loadAccess.mockRejectedValue(new Error("count access removed"));
    f.access.detail.mockRejectedValue(new Error("task no longer visible"));
    await f.make().discardPrepared(TASK);
    expect(f.values.size).toBe(0); expect(f.writes).toHaveLength(0); expect(f.reads).toHaveLength(0);
    expect(f.objectFetch).not.toHaveBeenCalled();
  });
  it.each(["identity", "view", "identity-unavailable", "record-advanced"])("retains unsent draft when %s changes during abandon", async fault => {
    const f = await prepareInterruptedDraft();
    if (fault === "identity") f.access.loadIdentity.mockResolvedValue({ ...actor, authorization_version: 8 });
    if (fault === "view") f.access.loadIdentity.mockImplementation(async () => { f.options({ live: false }); return actor; });
    if (fault === "identity-unavailable") f.access.loadIdentity.mockRejectedValue(new Error("identity unknown"));
    if (fault === "record-advanced") f.access.loadIdentity.mockImplementation(async () => {
      const [key, raw] = [...f.values.entries()][0];
      f.values.set(key, JSON.stringify({ ...JSON.parse(raw), phase: "intent_requested" })); return actor;
    });
    await expect(f.make().discardPrepared(TASK)).rejects.toThrow();
    expect(f.values.size).toBe(1); expect(f.writes).toHaveLength(0);
  });
  it("cannot abandon an upload with unknown outcome", async () => {
    const f = fixture(); f.options({ headFailure: true });
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    const before = [...f.values.values()]; const writes = f.writes.length;
    await expect(f.make().discardPrepared(TASK)).rejects.toThrow();
    expect([...f.values.values()]).toEqual(before); expect(f.writes).toHaveLength(writes);
  });
  it("uploads once, reviews original binding, waits for explicit confirmation and recovers a lost COMMIT acknowledgement", async () => {
    const f = fixture(), workflow = f.make();
    const ready = await workflow.start(TASK, ROUND, SCOPE, file());
    expect(ready.review.can_confirm).toBe(true);
    expect(f.writes.some(path => path.endsWith("/confirm"))).toBe(false);
    expect(f.events).toEqual(["put-after-persist"]);
    expect(workflow.store.read(TASK).kind).toBe("valid");
    const manual = createOpeningCountRecoveryStore(f);
    await expect(withNoPendingOpeningCount(TASK, async () => true, manual)).rejects.toThrow("导入");
    f.options({ confirmFailure: true });
    const confirmed = await workflow.decide(TASK, ready.review, "confirm");
    expect(confirmed.review.result.status).toBe("succeeded");
    expect(f.writes.filter(path => path.endsWith("/confirm"))).toHaveLength(1);
    expect((await f.make().resume(TASK)).review.result.status).toBe("succeeded");
    expect(f.writes.filter(path => path.endsWith("/confirm"))).toHaveLength(1);
    expect((await f.make().finish(TASK)).completion_id).toBe(id(7));
    expect(f.values.size).toBe(0);
  });
  it("reloads after unknown PUT/HEAD and recovers by HEAD only, retaining the same original keys", async () => {
    const f = fixture(); f.options({ headFailure: true });
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    const original = JSON.parse([...f.values.values()][0]);
    expect(original.phase).toBe("upload_started");
    f.options({ headFailure: false });
    const result = await f.make().resume(TASK);
    expect(result.record.import_key).toBe(original.import_key);
    expect(result.record.upload_key).toBe(original.upload_key);
    expect(f.objectFetch).toHaveBeenCalledTimes(1);
    expect(f.writes.filter(path => path.endsWith("/source-upload-intents"))).toHaveLength(1);
    expect(f.writes.filter(path => path.endsWith("/complete"))).toHaveLength(2);
  });
  it("lost intake acknowledgement and 404 preserve the original record and never re-POST on restart", async () => {
    const f = fixture(); f.options({ createFailure: true, recoveryMissing: true });
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    expect(JSON.parse([...f.values.values()][0]).phase).toBe("job_requested");
    const writes = f.writes.length;
    await expect(f.make().resume(TASK)).rejects.toThrow();
    expect(f.writes).toHaveLength(writes);
    f.options({ recoveryMissing: false });
    expect((await f.make().resume(TASK)).review.job_id).toBe(JOB);
    expect(f.writes).toHaveLength(writes);
  });
  it("does not replay an uncertain confirmation when the original still reads awaiting_confirmation", async () => {
    const f = fixture(), workflow = f.make();
    const ready = await workflow.start(TASK, ROUND, SCOPE, file());
    f.options({ confirmFailure: true, commitConfirm: false });
    const pending = await workflow.decide(TASK, ready.review, "confirm");
    expect(pending.record.phase).toBe("confirmation_requested");
    await expect(f.make().decide(TASK, pending.review, "confirm")).rejects.toThrow();
    expect(f.writes.filter(path => path.endsWith("/confirm"))).toHaveLength(1);
    const cancelled = await f.make().decide(TASK, pending.review, "cancel");
    expect(cancelled.review.result.status).toBe("cancelled");
    expect((await f.make().finish(TASK)).completion_id).toBeNull();
  });
  it.each(["round", "scope", "identity", "access", "view"])("refuses changed %s before any upload intent", async fault => {
    const f = fixture();
    if (fault === "round") f.access.detail.mockImplementation(async () => ({ ...detail(), current_round: { ...detail().current_round!, round_id: id(10) } }));
    if (fault === "scope") f.access.detail.mockImplementation(async () => ({ ...detail(), scopes: detail().scopes.map(s => ({ ...s, assigned_to_me: false })) }));
    if (fault === "identity") f.access.loadIdentity.mockResolvedValue({ ...actor, authorization_version: 8 });
    if (fault === "access") f.access.loadAccess.mockResolvedValue({ ...await f.access.loadAccess(), can_count: false });
    if (fault === "view") f.options({ live: false });
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    expect(f.writes).toHaveLength(0); expect(f.values.size).toBe(0);
  });
  it("rejects a different review binding without discarding the original record", async () => {
    const f = fixture(); f.options({ corruptReview: true });
    await expect(f.make().start(TASK, ROUND, SCOPE, file())).rejects.toThrow();
    expect(f.values.size).toBe(1);
    expect(f.writes.some(path => path.endsWith("/confirm"))).toBe(false);
  });
  it("rechecks current identity before a displayed review can be confirmed", async () => {
    const f = fixture(), workflow = f.make();
    const ready = await workflow.start(TASK, ROUND, SCOPE, file());
    f.access.loadIdentity.mockResolvedValue({ ...actor, authorization_version: 8 });
    await expect(workflow.decide(TASK, ready.review, "confirm")).rejects.toThrow();
    expect(f.writes.some(path => path.endsWith("/confirm"))).toBe(false);
  });
  it("never clears a nonterminal original when the user tries to finish", async () => {
    const f = fixture(), workflow = f.make();
    await workflow.start(TASK, ROUND, SCOPE, file());
    await expect(workflow.finish(TASK)).rejects.toThrow();
    expect(f.values.size).toBe(1);
  });
});
