import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiNoReplay, ApiError } from "./api";
import { createOpeningImportClient, parseOpeningImportStatus, prepareOpeningImportSource } from "./openingCountImportClient";

vi.mock("./api", async importOriginal => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, api: vi.fn(), apiNoReplay: vi.fn() };
});
afterEach(() => vi.clearAllMocks());
const FILE = "90000000-0000-4000-8000-000000000001";
const JOB = "90000000-0000-4000-8000-000000000002";
const OTHER = "90000000-0000-4000-8000-000000000003";
const KEY = "opening-import-" + "ab".repeat(18);
const NOW = Date.parse("2026-09-25T00:00:00Z");
const URL = "https://private.example.test/original%2fkey?signature=private%2Fvalue";
const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const coordinates = { source_file_id: FILE, task_id: JOB, round_id: OTHER, scope_id: FILE };
const ready = { job_id: JOB, status: "awaiting_confirmation", completion_id: null, row_count: 2,
  error_count: 0, error_file_available: false, failure_code: null, replayed: false };
const sourceFile = () => Object.assign(new Blob([new Uint8Array([80, 75, 3, 4])], { type: MIME }), { name: "盘点.xlsx" });
async function source() { return prepareOpeningImportSource(sourceFile()); }
function intent(sha: string, changes: Record<string, unknown> = {}) {
  return { file_id: FILE, status: "pending", replayed: false, upload: { method: "PUT", url: URL,
    expires_at: "2026-09-25T00:10:00Z", headers: { "Content-Type": MIME,
      "x-oss-meta-sha256": sha, "x-oss-meta-file-id": FILE, "x-oss-forbid-overwrite": "true" } }, ...changes };
}
const complete = { file_id: FILE, status: "available", replayed: false };

