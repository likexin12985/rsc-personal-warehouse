import {
  MATERIAL_REQUEST_SUPPLY_TASK_STATUSES,
  MATERIAL_REQUEST_SUPPLY_TYPES,
  MaterialRequestContractError,
  type MaterialRequestDetail,
  type MaterialRequestMutationResult,
  type MaterialRequestSupplyTask,
  validateMaterialRequestMutationResult,
} from "./formalMaterialRequests";

export const SUPPLY_ACTIONS = ["create_supply_task", "update_supply_task", "cancel_supply_task"] as const;
export type SupplyAction = typeof SUPPLY_ACTIONS[number];
export type SupplyCreateInput = Readonly<{
  expected_request_version: number;
  request_line_id: string;
  supply_type: typeof MATERIAL_REQUEST_SUPPLY_TYPES[number];
  reference_no: string | null;
  expected_qty: string;
  expected_date: string | null;
  note: string;
}>;
export type SupplyUpdateInput = Readonly<{
  expected_request_version: number;
  expected_task_version: number;
  status: typeof MATERIAL_REQUEST_SUPPLY_TASK_STATUSES[number];
  reference_no: string | null;
  expected_date: string | null;
  comment: string;
}>;
export type SupplyMutationResult = MaterialRequestMutationResult & Readonly<{
  action: SupplyAction;
  supply_task_id: string;
  task_no: string;
  task_status: typeof MATERIAL_REQUEST_SUPPLY_TASK_STATUSES[number];
  task_version: number;
}>;
export type SupplyCommand = Omit<SupplyMutationResult, "schema_version" | "idempotency_replayed"> & Readonly<{
  occurred_at: string;
}>;
export type SupplyCommandStatus = Readonly<{
  schema_version: "1.0";
  lookup_status: "confirmed" | "not_observed";
  command: SupplyCommand | null;
}>;

const DEFINITIVE_SUPPLY_POST_REJECTIONS = Object.freeze({
  404: Object.freeze({
    category: "not_found",
    codes: new Set(["supply_task_not_found"]),
  }),
  409: Object.freeze({
    category: "conflict",
    codes: new Set([
      "material_request_version_conflict",
      "material_request_supply_line_not_current",
      "material_request_supply_quantity_exceeds_approved",
      "supply_task_not_current_revision",
      "supply_task_version_conflict",
      "supply_task_terminal",
      "supply_task_status_transition_invalid",
      "supply_task_cancel_metadata_changed",
    ]),
  }),
  412: Object.freeze({
    category: "precondition_failed",
    codes: new Set([
      "material_request_supply_line_not_approved",
      "material_request_supply_active_substitution_exists",
    ]),
  }),
} as const);

/**
 * Only errors reachable after the service has found no persisted replay and
 * before it writes a command may release the current POST's durable sentinel.
 */
export function isDefinitiveSupplyPostRejection(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const value = error as Record<string, unknown>;
  if (value.responseReceived !== true || !Number.isInteger(value.status)) return false;
  const rule = DEFINITIVE_SUPPLY_POST_REJECTIONS[
    value.status as keyof typeof DEFINITIVE_SUPPLY_POST_REJECTIONS
  ] as Readonly<{ category: string; codes: ReadonlySet<string> }> | undefined;
  return Boolean(
    rule
    && value.category === rule.category
    && typeof value.code === "string"
    && rule.codes.has(value.code)
  );
}

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;
const REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$/;
const COMMON_RESULT_KEYS = [
  "schema_version", "request_id", "action", "request_version", "revision_id", "revision_no",
  "approval_instance_id", "approval_attempt_no", "current_step_id", "states", "idempotency_replayed",
] as const;
const TASK_RESULT_KEYS = ["supply_task_id", "task_no", "task_status", "task_version"] as const;

function fail(message: string): never {
  throw new MaterialRequestContractError("material_request_supply_contract_invalid", message);
}
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail("供给内容必须是对象");
  const object = value as Record<string, unknown>;
  if (Object.keys(object).sort().join(",") !== [...keys].sort().join(",")) {
    return fail("供给内容必须精确包含正式字段");
  }
  return object;
}
function integer(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) return fail(`${name}必须是非负整数`);
  return value as number;
}
function id(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) return fail(`${name}必须是规范对象标识`);
  return value;
}
function bounded(value: unknown, name: string, maximum: number, minimum = 0): string {
  if (typeof value !== "string" || value !== value.trim() || value.length < minimum
      || value.length > maximum || /[\u0000-\u001f\u007f]/.test(value)) return fail(`${name}无效`);
  return value;
}
function reference(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !REFERENCE.test(value)) return fail("供给参考号无效或超过160字符");
  return value;
}
function date(value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return fail("预计日期无效");
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) return fail("预计日期无效");
  return value;
}

