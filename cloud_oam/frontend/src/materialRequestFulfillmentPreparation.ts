import { type MaterialRequestDetail, type MaterialRequestStateAxes, validateMaterialRequestStateAxes } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";

export const PREPARATION_BLOCKERS = {
  source_inactive: "来源位置或物料已停用",
  pool_shortfall: "占用账户余额不足以覆盖现有占用，请核对库存账",
  serial_mismatch: "原占用 SN 的位置或状态不一致，请核对实物",
} as const;
export type PreparationBlocker = keyof typeof PREPARATION_BLOCKERS;
export type PreparationItem = Readonly<{
  reservation_id: string; reservation_no: string; allocation_id: string; source_stock_account_id: string;
  location_name: string; sku_code: string; material_name: string;
  reserved_qty: string; released_qty: string; remaining_reserved_qty: string; verified_held_qty: string;
  tracking_mode: "none" | "lot" | "serial" | "lot_and_serial"; quantity_scale: number; allow_fraction: boolean;
  source_balance_version: number; source_ledger_cursor: number;
  preparation_status: "ready_for_review" | "released" | "blocked";
  blockers: readonly PreparationBlocker[]; serials: readonly Readonly<{ serial_id: string; serial_no: string }>[];
}>;
export type FulfillmentPreparation = Readonly<{
  schema_version: "1.0"; scope: "authorized_sources"; request_id: string; request_line_id: string; material_id: string;
  request_version: number; revision_id: string; revision_no: number; state_axes: MaterialRequestStateAxes;
  ledger_cursor: number; projected_at: string; items: readonly PreparationItem[];
}>;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function fail(message: string): never { throw new Error(`履约准备：${message}`); }
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || !same(Object.keys(value).sort(), [...keys].sort())) fail("响应字段不完整或包含未知字段");
  return value as Record<string, unknown>;
}
function id(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) fail("对象标识无效"); return value; }
function text(value: unknown, max: number): string {
  if (typeof value !== "string" || !value || value !== value.trim() || value.length > max || /[\x00-\x1f\x7f]/.test(value)) fail("文本内容无效");
  return value;
}
function integer(value: unknown, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) fail("版本或账本坐标无效"); return value as number;
}
function amount(value: unknown): string {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value)) fail("数量须为三位小数"); return value;
}
function units(value: string): bigint { return BigInt(value.replace(".", "")); }

