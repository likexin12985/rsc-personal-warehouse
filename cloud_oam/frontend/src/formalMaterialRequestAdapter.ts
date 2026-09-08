import { api, apiNoReplay, ApiError, jsonBody } from "./api";
import { formalMaterialCatalogQuery } from "./formalMaterialCatalog";
import { validateMaterialRequestWorkOrderOptionQuery } from "./formalMaterialRequestOptions";
import { validateMaterialRequestAllocationOptionPage, type MaterialRequestAllocationOptionPage } from "./formalMaterialRequestAllocationOptions";
import { validateMaterialRequestAllocationCommandStatus, validateMaterialRequestAllocationMutationResult, type MaterialRequestAllocationCommandStatus, type MaterialRequestAllocationMutationResult } from "./formalMaterialRequestAllocationCommandStatus";
import { validateMaterialRequestReservationCommandStatus, validateMaterialRequestReservationMutationResult, type MaterialRequestReservationCommandStatus, type MaterialRequestReservationMutationResult } from "./formalMaterialRequestReservationCommandStatus";
import { type PickInput, type PickPage, type PickResult, validatePickInput, validatePickPage, validatePickResult, validatePickStatus } from "./materialRequestReservationPick";
import { type ShipmentInput, type ShipmentResult, type ShipmentOptions, type InboundOrderInput, type InboundOrderResult, type InboundPostingResult, validateShipmentInput, validateShipmentResult, validateShipmentOptions, validateInboundOrderResult, validateInboundPostingResult } from "./materialRequestShipment";
import { type OutboundInput, type OutboundPage, type OutboundResult, validateOutboundInput, validateOutboundPage, validateOutboundResult, validateOutboundStatus } from "./materialRequestOutbound";
import { type ReleaseInput, type ReleasePage, type ReleaseResult, validateReleaseInput, validateReleasePage, validateReleaseResult, validateReleaseStatus } from "./materialRequestReservationRelease";
import { validateMaterialRequestReservationOptionPage, type MaterialRequestReservationOptionPage } from "./formalMaterialRequestReservationOptions";
import { validateFulfillmentPreparation, type FulfillmentPreparation } from "./materialRequestFulfillmentPreparation";
import { validateSupplyCreateInput, validateSupplyUpdateInput } from "./formalMaterialRequestSupply";
import {
  MATERIAL_REQUEST_SCHEMA_VERSION,
  MATERIAL_REQUEST_MUTATION_ACTIONS,
  type MaterialRequestCreateIntent,
  type MaterialRequestDraftInput,
  type MaterialRequestMutationAction,
  type MaterialRequestMutationIntent,
  validateMaterialRequestDraftInput,
  validateMaterialRequestDetail,
} from "./formalMaterialRequests";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_PATH_SOURCE = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const SAFE_COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const DECIMAL = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$/;
const INTERNAL_ROLES = new Set(["admin", "provincial_manager", "technician"]);
const SUPPORTED_ROLES = new Set([...INTERNAL_ROLES, "star_headquarters_approver"]);

export type FormalMaterialRequestExpectedIdentity = Readonly<{
  person_id: string;
  authorization_version: number;
}>;

export type FormalMaterialRequestFreshIdentity = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  person_id: string;
  authorization_version: number;
}>;

type FormalMaterialRequestRequester = <T>(path: string, init?: RequestInit) => Promise<T>;

export type FormalMaterialRequestAccess = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  person_id: string;
  authorization_version: number;
  can_read: boolean;
  can_create: boolean;
  can_withdraw: boolean;
  can_cancel: boolean;
  can_read_material_catalog: boolean;
  can_read_allocation_options: boolean;
  can_approve_region: boolean;
  can_approve_headquarters: boolean;
  can_register_external: boolean;
  can_verify_external: boolean;
}>;

export type FormalMaterialRequestEditableDraft = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  request_id: string;
  request_version: number;
  draft: MaterialRequestDraftInput;
}>;

export type MaterialRequestAllocationCreateInput = Readonly<{
  expected_request_version: number;
  request_line_id: string;
  source_stock_account_id: string;
  allocated_qty: string;
  source_balance_version: number;
  source_ledger_cursor: number;
  serial_ids: readonly string[];
}>;

export type MaterialRequestReservationCreateInput = Readonly<{
  expected_request_version: number;
  request_line_id: string;
  allocation_id: string;
  reserved_qty: string;
  source_balance_version: number;
  source_ledger_cursor: number;
  serial_ids: readonly string[];
}>;

