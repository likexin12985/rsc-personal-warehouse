import { ApiError, api, apiBinary } from "./api";

const ROOT = "/v1/stocktakes/opening/imports/opening-count";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
export const OPENING_IMPORT_MAX_BYTES = 8 * 1024 * 1024;
const SHA256 = /^[0-9a-f]{64}$/;

export type OpeningCountFormatError = Readonly<{
  row: number;
  field: string;
  code: string;
  message: string;
}>;

export type OpeningCountFormatResult = Readonly<{
  source_sha256: string;
  row_count: number;
  format_valid: boolean;
  payload_sha256: string | null;
  errors: readonly OpeningCountFormatError[];
}>;

function validateSource(file: File): void {
  if (file.size < 1 || file.size > OPENING_IMPORT_MAX_BYTES || !file.name.toLowerCase().endsWith(".xlsx")) {
    throw new Error("请选择不超过 8 MiB 的 XLSX 文件");
  }
}

function validateResult(value: unknown): OpeningCountFormatResult {
  if (!value || typeof value !== "object") throw new Error("格式预检响应无效");
  const row = value as Record<string, unknown>;
  if (row.schema_version !== "1.0" || row.format !== "opening_count_import_v1"
      || typeof row.source_sha256 !== "string" || !SHA256.test(row.source_sha256)
      || !Number.isInteger(row.row_count) || (row.row_count as number) < 1 || (row.row_count as number) > 10000
      || typeof row.format_valid !== "boolean" || !Array.isArray(row.errors) || row.errors.length > 1000
      || !(row.payload_sha256 === null || typeof row.payload_sha256 === "string" && SHA256.test(row.payload_sha256))
      || row.format_valid !== (row.errors.length === 0 && row.payload_sha256 !== null)) {
    throw new Error("格式预检响应不符合约定");
  }
  const errors: OpeningCountFormatError[] = [];
  for (const item of row.errors) {
    if (!item || typeof item !== "object") throw new Error("格式预检错误报告无效");
    const error = item as Record<string, unknown>;
    if (!Number.isInteger(error.row) || (error.row as number) < 2 || (error.row as number) > 10001
        || typeof error.field !== "string" || error.field.length < 1 || error.field.length > 80
        || typeof error.code !== "string" || error.code.length < 1 || error.code.length > 80
        || typeof error.message !== "string" || error.message.length < 1 || error.message.length > 200) {
      throw new Error("格式预检错误报告无效");
    }
    errors.push(error as OpeningCountFormatError);
  }
  return {
    source_sha256: row.source_sha256,
    row_count: row.row_count as number,
    format_valid: row.format_valid,
    payload_sha256: row.payload_sha256 as string | null,
    errors,
  };
}

async function xlsxBlob(path: string, file?: File): Promise<Blob> {
  const response = await apiBinary(path, file ? {
    method: "POST",
    headers: { "Content-Type": XLSX },
    body: file,
    cache: "no-store",
  } : { cache: "no-store" });
  if (response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== XLSX) {
    throw new ApiError(502, "文件响应格式错误，已停止下载");
  }
  const blob = await response.blob();
  if (blob.size < 1 || blob.size > OPENING_IMPORT_MAX_BYTES) {
    throw new ApiError(502, "文件响应大小错误，已停止下载");
  }
  return blob;
}

export function openingCountTemplate(): Promise<Blob> {
  return xlsxBlob(`${ROOT}/template`);
}

export async function checkOpeningCountFormat(file: File): Promise<OpeningCountFormatResult> {
  validateSource(file);
  const result = await api<unknown>(`${ROOT}/format-check`, {
    method: "POST",
    headers: { "Content-Type": XLSX },
    body: file,
    cache: "no-store",
  });
  return validateResult(result);
}

export function openingCountFormatErrors(file: File): Promise<Blob> {
  validateSource(file);
  return xlsxBlob(`${ROOT}/error-report`, file);
}

export function saveOpeningCountWorkbook(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  try { link.click(); }
  finally {
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