describe("opening import client response boundaries", () => {
  const managementBinding = { ...coordinates, actor_person_id: OTHER, authorization_version: 3,
    source_sha256: "ab".repeat(32), size_bytes: 12 };
  const reviewer = { person_id: FILE, authorization_version: 9 };
  const management = { ...managementBinding, schema_version: "rsc.opening_import_management_recovery.v1",
    reviewer_person_id: FILE, reviewer_authorization_version: 9, job_id: JOB, status: "cancelled",
    completion_id: null, terminal_audit_id: OTHER, terminal_verified: true, automatic_retry_allowed: false };
  it("reads original coordinates with current reviewer separately from historical requester, without mutation", async () => {
    const read = vi.fn(async () => management), write = vi.fn();
    const result = await createOpeningImportClient({ read, write }).managementReview(KEY, managementBinding, reviewer, JOB);
    expect(result.terminal_verified).toBe(true);
    expect(result.authorization_version).toBe(3); expect(result.reviewer_authorization_version).toBe(9);
    expect(write).not.toHaveBeenCalled();
    const [path, init] = read.mock.calls[0] as unknown as [string, RequestInit];
    const params = new globalThis.URL(path, "https://example.test").searchParams;
    expect(Object.fromEntries(params)).toEqual(coordinates);
    expect(init.method).toBeUndefined(); expect(init.cache).toBe("no-store");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(KEY);
  });
  it.each([
    { authorization_version: 9 }, { actor_person_id: FILE }, { reviewer_person_id: OTHER },
    { reviewer_authorization_version: 10 }, { source_sha256: "ff".repeat(32) }, { size_bytes: 13 },
    { source_file_id: OTHER }, { task_id: OTHER }, { round_id: FILE }, { scope_id: OTHER },
    { job_id: OTHER }, { status: "running" }, { status: "awaiting_confirmation" },
    { automatic_retry_allowed: true }, { terminal_verified: "true" }, { terminal_audit_id: null },
    { status: "succeeded", completion_id: null }, { completion_id: FILE }, { preview: {} },
  ])("refuses mismatched or unsafe management recovery proof %#", changes => {
    return expect(createOpeningImportClient({ read: async () => ({ ...management, ...changes }) })
      .managementReview(KEY, managementBinding, reviewer, JOB)).rejects.toThrow();
  });
  it("an active management result or missing result never grants terminal recovery or issues a write", async () => {
    const read = vi.fn().mockResolvedValueOnce({ ...management, status: "awaiting_confirmation", terminal_audit_id: null,
      terminal_verified: false }).mockRejectedValueOnce(new ApiError(404, "not observed", {}));
    const write = vi.fn(), client = createOpeningImportClient({ read, write });
    expect((await client.managementReview(KEY, managementBinding, reviewer)).terminal_verified).toBe(false);
    await expect(client.managementReview(KEY, managementBinding, reviewer)).rejects.toThrow();
    expect(write).not.toHaveBeenCalled();
  });
  it.each([
    { ...ready, completion_id: FILE }, { ...ready, status: "succeeded" },
    { ...ready, row_count: null }, { ...ready, row_count: 0 }, { ...ready, row_count: 10001 },
    { ...ready, row_count: "2" }, { ...ready, error_count: 1 }, { ...ready, error_count: -1 },
    { ...ready, status: "arbitrary" }, { ...ready, failure_code: "private raw exception" },
    { ...ready, failure_code: "opening_import_context_changed" }, { ...ready, status: "cancelled" },
    { ...ready, error_file_available: true }, { ...ready, replayed: 1 },
    { ...ready, extra: "unsafe" }, { ...ready, job_id: "../../auth" },
  ])("refuses inconsistent or unrecognized status %#", value => {
    expect(() => parseOpeningImportStatus(value)).toThrow();
  });
  it("keeps success, failure, cancellation and confirmation independent", () => {
    expect(parseOpeningImportStatus(ready).status).toBe("awaiting_confirmation");
    expect(parseOpeningImportStatus({ ...ready, status: "succeeded", completion_id: FILE }).completion_id).toBe(FILE);
    expect(parseOpeningImportStatus({ ...ready, status: "failed", error_count: 1,
      failure_code: "opening_import_prevalidation_failed", error_file_available: true }).error_file_available).toBe(true);
    expect(parseOpeningImportStatus({ ...ready, status: "cancelled", failure_code: "opening_import_cancelled" }).status).toBe("cancelled");
    expect(() => parseOpeningImportStatus(ready, OTHER)).toThrow();
  });
  it("sends an exact source/task/round/scope request and preserves the original key", async () => {
    const write = vi.fn(async () => ready);
    await createOpeningImportClient({ write }).request(coordinates, KEY);
    expect(write).toHaveBeenCalledTimes(1);
    const [path, init] = write.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toMatch(/\/opening-count\/jobs$/);
    expect(JSON.parse(String(init.body))).toEqual(coordinates);
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(KEY);
    await expect(createOpeningImportClient({ write }).request({ ...coordinates, preview: ready } as typeof coordinates, KEY)).rejects.toThrow();
    expect(write).toHaveBeenCalledTimes(1);
  });
  it.each(["confirm", "cancel"] as const)("uses no-replay transport with empty body for %s, even on lost acknowledgement", async action => {
    vi.mocked(apiNoReplay).mockRejectedValueOnce(new ApiError(401, "uncertain", {}));
    const client = createOpeningImportClient();
    await expect(client.decide(JOB, KEY, action)).rejects.toThrow();
    expect(apiNoReplay).toHaveBeenCalledTimes(1);
    expect(api).not.toHaveBeenCalled();
    const [path, init] = vi.mocked(apiNoReplay).mock.calls[0];
    expect(path).toContain(`/jobs/${JOB}/${action}`);
    expect(JSON.parse(String(init?.body))).toEqual({});
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(KEY);
  });
  it("recovers only by GET using the original key; a 404 never starts another task", async () => {
    const read = vi.fn().mockRejectedValueOnce(new ApiError(404, "not found", {})).mockResolvedValueOnce(ready);
    const write = vi.fn();
    const client = createOpeningImportClient({ read, write });
    await expect(client.recover(KEY)).rejects.toThrow();
    expect((await client.recover(KEY, JOB)).job_id).toBe(JOB);
    expect(write).not.toHaveBeenCalled();
    expect(read.mock.calls.every(([path, init]) => path.endsWith("/jobs/recovery") && !init.method
      && init.headers["Idempotency-Key"] === KEY && init.cache === "no-store")).toBe(true);
  });
  it("rejects another job in direct, recovered and decision responses", async () => {
    const client = createOpeningImportClient({ read: async () => ready, write: async () => ready });
    await expect(client.status(OTHER)).rejects.toThrow();
    await expect(client.recover(KEY, OTHER)).rejects.toThrow();
    await expect(client.decide(OTHER, KEY, "confirm")).rejects.toThrow();
  });
  it("does not coerce capability values", async () => {
    expect(await createOpeningImportClient({ read: async () => ({ available: false }) }).available()).toBe(false);
    await expect(createOpeningImportClient({ read: async () => ({ available: "true" }) }).available()).rejects.toThrow();
  });
});

