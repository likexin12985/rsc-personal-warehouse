import { api, ApiError, jsonBody, mutationHeaders } from "./api";


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const SHA256 = /^[0-9a-f]{64}$/;
const SAFE_COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const MAXIMUM_FILE_SIZE = 120 * 1024 * 1024;
const PURPOSES = new Set([
  "request_attachment",
  "external_approval_evidence",
  "stocktake_evidence",
]);


export type FormalFilePurpose =
  | "request_attachment"
  | "external_approval_evidence"
  | "stocktake_evidence";

export type FormalUploadFile = Blob & Readonly<{ name: string; type: string }>;

export type FormalFileWriteHeaders = Readonly<{
  "X-Request-ID": string;
  "Idempotency-Key": string;
}>;

export type FormalFileCompleteHeaders = Readonly<{
  "X-Request-ID": string;
}>;

export type PreparedFormalFileUpload = Readonly<{
  file: FormalUploadFile;
  purpose: FormalFilePurpose;
  original_filename: string;
  size_bytes: number;
  mime_type: string;
  sha256: string;
  intent_headers: FormalFileWriteHeaders;
  complete_headers: FormalFileCompleteHeaders;
}>;

export type FormalFileUploadResult = Readonly<{
  file_id: string;
  purpose: FormalFilePurpose;
  status: "available";
  verified_at: string;
  sha256: string;
  size_bytes: number;
  mime_type: string;
}>;

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
type ObjectFetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export type FormalFileUploadDependencies = Readonly<{
  requester?: Requester;
  objectFetcher?: ObjectFetcher;
  digest?: (bytes: ArrayBuffer) => Promise<ArrayBuffer>;
  now?: () => number;
}>;


function fail(status: number, message: string): never {
  throw new ApiError(status, message);
}

function exactObject(
  value: unknown,
  keys: readonly string[],
  label: string,
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fail(409, `${label}不是有效对象`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (
    actual.length !== expected.length
    || actual.some((key, index) => key !== expected[index])
  ) {
    return fail(409, `${label}必须精确包含正式字段`);
  }
  return object;
}

function uuid(value: unknown, label: string): string {
  if (
    typeof value !== "string"
    || !UUID.test(value)
    || value.toLowerCase() === ZERO_UUID
  ) {
    return fail(409, `${label}无效`);
  }
  return value.toLowerCase();
}

function purpose(value: unknown): FormalFilePurpose {
  if (typeof value !== "string" || !PURPOSES.has(value)) {
    return fail(409, "文件用途无效");
  }
  return value as FormalFilePurpose;
}

function safeCoordinate(value: unknown, label: string): string {
  if (typeof value !== "string" || !SAFE_COORDINATE.test(value)) {
    return fail(409, `${label}无效`);
  }
  return value;
}

function timestamp(value: unknown, label: string): string {
  if (
    typeof value !== "string"
    || !AWARE_TIMESTAMP.test(value)
    || !Number.isFinite(Date.parse(value))
  ) {
    return fail(409, `${label}无效`);
  }
  return value;
}

function boundedText(
  value: unknown,
  label: string,
  maximum: number,
): string {
  if (
    typeof value !== "string"
    || !value
    || value !== value.trim()
    || value.length > maximum
    || /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return fail(400, `${label}无效`);
  }
  return value;
}

function validateFile(
  file: FormalUploadFile,
  selectedPurpose: FormalFilePurpose,
): void {
  if (
    !file
    || typeof file.arrayBuffer !== "function"
    || !Number.isSafeInteger(file.size)
    || file.size < 1
    || file.size > MAXIMUM_FILE_SIZE
  ) {
    fail(400, "附件大小无效或超过 120 MB 上限");
  }
  boundedText(file.name, "附件名称", 200);
  boundedText(file.type, "附件 MIME 类型", 160);
  purpose(selectedPurpose);
}

function generatedIntentHeaders(): FormalFileWriteHeaders {
  const raw = mutationHeaders("file-intent").headers;
  const headers = new Headers(raw);
  return Object.freeze({
    "X-Request-ID": safeCoordinate(headers.get("X-Request-ID"), "X-Request-ID"),
    "Idempotency-Key": safeCoordinate(
      headers.get("Idempotency-Key"),
      "Idempotency-Key",
    ),
  });
}

function generatedCompleteHeaders(): FormalFileCompleteHeaders {
  const raw = mutationHeaders().headers;
  const headers = new Headers(raw);
  return Object.freeze({
    "X-Request-ID": safeCoordinate(headers.get("X-Request-ID"), "X-Request-ID"),
  });
}

async function defaultDigest(bytes: ArrayBuffer): Promise<ArrayBuffer> {
  if (!globalThis.crypto?.subtle) {
    return fail(503, "当前浏览器缺少 SHA-256 能力，已停止附件上传");
  }
  return globalThis.crypto.subtle.digest("SHA-256", bytes);
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (value) => (
    value.toString(16).padStart(2, "0")
  )).join("");
}