export function validateSupplyCreateInput(value: unknown): SupplyCreateInput {
  const object = exact(value, [
    "expected_request_version", "request_line_id", "supply_type", "reference_no", "expected_qty", "expected_date", "note",
  ]);
  if (!MATERIAL_REQUEST_SUPPLY_TYPES.includes(object.supply_type as SupplyCreateInput["supply_type"])) return fail("供给类型无效");
  if (typeof object.expected_qty !== "string" || !QUANTITY.test(object.expected_qty)
      || object.expected_qty === "0.000") return fail("计划数量必须是大于零的三位小数精确字符串");
  return {
    expected_request_version: integer(object.expected_request_version, "需求版本"),
    request_line_id: id(object.request_line_id, "需求明细"),
    supply_type: object.supply_type as SupplyCreateInput["supply_type"],
    reference_no: reference(object.reference_no),
    expected_qty: object.expected_qty,
    expected_date: date(object.expected_date),
    note: bounded(object.note, "计划备注", 4000),
  };
}

export function validateSupplyUpdateInput(value: unknown, action: SupplyAction): SupplyUpdateInput {
  const object = exact(value, [
    "expected_request_version", "expected_task_version", "status", "reference_no", "expected_date", "comment",
  ]);
  if (action !== "update_supply_task" && action !== "cancel_supply_task") return fail("供给更新动作无效");
  if (!MATERIAL_REQUEST_SUPPLY_TASK_STATUSES.includes(object.status as SupplyUpdateInput["status"])) return fail("供给计划状态无效");
  if ((action === "cancel_supply_task") !== (object.status === "cancelled")) return fail("取消动作与计划状态不一致");
  const referenceNo = reference(object.reference_no);
  if (object.status === "reference_registered" && referenceNo === null) return fail("登记参考状态必须提供参考号");
  return {
    expected_request_version: integer(object.expected_request_version, "需求版本"),
    expected_task_version: integer(object.expected_task_version, "供给任务版本"),
    status: object.status as SupplyUpdateInput["status"],
    reference_no: referenceNo,
    expected_date: date(object.expected_date),
    comment: bounded(object.comment, "处理原因", 4000, ["cancelled", "closed_no_supply"].includes(String(object.status)) ? 1 : 0),
  };
}

export function validateSupplyMutationResult(
  value: unknown,
  expected: Readonly<{
    requestId: string; action: SupplyAction; previousVersion: number;
    taskId?: string; previousTaskVersion?: number;
  }>,
): SupplyMutationResult {
  const object = exact(value, [...COMMON_RESULT_KEYS, ...TASK_RESULT_KEYS]);
  const common = Object.fromEntries(COMMON_RESULT_KEYS.map((key) => [key, object[key]]));
  const base = validateMaterialRequestMutationResult(common, expected);
  if (!SUPPLY_ACTIONS.includes(base.action as SupplyAction)) return fail("供给响应动作无效");
  const taskId = id(object.supply_task_id, "供给任务");
  const taskVersion = integer(object.task_version, "供给任务版本");
  const taskStatus = object.task_status as SupplyMutationResult["task_status"];
  if (!MATERIAL_REQUEST_SUPPLY_TASK_STATUSES.includes(taskStatus)) return fail("供给响应不得包含履约状态");
  if (!base.approval_instance_id || base.current_step_id !== null
      || !["approved", "partially_approved"].includes(base.states.request_status)) return fail("供给响应缺少最终审批锚点");
  for (const [axis, initial] of Object.entries({
    allocation_status: "not_allocated", reservation_status: "not_reserved", outbound_status: "not_started",
    shipment_status: "not_started", logistics_signature_status: "not_signed", oam_receipt_status: "not_occurred",
    personal_inbound_status: "not_started", notification_status: "not_started", reconciliation_status: "not_started",
  })) {
    if (base.states[axis as keyof typeof base.states] !== initial) return fail("供给计划响应不得推进独立履约状态");
  }
  if ((base.action === "cancel_supply_task") !== (taskStatus === "cancelled")) return fail("供给取消响应不一致");
  if (base.action === "create_supply_task") {
    if (taskVersion !== 0 || !["open", "reference_registered"].includes(taskStatus)) return fail("新供给任务状态或版本无效");
  } else if (expected.taskId !== undefined || expected.previousTaskVersion !== undefined) {
    if (taskId !== id(expected.taskId, "期望供给任务")
        || taskVersion !== integer(expected.previousTaskVersion, "原供给版本") + 1) return fail("供给任务响应锚点或版本不一致");
  } else if (taskVersion === 0) return fail("供给任务更新版本必须递增");
  return {
    ...base,
    action: base.action as SupplyAction,
    supply_task_id: taskId,
    task_no: bounded(object.task_no, "供给任务编号", 100, 1),
    task_status: taskStatus,
    task_version: taskVersion,
  };
}

