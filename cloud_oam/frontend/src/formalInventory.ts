import { ApiError } from "./api";
import type { InventoryAccountPage, InventorySummary } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const DECIMAL_QUANTITY = /^(?:0|[1-9]\d*)\.\d{3}$/;
const LOCATION_TYPES = ["headquarters", "region", "personal", "transit", "quarantine"] as const;
const TRACKING_MODES = ["none", "lot", "serial", "lot_and_serial"] as const;
const CONDITIONS = ["new", "used", "damaged", "scrapped"] as const;
const AVAILABILITY_BUCKETS = [
  "available", "reserved", "picking", "outbound", "in_transit",
  "arrived_pending", "frozen", "return_pending", "scrap_pending",
] as const;
const QUANTITY_FIELDS = [
  "physical_in_stock_qty",
  "available_qty",
  "reserved_qty",
  "committed_qty",
  "frozen_qty",
  "physical_in_transit_qty",
] as const;

function invalid(message: string): never {
  throw new ApiError(409, message);
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalid("正式库存响应不是有效对象");
  }
  return value as Record<string, unknown>;
}

function field(object: Record<string, unknown>, name: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(object, name)) {
    return invalid(`正式库存响应缺少字段 ${name}`);
  }
  return object[name];
}

function exactEnum<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return invalid(`正式库存响应包含未知 ${name}`);
  }
  return value as T;
}

function timestamp(value: unknown): string | null {
  if (value === null) return null;
  if (
    typeof value !== "string"
    || !ISO_TIMESTAMP.test(value)
    || !Number.isFinite(Date.parse(value))
  ) return invalid("正式库存投影时间无效");
  return value;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) return invalid(`正式库存响应中的 ${name} 无效`);
  return value.toLowerCase();
}

function text(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) return invalid(`正式库存响应中的 ${name} 无效`);
  return value;
}

function nonnegativeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) return invalid(`正式库存响应中的 ${name} 无效`);
  return value as number;
}

function nullablePair(
  object: Record<string, unknown>,
  idName: string,
  labelName: string,
): void {
  const id = field(object, idName);
  const label = field(object, labelName);
  if (id === null && label === null) return;
  uuid(id, idName);
  text(label, labelName);
}

function validateProjection(object: Record<string, unknown>) {
  if (field(object, "schema_version") !== "1.0") invalid("正式库存响应版本不受支持");
  const projectionStatus = exactEnum(
    field(object, "projection_status"),
    ["not_initialized", "ready"] as const,
    "projection_status",
  );
  const openingStatus = exactEnum(
    field(object, "opening_balance_status"),
    ["not_established", "established"] as const,
    "opening_balance_status",
  );
  const cursor = nonnegativeInteger(field(object, "ledger_cursor"), "ledger_cursor");
  const projectedAt = timestamp(field(object, "projected_at"));
  if ((cursor === 0) !== (projectedAt === null)) invalid("正式库存账本游标与投影时间不一致");
  if (projectionStatus === "not_initialized" && cursor !== 0) invalid("未初始化投影不能包含库存流水");
  if (openingStatus === "established" && projectionStatus !== "ready") invalid("期初已建立但正式账本证据不完整");
  return { openingStatus, cursor };
}

