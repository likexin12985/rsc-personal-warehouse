import { ApiError, api, jsonBody, mutationHeaders } from "./api";


export const FORMAL_OPENING_STOCKTAKE_PATH = "/v1/stocktakes/opening";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const QUANTITY = /^(?:0|[1-9]\d*)\.\d{3}$/;
const SIGNED_QUANTITY = /^-?(?:0|[1-9]\d*)\.\d{3}$/;
const INPUT_QUANTITY = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const ZERO_INPUT_QUANTITY = /^0(?:\.0{1,3})?$/;
const SERIAL_UNIT_QUANTITY = /^1(?:\.0{1,3})?$/;

const OPENING_ACTIONS = [
  "count",
  "review_region",
  "review_headquarters",
  "open_recount",
  "post",
  "close",
] as const;
const EVIDENCE_STATUSES = ["not_started", "counting_hidden", "sealed"] as const;
const ROUND_STATUSES = ["counting", "submitted", "superseded"] as const;
const DIFFERENCE_TYPES = [
  "missing",
  "excess",
  "wrong_location",
  "wrong_condition",
  "wrong_lot",
  "wrong_serial",
  "control_unassigned",
] as const;
const MATERIAL_IDENTIFIER_TYPES = [
  "sku_code",
  "qr_code",
  "external_code",
  "unknown",
] as const;
const CONDITION_CODES = ["new", "used", "damaged", "scrapped"] as const;
const AVAILABILITY_BUCKETS = [
  "available",
  "reserved",
  "picking",
  "outbound",
  "in_transit",
  "arrived_pending",
  "frozen",
  "return_pending",
  "scrap_pending",
] as const;
const SERIAL_IDENTIFIER_TYPES = ["serial_no", "qr_code", "unknown"] as const;
const OBSERVATION_DISPOSITIONS = [
  "resolved_existing_master",
  "pending_verification",
  "requires_recount",
] as const;
const TASK_STATUSES = [
  "draft",
  "issued",
  "frozen",
  "counting",
  "submitted",
  "region_review",
  "hq_review",
  "approved",
  "recount_required",
  "posted",
  "closed",
  "cancelled",
] as const;
const SEALED_TASK_STATUSES = [
  "submitted",
  "region_review",
  "hq_review",
  "approved",
  "recount_required",
  "posted",
  "closed",
] as const;

export type OpeningAllowedAction = typeof OPENING_ACTIONS[number];
export type OpeningEvidenceStatus = typeof EVIDENCE_STATUSES[number];
export type OpeningTaskStatus = typeof TASK_STATUSES[number];
export type OpeningObservationDisposition = typeof OBSERVATION_DISPOSITIONS[number];
export type OpeningReviewTopDecision = "approve" | "recount" | "reject";
export type OpeningReviewItemDecision =
  | "accept_for_posting"
  | "pending_verification"
  | "recount"
  | "reject";

export type OpeningStocktakeRound = Readonly<{
  round_id: string;
  round_no: number;
  round_type: "initial" | "recount";
  status: typeof ROUND_STATUSES[number];
  started_at: string;
  submitted_at: string | null;
}>;

export type OpeningStocktakeReviewSummary = Readonly<{
  stage: "region" | "headquarters";
  decision: "approve" | "recount" | "reject";
  reviewed_at: string;
}>;

export type OpeningStocktakeTaskSummary = Readonly<{
  task_id: string;
  task_no: string;
  region_org_id: string;
  status: OpeningTaskStatus;
  blind_count: boolean;
  current_round_no: number;
  current_round_status: typeof ROUND_STATUSES[number] | null;
  visible_scope_count: number;
  completed_scope_count: number;
  evidence_status: OpeningEvidenceStatus;
  difference_count: number | null;
  task_version: number;
  deadline: string | null;
  allowed_actions: OpeningAllowedAction[];
}>;

export type OpeningStocktakeTaskPage = Readonly<{
  schema_version: "1.0";
  items: OpeningStocktakeTaskSummary[];
  next_after_id: string | null;
}>;

export type OpeningStocktakeScope = Readonly<{
  scope_id: string;
  scope_no: number;
  location_id: string;
  owner_org_id: string;
  assigned_to_me: boolean;
  completion_status: "pending" | "completed";
  zero_confirmed: boolean | null;
  count_line_count: number | null;
  observation_line_count: number | null;
  serial_count: number | null;
  total_counted_qty: string | null;
  completed_at: string | null;
}>;

export type OpeningStocktakeDifference = Readonly<{
  difference_id: string;
  difference_no: number;
  scope_id: string | null;
  difference_type: typeof DIFFERENCE_TYPES[number];
  material_id: string | null;
  book_qty: string;
  counted_qty: string;
  difference_qty: string;
  affected_qty: string;
  reason_code: string | null;
  evidence_required: boolean;
}>;

export type OpeningObservationDispositionSummary = Readonly<{
  disposition_id: string;
  disposition: OpeningObservationDisposition;
  resolved_material_id: string | null;
  resolved_lot_id: string | null;
  resolved_serial_id: string | null;
  reason_code: string;
  decided_at: string;
}>;

export type OpeningStocktakeObservation = Readonly<{
  observation_id: string;
  difference_id: string;
  observation_no: number;
  scope_id: string;
  material_identifier_type: typeof MATERIAL_IDENTIFIER_TYPES[number];
  material_identifier_raw: string;
  condition_code: typeof CONDITION_CODES[number];
  availability_bucket: typeof AVAILABILITY_BUCKETS[number];
  counted_qty: string;
  verification_status: "verified" | "pending_verification";
  material_id: string | null;
  lot_id: string | null;
  lot_no_raw: string | null;
  serial_id: string | null;
  serial_no_raw: string | null;
  serial_identifier_type: typeof SERIAL_IDENTIFIER_TYPES[number] | null;
  disposition: OpeningObservationDispositionSummary | null;
  allowed_dispositions: OpeningObservationDisposition[];
}>;

export type OpeningStocktakeTaskDetail = VersionedOpeningDetail & Readonly<{
  schema_version: "1.0";
  task_no: string;
  region_org_id: string;
  status: OpeningTaskStatus;
  blind_count: boolean;
  deadline: string | null;
  cutoff_at: string | null;
  current_round: OpeningStocktakeRound | null;
  evidence_status: OpeningEvidenceStatus;
  scopes: OpeningStocktakeScope[];
  observations: OpeningStocktakeObservation[];
  differences: OpeningStocktakeDifference[];
  reviews: OpeningStocktakeReviewSummary[];
  allowed_actions: OpeningAllowedAction[];
}>;

export type OpeningTerminalAction = "post" | "close";

export type OpeningPhysicalObservationInput = Readonly<{
  material_identifier_raw: string;
  material_identifier_type: "sku_code" | "qr_code" | "external_code" | "unknown";
  condition_code: "new" | "used" | "damaged" | "scrapped";
  availability_bucket:
    | "available"
    | "reserved"
    | "picking"
    | "outbound"
    | "in_transit"
    | "arrived_pending"
    | "frozen"
    | "return_pending"
    | "scrap_pending";
  counted_qty: string;
  material_id?: string | null;
  lot_id?: string | null;
  lot_no_raw?: string | null;
  serial_id?: string | null;
  serial_no_raw?: string | null;
  serial_identifier_type?: "serial_no" | "qr_code" | "unknown" | null;
  count_method?: "scan" | "manual" | "import";
  reason_code?: string | null;
  remark?: string;
}>;

export type OpeningScopeCountInput = Readonly<{
  physical_observations: readonly OpeningPhysicalObservationInput[];
  zero_confirmed: boolean;
}>;

export type OpeningReviewItemInput = Readonly<{
  difference_id: string;
  decision: OpeningReviewItemDecision;
  comment: string;
}>;

export type OpeningRegionReviewInput = Readonly<{
  decision: OpeningReviewTopDecision;
  items: readonly OpeningReviewItemInput[];
  comment: string;
}>;

export type OpeningHeadquartersReviewInput = Readonly<{
  decision: "approve" | "reject";
  items: readonly OpeningReviewItemInput[];
  comment: string;
}>;

export type OpeningRecountInput = Readonly<{
  assignments: readonly Readonly<{
    scope_id: string;
    assignee_user_id: string;
  }>[];
  reason: string;
}>;

export type OpeningObservationDispositionInput = Readonly<{
  disposition: OpeningObservationDisposition;
  reason_code: string;
  comment: string;
  resolved_material_id?: string | null;
  resolved_lot_id?: string | null;
  resolved_serial_id?: string | null;
}>;

export type OpeningObservationDispositionResult = Readonly<{
  schema_version: "1.0";
  disposition_id: string;
  task_id: string;
  round_id: string;
  scope_id: string;
  observation_id: string;
  disposition: OpeningObservationDisposition;
  resolved_material_id: string | null;
  resolved_lot_id: string | null;
  resolved_serial_id: string | null;
  disposition_manifest_sha256: string;
  replayed: boolean;
}>;

export type OpeningCountResult = Readonly<{
  schema_version: "1.0";
  task_id: string;
  round_id: string;
  scope_id: string;
  task_status: OpeningTaskStatus;
  round_status: typeof ROUND_STATUSES[number];
  scope_completed: boolean;
  round_sealed: boolean;
  has_pending_verification: boolean;
  replayed: boolean;
}>;

export type OpeningReviewResult = Readonly<{
  schema_version: "1.0";
  review_id: string;
  task_id: string;
  round_id: string;
  review_stage: "region" | "headquarters";
  decision: "approve" | "recount" | "reject";
  resulting_task_status: "hq_review" | "approved" | "recount_required";
  item_count: number;
  pending_control_count: number;
  replayed: boolean;
}>;

export type OpeningRecountResult = Readonly<{
  schema_version: "1.0";
  recount_case_id: string;
  task_id: string;
  source_round_id: string;
  next_round_id: string;
  next_round_no: number;
  scope_count: number;
  resulting_task_status: "counting";
  replayed: boolean;
}>;