export interface FormalMaterialRequestAdapter {
  loadIdentity(): Promise<unknown>;
  loadAccess(): Promise<unknown>;
  lifecycleCommandStatus(xRequestId: string): Promise<unknown>;
  supplyCommandStatus(xRequestId: string): Promise<unknown>;
  allocationCommandStatus(xRequestId: string): Promise<MaterialRequestAllocationCommandStatus>;
  allocationCommandStatusNoReplay?(xRequestId: string): Promise<MaterialRequestAllocationCommandStatus>;
  reservationCommandStatus?(xRequestId: string): Promise<MaterialRequestReservationCommandStatus>;
  reservationCommandStatusNoReplay?(xRequestId: string): Promise<MaterialRequestReservationCommandStatus>;
  releaseCommandStatusNoReplay?(xRequestId: string): Promise<ReleaseResult | null>;
  listReleaseOptions?(requestId: string, requestLineId: string): Promise<ReleasePage>;
  listFulfillmentPreparation?(requestId: string, requestLineId: string): Promise<FulfillmentPreparation>;
  createRelease?(requestId: string, input: ReleaseInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<ReleaseResult>;
  pickCommandStatusNoReplay?(xRequestId: string): Promise<PickResult | null>;
  outboundCommandStatusNoReplay?(xRequestId: string): Promise<OutboundResult | null>;
  listOutboundOptions?(requestId: string, requestLineId: string): Promise<OutboundPage>;
  listShipments?(requestId: string): Promise<readonly ShipmentResult[]>;
  listShipmentOptions?(requestId: string): Promise<ShipmentOptions>;
  createShipment?(requestId: string, input: ShipmentInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<ShipmentResult>;
  createInboundOrder?(requestId: string, input: InboundOrderInput, headers: Readonly<{ "X-Request-ID": string }>): Promise<InboundOrderResult>;
  postInboundOrder?(requestId: string, inboundOrderId: string, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<InboundPostingResult>;
  createOutbound?(requestId: string, input: OutboundInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<OutboundResult>;
  listPickOptions?(requestId: string, requestLineId: string): Promise<PickPage>;
  createPick?(requestId: string, input: PickInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>): Promise<PickResult>;
  loadIdentityNoReplay?(): Promise<unknown>;
  loadAccessNoReplay?(): Promise<unknown>;
  detailNoReplay?(requestId: string): Promise<unknown>;
  list(afterId: string | null): Promise<unknown>;
  detail(requestId: string): Promise<unknown>;
  loadDraftForEdit(requestId: string): Promise<unknown>;
  listWorkOrderOptions(query: string, afterId: string | null): Promise<unknown>;
  workOrderOptionDetail(workOrderId: string): Promise<unknown>;
  listMaterials(query: string, afterId: string | null): Promise<unknown>;
  listAllocationOptions(requestId: string, requestLineId: string): Promise<MaterialRequestAllocationOptionPage>;
  createAllocation(
    requestId: string,
    input: MaterialRequestAllocationCreateInput,
    headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>,
  ): Promise<MaterialRequestAllocationMutationResult>;
  listReservationOptions?(requestId: string, requestLineId: string): Promise<MaterialRequestReservationOptionPage>;
  createReservation?(
    requestId: string,
    input: MaterialRequestReservationCreateInput,
    headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>,
  ): Promise<MaterialRequestReservationMutationResult>;
  createDraft(intent: MaterialRequestCreateIntent): Promise<unknown>;
  mutate(intent: MaterialRequestMutationIntent): Promise<unknown>;
}

export function validateFormalMaterialRequestFreshIdentity(
  value: unknown,
): FormalMaterialRequestFreshIdentity {
  const object = exactObject(
    value,
    ["schema_version", "person_id", "authorization_version"],
    "正式需求新鲜登录身份",
  );
  if (object.schema_version !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return adapterError("正式需求新鲜登录身份版本不受支持");
  }
  return Object.freeze({
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: requiredUuid(object.person_id, "person_id"),
    authorization_version: version(object.authorization_version, "authorization_version"),
  });
}

function projectFreshIdentity(
  value: unknown,
  expectedIdentity: FormalMaterialRequestExpectedIdentity,
): FormalMaterialRequestFreshIdentity {
  const object = exactObject(value, [
    "person_id", "name", "employee_no", "organization_code", "organization_name",
    "account_status", "employment_status", "access_mode", "authorization_version", "role_codes",
  ], "正式登录身份");
  const personId = requiredUuid(object.person_id, "person_id");
  const authorizationVersion = version(object.authorization_version, "authorization_version");
  if (
    personId !== requiredUuid(expectedIdentity.person_id, "expected_person_id")
    || authorizationVersion !== version(
      expectedIdentity.authorization_version,
      "expected_authorization_version",
    )
  ) {
    return adapterError("登录身份或授权版本已变化，生命周期命令保持待核验");
  }
  if (
    object.account_status !== "active"
    || object.employment_status !== "active"
    || object.access_mode !== "active"
  ) {
    return adapterError("当前登录身份不是可执行需求生命周期操作的有效在职状态");
  }
  boundedText(object.name, "name", 160, false);
  boundedText(object.employee_no, "employee_no", 80, false);
  boundedText(object.organization_code, "organization_code", 120, false);
  boundedText(object.organization_name, "organization_name", 240, false);
  if (!Array.isArray(object.role_codes) || object.role_codes.length === 0) {
    return adapterError("正式登录身份角色无效");
  }
  const roleCodes = object.role_codes.map((role) => requiredText(role, "role_code"));
  if (
    new Set(roleCodes).size !== roleCodes.length
    || roleCodes.some((role) => !SUPPORTED_ROLES.has(role))
    || !roleCodes.some((role) => INTERNAL_ROLES.has(role))
  ) {
    return adapterError("正式登录身份角色无效");
  }
  return validateFormalMaterialRequestFreshIdentity({
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: personId,
    authorization_version: authorizationVersion,
  });
}

function adapterError(message: string): never {
  throw new ApiError(409, message);
}

function exactObject(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return adapterError(`${name}不是有效对象`);
  }
  const object = value as Record<string, unknown>;
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return adapterError(`${name}必须精确包含正式字段`);
  }
  return object;
}

function requiredUuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) {
    return adapterError(`${name}无效`);
  }
  return value.toLowerCase();
}

function version(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) return adapterError(`${name}无效`);
  return value as number;
}

function nonnegativeVersion(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) return adapterError(`${name}无效`);
  return value as number;
}

