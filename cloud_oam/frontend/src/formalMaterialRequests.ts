import { ApiError, mutationHeaders } from "./api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const DECIMAL_18_3 = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;
const SAFE_COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const SAFE_CLIENT_DRAFT_KEY = /^draft-[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const CONTACT_MOBILE = /^\+?[0-9][0-9 -]{4,30}[0-9]$/;

export const MATERIAL_REQUEST_SCHEMA_VERSION = "1.0" as const;

export const MATERIAL_REQUEST_STATUSES = [
  "draft",
  "submitted",
  "approval_in_progress",
  "returned",
  "partially_approved",
  "approved",
  "rejected",
  "withdrawn",
  "cancellation_pending",
  "cancelled",
] as const;

export const MATERIAL_REQUEST_APPROVAL_MODES = [
  "external_registration",
  "direct_star",
] as const;
export const MATERIAL_REQUEST_PHASE_ONE_APPROVAL_MODE = "external_registration" as const;

export const MATERIAL_REQUEST_APPROVAL_INSTANCE_STATUSES = [
  "active", "returned", "completed", "rejected", "withdrawn", "cancelled", "superseded",
] as const;
export const MATERIAL_REQUEST_APPROVAL_STEP_STATUSES = [
  "pending", "open", "awaiting_external_evidence", "evidence_pending_verification",
  "approved", "partially_approved", "rejected", "returned", "cancelled", "superseded",
] as const;
export const MATERIAL_REQUEST_APPROVAL_SOURCE_MODES = [
  "internal", "external_registration", "direct_star",
] as const;
export const MATERIAL_REQUEST_APPROVAL_ASSIGNEE_ROLES = [
  "admin", "provincial_manager", "star_headquarters_approver",
] as const;
export const MATERIAL_REQUEST_APPROVAL_CANDIDATE_KINDS = [
  "assignee", "registrar", "verifier",
] as const;
export const MATERIAL_REQUEST_LINE_STATUSES = [
  "draft", "approval_pending", "approved", "partially_approved", "rejected", "cancelled",
] as const;
export const MATERIAL_REQUEST_URGENCIES = ["normal", "urgent", "emergency"] as const;

export const MATERIAL_REQUEST_ALLOWED_ACTIONS = [
  "update",
  "submit",
  "withdraw",
  "cancel",
  "approve",
  "return",
  "reject",
  "register_external_approval",
  "verify_external_approval",
  "propose_substitution",
  "confirm_substitution",
  "reject_substitution",
  "create_supply_task",
] as const;
export const MATERIAL_REQUEST_SUPPLY_TASK_ALLOWED_ACTIONS = [
  "update_supply_task",
  "cancel_supply_task",
] as const;
export const MATERIAL_REQUEST_MUTATION_ACTIONS = [
  ...MATERIAL_REQUEST_ALLOWED_ACTIONS,
  ...MATERIAL_REQUEST_SUPPLY_TASK_ALLOWED_ACTIONS,
] as const;
export const MATERIAL_REQUEST_WRITE_ACTIONS = [
  "create",
  ...MATERIAL_REQUEST_MUTATION_ACTIONS,
] as const;

export const MATERIAL_REQUEST_SUPPLY_TYPES = [
  "cross_region_transfer",
  "headquarters_replenishment",
  "star_replenishment",
  "external_procurement_reference",
] as const;
export const MATERIAL_REQUEST_SUPPLY_TASK_STATUSES = [
  "open",
  "reference_registered",
  "awaiting_supply",
  "cancelled",
  "closed_no_supply",
] as const;

const ALLOCATION_STATUSES = [
  "not_allocated", "partially_allocated", "allocated", "shortage",
] as const;
const RESERVATION_STATUSES = [
  "not_reserved", "pending", "reserved", "partially_released", "released", "fulfilled",
] as const;
const OUTBOUND_STATUSES = ["not_started", "pending_pick", "picked", "outbound"] as const;
const SHIPMENT_STATUSES = [
  "not_started", "pending_handover", "shipped", "in_transit", "exception",
] as const;
const LOGISTICS_SIGNATURE_STATUSES = ["not_signed", "signed", "refused", "exception"] as const;
const OAM_RECEIPT_STATUSES = ["not_occurred", "synced", "exception"] as const;
const PERSONAL_INBOUND_STATUSES = [
  "not_started", "pending_acceptance", "partially_accepted", "accepted", "posted",
] as const;
const NOTIFICATION_STATUSES = [
  "not_started", "queued", "sent", "delivered", "read", "failed",
] as const;
const RECONCILIATION_STATUSES = [
  "not_started", "pending", "staged", "validated", "reconciled", "conflict", "failed",
] as const;

/** Short UI labels map one-to-one to these exact formal wire fields. */
export const MATERIAL_REQUEST_STATE_AXIS_FIELDS = Object.freeze({
  application: "request_status",
  allocation: "allocation_status",
  reservation: "reservation_status",
  outbound: "outbound_status",
  shipment: "shipment_status",
  logistics_signature: "logistics_signature_status",
  oam_receipt: "oam_receipt_status",
  inbound: "personal_inbound_status",
  notification: "notification_status",
  reconciliation: "reconciliation_status",
} as const);

const STATE_AXIS_WIRE_KEYS = Object.values(MATERIAL_REQUEST_STATE_AXIS_FIELDS);

export type MaterialRequestStatus = typeof MATERIAL_REQUEST_STATUSES[number];
export type MaterialRequestApprovalMode = typeof MATERIAL_REQUEST_APPROVAL_MODES[number];
export type MaterialRequestApprovalInstanceStatus = typeof MATERIAL_REQUEST_APPROVAL_INSTANCE_STATUSES[number];
export type MaterialRequestApprovalStepStatus = typeof MATERIAL_REQUEST_APPROVAL_STEP_STATUSES[number];
export type MaterialRequestApprovalSourceMode = typeof MATERIAL_REQUEST_APPROVAL_SOURCE_MODES[number];
export type MaterialRequestAllowedAction = typeof MATERIAL_REQUEST_ALLOWED_ACTIONS[number];
export type MaterialRequestSupplyTaskAllowedAction = typeof MATERIAL_REQUEST_SUPPLY_TASK_ALLOWED_ACTIONS[number];
export type MaterialRequestMutationAction = typeof MATERIAL_REQUEST_MUTATION_ACTIONS[number];
export type MaterialRequestWriteAction = typeof MATERIAL_REQUEST_WRITE_ACTIONS[number];

export type MaterialRequestDraftAddress = Readonly<{
  province_code: string;
  province_name: string;
  city_name: string;
  district_name: string;
  detail: string;
}>;
export type MaterialRequestDraftContact = Readonly<{
  name: string;
  mobile: string;
}>;
export type MaterialRequestDraftLine = Readonly<{
  material_id: string;
  requested_qty: string;
  required_date: string | null;
  suggested_substitute_material_id: string | null;
  note: string;
}>;
export type MaterialRequestDraftInput = Readonly<{
  work_order_id: string | null;
  purpose: string;
  urgency: typeof MATERIAL_REQUEST_URGENCIES[number];
  expected_date: string | null;
  address: MaterialRequestDraftAddress;
  contact: MaterialRequestDraftContact;
  attachment_file_ids: readonly string[];
  note: string;
  lines: readonly MaterialRequestDraftLine[];
}>;

export type MaterialRequestStateAxes = Readonly<{
  request_status: MaterialRequestStatus;
  allocation_status: typeof ALLOCATION_STATUSES[number];
  reservation_status: typeof RESERVATION_STATUSES[number];
  outbound_status: typeof OUTBOUND_STATUSES[number];
  shipment_status: typeof SHIPMENT_STATUSES[number];
  logistics_signature_status: typeof LOGISTICS_SIGNATURE_STATUSES[number];
  oam_receipt_status: typeof OAM_RECEIPT_STATUSES[number];
  personal_inbound_status: typeof PERSONAL_INBOUND_STATUSES[number];
  notification_status: typeof NOTIFICATION_STATUSES[number];
  reconciliation_status: typeof RECONCILIATION_STATUSES[number];
}>;

export type MaterialRequestApprovalStep = Readonly<{
  step_id: string;
  step_no: 1 | 2 | 3;
  attempt_no: number;
  predecessor_step_id: string | null;
  supersedes_step_id: string | null;
  reopened_from_step_id: string | null;
  source_mode: MaterialRequestApprovalSourceMode;
  status: MaterialRequestApprovalStepStatus;
  assignee_snapshot: MaterialRequestApprovalAssigneeSnapshot | null;
  candidate_pool_summary: MaterialRequestApprovalCandidatePoolSummary | null;
  opened_at: string | null;
  decided_at: string | null;
  version: number;
  line_decisions: MaterialRequestApprovalLineDecision[];
}>;
export type MaterialRequestApprovalAssigneeSnapshot = Readonly<{
  name_masked: string;
  role_code: typeof MATERIAL_REQUEST_APPROVAL_ASSIGNEE_ROLES[number];
}>;
export type MaterialRequestApprovalCandidatePoolSummary = Readonly<{
  candidate_count: number;
  candidate_kinds: typeof MATERIAL_REQUEST_APPROVAL_CANDIDATE_KINDS[number][];
}>;
export type MaterialRequestApprovalLineDecision = Readonly<{
  decision_id: string;
  step_id: string;
  request_revision_id: string;
  revision_no: number;
  request_line_id: string;
  input_qty: string;
  approved_qty: string;
  rejected_qty: string;
  reason: string;
  decision_source: MaterialRequestApprovalSourceMode;
  external_registration_id: string | null;
  decided_at: string;
}>;
export type MaterialRequestExternalEvidenceSummary = Readonly<{
  registration_id: string;
  registration_no: string;
  step_id: string;
  external_action: "approve" | "partial_approve" | "reject" | "return";
  status: "pending_verification" | "accepted" | "rejected" | "superseded";
  evidence_file_id: string;
  external_approver_name_masked: string;
  external_decided_at: string;
  registered_at: string;
  verified_at: string | null;
  version: number;
}>;
export type MaterialRequestReturnLineFact = Readonly<{
  return_fact_id: string;
  return_action_id: string;
  instance_id: string;
  returned_from_step_id: string;
  target_kind: "requester_revision" | "approval_step";
  target_step_id: string | null;
  request_revision_id: string;
  revision_no: number;
  request_line_id: string;
  returned_step_input_qty: string;
  target_step_max_qty: string;
  required_review_qty: string;
  reason: string;
  occurred_at: string;
}>;
export type MaterialRequestApprovalInstance = Readonly<{
  instance_id: string;
  request_revision_id: string;
  revision_no: number;
  attempt_no: number;
  status: MaterialRequestApprovalInstanceStatus;
  current_step_no: 1 | 2 | 3 | null;
  current_step_id: string | null;
  version: number;
  steps: MaterialRequestApprovalStep[];
  external_evidence_summaries: MaterialRequestExternalEvidenceSummary[] | null;
  return_line_facts: MaterialRequestReturnLineFact[] | null;
}>;
export type MaterialRequestLine = Readonly<{
  request_line_id: string;
  revision_id: string;
  revision_no: number;
  line_no: number;
  material_id: string;
  requested_qty: string;
  required_date: string | null;
  suggested_substitute_material_id: string | null;
  note: string;
  final_approved_qty: string;
  cancelled_qty: string;
  status: typeof MATERIAL_REQUEST_LINE_STATUSES[number];
  version: number;
}>;
export type MaterialRequestSupplyTask = Readonly<{
  id: string;
  task_no: string;
  request_line_id: string;
  substitution_decision_id: string | null;
  supply_type: typeof MATERIAL_REQUEST_SUPPLY_TYPES[number];
  reference_no: string | null;
  expected_qty: string;
  original_equivalent_qty: string;
  expected_date: string | null;
  status: typeof MATERIAL_REQUEST_SUPPLY_TASK_STATUSES[number];
  version: number;
  created_at: string;
  updated_at: string;
  allowed_actions: MaterialRequestSupplyTaskAllowedAction[];
}>;
export type MaterialRequestAddressSnapshot = Readonly<{
  province_code: string;
  province_name: string;
  city_name: string;
  district_name: string;
  detail_masked: string;
}>;
export type MaterialRequestContactMasked = Readonly<{
  name_masked: string;
  mobile_masked: string;
}>;
export type MaterialRequestAttachmentRef = Readonly<{
  revision_id: string;
  revision_no: number;
  request_line_id: string | null;
  file_id: string;
  display_name: string;
  purpose: "request_attachment" | "request_line_attachment";
}>;
export type MaterialRequestRevisionSummary = Readonly<{
  revision_id: string;
  revision_no: number;
  previous_revision_id: string | null;
  status: "draft" | "sealed";
  line_count: number;
  attachment_count: number;
  sealed_at: string | null;
  created_at: string;
}>;
export type MaterialRequestDetail = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  request_id: string;
  request_no: string;
  request_version: number;
  current_revision_id: string;
  current_revision_no: number;
  work_order_id: string | null;
  requester_person_id: string;
  requester_org_id: string;
  purpose: string;
  urgency: typeof MATERIAL_REQUEST_URGENCIES[number];
  expected_date: string | null;
  address_snapshot: MaterialRequestAddressSnapshot;
  contact_masked: MaterialRequestContactMasked;
  note: string;
  attachment_refs: MaterialRequestAttachmentRef[];
  approval_mode: MaterialRequestApprovalMode;
  states: MaterialRequestStateAxes;
  approval_instance: MaterialRequestApprovalInstance | null;
  lines: MaterialRequestLine[];
  revision_history: MaterialRequestRevisionSummary[];
  approval_history: MaterialRequestApprovalInstance[];
  supply_tasks: MaterialRequestSupplyTask[];
  allowed_actions: MaterialRequestAllowedAction[];
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
}>;
export type MaterialRequestSummary = Omit<
  MaterialRequestDetail,
  "schema_version" | "lines" | "revision_history" | "approval_history" | "supply_tasks"