export type OpeningPostResult = Readonly<{
  schema_version: "1.0";
  task_id: string;
  round_id: string;
  posting_id: string;
  inventory_transaction_id: string | null;
  resulting_task_status: "posted";
  task_version: number;
  total_quantity: string;
  established_scope_count: number;
  pending_control_difference_count: number;
  ledger_cursor: number;
  replayed: boolean;
}>;

export type OpeningCloseResult = Readonly<{
  schema_version: "1.0";
  task_id: string;
  posting_id: string;
  inventory_transaction_id: string | null;
  resulting_task_status: "closed";
  task_version: number;
  closed_at: string;
  replayed: boolean;
}>;

export type VersionedOpeningDetail = Readonly<{
  task_id: string;
  task_version: number;
}>;

export type VersionedOpeningMutationResult<TDetail, TResult> = Readonly<{
  before: TDetail;
  result: TResult;
  detail: TDetail;
}>;

function invalid(message: string): never {
  throw new ApiError(409, message);
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalid("正式期初盘点响应不是有效对象");
  }
  return value as Record<string, unknown>;
}

function field(object: Record<string, unknown>, name: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(object, name)) {
    return invalid(`正式期初盘点响应缺少字段 ${name}`);
  }
  return object[name];
}

function exactEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  name: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return invalid(`正式期初盘点响应包含未知 ${name}`);
  }
  return value as T;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value.toLowerCase();
}

function text(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim()) {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value;
}

function nonnegativeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value as number;
}

function positiveInteger(value: unknown, name: string): number {
  const checked = nonnegativeInteger(value, name);
  if (checked < 1) return invalid(`正式期初盘点响应中的 ${name} 无效`);
  return checked;
}

function timestamp(value: unknown, name: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (
    typeof value !== "string"
    || !ISO_TIMESTAMP.test(value)
    || !Number.isFinite(Date.parse(value))
  ) return invalid(`正式期初盘点响应中的 ${name} 无效`);
  return value;
}

function nullableUuid(value: unknown, name: string): string | null {
  return value === null ? null : uuid(value, name);
}

function nullableNonnegativeInteger(value: unknown, name: string): number | null {
  return value === null ? null : nonnegativeInteger(value, name);
}

function booleanValue(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value;
}

function schemaVersion(object: Record<string, unknown>): void {
  if (field(object, "schema_version") !== "1.0") {
    invalid("正式期初盘点响应版本不受支持");
  }
}

function quantity(value: unknown, name: string, signed = false): string {
  if (typeof value !== "string" || !(signed ? SIGNED_QUANTITY : QUANTITY).test(value)) {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value;
}

function allowedActions(value: unknown): OpeningAllowedAction[] {
  if (!Array.isArray(value)) return invalid("正式期初盘点允许操作不是数组");
  const checked = value.map((action) => exactEnum(
    action,
    OPENING_ACTIONS,
    "allowed_action",
  ));
  if (new Set(checked).size !== checked.length) {
    return invalid("正式期初盘点包含重复允许操作");
  }
  return checked;
}

function validateAllowedActionState(
  status: OpeningTaskStatus,
  actions: readonly OpeningAllowedAction[],
): void {
  const states: Record<OpeningAllowedAction, readonly OpeningTaskStatus[]> = {
    count: ["counting"],
    review_region: ["submitted", "region_review"],
    review_headquarters: ["hq_review"],
    open_recount: ["recount_required"],
    post: ["approved"],
    close: ["posted"],
  };
  if (actions.some((action) => !states[action].includes(status))) {
    invalid("正式期初盘点允许操作与任务状态不一致");
  }
}

function validateSummaryEvidenceState(
  status: OpeningTaskStatus,
  evidenceStatus: OpeningEvidenceStatus,
  roundNo: number,
  roundStatus: typeof ROUND_STATUSES[number] | null,
): void {
  if (evidenceStatus === "not_started") {
    if (roundNo !== 0 || roundStatus !== null) invalid("未开始盘点包含轮次证据");
    if (!["draft", "issued", "frozen", "cancelled"].includes(status)) {
      invalid("未开始盘点与任务状态不一致");
    }
    return;
  }
  if (evidenceStatus === "counting_hidden") {
    if (status !== "counting" || roundNo < 1 || roundStatus !== "counting") {
      invalid("盲盘计数状态与当前轮次不一致");
    }
    return;
  }
  if (!SEALED_TASK_STATUSES.includes(status as typeof SEALED_TASK_STATUSES[number])
    || roundNo < 1
    || roundStatus !== "submitted") {
    invalid("已封存盘点状态与当前轮次不一致");
  }
}

function validateEvidenceVisibility(
  evidenceStatus: OpeningEvidenceStatus,
  differenceCount: number | null,
): void {
  if (evidenceStatus === "sealed" ? differenceCount === null : differenceCount !== null) {
    invalid("盘点差异数量与证据封存状态不一致");
  }
}

function validateTaskSummary(value: unknown): OpeningStocktakeTaskSummary {
  const object = record(value);
  uuid(field(object, "task_id"), "task_id");
  text(field(object, "task_no"), "task_no");
  uuid(field(object, "region_org_id"), "region_org_id");
  const status = exactEnum(field(object, "status"), TASK_STATUSES, "task_status");
  if (typeof field(object, "blind_count") !== "boolean") invalid("盲盘标记无效");
  const roundNo = nonnegativeInteger(field(object, "current_round_no"), "current_round_no");
  const rawRoundStatus = field(object, "current_round_status");
  const roundStatus = rawRoundStatus === null ? null : exactEnum(
    rawRoundStatus,
    ROUND_STATUSES,
    "current_round_status",
  );
  if ((roundNo === 0) !== (roundStatus === null)) {
    invalid("盘点当前轮次编号与状态不一致");
  }
  const visibleScopeCount = nonnegativeInteger(
    field(object, "visible_scope_count"),
    "visible_scope_count",
  );
  const completedScopeCount = nonnegativeInteger(
    field(object, "completed_scope_count"),
    "completed_scope_count",
  );
  if (completedScopeCount > visibleScopeCount) invalid("盘点完成范围超过可见范围");
  const evidenceStatus = exactEnum(
    field(object, "evidence_status"),
    EVIDENCE_STATUSES,
    "evidence_status",
  );
  const differenceCount = nullableNonnegativeInteger(
    field(object, "difference_count"),
    "difference_count",
  );
  validateEvidenceVisibility(evidenceStatus, differenceCount);
  validateSummaryEvidenceState(status, evidenceStatus, roundNo, roundStatus);
  nonnegativeInteger(field(object, "task_version"), "task_version");
  timestamp(field(object, "deadline"), "deadline", true);
  validateAllowedActionState(status, allowedActions(field(object, "allowed_actions")));
  return object as OpeningStocktakeTaskSummary;
}

function validateRound(value: unknown): OpeningStocktakeRound {
  const object = record(value);
  uuid(field(object, "round_id"), "round_id");
  positiveInteger(field(object, "round_no"), "round_no");
  exactEnum(field(object, "round_type"), ["initial", "recount"] as const, "round_type");
  const status = exactEnum(field(object, "status"), ROUND_STATUSES, "round_status");
  timestamp(field(object, "started_at"), "started_at");
  const submittedAt = timestamp(field(object, "submitted_at"), "submitted_at", true);
  if ((status === "counting") !== (submittedAt === null)) {
    invalid("盘点轮次提交时间与状态不一致");
  }
  return object as OpeningStocktakeRound;
}

function validateScope(
  value: unknown,
  evidenceStatus: OpeningEvidenceStatus,
): OpeningStocktakeScope {
  const object = record(value);
  uuid(field(object, "scope_id"), "scope_id");
  positiveInteger(field(object, "scope_no"), "scope_no");
  uuid(field(object, "location_id"), "location_id");
  uuid(field(object, "owner_org_id"), "owner_org_id");
  if (typeof field(object, "assigned_to_me") !== "boolean") invalid("盘点范围指派标记无效");
  const completion = exactEnum(
    field(object, "completion_status"),
    ["pending", "completed"] as const,
    "completion_status",
  );
  const zeroConfirmed = field(object, "zero_confirmed");
  if (zeroConfirmed !== null && typeof zeroConfirmed !== "boolean") invalid("零库存确认标记无效");
  const countLineCount = nullableNonnegativeInteger(field(object, "count_line_count"), "count_line_count");
  const observationLineCount = nullableNonnegativeInteger(
    field(object, "observation_line_count"),
    "observation_line_count",
  );
  const serialCount = nullableNonnegativeInteger(field(object, "serial_count"), "serial_count");
  const totalCountedQty = field(object, "total_counted_qty");
  if (totalCountedQty !== null) quantity(totalCountedQty, "total_counted_qty");
  const completedAt = timestamp(field(object, "completed_at"), "completed_at", true);
  const protectedValues = [
    zeroConfirmed,
    countLineCount,
    observationLineCount,
    serialCount,
    totalCountedQty,
  ];
  if (evidenceStatus !== "sealed" && protectedValues.some((item) => item !== null)) {
    invalid("盘点证据封存前不得展示或推算实盘数量");
  }
  if (completion === "pending" && completedAt !== null) {
    invalid("待计数范围包含完成时间");
  }
  if (completion === "completed" && completedAt === null) {
    invalid("已完成范围缺少完成时间");
  }
  if (evidenceStatus === "sealed" && completion === "completed" && protectedValues.some((item) => item === null)) {
    invalid("已封存完成范围缺少实盘证据摘要");
  }
  if (evidenceStatus === "sealed" && completion === "pending" && protectedValues.some((item) => item !== null)) {
    invalid("未完成范围包含实盘证据摘要");
  }
  return object as OpeningStocktakeScope;
}

function validateDifference(value: unknown): OpeningStocktakeDifference {
  const object = record(value);
  uuid(field(object, "difference_id"), "difference_id");
  positiveInteger(field(object, "difference_no"), "difference_no");
  nullableUuid(field(object, "scope_id"), "scope_id");
  exactEnum(field(object, "difference_type"), DIFFERENCE_TYPES, "difference_type");
  nullableUuid(field(object, "material_id"), "material_id");
  quantity(field(object, "book_qty"), "book_qty");
  quantity(field(object, "counted_qty"), "counted_qty");
  quantity(field(object, "difference_qty"), "difference_qty", true);
  quantity(field(object, "affected_qty"), "affected_qty");
  const reason = field(object, "reason_code");
  if (reason !== null) text(reason, "reason_code");
  if (typeof field(object, "evidence_required") !== "boolean") invalid("差异凭证要求标记无效");
  return object as OpeningStocktakeDifference;
}

function nullableTrimmedText(value: unknown, name: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !value || value !== value.trim()) {
    return invalid(`正式期初盘点响应中的 ${name} 无效`);
  }
  return value;
}

