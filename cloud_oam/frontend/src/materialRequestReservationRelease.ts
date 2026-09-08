import { validateMaterialRequestStateAxes, type MaterialRequestStateAxes } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
export function releaseUnits(value: unknown): bigint {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(value)) throw new Error("释放数量必须是最多三位小数的数字");
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
}
function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || !same(Object.keys(value).sort(), [...keys].sort())) throw new Error("释放响应字段不完整或包含未知字段");
  return value as Record<string, unknown>;
}
function id(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) throw new Error("释放对象坐标无效"); return value; }
function integer(value: unknown, minimum = 0): number { if (!Number.isSafeInteger(value) || (value as number) < minimum) throw new Error("释放版本无效"); return value as number; }
function text(value: unknown, max = 500): string { if (typeof value !== "string" || !value || value !== value.trim() || value.length > max || /[\x00-\x1f\x7f]/.test(value)) throw new Error("释放文本无效"); return value; }
function amount(value: unknown, positive = false): string {
  const quantity = releaseUnits(value); if (typeof value !== "string" || !/\.\d{3}$/.test(value) || (positive && quantity === 0n)) throw new Error("释放数量格式无效"); return value;
}
function serials(value: unknown): readonly string[] {
  if (!Array.isArray(value) || value.length > 1000) throw new Error("释放 SN 列表无效");
  const ids = value.map(id); if (new Set(ids).size !== ids.length) throw new Error("释放 SN 重复"); return Object.freeze(ids);
}
export type ReleaseInput = Readonly<{ expected_request_version: number; reservation_id: string; released_qty: string; reason: string; source_balance_version: number; source_ledger_cursor: number; serial_ids: readonly string[] }>;
export function validateReleaseInput(value: unknown): ReleaseInput {
  const row = exact(value, ["expected_request_version", "reservation_id", "released_qty", "reason", "source_balance_version", "source_ledger_cursor", "serial_ids"]);
  return { expected_request_version: integer(row.expected_request_version), reservation_id: id(row.reservation_id), released_qty: amount(row.released_qty, true), reason: text(row.reason), source_balance_version: integer(row.source_balance_version), source_ledger_cursor: integer(row.source_ledger_cursor), serial_ids: serials(row.serial_ids) };
}
export type ReleaseOption = Readonly<{
  reservation_id: string; reservation_no: string; allocation_id: string; reserved_qty: string; released_qty: string; releasable_qty: string;
  material_name: string; sku_code: string; location_name: string; tracking_mode: "none" | "lot" | "serial" | "lot_and_serial";
  quantity_scale: number; allow_fraction: boolean; source_stock_account_id: string; target_stock_account_id: string;
  source_balance_version: number; source_ledger_cursor: number; serials: readonly Readonly<{ serial_id: string; serial_no: string }>[];
}>;
export type ReleasePage = Readonly<{ schema_version: "1.0"; request_id: string; request_line_id: string; request_version: number; revision_id: string; revision_no: number; state_axes: MaterialRequestStateAxes; items: readonly ReleaseOption[] }>;
export function validateReleasePage(value: unknown): ReleasePage {
  const row = exact(value, ["schema_version", "request_id", "request_line_id", "request_version", "revision_id", "revision_no", "state_axes", "items"]);
  if (row.schema_version !== "1.0" || !Array.isArray(row.items) || row.items.length > 100) throw new Error("释放候选版本或范围无效");
  let serialCount = 0;
  const items = row.items.map((value): ReleaseOption => {
    const item = exact(value, ["reservation_id", "reservation_no", "allocation_id", "reserved_qty", "released_qty", "releasable_qty", "material_name", "sku_code", "location_name", "tracking_mode", "quantity_scale", "allow_fraction", "source_stock_account_id", "target_stock_account_id", "source_balance_version", "source_ledger_cursor", "serials"]);
    if (!Array.isArray(item.serials) || !["none", "lot", "serial", "lot_and_serial"].includes(String(item.tracking_mode)) || typeof item.allow_fraction !== "boolean") throw new Error("释放追踪策略无效");
    serialCount += item.serials.length; if (serialCount > 1000) throw new Error("释放 SN 候选过多");
    const options = item.serials.map(value => { const s = exact(value, ["serial_id", "serial_no"]); return { serial_id: id(s.serial_id), serial_no: text(s.serial_no) }; });
    if (new Set(options.map(s => s.serial_id)).size !== options.length) throw new Error("释放候选 SN 重复");
    const reserved = amount(item.reserved_qty, true), released = amount(item.released_qty), capacity = amount(item.releasable_qty, true);
    if (releaseUnits(released) + releaseUnits(capacity) > releaseUnits(reserved)) throw new Error("释放候选超过原占用额度");
    const scale = integer(item.quantity_scale); if (scale > 3) throw new Error("物料数量精度无效");
    const usesSerial = ["serial", "lot_and_serial"].includes(String(item.tracking_mode));
    if (usesSerial ? releaseUnits(capacity) > BigInt(options.length) * 1000n : options.length !== 0) throw new Error("释放 SN 与可释放数量不一致");
    const source = id(item.source_stock_account_id), target = id(item.target_stock_account_id); if (source === target) throw new Error("释放账户必须不同");
    return { reservation_id: id(item.reservation_id), reservation_no: text(item.reservation_no, 100), allocation_id: id(item.allocation_id), reserved_qty: reserved, released_qty: released, releasable_qty: capacity,
      material_name: text(item.material_name), sku_code: text(item.sku_code), location_name: text(item.location_name), tracking_mode: item.tracking_mode as ReleaseOption["tracking_mode"],
      quantity_scale: scale, allow_fraction: item.allow_fraction, source_stock_account_id: source, target_stock_account_id: target,
      source_balance_version: integer(item.source_balance_version), source_ledger_cursor: integer(item.source_ledger_cursor), serials: options };
  });
  if (new Set(items.map(item => item.reservation_id)).size !== items.length) throw new Error("原占用候选重复");
  return { schema_version: "1.0", request_id: id(row.request_id), request_line_id: id(row.request_line_id), request_version: integer(row.request_version), revision_id: id(row.revision_id), revision_no: integer(row.revision_no, 1), state_axes: validateMaterialRequestStateAxes(row.state_axes), items };
}
export type ReleaseResult = Readonly<{
  schema_version: "1.0"; kind: "reservation_release"; release_id: string; release_no: string; reservation_id: string; allocation_id: string;
  request_id: string; request_no: string; request_line_id: string; revision_id: string; revision_no: number;
  request_version: number; current_request_version: number; released_qty: string; reason: string;
  source_stock_account_id: string; target_stock_account_id: string; source_balance_version: number; source_ledger_cursor: number;
  release_transaction_id: string; release_transaction_no: string; serial_ids: readonly string[]; state_axes: MaterialRequestStateAxes; idempotency_replayed: boolean;
}>;
export function validateReleaseResult(value: unknown): ReleaseResult {
  const row = exact(value, ["schema_version", "kind", "release_id", "release_no", "reservation_id", "allocation_id", "request_id", "request_no", "request_line_id", "revision_id", "revision_no", "request_version", "current_request_version", "released_qty", "reason", "source_stock_account_id", "target_stock_account_id", "source_balance_version", "source_ledger_cursor", "release_transaction_id", "release_transaction_no", "serial_ids", "state_axes", "idempotency_replayed"]);
  if (row.schema_version !== "1.0" || row.kind !== "reservation_release" || typeof row.idempotency_replayed !== "boolean") throw new Error("释放命令响应版本无效");
  const axes = validateMaterialRequestStateAxes(row.state_axes), ids = serials(row.serial_ids), quantity = amount(row.released_qty, true);
  if (!["partially_released", "released"].includes(axes.reservation_status) || (ids.length > 0 && releaseUnits(quantity) !== BigInt(ids.length) * 1000n)) throw new Error("释放状态或 SN 数量无效");
  const version = integer(row.request_version, 1), current = integer(row.current_request_version, version);
  const source = id(row.source_stock_account_id), target = id(row.target_stock_account_id); if (source === target) throw new Error("释放账户必须不同");
  return { schema_version: "1.0", kind: "reservation_release", release_id: id(row.release_id), release_no: text(row.release_no, 100), reservation_id: id(row.reservation_id), allocation_id: id(row.allocation_id), request_id: id(row.request_id), request_no: text(row.request_no, 100), request_line_id: id(row.request_line_id), revision_id: id(row.revision_id), revision_no: integer(row.revision_no, 1), request_version: version, current_request_version: current, released_qty: quantity, reason: text(row.reason), source_stock_account_id: source, target_stock_account_id: target, source_balance_version: integer(row.source_balance_version), source_ledger_cursor: integer(row.source_ledger_cursor), release_transaction_id: id(row.release_transaction_id), release_transaction_no: text(row.release_transaction_no, 100), serial_ids: ids, state_axes: axes, idempotency_replayed: row.idempotency_replayed };
}
export function validateReleaseStatus(value: unknown): ReleaseResult | null {
  const row = exact(value, ["schema_version", "lookup_status", "command"]);
  if (row.schema_version !== "1.0") throw new Error("释放核验版本无效");
  if (row.lookup_status === "not_observed" && row.command === null) return null;
  if (row.lookup_status !== "confirmed") throw new Error("释放核验状态无效");
  const result = validateReleaseResult(row.command); if (!result.idempotency_replayed) throw new Error("释放历史标记无效"); return result;
}

