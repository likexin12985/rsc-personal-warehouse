import { ApiError } from "./api";
import { validateMaterialRequestAllocationOption, type MaterialRequestAllocationOption } from "./formalMaterialRequestAllocationOptions";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
export type ReservationSerialOption = Readonly<{ serial_id: string; serial_no: string; qr_code: string; lot_id: string | null }>;
export type MaterialRequestReservationOption = MaterialRequestAllocationOption & Readonly<{
  allocation_id: string; allocation_no: string; allocated_qty: string; reserved_qty: string;
  remaining_qty: string; reservable_qty: string; tracking_mode: "none" | "lot" | "serial" | "lot_and_serial";
  serial_options: readonly ReservationSerialOption[];
}>;
export type MaterialRequestReservationOptionPage = Readonly<{
  schema_version: "1.0"; request_id: string; request_line_id: string; request_version: number;
  current_revision_id: string; current_revision_no: number; material_id: string;
  projection_status: "ready"; opening_balance_status: "established"; projected_at: string | null;
  ledger_cursor: number; items: readonly MaterialRequestReservationOption[];
}>;
function fail(message: string): never { throw new ApiError(409, message); }
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail("预留候选必须为对象");
  if (Object.keys(value).sort().join(",") !== [...keys].sort().join(",")) return fail("预留候选字段缺失或包含未知字段");
  return value as Record<string, unknown>;
}
function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) return fail("预留候选标识无效");
  return value.toLowerCase();
}
function text(value: unknown, max: number): string {
  if (typeof value !== "string" || !value || value.trim() !== value || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) return fail("预留候选文本无效");
  return value;
}
function integer(value: unknown, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) return fail("预留候选版本或游标无效");
  return value as number;
}
export function reservationQuantityUnits(value: string): bigint {
  if (!DECIMAL.test(value)) return fail("预留数量格式无效，最多保留三位小数");
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
}
function quantity(value: unknown, scale: number): string {
  const pattern = new RegExp(`^(?:0|[1-9]\\d{0,14})${scale ? `\\.\\d{${scale}}` : ""}$`);
  if (typeof value !== "string" || !pattern.test(value)) return fail("预留候选数量无效");
  return value;
}
export function reservationUsesSerials(option: MaterialRequestReservationOption): boolean {
  return option.tracking_mode === "serial" || option.tracking_mode === "lot_and_serial";
}
function option(value: unknown): MaterialRequestReservationOption {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail("预留候选明细无效");
  const { allocation_id, allocation_no, allocated_qty, reserved_qty, remaining_qty, reservable_qty, tracking_mode, serial_options, ...source } = value as Record<string, unknown>;
  const base = validateMaterialRequestAllocationOption(source);
  if (!["none", "lot", "serial", "lot_and_serial"].includes(tracking_mode as string)) return fail("预留候选追踪方式无效");
  if (!Array.isArray(serial_options) || serial_options.length > 1000) return fail("预留 SN 候选无效");
  const serials = serial_options.map((value) => {
    const serial = exact(value, ["serial_id", "serial_no", "qr_code", "lot_id"]);
    return Object.freeze({ serial_id: uuid(serial.serial_id), serial_no: text(serial.serial_no, 200), qr_code: text(serial.qr_code, 250), lot_id: serial.lot_id === null ? null : uuid(serial.lot_id) });
  });
  const result: MaterialRequestReservationOption = { ...base, allocation_id: uuid(allocation_id), allocation_no: text(allocation_no, 100), allocated_qty: quantity(allocated_qty, base.quantity_scale), reserved_qty: quantity(reserved_qty, base.quantity_scale), remaining_qty: quantity(remaining_qty, base.quantity_scale), reservable_qty: quantity(reservable_qty, base.quantity_scale), tracking_mode: tracking_mode as MaterialRequestReservationOption["tracking_mode"], serial_options: Object.freeze(serials) };
  const units = reservationQuantityUnits;
  const usesSerials = reservationUsesSerials(result);
  const lotTracked = tracking_mode === "lot" || tracking_mode === "lot_and_serial";
  if (units(result.allocated_qty) <= 0n || units(result.remaining_qty) !== units(result.allocated_qty) - units(result.reserved_qty)
      || units(result.reservable_qty) <= 0n || units(base.quantity) <= 0n
      || Boolean(base.lot_id) !== lotTracked || Boolean(base.lot_no) !== lotTracked
      || (!usesSerials && serials.length !== 0)
      || serials.some((serial) => serial.lot_id !== base.lot_id)
      || new Set(serials.map((serial) => serial.serial_id)).size !== serials.length
      || new Set(serials.map((serial) => serial.serial_no)).size !== serials.length
      || new Set(serials.map((serial) => serial.qr_code)).size !== serials.length) return fail("预留候选数量、批次或 SN 关系无效");
  const maximum = [units(result.remaining_qty), units(base.quantity), ...(usesSerials ? [BigInt(serials.length) * 1000n] : [])].reduce((a, b) => a < b ? a : b);
  if (units(result.reservable_qty) !== maximum || (usesSerials && (units(result.reservable_qty) % 1000n !== 0n))) return fail("预留候选可预留数量不一致");
  return Object.freeze(result);
}
export function validateMaterialRequestReservationOptionPage(value: unknown): MaterialRequestReservationOptionPage {
  const row = exact(value, ["schema_version", "request_id", "request_line_id", "request_version", "current_revision_id", "current_revision_no", "material_id", "projection_status", "opening_balance_status", "projected_at", "ledger_cursor", "items"]);
  if (row.schema_version !== "1.0" || row.projection_status !== "ready" || row.opening_balance_status !== "established") return fail("预留候选投影未就绪");
  if (row.projected_at !== null && (typeof row.projected_at !== "string" || !TIMESTAMP.test(row.projected_at) || !Number.isFinite(Date.parse(row.projected_at)))) return fail("预留候选投影时间无效");
  if (!Array.isArray(row.items) || row.items.length > 100) return fail("预留候选数量超出范围");
  const items = row.items.map(option); const materialId = uuid(row.material_id); const cursor = integer(row.ledger_cursor);
  if (new Set(items.map((item) => item.allocation_id)).size !== items.length || items.some((item) => item.material_id !== materialId || item.ledger_cursor > cursor)) return fail("预留候选分配、物料或游标不一致");
  return Object.freeze({ schema_version: "1.0", request_id: uuid(row.request_id), request_line_id: uuid(row.request_line_id), request_version: integer(row.request_version, 1), current_revision_id: uuid(row.current_revision_id), current_revision_no: integer(row.current_revision_no, 1), material_id: materialId, projection_status: "ready", opening_balance_status: "established", projected_at: row.projected_at as string | null, ledger_cursor: cursor, items: Object.freeze(items) });
}