function validateObservationDispositionSummary(
  value: unknown,
): OpeningObservationDispositionSummary {
  const object = record(value);
  uuid(field(object, "disposition_id"), "disposition_id");
  const disposition = exactEnum(
    field(object, "disposition"),
    OBSERVATION_DISPOSITIONS,
    "observation_disposition",
  );
  const materialId = nullableUuid(
    field(object, "resolved_material_id"),
    "resolved_material_id",
  );
  const lotId = nullableUuid(field(object, "resolved_lot_id"), "resolved_lot_id");
  const serialId = nullableUuid(
    field(object, "resolved_serial_id"),
    "resolved_serial_id",
  );
  const reasonCode = nullableTrimmedText(field(object, "reason_code"), "reason_code");
  if (reasonCode === null) invalid("现场观察处置缺少原因代码");
  if (reasonCode.length > 80) invalid("现场观察处置原因代码过长");
  timestamp(field(object, "decided_at"), "decided_at");
  if (disposition === "resolved_existing_master") {
    if (materialId === null) invalid("绑定正式主数据的观察处置缺少物料标识");
  } else if (materialId !== null || lotId !== null || serialId !== null) {
    invalid("未绑定正式主数据的观察处置夹带主数据标识");
  }
  return object as OpeningObservationDispositionSummary;
}

function validateAllowedObservationDispositions(
  value: unknown,
): OpeningObservationDisposition[] {
  if (!Array.isArray(value)) invalid("现场观察允许处置不是数组");
  const checked = value.map((item) => exactEnum(
    item,
    OBSERVATION_DISPOSITIONS,
    "allowed_observation_disposition",
  ));
  if (new Set(checked).size !== checked.length) {
    invalid("现场观察包含重复允许处置");
  }
  const canonical = [
    [],
    ["pending_verification", "requires_recount"],
    [...OBSERVATION_DISPOSITIONS],
  ].map((items) => JSON.stringify(items));
  if (!canonical.includes(JSON.stringify(checked))) {
    invalid("现场观察允许处置集合不符合正式角色策略");
  }
  return checked;
}

function validateObservation(value: unknown): OpeningStocktakeObservation {
  const object = record(value);
  uuid(field(object, "observation_id"), "observation_id");
  uuid(field(object, "difference_id"), "difference_id");
  positiveInteger(field(object, "observation_no"), "observation_no");
  uuid(field(object, "scope_id"), "scope_id");
  exactEnum(
    field(object, "material_identifier_type"),
    MATERIAL_IDENTIFIER_TYPES,
    "material_identifier_type",
  );
  nullableTrimmedText(
    field(object, "material_identifier_raw"),
    "material_identifier_raw",
  );
  exactEnum(field(object, "condition_code"), CONDITION_CODES, "condition_code");
  exactEnum(
    field(object, "availability_bucket"),
    AVAILABILITY_BUCKETS,
    "availability_bucket",
  );
  const countedQty = quantity(field(object, "counted_qty"), "counted_qty");
  if (countedQty === "0.000") invalid("现场观察实盘数量必须为正数");
  const verificationStatus = exactEnum(
    field(object, "verification_status"),
    ["verified", "pending_verification"] as const,
    "verification_status",
  );
  const materialId = nullableUuid(field(object, "material_id"), "material_id");
  const lotId = nullableUuid(field(object, "lot_id"), "lot_id");
  const lotNo = nullableTrimmedText(field(object, "lot_no_raw"), "lot_no_raw");
  const serialId = nullableUuid(field(object, "serial_id"), "serial_id");
  const serialNo = nullableTrimmedText(
    field(object, "serial_no_raw"),
    "serial_no_raw",
  );
  const rawSerialType = field(object, "serial_identifier_type");
  const serialType = rawSerialType === null
    ? null
    : exactEnum(rawSerialType, SERIAL_IDENTIFIER_TYPES, "serial_identifier_type");
  if (verificationStatus === "verified" && materialId === null) {
    invalid("已核实现场观察缺少正式物料主数据标识");
  }
  if (
    verificationStatus === "verified"
    && ((lotNo !== null && lotId === null) || (serialNo !== null && serialId === null))
  ) invalid("已核实现场观察仍包含未解析的批次或 SN 标识");
  if (lotId !== null && lotNo === null) {
    invalid("现场观察批次主数据缺少现场批次号");
  }
  if ((serialNo === null) !== (serialType === null) || (serialId !== null && serialNo === null)) {
    invalid("现场观察 SN 主数据与现场标识不一致");
  }
  if (serialNo !== null && countedQty !== "1.000") {
    invalid("带 SN 的现场观察实盘数量必须为 1.000");
  }
  const rawDisposition = field(object, "disposition");
  const disposition = rawDisposition === null
    ? null
    : validateObservationDispositionSummary(rawDisposition);
  const allowed = validateAllowedObservationDispositions(
    field(object, "allowed_dispositions"),
  );
  if (verificationStatus !== "pending_verification" && (disposition !== null || allowed.length > 0)) {
    invalid("已核实现场观察不得包含待核实处置或处置权限");
  }
  if (disposition !== null && allowed.length > 0) {
    invalid("已处置现场观察不得继续提供处置权限");
  }
  return object as OpeningStocktakeObservation;
}

function validateReview(value: unknown): OpeningStocktakeReviewSummary {
  const object = record(value);
  exactEnum(field(object, "stage"), ["region", "headquarters"] as const, "review_stage");
  exactEnum(field(object, "decision"), ["approve", "recount", "reject"] as const, "review_decision");
  timestamp(field(object, "reviewed_at"), "reviewed_at");
  return object as OpeningStocktakeReviewSummary;
}

export function validateOpeningStocktakeTaskPage(value: unknown): OpeningStocktakeTaskPage {
  const object = record(value);
  schemaVersion(object);
  const items = field(object, "items");
  if (!Array.isArray(items)) invalid("正式期初盘点任务列表不是数组");
  if (items.length > 20) invalid("正式期初盘点任务列表超过分页上限");
  const seen = new Set<string>();
  let previousTaskId: string | null = null;
  for (const item of items as unknown[]) {
    const checked = validateTaskSummary(item);
    const taskId = checked.task_id.toLowerCase();
    if (seen.has(taskId)) invalid("正式期初盘点任务列表包含重复任务");
    if (previousTaskId !== null && taskId <= previousTaskId) {
      invalid("正式期初盘点任务列表未按任务标识严格递增");
    }
    seen.add(taskId);
    previousTaskId = taskId;
  }
  const nextAfterId = nullableUuid(field(object, "next_after_id"), "next_after_id");
  if (nextAfterId !== null && (previousTaskId === null || nextAfterId !== previousTaskId)) {
    invalid("正式期初盘点下一页游标与本页末项不一致");
  }
  return object as OpeningStocktakeTaskPage;
}