export async function prepareFormalFileUpload(
  file: FormalUploadFile,
  selectedPurpose: FormalFilePurpose,
  dependencies: Pick<FormalFileUploadDependencies, "digest"> = {},
): Promise<PreparedFormalFileUpload> {
  validateFile(file, selectedPurpose);
  const digest = dependencies.digest ?? defaultDigest;
  const sha256 = hex(await digest(await file.arrayBuffer()));
  if (!SHA256.test(sha256)) fail(503, "附件 SHA-256 计算失败");
  return Object.freeze({
    file,
    purpose: selectedPurpose,
    original_filename: file.name,
    size_bytes: file.size,
    mime_type: file.type,
    sha256,
    intent_headers: generatedIntentHeaders(),
    complete_headers: generatedCompleteHeaders(),
  });
}

type UploadIntent = Readonly<{
  fileId: string;
  purpose: FormalFilePurpose;
  status: "pending" | "available";
  upload: Readonly<{
    url: string;
    expiresAt: string;
    headers: Readonly<Record<string, string>>;
  }> | null;
}>;

function projectUploadIntent(
  value: unknown,
  prepared: PreparedFormalFileUpload,
  now: number,
): UploadIntent {
  const object = exactObject(value, [
    "schema_version",
    "file_id",
    "purpose",
    "status",
    "upload",
    "idempotency_replayed",
  ], "文件上传意图");
  if (object.schema_version !== "1.0" || typeof object.idempotency_replayed !== "boolean") {
    return fail(409, "文件上传意图版本无效");
  }
  const fileId = uuid(object.file_id, "file_id");
  const projectedPurpose = purpose(object.purpose);
  if (projectedPurpose !== prepared.purpose) fail(409, "文件上传意图用途不一致");
  if (object.status !== "pending" && object.status !== "available") {
    return fail(409, "文件上传意图状态无效");
  }
  if (object.status === "available") {
    if (object.upload !== null) fail(409, "已可用文件不能返回上传凭证");
    return { fileId, purpose: projectedPurpose, status: "available", upload: null };
  }
  const upload = exactObject(
    object.upload,
    ["method", "url", "expires_at", "headers"],
    "对象存储上传凭证",
  );
  if (upload.method !== "PUT") fail(409, "对象存储上传方法无效");
  const expiresAt = timestamp(upload.expires_at, "上传凭证过期时间");
  if (Date.parse(expiresAt) <= now + 5_000) fail(409, "对象存储上传凭证已过期");
  const url = safeSignedUrl(upload.url);
  const headers = signedHeaders(upload.headers, prepared, fileId);
  return {
    fileId,
    purpose: projectedPurpose,
    status: "pending",
    upload: { url, expiresAt, headers },
  };
}

function safeSignedUrl(value: unknown): string {
  if (typeof value !== "string" || value.length < 1 || value.length > 8192) {
    return fail(409, "对象存储上传地址无效");
  }
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return fail(409, "对象存储上传地址无效");
  }
  if (
    parsed.protocol !== "https:"
    || !parsed.hostname
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    return fail(409, "对象存储上传地址不符合安全要求");
  }
  // Never reserialize a signed URL: even semantically equivalent escaping may
  // change the exact V4 signature coordinate.
  return value;
}