function requiredText(value: unknown, name: string): string {
  if (typeof value !== "string" || !value || value !== value.trim()) {
    return adapterError(`${name}无效`);
  }
  return value;
}

function boundedText(value: unknown, name: string, maximum: number, allowEmpty = true): string {
  if (typeof value !== "string" || value !== value.trim() || value.length > maximum
      || (!allowEmpty && !value) || /[\u0000-\u001f\u007f]/.test(value)) {
    return adapterError(`${name}无效`);
  }
  return value;
}

function decimalText(value: unknown, name: string, positive: boolean): string {
  if (typeof value !== "string" || !DECIMAL.test(value)) return adapterError(`${name}无效`);
  if (positive && /^0(?:\.0{1,3})?$/.test(value)) return adapterError(`${name}必须大于零`);
  return value;
}

function approvalLines(value: unknown, returnMode: boolean): void {
  if (!Array.isArray(value) || value.length > 200) return adapterError("审批逐行内容无效");
  const ids = value.map((row, index) => {
    const object = exactObject(
      row,
      returnMode
        ? ["request_line_id", "requested_reapproval_qty", "reason"]
        : ["request_line_id", "approved_qty", "reason"],
      `审批明细 ${index + 1}`,
    );
    const id = requiredUuid(object.request_line_id, "request_line_id");
    decimalText(
      object[returnMode ? "requested_reapproval_qty" : "approved_qty"],
      returnMode ? "requested_reapproval_qty" : "approved_qty",
      returnMode,
    );
    boundedText(object.reason, "reason", 4000, !returnMode);
    return id;
  });
  if (new Set(ids).size !== ids.length) return adapterError("审批逐行内容包含重复明细");
}

function approvalShape(
  object: Record<string, unknown>,
  action: "approve" | "return" | "reject",
): void {
  if (!Array.isArray(object.lines) || !Array.isArray(object.return_lines)) {
    return adapterError("审批逐行内容无效");
  }
  if (action === "approve" && (!object.lines.length || object.return_lines.length)) {
    return adapterError("批准动作必须包含完整批准明细且不能包含退回明细");
  }
  if (action === "return" && (object.lines.length || !object.return_lines.length)) {
    return adapterError("退回动作必须包含完整重审明细且不能包含批准明细");
  }
  if (action === "reject" && (object.lines.length || object.return_lines.length)) {
    return adapterError("驳回动作不能包含逐行数量");
  }
  approvalLines(object.lines, false);
  approvalLines(object.return_lines, true);
  const comment = boundedText(object.comment, "comment", 4000);
  if ((action === "return" || action === "reject") && !comment) {
    return adapterError("退回或驳回必须填写处理意见");
  }
}

function validateSupportedMutationBody(
  action: MaterialRequestMutationAction,
  body: Record<string, unknown>,
): void {
  if (action === "create_supply_task") {
    validateSupplyCreateInput(body);
    return;
  }
  if (action === "update_supply_task" || action === "cancel_supply_task") {
    validateSupplyUpdateInput(body, action);
    return;
  }
  if (action === "update") {
    const object = exactObject(body, [
      "work_order_id", "purpose", "urgency", "expected_date", "address", "contact",
      "attachment_file_ids", "note", "lines", "expected_version",
    ], "正式需求修改内容");
    const { expected_version: _expectedVersion, ...draft } = object;
    validateMaterialRequestDraftInput(draft);
    return;
  }
  if (action === "submit") {
    exactObject(body, ["expected_version"], "正式需求提交内容");
    return;
  }
  if (action === "withdraw") {
    const object = exactObject(body, ["expected_version", "reason"], "正式需求撤回内容");
    boundedText(object.reason, "reason", 4000, false);
    return;
  }
  if (action === "cancel") {
    const object = exactObject(body, ["expected_version", "reason", "lines"], "正式需求安全取消内容");
    boundedText(object.reason, "reason", 4000, false);
    if (!Array.isArray(object.lines) || object.lines.length > 200) {
      return adapterError("正式需求取消明细无效");
    }
    const lineIds = object.lines.map((row, index) => {
      const line = exactObject(
        row,
        ["request_line_id", "cancelled_qty", "reason"],
        `取消明细 ${index + 1}`,
      );
      const lineId = requiredUuid(line.request_line_id, "request_line_id");
      decimalText(line.cancelled_qty, "cancelled_qty", true);
      boundedText(line.reason, "line.reason", 4000, false);
      return lineId;
    });
    if (new Set(lineIds).size !== lineIds.length) {
      return adapterError("正式需求取消明细包含重复行");
    }
    return;
  }
  if (["approve", "return", "reject"].includes(action)) {
    const object = exactObject(body, [
      "expected_request_version", "expected_step_version", "action", "lines", "return_lines", "comment",
    ], "正式内部审批内容");
    nonnegativeVersion(object.expected_step_version, "expected_step_version");
    if (object.action !== action) return adapterError("审批动作与写意图不一致");
    approvalShape(object, action as "approve" | "return" | "reject");
    return;
  }
  if (action === "register_external_approval") {
    const object = exactObject(body, [
      "expected_request_version", "expected_step_version", "evidence_file_id",
      "external_approver_name", "external_reference_no", "external_decided_at",
      "action", "lines", "return_lines", "comment",
    ], "正式外部审批登记内容");
    nonnegativeVersion(object.expected_step_version, "expected_step_version");
    requiredUuid(object.evidence_file_id, "evidence_file_id");
    boundedText(object.external_approver_name, "external_approver_name", 160, false);
    const reference = boundedText(object.external_reference_no, "external_reference_no", 200, false);
    if (!SAFE_EXTERNAL_REFERENCE.test(reference)) return adapterError("external_reference_no无效");
    const decidedAt = boundedText(object.external_decided_at, "external_decided_at", 80, false);
    if (!AWARE_TIMESTAMP.test(decidedAt) || !Number.isFinite(Date.parse(decidedAt))) {
      return adapterError("external_decided_at无效");
    }
    if (!["approve", "return", "reject"].includes(String(object.action))) {
      return adapterError("外部审批动作无效");
    }
    approvalShape(object, object.action as "approve" | "return" | "reject");
    return;
  }
  if (action === "verify_external_approval") {
    const object = exactObject(body, [
      "expected_request_version", "expected_step_version", "decision", "comment",
    ], "正式外部审批复核内容");
    nonnegativeVersion(object.expected_step_version, "expected_step_version");
    if (object.decision !== "accept" && object.decision !== "reject") {
      return adapterError("外部审批复核决定无效");
    }
    const comment = boundedText(object.comment, "comment", 4000);
    if (object.decision === "reject" && !comment) return adapterError("复核拒绝必须填写原因");
  }
}