export function validateInventorySummary(value: unknown): InventorySummary {
  const object = record(value);
  if (field(object, "schema_version") !== "1.0") {
    return invalid("正式库存响应版本不受支持");
  }
  const projectionStatus = exactEnum(
    field(object, "projection_status"),
    ["not_initialized", "ready"] as const,
    "projection_status",
  );
  const openingStatus = exactEnum(
    field(object, "opening_balance_status"),
    ["not_established", "established"] as const,
    "opening_balance_status",
  );
  const cursor = field(object, "ledger_cursor");
  if (!Number.isSafeInteger(cursor) || (cursor as number) < 0) {
    return invalid("正式库存账本游标无效");
  }
  const projectedAt = timestamp(field(object, "projected_at"));
  if (((cursor as number) === 0) !== (projectedAt === null)) {
    return invalid("正式库存账本游标与投影时间不一致");
  }
  if (projectionStatus === "not_initialized" && cursor !== 0) {
    return invalid("未初始化投影不能包含库存流水");
  }
  if (openingStatus === "established" && projectionStatus !== "ready") {
    return invalid("期初已建立但正式账本证据不完整");
  }

  const scopes = field(object, "scopes");
  if (!Array.isArray(scopes) || scopes.length === 0) {
    return invalid("正式库存响应缺少数据范围");
  }
  const seenScopes = new Set<string>();
  for (const source of scopes) {
    const scope = record(source);
    const scopeType = exactEnum(
      field(scope, "scope_type"),
      ["national", "organization", "person"] as const,
      "scope_type",
    );
    const scopeId = field(scope, "scope_id");
    if (typeof scopeId !== "string" || !scopeId) return invalid("库存范围标识无效");
    if (scopeType === "national" ? scopeId !== "*" : !UUID.test(scopeId)) {
      return invalid("库存范围与范围类型不一致");
    }
    const scopeKey = `${scopeType}:${scopeId.toLowerCase()}`;
    if (seenScopes.has(scopeKey)) return invalid("正式库存响应包含重复数据范围");
    seenScopes.add(scopeKey);
  }

  const quantityStatus = exactEnum(
    field(object, "quantity_status"),
    ["opening_not_established", "material_filter_required"] as const,
    "quantity_status",
  );
  const quantities = QUANTITY_FIELDS.map((name) => field(object, name));
  if (openingStatus === "not_established") {
    if (quantityStatus !== "opening_not_established" || quantities.some((item) => item !== null)) {
      return invalid("期初未建立时不得返回库存数量");
    }
  } else if (quantityStatus === "material_filter_required") {
    if (quantities.some((item) => item !== null)) {
      return invalid("未限定物料时不得汇总不同 SKU 数量");
    }
  } else {
    return invalid("库存数量状态与期初状态不一致");
  }
  if (
    field(object, "expected_supply_status") !== "not_available"
    || field(object, "expected_supply_qty") !== null
  ) return invalid("预计供应状态尚不可用");

  return object as InventorySummary;
}

export function validateInventoryAccountPage(value: unknown): InventoryAccountPage {
  const object = record(value);
  const projection = validateProjection(object);
  const items = field(object, "items");
  if (!Array.isArray(items)) invalid("正式库存账户明细不是数组");
  const nextAfterId = field(object, "next_after_id");
  if (nextAfterId !== null) uuid(nextAfterId, "next_after_id");

  const accountIds = new Set<string>();
  for (const source of items as unknown[]) {
    const row = record(source);
    const accountId = uuid(field(row, "stock_account_id"), "stock_account_id");
    if (accountIds.has(accountId)) invalid("正式库存账户页包含重复账户");
    accountIds.add(accountId);
    for (const name of [
      "owner_org_id", "location_owner_org_id", "location_id", "material_id",
    ]) uuid(field(row, name), name);
    for (const name of [
      "owner_org_code", "owner_org_name", "location_owner_org_code",
      "location_owner_org_name", "location_code", "location_name", "sku_code",
      "material_name", "base_unit",
    ]) text(field(row, name), name);
    exactEnum(field(row, "location_type"), LOCATION_TYPES, "location_type");
    const parentId = field(row, "location_parent_id");
    if (parentId !== null) uuid(parentId, "location_parent_id");
    nullablePair(row, "custodian_person_id", "custodian_person_name");
    const trackingMode = exactEnum(field(row, "tracking_mode"), TRACKING_MODES, "tracking_mode");
    exactEnum(field(row, "condition_code"), CONDITIONS, "condition_code");
    exactEnum(field(row, "availability_bucket"), AVAILABILITY_BUCKETS, "availability_bucket");
    nullablePair(row, "lot_id", "lot_no");
    const hasLot = field(row, "lot_id") !== null;
    if (["lot", "lot_and_serial"].includes(trackingMode) !== hasLot) invalid("批次字段与追踪策略不一致");
    const balanceVersion = nonnegativeInteger(field(row, "balance_version"), "balance_version");
    const ledgerCursor = nonnegativeInteger(field(row, "ledger_cursor"), "ledger_cursor");
    if (ledgerCursor > projection.cursor) invalid("账户游标超过响应账本游标");
    if (ledgerCursor === 0 && balanceVersion !== 0) invalid("空账户游标不能带有余额版本");
    const quantityStatus = exactEnum(
      field(row, "quantity_status"),
      ["opening_not_established", "available"] as const,
      "quantity_status",
    );
    const quantity = field(row, "quantity");
    if (projection.openingStatus === "not_established") {
      if (quantityStatus !== "opening_not_established" || quantity !== null) invalid("期初未建立时账户数量必须隐藏");
    } else if (
      quantityStatus !== "available"
      || typeof quantity !== "string"
      || !DECIMAL_QUANTITY.test(quantity)
    ) invalid("账户数量状态与期初状态不一致");
  }
  return object as InventoryAccountPage;
}