function signedHeaders(
  value: unknown,
  prepared: PreparedFormalFileUpload,
  fileId: string,
): Readonly<Record<string, string>> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fail(409, "对象存储签名请求头无效");
  }
  const source = value as Record<string, unknown>;
  const normalized = new Map<string, string>();
  for (const [name, raw] of Object.entries(source)) {
    const key = name.toLowerCase();
    if (normalized.has(key) || typeof raw !== "string" || !raw) {
      return fail(409, "对象存储签名请求头无效");
    }
    normalized.set(key, raw);
  }
  const expected = new Map([
    ["content-type", prepared.mime_type],
    ["x-oss-meta-sha256", prepared.sha256],
    ["x-oss-meta-file-id", fileId],
    ["x-oss-forbid-overwrite", "true"],
  ]);
  if (
    normalized.size !== expected.size
    || [...expected].some(([name, expectedValue]) => normalized.get(name) !== expectedValue)
  ) {
    return fail(409, "对象存储签名请求头与文件证据不一致");
  }
  return Object.freeze(Object.fromEntries(Object.entries(source)) as Record<string, string>);
}

function projectCompletion(
  value: unknown,
  expectedFileId: string,
  expectedPurpose: FormalFilePurpose,
): Readonly<{ verifiedAt: string }> {
  const object = exactObject(value, [
    "schema_version",
    "file_id",
    "purpose",
    "status",
    "verified_at",
    "already_available",
  ], "文件完成确认");
  if (
    object.schema_version !== "1.0"
    || uuid(object.file_id, "file_id") !== expectedFileId
    || purpose(object.purpose) !== expectedPurpose
    || object.status !== "available"
    || typeof object.already_available !== "boolean"
  ) {
    return fail(409, "文件完成确认与上传意图不一致");
  }
  return { verifiedAt: timestamp(object.verified_at, "文件核验时间") };
}

export async function executeFormalFileUpload(
  prepared: PreparedFormalFileUpload,
  dependencies: FormalFileUploadDependencies = {},
): Promise<FormalFileUploadResult> {
  validateFile(prepared.file, prepared.purpose);
  if (
    prepared.file.name !== prepared.original_filename
    || prepared.file.size !== prepared.size_bytes
    || prepared.file.type !== prepared.mime_type
    || !SHA256.test(prepared.sha256)
  ) {
    return fail(409, "待上传文件与已计算证据不一致");
  }
  safeCoordinate(prepared.intent_headers["X-Request-ID"], "X-Request-ID");
  safeCoordinate(prepared.intent_headers["Idempotency-Key"], "Idempotency-Key");
  safeCoordinate(prepared.complete_headers["X-Request-ID"], "X-Request-ID");
  const requester = dependencies.requester ?? api;
  const objectFetcher = dependencies.objectFetcher ?? fetch;
  const intentPayload = await requester("/v1/files/upload-intents", {
    method: "POST",
    headers: prepared.intent_headers,
    ...jsonBody({
      purpose: prepared.purpose,
      original_filename: prepared.original_filename,
      size_bytes: prepared.size_bytes,
      mime_type: prepared.mime_type,
      sha256: prepared.sha256,
    }),
  });
  const intent = projectUploadIntent(
    intentPayload,
    prepared,
    (dependencies.now ?? Date.now)(),
  );

  let uploadFailure: unknown = null;
  if (intent.status === "pending" && intent.upload !== null) {
    try {
      const response = await objectFetcher(intent.upload.url, {
        method: "PUT",
        headers: intent.upload.headers,
        body: prepared.file,
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        cache: "no-store",
      });
      if (!response.ok) {
        uploadFailure = new ApiError(
          response.status,
          `对象存储拒绝附件上传 (${response.status})`,
          {},
        );
      }
    } catch (error) {
      uploadFailure = error;
    }
  }

  let completionPayload: unknown;
  try {
    completionPayload = await requester(
      `/v1/files/${intent.fileId}/complete`,
      {
        method: "POST",
        headers: prepared.complete_headers,
      },
    );
  } catch (completionError) {
    if (uploadFailure !== null) {
      return fail(503, "附件上传结果尚未确认；只能使用原请求坐标重试");
    }
    throw completionError;
  }
  const completion = projectCompletion(
    completionPayload,
    intent.fileId,
    prepared.purpose,
  );
  return Object.freeze({
    file_id: intent.fileId,
    purpose: prepared.purpose,
    status: "available",
    verified_at: completion.verifiedAt,
    sha256: prepared.sha256,
    size_bytes: prepared.size_bytes,
    mime_type: prepared.mime_type,
  });
}


export const FORMAL_FILE_MAXIMUM_SIZE_BYTES = MAXIMUM_FILE_SIZE;