export function validateSupplyCommandStatus(value: unknown): SupplyCommandStatus {
  const object = exact(value, ["schema_version", "lookup_status", "command"]);
  if (object.schema_version !== "1.0") return fail("供给命令状态版本不受支持");
  if (object.lookup_status === "not_observed") {
    if (object.command !== null) return fail("尚未观察到的命令不能包含确认事实");
    return { schema_version: "1.0", lookup_status: "not_observed", command: null };
  }
  if (object.lookup_status !== "confirmed") return fail("供给命令状态无效");
  const command = exact(object.command, [
    ...COMMON_RESULT_KEYS.filter((key) => key !== "schema_version" && key !== "idempotency_replayed"),
    ...TASK_RESULT_KEYS, "occurred_at",
  ]);
  const occurredAt = command.occurred_at;
  if (typeof occurredAt !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(occurredAt)
      || !Number.isFinite(Date.parse(occurredAt))) return fail("供给命令时间无效");
  const { occurred_at: _occurredAt, ...mutation } = command;
  const checked = validateSupplyMutationResult({ ...mutation, schema_version: "1.0", idempotency_replayed: true }, {
    requestId: id(command.request_id, "需求对象"),
    action: command.action as SupplyAction,
    previousVersion: integer(command.request_version, "需求版本") - 1,
  });
  const { schema_version: _schema, idempotency_replayed: _replayed, ...facts } = checked;
  return { schema_version: "1.0", lookup_status: "confirmed", command: { ...facts, occurred_at: occurredAt } };
}

export function supplyMutationMatchesDetail(
  result: SupplyMutationResult,
  before: MaterialRequestDetail,
  after: MaterialRequestDetail,
  body: SupplyCreateInput | SupplyUpdateInput,
): boolean {
  const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
  const task = after.supply_tasks.find((row) => row.id === result.supply_task_id);
  const previous = before.supply_tasks.find((row) => row.id === result.supply_task_id);
  const invariant = (detail: MaterialRequestDetail) => {
    const { request_version: _version, updated_at: _time, allowed_actions: _actions, supply_tasks: _tasks, ...facts } = detail;
    return facts;
  };
  if (!task || result.request_id !== before.request_id || after.request_id !== before.request_id
      || !same(invariant(before), invariant(after))
      || after.request_version !== result.request_version || before.request_version + 1 !== result.request_version
      || result.revision_id !== before.current_revision_id || result.revision_id !== after.current_revision_id
      || result.revision_no !== before.current_revision_no || result.revision_no !== after.current_revision_no
      || result.approval_instance_id !== before.approval_instance?.instance_id
      || result.approval_attempt_no !== before.approval_instance?.attempt_no
      || !same(before.approval_instance, after.approval_instance)
      || !same(before.approval_history, after.approval_history)
      || !same(before.lines, after.lines) || !same(before.states, after.states) || !same(result.states, after.states)
      || task.task_no !== result.task_no || task.status !== result.task_status || task.version !== result.task_version
      || task.reference_no !== body.reference_no || task.expected_date !== body.expected_date
      || !same(before.supply_tasks.filter((row) => row.id !== result.supply_task_id).map(withoutActions),
        after.supply_tasks.filter((row) => row.id !== result.supply_task_id).map(withoutActions))) return false;
  if ("request_line_id" in body) {
    return !previous && task.request_line_id === body.request_line_id && task.supply_type === body.supply_type
      && task.expected_qty === body.expected_qty && task.original_equivalent_qty === body.expected_qty
      && task.substitution_decision_id === null && task.status === (body.reference_no ? "reference_registered" : "open");
  }
  return Boolean(previous && task.request_line_id === previous.request_line_id
    && task.supply_type === previous.supply_type && task.expected_qty === previous.expected_qty
    && task.original_equivalent_qty === previous.original_equivalent_qty
    && task.substitution_decision_id === previous.substitution_decision_id
    && task.created_at === previous.created_at && Date.parse(task.updated_at) >= Date.parse(previous.updated_at)
    && task.status === body.status);
}

function withoutActions(task: MaterialRequestSupplyTask) {
  const { allowed_actions: _actions, ...facts } = task;
  return facts;
}