> & Readonly<{
  line_count: number;
}>;
export type MaterialRequestPage = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  items: MaterialRequestSummary[];
  next_after_id: string | null;
}>;
export type MaterialRequestMutationResult = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  request_id: string;
  action: MaterialRequestMutationAction;
  request_version: number;
  revision_id: string;
  revision_no: number;
  approval_instance_id: string | null;
  approval_attempt_no: number | null;
  current_step_id: string | null;
  states: MaterialRequestStateAxes;
  idempotency_replayed: boolean;
}>;
export type MaterialRequestLifecycleCommand = Readonly<{
  action: "withdraw" | "cancel";
  request_id: string;
  request_version: number;
  revision_id: string;
  revision_no: number;
  approval_instance_id: string;
  approval_attempt_no: number;
  current_step_id: null;
  states: MaterialRequestStateAxes;
  occurred_at: string;
}>;
export type MaterialRequestLifecycleCommandStatus = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  lookup_status: "not_observed" | "confirmed";
  command: MaterialRequestLifecycleCommand | null;
}>;
export type MaterialRequestCreateResult = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_SCHEMA_VERSION;
  request_id: string;
  action: "create";
  request_version: 0;
  revision_id: string;
  revision_no: 1;
  states: MaterialRequestStateAxes;
  idempotency_replayed: boolean;
}>;

export class MaterialRequestContractError extends ApiError {
  readonly code: string;
  constructor(code: string, message: string) {
    super(409, message);
    this.name = "MaterialRequestContractError";
    this.code = code;
  }
}

