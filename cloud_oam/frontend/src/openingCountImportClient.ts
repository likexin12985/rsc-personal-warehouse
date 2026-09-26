/** Transport boundary for durable opening imports. The workflow owns the task
 * lease and persisted request keys; this client never allocates replacement keys
 * or retries a command after an uncertain response. */
import { api, apiNoReplay, createIdempotencyKey, jsonBody } from "./api";

const ROOT = "/v1/stocktakes/opening/imports/opening-count";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA = /^[0-9a-f]{64}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const STATUSES = ["queued", "prevalidating", "awaiting_confirmation", "running", "succeeded", "failed", "cancelled"] as const;
const FAILURES = ["opening_import_context_changed", "opening_import_source_invalid", "opening_import_cancelled", "opening_import_prevalidation_failed"] as const;
type Requester = (path: string, init?: RequestInit) => Promise<unknown>;

export type OpeningImportStatus = Readonly<{
  job_id: string;
  status: typeof STATUSES[number];
  completion_id: string | null;
  row_count: number | null;
  error_count: number;
  error_file_available: boolean;
  failure_code: typeof FAILURES[number] | null;
  replayed: boolean;
}>;
export type OpeningImportCoordinates = Readonly<{
  source_file_id: string; task_id: string; round_id: string; scope_id: string;
}>;
export type OpeningImportReviewBinding = OpeningImportCoordinates & Readonly<{
  actor_person_id: string; authorization_version: number; source_sha256: string; size_bytes: number;
}>;
export type OpeningImportReview = OpeningImportReviewBinding & Readonly<{
  job_id: string; original_filename: string; can_confirm: boolean; result: OpeningImportStatus;
}>;
export type OpeningImportManagementReview = OpeningImportReviewBinding & Readonly<{
  schema_version: "rsc.opening_import_management_recovery.v1";
  reviewer_person_id: string; reviewer_authorization_version: number;
  job_id: string; status: Exclude<OpeningImportStatus["status"], "running">;
  completion_id: string | null; terminal_audit_id: string | null;
  terminal_verified: boolean; automatic_retry_allowed: false;
}>;
export type OpeningImportSealBinding = Omit<OpeningImportReviewBinding, "source_file_id"> & Readonly<{
  source_file_id: string | null;
}>;
export type OpeningImportSeal = OpeningImportReviewBinding & Readonly<{
  schema_version: "rsc.opening_import_seal.v1";
  seal_id: string; terminal_audit_id: string;
  reviewer_person_id: string; reviewer_authorization_version: number;
  permanent_nonexecution: true; automatic_retry_allowed: false; source_object_may_exist: true;
}>;
export type PreparedOpeningImportSource = Readonly<{
  original_filename: string; size_bytes: number; sha256: string; body: Blob;
}>;
export type OpeningImportUploadIntent = Readonly<{
  file_id: string; status: "pending" | "available"; replayed: boolean;
  upload: Readonly<{ method: "PUT"; url: string; expires_at: string; headers: Readonly<Record<string, string>> }> | null;
}>;
export type OpeningImportDownload = Readonly<{
  job_id: string; file_id: string; filename: string; sha256: string; size_bytes: number;
  download: Readonly<{ method: "GET"; url: string; expires_at: string }>;
}>;

function stop(): never { throw new Error("导入响应或请求未通过校验，请保留原任务并重新核验"); }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) stop();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) stop();
  return row;
}
function uuid(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) stop(); return value; }
function key(value: string): string { if (typeof value !== "string" || !KEY.test(value)) stop(); return value; }
function integer(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) stop();
  return value as number;
}
function digest(value: unknown): string { if (typeof value !== "string" || !SHA.test(value)) stop(); return value; }
function filename(value: unknown): string {
  if (typeof value !== "string" || !value || value !== value.trim() || value.length > 200
    || /[\u0000-\u001f\u007f/\\]/.test(value) || !/\.xlsx$/i.test(value)) stop();
  return value;
}
function signedUrl(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 8192) stop();
  let url: URL;
  try { url = new URL(value); } catch { return stop(); }
  if (url.protocol !== "https:" || !url.hostname || url.username || url.password || url.hash) stop();
  return value; // Preserve the signature's exact escaping; never reserialize.
}
function expiry(value: unknown, now: number, maxMs: number): string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) stop();
  const remaining = Date.parse(value) - now;
  if (!Number.isFinite(remaining) || remaining <= 0 || remaining > maxMs) stop();
  return value;
}
function headers(requestKey?: string): Record<string, string> {
  return { "X-Request-ID": createIdempotencyKey("opening-import-trace"),
    ...(requestKey === undefined ? {} : { "Idempotency-Key": key(requestKey) }) };
}