export function validateOpeningStocktakeTaskDetail(value: unknown): OpeningStocktakeTaskDetail {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "task_id"), "task_id");
  text(field(object, "task_no"), "task_no");
  uuid(field(object, "region_org_id"), "region_org_id");
  const status = exactEnum(field(object, "status"), TASK_STATUSES, "task_status");
  if (typeof field(object, "blind_count") !== "boolean") invalid("盲盘标记无效");
  nonnegativeInteger(field(object, "task_version"), "task_version");
  timestamp(field(object, "deadline"), "deadline", true);
  timestamp(field(object, "cutoff_at"), "cutoff_at", true);
  const rawRound = field(object, "current_round");
  const currentRound = rawRound === null ? null : validateRound(rawRound);
  const evidenceStatus = exactEnum(
    field(object, "evidence_status"),
    EVIDENCE_STATUSES,
    "evidence_status",
  );
  validateSummaryEvidenceState(
    status,
    evidenceStatus,
    currentRound?.round_no || 0,
    currentRound?.status || null,
  );
  const scopes = field(object, "scopes");
  if (!Array.isArray(scopes)) invalid("正式期初盘点范围不是数组");
  const scopeIds = new Set<string>();
  for (const source of scopes as unknown[]) {
    const checked = validateScope(source, evidenceStatus);
    if (scopeIds.has(checked.scope_id.toLowerCase())) invalid("正式期初盘点包含重复范围");
    scopeIds.add(checked.scope_id.toLowerCase());
  }
  const differences = field(object, "differences");
  if (!Array.isArray(differences)) invalid("正式期初盘点差异不是数组");
  if (evidenceStatus !== "sealed" && differences.length > 0) {
    invalid("盘点证据封存前不得展示或推算账面数与差异数");
  }
  const differenceIds = new Set<string>();
  const differenceById = new Map<string, OpeningStocktakeDifference>();
  for (const source of differences as unknown[]) {
    const checked = validateDifference(source);
    if (checked.scope_id !== null && !scopeIds.has(checked.scope_id.toLowerCase())) {
      invalid("盘点差异不属于当前可见范围");
    }
    const differenceId = checked.difference_id.toLowerCase();
    if (differenceIds.has(differenceId)) invalid("正式期初盘点包含重复差异");
    differenceIds.add(differenceId);
    differenceById.set(differenceId, checked);
  }
  const rawObservations = field(object, "observations");
  if (!Array.isArray(rawObservations)) invalid("正式期初盘点现场观察不是数组");
  if (evidenceStatus !== "sealed" && rawObservations.length > 0) {
    invalid("盘点证据封存前不得展示现场观察证据");
  }
  const observationIds = new Set<string>();
  const observationNumbers = new Set<number>();
  const observedDifferenceIds = new Set<string>();
  const checkedObservations: OpeningStocktakeObservation[] = [];
  let previousObservationNo = 0;
  for (const source of rawObservations as unknown[]) {
    const checked = validateObservation(source);
    const observationId = checked.observation_id.toLowerCase();
    const differenceId = checked.difference_id.toLowerCase();
    const scopeId = checked.scope_id.toLowerCase();
    if (observationIds.has(observationId) || observationNumbers.has(checked.observation_no)) {
      invalid("正式期初盘点包含重复现场观察");
    }
    if (checked.observation_no <= previousObservationNo) {
      invalid("正式期初盘点现场观察未按编号严格递增");
    }
    previousObservationNo = checked.observation_no;
    observationIds.add(observationId);
    observationNumbers.add(checked.observation_no);
    if (!scopeIds.has(scopeId)) invalid("现场观察不属于当前可见范围");
    const difference = differenceById.get(differenceId);
    if (!difference) invalid("现场观察未绑定当前可见差异");
    if (difference.scope_id === null || difference.scope_id.toLowerCase() !== scopeId) {
      invalid("现场观察与差异的盘点范围不一致");
    }
    const expectedReason = checked.verification_status === "pending_verification"
      ? "opening_pending_verification"
      : "opening_unexpected_dimension";
    if (
      difference.difference_type !== "excess"
      || difference.book_qty !== "0.000"
      || difference.counted_qty !== checked.counted_qty
      || difference.difference_qty !== checked.counted_qty
      || difference.affected_qty !== checked.counted_qty
      || difference.evidence_required !== true
      || difference.reason_code !== expectedReason
      || !sameNullableUuid(checked.material_id, difference.material_id)
    ) invalid("现场观察与差异的数量、主数据或证据语义不一致");
    if (observedDifferenceIds.has(differenceId)) {
      invalid("多个现场观察绑定同一差异");
    }
    observedDifferenceIds.add(differenceId);
    checkedObservations.push(checked);
  }
  const reviews = field(object, "reviews");
  if (!Array.isArray(reviews)) invalid("正式期初盘点复核摘要不是数组");
  const stages = new Set<string>();
  for (const source of reviews as unknown[]) {
    const checked = validateReview(source);
    if (stages.has(checked.stage)) invalid("正式期初盘点包含重复阶段复核");
    stages.add(checked.stage);
  }
  if (checkedObservations.some((row) => row.allowed_dispositions.length > 0)) {
    if (
      status !== "submitted"
      || currentRound?.status !== "submitted"
      || stages.size > 0
    ) invalid("现场观察允许处置与任务、轮次或复核状态不一致");
  }
  const actions = allowedActions(field(object, "allowed_actions"));
  validateAllowedActionState(status, actions);
  if (
    actions.some((action) => action.startsWith("review_"))
    && checkedObservations.some((row) => (
      row.verification_status === "pending_verification"
      && row.disposition === null
    ))
  ) invalid("尚未处置的待核实现场观察不得进入复核");
  return object as OpeningStocktakeTaskDetail;
}

export function formalOpeningTaskPath(taskId: string): string {
  if (!UUID.test(taskId)) return invalid("正式期初盘点任务标识无效");
  return `${FORMAL_OPENING_STOCKTAKE_PATH}/${taskId.toLowerCase()}`;
}

export async function loadFormalOpeningStocktakes(
  afterId: string | null = null,
): Promise<OpeningStocktakeTaskPage> {
  const query = new URLSearchParams({ limit: "20" });
  if (afterId !== null) query.set("after_id", uuid(afterId, "after_id"));
  const page = validateOpeningStocktakeTaskPage(await api<unknown>(
    `${FORMAL_OPENING_STOCKTAKE_PATH}?${query.toString()}`,
  ));
  if (
    afterId !== null
    && page.items.length > 0
    && page.items[0].task_id.toLowerCase() <= afterId.toLowerCase()
  ) invalid("正式期初盘点下一页未越过请求游标");
  return page;
}

export async function loadFormalOpeningStocktakeDetail(
  taskId: string,
): Promise<OpeningStocktakeTaskDetail> {
  const taskPath = formalOpeningTaskPath(taskId);
  return exactVersionedDetail(
    validateOpeningStocktakeTaskDetail(await api<unknown>(taskPath)),
    taskId,
  );
}

function exactVersionedDetail<TDetail extends VersionedOpeningDetail>(
  detail: TDetail,
  taskId: string,
): TDetail {
  if (
    detail.task_id.toLowerCase() !== taskId.toLowerCase()
    || !Number.isSafeInteger(detail.task_version)
    || detail.task_version < 0
  ) return invalid("正式期初盘点详情与目标任务或版本不一致");
  return detail;
}

type OpeningMutationCoordinates = Pick<RequestInit, "headers">;
type OpeningMutationAction = OpeningAllowedAction | "dispose_observation";
type OpeningMutationIntent = {
  readonly taskId: string;
  readonly action: OpeningMutationAction;
  readonly path: string;
  readonly body: unknown;
  readonly signature: string;
  readonly coordinates: OpeningMutationCoordinates;
  readonly before: unknown;
  postAttempted: boolean;
  inFlight: boolean;
  accepted: boolean;
  acceptedResponse?: unknown;
};
const uncertainOpeningMutationIntents = new Map<string, OpeningMutationIntent>();
const activeOpeningMutationCalls = new Map<string, symbol>();
const MAX_UNCERTAIN_OPENING_INTENTS = 64;

export const __openingMutationIntentTestOnly = import.meta.env.MODE === "test"
  ? Object.freeze({
      reset(): void {
        uncertainOpeningMutationIntents.clear();
        activeOpeningMutationCalls.clear();
      },
      isDefinitiveRejection: isDefinitiveClientRejection,
    })
  : undefined;

function sortedJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedJsonValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sortedJsonValue(child)]),
  );
}

function canonicalJson(value: unknown): string {
  const transported = JSON.stringify(value);
  if (transported === undefined) invalid("正式期初盘点写入内容不可序列化");
  return JSON.stringify(sortedJsonValue(JSON.parse(transported)));
}

function openingIntentSignature(options: Readonly<{
  taskId: string;
  action: OpeningMutationAction;
  path: string;
  body: unknown;
}>): string {
  return canonicalJson({
    action: options.action,
    body: options.body,
    path: options.path,
    task_id: options.taskId.toLowerCase(),
  });
}

function taskIntentKey(taskId: string): string {
  return taskId.toLowerCase();
}

async function withOpeningMutationCall<TResult>(taskId: string, work: () => Promise<TResult>): Promise<TResult> {
  const key = uuid(taskId, "task_id");
  if (activeOpeningMutationCalls.has(key)) invalid("同一期初盘点写入正在核验，请勿重复提交");
  const invocation = Symbol();
  activeOpeningMutationCalls.set(key, invocation);
  try {
    return await work();
  } finally {
    if (activeOpeningMutationCalls.get(key) === invocation) activeOpeningMutationCalls.delete(key);
  }
}

function transportedJsonValue(value: unknown): unknown {
  const transported = JSON.stringify(value);
  if (transported === undefined) invalid("正式期初盘点写入内容不可序列化");
  return JSON.parse(transported);
}

function safeRequestId(coordinates: OpeningMutationCoordinates): string {
  try {
    const value = new Headers(coordinates.headers).get("X-Request-ID");
    return value && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
      ? value
      : "unavailable";
  } catch {
    return "unavailable";
  }
}

export class OpeningMutationHandoffError extends ApiError {
  readonly code = "opening_mutation_handoff_required";
  readonly task_id: string;
  readonly action: OpeningMutationAction;
  readonly path: string;
  readonly request_id: string;

  constructor(intent: OpeningMutationIntent, reason: "different_request" | "state_advanced") {
    const requestId = safeRequestId(intent.coordinates);
    super(
      409,
      `正式期初盘点存在未确认写入，已停止同任务新写；请人工核验 action=${intent.action} request_id=${requestId} reason=${reason}`,
    );
    this.name = "OpeningMutationHandoffError";
    this.task_id = intent.taskId;
    this.action = intent.action;
    this.path = intent.path;
    this.request_id = requestId;
  }
}

function openingMutationHandoff(
  intent: OpeningMutationIntent,
  reason: "different_request" | "state_advanced",
): never {
  throw new OpeningMutationHandoffError(intent, reason);
}

function openingIntentForTask(taskId: string): OpeningMutationIntent | undefined {
  return uncertainOpeningMutationIntents.get(taskIntentKey(taskId));
}

function exactPendingOpeningIntent(
  taskId: string,
  action: OpeningMutationAction,
  path: string,
  body: unknown,
): OpeningMutationIntent | undefined {
  const intent = openingIntentForTask(taskId);
  if (!intent) return undefined;
  const signature = openingIntentSignature({ taskId, action, path, body });
  if (intent.action !== action || intent.signature !== signature) {
    openingMutationHandoff(intent, "different_request");
  }
  return intent;
}

const NO_EFFECT_STATE_REJECTIONS: Readonly<Record<OpeningMutationAction, readonly string[]>> = {
  count: ["opening_count_state_invalid"],
  review_region: ["opening_review_stage_state_invalid"],
  review_headquarters: ["opening_review_stage_state_invalid"],
  open_recount: ["opening_recount_state_invalid"],
  dispose_observation: ["opening_observation_disposition_state_invalid"],
  post: ["opening_finalize_state_invalid"],
  close: ["opening_finalize_state_invalid", "opening_close_reconciliation_pending"],
};