function invalid(code: string, message: string): never {
  throw new MaterialRequestContractError(code, message);
}
function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalid("material_request_contract_object_invalid", `${name}不是有效对象`);
  }
  return value as Record<string, unknown>;
}
function field(object: Record<string, unknown>, name: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(object, name)) {
    return invalid("material_request_contract_field_missing", `正式需求响应缺少字段 ${name}`);
  }
  return object[name];
}
function exactKeys(object: Record<string, unknown>, keys: readonly string[], name: string): void {
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    invalid("material_request_contract_shape_invalid", `${name}必须精确包含正式字段`);
  }
}
function exactEnum<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return invalid("material_request_contract_enum_unknown", `正式需求响应包含未知 ${name}`);
  }
  return value as T;
}
function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) {
    return invalid("material_request_contract_uuid_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value.toLowerCase();
}
function text(value: unknown, name: string): string {
  if (typeof value !== "string" || !value || value !== value.trim()) {
    return invalid("material_request_contract_text_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value;
}
function plainText(value: unknown, name: string): string {
  if (typeof value !== "string" || value !== value.trim()) {
    return invalid("material_request_contract_text_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value;
}
function nullableText(value: unknown, name: string): string | null {
  return value === null ? null : text(value, name);
}
function nullableUuid(value: unknown, name: string): string | null {
  return value === null ? null : uuid(value, name);
}
function dateOnly(value: unknown, name: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !DATE_ONLY.test(value)) {
    return invalid("material_request_contract_date_invalid", `正式需求响应中的 ${name} 无效`);
  }
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
    return invalid("material_request_contract_date_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value;
}
function awareTimestamp(value: unknown, name: string): string {
  if (typeof value !== "string" || !AWARE_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) {
    return invalid("material_request_contract_timestamp_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value;
}
function nullableTimestamp(value: unknown, name: string): string | null {
  return value === null ? null : awareTimestamp(value, name);
}

function maskedText(value: unknown, name: string): string {
  const checked = text(value, name);
  if (!/^(?:[^\s＊*•][＊*•]+|[＊*•]+)$/u.test(checked)) {
    return invalid("material_request_contract_mask_invalid", `正式需求响应中的 ${name} 脱敏形状无效`);
  }
  return checked;
}

function validateAddressSnapshot(value: unknown): MaterialRequestAddressSnapshot {
  const object = record(value, "脱敏收货地址快照");
  exactKeys(
    object,
    ["province_code", "province_name", "city_name", "district_name", "detail_masked"],
    "脱敏收货地址快照",
  );
  return {
    province_code: text(field(object, "province_code"), "province_code"),
    province_name: text(field(object, "province_name"), "province_name"),
    city_name: text(field(object, "city_name"), "city_name"),
    district_name: text(field(object, "district_name"), "district_name"),
    detail_masked: field(object, "detail_masked") === "******"
      ? "******"
      : invalid("material_request_contract_mask_invalid", "正式需求地址必须使用固定脱敏投影"),
  };
}

function validateContactMasked(value: unknown): MaterialRequestContactMasked {
  const object = record(value, "脱敏联系人");
  exactKeys(object, ["name_masked", "mobile_masked"], "脱敏联系人");
  const mobile = text(field(object, "mobile_masked"), "mobile_masked");
  if (!/^[＊*•]{2,20}\d{4}$/.test(mobile)) {
    invalid("material_request_contract_mask_invalid", "脱敏手机号形状无效");
  }
  return {
    name_masked: maskedText(field(object, "name_masked"), "name_masked"),
    mobile_masked: mobile,
  };
}

function validateAttachmentRef(value: unknown): MaterialRequestAttachmentRef {
  const object = record(value, "需求附件引用");
  exactKeys(object, [
    "revision_id", "revision_no", "request_line_id", "file_id", "display_name", "purpose",
  ], "需求附件引用");
  const purpose = exactEnum(
    field(object, "purpose"), ["request_attachment", "request_line_attachment"] as const, "附件用途",
  );
  const requestLineId = nullableUuid(field(object, "request_line_id"), "attachment.request_line_id");
  if ((purpose === "request_attachment") !== (requestLineId === null)) {
    invalid("material_request_contract_attachment_scope_invalid", "附件用途与需求明细锚点不一致");
  }
  return {
    revision_id: uuid(field(object, "revision_id"), "attachment.revision_id"),
    revision_no: positiveInteger(field(object, "revision_no"), "attachment.revision_no"),
    request_line_id: requestLineId,
    file_id: uuid(field(object, "file_id"), "attachment.file_id"),
    display_name: text(field(object, "display_name"), "attachment.display_name"),
    purpose,
  };
}
function nonnegativeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    return invalid("material_request_contract_version_invalid", `正式需求响应中的 ${name} 无效`);
  }
  return value as number;
}
function positiveInteger(value: unknown, name: string): number {
  const checked = nonnegativeInteger(value, name);
  if (checked === 0) invalid("material_request_contract_integer_invalid", `${name}必须大于零`);
  return checked;
}
function decimal(value: unknown, name: string, positive = false): string {
  if (typeof value !== "string" || !DECIMAL_18_3.test(value)) {
    return invalid("material_request_contract_decimal_invalid", `${name}必须是 Decimal(18,3) 精确字符串`);
  }
  if (positive && value === "0.000") {
    invalid("material_request_contract_decimal_invalid", `${name}必须大于零`);
  }
  return value;
}
function decimalUnits(value: string): bigint {
  return BigInt(value.replace(".", ""));
}
function requireNotGreater(left: string, right: string, message: string): void {
  if (decimalUnits(left) > decimalUnits(right)) {
    invalid("material_request_contract_quantity_order_invalid", message);
  }
}
function currentStep(value: unknown): 1 | 2 | 3 | null {
  if (value === null) return null;
  if (value !== 1 && value !== 2 && value !== 3) {
    return invalid("material_request_contract_step_invalid", "current_step_no无效");
  }
  return value;
}

function draftText(value: unknown, name: string, minimum: number, maximum: number): string {
  if (
    typeof value !== "string"
    || value !== value.trim()
    || value.length < minimum
    || value.length > maximum
  ) {
    return invalid("material_request_draft_text_invalid", `正式需求草稿中的 ${name} 无效`);
  }
  return value;
}

function validateDraftAddress(value: unknown): MaterialRequestDraftAddress {
  const object = record(value, "正式需求草稿收货地址");
  exactKeys(
    object,
    ["province_code", "province_name", "city_name", "district_name", "detail"],
    "正式需求草稿收货地址",
  );
  return {
    province_code: draftText(field(object, "province_code"), "province_code", 1, 12),
    province_name: draftText(field(object, "province_name"), "province_name", 1, 80),
    city_name: draftText(field(object, "city_name"), "city_name", 1, 80),
    district_name: draftText(field(object, "district_name"), "district_name", 1, 80),
    detail: draftText(field(object, "detail"), "address.detail", 1, 500),
  };
}

function validateDraftContact(value: unknown): MaterialRequestDraftContact {
  const object = record(value, "正式需求草稿联系人");
  exactKeys(object, ["name", "mobile"], "正式需求草稿联系人");
  const mobile = draftText(field(object, "mobile"), "contact.mobile", 6, 32);
  if (!CONTACT_MOBILE.test(mobile)) {
    return invalid("material_request_draft_mobile_invalid", "正式需求草稿中的 contact.mobile 无效");
  }
  return {
    name: draftText(field(object, "name"), "contact.name", 1, 120),
    mobile,
  };
}

function validateDraftLine(value: unknown): MaterialRequestDraftLine {
  const object = record(value, "正式需求草稿明细");
  exactKeys(object, [
    "material_id", "requested_qty", "required_date", "suggested_substitute_material_id", "note",
  ], "正式需求草稿明细");
  const materialId = uuid(field(object, "material_id"), "material_id");
  const suggestedSubstituteMaterialId = nullableUuid(
    field(object, "suggested_substitute_material_id"), "suggested_substitute_material_id",
  );
  if (suggestedSubstituteMaterialId === materialId) {
    invalid("material_request_contract_substitute_invalid", "建议替代物料不能与申请物料相同");
  }
  return {
    material_id: materialId,
    requested_qty: decimal(field(object, "requested_qty"), "申请数量", true),
    required_date: dateOnly(field(object, "required_date"), "line.required_date"),
    suggested_substitute_material_id: suggestedSubstituteMaterialId,
    note: draftText(field(object, "note"), "line.note", 0, 2000),
  };
}

/**
 * Validates the exact backend MaterialRequestCreateIn/MaterialRequestDraftFields wire shape.
 * Raw address and contact values are returned only to the caller's in-memory write flow.
 */
export function validateMaterialRequestDraftInput(value: unknown): MaterialRequestDraftInput {
  const object = record(value, "正式需求草稿");
  exactKeys(object, [
    "work_order_id", "purpose", "urgency", "expected_date", "address", "contact",
    "attachment_file_ids", "note", "lines",
  ], "正式需求草稿");
  const rawAttachmentIds = field(object, "attachment_file_ids");
  if (!Array.isArray(rawAttachmentIds) || rawAttachmentIds.length > 20) {
    return invalid("material_request_draft_attachments_invalid", "正式需求草稿附件引用无效");
  }
  const attachmentFileIds = rawAttachmentIds.map((id) => uuid(id, "attachment_file_id"));
  if (new Set(attachmentFileIds).size !== attachmentFileIds.length) {
    invalid("material_request_draft_attachments_duplicate", "正式需求草稿附件引用不能重复");
  }
  const rawLines = field(object, "lines");
  if (!Array.isArray(rawLines) || rawLines.length < 1 || rawLines.length > 200) {
    return invalid("material_request_draft_lines_invalid", "正式需求草稿必须包含一至二百条明细");
  }
  const lines = rawLines.map(validateDraftLine);
  const dimensions = lines.map((line) => [
    line.material_id,
    line.required_date,
    line.suggested_substitute_material_id,
  ].join("|"));
  if (new Set(dimensions).size !== dimensions.length) {
    invalid("material_request_draft_lines_duplicate", "正式需求草稿包含重复明细维度");
  }
  return deepFreeze({
    work_order_id: nullableUuid(field(object, "work_order_id"), "work_order_id"),
    purpose: draftText(field(object, "purpose"), "purpose", 1, 4000),
    urgency: exactEnum(field(object, "urgency"), MATERIAL_REQUEST_URGENCIES, "紧急程度"),
    expected_date: dateOnly(field(object, "expected_date"), "expected_date"),
    address: validateDraftAddress(field(object, "address")),
    contact: validateDraftContact(field(object, "contact")),
    attachment_file_ids: attachmentFileIds,
    note: draftText(field(object, "note"), "note", 0, 10000),
    lines,
  });
}

export function validateMaterialRequestStateAxes(value: unknown): MaterialRequestStateAxes {
  const object = record(value, "正式需求十状态轴");
  exactKeys(object, STATE_AXIS_WIRE_KEYS, "正式需求十状态轴");
  return {
    request_status: exactEnum(field(object, "request_status"), MATERIAL_REQUEST_STATUSES, "申请状态"),
    allocation_status: exactEnum(field(object, "allocation_status"), ALLOCATION_STATUSES, "分配状态"),
    reservation_status: exactEnum(field(object, "reservation_status"), RESERVATION_STATUSES, "占用状态"),
    outbound_status: exactEnum(field(object, "outbound_status"), OUTBOUND_STATUSES, "出库状态"),
    shipment_status: exactEnum(field(object, "shipment_status"), SHIPMENT_STATUSES, "发运状态"),
    logistics_signature_status: exactEnum(
      field(object, "logistics_signature_status"), LOGISTICS_SIGNATURE_STATUSES, "物流签收状态",
    ),
    oam_receipt_status: exactEnum(field(object, "oam_receipt_status"), OAM_RECEIPT_STATUSES, "OAM收货状态"),
    personal_inbound_status: exactEnum(
      field(object, "personal_inbound_status"), PERSONAL_INBOUND_STATUSES, "个人仓入库状态",
    ),
    notification_status: exactEnum(field(object, "notification_status"), NOTIFICATION_STATUSES, "通知状态"),
    reconciliation_status: exactEnum(
      field(object, "reconciliation_status"), RECONCILIATION_STATUSES, "同步对账状态",
    ),
  };
}

function validateApprovalAssigneeSnapshot(value: unknown): MaterialRequestApprovalAssigneeSnapshot | null {
  if (value === null) return null;
  const object = record(value, "审批人脱敏快照");
  exactKeys(object, ["name_masked", "role_code"], "审批人脱敏快照");
  return {
    name_masked: maskedText(field(object, "name_masked"), "assignee.name_masked"),
    role_code: exactEnum(
      field(object, "role_code"), MATERIAL_REQUEST_APPROVAL_ASSIGNEE_ROLES, "审批角色",
    ),
  };
}

function validateCandidatePoolSummary(value: unknown): MaterialRequestApprovalCandidatePoolSummary | null {
  if (value === null) return null;
  const object = record(value, "审批候选池摘要");
  exactKeys(object, ["candidate_count", "candidate_kinds"], "审批候选池摘要");
  const rawKinds = field(object, "candidate_kinds");
  if (!Array.isArray(rawKinds) || rawKinds.length === 0) {
    return invalid("material_request_contract_candidate_pool_invalid", "审批候选池类型不能为空");
  }
  const kinds = rawKinds.map((item) => exactEnum(
    item, MATERIAL_REQUEST_APPROVAL_CANDIDATE_KINDS, "候选人类型",
  ));
  const canonicalKinds = MATERIAL_REQUEST_APPROVAL_CANDIDATE_KINDS.filter((item) => kinds.includes(item));
  if (new Set(kinds).size !== kinds.length || kinds.some((item, index) => item !== canonicalKinds[index])) {
    invalid("material_request_contract_candidate_pool_invalid", "审批候选池类型必须唯一且顺序固定");
  }
  return {
    candidate_count: positiveInteger(field(object, "candidate_count"), "candidate_count"),
    candidate_kinds: kinds,
  };
}

function validateApprovalLineDecision(value: unknown): MaterialRequestApprovalLineDecision {
  const object = record(value, "逐行审批事实");
  exactKeys(object, [
    "decision_id", "step_id", "request_revision_id", "revision_no", "request_line_id",
    "input_qty", "approved_qty", "rejected_qty", "reason", "decision_source",
    "external_registration_id", "decided_at",
  ], "逐行审批事实");
  const inputQty = decimal(field(object, "input_qty"), "审批输入数量", true);
  const approvedQty = decimal(field(object, "approved_qty"), "审批同意数量");
  const rejectedQty = decimal(field(object, "rejected_qty"), "审批拒绝数量");
  if (decimalUnits(approvedQty) + decimalUnits(rejectedQty) !== decimalUnits(inputQty)) {
    invalid("material_request_contract_decision_quantity_invalid", "逐行审批数量不守恒");
  }
  const reason = plainText(field(object, "reason"), "decision.reason");
  if (rejectedQty !== "0.000" && !reason) {
    invalid("material_request_contract_decision_reason_missing", "存在拒绝数量时必须填写理由");
  }
  const source = exactEnum(
    field(object, "decision_source"), MATERIAL_REQUEST_APPROVAL_SOURCE_MODES, "审批事实来源",
  );
  const externalRegistrationId = nullableUuid(
    field(object, "external_registration_id"), "external_registration_id",
  );
  if ((source === "external_registration") !== (externalRegistrationId !== null)) {
    invalid("material_request_contract_decision_source_invalid", "外部审批来源与证据登记锚点不一致");
  }
  return {
    decision_id: uuid(field(object, "decision_id"), "decision_id"),
    step_id: uuid(field(object, "step_id"), "decision.step_id"),
    request_revision_id: uuid(field(object, "request_revision_id"), "decision.request_revision_id"),
    revision_no: positiveInteger(field(object, "revision_no"), "decision.revision_no"),
    request_line_id: uuid(field(object, "request_line_id"), "decision.request_line_id"),
    input_qty: inputQty,
    approved_qty: approvedQty,
    rejected_qty: rejectedQty,
    reason,
    decision_source: source,
    external_registration_id: externalRegistrationId,
    decided_at: awareTimestamp(field(object, "decided_at"), "decision.decided_at"),
  };
}

function validateExternalEvidenceSummary(value: unknown): MaterialRequestExternalEvidenceSummary {
  const object = record(value, "外部审批证据摘要");
  exactKeys(object, [
    "registration_id", "registration_no", "step_id", "external_action", "status",
    "evidence_file_id", "external_approver_name_masked", "external_decided_at",
    "registered_at", "verified_at", "version",
  ], "外部审批证据摘要");
  const status = exactEnum(
    field(object, "status"), ["pending_verification", "accepted", "rejected", "superseded"] as const,
    "外部证据状态",
  );
  const externalDecidedAt = awareTimestamp(field(object, "external_decided_at"), "external_decided_at");
  const registeredAt = awareTimestamp(field(object, "registered_at"), "registered_at");
  const verifiedAt = nullableTimestamp(field(object, "verified_at"), "verified_at");
  if (Date.parse(externalDecidedAt) > Date.parse(registeredAt)
      || (verifiedAt !== null && Date.parse(verifiedAt) < Date.parse(registeredAt))) {
    invalid("material_request_contract_timestamp_order_invalid", "外部审批证据时间顺序无效");
  }
  if ((status === "pending_verification") !== (verifiedAt === null)) {
    invalid("material_request_contract_external_verification_invalid", "外部证据状态与复核时间不一致");
  }
  return {
    registration_id: uuid(field(object, "registration_id"), "registration_id"),
    registration_no: text(field(object, "registration_no"), "registration_no"),
    step_id: uuid(field(object, "step_id"), "external_evidence.step_id"),
    external_action: exactEnum(
      field(object, "external_action"), ["approve", "partial_approve", "reject", "return"] as const,
      "外部审批动作",
    ),
    status,
    evidence_file_id: uuid(field(object, "evidence_file_id"), "evidence_file_id"),
    external_approver_name_masked: maskedText(
      field(object, "external_approver_name_masked"), "external_approver_name_masked",
    ),
    external_decided_at: externalDecidedAt,
    registered_at: registeredAt,
    verified_at: verifiedAt,
    version: nonnegativeInteger(field(object, "version"), "external_evidence.version"),
  };
}

function validateReturnLineFact(value: unknown): MaterialRequestReturnLineFact {
  const object = record(value, "审批退回逐行事实");
  exactKeys(object, [
    "return_fact_id", "return_action_id", "instance_id", "returned_from_step_id",
    "target_kind", "target_step_id", "request_revision_id", "revision_no", "request_line_id",
    "returned_step_input_qty", "target_step_max_qty", "required_review_qty", "reason", "occurred_at",
  ], "审批退回逐行事实");
  const targetKind = exactEnum(
    field(object, "target_kind"), ["requester_revision", "approval_step"] as const, "退回目标",
  );
  const targetStepId = nullableUuid(field(object, "target_step_id"), "return.target_step_id");
  if ((targetKind === "requester_revision") !== (targetStepId === null)) {
    invalid("material_request_contract_return_target_invalid", "退回目标类型与步骤锚点不一致");
  }
  const targetMaxQty = decimal(field(object, "target_step_max_qty"), "目标步骤最大数量", true);
  const requiredReviewQty = decimal(field(object, "required_review_qty"), "必须复核数量", true);
  requireNotGreater(requiredReviewQty, targetMaxQty, "必须复核数量不能超过目标步骤最大数量");
  return {
    return_fact_id: uuid(field(object, "return_fact_id"), "return_fact_id"),
    return_action_id: uuid(field(object, "return_action_id"), "return_action_id"),
    instance_id: uuid(field(object, "instance_id"), "return.instance_id"),
    returned_from_step_id: uuid(field(object, "returned_from_step_id"), "returned_from_step_id"),
    target_kind: targetKind,
    target_step_id: targetStepId,
    request_revision_id: uuid(field(object, "request_revision_id"), "return.request_revision_id"),
    revision_no: positiveInteger(field(object, "revision_no"), "return.revision_no"),
    request_line_id: uuid(field(object, "request_line_id"), "return.request_line_id"),
    returned_step_input_qty: decimal(
      field(object, "returned_step_input_qty"), "退回步骤输入数量", true,
    ),
    target_step_max_qty: targetMaxQty,
    required_review_qty: requiredReviewQty,
    reason: text(field(object, "reason"), "return.reason"),
    occurred_at: awareTimestamp(field(object, "occurred_at"), "return.occurred_at"),
  };
}

function validateApprovalStep(value: unknown): MaterialRequestApprovalStep {
  const object = record(value, "正式需求审批步骤");
  exactKeys(object, [
    "step_id", "step_no", "attempt_no", "predecessor_step_id", "supersedes_step_id",
    "reopened_from_step_id", "source_mode", "status", "assignee_snapshot",
    "candidate_pool_summary", "opened_at", "decided_at", "version", "line_decisions",
  ], "正式需求审批步骤");
  const rawStepNo = positiveInteger(field(object, "step_no"), "step_no");
  if (rawStepNo > 3) invalid("material_request_contract_step_invalid", "step_no无效");
  const stepNo = rawStepNo as 1 | 2 | 3;
  const attemptNo = positiveInteger(field(object, "attempt_no"), "step.attempt_no");
  const predecessorStepId = nullableUuid(field(object, "predecessor_step_id"), "predecessor_step_id");
  const supersedesStepId = nullableUuid(field(object, "supersedes_step_id"), "supersedes_step_id");
  const reopenedFromStepId = nullableUuid(field(object, "reopened_from_step_id"), "reopened_from_step_id");
  if ((stepNo === 1) !== (predecessorStepId === null)) {
    invalid("material_request_contract_step_causality_invalid", "审批前序步骤锚点无效");
  }
  if ((attemptNo === 1) !== (supersedesStepId === null)) {
    invalid("material_request_contract_step_causality_invalid", "审批重审步骤锚点无效");
  }
  const sourceMode = exactEnum(
    field(object, "source_mode"), MATERIAL_REQUEST_APPROVAL_SOURCE_MODES, "审批来源模式",
  );
  const status = exactEnum(
    field(object, "status"), MATERIAL_REQUEST_APPROVAL_STEP_STATUSES, "审批步骤状态",
  );
  const assignee = validateApprovalAssigneeSnapshot(field(object, "assignee_snapshot"));
  const candidatePool = validateCandidatePoolSummary(field(object, "candidate_pool_summary"));
  if (sourceMode === "internal" && assignee === null && candidatePool === null) {
    invalid("material_request_contract_assignee_invalid", "内部审批步骤缺少审批人或候选池摘要");
  }
  if (sourceMode !== "internal" && assignee !== null) {
    invalid("material_request_contract_assignee_invalid", "外部登记步骤不能暴露内部审批人");
  }
  if (sourceMode === "external_registration"
      && (candidatePool === null
        || candidatePool.candidate_kinds.join("|") !== "registrar|verifier")) {
    invalid("material_request_contract_candidate_pool_invalid", "外部登记步骤缺少登记人和复核人候选池");
  }
  const openedAt = nullableTimestamp(field(object, "opened_at"), "step.opened_at");
  const decidedAt = nullableTimestamp(field(object, "decided_at"), "step.decided_at");
  const decidedStatuses: readonly MaterialRequestApprovalStepStatus[] = [
    "approved", "partially_approved", "rejected", "returned",
  ];
  if (decidedStatuses.includes(status) !== (decidedAt !== null)) {
    invalid("material_request_contract_step_time_invalid", "审批步骤状态与决定时间不一致");
  }
  const requiresOpened: readonly MaterialRequestApprovalStepStatus[] = [
    "open", "awaiting_external_evidence", "evidence_pending_verification", ...decidedStatuses,
  ];
  if (requiresOpened.includes(status) && openedAt === null) {
    invalid("material_request_contract_step_time_invalid", "已打开或已决定步骤缺少打开时间");
  }
  if (status === "pending" && (openedAt !== null || decidedAt !== null)) {
    invalid("material_request_contract_step_time_invalid", "待处理步骤不能包含操作时间");
  }
  if (["cancelled", "superseded"].includes(status) && decidedAt !== null) {
    invalid("material_request_contract_step_time_invalid", "取消或被替代步骤不能伪造决定时间");
  }
  if (openedAt !== null && decidedAt !== null && Date.parse(decidedAt) < Date.parse(openedAt)) {
    invalid("material_request_contract_timestamp_order_invalid", "审批决定时间不能早于打开时间");
  }
  const rawDecisions = field(object, "line_decisions");
  if (!Array.isArray(rawDecisions)) {
    return invalid("material_request_contract_decisions_invalid", "逐行审批事实必须是数组");
  }
  const decisions = rawDecisions.map(validateApprovalLineDecision);
  const stepId = uuid(field(object, "step_id"), "step_id");
  if (new Set(decisions.map((item) => item.decision_id)).size !== decisions.length
      || new Set(decisions.map((item) => item.request_line_id)).size !== decisions.length
      || decisions.some((item) => item.step_id !== stepId)) {
    invalid("material_request_contract_decisions_invalid", "逐行审批事实重复或步骤锚点不一致");
  }
  if (["approved", "partially_approved", "rejected"].includes(status) && decisions.length === 0) {
    invalid("material_request_contract_decisions_invalid", "已决定审批步骤必须包含逐行事实");
  }
  return {
    step_id: stepId,
    step_no: stepNo,
    attempt_no: attemptNo,
    predecessor_step_id: predecessorStepId,
    supersedes_step_id: supersedesStepId,
    reopened_from_step_id: reopenedFromStepId,
    source_mode: sourceMode,
    status,
    assignee_snapshot: assignee,
    candidate_pool_summary: candidatePool,
    opened_at: openedAt,
    decided_at: decidedAt,
    version: nonnegativeInteger(field(object, "version"), "step.version"),
    line_decisions: decisions,
  };
}

function validateApprovalInstance(value: unknown): MaterialRequestApprovalInstance | null {
  if (value === null) return null;
  const object = record(value, "正式需求审批实例");
  exactKeys(object, [
    "instance_id", "request_revision_id", "revision_no", "attempt_no", "status",
    "current_step_no", "current_step_id", "version", "steps",
    "external_evidence_summaries", "return_line_facts",
  ], "正式需求审批实例");
  const rawSteps = field(object, "steps");
  if (!Array.isArray(rawSteps) || rawSteps.length < 3) {
    return invalid("material_request_contract_approval_steps_invalid", "审批步骤历史至少包含三级初始步骤");
  }
  const steps = rawSteps.map(validateApprovalStep);
  const coordinates = steps.map((step) => `${step.step_no}:${step.attempt_no}`);
  const sortedCoordinates = [...coordinates].sort((left, right) => {
    const [leftStep, leftAttempt] = left.split(":").map(Number);
    const [rightStep, rightAttempt] = right.split(":").map(Number);
    return leftStep - rightStep || leftAttempt - rightAttempt;
  });
  if (coordinates.some((item, index) => item !== sortedCoordinates[index])
      || new Set(coordinates).size !== coordinates.length
      || new Set(steps.map((step) => step.step_id)).size !== steps.length) {
    invalid("material_request_contract_approval_steps_invalid", "审批步骤历史坐标重复或顺序不固定");
  }
  const expectedSources: Record<1 | 2 | 3, MaterialRequestApprovalSourceMode> = {
    1: "internal", 2: "internal", 3: "external_registration",
  };
  if (steps.some((step) => step.source_mode !== expectedSources[step.step_no])) {
    invalid("material_request_contract_approval_route_invalid", "一期审批路由必须为两级内部审批加外部登记");
  }
  const byId = new Map(steps.map((step) => [step.step_id, step]));
  for (const stepNo of [1, 2, 3] as const) {
    const attempts = steps.filter((step) => step.step_no === stepNo);
    if (attempts.some((step, index) => step.attempt_no !== index + 1
        || step.supersedes_step_id !== (index === 0 ? null : attempts[index - 1].step_id))) {
      invalid("material_request_contract_step_causality_invalid", "审批重审链不连续");
    }
  }
  for (const step of steps) {
    if (step.predecessor_step_id !== null) {
      const predecessor = byId.get(step.predecessor_step_id);
      if (!predecessor || predecessor.step_no !== step.step_no - 1) {
        invalid("material_request_contract_step_causality_invalid", "审批前序不是紧邻下一级");
      }
    }
    if (step.reopened_from_step_id !== null) {
      const returning = byId.get(step.reopened_from_step_id);
      if (!returning || returning.step_no !== step.step_no + 1 || returning.status !== "returned") {
        invalid("material_request_contract_step_causality_invalid", "重开步骤缺少上一级退回因果");
      }
    }
  }
  const currentStepNo = currentStep(field(object, "current_step_no"));
  const currentStepId = nullableUuid(field(object, "current_step_id"), "current_step_id");
  const status = exactEnum(field(object, "status"), MATERIAL_REQUEST_APPROVAL_INSTANCE_STATUSES, "审批实例状态");
  if (status === "active" && (currentStepNo === null || currentStepId === null)) {
    invalid("material_request_contract_step_mismatch", "活动审批实例必须精确指向当前步骤");
  }
  if (["returned", "completed", "rejected", "withdrawn", "cancelled", "superseded"].includes(status)
      && (currentStepNo !== null || currentStepId !== null)) {
    invalid("material_request_contract_step_mismatch", "非活动审批实例不能指向当前步骤");
  }
  if (currentStepId !== null) {
    const current = byId.get(currentStepId);
    if (!current || current.step_no !== currentStepNo
        || !["open", "awaiting_external_evidence", "evidence_pending_verification"].includes(current.status)) {
      invalid("material_request_contract_step_mismatch", "当前步骤 ID 未命中唯一可处理步骤");
    }
  }
  const instanceId = uuid(field(object, "instance_id"), "instance_id");
  const requestRevisionId = uuid(field(object, "request_revision_id"), "instance.request_revision_id");
  const revisionNo = positiveInteger(field(object, "revision_no"), "instance.revision_no");
  const rawEvidence = field(object, "external_evidence_summaries");
  const evidence = rawEvidence === null ? null : (() => {
    if (!Array.isArray(rawEvidence)) {
      return invalid("material_request_contract_external_evidence_invalid", "外部证据可见投影必须是数组或null");
    }
    const parsed = rawEvidence.map(validateExternalEvidenceSummary);
    if (new Set(parsed.map((item) => item.registration_id)).size !== parsed.length
        || new Set(parsed.map((item) => item.registration_no)).size !== parsed.length
        || parsed.some((item) => byId.get(item.step_id)?.source_mode !== "external_registration")) {
      invalid("material_request_contract_external_evidence_invalid", "外部证据重复或步骤锚点不一致");
    }
    return parsed;
  })();
  const rawFacts = field(object, "return_line_facts");
  const facts = rawFacts === null ? null : (() => {
    if (!Array.isArray(rawFacts)) {
      return invalid("material_request_contract_return_facts_invalid", "退回事实可见投影必须是数组或null");
    }
    const parsed = rawFacts.map(validateReturnLineFact);
    if (new Set(parsed.map((item) => item.return_fact_id)).size !== parsed.length
        || new Set(parsed.map((item) => `${item.return_action_id}:${item.request_line_id}`)).size !== parsed.length) {
      invalid("material_request_contract_return_facts_invalid", "退回逐行事实重复");
    }
    for (const fact of parsed) {
      const source = byId.get(fact.returned_from_step_id);
      const target = fact.target_step_id === null ? null : byId.get(fact.target_step_id);
      if (fact.instance_id !== instanceId || fact.request_revision_id !== requestRevisionId
          || fact.revision_no !== revisionNo || !source || source.status !== "returned") {
        invalid("material_request_contract_return_facts_invalid", "退回事实与审批实例锚点不一致");
      }
      if (fact.target_kind === "requester_revision" && source.step_no !== 1) {
        invalid("material_request_contract_return_facts_invalid", "仅一级退回可指向申请人修订");
      }
      if (fact.target_kind === "approval_step"
          && (!target || target.step_no !== source.step_no - 1
            || target.reopened_from_step_id !== source.step_id)) {
        invalid("material_request_contract_return_facts_invalid", "退回目标缺少重开步骤因果");
      }
    }
    const returnedStepIds = new Set(
      steps.filter((step) => step.status === "returned").map((step) => step.step_id),
    );
    const factStepIds = new Set(parsed.map((fact) => fact.returned_from_step_id));
    if (returnedStepIds.size !== factStepIds.size
        || [...returnedStepIds].some((stepId) => !factStepIds.has(stepId))) {
      invalid("material_request_contract_return_facts_invalid", "可见退回事实必须覆盖每个退回步骤");
    }
    return parsed;
  })();
  if (status === "returned" && !steps.some((step) => step.status === "returned")) {
    invalid("material_request_contract_return_facts_invalid", "退回审批实例缺少退回步骤");
  }
  return {
    instance_id: instanceId,
    request_revision_id: requestRevisionId,
    revision_no: revisionNo,
    attempt_no: positiveInteger(field(object, "attempt_no"), "instance.attempt_no"),
    status,
    current_step_no: currentStepNo,
    current_step_id: currentStepId,
    version: nonnegativeInteger(field(object, "version"), "approval_instance.version"),
    steps,
    external_evidence_summaries: evidence,
    return_line_facts: facts,
  };
}

function validateRevisionSummary(value: unknown): MaterialRequestRevisionSummary {
  const object = record(value, "需求修订摘要");
  exactKeys(object, [
    "revision_id", "revision_no", "previous_revision_id", "status", "line_count",
    "attachment_count", "sealed_at", "created_at",
  ], "需求修订摘要");
  const status = exactEnum(field(object, "status"), ["draft", "sealed"] as const, "修订状态");
  const sealedAt = nullableTimestamp(field(object, "sealed_at"), "revision.sealed_at");
  const createdAt = awareTimestamp(field(object, "created_at"), "revision.created_at");
  if ((status === "sealed") !== (sealedAt !== null)) {
    invalid("material_request_contract_revision_state_invalid", "修订状态与封存时间不一致");
  }
  if (sealedAt !== null && Date.parse(sealedAt) < Date.parse(createdAt)) {
    invalid("material_request_contract_timestamp_order_invalid", "修订封存时间不能早于创建时间");
  }
  return {
    revision_id: uuid(field(object, "revision_id"), "revision_id"),
    revision_no: positiveInteger(field(object, "revision_no"), "revision_no"),
    previous_revision_id: nullableUuid(field(object, "previous_revision_id"), "previous_revision_id"),
    status,
    line_count: positiveInteger(field(object, "line_count"), "revision.line_count"),
    attachment_count: nonnegativeInteger(field(object, "attachment_count"), "revision.attachment_count"),
    sealed_at: sealedAt,
    created_at: createdAt,
  };
}

function validateLine(value: unknown): MaterialRequestLine {
  const object = record(value, "正式需求明细");
  exactKeys(object, [
    "request_line_id", "revision_id", "revision_no", "line_no", "material_id", "requested_qty", "required_date",
    "suggested_substitute_material_id", "note", "final_approved_qty", "cancelled_qty", "status",
    "version",
  ], "正式需求明细");
  const requestedQty = decimal(field(object, "requested_qty"), "申请数量", true);
  const finalApprovedQty = decimal(field(object, "final_approved_qty"), "最终批准数量");
  const cancelledQty = decimal(field(object, "cancelled_qty"), "取消数量");
  const materialId = uuid(field(object, "material_id"), "material_id");
  const substituteMaterialId = nullableUuid(
    field(object, "suggested_substitute_material_id"), "suggested_substitute_material_id",
  );
  requireNotGreater(finalApprovedQty, requestedQty, "最终批准数量不能超过申请数量");
  requireNotGreater(cancelledQty, finalApprovedQty, "取消数量不能超过最终批准数量");
  if (substituteMaterialId === materialId) {
    invalid("material_request_contract_substitute_invalid", "建议替代物料不能与申请物料相同");
  }
  return {
    request_line_id: uuid(field(object, "request_line_id"), "request_line_id"),
    revision_id: uuid(field(object, "revision_id"), "line.revision_id"),
    revision_no: positiveInteger(field(object, "revision_no"), "line.revision_no"),
    line_no: positiveInteger(field(object, "line_no"), "line_no"),
    material_id: materialId,
    requested_qty: requestedQty,
    required_date: dateOnly(field(object, "required_date"), "line.required_date"),
    suggested_substitute_material_id: substituteMaterialId,
    note: plainText(field(object, "note"), "line.note"),
    final_approved_qty: finalApprovedQty,
    cancelled_qty: cancelledQty,
    status: exactEnum(field(object, "status"), MATERIAL_REQUEST_LINE_STATUSES, "明细状态"),
    version: nonnegativeInteger(field(object, "version"), "line.version"),
  };
}

function validateSupplyTaskAllowedActions(
  value: unknown,
  status: MaterialRequestSupplyTask["status"],
): MaterialRequestSupplyTaskAllowedAction[] {
  if (!Array.isArray(value)) {
    return invalid("material_request_contract_actions_invalid", "供给任务allowed_actions必须是数组");
  }
  const actions = value.map((action) => exactEnum(
    action, MATERIAL_REQUEST_SUPPLY_TASK_ALLOWED_ACTIONS, "供给任务allowed_action",
  ));
  if (new Set(actions).size !== actions.length) {
    invalid("material_request_contract_actions_duplicate", "供给任务allowed_actions不能重复");
  }
  if (["cancelled", "closed_no_supply"].includes(status) && actions.length > 0) {
    invalid("material_request_contract_action_state_mismatch", "已关闭供给任务不能包含可执行动作");
  }
  return actions;
}

function validateSupplyTask(value: unknown): MaterialRequestSupplyTask {
  const object = record(value, "正式需求供给任务");
  exactKeys(object, [
    "id", "task_no", "request_line_id", "substitution_decision_id", "supply_type",
    "reference_no", "expected_qty", "original_equivalent_qty", "expected_date", "status",
    "version", "created_at", "updated_at", "allowed_actions",
  ], "正式需求供给任务");
  const status = exactEnum(field(object, "status"), MATERIAL_REQUEST_SUPPLY_TASK_STATUSES, "供给任务状态");
  const referenceNo = nullableText(field(object, "reference_no"), "reference_no");
  if (status === "reference_registered" && referenceNo === null) {
    invalid("material_request_contract_supply_reference_missing", "已登记参考的供给任务必须包含参考编号");
  }
  const createdAt = awareTimestamp(field(object, "created_at"), "created_at");
  const updatedAt = awareTimestamp(field(object, "updated_at"), "updated_at");
  if (Date.parse(updatedAt) < Date.parse(createdAt)) {
    invalid("material_request_contract_timestamp_order_invalid", "供给任务更新时间不能早于创建时间");
  }
  const substitution = field(object, "substitution_decision_id");
  return {
    id: uuid(field(object, "id"), "supply_task.id"),
    task_no: text(field(object, "task_no"), "task_no"),
    request_line_id: uuid(field(object, "request_line_id"), "supply_task.request_line_id"),
    substitution_decision_id: substitution === null
      ? null
      : uuid(substitution, "substitution_decision_id"),
    supply_type: exactEnum(field(object, "supply_type"), MATERIAL_REQUEST_SUPPLY_TYPES, "供给类型"),
    reference_no: referenceNo,
    expected_qty: decimal(field(object, "expected_qty"), "供给计划数量", true),
    original_equivalent_qty: decimal(
      field(object, "original_equivalent_qty"), "原物料等价数量", true,
    ),
    expected_date: dateOnly(field(object, "expected_date"), "expected_date"),
    status,
    version: nonnegativeInteger(field(object, "version"), "supply_task.version"),
    created_at: createdAt,
    updated_at: updatedAt,
    allowed_actions: validateSupplyTaskAllowedActions(field(object, "allowed_actions"), status),
  };
}

function validateAllowedActions(value: unknown): MaterialRequestAllowedAction[] {
  if (!Array.isArray(value)) {
    return invalid("material_request_contract_actions_invalid", "allowed_actions必须是数组");
  }
  const actions = value.map((action) => exactEnum(action, MATERIAL_REQUEST_ALLOWED_ACTIONS, "allowed_action"));
  if (new Set(actions).size !== actions.length) {
    invalid("material_request_contract_actions_duplicate", "allowed_actions不能重复");
  }
  return actions;
}
function currentApprovalStep(instance: MaterialRequestApprovalInstance | null): MaterialRequestApprovalStep | null {
  if (!instance?.current_step_id) return null;
  return instance.steps.find((step) => step.step_id === instance.current_step_id) || null;
}
function validateApprovalPhaseOne(
  requestStatus: MaterialRequestStatus,
  mode: MaterialRequestApprovalMode,
  instance: MaterialRequestApprovalInstance | null,
): void {
  if (mode !== MATERIAL_REQUEST_PHASE_ONE_APPROVAL_MODE) {
    invalid("material_request_contract_approval_mode_unsupported", "一期仅支持外部审批登记模式");
  }
  if (instance && instance.steps.some((step) => step.source_mode !== (
    step.step_no === 3 ? "external_registration" : "internal"
  ))) {
    invalid("material_request_contract_approval_route_invalid", "一期审批路由必须为两级内部审批加外部登记");
  }
  if (requestStatus === "draft" && instance !== null) {
    invalid("material_request_contract_approval_instance_unexpected", "草稿不能包含审批实例");
  }
  if (requestStatus === "approval_in_progress" && instance?.status !== "active") {
    invalid("material_request_contract_approval_instance_mismatch", "审批中需求必须包含活动审批实例");
  }
  const expectedInstanceStatus: Partial<Record<MaterialRequestStatus, MaterialRequestApprovalInstanceStatus>> = {
    submitted: "active",
    returned: "returned",
    partially_approved: "completed",
    approved: "completed",
    rejected: "rejected",
    withdrawn: "withdrawn",
    cancellation_pending: "completed",
  };
  const expected = expectedInstanceStatus[requestStatus];
  if (expected && instance?.status !== expected) {
    invalid("material_request_contract_approval_instance_mismatch", "需求状态与审批实例状态不一致");
  }
  if (requestStatus === "cancelled"
      && (instance === null || !["returned", "completed"].includes(instance.status))) {
    invalid(
      "material_request_contract_approval_instance_mismatch",
      "已取消需求必须保留原有退回或已完成审批实例",
    );
  }
  if (requestStatus === "withdrawn" && instance?.steps.some((step) => [
    "pending", "open", "awaiting_external_evidence", "evidence_pending_verification",
  ].includes(step.status))) {
    invalid(
      "material_request_contract_approval_instance_mismatch",
      "已撤回需求不能保留活动审批步骤",
    );
  }
}

function validateActionCompatibility(
  actions: readonly MaterialRequestAllowedAction[],
  status: MaterialRequestStatus,
  instance: MaterialRequestApprovalInstance | null,
): void {
  const step = currentApprovalStep(instance);
  const requestStatusActions: Partial<Record<MaterialRequestAllowedAction, readonly MaterialRequestStatus[]>> = {
    update: ["draft", "returned"],
    submit: ["draft", "returned"],
    withdraw: ["submitted", "approval_in_progress"],
    cancel: ["returned", "partially_approved", "approved", "cancellation_pending"],
    propose_substitution: ["partially_approved", "approved"],
    confirm_substitution: ["partially_approved", "approved"],
    reject_substitution: ["partially_approved", "approved"],
    create_supply_task: ["partially_approved", "approved"],
  };
  for (const action of actions) {
    const statuses = requestStatusActions[action];
    if (statuses && !statuses.includes(status)) {
      invalid("material_request_contract_action_state_mismatch", `${action}与当前申请状态不一致`);
    }
    if (["approve", "return", "reject"].includes(action)
        && (step?.status !== "open" || step.source_mode !== "internal")) {
      invalid("material_request_contract_action_state_mismatch", `${action}与当前内部审批步骤不一致`);
    }
    if (action === "register_external_approval"
        && (step?.status !== "awaiting_external_evidence" || step.source_mode !== "external_registration")) {
      invalid("material_request_contract_action_state_mismatch", "外部审批登记动作与当前步骤不一致");
    }
    if (action === "verify_external_approval"
        && (step?.status !== "evidence_pending_verification" || step.source_mode !== "external_registration")) {
      invalid("material_request_contract_action_state_mismatch", "外部审批复核动作与当前步骤不一致");
    }
  }
}

const DETAIL_KEYS = [
  "schema_version", "request_id", "request_no", "request_version", "current_revision_id",
  "current_revision_no", "work_order_id",
  "requester_person_id", "requester_org_id", "purpose", "urgency", "expected_date",
  "address_snapshot", "contact_masked", "note", "attachment_refs", "approval_mode", "states",
  "approval_instance", "lines", "revision_history", "approval_history", "supply_tasks", "allowed_actions", "created_at", "updated_at",
  "submitted_at",
] as const;
const SUMMARY_KEYS = [
  "request_id", "request_no", "request_version", "current_revision_id", "current_revision_no",
  "work_order_id", "requester_person_id",
  "requester_org_id", "purpose", "urgency", "expected_date", "address_snapshot", "contact_masked",
  "note", "attachment_refs", "approval_mode", "states", "approval_instance", "line_count",
  "allowed_actions", "created_at", "updated_at", "submitted_at",
] as const;

function validateSummaryFields(object: Record<string, unknown>) {
  const mode = exactEnum(field(object, "approval_mode"), MATERIAL_REQUEST_APPROVAL_MODES, "approval_mode");
  const states = validateMaterialRequestStateAxes(field(object, "states"));
  const approvalInstance = validateApprovalInstance(field(object, "approval_instance"));
  validateApprovalPhaseOne(states.request_status, mode, approvalInstance);
  const actions = validateAllowedActions(field(object, "allowed_actions"));
  validateActionCompatibility(actions, states.request_status, approvalInstance);
  const rawAttachmentRefs = field(object, "attachment_refs");
  if (!Array.isArray(rawAttachmentRefs)) {
    return invalid("material_request_contract_attachments_invalid", "需求附件引用必须是数组");
  }
  const attachmentRefs = rawAttachmentRefs.map(validateAttachmentRef);
  if (new Set(attachmentRefs.map((attachment) => attachment.file_id)).size !== attachmentRefs.length) {
    invalid("material_request_contract_attachments_duplicate", "需求附件引用不能重复");
  }
  const currentRevisionId = uuid(field(object, "current_revision_id"), "current_revision_id");
  const currentRevisionNo = positiveInteger(field(object, "current_revision_no"), "current_revision_no");
  if (attachmentRefs.some((attachment) => attachment.revision_id !== currentRevisionId
      || attachment.revision_no !== currentRevisionNo)) {
    invalid("material_request_contract_revision_anchor_invalid", "附件未锚定当前修订");
  }
  const createdAt = awareTimestamp(field(object, "created_at"), "created_at");
  const updatedAt = awareTimestamp(field(object, "updated_at"), "updated_at");
  const submittedAt = nullableTimestamp(field(object, "submitted_at"), "submitted_at");
  if (Date.parse(updatedAt) < Date.parse(createdAt)
      || (submittedAt !== null && Date.parse(submittedAt) < Date.parse(createdAt))) {
    invalid("material_request_contract_timestamp_order_invalid", "需求时间字段顺序无效");
  }
  if ((states.request_status === "draft") !== (submittedAt === null)) {
    invalid("material_request_contract_submission_state_invalid", "需求状态与提交时间不一致");
  }
  return {
    request_id: uuid(field(object, "request_id"), "request_id"),
    request_no: text(field(object, "request_no"), "request_no"),
    request_version: nonnegativeInteger(field(object, "request_version"), "request_version"),
    current_revision_id: currentRevisionId,
    current_revision_no: currentRevisionNo,
    work_order_id: nullableUuid(field(object, "work_order_id"), "work_order_id"),
    requester_person_id: uuid(field(object, "requester_person_id"), "requester_person_id"),
    requester_org_id: uuid(field(object, "requester_org_id"), "requester_org_id"),
    purpose: text(field(object, "purpose"), "purpose"),
    urgency: exactEnum(field(object, "urgency"), MATERIAL_REQUEST_URGENCIES, "紧急程度"),
    expected_date: dateOnly(field(object, "expected_date"), "expected_date"),
    address_snapshot: validateAddressSnapshot(field(object, "address_snapshot")),
    contact_masked: validateContactMasked(field(object, "contact_masked")),
    note: plainText(field(object, "note"), "note"),
    attachment_refs: attachmentRefs,
    approval_mode: mode,
    states,
    approval_instance: approvalInstance,
    allowed_actions: actions,
    created_at: createdAt,
    updated_at: updatedAt,
    submitted_at: submittedAt,
  } as const;
}

export function validateMaterialRequestDetail(value: unknown, expectedRequestId = ""): MaterialRequestDetail {
  const object = record(value, "正式需求详情");
  exactKeys(object, DETAIL_KEYS, "正式需求详情");
  if (field(object, "schema_version") !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return invalid("material_request_contract_version_unknown", "正式需求响应版本不受支持");
  }
  const summary = validateSummaryFields(object);
  if (expectedRequestId && summary.request_id !== uuid(expectedRequestId, "expected_request_id")) {
    invalid("material_request_contract_anchor_mismatch", "正式需求详情与请求目标不一致");
  }
  const rawLines = field(object, "lines");
  if (!Array.isArray(rawLines) || rawLines.length === 0) {
    return invalid("material_request_contract_lines_invalid", "正式需求必须包含至少一条明细");
  }
  const lines = rawLines.map(validateLine);
  if (new Set(lines.map((line) => line.request_line_id)).size !== lines.length
      || new Set(lines.map((line) => line.line_no)).size !== lines.length) {
    invalid("material_request_contract_lines_duplicate", "正式需求明细标识或行号重复");
  }
  if (lines.some((line) => line.revision_id !== summary.current_revision_id
      || line.revision_no !== summary.current_revision_no)) {
    invalid("material_request_contract_revision_anchor_invalid", "需求明细未锚定当前修订");
  }
  if (summary.states.request_status === "cancelled" && lines.some(
    (line) => line.status !== "cancelled" || line.cancelled_qty !== line.final_approved_qty,
  )) {
    invalid(
      "material_request_contract_cancellation_projection_invalid",
      "已取消需求的每条明细都必须完整取消",
    );
  }
  const rawRevisions = field(object, "revision_history");
  if (!Array.isArray(rawRevisions) || rawRevisions.length === 0) {
    return invalid("material_request_contract_revision_history_invalid", "修订历史不能为空");
  }
  const revisionHistory = rawRevisions.map(validateRevisionSummary);
  if (new Set(revisionHistory.map((item) => item.revision_id)).size !== revisionHistory.length
      || revisionHistory.some((item, index) => item.revision_no !== index + 1
        || item.previous_revision_id !== (index === 0 ? null : revisionHistory[index - 1].revision_id)
        || (index < revisionHistory.length - 1 && item.status !== "sealed"))) {
    invalid("material_request_contract_revision_history_invalid", "修订历史编号、链路或封存状态无效");
  }
  const currentRevision = revisionHistory[revisionHistory.length - 1];
  if (currentRevision.revision_id !== summary.current_revision_id
      || currentRevision.revision_no !== summary.current_revision_no
      || currentRevision.line_count !== lines.length
      || currentRevision.attachment_count !== summary.attachment_refs.length) {
    invalid("material_request_contract_revision_anchor_invalid", "当前修订与修订历史头不一致");
  }
  const rawApprovalHistory = field(object, "approval_history");
  if (!Array.isArray(rawApprovalHistory)) {
    return invalid("material_request_contract_approval_history_invalid", "审批实例历史必须是数组");
  }
  const approvalHistory = rawApprovalHistory.map((item) => {
    const parsed = validateApprovalInstance(item);
    if (parsed === null) {
      return invalid("material_request_contract_approval_history_invalid", "审批实例历史不能包含空值");
    }
    return parsed;
  });
  const instance = summary.approval_instance;
  if ((instance === null) !== (approvalHistory.length === 0)) {
    invalid("material_request_contract_approval_history_invalid", "审批快捷投影与完整历史不一致");
  }
  if (instance !== null
      && JSON.stringify(instance) !== JSON.stringify(approvalHistory[approvalHistory.length - 1])) {
    invalid("material_request_contract_approval_history_invalid", "审批快捷投影不是最近历史实例");
  }
  if (approvalHistory.some((item, index) => item.attempt_no !== index + 1)
      || new Set(approvalHistory.map((item) => item.instance_id)).size !== approvalHistory.length
      || approvalHistory.some((item, index) => index > 0
        && item.revision_no <= approvalHistory[index - 1].revision_no)
      || approvalHistory.slice(0, -1).some((item) => item.status === "active")) {
    invalid("material_request_contract_approval_history_invalid", "审批实例历史尝试、修订或顺序无效");
  }
  for (const historicalInstance of approvalHistory) {
    const instanceRevision = revisionHistory.find(
      (item) => item.revision_id === historicalInstance.request_revision_id,
    );
    if (!instanceRevision || instanceRevision.revision_no !== historicalInstance.revision_no
        || instanceRevision.status !== "sealed") {
      invalid("material_request_contract_revision_anchor_invalid", "审批实例未锚定已封存修订");
    }
    const visibleRegistrationIds = historicalInstance.external_evidence_summaries === null
      ? null
      : new Set(historicalInstance.external_evidence_summaries.map((item) => item.registration_id));
    for (const step of historicalInstance.steps) {
      for (const decision of step.line_decisions) {
        if (decision.request_revision_id !== historicalInstance.request_revision_id
            || decision.revision_no !== historicalInstance.revision_no
            || (visibleRegistrationIds !== null && decision.external_registration_id !== null
              && !visibleRegistrationIds.has(decision.external_registration_id))) {
          invalid("material_request_contract_revision_anchor_invalid", "逐行审批事实修订或外部证据锚点无效");
        }
      }
    }
    if (historicalInstance.request_revision_id === summary.current_revision_id) {
      const currentLineIds = new Set(lines.map((line) => line.request_line_id));
      if (historicalInstance.steps.some((step) => step.line_decisions.some(
        (decision) => !currentLineIds.has(decision.request_line_id),
      )) || historicalInstance.return_line_facts?.some(
        (fact) => !currentLineIds.has(fact.request_line_id),
      )) {
        invalid("material_request_contract_revision_anchor_invalid", "审批或退回事实未锚定需求明细");
      }
    }
  }
  const rawSupplyTasks = field(object, "supply_tasks");
  if (!Array.isArray(rawSupplyTasks)) {
    return invalid("material_request_contract_supply_tasks_invalid", "正式需求供给任务必须是数组");
  }
  const supplyTasks = rawSupplyTasks.map(validateSupplyTask);
  const lineIds = new Set(lines.map((line) => line.request_line_id));
  if (new Set(supplyTasks.map((task) => task.id)).size !== supplyTasks.length
      || new Set(supplyTasks.map((task) => task.task_no)).size !== supplyTasks.length) {
    invalid("material_request_contract_supply_tasks_duplicate", "正式需求包含重复供给任务");
  }
  if (supplyTasks.some((task) => !lineIds.has(task.request_line_id))) {
    invalid("material_request_contract_supply_task_anchor_mismatch", "供给任务不属于当前需求明细");
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    ...summary,
    lines,
    revision_history: revisionHistory,
    approval_history: approvalHistory,
    supply_tasks: supplyTasks,
  };
}

export function validateMaterialRequestPage(value: unknown): MaterialRequestPage {
  const object = record(value, "正式需求分页响应");
  exactKeys(object, ["schema_version", "items", "next_after_id"], "正式需求分页响应");
  if (field(object, "schema_version") !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return invalid("material_request_contract_version_unknown", "正式需求分页版本不受支持");
  }
  const rawItems = field(object, "items");
  if (!Array.isArray(rawItems)) {
    return invalid("material_request_contract_page_invalid", "正式需求分页items无效");
  }
  const items = rawItems.map((item) => {
    const source = record(item, "正式需求摘要");
    exactKeys(source, SUMMARY_KEYS, "正式需求摘要");
    return {
      ...validateSummaryFields(source),
      line_count: positiveInteger(field(source, "line_count"), "line_count"),
    };
  });
  if (new Set(items.map((item) => item.request_id)).size !== items.length) {
    invalid("material_request_contract_page_duplicate", "正式需求分页包含重复申请");
  }
  const next = field(object, "next_after_id");
  const nextAfterId = next === null ? null : uuid(next, "next_after_id");
  if (nextAfterId && items.some((item) => item.request_id === nextAfterId)) {
    invalid("material_request_contract_cursor_invalid", "正式需求分页游标指向当前页对象");
  }
  return { schema_version: MATERIAL_REQUEST_SCHEMA_VERSION, items, next_after_id: nextAfterId };
}

export function validateMaterialRequestMutationResult(
  value: unknown,
  expected: Readonly<{ requestId: string; action: MaterialRequestMutationAction; previousVersion: number }>,
): MaterialRequestMutationResult {
  const object = record(value, "正式需求写响应");
  exactKeys(object, [
    "schema_version", "request_id", "action", "request_version", "revision_id", "revision_no",
    "approval_instance_id", "approval_attempt_no", "current_step_id", "states", "idempotency_replayed",
  ], "正式需求写响应");
  if (field(object, "schema_version") !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return invalid("material_request_contract_version_unknown", "正式需求写响应版本不受支持");
  }
  const requestId = uuid(field(object, "request_id"), "request_id");
  const action = exactEnum(field(object, "action"), MATERIAL_REQUEST_MUTATION_ACTIONS, "写动作");
  const requestVersion = nonnegativeInteger(field(object, "request_version"), "request_version");
  const revisionId = uuid(field(object, "revision_id"), "revision_id");
  const revisionNo = positiveInteger(field(object, "revision_no"), "revision_no");
  const approvalInstanceId = nullableUuid(field(object, "approval_instance_id"), "approval_instance_id");
  const rawApprovalAttemptNo = field(object, "approval_attempt_no");
  const approvalAttemptNo = rawApprovalAttemptNo === null
    ? null
    : positiveInteger(rawApprovalAttemptNo, "approval_attempt_no");
  const currentStepId = nullableUuid(field(object, "current_step_id"), "current_step_id");
  const states = validateMaterialRequestStateAxes(field(object, "states"));
  if ((approvalInstanceId === null) !== (approvalAttemptNo === null)
      || (approvalInstanceId === null && currentStepId !== null)) {
    invalid("material_request_contract_approval_anchor_invalid", "审批实例、尝试和当前步骤锚点不一致");
  }
  const approvalActions: readonly MaterialRequestMutationAction[] = [
    "submit", "withdraw", "cancel", "approve", "return", "reject",
    "register_external_approval", "verify_external_approval",
  ];
  if (approvalActions.includes(action) && approvalInstanceId === null) {
    invalid("material_request_contract_approval_anchor_invalid", "审批写响应缺少实例尝试锚点");
  }
  if (action === "submit" && currentStepId === null) {
    invalid("material_request_contract_approval_anchor_invalid", "提交响应缺少已打开当前步骤");
  }
  if (action === "withdraw" && states.request_status !== "withdrawn") {
    invalid("material_request_contract_lifecycle_state_invalid", "撤回响应必须进入已撤回申请状态");
  }
  if (action === "cancel" && states.request_status !== "cancelled") {
    invalid("material_request_contract_lifecycle_state_invalid", "取消响应必须进入已取消申请状态");
  }
  if ((action === "withdraw" || action === "cancel") && currentStepId !== null) {
    invalid("material_request_contract_approval_anchor_invalid", "终态生命周期响应不能保留当前审批步骤");
  }
  const expectedVersion = nonnegativeInteger(expected.previousVersion, "previousVersion");
  if (requestId !== uuid(expected.requestId, "expected_request_id") || action !== expected.action) {
    invalid("material_request_contract_anchor_mismatch", "正式需求写响应与请求目标或动作不一致");
  }
  if (requestVersion !== expectedVersion + 1) {
    invalid("material_request_contract_version_mismatch", "正式需求写响应版本未精确递增");
  }
  const replayed = field(object, "idempotency_replayed");
  if (typeof replayed !== "boolean") {
    invalid("material_request_contract_replay_invalid", "idempotency_replayed无效");
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: requestId,
    action,
    request_version: requestVersion,
    revision_id: revisionId,
    revision_no: revisionNo,
    approval_instance_id: approvalInstanceId,
    approval_attempt_no: approvalAttemptNo,
    current_step_id: currentStepId,
    states,
    idempotency_replayed: replayed,
  };
}

export function validateMaterialRequestLifecycleCommandStatus(
  value: unknown,
): MaterialRequestLifecycleCommandStatus {
  const object = record(value, "正式需求生命周期命令状态");
  exactKeys(object, ["schema_version", "lookup_status", "command"], "正式需求生命周期命令状态");
  if (field(object, "schema_version") !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return invalid("material_request_contract_version_unknown", "正式需求生命周期命令状态版本不受支持");
  }
  const lookupStatus = exactEnum(
    field(object, "lookup_status"),
    ["not_observed", "confirmed"] as const,
    "生命周期命令查询状态",
  );
  const rawCommand = field(object, "command");
  if (lookupStatus === "not_observed") {
    if (rawCommand !== null) {
      invalid("material_request_contract_command_status_invalid", "未观察到的命令状态不能包含命令事实");
    }
    return {
      schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
      lookup_status: "not_observed",
      command: null,
    };
  }
  const command = record(rawCommand, "正式需求生命周期命令事实");
  exactKeys(command, [
    "action", "request_id", "request_version", "revision_id", "revision_no",
    "approval_instance_id", "approval_attempt_no", "current_step_id", "states", "occurred_at",
  ], "正式需求生命周期命令事实");
  const action = exactEnum(field(command, "action"), ["withdraw", "cancel"] as const, "生命周期动作");
  const states = validateMaterialRequestStateAxes(field(command, "states"));
  if (states.request_status !== (action === "withdraw" ? "withdrawn" : "cancelled")) {
    invalid("material_request_contract_lifecycle_state_invalid", "生命周期命令事实与申请终态不一致");
  }
  if (field(command, "current_step_id") !== null) {
    invalid("material_request_contract_approval_anchor_invalid", "已确认生命周期命令不能保留当前审批步骤");
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    lookup_status: "confirmed",
    command: {
      action,
      request_id: uuid(field(command, "request_id"), "command.request_id"),
      request_version: positiveInteger(field(command, "request_version"), "command.request_version"),
      revision_id: uuid(field(command, "revision_id"), "command.revision_id"),
      revision_no: positiveInteger(field(command, "revision_no"), "command.revision_no"),
      approval_instance_id: uuid(field(command, "approval_instance_id"), "command.approval_instance_id"),
      approval_attempt_no: positiveInteger(
        field(command, "approval_attempt_no"),
        "command.approval_attempt_no",
      ),
      current_step_id: null,
      states,
      occurred_at: awareTimestamp(field(command, "occurred_at"), "command.occurred_at"),
    },
  };
}

export function validateMaterialRequestCreateResult(value: unknown): MaterialRequestCreateResult {
  const object = record(value, "正式需求创建响应");
  exactKeys(object, [
    "schema_version", "request_id", "action", "request_version", "revision_id", "revision_no",
    "states", "idempotency_replayed",
  ], "正式需求创建响应");
  if (field(object, "schema_version") !== MATERIAL_REQUEST_SCHEMA_VERSION) {
    return invalid("material_request_contract_version_unknown", "正式需求创建响应版本不受支持");
  }
  if (field(object, "action") !== "create") {
    return invalid("material_request_contract_anchor_mismatch", "正式需求创建响应动作不一致");
  }
  if (field(object, "request_version") !== 0) {
    return invalid("material_request_contract_version_mismatch", "新建正式需求版本必须为零");
  }
  if (field(object, "revision_no") !== 1) {
    return invalid("material_request_contract_revision_anchor_invalid", "新建正式需求修订号必须为一");
  }
  const states = validateMaterialRequestStateAxes(field(object, "states"));
  if (
    states.request_status !== "draft"
    || states.allocation_status !== "not_allocated"
    || states.reservation_status !== "not_reserved"
    || states.outbound_status !== "not_started"
    || states.shipment_status !== "not_started"
    || states.logistics_signature_status !== "not_signed"
    || states.oam_receipt_status !== "not_occurred"
    || states.personal_inbound_status !== "not_started"
    || states.notification_status !== "not_started"
    || states.reconciliation_status !== "not_started"
  ) {
    return invalid("material_request_contract_create_state_invalid", "新建正式需求必须返回十轴中性草稿状态");
  }
  const replayed = field(object, "idempotency_replayed");
  if (typeof replayed !== "boolean") {
    return invalid("material_request_contract_replay_invalid", "idempotency_replayed无效");
  }
  return {
    schema_version: MATERIAL_REQUEST_SCHEMA_VERSION,
    request_id: uuid(field(object, "request_id"), "request_id"),
    action: "create",
    request_version: 0,
    revision_id: uuid(field(object, "revision_id"), "revision_id"),
    revision_no: 1,
    states,
    idempotency_replayed: replayed,
  };
}

export type MaterialRequestWriteHeaders = Readonly<{ "X-Request-ID": string; "Idempotency-Key": string }>;

export function createMaterialRequestWriteHeaders(action: MaterialRequestWriteAction): MaterialRequestWriteHeaders {
  exactEnum(action, MATERIAL_REQUEST_WRITE_ACTIONS, "写动作");
  const generated = new Headers(mutationHeaders(`material-request-${action}`).headers);
  const requestId = generated.get("X-Request-ID") || "";
  const idempotencyKey = generated.get("Idempotency-Key") || "";
  if (!SAFE_COORDINATE.test(requestId) || !SAFE_COORDINATE.test(idempotencyKey)) {
    return invalid("material_request_contract_coordinate_invalid", "无法生成安全正式需求写请求坐标");
  }
  return Object.freeze({ "X-Request-ID": requestId, "Idempotency-Key": idempotencyKey });
}

export type MaterialRequestCreateIntent = Readonly<{
  client_draft_key: string;
  action: "create";
  path: "/v1/material-requests";
  body: MaterialRequestDraftInput;
  signature: string;
  headers: MaterialRequestWriteHeaders;
}>;

export type MaterialRequestMutationIntent = Readonly<{
  request_id: string;
  action: MaterialRequestMutationAction;
  path: string;
  body: unknown;
  expected_version: number;
  signature: string;
  headers: MaterialRequestWriteHeaders;
}>;

function sortedJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedJsonValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, child]) => [key, sortedJsonValue(child)]));
}
function transportedValue(value: unknown): unknown {
  let encoded: string | undefined;
  try {
    encoded = JSON.stringify(value);
  } catch {
    encoded = undefined;
  }
  if (encoded === undefined) {
    return invalid("material_request_contract_intent_body_invalid", "正式需求写入内容不可序列化");
  }
  return JSON.parse(encoded) as unknown;
}
function canonicalJson(value: unknown): string {
  return JSON.stringify(sortedJsonValue(transportedValue(value)));
}
function deepFreeze<T>(value: T): T {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value as Record<string, unknown>)) deepFreeze(child);
  return Object.freeze(value);
}
function canonicalFormalPath(value: unknown, requestId: string): string {
  if (typeof value !== "string" || !value.startsWith("/v1/material-requests/")) {
    return invalid("material_request_contract_intent_path_invalid", "正式需求写入路径不在正式命名空间");
  }
  if (value.includes("?") || value.includes("#") || value.includes("\\") || value.includes("%")
      || value.includes("//") || /[\u0000-\u0020\u007f]/.test(value)
      || value.split("/").some((part) => part === "." || part === "..")) {
    return invalid("material_request_contract_intent_path_invalid", "正式需求写入路径不规范");
  }
  const normalized = value.replace(/\/+$/, "");
  const root = `/v1/material-requests/${requestId}`;
  if (normalized !== root && !normalized.startsWith(`${root}/`)) {
    return invalid("material_request_contract_anchor_mismatch", "正式需求写入路径与目标对象不一致");
  }
  return normalized;
}
function expectedVersionField(action: MaterialRequestMutationAction): "expected_version" | "expected_request_version" {
  return (["update", "submit", "withdraw", "cancel"] as readonly MaterialRequestMutationAction[]).includes(action)
    ? "expected_version" : "expected_request_version";
}