export function parseOpeningImportStatus(value: unknown, expectedJobId?: string): OpeningImportStatus {
  const row = object(value, ["job_id", "status", "completion_id", "row_count", "error_count", "error_file_available", "failure_code", "replayed"]);
  uuid(row.job_id);
  if (expectedJobId !== undefined && row.job_id !== uuid(expectedJobId)) stop();
  if (!STATUSES.includes(row.status as OpeningImportStatus["status"])) stop();
  if (row.completion_id !== null) uuid(row.completion_id);
  if (row.row_count !== null) integer(row.row_count, 0, 10000);
  integer(row.error_count, 0, 1000);
  if (typeof row.error_file_available !== "boolean" || typeof row.replayed !== "boolean"
    || !(row.failure_code === null || FAILURES.includes(row.failure_code as typeof FAILURES[number]))) stop();
  if ((row.status === "succeeded") !== (row.completion_id !== null)
    || (row.error_file_available && (row.status !== "failed" || row.error_count === 0))
    || (row.failure_code !== null && !["failed", "cancelled"].includes(String(row.status)))
    || (row.status === "cancelled" && row.failure_code !== "opening_import_cancelled")
    || (row.status !== "cancelled" && row.failure_code === "opening_import_cancelled")
    || (["awaiting_confirmation", "running", "succeeded"].includes(String(row.status))
      && (row.row_count === null || row.row_count === 0 || row.error_count !== 0))) stop();
  return Object.freeze({ ...row }) as OpeningImportStatus;
}

function managementBinding(expected: OpeningImportReviewBinding,
  reviewer: Readonly<{ person_id: string; authorization_version: number }>, expectedJobId?: string) {
  const binding = object(expected, ["source_file_id", "task_id", "round_id", "scope_id",
    "actor_person_id", "authorization_version", "source_sha256", "size_bytes"]);
  for (const field of ["source_file_id", "task_id", "round_id", "scope_id", "actor_person_id"]) uuid(binding[field]);
  integer(binding.authorization_version, 1, Number.MAX_SAFE_INTEGER);
  integer(binding.size_bytes, 1, 8 * 1024 * 1024); digest(binding.source_sha256);
  uuid(reviewer.person_id); integer(reviewer.authorization_version, 1, Number.MAX_SAFE_INTEGER);
  if (expectedJobId !== undefined) uuid(expectedJobId);
  return binding;
}
export function parseOpeningImportManagementReview(value: unknown, expected: OpeningImportReviewBinding,
  reviewer: Readonly<{ person_id: string; authorization_version: number }>, expectedJobId?: string): OpeningImportManagementReview {
  const binding = managementBinding(expected, reviewer, expectedJobId);
  const row = object(value, [...Object.keys(binding), "schema_version", "reviewer_person_id", "reviewer_authorization_version",
    "job_id", "status", "completion_id", "terminal_audit_id", "terminal_verified", "automatic_retry_allowed"]);
  uuid(row.job_id);
  if (row.schema_version !== "rsc.opening_import_management_recovery.v1"
    || row.reviewer_person_id !== reviewer.person_id || row.reviewer_authorization_version !== reviewer.authorization_version
    || Object.entries(binding).some(([field, value]) => row[field] !== value)
    || (expectedJobId !== undefined && row.job_id !== expectedJobId)
    || !STATUSES.includes(row.status as OpeningImportStatus["status"]) || row.status === "running"
    || row.automatic_retry_allowed !== false || typeof row.terminal_verified !== "boolean") stop();
  if (row.completion_id !== null) uuid(row.completion_id);
  if (row.terminal_audit_id !== null) uuid(row.terminal_audit_id);
  const terminal = ["succeeded", "failed", "cancelled"].includes(String(row.status));
  if (row.terminal_verified !== terminal || terminal !== (row.terminal_audit_id !== null)
    || (row.status === "succeeded") !== (row.completion_id !== null)) stop();
  return Object.freeze({ ...row }) as OpeningImportManagementReview;
}