function isDefinitiveClientRejection(error: unknown, action: OpeningMutationAction): boolean {
  if (!(error instanceof ApiError) || !error.responseReceived || !error.code) return false;
  // Only exact route/header or rolled-back domain rejections, never generic
  // 4xx, authentication failures, conflicts or database/evidence guard errors.
  if (error.status === 400 && error.category === "invalid_request") {
    return error.code === "idempotency_key_invalid" || error.code === "x_request_id_invalid";
  }
  return error.status === 412
    && error.category === "precondition_failed"
    && NO_EFFECT_STATE_REJECTIONS[action].includes(error.code);
}

async function executeOpeningMutationIntent<TResult>(options: Readonly<{
  taskId: string;
  action: OpeningMutationAction;
  prefix: string;
  path: string;
  body: unknown;
  before: unknown;
  accept: (value: unknown) => TResult;
  reread: () => Promise<unknown>;
}>): Promise<Readonly<{
  result: TResult;
  intent: OpeningMutationIntent;
  recoveredAcceptedResult: boolean;
}>> {
  const signature = openingIntentSignature(options);
  const taskKey = taskIntentKey(options.taskId);
  let intent = uncertainOpeningMutationIntents.get(taskKey);
  const createdForThisInvocation = !intent;
  if (intent && intent.signature !== signature) {
    openingMutationHandoff(intent, "different_request");
  }
  if (!intent) {
    if (uncertainOpeningMutationIntents.size >= MAX_UNCERTAIN_OPENING_INTENTS) {
      invalid("存在过多结果未确认的正式期初盘点写入，已停止创建新请求坐标");
    }
    intent = {
      taskId: options.taskId.toLowerCase(),
      action: options.action,
      path: options.path,
      body: transportedJsonValue(options.body),
      signature,
      coordinates: mutationHeaders(options.prefix),
      before: options.before,
      postAttempted: false,
      inFlight: false,
      accepted: false,
    };
    uncertainOpeningMutationIntents.set(taskKey, intent);
  }
  if (intent.inFlight) invalid("同一期初盘点写入正在核验，请勿重复提交");
  if (intent.accepted) {
    return {
      result: options.accept(transportedJsonValue(intent.acceptedResponse)),
      intent,
      recoveredAcceptedResult: true,
    };
  }
  const firstDirectPost = createdForThisInvocation && !intent.postAttempted;
  intent.postAttempted = true;
  intent.inFlight = true;
  let directPostRejected = false;
  let directPostRejection: unknown;
  try {
    const request = { method: "POST", ...intent.coordinates, ...jsonBody(intent.body) };
    let rawResponse: unknown;
    try {
      rawResponse = await api<unknown>(intent.path, request);
    } catch (error) {
      directPostRejected = true;
      directPostRejection = error;
      throw error;
    }
    const response = transportedJsonValue(rawResponse);
    const result = options.accept(transportedJsonValue(response));
    // A response is recoverable only after the caller's strict action/target/
    // result validator has accepted it.  Keep it with the original request
    // coordinates until a trustworthy detail can be returned to the UI.
    intent.acceptedResponse = response;
    intent.accepted = true;
    return { result, intent, recoveredAcceptedResult: false };
  } catch (error) {
    if (
      firstDirectPost && directPostRejected && error === directPostRejection
      && uncertainOpeningMutationIntents.get(taskKey) === intent
      && isDefinitiveClientRejection(error, intent.action)
    ) {
      uncertainOpeningMutationIntents.delete(taskKey);
    } else {
      // Transport failures, 5xx responses and malformed success payloads are
      // all uncertain.  Re-read before yielding and retain the coordinates so
      // only an exactly identical retry can reuse the same idempotency intent.
      try {
        await options.reread();
      } catch {
        // The original mutation uncertainty remains the actionable failure.
      }
    }
    throw error;
  } finally {
    intent.inFlight = false;
  }
}

function confirmOpeningMutationIntent(intent: OpeningMutationIntent): void {
  const taskKey = taskIntentKey(intent.taskId);
  if (uncertainOpeningMutationIntents.get(taskKey) === intent) {
    uncertainOpeningMutationIntents.delete(taskKey);
  }
}

/**
 * Execute one terminal transition against a freshly reread task version.
 *
 * Every new intent creates independent request coordinates; an uncertain
 * retry of the exact same intent reuses them.  The mutation is never accepted
 * as client truth until the exact task detail is read and validated again.
 */
type VersionedOpeningTerminalResult = Readonly<{
  task_id: string;
  resulting_task_status: "posted" | "closed";
  task_version: number;
  round_id?: string;
}>;

export async function executeVersionedOpeningTerminalAction<
  TDetail extends VersionedOpeningDetail,
  TResult extends VersionedOpeningTerminalResult,
>(options: Parameters<typeof executeVersionedOpeningTerminalActionUnlocked<TDetail, TResult>>[0])
  : Promise<VersionedOpeningMutationResult<TDetail, TResult>> {
  return withOpeningMutationCall(options.taskId, () => executeVersionedOpeningTerminalActionUnlocked(options));
}

async function executeVersionedOpeningTerminalActionUnlocked<
  TDetail extends VersionedOpeningDetail,
  TResult extends VersionedOpeningTerminalResult,
>(options: Readonly<{
  taskId: string;
  validateDetail: (value: unknown) => TDetail;
  validateResult: (value: unknown) => TResult;
} & (
  | Readonly<{
      action: "post";
      expectedRoundId: (detail: TDetail) => string;
    }>
  | Readonly<{
      action: "close";
      expectedRoundId?: never;
    }>
)>): Promise<VersionedOpeningMutationResult<TDetail, TResult>> {
  const taskPath = formalOpeningTaskPath(options.taskId);
  const pending = openingIntentForTask(options.taskId);
  let before: TDetail;
  if (pending) {
    if (pending.action !== options.action) {
      openingMutationHandoff(pending, "different_request");
    }
    before = pending.before as TDetail;
  } else {
    before = exactVersionedDetail(
      options.validateDetail(await api<unknown>(taskPath)),
      options.taskId,
    );
  }
  const body = { expected_version: before.task_version };
  const exactPending = exactPendingOpeningIntent(
    options.taskId,
    options.action,
    `${taskPath}/${options.action}`,
    body,
  );
  if (exactPending && !exactPending.accepted) {
    const current = exactVersionedDetail(
      options.validateDetail(await api<unknown>(taskPath)),
      options.taskId,
    );
    if (current.task_version !== before.task_version) {
      openingMutationHandoff(exactPending, "state_advanced");
    }
  }
  const attempt = await executeOpeningMutationIntent({
    taskId: options.taskId,
    action: options.action,
    prefix: `opening-${options.action}`,
    path: `${taskPath}/${options.action}`,
    body,
    before,
    accept: (value) => {
      const result = options.validateResult(value);
      const expectedStatus = options.action === "post" ? "posted" : "closed";
      if (
        typeof result.task_id !== "string"
        || result.task_id.toLowerCase() !== options.taskId.toLowerCase()
      ) invalid("正式期初通用终态写入结果与目标任务不一致");
      if (result.resulting_task_status !== expectedStatus) {
        invalid("正式期初通用终态写入结果与请求动作不一致");
      }
      if (
        !Number.isSafeInteger(result.task_version)
        || result.task_version !== before.task_version + 1
      ) invalid("正式期初通用终态写入结果与请求版本不一致");
      if (options.action === "post") {
        const expectedRoundId = uuid(options.expectedRoundId(before), "round_id");
        if (
          typeof result.round_id !== "string"
          || result.round_id.toLowerCase() !== expectedRoundId
        ) invalid("正式期初通用终态写入结果与目标轮次不一致");
      }
      return result;
    },
    reread: async () => exactVersionedDetail(
      options.validateDetail(await api<unknown>(taskPath)),
      options.taskId,
    ),
  });
  const detail = exactVersionedDetail(
    options.validateDetail(await api<unknown>(taskPath)),
    options.taskId,
  );
  confirmOpeningMutationIntent(attempt.intent);
  return { before, result: attempt.result, detail };
}

export function validateCountResult(value: unknown): OpeningCountResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "round_id"), "round_id");
  uuid(field(object, "scope_id"), "scope_id");
  const taskStatus = exactEnum(
    field(object, "task_status"),
    ["counting", ...SEALED_TASK_STATUSES] as const,
    "task_status",
  );
  const roundStatus = exactEnum(field(object, "round_status"), ROUND_STATUSES, "round_status");
  if (!booleanValue(field(object, "scope_completed"), "scope_completed")) {
    invalid("正式期初计数结果未完成目标范围");
  }
  const roundSealed = booleanValue(field(object, "round_sealed"), "round_sealed");
  if (roundSealed ? roundStatus !== "submitted" : roundStatus !== "counting") {
    invalid("正式期初计数结果的轮次封存状态不一致");
  }
  if (roundSealed ? taskStatus === "counting" : taskStatus !== "counting") {
    invalid("正式期初计数结果的任务状态不一致");
  }
  booleanValue(
    field(object, "has_pending_verification"),
    "has_pending_verification",
  );
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningCountResult;
}

function validateReviewResult(value: unknown): OpeningReviewResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "review_id"), "review_id");
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "round_id"), "round_id");
  const reviewStage = exactEnum(field(object, "review_stage"), ["region", "headquarters"] as const, "review_stage");
  const decision = exactEnum(field(object, "decision"), ["approve", "recount", "reject"] as const, "review_decision");
  const resultingStatus = exactEnum(
    field(object, "resulting_task_status"),
    ["hq_review", "approved", "recount_required"] as const,
    "resulting_task_status",
  );
  if (
    (reviewStage === "headquarters" && decision === "recount")
    || (decision === "approve" && resultingStatus !== (reviewStage === "region" ? "hq_review" : "approved"))
    || (decision !== "approve" && resultingStatus !== "recount_required")
  ) invalid("正式期初复核决定与结果状态不一致");
  nonnegativeInteger(field(object, "item_count"), "item_count");
  nonnegativeInteger(
    field(object, "pending_control_count"),
    "pending_control_count",
  );
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningReviewResult;
}

