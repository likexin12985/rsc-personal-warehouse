import {
  MaterialRequestContractError,
  validateMaterialRequestStateAxes,
  type MaterialRequestStateAxes,
} from "./formalMaterialRequests";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const QUANTITY = /^[1-9]\d{0,14}\.\d{3}$/;

export type MaterialRequestAllocationCommand = Readonly<{
  request_id: string;
  allocation_id: string;
  allocation_no: string;
  request_version: number;
  revision_id: string;
  revision_no: number;
  request_line_id: string;
  source_stock_account_id: string;
  allocated_qty: string;
  allocation_status: "allocated";
  request_status: MaterialRequestStateAxes["request_status"];
  state_axes: MaterialRequestStateAxes;
  idempotency_replayed: true;
}>;

export type MaterialRequestAllocationMutationResult = Omit<MaterialRequestAllocationCommand, "idempotency_replayed"> & Readonly<{
  idempotency_replayed: boolean;
}>;

export type MaterialRequestAllocationCommandStatus = Readonly<{
  schema_version: "1.0";
  lookup_status: "confirmed" | "not_observed";
  command: MaterialRequestAllocationCommand | null;
}>;

function fail(message: string): never {
  throw new MaterialRequestContractError("material_request_allocation_contract_invalid", message);
}

function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail("分配命令状态必须是对象");
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return fail("分配命令状态字段不完整或包含未知字段");
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

function text(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max
      || value.trim() !== value || /[\u0000-\u001f\u007f]/.test(value)) return fail(`${name}无效`);
  return value;
}

export function validateMaterialRequestAllocationCommandStatus(
  value: unknown,
): MaterialRequestAllocationCommandStatus {
  const row = exact(value, ["schema_version", "lookup_status", "command"]);
  if (row.schema_version !== "1.0") return fail("分配命令状态版本无效");
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) return fail("未观察到分配命令时不能携带事实");
    return { schema_version: "1.0", lookup_status: "not_observed", command: null };
  }
  if (row.lookup_status !== "confirmed" || row.command === null) return fail("分配命令状态无效");
  const command = exact(row.command, [
    "request_id", "allocation_id", "allocation_no", "request_version", "revision_id", "revision_no",
    "request_line_id", "source_stock_account_id", "allocated_qty", "allocation_status", "request_status",
    "state_axes", "idempotency_replayed",
  ]);
  const states = validateMaterialRequestStateAxes(command.state_axes);
  if (command.allocation_status !== "allocated" || command.idempotency_replayed !== true) {
    return fail("分配命令状态轴无效");
  }
  if (states.request_status !== command.request_status) return fail("分配命令申请状态与状态轴不一致");
  if (typeof command.allocated_qty !== "string" || !QUANTITY.test(command.allocated_qty)) return fail("分配数量无效");
  return {
    schema_version: "1.0",
    lookup_status: "confirmed",
    command: {
      request_id: id(command.request_id, "request_id"),
      allocation_id: id(command.allocation_id, "allocation_id"),
      allocation_no: text(command.allocation_no, "allocation_no", 100),
      request_version: integer(command.request_version, "request_version"),
      revision_id: id(command.revision_id, "revision_id"),
      revision_no: integer(command.revision_no, "revision_no"),
      request_line_id: id(command.request_line_id, "request_line_id"),
      source_stock_account_id: id(command.source_stock_account_id, "source_stock_account_id"),
      allocated_qty: command.allocated_qty,
      allocation_status: "allocated",
      request_status: command.request_status as MaterialRequestStateAxes["request_status"],
      state_axes: states,
      idempotency_replayed: true,
    },
  };
}

export function validateMaterialRequestAllocationMutationResult(
  value: unknown,
): MaterialRequestAllocationMutationResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail("分配写响应必须是对象");
  const raw = value as Record<string, unknown>;
  if (raw.schema_version !== "1.0" || typeof raw.idempotency_replayed !== "boolean") return fail("分配写响应版本或幂等标记无效");
  const { schema_version: _schemaVersion, ...commandRaw } = raw;
  const normalized = validateMaterialRequestAllocationCommandStatus({
    schema_version: "1.0", lookup_status: "confirmed",
    command: { ...commandRaw, idempotency_replayed: true },
  });
  return { ...normalized.command!, idempotency_replayed: raw.idempotency_replayed };
}