export class MaterialRequestIntentConflictError extends MaterialRequestContractError {
  readonly request_id: string;
  readonly pending_signature: string;
  constructor(requestId: string, pendingSignature: string) {
    super("material_request_intent_conflict", "同一正式需求存在结果未确认的不同写入，已停止创建新请求坐标");
    this.name = "MaterialRequestIntentConflictError";
    this.request_id = requestId;
    this.pending_signature = pendingSignature;
  }
}

export class MaterialRequestCreateIntentConflictError extends MaterialRequestContractError {
  readonly client_draft_key: string;
  readonly pending_signature: string;
  constructor(clientDraftKey: string, pendingSignature: string) {
    super("material_request_create_intent_conflict", "存在结果未确认的草稿创建，已停止生成新的写请求坐标");
    this.name = "MaterialRequestCreateIntentConflictError";
    this.client_draft_key = clientDraftKey;
    this.pending_signature = pendingSignature;
  }
}

/** One page instance may hold exactly one unconfirmed create. Nothing is persisted. */
export class MaterialRequestCreateIntentRegistry {
  private pending: Readonly<{ canonical: string; intent: MaterialRequestCreateIntent }> | null = null;

  begin(input: Readonly<{ body: unknown; path?: string }>): MaterialRequestCreateIntent {
    const path = input.path ?? "/v1/material-requests";
    if (path !== "/v1/material-requests") {
      return invalid("material_request_contract_intent_path_invalid", "正式需求创建路径不在正式命名空间");
    }
    const body = validateMaterialRequestDraftInput(transportedValue(input.body));
    const canonical = canonicalJson({ action: "create", body, path });
    if (this.pending) {
      if (this.pending.canonical !== canonical) {
        throw new MaterialRequestCreateIntentConflictError(
          this.pending.intent.client_draft_key,
          this.pending.intent.signature,
        );
      }
      return this.pending.intent;
    }
    const headers = createMaterialRequestWriteHeaders("create");
    const clientDraftKey = `draft-${headers["X-Request-ID"]}`;
    if (!SAFE_CLIENT_DRAFT_KEY.test(clientDraftKey)) {
      return invalid("material_request_contract_coordinate_invalid", "无法生成安全正式需求草稿锚点");
    }
    const intent = deepFreeze({
      client_draft_key: clientDraftKey,
      action: "create" as const,
      path: "/v1/material-requests" as const,
      body,
      signature: headers["Idempotency-Key"],
      headers,
    });
    this.pending = Object.freeze({ canonical, intent });
    return intent;
  }