function validateRecountResult(value: unknown): OpeningRecountResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "recount_case_id"), "recount_case_id");
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "source_round_id"), "source_round_id");
  uuid(field(object, "next_round_id"), "next_round_id");
  const nextRoundNo = positiveInteger(field(object, "next_round_no"), "next_round_no");
  if (nextRoundNo < 2) invalid("正式期初复盘轮次编号无效");
  positiveInteger(field(object, "scope_count"), "scope_count");
  if (field(object, "resulting_task_status") !== "counting") {
    invalid("正式期初复盘结果状态无效");
  }
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningRecountResult;
}

function validateObservationDispositionResult(
  value: unknown,
): OpeningObservationDispositionResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "disposition_id"), "disposition_id");
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "round_id"), "round_id");
  uuid(field(object, "scope_id"), "scope_id");
  uuid(field(object, "observation_id"), "observation_id");
  const disposition = exactEnum(
    field(object, "disposition"),
    OBSERVATION_DISPOSITIONS,
    "observation_disposition",
  );
  const materialId = nullableUuid(
    field(object, "resolved_material_id"),
    "resolved_material_id",
  );
  const lotId = nullableUuid(field(object, "resolved_lot_id"), "resolved_lot_id");
  const serialId = nullableUuid(
    field(object, "resolved_serial_id"),
    "resolved_serial_id",
  );
  if (disposition === "resolved_existing_master") {
    if (materialId === null) invalid("绑定正式主数据的处置结果缺少物料标识");
  } else if (materialId !== null || lotId !== null || serialId !== null) {
    invalid("未绑定正式主数据的处置结果夹带主数据标识");
  }
  const manifest = field(object, "disposition_manifest_sha256");
  if (typeof manifest !== "string" || !/^[0-9a-f]{64}$/.test(manifest)) {
    invalid("现场观察处置结果清单摘要无效");
  }
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningObservationDispositionResult;
}

function validatePostResult(value: unknown): OpeningPostResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "round_id"), "round_id");
  uuid(field(object, "posting_id"), "posting_id");
  nullableUuid(field(object, "inventory_transaction_id"), "inventory_transaction_id");
  if (field(object, "resulting_task_status") !== "posted") {
    invalid("正式期初过账结果状态无效");
  }
  nonnegativeInteger(field(object, "task_version"), "task_version");
  quantity(field(object, "total_quantity"), "total_quantity");
  positiveInteger(field(object, "established_scope_count"), "established_scope_count");
  nonnegativeInteger(
    field(object, "pending_control_difference_count"),
    "pending_control_difference_count",
  );
  nonnegativeInteger(field(object, "ledger_cursor"), "ledger_cursor");
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningPostResult;
}

function validateCloseResult(value: unknown): OpeningCloseResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "task_id"), "task_id");
  uuid(field(object, "posting_id"), "posting_id");
  nullableUuid(field(object, "inventory_transaction_id"), "inventory_transaction_id");
  if (field(object, "resulting_task_status") !== "closed") {
    invalid("正式期初关闭结果状态无效");
  }
  nonnegativeInteger(field(object, "task_version"), "task_version");
  timestamp(field(object, "closed_at"), "closed_at");
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningCloseResult;
}

function requiredCurrentRound(
  detail: OpeningStocktakeTaskDetail,
): OpeningStocktakeRound {
  if (!detail.current_round) invalid("正式期初盘点尚无可操作轮次");
  return detail.current_round;
}

function requireAllowedAction(
  detail: OpeningStocktakeTaskDetail,
  action: OpeningAllowedAction,
): void {
  if (!detail.allowed_actions.includes(action)) {
    invalid("服务端未授权当前正式期初盘点操作");
  }
}

function exactMutationResultTarget(
  taskId: string,
  roundId: string | null,
  result: Readonly<{ task_id: string; round_id?: string; source_round_id?: string }>,
): void {
  if (result.task_id.toLowerCase() !== taskId.toLowerCase()) {
    invalid("正式期初盘点写入结果与目标任务不一致");
  }
  const resultRoundId = result.round_id ?? result.source_round_id;
  if (roundId !== null && resultRoundId?.toLowerCase() !== roundId.toLowerCase()) {
    invalid("正式期初盘点写入结果与目标轮次不一致");
  }
}

