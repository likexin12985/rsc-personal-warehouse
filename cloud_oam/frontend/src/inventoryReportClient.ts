import { api, apiNoReplay, createIdempotencyKey } from "./api";

const ROOT = "/v1/reports/inventory-balances/exports";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COORDINATE = /^inventory-export-[0-9a-f]{36}$/;

export type InventoryReportStatus = Readonly<{
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string;
  completed_at: string | null;
  download_count: number;
  file_available: boolean;
}>;

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("报表响应结构无效");
  return value as Record<string, unknown>;
}

function parseStatus(value: unknown): InventoryReportStatus {
  const row = record(value);
  if (
    typeof row.job_id !== "string" || !UUID.test(row.job_id)
    || !["queued", "running", "succeeded", "failed", "cancelled"].includes(String(row.status))
    || typeof row.created_at !== "string" || !Number.isFinite(Date.parse(row.created_at))
    || !(row.completed_at === null || typeof row.completed_at === "string" && Number.isFinite(Date.parse(row.completed_at)))
    || !Number.isSafeInteger(row.download_count) || (row.download_count as number) < 0
    || typeof row.file_available !== "boolean"
    || (row.file_available && row.status !== "succeeded")
  ) throw new Error("报表任务响应未通过校验");
  return row as InventoryReportStatus;
}

export async function inventoryReportAvailable(): Promise<boolean> {
  const row = record(await api<unknown>(`${ROOT}/capabilities`, { cache: "no-store" }));
  if (typeof row.available !== "boolean") throw new Error("报表能力响应无效");
  return row.available;
}

export async function recoverInventoryReport(key: string): Promise<InventoryReportStatus> {
  return parseStatus(await api<unknown>(`${ROOT}/recovery`, {
    headers: { "Idempotency-Key": key }, cache: "no-store",
  }));
}

export async function readInventoryReport(jobId: string): Promise<InventoryReportStatus> {
  if (!UUID.test(jobId)) throw new Error("报表任务编号无效");
  return parseStatus(await api<unknown>(`${ROOT}/${jobId}`, { cache: "no-store" }));
}

export function newInventoryReportKey(): string {
  return createIdempotencyKey("inventory-export");
}

export async function requestInventoryReport(key: string): Promise<void> {
  if (!COORDINATE.test(key)) throw new Error("报表申请坐标无效");
  const row = record(await apiNoReplay<unknown>(ROOT, {
    method: "POST", headers: {
      "Idempotency-Key": key,
      "X-Request-ID": createIdempotencyKey("inventory-request"),
    },
  }));
  if (typeof row.job_id !== "string" || !UUID.test(row.job_id)
    || !["queued", "running", "succeeded", "failed"].includes(String(row.status))) {
    throw new Error("报表申请回执无效；请按原坐标找回，勿重复申请");
  }
}

export async function createInventoryReportDownload(jobId: string): Promise<{ url: string; expires_at: string }> {
  if (!UUID.test(jobId)) throw new Error("报表任务编号无效");
  const row = record(await apiNoReplay<unknown>(`${ROOT}/${jobId}/download-intents`, {
    method: "POST", headers: { "X-Request-ID": createIdempotencyKey("inventory-download") },
  }));
  let url: URL;
  try { url = new URL(String(row.url)); } catch { throw new Error("报表下载地址无效"); }
  if (
    row.job_id !== jobId || typeof row.file_id !== "string" || !UUID.test(row.file_id)
    || url.protocol !== "https:" || !!url.username || !!url.password
    || typeof row.expires_at !== "string" || Date.parse(row.expires_at) <= Date.now()
    || !Number.isSafeInteger(row.download_count) || (row.download_count as number) < 1
  ) throw new Error("报表下载意图未通过校验");
  return { url: url.toString(), expires_at: row.expires_at };
}

function storageKey(personId: string, authorizationVersion: number): string {
  if (!UUID.test(personId) || !Number.isSafeInteger(authorizationVersion) || authorizationVersion < 1) {
    throw new Error("报表申请身份无效");
  }
  return `rsc-inventory-report-v1:${personId}:${authorizationVersion}`;
}

export function readInventoryReportKey(personId: string, authorizationVersion: number): string | null {
  const value = sessionStorage.getItem(storageKey(personId, authorizationVersion));
  if (value !== null && !COORDINATE.test(value)) throw new Error("报表恢复标记损坏，已停止新申请");
  return value;
}

export function saveInventoryReportKey(personId: string, authorizationVersion: number, key: string): void {
  if (!COORDINATE.test(key)) throw new Error("报表申请坐标无效");
  sessionStorage.setItem(storageKey(personId, authorizationVersion), key);
  if (readInventoryReportKey(personId, authorizationVersion) !== key) throw new Error("报表申请坐标未保存");
}

export function clearInventoryReportKey(personId: string, authorizationVersion: number): void {
  sessionStorage.removeItem(storageKey(personId, authorizationVersion));
}