describe("opening import source upload", () => {
  it("hashes an immutable XLSX snapshot locally without sending file content to an API", async () => {
    const prepared = await source();
    expect(prepared.sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(prepared.body.type).toBe(MIME);
    expect(await prepared.body.arrayBuffer()).toEqual(await sourceFile().arrayBuffer());
    const write = vi.fn(async () => intent(prepared.sha256));
    await createOpeningImportClient({ write, now: () => NOW }).sourceIntent(prepared, KEY);
    const [, init] = write.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ original_filename: "盘点.xlsx", size_bytes: 4, sha256: prepared.sha256 });
  });
  it.each(["wrong.csv", "../盘点.xlsx", "=\nprivate.xlsx"])("refuses invalid source name %s", async name => {
    await expect(prepareOpeningImportSource(Object.assign(new Blob(["a"]), { name }))).rejects.toThrow();
  });
  it("refuses zero-size and oversized sources before reading their bytes", async () => {
    for (const size of [0, 8 * 1024 * 1024 + 1]) {
      const arrayBuffer = vi.fn();
      await expect(prepareOpeningImportSource({ name: "file.xlsx", size, arrayBuffer } as unknown as File)).rejects.toThrow();
      expect(arrayBuffer).not.toHaveBeenCalled();
    }
  });
  it.each(["timeout", "http-error", "success"])("HEAD-verifies the exact object after %s and never re-PUTs", async outcome => {
    const prepared = await source();
    const calls: string[] = [];
    const write = vi.fn(async (path: string) => {
      calls.push(path.endsWith("/complete") ? "complete" : "intent");
      return path.endsWith("/complete") ? complete : intent(prepared.sha256);
    });
    const objectFetch = vi.fn(async () => {
      calls.push("put");
      if (outcome === "timeout") throw new TypeError(`network ${URL}`);
      return new Response(null, { status: outcome === "http-error" ? 409 : 200 });
    });
    const client = createOpeningImportClient({ write, objectFetch, now: () => NOW });
    const issued = await client.sourceIntent(prepared, KEY);
    let claimed = false;
    const claim = vi.fn(async (fileId, hash) => {
      expect([fileId, hash]).toEqual([FILE, prepared.sha256]);
      if (claimed) return false;
      calls.push("persist-and-reread"); claimed = true; return true;
    });
    expect(await client.uploadOnce(prepared, issued, claim)).toBe(FILE);
    expect(calls).toEqual(["intent", "persist-and-reread", "put", "complete"]);
    await expect(client.uploadOnce(prepared, issued, claim)).rejects.toThrow();
    expect(await client.completeSource(FILE)).toBe(FILE);
    expect(objectFetch).toHaveBeenCalledTimes(1);
    expect(objectFetch.mock.calls[0]).toEqual([URL, expect.objectContaining({ method: "PUT", body: prepared.body,
      credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store" })]);
  });
  it("does not upload when persistence fails or the view changes during claim", async () => {
    const prepared = await source();
    const objectFetch = vi.fn();
    const client = createOpeningImportClient({ write: async () => intent(prepared.sha256), objectFetch, now: () => NOW });
    const issued = await client.sourceIntent(prepared, KEY);
    await expect(client.uploadOnce(prepared, issued, async () => { throw new Error("storage unavailable"); })).rejects.toThrow();
    let current = true;
    await expect(client.uploadOnce(prepared, issued, async () => { current = false; return true; }, () => current)).rejects.toThrow();
    expect(objectFetch).not.toHaveBeenCalled();
  });
  it("does not send completion under a new identity when the view changes during PUT", async () => {
    const prepared = await source();
    let current = true;
    const write = vi.fn(async () => intent(prepared.sha256));
    const client = createOpeningImportClient({ write, now: () => NOW, objectFetch: async () => {
      current = false; return new Response();
    } });
    const issued = await client.sourceIntent(prepared, KEY);
    await expect(client.uploadOnce(prepared, issued, async () => true, () => current)).rejects.toThrow();
    expect(write).toHaveBeenCalledTimes(1);
  });
  it("rehashes the actual body and rejects tampered source content before claiming", async () => {
    const prepared = await source();
    const client = createOpeningImportClient({ write: async () => intent(prepared.sha256), now: () => NOW });
    const issued = await client.sourceIntent(prepared, KEY);
    const claim = vi.fn(async () => true);
    await expect(client.uploadOnce({ ...prepared, body: new Blob(["oops"], { type: MIME }) }, issued, claim)).rejects.toThrow();
    expect(claim).not.toHaveBeenCalled();
  });
  it("keeps a lost PUT and failed HEAD unknown without replaying or leaking signed URLs", async () => {
    const prepared = await source();
    const objectFetch = vi.fn(async () => { throw new Error(URL); });
    const write = vi.fn(async (path: string) => {
      if (path.endsWith("/complete")) throw new ApiError(503, "still unknown", {});
      return intent(prepared.sha256);
    });
    const client = createOpeningImportClient({ write, objectFetch, now: () => NOW });
    const issued = await client.sourceIntent(prepared, KEY);
    await expect(client.uploadOnce(prepared, issued, async () => true)).rejects.toThrow("still unknown");
    expect(objectFetch).toHaveBeenCalledTimes(1);
    expect(write).toHaveBeenCalledTimes(2);
  });
  it.each(["http", "expired", "wrong-file", "wrong-hash", "authorization", "case-duplicate"])("rejects unsafe upload intent %s", async fault => {
    const prepared = await source();
    const payload = intent(prepared.sha256);
    const upload = payload.upload as { url: string; expires_at: string; headers: Record<string, string> };
    if (fault === "http") upload.url = URL.replace("https:", "http:");
    if (fault === "expired") upload.expires_at = "2026-09-24T23:59:59Z";
    if (fault === "wrong-file") upload.headers["x-oss-meta-file-id"] = OTHER;
    if (fault === "wrong-hash") upload.headers["x-oss-meta-sha256"] = "ff".repeat(32);
    if (fault === "authorization") upload.headers.Authorization = "not-allowed";
    if (fault === "case-duplicate") upload.headers["content-type"] = MIME;
    await expect(createOpeningImportClient({ write: async () => payload, now: () => NOW }).sourceIntent(prepared, KEY)).rejects.toThrow();
  });
  it("recovers an already available source without PUT or another completion", async () => {
    const prepared = await source();
    const write = vi.fn(async () => intent(prepared.sha256, { status: "available", upload: null, replayed: true }));
    const objectFetch = vi.fn();
    const client = createOpeningImportClient({ write, objectFetch, now: () => NOW });
    const issued = await client.sourceIntent(prepared, KEY);
    const claim = vi.fn();
    expect(await client.uploadOnce(prepared, issued, claim)).toBe(FILE);
    expect(objectFetch).not.toHaveBeenCalled(); expect(claim).not.toHaveBeenCalled();
    expect(write).toHaveBeenCalledTimes(1);
  });
});

describe("opening import private error download", () => {
  function download() { return { job_id: JOB, file_id: FILE, filename: "errors.xlsx", sha256: "ab".repeat(32), size_bytes: 200,
    download: { method: "GET", url: URL, expires_at: "2026-09-25T00:05:00Z" } }; }
  it("preserves the exact private URL without fetching or sending cookies to it", async () => {
    const objectFetch = vi.fn();
    const write = vi.fn(async () => download());
    const result = await createOpeningImportClient({ write, objectFetch, now: () => NOW }).errorDownload(JOB);
    expect(result.download.url).toBe(URL);
    expect(objectFetch).not.toHaveBeenCalled();
    const [, init] = write.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBe("{}");
  });
  it.each(["other-job", "http", "credentials", "fragment", "expired", "long-ttl", "wrong-method"])("refuses download %s", async fault => {
    const payload = download();
    if (fault === "other-job") payload.job_id = OTHER;
    if (fault === "http") payload.download.url = URL.replace("https:", "http:");
    if (fault === "credentials") payload.download.url = URL.replace("https://", "https://user:password@");
    if (fault === "fragment") payload.download.url += "#fragment";
    if (fault === "expired") payload.download.expires_at = "2026-09-24T23:59:59Z";
    if (fault === "long-ttl") payload.download.expires_at = "2026-09-25T00:10:01Z";
    if (fault === "wrong-method") payload.download.method = "PUT";
    await expect(createOpeningImportClient({ write: async () => payload, now: () => NOW }).errorDownload(JOB)).rejects.toThrow();
  });
});

describe("opening import review coordinates", () => {
  const expected = { ...coordinates, actor_person_id: OTHER, authorization_version: 7,
    source_sha256: "ab".repeat(32), size_bytes: 12 };
  const response = { ...expected, job_id: JOB, original_filename: "count.xlsx", can_confirm: true, result: ready };
  it("reads an exact bound review without a write or source download", async () => {
    const write = vi.fn(), objectFetch = vi.fn();
    const client = createOpeningImportClient({ read: async () => response, write, objectFetch });
    expect((await client.review(JOB, expected)).can_confirm).toBe(true);
    expect(write).not.toHaveBeenCalled(); expect(objectFetch).not.toHaveBeenCalled();
  });
  it.each(Object.keys(expected))("rejects a changed %s in the authoritative review", async field => {
    const client = createOpeningImportClient({ read: async () => ({ ...response, [field]: "different" }) });
    await expect(client.review(JOB, expected)).rejects.toThrow();
  });
  it("does not treat a terminal result or a mismatched nested job as confirmable", async () => {
    for (const result of [{ ...ready, status: "succeeded", completion_id: FILE }, { ...ready, job_id: OTHER }]) {
      const client = createOpeningImportClient({ read: async () => ({ ...response, result }) });
      await expect(client.review(JOB, expected)).rejects.toThrow();
    }
  });
});
