import { ApiError } from "./api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const CONDITIONS = new Set(["new", "used", "damaged", "scrapped"]);

export type MaterialRequestAllocationOption = Readonly<{
  stock_account_id: string; owner_org_id: string; owner_org_code: string; owner_org_name: string;
  location_owner_org_id: string; location_owner_org_code: string; location_owner_org_name: string;
  location_id: string; location_code: string; location_name: string; location_type: string;
  location_parent_id: string | null; custodian_person_id: string | null; custodian_person_name: string | null;
  material_id: string; sku_code: string; material_name: string; base_unit: string;
  condition_code: "new" | "used" | "damaged" | "scrapped"; availability_bucket: "available";
  lot_id: string | null; lot_no: string | null; quantity: string; quantity_scale: number;
  balance_version: number; ledger_cursor: number;
}>;

export type MaterialRequestAllocationOptionPage = Readonly<{
  schema_version: "1.0"; request_id: string; request_line_id: string; request_version: number;
  current_revision_id: string; current_revision_no: number; material_id: string;
  final_approved_qty: string; cancelled_qty: string; allocatable_qty: string;
  projection_status: "ready"; opening_balance_status: "established"; projected_at: string | null;
  ledger_cursor: number; items: readonly MaterialRequestAllocationOption[];
}>;

function fail(message: string): never { throw new ApiError(409, message); }
function object(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(`${name}不是有效对象`);
  const actual = Object.keys(value as object).sort(); const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) return fail(`${name}字段不完整或包含未知字段`);
  return value as Record<string, unknown>;
}
function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) return fail(`${name}无效`);
  return value.toLowerCase();
}
function text(value: unknown, name: string, max = 200): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max || value.trim() !== value || /[\u0000-\u001f\u007f]/.test(value)) return fail(`${name}无效`);
  return value;
}
function optionalText(value: unknown, name: string, max = 200): string | null {
  if (value === null) return null; return text(value, name, max);
}
function nonnegative(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) return fail(`${name}无效`); return value as number;
}
function quantity(value: unknown, name: string, scale: number | null = null): string {
  const scaled = scale === null ? null : scale === 0 ? /^\d+$/ : new RegExp(`^\\d+\\.${"\\d".repeat(scale)}$`);
  if (typeof value !== "string" || !DECIMAL.test(value) || (scaled !== null && !scaled.test(value))) return fail(`${name}无效`);
  return value;
}
function option(value: unknown): MaterialRequestAllocationOption {
  const row = object(value, [
    "stock_account_id", "owner_org_id", "owner_org_code", "owner_org_name", "location_owner_org_id",
    "location_owner_org_code", "location_owner_org_name", "location_id", "location_code", "location_name",
    "location_type", "location_parent_id", "custodian_person_id", "custodian_person_name", "material_id",
    "sku_code", "material_name", "base_unit", "condition_code", "availability_bucket", "lot_id", "lot_no",
    "quantity", "quantity_scale", "balance_version", "ledger_cursor",
  ], "货源候选");
  const quantityScale = nonnegative(row.quantity_scale, "quantity_scale");
  if (quantityScale > 3) return fail("quantity_scale无效");
  const condition = row.condition_code;
  if (typeof condition !== "string" || !CONDITIONS.has(condition)) return fail("condition_code无效");
  if (row.availability_bucket !== "available") return fail("availability_bucket无效");
  return Object.freeze({
    stock_account_id: uuid(row.stock_account_id, "stock_account_id"), owner_org_id: uuid(row.owner_org_id, "owner_org_id"),
    owner_org_code: text(row.owner_org_code, "owner_org_code", 100), owner_org_name: text(row.owner_org_name, "owner_org_name"),
    location_owner_org_id: uuid(row.location_owner_org_id, "location_owner_org_id"), location_owner_org_code: text(row.location_owner_org_code, "location_owner_org_code", 100),
    location_owner_org_name: text(row.location_owner_org_name, "location_owner_org_name"), location_id: uuid(row.location_id, "location_id"),
    location_code: text(row.location_code, "location_code", 100), location_name: text(row.location_name, "location_name"), location_type: text(row.location_type, "location_type", 24),
    location_parent_id: row.location_parent_id === null ? null : uuid(row.location_parent_id, "location_parent_id"),
    custodian_person_id: row.custodian_person_id === null ? null : uuid(row.custodian_person_id, "custodian_person_id"), custodian_person_name: optionalText(row.custodian_person_name, "custodian_person_name"),
    material_id: uuid(row.material_id, "material_id"), sku_code: text(row.sku_code, "sku_code", 80), material_name: text(row.material_name, "material_name"), base_unit: text(row.base_unit, "base_unit", 32),
    condition_code: condition as MaterialRequestAllocationOption["condition_code"], availability_bucket: "available", lot_id: row.lot_id === null ? null : uuid(row.lot_id, "lot_id"), lot_no: optionalText(row.lot_no, "lot_no", 160),
    quantity: quantity(row.quantity, "quantity", quantityScale), quantity_scale: quantityScale, balance_version: nonnegative(row.balance_version, "balance_version"), ledger_cursor: nonnegative(row.ledger_cursor, "ledger_cursor"),
  });
}

export function validateMaterialRequestAllocationOptionPage(value: unknown): MaterialRequestAllocationOptionPage {
  const row = object(value, ["schema_version", "request_id", "request_line_id", "request_version", "current_revision_id", "current_revision_no", "material_id", "final_approved_qty", "cancelled_qty", "allocatable_qty", "projection_status", "opening_balance_status", "projected_at", "ledger_cursor", "items"], "货源候选响应");
  if (row.schema_version !== "1.0" || row.projection_status !== "ready" || row.opening_balance_status !== "established") return fail("货源候选响应状态无效");
  if (!Array.isArray(row.items) || row.items.length > 1000) return fail("货源候选响应明细无效");
  if (row.projected_at !== null && (typeof row.projected_at !== "string" || !TIMESTAMP.test(row.projected_at))) return fail("货源候选时间无效");
  return Object.freeze({
    schema_version: "1.0", request_id: uuid(row.request_id, "request_id"), request_line_id: uuid(row.request_line_id, "request_line_id"), request_version: nonnegative(row.request_version, "request_version"), current_revision_id: uuid(row.current_revision_id, "current_revision_id"), current_revision_no: nonnegative(row.current_revision_no, "current_revision_no"), material_id: uuid(row.material_id, "material_id"),
    final_approved_qty: quantity(row.final_approved_qty, "final_approved_qty"), cancelled_qty: quantity(row.cancelled_qty, "cancelled_qty"), allocatable_qty: quantity(row.allocatable_qty, "allocatable_qty"), projection_status: "ready", opening_balance_status: "established", projected_at: row.projected_at as string | null, ledger_cursor: nonnegative(row.ledger_cursor, "ledger_cursor"), items: Object.freeze(row.items.map(option)),
  });
}
