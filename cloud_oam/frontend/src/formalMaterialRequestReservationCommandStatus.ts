import {
  MaterialRequestContractError,
  validateMaterialRequestStateAxes,
  type MaterialRequestStateAxes,
} from "./formalMaterialRequests";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;

/** The durable response of one reservation fact.  The fact status and the
 * request reservation axis are intentionally separate: a partial reservation
 * still returns `reservation_status=reserved` while the aggregate axis may be
 * `pending`.
 */
export type MaterialRequestReservationCommand = Readonly<{
  request_id: string;
  reservation_id: string;
  reservation_no: string;
  request_version: number;
  current_request_version: number | null;
  revision_id: string;
  revision_no: number;
  request_line_id: string;
  allocation_id: string;
  source_stock_account_id: string;
  source_balance_version: number;
  source_ledger_cursor: number;
  serial_ids: readonly string[];
  stock_account_id: string;
  reserve_transaction_id: string;
  reserve_transaction_no: string;
  reserved_qty: string;
  reservation_status: "reserved";
  request_status: MaterialRequestStateAxes["request_status"];
  state_axes: MaterialRequestStateAxes;
  idempotency_replayed: true;
}>;

export type MaterialRequestReservationMutationResult =
  Omit<MaterialRequestReservationCommand, "idempotency_replayed"> & Readonly<{
    idempotency_replayed: boolean;
  }>;

export type MaterialRequestReservationCommandStatus = Readonly<{
  schema_version: "1.0";
  lookup_status: "confirmed" | "not_observed";
  command: MaterialRequestReservationCommand | null;
}>;

function fail(message: string): never {
  throw new MaterialRequestContractError("material_request_reservation_contract_invalid", message);
}

function exact(value: unknown, keys: readonly string[], name = "预留命令状态"): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(`${name}必须是对象`);
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return fail(`${name}字段不完整或包含未知字段`);
  }
  return object;
}

function id(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) return fail(`${name}无效`);
  return value.toLowerCase();
}

function integer(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) return fail(`${name}无效`);
  return value as number;
}

function nullableInteger(value: unknown, name: string): number | null {
  return value === null ? null : integer(value, name);
}

function text(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max
      || value.trim() !== value || /[\u0000-\u001f\u007f]/.test(value)) return fail(`${name}无效`);
  return value;
}

function normalizeCommand(value: unknown): MaterialRequestReservationCommand {
  const command = exact(value, [
    "request_id", "reservation_id", "reservation_no", "request_version", "current_request_version",
    "revision_id", "revision_no", "request_line_id", "allocation_id", "source_stock_account_id",
    "stock_account_id", "source_balance_version", "source_ledger_cursor", "serial_ids", "reserve_transaction_id", "reserve_transaction_no", "reserved_qty",
    "reservation_status", "request_status", "state_axes", "idempotency_replayed",
  ], "预留命令");
  const states = validateMaterialRequestStateAxes(command.state_axes);
  if (command.reservation_status !== "reserved" || command.idempotency_replayed !== true) {
    return fail("预留命令状态轴无效");
  }
  if (states.request_status !== command.request_status) return fail("预留命令申请状态与状态轴不一致");
  if (!["pending", "reserved", "partially_released"].includes(states.reservation_status)) {
    return fail("预留命令占用状态轴无效");
  }
  if (typeof command.reserved_qty !== "string" || !QUANTITY.test(command.reserved_qty) || command.reserved_qty === "0.000") {
    return fail("预留数量无效");
  }
  if (!Array.isArray(command.serial_ids) || command.serial_ids.length > 1000) return fail("预留 SN 明细无效");
  const serialIds = command.serial_ids.map((serialId) => id(serialId, "serial_id"));
  if (new Set(serialIds).size !== serialIds.length || (serialIds.length > 0 && command.reserved_qty !== `${serialIds.length}.000`)) return fail("预留 SN 数量不匹配");
  if (id(command.source_stock_account_id, "source_stock_account_id") === id(command.stock_account_id, "stock_account_id")) return fail("预留来源与占用账户不能相同");
  return {
    source_balance_version: integer(command.source_balance_version, "source_balance_version"),
    source_ledger_cursor: integer(command.source_ledger_cursor, "source_ledger_cursor"),
    serial_ids: Object.freeze(serialIds),
    request_id: id(command.request_id, "request_id"),
    reservation_id: id(command.reservation_id, "reservation_id"),
    reservation_no: text(command.reservation_no, "reservation_no", 100),
    request_version: integer(command.request_version, "request_version"),
    current_request_version: nullableInteger(command.current_request_version, "current_request_version"),
    revision_id: id(command.revision_id, "revision_id"),
    revision_no: integer(command.revision_no, "revision_no"),
    request_line_id: id(command.request_line_id, "request_line_id"),
    allocation_id: id(command.allocation_id, "allocation_id"),
    source_stock_account_id: id(command.source_stock_account_id, "source_stock_account_id"),
    stock_account_id: id(command.stock_account_id, "stock_account_id"),
    reserve_transaction_id: id(command.reserve_transaction_id, "reserve_transaction_id"),
    reserve_transaction_no: text(command.reserve_transaction_no, "reserve_transaction_no", 100),
    reserved_qty: command.reserved_qty,
    reservation_status: "reserved",
    request_status: command.request_status as MaterialRequestStateAxes["request_status"],
    state_axes: states,
    idempotency_replayed: true,
  };
}

export function validateMaterialRequestReservationCommandStatus(
  value: unknown,
): MaterialRequestReservationCommandStatus {
  const row = exact(value, ["schema_version", "lookup_status", "command"]);
  if (row.schema_version !== "1.0") return fail("预留命令状态版本无效");
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) return fail("未观察到预留命令时不能携带事实");
    return { schema_version: "1.0", lookup_status: "not_observed", command: null };
  }
  if (row.lookup_status !== "confirmed" || row.command === null) return fail("预留命令状态无效");
  return { schema_version: "1.0", lookup_status: "confirmed", command: normalizeCommand(row.command) };
}

export function validateMaterialRequestReservationMutationResult(
  value: unknown,
): MaterialRequestReservationMutationResult {
  const raw = exact(value, [
    "request_id", "reservation_id", "reservation_no", "request_version", "current_request_version",
    "revision_id", "revision_no", "request_line_id", "allocation_id", "source_stock_account_id",
    "stock_account_id", "source_balance_version", "source_ledger_cursor", "serial_ids", "reserve_transaction_id", "reserve_transaction_no", "reserved_qty",
    "reservation_status", "request_status", "state_axes", "idempotency_replayed",
  ], "预留写响应");
  if (typeof raw.idempotency_replayed !== "boolean") return fail("预留写响应幂等标记无效");
  const normalized = validateMaterialRequestReservationCommandStatus({
    schema_version: "1.0",
    lookup_status: "confirmed",
    command: { ...raw, idempotency_replayed: true },
  });
  return { ...normalized.command!, idempotency_replayed: raw.idempotency_replayed };
}