export function validateFulfillmentPreparation(value: unknown): FulfillmentPreparation {
  const page = exact(value, ["schema_version", "scope", "request_id", "request_line_id", "material_id", "request_version", "revision_id", "revision_no", "state_axes", "ledger_cursor", "projected_at", "items"]);
  if (page.schema_version !== "1.0" || page.scope !== "authorized_sources" || !Array.isArray(page.items) || page.items.length > 100) fail("响应版本或范围无效");
  const axes = validateMaterialRequestStateAxes(page.state_axes), cursor = integer(page.ledger_cursor);
  if (!["approved", "partially_approved"].includes(axes.request_status) || axes.outbound_status !== "not_started") fail("需求已离开出库前准备阶段");
  if (typeof page.projected_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(page.projected_at) || !Number.isFinite(Date.parse(page.projected_at))) fail("库存核验时间无效");
  let serialCount = 0;
  const items = page.items.map((raw): PreparationItem => {
    const row = exact(raw, ["reservation_id", "reservation_no", "allocation_id", "source_stock_account_id", "location_name", "sku_code", "material_name", "reserved_qty", "released_qty", "remaining_reserved_qty", "verified_held_qty", "tracking_mode", "quantity_scale", "allow_fraction", "source_balance_version", "source_ledger_cursor", "preparation_status", "blockers", "serials"]);
    if (!["none", "lot", "serial", "lot_and_serial"].includes(String(row.tracking_mode)) || typeof row.allow_fraction !== "boolean" || !Array.isArray(row.serials) || !Array.isArray(row.blockers)) fail("物料策略或核验证据无效");
    serialCount += row.serials.length;
    if (serialCount > 1000 || row.blockers.some(key => typeof key !== "string" || !Object.hasOwn(PREPARATION_BLOCKERS, key)) || new Set(row.blockers).size !== row.blockers.length) fail("SN 范围或阻塞原因无效");
    const serials = row.serials.map(value => { const serial = exact(value, ["serial_id", "serial_no"]); return { serial_id: id(serial.serial_id), serial_no: text(serial.serial_no, 160) }; });
    const amounts = [row.reserved_qty, row.released_qty, row.remaining_reserved_qty, row.verified_held_qty].map(amount);
    const [reserved, released, remaining, held] = amounts.map(units);
    if (reserved <= 0n || released + remaining !== reserved || held > remaining) fail("原占用与释放数量不守恒");
    const scale = integer(row.quantity_scale);
    if (scale > 3 || amounts.map(units).some(q => q % (10n ** BigInt(3 - scale)) !== 0n || (!row.allow_fraction && q % 1000n !== 0n))) fail("数量不符合当前物料精度");
    const usesSerial = ["serial", "lot_and_serial"].includes(String(row.tracking_mode));
    if (usesSerial ? held !== BigInt(serials.length) * 1000n || amounts.map(units).some(q => q % 1000n !== 0n) : serials.length !== 0) fail("SN 与原占用数量不一致");
    const status = row.preparation_status;
    if (status === "ready_for_review") {
      if (remaining <= 0n || held !== remaining || row.blockers.length) fail("可核对状态缺少足量证据");
    } else if (status === "released") {
      if (remaining !== 0n || held !== 0n || row.blockers.length) fail("全部释放状态与数量不一致");
    } else if (status === "blocked") {
      if (remaining <= 0n || held !== 0n || !row.blockers.length) fail("阻塞状态缺少原因");
    } else fail("准备状态未知");
    const sourceCursor = integer(row.source_ledger_cursor);
    if (sourceCursor > cursor) fail("账户版本晚于本次库存快照");
    return {
      reservation_id: id(row.reservation_id), reservation_no: text(row.reservation_no, 100), allocation_id: id(row.allocation_id), source_stock_account_id: id(row.source_stock_account_id),
      location_name: text(row.location_name, 200), sku_code: text(row.sku_code, 100), material_name: text(row.material_name, 240),
      reserved_qty: amounts[0], released_qty: amounts[1], remaining_reserved_qty: amounts[2], verified_held_qty: amounts[3],
      tracking_mode: row.tracking_mode as PreparationItem["tracking_mode"], quantity_scale: scale, allow_fraction: row.allow_fraction,
      source_balance_version: integer(row.source_balance_version), source_ledger_cursor: sourceCursor, preparation_status: status,
      blockers: row.blockers as PreparationBlocker[], serials,
    };
  });
  const serialIds = items.flatMap(row => row.serials.map(s => s.serial_id));
  if (new Set(items.map(row => row.reservation_id)).size !== items.length || new Set(serialIds).size !== serialIds.length) fail("原占用或 SN 重复");
  return { schema_version: "1.0", scope: "authorized_sources", request_id: id(page.request_id), request_line_id: id(page.request_line_id), material_id: id(page.material_id),
    request_version: integer(page.request_version, 1), revision_id: id(page.revision_id), revision_no: integer(page.revision_no, 1), state_axes: axes,
    ledger_cursor: cursor, projected_at: page.projected_at, items };
}

export function preparationMatchesDetail(page: FulfillmentPreparation, detail: MaterialRequestDetail, lineId: string): boolean {
  const line = detail.lines.find(row => row.request_line_id === lineId);
  return Boolean(line && page.request_id === detail.request_id && page.request_line_id === lineId && page.material_id === line.material_id
    && page.request_version === detail.request_version && page.revision_id === detail.current_revision_id && page.revision_no === detail.current_revision_no
    && line.revision_id === page.revision_id && line.revision_no === page.revision_no && same(page.state_axes, detail.states));
}