async function executeFormalOpeningWrite<TResult>(
  options: Parameters<typeof executeFormalOpeningWriteUnlocked<TResult>>[0],
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, TResult>> {
  return withOpeningMutationCall(options.taskId, () => executeFormalOpeningWriteUnlocked(options));
}

async function executeFormalOpeningWriteUnlocked<TResult>(options: Readonly<{
  taskId: string;
  action: OpeningAllowedAction;
  prefix: string;
  resolve: (detail: OpeningStocktakeTaskDetail) => Readonly<{
    path: string;
    body: unknown;
    roundId: string | null;
  }>;
  validateResult: (value: unknown) => TResult;
  resultTarget: (result: TResult) => Readonly<{
    task_id: string;
    round_id?: string;
    source_round_id?: string;
    task_version?: number;
  }>;
  validateAfter: (
    before: OpeningStocktakeTaskDetail,
    result: TResult,
    detail: OpeningStocktakeTaskDetail,
  ) => void;
}>): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, TResult>> {
  const pending = openingIntentForTask(options.taskId);
  let before: OpeningStocktakeTaskDetail;
  let target: ReturnType<typeof options.resolve>;
  if (pending) {
    if (pending.action !== options.action) {
      openingMutationHandoff(pending, "different_request");
    }
    before = pending.before as OpeningStocktakeTaskDetail;
    try {
      target = options.resolve(before);
      exactPendingOpeningIntent(
        options.taskId,
        options.action,
        target.path,
        target.body,
      );
    } catch (error) {
      if (error instanceof OpeningMutationHandoffError) throw error;
      openingMutationHandoff(pending, "different_request");
    }
    if (!pending.accepted) {
      const current = await loadFormalOpeningStocktakeDetail(options.taskId);
      try {
        requireAllowedAction(current, options.action);
        const currentTarget = options.resolve(current);
        if (openingIntentSignature({
          taskId: options.taskId,
          action: options.action,
          path: currentTarget.path,
          body: currentTarget.body,
        }) !== pending.signature) {
          openingMutationHandoff(pending, "state_advanced");
        }
      } catch (error) {
        if (error instanceof OpeningMutationHandoffError) throw error;
        openingMutationHandoff(pending, "state_advanced");
      }
    }
  } else {
    before = await loadFormalOpeningStocktakeDetail(options.taskId);
    requireAllowedAction(before, options.action);
    target = options.resolve(before);
  }
  const attempt = await executeOpeningMutationIntent({
    taskId: options.taskId,
    action: options.action,
    prefix: options.prefix,
    path: target.path,
    body: target.body,
    before,
    accept: (value) => {
      const checked = options.validateResult(value);
      const checkedTarget = options.resultTarget(checked);
      exactMutationResultTarget(
        options.taskId,
        target.roundId,
        checkedTarget,
      );
      if (
        (options.action === "post" || options.action === "close")
        && (
          checkedTarget.task_version !== before.task_version + 1
          || (target.body as { expected_version?: unknown }).expected_version
            !== before.task_version
        )
      ) invalid("正式期初终态写入结果与请求版本不一致");
      return checked;
    },
    reread: () => loadFormalOpeningStocktakeDetail(options.taskId),
  });
  const detail = await loadFormalOpeningStocktakeDetail(options.taskId);
  options.validateAfter(before, attempt.result, detail);
  confirmOpeningMutationIntent(attempt.intent);
  return { before, result: attempt.result, detail };
}

function laterRoundOrSame(
  before: OpeningStocktakeTaskDetail,
  detail: OpeningStocktakeTaskDetail,
  expectedRoundId: string,
): "same" | "later" {
  const beforeRound = requiredCurrentRound(before);
  const afterRound = requiredCurrentRound(detail);
  if (afterRound.round_id.toLowerCase() === expectedRoundId.toLowerCase()) return "same";
  if (afterRound.round_no > beforeRound.round_no) return "later";
  return invalid("正式期初盘点写后轮次未包含已确认结果");
}

function exactText(value: string, name: string, allowEmpty = false): string {
  if (
    typeof value !== "string"
    || value !== value.trim()
    || (!allowEmpty && !value)
  ) return invalid(`${name} 无效`);
  return value;
}

function inputUuid(value: unknown, name: string): string {
  if (
    typeof value !== "string"
    || value !== value.trim()
    || !UUID.test(value)
    || /^0{8}-0{4}-0{4}-0{4}-0{12}$/i.test(value)
  ) return invalid(`${name} 必须是正式主数据 UUID`);
  return value.toLowerCase();
}

function optionalInputUuid(value: unknown, name: string): string | null {
  return value === undefined || value === null || value === ""
    ? null
    : inputUuid(value, name);
}

export type OpeningReviewItemPolicy = Readonly<{
  decision: OpeningReviewItemDecision;
  comment_required: boolean;
  reason: string;
}>;

export function openingReviewItemPolicy(
  detail: OpeningStocktakeTaskDetail,
  difference: OpeningStocktakeDifference,
  decision: OpeningReviewTopDecision,
): OpeningReviewItemPolicy {
  if (difference.difference_type === "control_unassigned") {
    return {
      decision: "pending_verification",
      comment_required: true,
      reason: "OAM 控制差异必须填写待核实说明",
    };
  }
  const observation = detail.observations.find(
    (row) => row.difference_id.toLowerCase() === difference.difference_id.toLowerCase(),
  );
  const ordinaryDecision = ({
    approve: "accept_for_posting",
    recount: "recount",
    reject: "reject",
  } as const)[decision];
  if (!observation) {
    return { decision: ordinaryDecision, comment_required: false, reason: "" };
  }
  const round = requiredCurrentRound(detail);
  if (
    round.round_no > 1
    && round.round_type === "recount"
    && observation.verification_status === "verified"
  ) {
    return { decision: ordinaryDecision, comment_required: false, reason: "" };
  }
  if (observation.verification_status === "pending_verification") {
    const disposition = observation.disposition?.disposition;
    if (disposition === "pending_verification") {
      if (decision !== "recount" && decision !== "reject") {
        invalid("待核实现场观察只能进入复盘或驳回本轮");
      }
      return {
        decision: "pending_verification",
        comment_required: true,
        reason: "待核实现场观察必须填写保留待核实说明",
      };
    }
    if (
      disposition === "requires_recount"
      || disposition === "resolved_existing_master"
    ) {
      if (decision !== "recount") {
        invalid("已处置现场观察必须先进入独立复盘轮次");
      }
      return { decision: "recount", comment_required: false, reason: "" };
    }
    invalid("待核实现场观察必须先形成唯一处置事实");
  }
  if (round.round_no === 1) {
    if (decision !== "recount") {
      invalid("初盘现场观察必须先进入独立复盘轮次");
    }
    return { decision: "recount", comment_required: false, reason: "" };
  }
  invalid("现场观察不符合可终止复核的正式轮次语义");
}

function validateReviewItems(
  detail: OpeningStocktakeTaskDetail,
  items: readonly OpeningReviewItemInput[],
  decision: OpeningReviewTopDecision,
): void {
  const visibleDifferences = new Map(
    detail.differences.map((row) => [row.difference_id.toLowerCase(), row]),
  );
  const seen = new Set<string>();
  for (const item of items) {
    const differenceId = uuid(item.difference_id, "difference_id");
    const difference = visibleDifferences.get(differenceId);
    if (!difference) {
      invalid("复核项不属于当前已封存差异集");
    }
    if (seen.has(differenceId)) invalid("复核项包含重复差异");
    seen.add(differenceId);
    exactEnum(
      item.decision,
      ["accept_for_posting", "pending_verification", "recount", "reject"] as const,
      "review_item_decision",
    );
    const comment = exactText(item.comment, "复核项说明", true);
    const policy = openingReviewItemPolicy(detail, difference, decision);
    if (item.decision !== policy.decision) {
      invalid("逐项复核决定与差异类型或本级结论不一致");
    }
    if (policy.comment_required && !comment) {
      invalid(policy.reason);
    }
  }
  if (seen.size !== visibleDifferences.size) invalid("复核未逐项覆盖全部封存差异");
}

function sameNullableUuid(left: string | null, right: string | null): boolean {
  return left === null ? right === null : right?.toLowerCase() === left.toLowerCase();
}

function dispositionMatchesInput(
  disposition: OpeningObservationDispositionSummary,
  input: Readonly<{
    disposition: OpeningObservationDisposition;
    reason_code: string;
    resolved_material_id: string | null;
    resolved_lot_id: string | null;
    resolved_serial_id: string | null;
  }>,
): boolean {
  return disposition.disposition === input.disposition
    && disposition.reason_code === input.reason_code
    && sameNullableUuid(input.resolved_material_id, disposition.resolved_material_id)
    && sameNullableUuid(input.resolved_lot_id, disposition.resolved_lot_id)
    && sameNullableUuid(input.resolved_serial_id, disposition.resolved_serial_id);
}

export async function recordOpeningObservationDisposition(
  taskId: string,
  observationId: string,
  input: OpeningObservationDispositionInput,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningObservationDispositionResult>> {
  return withOpeningMutationCall(taskId, () => recordOpeningObservationDispositionUnlocked(taskId, observationId, input));
}

async function recordOpeningObservationDispositionUnlocked(
  taskId: string,
  observationId: string,
  input: OpeningObservationDispositionInput,
): Promise<VersionedOpeningMutationResult<
  OpeningStocktakeTaskDetail,
  OpeningObservationDispositionResult
>> {
  const checkedObservationId = inputUuid(observationId, "现场观察标识");
  const disposition = exactEnum(
    input.disposition,
    OBSERVATION_DISPOSITIONS,
    "observation_disposition",
  );
  const reasonCode = exactText(input.reason_code, "处置原因代码");
  const comment = exactText(input.comment, "处置说明", true);
  if (reasonCode.length > 80) invalid("处置原因代码超过 80 个字符");
  if (comment.length > 4000) invalid("处置说明超过 4000 个字符");
  const resolvedMaterialId = optionalInputUuid(
    input.resolved_material_id,
    "正式物料标识",
  );
  const resolvedLotId = optionalInputUuid(input.resolved_lot_id, "正式批次标识");
  const resolvedSerialId = optionalInputUuid(
    input.resolved_serial_id,
    "正式 SN 标识",
  );
  if (disposition === "resolved_existing_master") {
    if (resolvedMaterialId === null) invalid("绑定正式主数据必须填写物料 UUID");
  } else if (
    resolvedMaterialId !== null
    || resolvedLotId !== null
    || resolvedSerialId !== null
  ) {
    invalid("待核实或要求复盘处置不得填写主数据 UUID");
  } else if (!comment) {
    invalid("待核实或要求复盘处置必须填写说明");
  }
  const body = {
    disposition,
    reason_code: reasonCode,
    comment,
    resolved_material_id: resolvedMaterialId,
    resolved_lot_id: resolvedLotId,
    resolved_serial_id: resolvedSerialId,
  };
  const resolveTarget = (
    detail: OpeningStocktakeTaskDetail,
    requirePermission: boolean,
  ) => {
    const round = requiredCurrentRound(detail);
    if (detail.evidence_status !== "sealed" || round.status !== "submitted") {
      invalid("现场观察证据尚未封存，不能处置");
    }
    const observation = detail.observations.find(
      (row) => row.observation_id.toLowerCase() === checkedObservationId,
    );
    if (!observation) invalid("现场观察不属于当前可见盘点轮次");
    if (
      requirePermission
      && !observation.allowed_dispositions.includes(disposition)
    ) invalid("服务端未授权当前现场观察处置");
    return {
      round,
      observation,
      path: `${formalOpeningTaskPath(taskId)}/rounds/${round.round_id}/observations/${checkedObservationId}/disposition`,
    };
  };
  const pending = openingIntentForTask(taskId);
  let before: OpeningStocktakeTaskDetail;
  let target: ReturnType<typeof resolveTarget>;
  if (pending) {
    if (pending.action !== "dispose_observation") {
      openingMutationHandoff(pending, "different_request");
    }
    before = pending.before as OpeningStocktakeTaskDetail;
    try {
      target = resolveTarget(before, false);
      exactPendingOpeningIntent(
        taskId,
        "dispose_observation",
        target.path,
        body,
      );
    } catch (error) {
      if (error instanceof OpeningMutationHandoffError) throw error;
      openingMutationHandoff(pending, "different_request");
    }
    if (!pending.accepted) {
      const current = await loadFormalOpeningStocktakeDetail(taskId);
      try {
        const currentTarget = resolveTarget(current, true);
        if (openingIntentSignature({
          taskId,
          action: "dispose_observation",
          path: currentTarget.path,
          body,
        }) !== pending.signature) {
          openingMutationHandoff(pending, "state_advanced");
        }
      } catch (error) {
        if (error instanceof OpeningMutationHandoffError) throw error;
        openingMutationHandoff(pending, "state_advanced");
      }
    }
  } else {
    before = await loadFormalOpeningStocktakeDetail(taskId);
    target = resolveTarget(before, true);
  }
  const attempt = await executeOpeningMutationIntent({
    taskId,
    action: "dispose_observation",
    prefix: "opening-observation-disposition",
    path: target.path,
    body,
    before,
    accept: (value) => {
      const result = validateObservationDispositionResult(value);
      if (
        result.task_id.toLowerCase() !== taskId.toLowerCase()
        || result.round_id.toLowerCase() !== target.round.round_id.toLowerCase()
        || result.scope_id.toLowerCase() !== target.observation.scope_id.toLowerCase()
        || result.observation_id.toLowerCase() !== checkedObservationId
        || result.disposition !== disposition
        || !sameNullableUuid(resolvedMaterialId, result.resolved_material_id)
        || !sameNullableUuid(resolvedLotId, result.resolved_lot_id)
        || !sameNullableUuid(resolvedSerialId, result.resolved_serial_id)
      ) invalid("现场观察处置结果与精确请求目标不一致");
      return result;
    },
    reread: () => loadFormalOpeningStocktakeDetail(taskId),
  });
  const detail = await loadFormalOpeningStocktakeDetail(taskId);
  const afterRound = requiredCurrentRound(detail);
  const afterObservation = detail.observations.find(
    (row) => row.observation_id.toLowerCase() === checkedObservationId,
  );
  if (
    afterRound.round_id.toLowerCase() !== attempt.result.round_id.toLowerCase()
    || !afterObservation?.disposition
    || afterObservation.disposition.disposition_id.toLowerCase()
      !== attempt.result.disposition_id.toLowerCase()
    || !dispositionMatchesInput(afterObservation.disposition, body)
    || afterObservation.allowed_dispositions.length > 0
  ) invalid("现场观察处置写后详情未包含精确处置事实");
  confirmOpeningMutationIntent(attempt.intent);
  return { before, result: attempt.result, detail };
}

/**
 * Count/review/recount intentionally do not send a router-only task version.
 * Their domain services revalidate the exact task/round/scope anchors while
 * holding their locks, so different scopes can still be counted concurrently.
 * The client nevertheless binds every write to a fresh detail and rereads it.
 */
/** Shared preparation only; does not allocate an intent or perform transport. */
export function resolveOpeningScopeCount(
  taskId: string,
  scopeId: string,
  input: OpeningScopeCountInput,
  detail: OpeningStocktakeTaskDetail,
) {
  const round = requiredCurrentRound(detail);
  if (round.status !== "counting") invalid("当前盘点轮次不可计数");
  const checkedScopeId = uuid(scopeId, "scope_id");
  const scope = detail.scopes.find((row) => row.scope_id.toLowerCase() === checkedScopeId);
  if (!scope || !scope.assigned_to_me || scope.completion_status !== "pending") {
    invalid("当前人员不可提交该盘点范围");
  }
  if (typeof input.zero_confirmed !== "boolean") invalid("零库存确认标记无效");
  if (input.zero_confirmed && input.physical_observations.length > 0) {
    invalid("零库存确认不能同时提交实盘行");
  }
  if (!input.zero_confirmed && input.physical_observations.length === 0) {
    invalid("非零盘点必须提交至少一条实盘行");
  }
  const dimensions = new Set<string>();
  for (const row of input.physical_observations) {
    exactText(row.material_identifier_raw, "物料标识");
    if (
      typeof row.counted_qty !== "string"
      || !INPUT_QUANTITY.test(row.counted_qty)
      || ZERO_INPUT_QUANTITY.test(row.counted_qty)
    ) {
      invalid("实盘数量必须是 numeric(18,3) 范围内的正数且最多三位小数");
    }
    const hasSerial = row.serial_no_raw !== undefined && row.serial_no_raw !== null;
    if (hasSerial && !SERIAL_UNIT_QUANTITY.test(row.counted_qty)) {
      invalid("带 SN 的实盘行数量必须为一件");
    }
    // Only identical explicit raw/master dimensions are detected and rejected here. Do not
    // infer aliases or normalize source identifiers on the client.
    const dimension = JSON.stringify([
      row.material_identifier_raw,
      row.material_identifier_type,
      row.condition_code,
      row.availability_bucket,
      row.material_id ?? null,
      row.lot_id ?? null,
      row.lot_no_raw ?? null,
      row.serial_id ?? null,
      row.serial_no_raw ?? null,
      row.serial_identifier_type ?? null,
    ]);
    if (dimensions.has(dimension)) {
      invalid("同一现场维度必须合并数量后提交");
    }
    dimensions.add(dimension);
  }
  return {
    path: `${formalOpeningTaskPath(taskId)}/rounds/${round.round_id}/scopes/${checkedScopeId}/count`,
    body: input,
    roundId: round.round_id,
  };
}

/** @deprecated PC writes must use submitDurableOpeningScopeCount; this adapter has no cross-refresh recovery. */
export async function submitOpeningScopeCount(
  taskId: string,
  scopeId: string,
  input: OpeningScopeCountInput,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningCountResult>> {
  return executeFormalOpeningWrite({
    taskId,
    action: "count",
    prefix: "opening-count",
    resolve: (detail) => resolveOpeningScopeCount(taskId, scopeId, input, detail),
    validateResult: (value) => {
      const result = validateCountResult(value);
      if (result.scope_id.toLowerCase() !== scopeId.toLowerCase()) {
        invalid("正式期初计数结果与目标范围不一致");
      }
      return result;
    },
    resultTarget: (result) => result,
    validateAfter: (before, result, detail) => {
      if (laterRoundOrSame(before, detail, result.round_id) === "later") return;
      const scope = detail.scopes.find(
        (row) => row.scope_id.toLowerCase() === result.scope_id.toLowerCase(),
      );
      if (!scope || scope.completion_status !== "completed") {
        invalid("正式期初计数写后详情未包含范围完成证据");
      }
      if (
        (result.round_sealed && detail.evidence_status !== "sealed")
        || (!result.round_sealed && detail.current_round?.status === "counting" && detail.status !== "counting")
      ) invalid("正式期初计数写后详情与轮次结果不一致");
    },
  });
}

export async function submitOpeningRegionReview(
  taskId: string,
  input: OpeningRegionReviewInput,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningReviewResult>> {
  return executeFormalOpeningWrite({
    taskId,
    action: "review_region",
    prefix: "opening-review-region",
    resolve: (detail) => {
      const round = requiredCurrentRound(detail);
      if (detail.evidence_status !== "sealed") invalid("盘点证据未封存，不能区域复核");
      validateReviewItems(detail, input.items, input.decision);
      exactText(input.comment, "区域复核说明", input.decision === "approve");
      return {
        path: `${formalOpeningTaskPath(taskId)}/rounds/${round.round_id}/reviews/region`,
        body: input,
        roundId: round.round_id,
      };
    },
    validateResult: (value) => {
      const result = validateReviewResult(value);
      if (result.review_stage !== "region" || result.decision !== input.decision) {
        invalid("正式期初区域复核结果与请求不一致");
      }
      return result;
    },
    resultTarget: (result) => result,
    validateAfter: (before, result, detail) => {
      if (laterRoundOrSame(before, detail, result.round_id) === "later") return;
      if (!detail.reviews.some(
        (row) => row.stage === "region" && row.decision === result.decision,
      )) invalid("正式期初区域复核写后详情缺少本级复核结果");
    },
  });
}

export async function submitOpeningHeadquartersReview(
  taskId: string,
  input: OpeningHeadquartersReviewInput,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningReviewResult>> {
  return executeFormalOpeningWrite({
    taskId,
    action: "review_headquarters",
    prefix: "opening-review-headquarters",
    resolve: (detail) => {
      const round = requiredCurrentRound(detail);
      if (detail.evidence_status !== "sealed") invalid("盘点证据未封存，不能总部复核");
      validateReviewItems(detail, input.items, input.decision);
      exactText(input.comment, "总部复核说明", input.decision === "approve");
      return {
        path: `${formalOpeningTaskPath(taskId)}/rounds/${round.round_id}/reviews/headquarters`,
        body: input,
        roundId: round.round_id,
      };
    },
    validateResult: (value) => {
      const result = validateReviewResult(value);
      if (result.review_stage !== "headquarters" || result.decision !== input.decision) {
        invalid("正式期初总部复核结果与请求不一致");
      }
      return result;
    },
    resultTarget: (result) => result,
    validateAfter: (before, result, detail) => {
      if (laterRoundOrSame(before, detail, result.round_id) === "later") return;
      if (!detail.reviews.some(
        (row) => row.stage === "headquarters" && row.decision === result.decision,
      )) invalid("正式期初总部复核写后详情缺少本级复核结果");
    },
  });
}

export async function openOpeningRecount(
  taskId: string,
  input: OpeningRecountInput,
  expected?: Readonly<{ task_version: number; source_round_id: string }>,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningRecountResult>> {
  const selectedRoundId = expected === undefined ? undefined : uuid(expected.source_round_id, "source_round_id");
  const selectedVersion = expected?.task_version;
  if (expected !== undefined && (typeof selectedVersion !== "number" || !Number.isSafeInteger(selectedVersion) || selectedVersion < 0)) {
    invalid("复盘人员选择绑定的任务版本无效");
  }
  return executeFormalOpeningWrite({
    taskId,
    action: "open_recount",
    prefix: "opening-recount",
    resolve: (detail) => {
      const round = requiredCurrentRound(detail);
      if (expected !== undefined && (detail.task_version !== selectedVersion || round.round_id.toLowerCase() !== selectedRoundId)) {
        invalid("复盘人员选择绑定的任务版本或来源轮次已变化，请重新选择");
      }
      if (input.assignments.length === 0) invalid("复盘必须至少指派一个范围");
      const visibleScopes = new Set(detail.scopes.map((row) => row.scope_id.toLowerCase()));
      const seen = new Set<string>();
      for (const assignment of input.assignments) {
        const scopeId = uuid(assignment.scope_id, "scope_id");
        if (!visibleScopes.has(scopeId)) invalid("复盘范围不属于当前任务");
        if (seen.has(scopeId)) invalid("复盘包含重复范围");
        seen.add(scopeId);
        if (
          !/^[!-~]{1,36}$/.test(assignment.assignee_user_id)
          || assignment.assignee_user_id !== assignment.assignee_user_id.trim()
        ) invalid("复盘指派人员标识无效");
      }
      exactText(input.reason, "复盘原因");
      return {
        path: `${formalOpeningTaskPath(taskId)}/rounds/${round.round_id}/recount`,
        body: input,
        roundId: round.round_id,
      };
    },
    validateResult: validateRecountResult,
    resultTarget: (result) => result,
    validateAfter: (_before, result, detail) => {
      const round = requiredCurrentRound(detail);
      if (
        round.round_no < result.next_round_no
        || (round.round_no === result.next_round_no
          && round.round_id.toLowerCase() !== result.next_round_id.toLowerCase())
      ) invalid("正式期初复盘写后详情未包含新轮次");
    },
  });
}

export async function postOpeningStocktake(
  taskId: string,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningPostResult>> {
  return executeFormalOpeningWrite({
    taskId,
    action: "post",
    prefix: "opening-post",
    resolve: (detail) => ({
      path: `${formalOpeningTaskPath(taskId)}/post`,
      body: { expected_version: detail.task_version },
      roundId: requiredCurrentRound(detail).round_id,
    }),
    validateResult: validatePostResult,
    resultTarget: (result) => result,
    validateAfter: (_before, result, detail) => {
      if (
        !["posted", "closed"].includes(detail.status)
        || detail.task_version < result.task_version
        || requiredCurrentRound(detail).round_id.toLowerCase() !== result.round_id.toLowerCase()
      ) invalid("正式期初过账写后详情未包含过账结果");
    },
  });
}

export async function closeOpeningStocktake(
  taskId: string,
): Promise<VersionedOpeningMutationResult<OpeningStocktakeTaskDetail, OpeningCloseResult>> {
  return executeFormalOpeningWrite({
    taskId,
    action: "close",
    prefix: "opening-close",
    resolve: (detail) => ({
      path: `${formalOpeningTaskPath(taskId)}/close`,
      body: { expected_version: detail.task_version },
      roundId: null,
    }),
    validateResult: validateCloseResult,
    resultTarget: (result) => result,
    validateAfter: (_before, result, detail) => {
      if (detail.status !== "closed" || detail.task_version < result.task_version) {
        invalid("正式期初关闭写后详情未包含关闭结果");
      }
    },
  });
}