function projectAccessContext(
  value: unknown,
  expectedIdentity: FormalMaterialRequestExpectedIdentity,
): FormalMaterialRequestAccess {
  const object = exactObject(value, [
    "person_id",
    "account_status",
    "employment_status",
    "authorization_version",
    "access_mode",
    "role_codes",
    "assignments",
    "permissions",
  ], "正式访问上下文");
  const personId = requiredUuid(object.person_id, "person_id");
  const authorizationVersion = version(object.authorization_version, "authorization_version");
  if (
    personId !== requiredUuid(expectedIdentity.person_id, "expected_person_id")
    || authorizationVersion !== version(
      expectedIdentity.authorization_version,
      "expected_authorization_version",
    )
  ) {
    return adapterError("登录身份或授权版本与正式访问上下文不一致");
  }
  if (
    object.account_status !== "active"
    || object.employment_status !== "active"
    || object.access_mode !== "active"
  ) {
    return adapterError("当前身份不是可执行需求提报的有效在职访问状态");
  }
  if (!Array.isArray(object.role_codes) || object.role_codes.length === 0) {
    return adapterError("正式访问上下文角色无效");
  }
  const roleCodes = object.role_codes.map((role) => requiredText(role, "role_code"));
  if (
    new Set(roleCodes).size !== roleCodes.length
    || roleCodes.some((role) => !SUPPORTED_ROLES.has(role))
    || !roleCodes.some((role) => INTERNAL_ROLES.has(role))
  ) {
    return adapterError("正式访问上下文角色无效");
  }
  if (!Array.isArray(object.assignments) || !Array.isArray(object.permissions)) {
    return adapterError("正式访问上下文授权结构无效");
  }
  const permissionKeys = object.permissions.map((value, index) => {
    const permission = exactObject(
      value,
      ["resource", "action", "field_code"],
      `正式权限 ${index + 1}`,
    );
    const resource = requiredText(permission.resource, "permission.resource");
    const action = requiredText(permission.action, "permission.action");
    if (typeof permission.field_code !== "string" || permission.field_code !== permission.field_code.trim()) {
      return adapterError("permission.field_code无效");
    }
    return `${resource}\u0000${action}\u0000${permission.field_code}`;
  });
  if (new Set(permissionKeys).size !== permissionKeys.length) {
    return adapterError("正式访问上下文包含重复权限");
  }
  const canRead = permissionKeys.includes("material_request\u0000read\u0000");
  const canCreate = canRead && permissionKeys.includes("material_request\u0000create\u0000");
  return validateFormalMaterialRequestAccess({
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: personId,
    authorization_version: authorizationVersion,
    can_read: canRead,
    can_create: canCreate,
    can_withdraw: canRead && permissionKeys.includes("material_request\u0000withdraw\u0000"),
    can_cancel: canRead && permissionKeys.includes("material_request\u0000cancel\u0000"),
    can_read_material_catalog: permissionKeys.includes("inventory\u0000read\u0000"),
    can_read_allocation_options: roleCodes.some((role) => role === "admin" || role === "provincial_manager"),
    can_approve_region: permissionKeys.includes("material_request\u0000approve_region\u0000approval_decision"),
    can_approve_headquarters: permissionKeys.includes("material_request\u0000approve_headquarters\u0000approval_decision"),
    can_register_external: permissionKeys.includes("material_request\u0000register_external\u0000approval_evidence"),
    can_verify_external: permissionKeys.includes("material_request\u0000verify_external\u0000approval_evidence"),
  });
}