export type ReleaseSentinel = Readonly<{ v: 1; kind: "reservation_release"; trace: string; person_id: string; authorization_version: number; request_id: string; request_line_id: string; revision_id: string; revision_no: number; allocation_id: string; source_stock_account_id: string; target_stock_account_id: string; state_axes: MaterialRequestStateAxes; input: ReleaseInput }>;
type Read = Readonly<{ kind: "missing" | "corrupt" | "unavailable" }> | Readonly<{ kind: "valid"; value: ReleaseSentinel }>;
export type ReleaseStore = Readonly<{ read(): Read; persist(value: ReleaseSentinel): void; clear(trace: string): void }>;
export function validateReleaseSentinel(value: unknown): ReleaseSentinel {
  const row = exact(value, ["v", "kind", "trace", "person_id", "authorization_version", "request_id", "request_line_id", "revision_id", "revision_no", "allocation_id", "source_stock_account_id", "target_stock_account_id", "state_axes", "input"]);
  if (row.v !== 1 || row.kind !== "reservation_release" || typeof row.trace !== "string" || !TRACE.test(row.trace)) throw new Error("原释放请求坐标无效");
  return { v: 1, kind: "reservation_release", trace: row.trace, person_id: id(row.person_id), authorization_version: integer(row.authorization_version, 1), request_id: id(row.request_id), request_line_id: id(row.request_line_id), revision_id: id(row.revision_id), revision_no: integer(row.revision_no, 1), allocation_id: id(row.allocation_id), source_stock_account_id: id(row.source_stock_account_id), target_stock_account_id: id(row.target_stock_account_id), state_axes: validateMaterialRequestStateAxes(row.state_axes), input: validateReleaseInput(row.input) };
}
export function createReleaseStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): ReleaseStore {
  const key = "cloud-oam-material-request-reservation-release-v1";
  const target = () => storage ?? window.localStorage;
  const read = (): Read => {
    let raw: string | null; try { raw = target().getItem(key); } catch { return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateReleaseSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  };
  return { read, persist(value) {
    validateReleaseSentinel(value); if (read().kind !== "missing") throw new Error("已有待核验释放，禁止覆盖");
    target().setItem(key, JSON.stringify(value)); const saved = read(); if (saved.kind !== "valid" || !same(saved.value, value)) throw new Error("释放坐标保存后回读失败");
  }, clear(trace) {
    const current = read(); if (current.kind !== "valid" || current.value.trace !== trace) throw new Error("原释放坐标已变化，禁止清除");
    target().removeItem(key); if (read().kind !== "missing") throw new Error("释放核验记录清理失败");
  } };
}
