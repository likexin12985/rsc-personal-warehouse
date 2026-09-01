import { ApiError } from "./api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const TRACKING_MODES = ["none", "lot", "serial", "lot_and_serial"] as const;

export type FormalMaterialCatalogItem = Readonly<{
  material_id: string;
  sku_code: string;
  name: string;
  specification: string;
  base_unit: string;
  tracking_mode: typeof TRACKING_MODES[number];
  quantity_scale: number;
  allow_fraction: boolean;
  source_updated_at: string;
}>;

export type FormalMaterialCatalogPage = Readonly<{
  schema_version: "1.0";
  items: FormalMaterialCatalogItem[];
  next_after_id: string | null;
}>;

function invalid(message: string): never {
  throw new ApiError(409, message);
}

function exactObject(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid(`${name}不是有效对象`);
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return invalid(`${name}必须精确包含正式字段`);
  }
  return object;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) {
    return invalid(`${name}无效`);
  }
  return value.toLowerCase();
}

function text(value: unknown, name: string, maximum: number, allowEmpty = false): string {
  if (typeof value !== "string" || value !== value.trim() || value.length > maximum
      || (!allowEmpty && value.length === 0) || /[\u0000-\u001f\u007f]/.test(value)) {
    return invalid(`${name}无效`);
  }
  return value;
}

function item(value: unknown): FormalMaterialCatalogItem {
  const object = exactObject(value, [
    "material_id", "sku_code", "name", "specification", "base_unit", "tracking_mode",
    "quantity_scale", "allow_fraction", "source_updated_at",
  ], "正式物料目录项");
  if (!TRACKING_MODES.includes(object.tracking_mode as typeof TRACKING_MODES[number])) {
    return invalid("tracking_mode无效");
  }
  if (!Number.isSafeInteger(object.quantity_scale)
      || (object.quantity_scale as number) < 0 || (object.quantity_scale as number) > 3) {
    return invalid("quantity_scale无效");
  }
  if (typeof object.allow_fraction !== "boolean") return invalid("allow_fraction无效");
  const updatedAt = text(object.source_updated_at, "source_updated_at", 80);
  if (!AWARE_TIMESTAMP.test(updatedAt) || !Number.isFinite(Date.parse(updatedAt))) {
    return invalid("source_updated_at无效");
  }
  return Object.freeze({
    material_id: uuid(object.material_id, "material_id"),
    sku_code: text(object.sku_code, "sku_code", 80),
    name: text(object.name, "name", 200),
    specification: text(object.specification, "specification", 300, true),
    base_unit: text(object.base_unit, "base_unit", 32),
    tracking_mode: object.tracking_mode as typeof TRACKING_MODES[number],
    quantity_scale: object.quantity_scale as number,
    allow_fraction: object.allow_fraction,
    source_updated_at: updatedAt,
  });
}

export function validateFormalMaterialCatalogPage(value: unknown): FormalMaterialCatalogPage {
  const object = exactObject(value, ["schema_version", "items", "next_after_id"], "正式物料目录分页");
  if (object.schema_version !== "1.0") return invalid("正式物料目录版本不受支持");
  if (!Array.isArray(object.items)) return invalid("正式物料目录items无效");
  if (object.items.length > 100) return invalid("正式物料目录分页超过服务端上限");
  const items = object.items.map(item);
  if (new Set(items.map((row) => row.material_id)).size !== items.length
      || new Set(items.map((row) => row.sku_code)).size !== items.length) {
    return invalid("正式物料目录分页包含重复物料或SKU");
  }
  const nextAfterId = object.next_after_id === null
    ? null
    : uuid(object.next_after_id, "next_after_id");
  if (nextAfterId && items.some((row) => row.material_id === nextAfterId)) {
    return invalid("正式物料目录游标指向当前页对象");
  }
  return Object.freeze({ schema_version: "1.0", items, next_after_id: nextAfterId });
}

export function formalMaterialCatalogQuery(value: string): string {
  if (value !== value.trim() || value.length > 100 || /[\u0000-\u001f\u007f]/.test(value)) {
    return invalid("物料检索词无效");
  }
  return value;
}