export function validateFormalMaterialRequestAccess(value: unknown): FormalMaterialRequestAccess {
  const object = exactObject(
    value,
    [
      "schema_version", "person_id", "authorization_version", "can_read", "can_create",
      "can_withdraw", "can_cancel",
      "can_read_material_catalog", "can_read_allocation_options", "can_approve_region", "can_approve_headquarters",
      "can_register_external", "can_verify_external",
    ],
    "正式需求访问上下文",
  );
  if (object.schema_version !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return adapterError("正式需求访问上下文版本不受支持");
  }
  const capabilityFields = [
    "can_read", "can_create", "can_withdraw", "can_cancel", "can_read_material_catalog", "can_read_allocation_options", "can_approve_region",
    "can_approve_headquarters", "can_register_external", "can_verify_external",
  ] as const;
  if (capabilityFields.some((field) => typeof object[field] !== "boolean")) {
    return adapterError("正式需求访问授权无效");
  }
  if ((object.can_create || object.can_withdraw || object.can_cancel) && !object.can_read) {
    return adapterError("正式需求写权限缺少必需的读取回验权限");
  }
  return Object.freeze({
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    person_id: requiredUuid(object.person_id, "person_id"),
    authorization_version: version(object.authorization_version, "authorization_version"),
    can_read: object.can_read as boolean,
    can_create: object.can_create as boolean,
    can_withdraw: object.can_withdraw as boolean,
    can_cancel: object.can_cancel as boolean,
    can_read_material_catalog: object.can_read_material_catalog as boolean,
    can_read_allocation_options: object.can_read_allocation_options as boolean,
    can_approve_region: object.can_approve_region as boolean,
    can_approve_headquarters: object.can_approve_headquarters as boolean,
    can_register_external: object.can_register_external as boolean,
    can_verify_external: object.can_verify_external as boolean,
  });
}

export function validateFormalMaterialRequestEditableDraft(
  value: unknown,
  expectedRequestId: string,
  expectedVersion: number,
): FormalMaterialRequestEditableDraft {
  const object = exactObject(
    value,
    ["schema_version", "request_id", "request_version", "draft"],
    "正式需求可编辑草稿",
  );
  if (object.schema_version !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return adapterError("正式需求可编辑草稿版本不受支持");
  }
  const requestId = requiredUuid(object.request_id, "request_id");
  if (requestId !== requiredUuid(expectedRequestId, "expected_request_id")) {
    return adapterError("正式需求可编辑草稿与目标对象不一致");
  }
  const requestVersion = nonnegativeVersion(object.request_version, "request_version");
  if (requestVersion !== nonnegativeVersion(expectedVersion, "expected_version")) {
    return adapterError("正式需求可编辑草稿版本已变化，请重新读取");
  }
  return Object.freeze({
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: requestId,
    request_version: requestVersion,
    draft: validateMaterialRequestDraftInput(object.draft),
  });
}

function validateWriteHeaders(
  value: unknown,
  signature: unknown,
): Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }> {
  const headers = exactObject(value, ["X-Request-ID", "Idempotency-Key"], "正式需求写请求头");
  const requestId = requiredText(headers["X-Request-ID"], "X-Request-ID");
  const idempotencyKey = requiredText(headers["Idempotency-Key"], "Idempotency-Key");
  if (!SAFE_COORDINATE.test(requestId) || !SAFE_COORDINATE.test(idempotencyKey)) {
    return adapterError("正式需求写请求坐标无效");
  }
  if (signature !== idempotencyKey) return adapterError("正式需求写意图签名与幂等键不一致");
  return value as Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>;
}

function mutationMethod(intent: MaterialRequestMutationIntent): "PUT" | "POST" {
  const object = exactObject(intent, [
    "request_id", "action", "path", "body", "expected_version", "signature", "headers",
  ], "正式需求写意图");
  const requestId = requiredUuid(object.request_id, "request_id");
  const action = requiredText(object.action, "action") as MaterialRequestMutationAction;
  if (!MATERIAL_REQUEST_MUTATION_ACTIONS.includes(action)) return adapterError("正式需求写动作无效");
  const expectedVersion = nonnegativeVersion(object.expected_version, "expected_version");
  if (!object.body || typeof object.body !== "object" || Array.isArray(object.body)) {
    return adapterError("正式需求写内容不是有效对象");
  }
  const body = object.body as Record<string, unknown>;
  const versionField = ["update", "submit", "withdraw", "cancel"].includes(action)
    ? "expected_version"
    : "expected_request_version";
  if (body[versionField] !== expectedVersion) return adapterError("正式需求写内容版本与意图不一致");
  validateSupportedMutationBody(action, body);
  validateWriteHeaders(object.headers, object.signature);

  const root = `/v1/material-requests/${requestId}`;
  const step = `${root}/approval-steps/(${UUID_PATH_SOURCE})`;
  let expectedPath: RegExp | string;
  let method: "PUT" | "POST" = "POST";
  switch (action) {
    case "update":
      expectedPath = root;
      method = "PUT";
      break;
    case "submit":
      expectedPath = `${root}/submit`;
      break;
    case "withdraw":
      expectedPath = `${root}/withdraw`;
      break;
    case "cancel":
      expectedPath = `${root}/cancel`;
      break;
    case "approve":
    case "return":
    case "reject":
      expectedPath = new RegExp(`^${step}/decision$`, "i");
      if (body.action !== action) return adapterError("审批动作与正式需求写意图不一致");
      break;
    case "register_external_approval":
      expectedPath = new RegExp(`^${step}/external-evidence$`, "i");
      break;
    case "verify_external_approval":
      expectedPath = new RegExp(`^${step}/external-evidence/(${UUID_PATH_SOURCE})/verification$`, "i");
      break;
    case "create_supply_task":
      expectedPath = `${root}/supply-tasks`;
      break;
    case "update_supply_task":
    case "cancel_supply_task":
      expectedPath = new RegExp(`^${root}/supply-tasks/(${UUID_PATH_SOURCE})$`, "i");
      break;
    default:
      return adapterError("该正式需求写动作尚无已验收后端接口");
  }
  if (
    typeof object.path !== "string"
    || (typeof expectedPath === "string" ? object.path !== expectedPath : !expectedPath.test(object.path))
  ) {
    return adapterError("正式需求写路径与动作不一致");
  }
  const pathUuids = object.path.match(new RegExp(UUID_PATH_SOURCE, "gi")) || [];
  if (
    pathUuids.length < 1
    || pathUuids.some((value) => requiredUuid(value, "path_uuid") !== value)
  ) {
    return adapterError("正式需求写路径必须使用规范 UUID");
  }
  return method;
}