function sealBinding(expected: OpeningImportSealBinding,
  reviewer: Readonly<{ person_id: string; authorization_version: number }>) {
  // A lost upload-intent acknowledgement may leave the source UUID unknown.
  // Validate all other original coordinates before sending any request.
  object(expected, ["source_file_id", "task_id", "round_id", "scope_id",
    "actor_person_id", "authorization_version", "source_sha256", "size_bytes"]);
  for (const value of [expected.task_id, expected.round_id, expected.scope_id, expected.actor_person_id, reviewer.person_id]) uuid(value);
  if (expected.source_file_id !== null) uuid(expected.source_file_id);
  integer(expected.authorization_version, 1, Number.MAX_SAFE_INTEGER);
  integer(reviewer.authorization_version, 1, Number.MAX_SAFE_INTEGER);
  integer(expected.size_bytes, 1, 8 * 1024 * 1024); digest(expected.source_sha256);
  return Object.freeze({ ...expected });
}
export function parseOpeningImportSeal(value: unknown, expected: OpeningImportSealBinding,
  reviewer: Readonly<{ person_id: string; authorization_version: number }>): OpeningImportSeal {
  const binding = sealBinding(expected, reviewer);
  const row = object(value, [...Object.keys(binding), "schema_version", "seal_id", "terminal_audit_id",
    "reviewer_person_id", "reviewer_authorization_version", "permanent_nonexecution",
    "automatic_retry_allowed", "source_object_may_exist"]);
  uuid(row.source_file_id); uuid(row.seal_id); uuid(row.terminal_audit_id);
  if (row.schema_version !== "rsc.opening_import_seal.v1" || row.permanent_nonexecution !== true
    || row.automatic_retry_allowed !== false || row.source_object_may_exist !== true
    || row.reviewer_person_id !== reviewer.person_id || row.reviewer_authorization_version !== reviewer.authorization_version
    || Object.entries(binding).some(([field, value]) => !(field === "source_file_id" && value === null) && row[field] !== value)) stop();
  return Object.freeze({ ...row }) as OpeningImportSeal;
}

export async function prepareOpeningImportSource(file: Blob & { name: string }): Promise<PreparedOpeningImportSource> {
  filename(file.name);
  integer(file.size, 1, 8 * 1024 * 1024);
  if (!globalThis.crypto?.subtle || typeof file.arrayBuffer !== "function") stop();
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength !== file.size) stop();
  const hash = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
  return Object.freeze({ original_filename: file.name, size_bytes: bytes.byteLength,
    sha256: digest(sha256), body: new Blob([bytes], { type: MIME }) });
}

function sourceMetadata(source: PreparedOpeningImportSource) {
  if (!(source.body instanceof Blob) || source.body.size !== source.size_bytes || source.body.type !== MIME) stop();
  return { original_filename: filename(source.original_filename), size_bytes: integer(source.size_bytes, 1, 8 * 1024 * 1024), sha256: digest(source.sha256) };
}
function parseUpload(value: unknown, source: PreparedOpeningImportSource, now: number): OpeningImportUploadIntent {
  const row = object(value, ["file_id", "status", "upload", "replayed"]);
  const fileId = uuid(row.file_id);
  if (typeof row.replayed !== "boolean" || !["pending", "available"].includes(String(row.status))) stop();
  if (row.status === "available") {
    if (row.upload !== null) stop();
    return Object.freeze({ file_id: fileId, status: "available", upload: null, replayed: row.replayed });
  }
  const upload = object(row.upload, ["method", "url", "expires_at", "headers"]);
  if (upload.method !== "PUT") stop();
  const values = upload.headers;
  if (!values || typeof values !== "object" || Array.isArray(values)) stop();
  const normalized = new Map<string, string>();
  for (const [name, value] of Object.entries(values)) {
    if (typeof value !== "string" || /[\r\n]/.test(value) || normalized.has(name.toLowerCase())) stop();
    normalized.set(name.toLowerCase(), value);
  }
  const expected = { "content-type": MIME, "x-oss-meta-sha256": source.sha256,
    "x-oss-meta-file-id": fileId, "x-oss-forbid-overwrite": "true" };
  if (normalized.size !== 4 || Object.entries(expected).some(([k, v]) => normalized.get(k) !== v)) stop();
  return Object.freeze({ file_id: fileId, status: "pending", replayed: row.replayed,
    upload: Object.freeze({ method: "PUT", url: signedUrl(upload.url),
      expires_at: expiry(upload.expires_at, now, 3600_000),
      headers: Object.freeze(Object.fromEntries(normalized)) }) });
}