  get(): MaterialRequestCreateIntent | undefined {
    return this.pending?.intent;
  }

  confirm(clientDraftKey: string, signature: string): void {
    if (
      !this.pending
      || this.pending.intent.client_draft_key !== clientDraftKey
      || this.pending.intent.signature !== signature
    ) {
      invalid("material_request_contract_intent_confirmation_invalid", "不能确认不匹配的正式需求创建意图");
    }
    this.pending = null;
  }

  clearDefinitiveRejection(clientDraftKey: string, signature: string): void {
    this.confirm(clientDraftKey, signature);
  }

  get size(): number {
    return this.pending ? 1 : 0;
  }
}

type PendingIntent = Readonly<{ canonical: string; intent: MaterialRequestMutationIntent }>;

export class MaterialRequestIntentRegistry {
  private readonly intents = new Map<string, PendingIntent>();
  private readonly maximum: number;
  constructor(maximum = 64) {
    if (!Number.isSafeInteger(maximum) || maximum <= 0) {
      invalid("material_request_contract_intent_limit_invalid", "未确认写意图上限无效");
    }
    this.maximum = maximum;
  }
  begin(input: Readonly<{
    requestId: string;
    action: MaterialRequestMutationAction;
    path: string;
    body: unknown;
    expectedVersion: number;
  }>): MaterialRequestMutationIntent {
    const requestId = uuid(input.requestId, "request_id");
    const action = exactEnum(input.action, MATERIAL_REQUEST_MUTATION_ACTIONS, "写动作");
    const path = canonicalFormalPath(input.path, requestId);
    const expectedVersion = nonnegativeInteger(input.expectedVersion, "expectedVersion");
    const body = transportedValue(input.body);
    const bodyObject = record(body, "正式需求写入内容");
    const versionField = expectedVersionField(action);
    if (field(bodyObject, versionField) !== expectedVersion) {
      invalid("material_request_contract_intent_version_mismatch", "写入内容与期望版本不一致");
    }
    const canonical = canonicalJson({ action, body, expected_version: expectedVersion, path, request_id: requestId });
    const pending = this.intents.get(requestId);
    if (pending) {
      if (pending.canonical !== canonical) {
        throw new MaterialRequestIntentConflictError(requestId, pending.intent.signature);
      }
      return pending.intent;
    }
    if (this.intents.size >= this.maximum) {
      invalid("material_request_contract_intent_limit_reached", "未确认正式需求写入过多，已停止创建新坐标");
    }
    const headers = createMaterialRequestWriteHeaders(action);
    const intent = deepFreeze({
      request_id: requestId,
      action,
      path,
      body,
      expected_version: expectedVersion,
      signature: headers["Idempotency-Key"],
      headers,
    });
    this.intents.set(requestId, Object.freeze({ canonical, intent }));
    return intent;
  }
  get(requestId: string): MaterialRequestMutationIntent | undefined {
    return this.intents.get(uuid(requestId, "request_id"))?.intent;
  }
  confirm(requestId: string, signature: string): void {
    const checkedId = uuid(requestId, "request_id");
    const pending = this.intents.get(checkedId);
    if (!pending || pending.intent.signature !== signature) {
      invalid("material_request_contract_intent_confirmation_invalid", "不能确认不匹配的正式需求写意图");
    }
    this.intents.delete(checkedId);
  }
  clearDefinitiveRejection(requestId: string, signature: string): void {
    this.confirm(requestId, signature);
  }
  get size(): number {
    return this.intents.size;
  }
}
