import { apiNoReplay, ApiError, jsonBody } from "./api";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const OPERATION_TYPES = new Set(["consume", "release", "occupy", "recover"]);
const OPERATION_STATUSES = new Set(["posted", "replayed"]);

export type WorkOrderMaterialOperation = "consume" | "release" | "occupy" | "recover";
export type SerialVerification = Readonly<{ serial_id: string; sku_code: string; serial_no: string; qr_code: string }>;
export type WorkOrderMaterialLine = Readonly<{
  material_id: string;
  stock_account_id?: string;
  target_stock_account_id?: string;
  quantity: string;
  serial_ids: readonly string[];
  condition_before: "new" | "used" | "damaged" | "scrapped";
  serial_verifications: readonly SerialVerification[];
}>;
export type WorkOrderMaterialOperationInput = Readonly<{
  operator_person_id: string;
  lines: readonly WorkOrderMaterialLine[];
  idempotency_key: string;
  request_id: string;
}>;
export type WorkOrderMaterialOperationResult = Readonly<{
  schema_version: "1.0";
  operation_id: string;
  operation_no: string;
  work_order_id: string;
  posting_transaction_id: string;
  operation_type: string;
  status: string;
}>;
export type WorkOrderMaterialOperationHistory = Readonly<{ items: readonly WorkOrderMaterialOperationResult[] }>;

function fail(message: string): never { throw new ApiError(409, message); }
function id(value: unknown, field: string): string { if (typeof value !== "string" || !UUID.test(value)) return fail(`${field}无效`); return value; }
function text(value: unknown, field: string, max: number): string { if (typeof value !== "string" || !value || value.trim() !== value || value.length > max || /[\x00-\x1f\x7f]/.test(value)) return fail(`${field}无效`); return value; }
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> { if (!value || typeof value !== "object" || Array.isArray(value)) return fail("工单物料输入不是对象"); const object = value as Record<string, unknown>; if (Object.keys(object).sort().join("\0") !== [...keys].sort().join("\0")) return fail("工单物料输入字段不完整"); return object; }

export function validateWorkOrderMaterialOperationInput(value: unknown, operation: WorkOrderMaterialOperation): WorkOrderMaterialOperationInput {
  const keys = ["operator_person_id", "lines", "idempotency_key", "request_id"] as const;
  const object = exact(value, keys);
  if (!Array.isArray(object.lines) || object.lines.length < 1 || object.lines.length > 100) return fail("工单物料明细无效");
  const lines = object.lines.map((raw) => {
    const line = raw as Record<string, unknown>;
    const expected = operation === "consume" ? ["condition_before", "material_id", "quantity", "serial_ids", "serial_verifications", "stock_account_id"] : operation === "recover" ? ["condition_before", "material_id", "quantity", "serial_ids", "serial_verifications", "target_stock_account_id"] : ["condition_before", "material_id", "quantity", "serial_ids", "serial_verifications", "stock_account_id", "target_stock_account_id"];
    const checked = exact(line, expected);
    const condition = checked.condition_before;
    const allowed = operation === "recover" ? ["used", "damaged"] : ["new", "used", "damaged", "scrapped"];
    if (typeof condition !== "string" || !allowed.includes(condition)) return fail("物料状态不适用于当前操作");
    if (typeof checked.quantity !== "string" || !DECIMAL.test(checked.quantity) || Number(checked.quantity) <= 0) return fail("物料数量无效");
    if (!Array.isArray(checked.serial_ids) || checked.serial_ids.length > 1000 || new Set(checked.serial_ids).size !== checked.serial_ids.length) return fail("物料串码无效");
    const serialIds = checked.serial_ids.map((serial) => id(serial, "serial_id"));
    const verifications = checked.serial_verifications;
    if (!Array.isArray(verifications) || verifications.length > 1000) return fail("串码核验无效");
    const serialVerifications = verifications.map((rawVerification) => { const verification = exact(rawVerification, ["qr_code", "serial_id", "serial_no", "sku_code"]); return { serial_id: id(verification.serial_id, "serial_id"), sku_code: text(verification.sku_code, "sku_code", 80), serial_no: text(verification.serial_no, "serial_no", 200), qr_code: text(verification.qr_code, "qr_code", 250) }; });
    return { material_id: id(checked.material_id, "material_id"), ...(checked.stock_account_id === undefined ? {} : { stock_account_id: id(checked.stock_account_id, "stock_account_id") }), ...(checked.target_stock_account_id === undefined ? {} : { target_stock_account_id: id(checked.target_stock_account_id, "target_stock_account_id") }), quantity: checked.quantity, condition_before: condition as WorkOrderMaterialLine["condition_before"], serial_ids: Object.freeze(serialIds), serial_verifications: Object.freeze(serialVerifications) };
  });
  return { operator_person_id: id(object.operator_person_id, "operator_person_id"), lines: Object.freeze(lines), idempotency_key: text(object.idempotency_key, "idempotency_key", 200), request_id: (() => { const value = text(object.request_id, "request_id", 160); if (!COORDINATE.test(value)) return fail("request_id无效"); return value; })() };
}

export function validateWorkOrderMaterialOperationResult(value: unknown): WorkOrderMaterialOperationResult {
  const object = exact(value, ["operation_id", "operation_no", "work_order_id", "posting_transaction_id", "operation_type", "status", "schema_version"]);
  if (object.schema_version !== "1.0") return fail("工单物料响应版本无效");
  const operationType = text(object.operation_type, "operation_type", 32);
  const status = text(object.status, "status", 32);
  if (!OPERATION_TYPES.has(operationType) || !OPERATION_STATUSES.has(status)) return fail("工单物料响应状态无效");
  return { schema_version: "1.0", operation_id: id(object.operation_id, "operation_id"), operation_no: text(object.operation_no, "operation_no", 80), work_order_id: id(object.work_order_id, "work_order_id"), posting_transaction_id: id(object.posting_transaction_id, "posting_transaction_id"), operation_type: operationType, status };
}

export function validateWorkOrderMaterialOperationHistory(value: unknown): WorkOrderMaterialOperationHistory {
  const object = exact(value, ["items"]);
  if (!Array.isArray(object.items)) return fail("工单物料历史响应无效");
  return { items: Object.freeze(object.items.map(validateWorkOrderMaterialOperationResult)) };
}

export function listWorkOrderMaterialOperations(workOrderId: string): Promise<WorkOrderMaterialOperationHistory> {
  return apiNoReplay<unknown>(`/v1/work-orders/${id(workOrderId, "work_order_id")}/material-operations`, {
    method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
  }).then(validateWorkOrderMaterialOperationHistory);
}

export function executeWorkOrderMaterialOperation(workOrderId: string, operation: WorkOrderMaterialOperation, input: WorkOrderMaterialOperationInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<WorkOrderMaterialOperationResult> {
  const checkedWorkOrderId = id(workOrderId, "work_order_id");
  const body = validateWorkOrderMaterialOperationInput(input, operation);
  const requestId = text(headers["X-Request-ID"], "X-Request-ID", 160);
  const idempotencyKey = text(headers["Idempotency-Key"], "Idempotency-Key", 128);
  if (!COORDINATE.test(requestId) || idempotencyKey.length < 16) return Promise.reject(new ApiError(409, "写坐标无效"));
  return apiNoReplay<unknown>(`/v1/work-orders/${checkedWorkOrderId}/material-operations/${operation}`, { method: "POST", headers: { "X-Request-ID": requestId, "Idempotency-Key": idempotencyKey }, ...jsonBody(body) }).then(validateWorkOrderMaterialOperationResult);
}