export function createOpeningImportClient(dependencies: Readonly<{
  read?: Requester; write?: Requester; objectFetch?: typeof fetch; now?: () => number;
}> = {}) {
  const read = dependencies.read ?? api;
  const write = dependencies.write ?? apiNoReplay;
  const objectFetch = dependencies.objectFetch ?? fetch;
  const now = dependencies.now ?? Date.now;
  async function commandSeal(operation: "request" | "recover", originalKey: string, uploadKey: string,
    expected: OpeningImportSealBinding, reviewer: Readonly<{ person_id: string; authorization_version: number }>) {
    const binding = sealBinding(expected, reviewer), identity = Object.freeze({ ...reviewer });
    if (key(originalKey) === key(uploadKey)) stop();
    const body = Object.fromEntries(Object.entries(binding).filter(([, value]) => value !== null));
    const requestHeaders = { ...headers(originalKey), "X-Original-Upload-Key": uploadKey };
    const response = operation === "request"
      ? await write(`${ROOT}/command-seals`, { method: "POST", headers: requestHeaders, ...jsonBody(body), cache: "no-store" })
      : await read(`${ROOT}/command-seals/recovery?${new URLSearchParams(Object.entries(body).map(([k, v]) => [k, String(v)]))}`,
        { headers: requestHeaders, cache: "no-store" });
    return parseOpeningImportSeal(response, binding, identity);
  }
  async function completeSource(fileId: string): Promise<string> {
    const row = object(await write(`${ROOT}/sources/${uuid(fileId)}/complete`, {
      method: "POST", headers: headers(), cache: "no-store",
    }), ["file_id", "status", "replayed"]);
    if (row.file_id !== fileId || row.status !== "available" || typeof row.replayed !== "boolean") stop();
    return fileId;
  }
  return Object.freeze({
    // The workflow must first persist a one-shot seal-request marker under the
    // shared task lease. Neither method clears local state or retries a POST.
    requestSeal: (originalKey: string, uploadKey: string, expected: OpeningImportSealBinding,
      reviewer: Readonly<{ person_id: string; authorization_version: number }>) => commandSeal("request", originalKey, uploadKey, expected, reviewer),
    recoverSeal: (originalKey: string, uploadKey: string, expected: OpeningImportSealBinding,
      reviewer: Readonly<{ person_id: string; authorization_version: number }>) => commandSeal("recover", originalKey, uploadKey, expected, reviewer),
    async available(): Promise<boolean> {
      const row = object(await read(`${ROOT}/capabilities`, { cache: "no-store" }), ["available"]);
      if (typeof row.available !== "boolean") stop();
      return row.available;
    },
    async sourceIntent(source: PreparedOpeningImportSource, originalKey: string): Promise<OpeningImportUploadIntent> {
      const metadata = sourceMetadata(source);
      return parseUpload(await write(`${ROOT}/source-upload-intents`, {
        method: "POST", headers: headers(originalKey), ...jsonBody(metadata), cache: "no-store",
      }), source, now());
    },
    /** claimUpload must persist and reread the exact file/hash under the task
     * lease before returning true, once only. Recovery calls completeSource;
     * it must never call this method with a fresh claim for the same file. */
    async uploadOnce(source: PreparedOpeningImportSource, intent: OpeningImportUploadIntent,
      claimUpload: (fileId: string, sha256: string) => Promise<boolean>,
      isCurrent: () => boolean = () => true): Promise<string> {
      sourceMetadata(source);
      const checked = parseUpload(intent, source, now());
      if (!isCurrent()) stop();
      if (checked.upload === null) return checked.file_id;
      // Rehash the actual immutable body before crossing the storage boundary.
      const actual = await prepareOpeningImportSource(Object.assign(checkedBlob(source.body), { name: source.original_filename }));
      if (actual.sha256 !== source.sha256 || !isCurrent()) stop();
      if (!await claimUpload(checked.file_id, source.sha256) || !isCurrent()) stop();
      expiry(checked.upload.expires_at, now(), 3600_000);
      try {
        // A lost acknowledgement or non-2xx response does not prove absence.
        // Only server-side HEAD verification of the original object decides.
        await objectFetch(checked.upload.url, { method: "PUT", body: source.body,
          headers: checked.upload.headers, credentials: "omit", redirect: "error",
          referrerPolicy: "no-referrer", cache: "no-store" });
      } catch { /* Continue to exact completion verification, never another PUT. */ }
      if (!isCurrent()) stop();
      return completeSource(checked.file_id);
    },
    completeSource,
    async request(coordinates: OpeningImportCoordinates, originalKey: string): Promise<OpeningImportStatus> {
      const source = object(coordinates, ["source_file_id", "task_id", "round_id", "scope_id"]);
      const body = Object.fromEntries(Object.entries(source).map(([name, value]) => [name, uuid(value)]));
      return parseOpeningImportStatus(await write(`${ROOT}/jobs`, {
        method: "POST", headers: headers(originalKey), ...jsonBody(body), cache: "no-store",
      }));
    },
    async recover(originalKey: string, expectedJobId?: string): Promise<OpeningImportStatus> {
      if (expectedJobId !== undefined) uuid(expectedJobId);
      return parseOpeningImportStatus(await read(`${ROOT}/jobs/recovery`, {
        headers: { "Idempotency-Key": key(originalKey) }, cache: "no-store",
      }), expectedJobId);
    },
    async status(jobId: string): Promise<OpeningImportStatus> {
      return parseOpeningImportStatus(await read(`${ROOT}/jobs/${uuid(jobId)}`, { cache: "no-store" }), jobId);
    },
    async managementReview(originalKey: string, expected: OpeningImportReviewBinding,
      reviewer: Readonly<{ person_id: string; authorization_version: number }>, expectedJobId?: string): Promise<OpeningImportManagementReview> {
      const binding = managementBinding(expected, reviewer, expectedJobId);
      const params = new URLSearchParams(Object.fromEntries(["source_file_id", "task_id", "round_id", "scope_id"]
        .map(field => [field, String(binding[field])])));
      return parseOpeningImportManagementReview(await read(`${ROOT}/jobs/management-recovery?${params}`, {
        headers: { "Idempotency-Key": key(originalKey) }, cache: "no-store",
      }), expected, reviewer, expectedJobId);
    },
    async review(jobId: string, expected: OpeningImportReviewBinding): Promise<OpeningImportReview> {
      const binding = object(expected, ["source_file_id", "task_id", "round_id", "scope_id",
        "actor_person_id", "authorization_version", "source_sha256", "size_bytes"]);
      for (const field of ["source_file_id", "task_id", "round_id", "scope_id", "actor_person_id"]) uuid(binding[field]);
      integer(binding.authorization_version, 1, Number.MAX_SAFE_INTEGER);
      integer(binding.size_bytes, 1, 8 * 1024 * 1024); digest(binding.source_sha256);
      const row = object(await read(`${ROOT}/jobs/${uuid(jobId)}/review`, { cache: "no-store" }),
        [...Object.keys(binding), "job_id", "original_filename", "can_confirm", "result"]);
      if (row.job_id !== jobId || Object.entries(binding).some(([k, v]) => row[k] !== v)
        || typeof row.can_confirm !== "boolean") stop();
      const result = parseOpeningImportStatus(row.result, jobId);
      if (row.can_confirm && result.status !== "awaiting_confirmation") stop();
      return Object.freeze({ ...row, original_filename: filename(row.original_filename), result }) as OpeningImportReview;
    },
    async decide(jobId: string, originalKey: string, action: "confirm" | "cancel"): Promise<OpeningImportStatus> {
      if (action !== "confirm" && action !== "cancel") stop();
      return parseOpeningImportStatus(await write(`${ROOT}/jobs/${uuid(jobId)}/${action}`, {
        method: "POST", headers: headers(originalKey), ...jsonBody({}), cache: "no-store",
      }), jobId);
    },
    async errorDownload(jobId: string): Promise<OpeningImportDownload> {
      const row = object(await write(`${ROOT}/jobs/${uuid(jobId)}/error-download-intents`, {
        method: "POST", headers: headers(), ...jsonBody({}), cache: "no-store",
      }), ["job_id", "file_id", "filename", "sha256", "size_bytes", "download"]);
      if (row.job_id !== jobId) stop();
      const download = object(row.download, ["method", "url", "expires_at"]);
      if (download.method !== "GET") stop();
      return Object.freeze({ job_id: jobId, file_id: uuid(row.file_id), filename: filename(row.filename),
        sha256: digest(row.sha256), size_bytes: integer(row.size_bytes, 1, 2 * 1024 * 1024),
        download: Object.freeze({ method: "GET", url: signedUrl(download.url), expires_at: expiry(download.expires_at, now(), 600_000) }) });
    },
  });
}

function checkedBlob(blob: Blob): Blob { return blob.slice(0, blob.size, blob.type); }