export function createFormalMaterialRequestAdapter(
  expectedIdentity: FormalMaterialRequestExpectedIdentity,
  requester: FormalMaterialRequestRequester = api,
  noReplayRequesterOverride?: FormalMaterialRequestRequester,
): FormalMaterialRequestAdapter {
  const noReplayRequester = noReplayRequesterOverride ?? (requester === api ? apiNoReplay : undefined);
  const requireNoReplayRequester = (): FormalMaterialRequestRequester => {
    if (!noReplayRequester) throw new ApiError(503, "分配只读核验通道不可用");
    return noReplayRequester;
  };
  const frozenIdentity = Object.freeze({
    person_id: requiredUuid(expectedIdentity.person_id, "expected_person_id"),
    authorization_version: version(
      expectedIdentity.authorization_version,
      "expected_authorization_version",
    ),
  });
  return Object.freeze({
    async loadIdentity() {
      return projectFreshIdentity(await requester<unknown>("/auth/me", {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }), frozenIdentity);
    },
    async loadIdentityNoReplay() {
      const read = requireNoReplayRequester();
      return projectFreshIdentity(await read<unknown>("/auth/me", {
        cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }), frozenIdentity);
    },
    async loadAccess() {
      return projectAccessContext(await requester<unknown>("/access/context", {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }), frozenIdentity);
    },
    async loadAccessNoReplay() {
      const read = requireNoReplayRequester();
      return projectAccessContext(await read<unknown>("/access/context", {
        cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }), frozenIdentity);
    },
    lifecycleCommandStatus(xRequestId: string) {
      const checkedRequestId = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(checkedRequestId)) {
        return Promise.reject(new ApiError(409, "生命周期命令查询坐标无效"));
      }
      return requester("/v1/material-request-lifecycle-command-status", {
        method: "GET",
        cache: "no-store",
        headers: {
          "X-Request-ID": checkedRequestId,
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
      });
    },
    supplyCommandStatus(xRequestId: string) {
      if (typeof xRequestId !== "string" || !SAFE_COORDINATE.test(xRequestId)) {
        return Promise.reject(new ApiError(409, "供给命令查询坐标无效"));
      }
      return requester(`/v1/material-request-supply-command-status?trace_request_id=${encodeURIComponent(xRequestId)}`, {
        method: "GET",
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    allocationCommandStatus(xRequestId: string) {
      const checkedRequestId = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(checkedRequestId)) {
        return Promise.reject(new ApiError(409, "分配命令查询坐标无效"));
      }
      return requester<unknown>("/v1/material-request-allocation-command-status", {
        method: "GET",
        cache: "no-store",
        headers: {
          "X-Request-ID": checkedRequestId,
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
      }).then(validateMaterialRequestAllocationCommandStatus);
    },
    allocationCommandStatusNoReplay(xRequestId: string) {
      const checkedRequestId = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(checkedRequestId)) return Promise.reject(new ApiError(409, "分配命令查询坐标无效"));
      const read = requireNoReplayRequester();
      return read<unknown>("/v1/material-request-allocation-command-status", {
        method: "GET", cache: "no-store", headers: {
          "X-Request-ID": checkedRequestId, "Cache-Control": "no-store", Pragma: "no-cache",
        },
      }).then(validateMaterialRequestAllocationCommandStatus);
    },
    reservationCommandStatus(xRequestId: string) {
      const checked = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(checked)) return Promise.reject(new ApiError(409, "预约命令查询坐标无效"));
      return requester<unknown>("/v1/material-request-reservation-command-status", {
        method: "GET", cache: "no-store", headers: { "X-Request-ID": checked, "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateMaterialRequestReservationCommandStatus);
    },
    reservationCommandStatusNoReplay(xRequestId: string) {
      const checked = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(checked)) return Promise.reject(new ApiError(409, "预约命令查询坐标无效"));
      const read = requireNoReplayRequester();
      return read<unknown>("/v1/material-request-reservation-command-status", {
        method: "GET", cache: "no-store", headers: { "X-Request-ID": checked, "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateMaterialRequestReservationCommandStatus);
    },
    list(afterId: string | null) {
      const suffix = afterId === null
        ? ""
        : `&after_id=${encodeURIComponent(requiredUuid(afterId, "after_id"))}`;
      return requester(`/v1/material-requests?limit=50${suffix}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    detail(requestId: string) {
      return requester(`/v1/material-requests/${requiredUuid(requestId, "request_id")}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    detailNoReplay(requestId: string) {
      const read = requireNoReplayRequester();
      return read(`/v1/material-requests/${requiredUuid(requestId, "request_id")}`, {
        cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateMaterialRequestDetail);
    },
    loadDraftForEdit(requestId: string) {
      return requester(
        `/v1/material-requests/${requiredUuid(requestId, "request_id")}/editable-draft`,
        { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } },
      );
    },
    listWorkOrderOptions(query: string, afterId: string | null) {
      const checkedQuery = validateMaterialRequestWorkOrderOptionQuery(query);
      const queryPart = checkedQuery ? `&query=${encodeURIComponent(checkedQuery)}` : "";
      const cursorPart = afterId === null
        ? ""
        : `&after_id=${encodeURIComponent(requiredUuid(afterId, "after_id"))}`;
      return requester(
        `/v1/material-request-options/work-orders?limit=50${queryPart}${cursorPart}`,
        { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } },
      );
    },
    workOrderOptionDetail(workOrderId: string) {
      return requester(
        `/v1/material-request-options/work-orders/${requiredUuid(workOrderId, "work_order_id")}`,
        { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } },
      );
    },
    listMaterials(query: string, afterId: string | null) {
      const checkedQuery = formalMaterialCatalogQuery(query);
      const queryPart = checkedQuery ? `&query=${encodeURIComponent(checkedQuery)}` : "";
      const cursorPart = afterId === null
        ? ""
        : `&after_id=${encodeURIComponent(requiredUuid(afterId, "after_id"))}`;
      return requester(`/v1/materials?limit=50${queryPart}${cursorPart}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    listAllocationOptions(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/allocation-options?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      return requester<unknown>(path, {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateMaterialRequestAllocationOptionPage);
    },
    createAllocation(requestId: string, input: MaterialRequestAllocationCreateInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const checkedRequestId = requiredUuid(requestId, "request_id");
      const body = exactObject(input, [
        "expected_request_version", "request_line_id", "source_stock_account_id", "allocated_qty",
        "source_balance_version", "source_ledger_cursor", "serial_ids",
      ], "分配写内容");
      if (!Number.isSafeInteger(body.expected_request_version) || (body.expected_request_version as number) < 0) {
        return Promise.reject(new ApiError(409, "分配需求版本无效"));
      }
      requiredUuid(body.request_line_id, "request_line_id");
      requiredUuid(body.source_stock_account_id, "source_stock_account_id");
      if (typeof body.allocated_qty !== "string" || !DECIMAL.test(body.allocated_qty)
          || /^0(?:\.0{1,3})?$/.test(body.allocated_qty)) {
        return Promise.reject(new ApiError(409, "分配数量无效"));
      }
      for (const field of ["source_balance_version", "source_ledger_cursor"] as const) {
        if (!Number.isSafeInteger(body[field]) || (body[field] as number) < 0) {
          return Promise.reject(new ApiError(409, "货源投影坐标无效"));
        }
      }
      if (!Array.isArray(body.serial_ids) || body.serial_ids.length > 1000) {
        return Promise.reject(new ApiError(409, "分配串码内容无效"));
      }
      const serialIds = body.serial_ids.map((serialId) => requiredUuid(serialId, "serial_id"));
      if (new Set(serialIds).size !== serialIds.length) {
        return Promise.reject(new ApiError(409, "分配串码不能重复"));
      }
      const checkedHeaders = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      const write = requireNoReplayRequester();
      return write<unknown>(`/v1/material-requests/${checkedRequestId}/allocations`, {
        method: "POST",
        headers: checkedHeaders,
        ...jsonBody({
          expected_request_version: body.expected_request_version,
          request_line_id: requiredUuid(body.request_line_id, "request_line_id"),
          source_stock_account_id: requiredUuid(body.source_stock_account_id, "source_stock_account_id"),
          allocated_qty: body.allocated_qty,
          source_balance_version: body.source_balance_version,
          source_ledger_cursor: body.source_ledger_cursor,
          serial_ids: serialIds,
        }),
      }).then(validateMaterialRequestAllocationMutationResult);
    },
    listReservationOptions(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/reservation-options?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      const read = requireNoReplayRequester();
      return read<unknown>(path, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } })
        .then(validateMaterialRequestReservationOptionPage);
    },
    releaseCommandStatusNoReplay(xRequestId: string) {
      const trace = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(trace)) return Promise.reject(new ApiError(409, "释放核验坐标无效"));
      return requireNoReplayRequester()<unknown>("/v1/material-request-reservation-release-command-status", {
        method: "GET", cache: "no-store", headers: { "X-Request-ID": trace, "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateReleaseStatus);
    },
    listReleaseOptions(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/reservation-release-options?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      return requireNoReplayRequester()<unknown>(path, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(validateReleasePage);
    },
    listFulfillmentPreparation(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/fulfillment-preparation?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      return requireNoReplayRequester()<unknown>(path, { method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(validateFulfillmentPreparation);
    },
    createRelease(requestId: string, input: ReleaseInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const body = validateReleaseInput(input);
      const checked = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/reservation-releases`, {
        method: "POST", headers: checked, ...jsonBody(body),
      }).then(validateReleaseResult);
    },
    outboundCommandStatusNoReplay(xRequestId: string) {
      const trace = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(trace)) return Promise.reject(new ApiError(409, "出库核验坐标无效"));
      return requireNoReplayRequester()<unknown>("/v1/material-request-outbound-command-status", {
        method: "GET", cache: "no-store", headers: { "X-Request-ID": trace, "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validateOutboundStatus);
    },
    listOutboundOptions(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/outbound-options?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      return requireNoReplayRequester()<unknown>(path, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(validateOutboundPage);
    },
    listShipments(requestId: string) {
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/shipments`, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(value => { if (!Array.isArray(value)) throw new ApiError(502, "发运查询响应无效"); return value.map(validateShipmentResult); });
    },
    listShipmentOptions(requestId: string) {
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/shipment-options`, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(validateShipmentOptions);
    },
    createShipment(requestId: string, input: ShipmentInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const body = validateShipmentInput(input);
      const checked = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/shipments`, { method: "POST", headers: checked, ...jsonBody(body) }).then(validateShipmentResult);
    },
    createInboundOrder(requestId: string, input: InboundOrderInput, headers: Readonly<{ "X-Request-ID": string }>) {
      const body = input; return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/inbound-orders`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, ...jsonBody(body) }).then(validateInboundOrderResult);
    },
    postInboundOrder(requestId: string, inboundOrderId: string, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const checked = validateWriteHeaders(headers, headers["Idempotency-Key"]); return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/inbound-orders/${requiredUuid(inboundOrderId, "inbound_order_id")}/post`, { method: "POST", headers: checked }).then(validateInboundPostingResult);
    },
    createOutbound(requestId: string, input: OutboundInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const body = validateOutboundInput(input);
      const checked = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/outbounds`, {
        method: "POST", headers: checked, ...jsonBody(body),
      }).then(validateOutboundResult);
    },
    pickCommandStatusNoReplay(xRequestId: string) {
      const trace = requiredText(xRequestId, "X-Request-ID");
      if (!SAFE_COORDINATE.test(trace)) return Promise.reject(new ApiError(409, "拣货核验坐标无效"));
      return requireNoReplayRequester()<unknown>("/v1/material-request-reservation-pick-command-status", {
        method: "GET", cache: "no-store", headers: { "X-Request-ID": trace, "Cache-Control": "no-store", Pragma: "no-cache" },
      }).then(validatePickStatus);
    },
    listPickOptions(requestId: string, requestLineId: string) {
      const path = `/v1/material-requests/${requiredUuid(requestId, "request_id")}/reservation-pick-options?request_line_id=${encodeURIComponent(requiredUuid(requestLineId, "request_line_id"))}`;
      return requireNoReplayRequester()<unknown>(path, { cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } }).then(validatePickPage);
    },
    createPick(requestId: string, input: PickInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const body = validatePickInput(input);
      const checked = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      return requireNoReplayRequester()<unknown>(`/v1/material-requests/${requiredUuid(requestId, "request_id")}/reservation-picks`, {
        method: "POST", headers: checked, ...jsonBody(body),
      }).then(validatePickResult);
    },
    createReservation(requestId: string, input: MaterialRequestReservationCreateInput, headers: Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>) {
      const checkedRequestId = requiredUuid(requestId, "request_id");
      const body = exactObject(input, ["expected_request_version", "request_line_id", "allocation_id", "reserved_qty", "source_balance_version", "source_ledger_cursor", "serial_ids"], "预约写内容");
      if (!Number.isSafeInteger(body.expected_request_version) || (body.expected_request_version as number) < 0) return Promise.reject(new ApiError(409, "预约需求版本无效"));
      requiredUuid(body.request_line_id, "request_line_id"); requiredUuid(body.allocation_id, "allocation_id");
      if (typeof body.reserved_qty !== "string" || !DECIMAL.test(body.reserved_qty) || /^0(?:\.0{1,3})?$/.test(body.reserved_qty)) return Promise.reject(new ApiError(409, "预约数量无效"));
      for (const field of ["source_balance_version", "source_ledger_cursor"] as const) if (!Number.isSafeInteger(body[field]) || (body[field] as number) < 0) return Promise.reject(new ApiError(409, "货源投影坐标无效"));
      if (!Array.isArray(body.serial_ids) || body.serial_ids.length > 1000) return Promise.reject(new ApiError(409, "预约串码内容无效"));
      const serialIds = body.serial_ids.map((serialId) => requiredUuid(serialId, "serial_id"));
      if (new Set(serialIds).size !== serialIds.length) return Promise.reject(new ApiError(409, "预约串码不能重复"));
      const checkedHeaders = validateWriteHeaders(headers, headers["Idempotency-Key"]);
      const write = requireNoReplayRequester();
      return write<unknown>(`/v1/material-requests/${checkedRequestId}/reservations`, { method: "POST", headers: checkedHeaders, ...jsonBody({ expected_request_version: body.expected_request_version, request_line_id: requiredUuid(body.request_line_id, "request_line_id"), allocation_id: requiredUuid(body.allocation_id, "allocation_id"), reserved_qty: body.reserved_qty, source_balance_version: body.source_balance_version, source_ledger_cursor: body.source_ledger_cursor, serial_ids: serialIds }) }).then(validateMaterialRequestReservationMutationResult);
    },
    createDraft(intent: MaterialRequestCreateIntent) {
      const object = exactObject(intent, [
        "client_draft_key", "action", "path", "body", "signature", "headers",
      ], "正式需求创建意图");
      if (object.action !== "create" || object.path !== "/v1/material-requests") {
        return Promise.reject(new ApiError(409, "正式需求创建意图路径或动作无效"));
      }
      const headers = validateWriteHeaders(object.headers, object.signature);
      if (object.client_draft_key !== `draft-${headers["X-Request-ID"]}`) {
        return Promise.reject(new ApiError(409, "正式需求草稿锚点与请求坐标不一致"));
      }
      validateMaterialRequestDraftInput(object.body);
      return requester(object.path, { method: "POST", headers, ...jsonBody(object.body) });
    },
    mutate(intent: MaterialRequestMutationIntent) {
      let method: "PUT" | "POST";
      try {
        method = mutationMethod(intent);
      } catch (error) {
        return Promise.reject(error);
      }
      return requester(intent.path, {
        method,
        headers: intent.headers,
        ...jsonBody(intent.body),
      });
    },
  });
}

export function isDefinitiveMaterialRequestRejection(error: unknown): boolean {
  return error instanceof ApiError
    && error.responseReceived
    && error.status >= 400
    && error.status < 500
    && error.status !== 408
    && error.status !== 425;
}
