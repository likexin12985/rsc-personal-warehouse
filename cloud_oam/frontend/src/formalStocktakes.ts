import { ApiError, mutationHeaders } from "./api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;
const SIGNED_QUANTITY = /^-?(?:0|[1-9]\d{0,14})\.\d{3}$/;
const INPUT_QUANTITY = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const SAFE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;

export const FORMAL_STOCKTAKE_SCHEMA_VERSION = "1.0" as const;
export const FORMAL_STOCKTAKE_ACTIONS = [
  "start",
  "submit_initial_count",
  "generate_initial_differences",
  "review_region",
  "review_headquarters",
  "open_recount",
  "submit_recount_count",
  "generate_recount_differences",
  "post",
  "reconcile",
  "close",
] as const;
export const FORMAL_STOCKTAKE_STATUSES = [
  "draft", "issued", "frozen", "counting", "submitted", "region_review",
  "hq_review", "approved", "recount_required", "posted", "closed", "cancelled",
] as const;

const TASK_TYPES = ["full", "sample", "ad_hoc", "personal", "termination"] as const;
const ROUND_STATUSES = ["counting", "submitted", "superseded"] as const;
const CONDITION_CODES = ["new", "used", "damaged", "scrapped"] as const;
const AVAILABILITY_BUCKETS = [
  "available", "reserved", "picking", "outbound", "in_transit", "arrived_pending",
  "frozen", "return_pending", "scrap_pending",
] as const;
const COUNT_METHODS = ["scan", "manual", "import"] as const;
const DIFFERENCE_TYPES = [
  "missing", "excess", "wrong_location", "wrong_condition", "wrong_lot", "wrong_serial",
] as const;
const REVIEW_DECISIONS = ["approve", "recount", "reject"] as const;
const REVIEW_ITEM_DECISIONS = [
  "accept_for_posting", "pending_verification", "no_adjustment", "recount", "reject",
] as const;

type TaskType = typeof TASK_TYPES[number];
type TaskStatus = typeof FORMAL_STOCKTAKE_STATUSES[number];
export type FormalStocktakeAllowedAction = typeof FORMAL_STOCKTAKE_ACTIONS[number];
export type FormalStocktakeCommandAction = "create_personal" | "create_managed" | FormalStocktakeAllowedAction;

export type FormalStocktakeStateAxes = Readonly<{
  count_status: "not_started" | "counting" | "submitted";
  difference_status: "not_ready" | "not_evaluated" | "evaluated" | "hidden_for_blind_counter";
  region_review_status: "not_ready" | "pending" | "approve" | "recount" | "reject";
  headquarters_review_status: "not_ready" | "pending" | "approve" | "recount" | "reject";
  recount_status: "not_required" | "required" | "counting" | "submitted";
  posting_status: "not_posted" | "recorded";
  reconciliation_status: "not_reconciled" | "recorded" | "stale";
  closure_status: "open" | "closed";
}>;

export type FormalStocktakeSummary = Readonly<{
  task_id: string;
  task_no: string;
  task_type: TaskType;
  region_org_id: string;
  status: TaskStatus;
  version: number;
  blind_count: boolean;
  current_round_no: number;
  current_round_status: typeof ROUND_STATUSES[number] | null;
  cutoff_ledger_cursor: number | null;
  cutoff_at: string | null;
  visible_scope_count: number;
  current_round_visible_completed_scope_count: number;
  freeze_status: "not_started" | "active" | "released" | "cancelled" | "mixed";
  state_axes: FormalStocktakeStateAxes;
  deadline: string | null;
  allowed_actions: readonly FormalStocktakeAllowedAction[];
}>;

export type FormalStocktakeSnapshotAccount = Readonly<{
  stock_account_id: string;
  material_id: string;
  condition_code: typeof CONDITION_CODES[number];
  availability_bucket: typeof AVAILABILITY_BUCKETS[number];
  lot_id: string | null;
  book_qty: string;
  expected_serial_ids: readonly string[];
}>;

export type FormalStocktakeScope = Readonly<{
  scope_id: string;
  scope_no: number;
  scope_mode: "location_all" | "filtered";
  owner_org_id: string;
  location_id: string;
  custodian_person_id_snapshot: string | null;
  material_id: string | null;
  condition_code: typeof CONDITION_CODES[number] | null;
  availability_bucket: typeof AVAILABILITY_BUCKETS[number] | null;
  assigned_to_me: boolean;
  freeze: null | Readonly<{
    freeze_id: string;
    freeze_mode: "hard" | "cutoff_replay";
    status: "active" | "released" | "cancelled";
    valid_from: string;
    valid_to: string | null;
    version: number;
  }>;
  snapshot_visibility: "not_started" | "hidden" | "visible";
  snapshot_accounts: readonly FormalStocktakeSnapshotAccount[];
  allowed_actions: readonly ("submit_initial_count" | "submit_recount_count")[];
}>;

export type FormalStocktakeDifference = Readonly<{
  difference_id: string;
  scope_id: string;
  difference_no: number;
  difference_type: typeof DIFFERENCE_TYPES[number];
  material_id: string | null;
  expected_account_id: string | null;
  observed_account_id: string | null;
  observed_line_id: string | null;
  serial_id: string | null;
  book_qty: string;
  counted_qty: string;
  difference_qty: string;
  affected_qty: string;
  reason_code: string | null;
  reason_text: string;
  evidence_required: boolean;
  posting_blocked_by_pending_verification: boolean;
}>;

export type FormalStocktakeReview = Readonly<{
  review_id: string;
  review_stage: "region" | "headquarters";
  decision: typeof REVIEW_DECISIONS[number];
  comment: string | null;
  comment_visible: boolean;
  reviewer_person_id: string;
  reviewed_at: string;
  visible_items: readonly Readonly<{
    difference_id: string;
    decision: typeof REVIEW_ITEM_DECISIONS[number];
    comment: string;
  }>[];
  covers_all_task_scopes: boolean;
}>;

export type FormalStocktakeRound = Readonly<{
  round_id: string;
  round_no: number;
  round_type: "initial" | "recount";
  status: typeof ROUND_STATUSES[number];
  started_at: string;
  submitted_at: string | null;
  submission: null | Record<string, unknown>;
  visible_scope_completions: readonly Record<string, unknown>[];
  visible_count_lines: readonly Record<string, unknown>[];
  visible_observations: readonly Record<string, unknown>[];
  differences_visible: boolean;
  difference_completion: null | Readonly<{
    completion_id: string;
    completed_at: string;
    visible_difference_count: number;
    visible_pending_verification_count: number;
    visible_total_affected_qty: string;
    covers_all_task_scopes: boolean;
  }>;
  visible_differences: readonly FormalStocktakeDifference[];
  region_review: FormalStocktakeReview | null;
  headquarters_review: FormalStocktakeReview | null;
  recount_cause: null | Readonly<{
    recount_case_id: string;
    source_round_id: string;
    source_difference_completion_id: string;
    trigger_review_id: string;
    next_round_no: number;
    visible_scope_count: number;
    covers_all_task_scopes: boolean;
    reason: string | null;
    reason_visible: boolean;
    opened_by_person_id: string;
    opened_at: string;
    assignments: readonly Record<string, unknown>[];
  }>;
  posting: Readonly<{
    status: "not_posted" | "recorded";
    posting_ids: readonly string[];
    posting_fact_count: number;
    visible_total_quantity: string;
    covers_all_task_scopes: boolean;
    inventory_transaction_count: number;
    first_posted_at: string | null;
    last_posted_at: string | null;
  }>;
  allowed_actions: readonly FormalStocktakeAllowedAction[];
}>;

export type FormalStocktakeCloseControl = Readonly<{
  latest_reconciliation: null | Readonly<{
    completion_id: string;
    reconciliation_no: number;
    reconciliation_ledger_cursor: number;
    reconciled_task_version: number;
    reconciled_at: string;
  }>;
  close_completion: null | Readonly<{
    completion_id: string;
    reconciliation_completion_id: string;
    closed_task_version: number;
    closed_at: string;
  }>;
}>;

export type FormalStocktakeDetail = Readonly<{
  schema_version: typeof FORMAL_STOCKTAKE_SCHEMA_VERSION;
  task_id: string;
  task_no: string;
  task_type: TaskType;
  region_org_id: string;
  status: TaskStatus;
  version: number;
  blind_count: boolean;
  current_round_no: number;
  cutoff_ledger_cursor: number | null;
  cutoff_at: string | null;
  issued_at: string | null;
  frozen_at: string | null;
  submitted_at: string | null;
  posted_at: string | null;
  closed_at: string | null;
  cancelled_at: string | null;
  deadline: string | null;
  note: string;
  state_axes: FormalStocktakeStateAxes;
  close_control: FormalStocktakeCloseControl;
  scopes: readonly FormalStocktakeScope[];
  rounds: readonly FormalStocktakeRound[];
  allowed_actions: readonly FormalStocktakeAllowedAction[];
}>;

export type FormalStocktakePage = Readonly<{
  schema_version: typeof FORMAL_STOCKTAKE_SCHEMA_VERSION;
  items: readonly FormalStocktakeSummary[];
  next_after_id: string | null;
}>;

export type StocktakeAccountCountInput = Readonly<{
  stock_account_id: string;
  counted_qty: string;
  count_method: typeof COUNT_METHODS[number];
  serial_ids: readonly string[];
  book_qty_confirmation: string | null;
  reason_code: string | null;
  remark: string;
}>;

export type StocktakeObservationInput = Readonly<{
  material_id: string | null;
  material_identifier_raw: string;
  material_identifier_type: "sku_code" | "qr_code" | "external_code" | "unknown";
  condition_code: typeof CONDITION_CODES[number];
  availability_bucket: typeof AVAILABILITY_BUCKETS[number];
  counted_qty: string;
  lot_id: string | null;
  lot_no_raw: string | null;
  serial_id: string | null;
  serial_no_raw: string | null;
  serial_identifier_type: "serial_no" | "qr_code" | "unknown" | null;
  count_method: typeof COUNT_METHODS[number];
  reason_code: string | null;
  remark: string;
}>;

export type StocktakeCountInput = Readonly<{
  count_mode: "blind" | "open";
  account_counts: readonly StocktakeAccountCountInput[];
  physical_observations: readonly StocktakeObservationInput[];
  evidence_file_ids: readonly string[];
  zero_confirmed: boolean;
}>;

export type FormalStocktakeCommandInput =
  | Readonly<{ action: "create_personal"; body: { blind_count: boolean; freeze_mode: "hard" | "cutoff_replay"; note: string } }>
  | Readonly<{ action: "create_managed"; body: { task_type: "full" | "sample" | "ad_hoc" | "termination"; region_org_id: string; blind_count: boolean; scopes: readonly { owner_org_id: string; location_id: string; assignee_person_id: string; scope_mode: "location_all" | "filtered"; material_id: string | null; condition_code: typeof CONDITION_CODES[number] | null; availability_bucket: typeof AVAILABILITY_BUCKETS[number] | null; freeze_mode: "hard" | "cutoff_replay" }[]; deadline: string | null; note: string } }>
  | Readonly<{ action: "start"; taskId: string; expectedTaskVersion: number; body: { expected_version: number } }>
  | Readonly<{ action: "submit_initial_count" | "submit_recount_count"; taskId: string; roundId: string; scopeId: string; expectedTaskVersion: number; body: StocktakeCountInput }>
  | Readonly<{ action: "generate_initial_differences" | "generate_recount_differences"; taskId: string; roundId: string; expectedTaskVersion: number; body: { expected_task_version: number } }>
  | Readonly<{ action: "review_region" | "review_headquarters"; taskId: string; roundId: string; expectedTaskVersion: number; body: { expected_task_version: number; decision: typeof REVIEW_DECISIONS[number]; items: readonly { difference_id: string; decision: typeof REVIEW_ITEM_DECISIONS[number]; comment: string }[]; comment: string } }>
  | Readonly<{ action: "open_recount"; taskId: string; roundId: string; expectedTaskVersion: number; body: { expected_task_version: number; assignments: readonly { scope_id: string; assignee_user_id: string }[]; reason: string } }>
  | Readonly<{ action: "post" | "reconcile" | "close"; taskId: string; expectedTaskVersion: number; body: { expected_task_version: number } }>;

export type FormalStocktakeIntent = Readonly<{
  action: FormalStocktakeCommandAction;
  method: "POST";
  path: string;
  body: Readonly<Record<string, unknown>>;
  headers: Readonly<{ "Idempotency-Key": string; "X-Request-ID": string }>;
  taskId: string | null;
  roundId: string | null;
  scopeId: string | null;
  expectedTaskVersion: number | null;
  signature: string;
}>;

function fail(message: string): never {
  throw new ApiError(409, message);
}

function objectValue(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(`${name}不是有效对象`);
  return value as Record<string, unknown>;
}

function exact(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  const object = objectValue(value, name);
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return fail(`${name}必须精确包含正式字段`);
  }
  return object;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) return fail(`${name}包含未知值`);
  return value as T;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) {
    return fail(`${name}无效`);
  }
  return value.toLowerCase();
}

function nullableUuid(value: unknown, name: string): string | null {
  return value === null ? null : uuid(value, name);
}

function integer(value: unknown, name: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) return fail(`${name}无效`);
  return value as number;
}

function booleanValue(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") return fail(`${name}无效`);
  return value;
}

function text(value: unknown, name: string, allowEmpty = true): string {
  if (typeof value !== "string" || value !== value.trim() || (!allowEmpty && !value)) {
    return fail(`${name}无效`);
  }
  return value;
}

function nullableText(value: unknown, name: string): string | null {
  return value === null ? null : text(value, name, false);
}

function timestamp(value: unknown, name: string): string {
  if (typeof value !== "string" || !AWARE_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) {
    return fail(`${name}无效`);
  }
  return value;
}

function nullableTimestamp(value: unknown, name: string): string | null {
  return value === null ? null : timestamp(value, name);
}

function quantity(value: unknown, name: string, signed = false): string {
  if (typeof value !== "string" || !(signed ? SIGNED_QUANTITY : QUANTITY).test(value)) {
    return fail(`${name}必须是固定三位小数文本`);
  }
  return value;
}

function uniqueArray<T>(value: unknown, parser: (row: unknown, index: number) => T, name: string, key: (row: T) => string = String): T[] {
  if (!Array.isArray(value)) return fail(`${name}必须是数组`);
  const rows = value.map(parser);
  const keys = rows.map(key);
  if (new Set(keys).size !== keys.length) return fail(`${name}包含重复项`);
  return rows;
}

function stateAxes(value: unknown): FormalStocktakeStateAxes {
  const row = exact(value, [
    "count_status", "difference_status", "region_review_status",
    "headquarters_review_status", "recount_status", "posting_status",
    "reconciliation_status", "closure_status",
  ], "盘点状态轴");
  return Object.freeze({
    count_status: oneOf(row.count_status, ["not_started", "counting", "submitted"], "count_status"),
    difference_status: oneOf(row.difference_status, ["not_ready", "not_evaluated", "evaluated", "hidden_for_blind_counter"], "difference_status"),
    region_review_status: oneOf(row.region_review_status, ["not_ready", "pending", ...REVIEW_DECISIONS], "region_review_status"),
    headquarters_review_status: oneOf(row.headquarters_review_status, ["not_ready", "pending", ...REVIEW_DECISIONS], "headquarters_review_status"),
    recount_status: oneOf(row.recount_status, ["not_required", "required", "counting", "submitted"], "recount_status"),
    posting_status: oneOf(row.posting_status, ["not_posted", "recorded"], "posting_status"),
    reconciliation_status: oneOf(row.reconciliation_status, ["not_reconciled", "recorded", "stale"], "reconciliation_status"),
    closure_status: oneOf(row.closure_status, ["open", "closed"], "closure_status"),
  });
}

function actions(value: unknown, allowed: readonly string[] = FORMAL_STOCKTAKE_ACTIONS): FormalStocktakeAllowedAction[] {
  return uniqueArray(value, (row) => oneOf(row, allowed, "allowed_action") as FormalStocktakeAllowedAction, "allowed_actions");
}

function summary(value: unknown): FormalStocktakeSummary {
  const row = exact(value, [
    "task_id", "task_no", "task_type", "region_org_id", "status", "version", "blind_count",
    "current_round_no", "current_round_status", "cutoff_ledger_cursor", "cutoff_at",
    "visible_scope_count", "current_round_visible_completed_scope_count", "freeze_status",
    "state_axes", "deadline", "allowed_actions",
  ], "盘点任务摘要");
  const cursor = row.cutoff_ledger_cursor === null ? null : integer(row.cutoff_ledger_cursor, "cutoff_ledger_cursor");
  const cutoffAt = nullableTimestamp(row.cutoff_at, "cutoff_at");
  if ((cursor === null) !== (cutoffAt === null)) fail("盘点截止游标与时间必须同时存在");
  const visibleScopeCount = integer(row.visible_scope_count, "visible_scope_count", 1);
  const completed = integer(row.current_round_visible_completed_scope_count, "completed_scope_count");
  if (completed > visibleScopeCount) fail("已完成范围数不能超过可见范围数");
  const status = oneOf(row.status, FORMAL_STOCKTAKE_STATUSES, "status");
  const axes = stateAxes(row.state_axes);
  const allowed = actions(row.allowed_actions);
  if (status === "closed" && (axes.posting_status !== "recorded" || axes.reconciliation_status !== "recorded" || axes.closure_status !== "closed" || allowed.length)) fail("已关闭盘点摘要必须是无写动作的完整终态");
  if (status === "posted" && (axes.posting_status !== "recorded" || axes.closure_status !== "open")) fail("已过账盘点摘要状态轴不完整");
  if (status === "posted" && allowed.some((action) => action !== "reconcile" && action !== "close")) fail("已过账盘点摘要只能开放对账或关闭动作");
  if (status !== "posted" && status !== "closed" && (axes.posting_status !== "not_posted" || axes.reconciliation_status !== "not_reconciled" || axes.closure_status !== "open" || allowed.includes("reconcile") || allowed.includes("close"))) fail("非终态盘点摘要不得暴露终态事实或动作");
  if (allowed.includes("close") && (status !== "posted" || axes.reconciliation_status !== "recorded")) fail("关闭动作只能由当前有效内部对账开放");
  if (allowed.includes("reconcile") && status !== "posted") fail("内部对账动作只能由已过账任务开放");
  return Object.freeze({
    task_id: uuid(row.task_id, "task_id"), task_no: text(row.task_no, "task_no", false),
    task_type: oneOf(row.task_type, TASK_TYPES, "task_type"), region_org_id: uuid(row.region_org_id, "region_org_id"),
    status, version: integer(row.version, "version"),
    blind_count: booleanValue(row.blind_count, "blind_count"), current_round_no: integer(row.current_round_no, "current_round_no"),
    current_round_status: row.current_round_status === null ? null : oneOf(row.current_round_status, ROUND_STATUSES, "current_round_status"),
    cutoff_ledger_cursor: cursor, cutoff_at: cutoffAt, visible_scope_count: visibleScopeCount,
    current_round_visible_completed_scope_count: completed,
    freeze_status: oneOf(row.freeze_status, ["not_started", "active", "released", "cancelled", "mixed"], "freeze_status"),
    state_axes: axes, deadline: nullableTimestamp(row.deadline, "deadline"),
    allowed_actions: Object.freeze(allowed),
  });
}

export function validateFormalStocktakePage(value: unknown): FormalStocktakePage {
  const row = exact(value, ["schema_version", "items", "next_after_id"], "正式盘点列表");
  if (row.schema_version !== FORMAL_STOCKTAKE_SCHEMA_VERSION) fail("正式盘点 schema_version 不受支持");
  return Object.freeze({
    schema_version: FORMAL_STOCKTAKE_SCHEMA_VERSION,
    items: Object.freeze(uniqueArray(row.items, summary, "盘点任务", (item) => item.task_id)),
    next_after_id: nullableUuid(row.next_after_id, "next_after_id"),
  });
}

function freezeFact(value: unknown): FormalStocktakeScope["freeze"] {
  if (value === null) return null;
  const row = exact(value, ["freeze_id", "freeze_mode", "status", "valid_from", "valid_to", "version"], "冻结事实");
  const status = oneOf(row.status, ["active", "released", "cancelled"], "freeze.status");
  const validFrom = timestamp(row.valid_from, "freeze.valid_from");
  const validTo = nullableTimestamp(row.valid_to, "freeze.valid_to");
  if ((status === "active") !== (validTo === null)) fail("冻结状态与有效期不一致");
  if (validTo && Date.parse(validTo) <= Date.parse(validFrom)) fail("冻结结束时间无效");
  return Object.freeze({ freeze_id: uuid(row.freeze_id, "freeze_id"), freeze_mode: oneOf(row.freeze_mode, ["hard", "cutoff_replay"], "freeze_mode"), status, valid_from: validFrom, valid_to: validTo, version: integer(row.version, "freeze.version") });
}

function snapshotAccount(value: unknown): FormalStocktakeSnapshotAccount {
  const row = exact(value, ["stock_account_id", "material_id", "condition_code", "availability_bucket", "lot_id", "book_qty", "expected_serial_ids"], "账面账户快照");
  return Object.freeze({
    stock_account_id: uuid(row.stock_account_id, "stock_account_id"), material_id: uuid(row.material_id, "material_id"),
    condition_code: oneOf(row.condition_code, CONDITION_CODES, "condition_code"), availability_bucket: oneOf(row.availability_bucket, AVAILABILITY_BUCKETS, "availability_bucket"),
    lot_id: nullableUuid(row.lot_id, "lot_id"), book_qty: quantity(row.book_qty, "book_qty"),
    expected_serial_ids: Object.freeze(uniqueArray(row.expected_serial_ids, (id) => uuid(id, "expected_serial_id"), "expected_serial_ids")),
  });
}

function scope(value: unknown): FormalStocktakeScope {
  const row = exact(value, [
    "scope_id", "scope_no", "scope_mode", "owner_org_id", "location_id", "custodian_person_id_snapshot",
    "material_id", "condition_code", "availability_bucket", "assigned_to_me", "freeze",
    "snapshot_visibility", "snapshot_accounts", "allowed_actions",
  ], "盘点范围");
  const visibility = oneOf(row.snapshot_visibility, ["not_started", "hidden", "visible"], "snapshot_visibility");
  const accounts = uniqueArray(row.snapshot_accounts, snapshotAccount, "snapshot_accounts", (item) => item.stock_account_id);
  if (visibility !== "visible" && accounts.length) fail("隐藏账面不能返回账户快照");
  return Object.freeze({
    scope_id: uuid(row.scope_id, "scope_id"), scope_no: integer(row.scope_no, "scope_no", 1),
    scope_mode: oneOf(row.scope_mode, ["location_all", "filtered"], "scope_mode"), owner_org_id: uuid(row.owner_org_id, "owner_org_id"),
    location_id: uuid(row.location_id, "location_id"), custodian_person_id_snapshot: nullableUuid(row.custodian_person_id_snapshot, "custodian_person_id_snapshot"),
    material_id: nullableUuid(row.material_id, "material_id"), condition_code: row.condition_code === null ? null : oneOf(row.condition_code, CONDITION_CODES, "condition_code"),
    availability_bucket: row.availability_bucket === null ? null : oneOf(row.availability_bucket, AVAILABILITY_BUCKETS, "availability_bucket"),
    assigned_to_me: booleanValue(row.assigned_to_me, "assigned_to_me"), freeze: freezeFact(row.freeze), snapshot_visibility: visibility,
    snapshot_accounts: Object.freeze(accounts),
    allowed_actions: Object.freeze(actions(row.allowed_actions, ["submit_initial_count", "submit_recount_count"]) as ("submit_initial_count" | "submit_recount_count")[]),
  });
}

function scopeCompletion(value: unknown): Record<string, unknown> {
  const row = exact(value, ["completion_id", "scope_id", "count_ledger_cursor", "count_line_count", "observation_line_count", "serial_count", "total_counted_qty", "zero_confirmed", "completed_by_person_id", "completed_at"], "范围计数完成事实");
  return Object.freeze({ completion_id: uuid(row.completion_id, "completion_id"), scope_id: uuid(row.scope_id, "scope_id"), count_ledger_cursor: row.count_ledger_cursor === null ? null : integer(row.count_ledger_cursor, "count_ledger_cursor"), count_line_count: integer(row.count_line_count, "count_line_count"), observation_line_count: integer(row.observation_line_count, "observation_line_count"), serial_count: integer(row.serial_count, "serial_count"), total_counted_qty: quantity(row.total_counted_qty, "total_counted_qty"), zero_confirmed: booleanValue(row.zero_confirmed, "zero_confirmed"), completed_by_person_id: uuid(row.completed_by_person_id, "completed_by_person_id"), completed_at: timestamp(row.completed_at, "completed_at") });
}

function countLine(value: unknown): Record<string, unknown> {
  const row = exact(value, ["count_line_id", "scope_id", "stock_account_id", "material_id", "counted_qty", "count_method", "reason_code", "remark", "counted_by_me", "counted_at", "counted_serial_ids", "book_qty", "expected_serial_ids"], "盘点计数行");
  const bookQty = row.book_qty === null ? null : quantity(row.book_qty, "count_line.book_qty");
  const expected = row.expected_serial_ids === null ? null : uniqueArray(row.expected_serial_ids, (id) => uuid(id, "expected_serial_id"), "expected_serial_ids");
  if ((bookQty === null) !== (expected === null)) fail("计数行账面数量与预期序列号必须同时隐藏");
  return Object.freeze({ count_line_id: uuid(row.count_line_id, "count_line_id"), scope_id: uuid(row.scope_id, "scope_id"), stock_account_id: uuid(row.stock_account_id, "stock_account_id"), material_id: uuid(row.material_id, "material_id"), counted_qty: quantity(row.counted_qty, "counted_qty"), count_method: oneOf(row.count_method, COUNT_METHODS, "count_method"), reason_code: nullableText(row.reason_code, "reason_code"), remark: text(row.remark, "remark"), counted_by_me: booleanValue(row.counted_by_me, "counted_by_me"), counted_at: timestamp(row.counted_at, "counted_at"), counted_serial_ids: Object.freeze(uniqueArray(row.counted_serial_ids, (id) => uuid(id, "counted_serial_id"), "counted_serial_ids")), book_qty: bookQty, expected_serial_ids: expected === null ? null : Object.freeze(expected) });
}

function disposition(value: unknown): Record<string, unknown> | null {
  if (value === null) return null;
  const row = exact(value, ["disposition_id", "disposition", "resolved_material_id", "resolved_lot_id", "resolved_serial_id", "reason_code", "comment", "decided_at"], "观察处置事实");
  return Object.freeze({ disposition_id: uuid(row.disposition_id, "disposition_id"), disposition: oneOf(row.disposition, ["resolved_existing_master", "pending_verification", "requires_recount"], "disposition"), resolved_material_id: nullableUuid(row.resolved_material_id, "resolved_material_id"), resolved_lot_id: nullableUuid(row.resolved_lot_id, "resolved_lot_id"), resolved_serial_id: nullableUuid(row.resolved_serial_id, "resolved_serial_id"), reason_code: text(row.reason_code, "reason_code", false), comment: text(row.comment, "comment"), decided_at: timestamp(row.decided_at, "decided_at") });
}

function observation(value: unknown): Record<string, unknown> {
  const row = exact(value, ["observation_id", "scope_id", "observation_no", "owner_org_id", "location_id", "custodian_person_id_snapshot", "material_id", "material_identifier_raw", "material_identifier_type", "condition_code", "availability_bucket", "lot_id", "lot_no_raw", "serial_id", "serial_no_raw", "serial_identifier_type", "counted_qty", "verification_status", "requires_verification", "count_method", "reason_code", "remark", "counted_by_me", "counted_at", "disposition"], "实物观察行");
  const verification = oneOf(row.verification_status, ["verified", "pending_verification"], "verification_status");
  const required = booleanValue(row.requires_verification, "requires_verification");
  if (required !== (verification === "pending_verification")) fail("观察行核验标记与状态不一致");
  const serialNo = nullableText(row.serial_no_raw, "serial_no_raw");
  const serialType = row.serial_identifier_type === null ? null : oneOf(row.serial_identifier_type, ["serial_no", "qr_code", "unknown"], "serial_identifier_type");
  if ((serialNo === null) !== (serialType === null)) fail("观察行 SN 与类型必须成对出现");
  return Object.freeze({ observation_id: uuid(row.observation_id, "observation_id"), scope_id: uuid(row.scope_id, "scope_id"), observation_no: integer(row.observation_no, "observation_no", 1), owner_org_id: uuid(row.owner_org_id, "owner_org_id"), location_id: uuid(row.location_id, "location_id"), custodian_person_id_snapshot: nullableUuid(row.custodian_person_id_snapshot, "custodian_person_id_snapshot"), material_id: nullableUuid(row.material_id, "material_id"), material_identifier_raw: text(row.material_identifier_raw, "material_identifier_raw", false), material_identifier_type: oneOf(row.material_identifier_type, ["sku_code", "qr_code", "external_code", "unknown"], "material_identifier_type"), condition_code: oneOf(row.condition_code, CONDITION_CODES, "condition_code"), availability_bucket: oneOf(row.availability_bucket, AVAILABILITY_BUCKETS, "availability_bucket"), lot_id: nullableUuid(row.lot_id, "lot_id"), lot_no_raw: nullableText(row.lot_no_raw, "lot_no_raw"), serial_id: nullableUuid(row.serial_id, "serial_id"), serial_no_raw: serialNo, serial_identifier_type: serialType, counted_qty: quantity(row.counted_qty, "counted_qty"), verification_status: verification, requires_verification: required, count_method: oneOf(row.count_method, COUNT_METHODS, "count_method"), reason_code: nullableText(row.reason_code, "reason_code"), remark: text(row.remark, "remark"), counted_by_me: booleanValue(row.counted_by_me, "counted_by_me"), counted_at: timestamp(row.counted_at, "counted_at"), disposition: disposition(row.disposition) });
}

function difference(value: unknown): FormalStocktakeDifference {
  const row = exact(value, ["difference_id", "scope_id", "difference_no", "difference_type", "material_id", "expected_account_id", "observed_account_id", "observed_line_id", "serial_id", "book_qty", "counted_qty", "difference_qty", "affected_qty", "reason_code", "reason_text", "evidence_required", "posting_blocked_by_pending_verification"], "盘点差异");
  return Object.freeze({ difference_id: uuid(row.difference_id, "difference_id"), scope_id: uuid(row.scope_id, "scope_id"), difference_no: integer(row.difference_no, "difference_no", 1), difference_type: oneOf(row.difference_type, DIFFERENCE_TYPES, "difference_type"), material_id: nullableUuid(row.material_id, "material_id"), expected_account_id: nullableUuid(row.expected_account_id, "expected_account_id"), observed_account_id: nullableUuid(row.observed_account_id, "observed_account_id"), observed_line_id: nullableUuid(row.observed_line_id, "observed_line_id"), serial_id: nullableUuid(row.serial_id, "serial_id"), book_qty: quantity(row.book_qty, "book_qty"), counted_qty: quantity(row.counted_qty, "counted_qty"), difference_qty: quantity(row.difference_qty, "difference_qty", true), affected_qty: quantity(row.affected_qty, "affected_qty"), reason_code: nullableText(row.reason_code, "reason_code"), reason_text: text(row.reason_text, "reason_text"), evidence_required: booleanValue(row.evidence_required, "evidence_required"), posting_blocked_by_pending_verification: booleanValue(row.posting_blocked_by_pending_verification, "posting_blocked_by_pending_verification") });
}

function review(value: unknown): FormalStocktakeReview | null {
  if (value === null) return null;
  const row = exact(value, ["review_id", "review_stage", "decision", "comment", "comment_visible", "reviewer_person_id", "reviewed_at", "visible_items", "covers_all_task_scopes"], "盘点复核事实");
  const comment = row.comment === null ? null : text(row.comment, "review.comment");
  const visible = booleanValue(row.comment_visible, "comment_visible");
  if (visible !== (comment !== null)) fail("复核意见可见性与内容不一致");
  const items = uniqueArray(row.visible_items, (value) => {
    const item = exact(value, ["difference_id", "decision", "comment"], "复核逐项决定");
    return Object.freeze({ difference_id: uuid(item.difference_id, "difference_id"), decision: oneOf(item.decision, REVIEW_ITEM_DECISIONS, "item.decision"), comment: text(item.comment, "item.comment") });
  }, "visible_items", (item) => item.difference_id);
  return Object.freeze({ review_id: uuid(row.review_id, "review_id"), review_stage: oneOf(row.review_stage, ["region", "headquarters"], "review_stage"), decision: oneOf(row.decision, REVIEW_DECISIONS, "review.decision"), comment, comment_visible: visible, reviewer_person_id: uuid(row.reviewer_person_id, "reviewer_person_id"), reviewed_at: timestamp(row.reviewed_at, "reviewed_at"), visible_items: Object.freeze(items), covers_all_task_scopes: booleanValue(row.covers_all_task_scopes, "covers_all_task_scopes") });
}

function submission(value: unknown): Record<string, unknown> | null {
  if (value === null) return null;
  const row = exact(value, ["submission_id", "submitted_at", "visible_scope_count", "visible_zero_scope_count", "visible_count_line_count", "visible_observation_line_count", "visible_serial_count", "visible_total_counted_qty", "covers_all_task_scopes"], "轮次提交事实");
  const scopeCount = integer(row.visible_scope_count, "visible_scope_count");
  const zeroCount = integer(row.visible_zero_scope_count, "visible_zero_scope_count");
  if (zeroCount > scopeCount) fail("零库存范围数不能超过范围数");
  return Object.freeze({ submission_id: uuid(row.submission_id, "submission_id"), submitted_at: timestamp(row.submitted_at, "submitted_at"), visible_scope_count: scopeCount, visible_zero_scope_count: zeroCount, visible_count_line_count: integer(row.visible_count_line_count, "visible_count_line_count"), visible_observation_line_count: integer(row.visible_observation_line_count, "visible_observation_line_count"), visible_serial_count: integer(row.visible_serial_count, "visible_serial_count"), visible_total_counted_qty: quantity(row.visible_total_counted_qty, "visible_total_counted_qty"), covers_all_task_scopes: booleanValue(row.covers_all_task_scopes, "covers_all_task_scopes") });
}

function differenceCompletion(value: unknown): FormalStocktakeRound["difference_completion"] {
  if (value === null) return null;
  const row = exact(value, ["completion_id", "completed_at", "visible_difference_count", "visible_pending_verification_count", "visible_total_affected_qty", "covers_all_task_scopes"], "差异完成事实");
  const count = integer(row.visible_difference_count, "visible_difference_count");
  const pending = integer(row.visible_pending_verification_count, "visible_pending_verification_count");
  if (pending > count) fail("待核验差异数不能超过差异总数");
  return Object.freeze({ completion_id: uuid(row.completion_id, "completion_id"), completed_at: timestamp(row.completed_at, "completed_at"), visible_difference_count: count, visible_pending_verification_count: pending, visible_total_affected_qty: quantity(row.visible_total_affected_qty, "visible_total_affected_qty"), covers_all_task_scopes: booleanValue(row.covers_all_task_scopes, "covers_all_task_scopes") });
}

function recountCause(value: unknown): FormalStocktakeRound["recount_cause"] {
  if (value === null) return null;
  const row = exact(value, ["recount_case_id", "source_round_id", "source_difference_completion_id", "trigger_review_id", "next_round_no", "visible_scope_count", "covers_all_task_scopes", "reason", "reason_visible", "opened_by_person_id", "opened_at", "assignments"], "复盘原因事实");
  const reason = row.reason === null ? null : text(row.reason, "recount.reason", false);
  const reasonVisible = booleanValue(row.reason_visible, "reason_visible");
  if (reasonVisible !== (reason !== null)) fail("复盘原因可见性与内容不一致");
  const assignments = uniqueArray(row.assignments, (value) => {
    const item = exact(value, ["assignment_id", "scope_id", "assignee_person_id", "assigned_to_me", "assigned_at"], "复盘分配事实");
    return Object.freeze({ assignment_id: uuid(item.assignment_id, "assignment_id"), scope_id: uuid(item.scope_id, "scope_id"), assignee_person_id: uuid(item.assignee_person_id, "assignee_person_id"), assigned_to_me: booleanValue(item.assigned_to_me, "assigned_to_me"), assigned_at: timestamp(item.assigned_at, "assigned_at") });
  }, "recount.assignments", (item) => String(item.scope_id));
  return Object.freeze({ recount_case_id: uuid(row.recount_case_id, "recount_case_id"), source_round_id: uuid(row.source_round_id, "source_round_id"), source_difference_completion_id: uuid(row.source_difference_completion_id, "source_difference_completion_id"), trigger_review_id: uuid(row.trigger_review_id, "trigger_review_id"), next_round_no: integer(row.next_round_no, "next_round_no", 2), visible_scope_count: integer(row.visible_scope_count, "visible_scope_count"), covers_all_task_scopes: booleanValue(row.covers_all_task_scopes, "covers_all_task_scopes"), reason, reason_visible: reasonVisible, opened_by_person_id: uuid(row.opened_by_person_id, "opened_by_person_id"), opened_at: timestamp(row.opened_at, "opened_at"), assignments: Object.freeze(assignments) });
}

function posting(value: unknown): FormalStocktakeRound["posting"] {
  const row = exact(value, ["status", "posting_ids", "posting_fact_count", "visible_total_quantity", "covers_all_task_scopes", "inventory_transaction_count", "first_posted_at", "last_posted_at"], "盘点过账事实");
  const status = oneOf(row.status, ["not_posted", "recorded"], "posting.status");
  const ids = uniqueArray(row.posting_ids, (id) => uuid(id, "posting_id"), "posting_ids");
  const count = integer(row.posting_fact_count, "posting_fact_count");
  const first = nullableTimestamp(row.first_posted_at, "first_posted_at");
  const last = nullableTimestamp(row.last_posted_at, "last_posted_at");
  const total = quantity(row.visible_total_quantity, "visible_total_quantity");
  const transactions = integer(row.inventory_transaction_count, "inventory_transaction_count");
  if (count !== ids.length || ((status === "not_posted") !== (count === 0)) || ((first === null) !== (last === null))) fail("过账状态与不可变事实不一致");
  if ((status === "not_posted" && (total !== "0.000" || transactions !== 0)) || transactions > count || (first !== null && last !== null && Date.parse(last) < Date.parse(first))) fail("过账事实数量或时间边界无效");
  return Object.freeze({ status, posting_ids: Object.freeze(ids), posting_fact_count: count, visible_total_quantity: total, covers_all_task_scopes: booleanValue(row.covers_all_task_scopes, "covers_all_task_scopes"), inventory_transaction_count: transactions, first_posted_at: first, last_posted_at: last });
}

function round(value: unknown): FormalStocktakeRound {
  const row = exact(value, ["round_id", "round_no", "round_type", "status", "started_at", "submitted_at", "submission", "visible_scope_completions", "visible_count_lines", "visible_observations", "differences_visible", "difference_completion", "visible_differences", "region_review", "headquarters_review", "recount_cause", "posting", "allowed_actions"], "盘点轮次");
  const status = oneOf(row.status, ROUND_STATUSES, "round.status");
  const submittedAt = nullableTimestamp(row.submitted_at, "round.submitted_at");
  if ((status !== "counting") !== (submittedAt !== null)) fail("轮次提交状态与时间不一致");
  const differencesVisible = booleanValue(row.differences_visible, "differences_visible");
  const completion = differenceCompletion(row.difference_completion);
  const differences = uniqueArray(row.visible_differences, difference, "visible_differences", (item) => item.difference_id);
  const regionReview = review(row.region_review);
  const headquartersReview = review(row.headquarters_review);
  if (!differencesVisible && (completion || differences.length || regionReview || headquartersReview)) fail("盲盘隐藏阶段不能暴露差异或复核事实");
  return Object.freeze({ round_id: uuid(row.round_id, "round_id"), round_no: integer(row.round_no, "round_no", 1), round_type: oneOf(row.round_type, ["initial", "recount"], "round_type"), status, started_at: timestamp(row.started_at, "started_at"), submitted_at: submittedAt, submission: submission(row.submission), visible_scope_completions: Object.freeze(uniqueArray(row.visible_scope_completions, scopeCompletion, "scope_completions", (item) => String(item.scope_id))), visible_count_lines: Object.freeze(uniqueArray(row.visible_count_lines, countLine, "count_lines", (item) => String(item.count_line_id))), visible_observations: Object.freeze(uniqueArray(row.visible_observations, observation, "observations", (item) => String(item.observation_id))), differences_visible: differencesVisible, difference_completion: completion, visible_differences: Object.freeze(differences), region_review: regionReview, headquarters_review: headquartersReview, recount_cause: recountCause(row.recount_cause), posting: posting(row.posting), allowed_actions: Object.freeze(actions(row.allowed_actions, ["generate_initial_differences", "review_region", "review_headquarters", "open_recount", "generate_recount_differences"])) });
}

function closeControl(value: unknown): FormalStocktakeCloseControl {
  const row = exact(value, ["latest_reconciliation", "close_completion"], "盘点对账关闭控制");
  let latest: FormalStocktakeCloseControl["latest_reconciliation"] = null;
  if (row.latest_reconciliation !== null) {
    const item = exact(row.latest_reconciliation, ["completion_id", "reconciliation_no", "reconciliation_ledger_cursor", "reconciled_task_version", "reconciled_at"], "最新内部对账事实");
    latest = Object.freeze({
      completion_id: uuid(item.completion_id, "reconciliation.completion_id"),
      reconciliation_no: integer(item.reconciliation_no, "reconciliation_no", 1),
      reconciliation_ledger_cursor: integer(item.reconciliation_ledger_cursor, "reconciliation_ledger_cursor"),
      reconciled_task_version: integer(item.reconciled_task_version, "reconciled_task_version", 1),
      reconciled_at: timestamp(item.reconciled_at, "reconciled_at"),
    });
  }
  let closed: FormalStocktakeCloseControl["close_completion"] = null;
  if (row.close_completion !== null) {
    const item = exact(row.close_completion, ["completion_id", "reconciliation_completion_id", "closed_task_version", "closed_at"], "盘点关闭完成事实");
    closed = Object.freeze({
      completion_id: uuid(item.completion_id, "close.completion_id"),
      reconciliation_completion_id: uuid(item.reconciliation_completion_id, "close.reconciliation_completion_id"),
      closed_task_version: integer(item.closed_task_version, "closed_task_version", 1),
      closed_at: timestamp(item.closed_at, "close.closed_at"),
    });
  }
  if (closed && (!latest || closed.reconciliation_completion_id !== latest.completion_id || closed.closed_task_version !== latest.reconciled_task_version + 1)) fail("盘点关闭完成事实未精确承接最新内部对账");
  return Object.freeze({ latest_reconciliation: latest, close_completion: closed });
}

export function validateFormalStocktakeDetail(value: unknown): FormalStocktakeDetail {
  const row = exact(value, ["schema_version", "task_id", "task_no", "task_type", "region_org_id", "status", "version", "blind_count", "current_round_no", "cutoff_ledger_cursor", "cutoff_at", "issued_at", "frozen_at", "submitted_at", "posted_at", "closed_at", "cancelled_at", "deadline", "note", "state_axes", "close_control", "scopes", "rounds", "allowed_actions"], "正式盘点详情");
  if (row.schema_version !== FORMAL_STOCKTAKE_SCHEMA_VERSION) fail("正式盘点 schema_version 不受支持");
  const cursor = row.cutoff_ledger_cursor === null ? null : integer(row.cutoff_ledger_cursor, "cutoff_ledger_cursor");
  const cutoffAt = nullableTimestamp(row.cutoff_at, "cutoff_at");
  if ((cursor === null) !== (cutoffAt === null)) fail("盘点截止游标与时间必须同时存在");
  const scopes = uniqueArray(row.scopes, scope, "scopes", (item) => item.scope_id);
  if (!scopes.length) fail("正式盘点详情必须至少包含一个可见范围");
  const rounds = uniqueArray(row.rounds, round, "rounds", (item) => item.round_id);
  const currentRoundNo = integer(row.current_round_no, "current_round_no");
  if (currentRoundNo === 0 && rounds.length) fail("草稿任务不能暴露盘点轮次");
  const status = oneOf(row.status, FORMAL_STOCKTAKE_STATUSES, "status");
  const version = integer(row.version, "version");
  const postedAt = nullableTimestamp(row.posted_at, "posted_at");
  const closedAt = nullableTimestamp(row.closed_at, "closed_at");
  const axes = stateAxes(row.state_axes);
  const control = closeControl(row.close_control);
  const allowed = actions(row.allowed_actions);
  if ((axes.reconciliation_status === "not_reconciled") !== (control.latest_reconciliation === null)) fail("盘点内部对账状态轴与完成事实不一致");
  if ((axes.closure_status === "closed") !== (control.close_completion !== null)) fail("盘点关闭状态轴与完成事实不一致");
  if ((status === "closed") !== (closedAt !== null) || (status === "closed") !== (axes.closure_status === "closed")) fail("盘点任务关闭状态、时间与独立状态轴不一致");
  if (control.close_completion && (control.close_completion.closed_task_version !== version || Date.parse(control.close_completion.closed_at) !== Date.parse(closedAt as string))) fail("盘点关闭完成事实未精确承接任务版本与时间");
  if (status === "posted" && axes.reconciliation_status === "recorded" && control.latest_reconciliation?.reconciled_task_version !== version) fail("当前有效内部对账未精确承接任务版本");
  if (status === "posted" && axes.reconciliation_status === "stale" && control.latest_reconciliation && control.latest_reconciliation.reconciled_task_version > version) fail("过期内部对账版本不能晚于任务版本");
  if (status !== "posted" && status !== "closed" && (axes.posting_status !== "not_posted" || axes.reconciliation_status !== "not_reconciled" || axes.closure_status !== "open" || control.latest_reconciliation !== null || control.close_completion !== null || allowed.includes("reconcile") || allowed.includes("close"))) fail("非终态盘点不得携带过账、对账、关闭事实或终态动作");
  if ((status === "posted" || status === "closed") !== (postedAt !== null) || ((status === "posted" || status === "closed") && axes.posting_status !== "recorded")) fail("任务过账状态、时间与独立过账完成事实不一致");
  if (control.latest_reconciliation && (postedAt === null || Date.parse(control.latest_reconciliation.reconciled_at) <= Date.parse(postedAt))) fail("内部对账时间必须晚于过账时间");
  if (control.close_completion && control.latest_reconciliation && Date.parse(control.close_completion.closed_at) <= Date.parse(control.latest_reconciliation.reconciled_at)) fail("关闭时间必须晚于内部对账时间");
  if (status === "posted" && (allowed.some((action) => action !== "reconcile" && action !== "close") || scopes.some((item) => item.allowed_actions.length) || rounds.some((item) => item.allowed_actions.length))) fail("已过账任务只能开放独立对账或关闭动作");
  if (status === "closed" && (axes.reconciliation_status !== "recorded" || control.latest_reconciliation === null || control.latest_reconciliation.reconciled_task_version !== version - 1 || allowed.length || scopes.some((item) => item.allowed_actions.length) || rounds.some((item) => item.allowed_actions.length))) fail("已关闭任务必须承接当前对账事实且不得再暴露写动作");
  if (status === "posted" && allowed.includes("close") && axes.reconciliation_status !== "recorded") fail("关闭动作只能由当前有效内部对账开放");
  return Object.freeze({ schema_version: FORMAL_STOCKTAKE_SCHEMA_VERSION, task_id: uuid(row.task_id, "task_id"), task_no: text(row.task_no, "task_no", false), task_type: oneOf(row.task_type, TASK_TYPES, "task_type"), region_org_id: uuid(row.region_org_id, "region_org_id"), status, version, blind_count: booleanValue(row.blind_count, "blind_count"), current_round_no: currentRoundNo, cutoff_ledger_cursor: cursor, cutoff_at: cutoffAt, issued_at: nullableTimestamp(row.issued_at, "issued_at"), frozen_at: nullableTimestamp(row.frozen_at, "frozen_at"), submitted_at: nullableTimestamp(row.submitted_at, "submitted_at"), posted_at: postedAt, closed_at: closedAt, cancelled_at: nullableTimestamp(row.cancelled_at, "cancelled_at"), deadline: nullableTimestamp(row.deadline, "deadline"), note: text(row.note, "note"), state_axes: axes, close_control: control, scopes: Object.freeze(scopes), rounds: Object.freeze(rounds), allowed_actions: Object.freeze(allowed) });
}

export function fixedQuantityText(value: string, positive = false): string {
  if (typeof value !== "string") fail("数量必须以十进制文本提交");
  const checked = value.trim();
  if (!INPUT_QUANTITY.test(checked)) fail("数量必须是非指数十进制文本，最多三位小数");
  const [whole, fraction = ""] = checked.split(".");
  const result = `${whole}.${fraction.padEnd(3, "0")}`;
  if (positive && result === "0.000") fail("实物观察数量必须大于零");
  return result;
}

function validateCountBody(value: unknown): StocktakeCountInput {
  const row = exact(value, ["count_mode", "account_counts", "physical_observations", "evidence_file_ids", "zero_confirmed"], "盘点计数命令");
  const mode = oneOf(row.count_mode, ["blind", "open"], "count_mode");
  const accounts = uniqueArray(row.account_counts, (value) => {
    const item = exact(value, ["stock_account_id", "counted_qty", "count_method", "serial_ids", "book_qty_confirmation", "reason_code", "remark"], "账面账户计数");
    const confirmation = item.book_qty_confirmation === null ? null : fixedQuantityText(item.book_qty_confirmation as string);
    if ((mode === "open") !== (confirmation !== null)) fail("明盘必须逐行确认账面数，盲盘不得回传账面数");
    return Object.freeze({ stock_account_id: uuid(item.stock_account_id, "stock_account_id"), counted_qty: fixedQuantityText(item.counted_qty as string), count_method: oneOf(item.count_method, COUNT_METHODS, "count_method"), serial_ids: Object.freeze(uniqueArray(item.serial_ids, (id) => uuid(id, "serial_id"), "serial_ids")), book_qty_confirmation: confirmation, reason_code: item.reason_code === null ? null : text(item.reason_code, "reason_code", false), remark: text(item.remark, "remark") });
  }, "account_counts", (item) => item.stock_account_id);
  const observations = Array.isArray(row.physical_observations) ? row.physical_observations.map((value) => {
    const item = exact(value, ["material_id", "material_identifier_raw", "material_identifier_type", "condition_code", "availability_bucket", "counted_qty", "lot_id", "lot_no_raw", "serial_id", "serial_no_raw", "serial_identifier_type", "count_method", "reason_code", "remark"], "实物观察");
    const serialNo = item.serial_no_raw === null ? null : text(item.serial_no_raw, "serial_no_raw", false);
    const serialType = item.serial_identifier_type === null ? null : oneOf(item.serial_identifier_type, ["serial_no", "qr_code", "unknown"], "serial_identifier_type");
    if ((serialNo === null) !== (serialType === null)) fail("SN 与标识类型必须同时填写");
    return Object.freeze({ material_id: nullableUuid(item.material_id, "material_id"), material_identifier_raw: text(item.material_identifier_raw, "material_identifier_raw", false), material_identifier_type: oneOf(item.material_identifier_type, ["sku_code", "qr_code", "external_code", "unknown"], "material_identifier_type"), condition_code: oneOf(item.condition_code, CONDITION_CODES, "condition_code"), availability_bucket: oneOf(item.availability_bucket, AVAILABILITY_BUCKETS, "availability_bucket"), counted_qty: fixedQuantityText(item.counted_qty as string, true), lot_id: nullableUuid(item.lot_id, "lot_id"), lot_no_raw: item.lot_no_raw === null ? null : text(item.lot_no_raw, "lot_no_raw", false), serial_id: nullableUuid(item.serial_id, "serial_id"), serial_no_raw: serialNo, serial_identifier_type: serialType, count_method: oneOf(item.count_method, COUNT_METHODS, "count_method"), reason_code: item.reason_code === null ? null : text(item.reason_code, "reason_code", false), remark: text(item.remark, "remark") });
  }) : fail("physical_observations必须是数组");
  const evidence = uniqueArray(row.evidence_file_ids, (id) => uuid(id, "evidence_file_id"), "evidence_file_ids");
  const zero = booleanValue(row.zero_confirmed, "zero_confirmed");
  if (zero && (accounts.length || observations.length)) fail("零库存确认不能与计数明细并存");
  if (!zero && !accounts.length && !observations.length) fail("非零库存确认必须包含计数或观察明细");
  return Object.freeze({ count_mode: mode, account_counts: Object.freeze(accounts), physical_observations: Object.freeze(observations), evidence_file_ids: Object.freeze(evidence), zero_confirmed: zero });
}

function commandPath(input: FormalStocktakeCommandInput): string {
  if (input.action === "create_personal") return "/v1/stocktakes/personal";
  if (input.action === "create_managed") return "/v1/stocktakes";
  const taskId = uuid(input.taskId, "task_id");
  if (input.action === "start") return `/v1/stocktakes/${taskId}/start`;
  if (input.action === "post") return `/v1/stocktakes/${taskId}/post-differences`;
  if (input.action === "reconcile") return `/v1/stocktakes/${taskId}/reconcile`;
  if (input.action === "close") return `/v1/stocktakes/${taskId}/close`;
  if (!("roundId" in input)) fail("盘点轮次写命令缺少 round_id");
  const roundId = uuid(input.roundId, "round_id");
  if (input.action === "submit_initial_count") return `/v1/stocktakes/${taskId}/rounds/${roundId}/scopes/${uuid(input.scopeId, "scope_id")}/initial-count`;
  if (input.action === "submit_recount_count") return `/v1/stocktakes/${taskId}/rounds/${roundId}/scopes/${uuid(input.scopeId, "scope_id")}/recount-count`;
  if (input.action === "generate_initial_differences") return `/v1/stocktakes/${taskId}/rounds/${roundId}/differences`;
  if (input.action === "generate_recount_differences") return `/v1/stocktakes/${taskId}/rounds/${roundId}/recount-differences`;
  if (input.action === "review_region") return `/v1/stocktakes/${taskId}/rounds/${roundId}/reviews/region`;
  if (input.action === "review_headquarters") return `/v1/stocktakes/${taskId}/rounds/${roundId}/reviews/headquarters`;
  return `/v1/stocktakes/${taskId}/rounds/${roundId}/recount`;
}

function commandBody(input: FormalStocktakeCommandInput): Record<string, unknown> {
  const body = objectValue(input.body, "正式盘点写命令");
  if (input.action === "create_personal") {
    const row = exact(body, ["blind_count", "freeze_mode", "note"], "个人自盘创建命令");
    return { blind_count: booleanValue(row.blind_count, "blind_count"), freeze_mode: oneOf(row.freeze_mode, ["hard", "cutoff_replay"], "freeze_mode"), note: text(row.note, "note") };
  }
  if (input.action === "create_managed") {
    const row = exact(body, ["task_type", "region_org_id", "blind_count", "scopes", "deadline", "note"], "托管盘点创建命令");
    const taskType = oneOf(row.task_type, ["full", "sample", "ad_hoc", "termination"], "task_type");
    const regionOrgId = uuid(row.region_org_id, "region_org_id");
    const scopes = uniqueArray(row.scopes, (value) => {
      const item = exact(value, ["owner_org_id", "location_id", "assignee_person_id", "scope_mode", "material_id", "condition_code", "availability_bucket", "freeze_mode"], "托管盘点范围");
      const scopeMode = oneOf(item.scope_mode, ["location_all", "filtered"], "scope_mode");
      const materialId = nullableUuid(item.material_id, "material_id");
      const conditionCode = item.condition_code === null ? null : oneOf(item.condition_code, CONDITION_CODES, "condition_code");
      const availability = item.availability_bucket === null ? null : oneOf(item.availability_bucket, AVAILABILITY_BUCKETS, "availability_bucket");
      if (scopeMode === "location_all" && (materialId || conditionCode || availability)) fail("整库范围不能携带筛选条件");
      if (scopeMode === "filtered" && !materialId && !conditionCode && !availability) fail("筛选范围至少需要一个筛选条件");
      return Object.freeze({ owner_org_id: uuid(item.owner_org_id, "owner_org_id"), location_id: uuid(item.location_id, "location_id"), assignee_person_id: uuid(item.assignee_person_id, "assignee_person_id"), scope_mode: scopeMode, material_id: materialId, condition_code: conditionCode, availability_bucket: availability, freeze_mode: oneOf(item.freeze_mode, ["hard", "cutoff_replay"], "freeze_mode") });
    }, "scopes", (item) => `${item.location_id}\u0000${item.material_id || ""}\u0000${item.condition_code || ""}\u0000${item.availability_bucket || ""}`);
    if (!scopes.length || scopes.length > 500) fail("托管盘点范围数量无效");
    if (taskType === "full" && scopes.some((item) => item.scope_mode !== "location_all")) fail("全盘只能使用整库范围");
    return { task_type: taskType, region_org_id: regionOrgId, blind_count: booleanValue(row.blind_count, "blind_count"), scopes, deadline: row.deadline === null ? null : timestamp(row.deadline, "deadline"), note: text(row.note, "note") };
  }
  if (input.action === "start") {
    const row = exact(body, ["expected_version"], "盘点启动命令");
    const expected = integer(row.expected_version, "expected_version");
    if (expected !== input.expectedTaskVersion) fail("启动命令版本与写意图不一致");
    return { expected_version: expected };
  }
  if (input.action === "post" || input.action === "reconcile" || input.action === "close") {
    const commandName = input.action === "post" ? "盘点差异过账命令" : input.action === "reconcile" ? "盘点内部对账命令" : "盘点关闭命令";
    const row = exact(body, ["expected_task_version"], commandName);
    const expected = integer(row.expected_task_version, "expected_task_version");
    if (expected !== input.expectedTaskVersion) fail(`${commandName}版本与写意图不一致`);
    return { expected_task_version: expected };
  }
  if (input.action === "submit_initial_count" || input.action === "submit_recount_count") {
    return validateCountBody(body) as unknown as Record<string, unknown>;
  }
  if (input.action === "generate_initial_differences" || input.action === "generate_recount_differences") {
    const row = exact(body, ["expected_task_version"], "差异生成命令");
    const expected = integer(row.expected_task_version, "expected_task_version");
    if (expected !== input.expectedTaskVersion) fail("差异命令版本与写意图不一致");
    return { expected_task_version: expected };
  }
  if (input.action === "open_recount") {
    const row = exact(body, ["expected_task_version", "assignments", "reason"], "开复盘命令");
    const expected = integer(row.expected_task_version, "expected_task_version");
    if (expected !== input.expectedTaskVersion) fail("开复盘版本与写意图不一致");
    const assignments = uniqueArray(row.assignments, (value) => {
      const item = exact(value, ["scope_id", "assignee_user_id"], "复盘范围分配");
      const assigneeUserId = text(item.assignee_user_id, "assignee_user_id", false);
      if (assigneeUserId.length > 160) fail("assignee_user_id 无效");
      return Object.freeze({ scope_id: uuid(item.scope_id, "scope_id"), assignee_user_id: assigneeUserId });
    }, "assignments", (item) => item.scope_id);
    if (!assignments.length) fail("开复盘至少选择一个范围");
    return { expected_task_version: expected, assignments, reason: text(row.reason, "reason", false) };
  }
  const row = exact(body, ["expected_task_version", "decision", "items", "comment"], "盘点复核命令");
  const expected = integer(row.expected_task_version, "expected_task_version");
  if (expected !== input.expectedTaskVersion) fail("复核版本与写意图不一致");
  const decision = oneOf(row.decision, REVIEW_DECISIONS, "decision");
  const comment = text(row.comment, "comment");
  if (decision !== "approve" && !comment) fail("复盘或驳回复核必须填写意见");
  const items = uniqueArray(row.items, (value) => {
    const item = exact(value, ["difference_id", "decision", "comment"], "复核逐项决定");
    return Object.freeze({ difference_id: uuid(item.difference_id, "difference_id"), decision: oneOf(item.decision, REVIEW_ITEM_DECISIONS, "item.decision"), comment: text(item.comment, "item.comment") });
  }, "review.items", (item) => item.difference_id);
  return { expected_task_version: expected, decision, items, comment };
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.values(value as Record<string, unknown>).forEach(deepFreeze);
  }
  return value;
}

function coordinates(): { "Idempotency-Key": string; "X-Request-ID": string } {
  const supplied = mutationHeaders("stocktake").headers as Record<string, string>;
  const key = supplied["Idempotency-Key"];
  const requestId = supplied["X-Request-ID"];
  if (!SAFE_IDEMPOTENCY_KEY.test(key || "") || !SAFE_REQUEST_ID.test(requestId || "")) fail("无法生成安全盘点写坐标");
  return { "Idempotency-Key": key, "X-Request-ID": requestId };
}

export function createFormalStocktakeIntentRegistry(options: { coordinateFactory?: () => { "Idempotency-Key": string; "X-Request-ID": string } } = {}) {
  let pending: FormalStocktakeIntent | null = null;
  const coordinateFactory = options.coordinateFactory ?? coordinates;
  return {
    begin(input: FormalStocktakeCommandInput): FormalStocktakeIntent {
      const path = commandPath(input);
      const body = deepFreeze(JSON.parse(JSON.stringify(commandBody(input))) as Record<string, unknown>);
      const signature = `${input.action}\n${path}\n${JSON.stringify(body)}`;
      if (pending) {
        if (pending.signature !== signature) fail("上一笔盘点写结果尚未精确确认，禁止生成新写坐标");
        return pending;
      }
      const headers = coordinateFactory();
      if (!headers || !SAFE_IDEMPOTENCY_KEY.test(headers["Idempotency-Key"] || "") || !SAFE_REQUEST_ID.test(headers["X-Request-ID"] || "")) fail("盘点写坐标无效");
      const creation = input.action === "create_personal" || input.action === "create_managed";
      pending = deepFreeze({ action: input.action, method: "POST", path, body, headers: { ...headers }, taskId: creation ? null : uuid(input.taskId, "task_id"), roundId: "roundId" in input ? uuid(input.roundId, "round_id") : null, scopeId: "scopeId" in input ? uuid(input.scopeId, "scope_id") : null, expectedTaskVersion: creation ? null : input.expectedTaskVersion, signature });
      return pending;
    },
    current(): FormalStocktakeIntent | null { return pending; },
    complete(intent: FormalStocktakeIntent): void {
      if (pending !== intent) fail("只能完成当前精确盘点写意图");
      pending = null;
    },
  };
}

type WriteResult = Record<string, unknown>;

function resultBase(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  const row = exact(value, ["schema_version", ...keys], name);
  if (row.schema_version !== FORMAL_STOCKTAKE_SCHEMA_VERSION) fail(`${name} schema_version 不受支持`);
  return row;
}

export function validateFormalStocktakeWriteResult(intent: FormalStocktakeIntent, value: unknown): WriteResult {
  let row: Record<string, unknown>;
  if (intent.action === "create_personal" || intent.action === "create_managed") {
    const managed = intent.action === "create_managed";
    row = resultBase(value, ["task_id", "task_no", "task_type", "status", "task_version", "scope_count", "idempotency_replayed"], managed ? "托管盘点创建结果" : "个人自盘创建结果");
    const taskType = oneOf(row.task_type, TASK_TYPES, "task_type");
    if ((!managed && taskType !== "personal") || (managed && taskType === "personal") || row.status !== "draft" || row.task_version !== 0) fail("盘点创建结果状态无效");
    uuid(row.task_id, "task_id"); text(row.task_no, "task_no", false); integer(row.scope_count, "scope_count", 1); booleanValue(row.idempotency_replayed, "idempotency_replayed");
    return Object.freeze({ ...row });
  }
  if (intent.action === "start") {
    row = resultBase(value, ["task_id", "task_type", "status", "task_version", "cutoff_ledger_cursor", "initial_round_id", "scope_count", "snapshot_line_count", "active_freeze_count", "idempotency_replayed"], "盘点启动结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || row.status !== "counting") fail("盘点启动结果锚点无效");
    oneOf(row.task_type, TASK_TYPES, "task_type"); integer(row.task_version, "task_version", 1); integer(row.cutoff_ledger_cursor, "cutoff_ledger_cursor"); uuid(row.initial_round_id, "initial_round_id");
    const scopes = integer(row.scope_count, "scope_count", 1); if (integer(row.active_freeze_count, "active_freeze_count", 1) !== scopes) fail("启动结果冻结范围不完整");
    integer(row.snapshot_line_count, "snapshot_line_count"); booleanValue(row.idempotency_replayed, "idempotency_replayed"); return Object.freeze({ ...row });
  }
  if (intent.action === "post") {
    row = resultBase(value, ["completion_id", "task_id", "terminal_round_id", "resulting_task_status", "task_version", "scope_count", "difference_count", "accepted_difference_count", "no_adjustment_count", "transaction_count", "movement_count", "total_quantity", "first_ledger_cursor", "last_ledger_cursor", "replayed"], "盘点差异过账结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || row.resulting_task_status !== "posted") fail("盘点差异过账结果锚点无效");
    uuid(row.completion_id, "completion_id"); uuid(row.terminal_round_id, "terminal_round_id");
    const taskVersion = integer(row.task_version, "task_version", 1);
    if (taskVersion !== (intent.expectedTaskVersion as number) + 1) fail("盘点差异过账结果版本无效");
    integer(row.scope_count, "scope_count", 1);
    const differenceCount = integer(row.difference_count, "difference_count");
    const acceptedCount = integer(row.accepted_difference_count, "accepted_difference_count");
    const noAdjustmentCount = integer(row.no_adjustment_count, "no_adjustment_count");
    const transactionCount = integer(row.transaction_count, "transaction_count");
    const movementCount = integer(row.movement_count, "movement_count");
    const totalQuantity = quantity(row.total_quantity, "total_quantity");
    const firstCursor = row.first_ledger_cursor === null ? null : integer(row.first_ledger_cursor, "first_ledger_cursor", 1);
    const lastCursor = row.last_ledger_cursor === null ? null : integer(row.last_ledger_cursor, "last_ledger_cursor", 1);
    if (acceptedCount + noAdjustmentCount !== differenceCount || movementCount !== acceptedCount) fail("盘点差异过账结果数量不守恒");
    if (transactionCount === 0) {
      if (firstCursor !== null || lastCursor !== null || movementCount !== 0 || totalQuantity !== "0.000") fail("零交易过账结果流水汇总无效");
    } else if (firstCursor === null || lastCursor === null || lastCursor - firstCursor + 1 !== transactionCount || movementCount <= 0 || totalQuantity === "0.000") {
      fail("盘点差异过账结果流水游标无效");
    }
    booleanValue(row.replayed, "replayed"); return Object.freeze({ ...row });
  }
  if (intent.action === "reconcile") {
    row = resultBase(value, ["completion_id", "task_id", "posting_completion_id", "reconciliation_no", "reconciliation_ledger_cursor", "resulting_task_status", "task_version", "scope_count", "account_count", "scoped_account_count", "serial_count", "transaction_count", "movement_count", "book_total_qty", "physical_total_qty", "reconciled_at", "replayed"], "盘点内部对账结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || row.resulting_task_status !== "posted") fail("盘点内部对账结果锚点无效");
    uuid(row.completion_id, "completion_id"); uuid(row.posting_completion_id, "posting_completion_id");
    integer(row.reconciliation_no, "reconciliation_no", 1); integer(row.reconciliation_ledger_cursor, "reconciliation_ledger_cursor");
    const taskVersion = integer(row.task_version, "task_version", 1);
    if (taskVersion !== (intent.expectedTaskVersion as number) + 1) fail("盘点内部对账结果版本无效");
    integer(row.scope_count, "scope_count", 1);
    const accountCount = integer(row.account_count, "account_count");
    const scopedAccountCount = integer(row.scoped_account_count, "scoped_account_count");
    if (scopedAccountCount > accountCount) fail("盘点内部对账范围账户数量无效");
    integer(row.serial_count, "serial_count"); integer(row.transaction_count, "transaction_count"); integer(row.movement_count, "movement_count");
    if (quantity(row.book_total_qty, "book_total_qty") !== quantity(row.physical_total_qty, "physical_total_qty")) fail("盘点内部对账账物总量不一致");
    timestamp(row.reconciled_at, "reconciled_at"); booleanValue(row.replayed, "replayed");
    return Object.freeze({ ...row });
  }
  if (intent.action === "close") {
    row = resultBase(value, ["completion_id", "task_id", "reconciliation_completion_id", "reconciliation_no", "reconciliation_ledger_cursor", "resulting_task_status", "task_version", "closed_at", "replayed"], "盘点关闭结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || row.resulting_task_status !== "closed") fail("盘点关闭结果锚点无效");
    uuid(row.completion_id, "completion_id"); uuid(row.reconciliation_completion_id, "reconciliation_completion_id");
    integer(row.reconciliation_no, "reconciliation_no", 1); integer(row.reconciliation_ledger_cursor, "reconciliation_ledger_cursor");
    const taskVersion = integer(row.task_version, "task_version", 1);
    if (taskVersion !== (intent.expectedTaskVersion as number) + 1) fail("盘点关闭结果版本无效");
    timestamp(row.closed_at, "closed_at"); booleanValue(row.replayed, "replayed");
    return Object.freeze({ ...row });
  }
  if (intent.action === "submit_initial_count" || intent.action === "submit_recount_count") {
    const recount = intent.action === "submit_recount_count";
    const keys = ["task_id", "round_id", "scope_id", ...(recount ? ["recount_case_id"] : []), "task_status", "round_status", "task_version", "scope_completed", "round_submitted", ...(recount ? ["count_ledger_cursor"] : []), "evidence_file_count", "replayed"];
    row = resultBase(value, keys, recount ? "复盘计数结果" : "初盘计数结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || uuid(row.round_id, "round_id") !== intent.roundId || uuid(row.scope_id, "scope_id") !== intent.scopeId) fail("盘点计数结果锚点无效");
    if (recount) { uuid(row.recount_case_id, "recount_case_id"); integer(row.count_ledger_cursor, "count_ledger_cursor"); }
    oneOf(row.round_status, recount ? ["counting", "submitted"] : ROUND_STATUSES, "round_status"); integer(row.task_version, "task_version"); booleanValue(row.scope_completed, "scope_completed"); booleanValue(row.round_submitted, "round_submitted"); integer(row.evidence_file_count, "evidence_file_count"); booleanValue(row.replayed, "replayed"); text(row.task_status, "task_status", false); return Object.freeze({ ...row });
  }
  if (intent.action === "generate_initial_differences" || intent.action === "generate_recount_differences") {
    const recount = intent.action === "generate_recount_differences";
    row = resultBase(value, ["task_id", "round_id", ...(recount ? ["recount_case_id"] : []), "completion_id", "task_status", "round_status", "task_version", "difference_status", "difference_count", "physical_difference_count", "pending_observation_difference_count", "total_affected_qty", "difference_manifest_sha256", ...(recount ? ["selected_scope_count"] : []), "replayed"], recount ? "复盘差异结果" : "初盘差异结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || uuid(row.round_id, "round_id") !== intent.roundId || row.difference_status !== "evaluated") fail("盘点差异结果锚点无效");
    if (recount) { uuid(row.recount_case_id, "recount_case_id"); integer(row.selected_scope_count, "selected_scope_count", 1); }
    uuid(row.completion_id, "completion_id"); text(row.task_status, "task_status", false); oneOf(row.round_status, recount ? ["submitted"] : ["submitted", "superseded"], "round_status"); integer(row.task_version, "task_version"); integer(row.difference_count, "difference_count"); integer(row.physical_difference_count, "physical_difference_count"); integer(row.pending_observation_difference_count, "pending_observation_difference_count"); quantity(row.total_affected_qty, "total_affected_qty"); if (typeof row.difference_manifest_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(row.difference_manifest_sha256)) fail("差异清单摘要无效"); booleanValue(row.replayed, "replayed"); return Object.freeze({ ...row });
  }
  if (intent.action === "open_recount") {
    row = resultBase(value, ["recount_case_id", "task_id", "source_round_id", "next_round_id", "next_round_no", "scope_count", "assignment_count", "resulting_task_status", "task_version", "replayed"], "开复盘结果");
    if (uuid(row.task_id, "task_id") !== intent.taskId || uuid(row.source_round_id, "source_round_id") !== intent.roundId || row.resulting_task_status !== "counting") fail("开复盘结果锚点无效");
    uuid(row.recount_case_id, "recount_case_id"); uuid(row.next_round_id, "next_round_id"); integer(row.next_round_no, "next_round_no", 2); integer(row.scope_count, "scope_count", 1); integer(row.assignment_count, "assignment_count", 1); integer(row.task_version, "task_version"); booleanValue(row.replayed, "replayed"); return Object.freeze({ ...row });
  }
  row = resultBase(value, ["review_id", "task_id", "round_id", "review_stage", "decision", "resulting_task_status", "task_version", "item_count", "pending_verification_count", "ready_for_posting", "replayed"], "盘点复核结果");
  const stage = intent.action === "review_region" ? "region" : "headquarters";
  if (uuid(row.task_id, "task_id") !== intent.taskId || uuid(row.round_id, "round_id") !== intent.roundId || row.review_stage !== stage) fail("盘点复核结果锚点无效");
  uuid(row.review_id, "review_id"); oneOf(row.decision, REVIEW_DECISIONS, "decision"); text(row.resulting_task_status, "resulting_task_status", false); integer(row.task_version, "task_version"); integer(row.item_count, "item_count"); integer(row.pending_verification_count, "pending_verification_count"); booleanValue(row.ready_for_posting, "ready_for_posting"); booleanValue(row.replayed, "replayed"); return Object.freeze({ ...row });
}

export function confirmFormalStocktakeWrite(intent: FormalStocktakeIntent, result: WriteResult, detail: FormalStocktakeDetail): void {
  const taskId = uuid(result.task_id, "result.task_id");
  if (detail.task_id !== taskId || detail.version !== integer(result.task_version, "result.task_version")) fail("精确回读未确认同一任务版本");
  if (intent.action === "create_personal" || intent.action === "create_managed") {
    if (detail.task_no !== result.task_no || (intent.action === "create_personal" && detail.task_type !== "personal") || (intent.action === "create_managed" && detail.task_type === "personal") || detail.status !== "draft") fail("精确回读未确认盘点草稿");
    return;
  }
  if (intent.action === "reconcile") {
    const latest = detail.close_control.latest_reconciliation;
    if (
      detail.status !== "posted"
      || detail.closed_at !== null
      || detail.state_axes.posting_status !== "recorded"
      || detail.state_axes.reconciliation_status !== "recorded"
      || detail.state_axes.closure_status !== "open"
      || detail.close_control.close_completion !== null
      || latest === null
      || latest.completion_id !== uuid(result.completion_id, "result.completion_id")
      || latest.reconciliation_no !== integer(result.reconciliation_no, "result.reconciliation_no", 1)
      || latest.reconciliation_ledger_cursor !== integer(result.reconciliation_ledger_cursor, "result.reconciliation_ledger_cursor")
      || latest.reconciled_task_version !== integer(result.task_version, "result.task_version", 1)
      || Date.parse(latest.reconciled_at) !== Date.parse(timestamp(result.reconciled_at, "result.reconciled_at"))
    ) fail("精确回读未确认独立盘点内部对账完成事实");
    return;
  }
  if (intent.action === "close") {
    const latest = detail.close_control.latest_reconciliation;
    const closed = detail.close_control.close_completion;
    if (
      detail.status !== "closed"
      || detail.closed_at === null
      || detail.state_axes.posting_status !== "recorded"
      || detail.state_axes.reconciliation_status !== "recorded"
      || detail.state_axes.closure_status !== "closed"
      || latest === null
      || closed === null
      || closed.completion_id !== uuid(result.completion_id, "result.completion_id")
      || closed.reconciliation_completion_id !== uuid(result.reconciliation_completion_id, "result.reconciliation_completion_id")
      || latest.completion_id !== closed.reconciliation_completion_id
      || latest.reconciliation_no !== integer(result.reconciliation_no, "result.reconciliation_no", 1)
      || latest.reconciliation_ledger_cursor !== integer(result.reconciliation_ledger_cursor, "result.reconciliation_ledger_cursor")
      || closed.closed_task_version !== integer(result.task_version, "result.task_version", 1)
      || Date.parse(closed.closed_at) !== Date.parse(timestamp(result.closed_at, "result.closed_at"))
      || Date.parse(detail.closed_at) !== Date.parse(closed.closed_at)
      || detail.allowed_actions.includes("reconcile")
      || detail.allowed_actions.includes("close")
    ) fail("精确回读未确认独立盘点关闭完成事实");
    return;
  }
  if (intent.action === "post") {
    if (intent.expectedTaskVersion === null) fail("盘点过账意图缺少期望版本");
    confirmFormalStocktakePostProjection(result, detail, intent.expectedTaskVersion, false);
    return;
  }
  const roundId = intent.action === "start" ? uuid(result.initial_round_id, "initial_round_id") : intent.roundId;
  const currentRound = detail.rounds.find((row) => row.round_id === roundId);
  if (intent.action === "start") {
    if (!currentRound || detail.status !== "counting" || detail.cutoff_ledger_cursor !== result.cutoff_ledger_cursor) fail("精确回读未确认启动冻结与截止游标");
    return;
  }
  if (!currentRound) fail("精确回读缺少目标轮次");
  if (intent.action === "submit_initial_count" || intent.action === "submit_recount_count") {
    if (!currentRound.visible_scope_completions.some((row) => row.scope_id === intent.scopeId)) fail("精确回读未确认目标范围计数完成事实");
    return;
  }
  if (intent.action === "generate_initial_differences" || intent.action === "generate_recount_differences") {
    if (currentRound.difference_completion?.completion_id !== result.completion_id || detail.state_axes.difference_status !== "evaluated") fail("精确回读未确认目标轮次差异完成事实");
    return;
  }
  if (intent.action === "review_region" || intent.action === "review_headquarters") {
    const fact = intent.action === "review_region" ? currentRound.region_review : currentRound.headquarters_review;
    if (!fact || fact.review_id !== result.review_id || fact.decision !== result.decision) fail("精确回读未确认独立复核事实");
    return;
  }
  if (detail.current_round_no !== result.next_round_no || !detail.rounds.some((row) => row.round_id === result.next_round_id && row.recount_cause?.recount_case_id === result.recount_case_id)) fail("精确回读未确认选定范围复盘事实");
}

/**
 * Confirm the immutable posting facts against a current detail projection.
 *
 * A direct POST readback must use the exact task version.  A recovery lookup
 * is historical: reconciliation and close may have advanced the live task
 * version, so it may only accept a version at or after the posting version,
 * while retaining every posting-axis and quantity invariant.
 */
export function confirmFormalStocktakePostProjection(
  result: WriteResult,
  detail: FormalStocktakeDetail,
  expectedTaskVersion: number,
  allowAdvancedVersion = true,
): void {
  const taskId = uuid(result.task_id, "result.task_id");
  const taskVersion = integer(result.task_version, "result.task_version", 1);
  if (
    detail.task_id !== taskId
    || taskVersion !== expectedTaskVersion + 1
    || (!allowAdvancedVersion && detail.version !== taskVersion)
    || (allowAdvancedVersion && detail.version < taskVersion)
  ) fail("盘点过账历史证据与当前任务版本不一致");
  const terminalRoundId = uuid(result.terminal_round_id, "result.terminal_round_id");
  const terminalRound = detail.rounds.find((row) => row.round_id === terminalRoundId);
  const expectedScopeCount = integer(result.scope_count, "result.scope_count", 1);
  const differenceCount = integer(result.difference_count, "result.difference_count");
  const acceptedCount = integer(result.accepted_difference_count, "result.accepted_difference_count");
  const noAdjustmentCount = integer(result.no_adjustment_count, "result.no_adjustment_count");
  const movementCount = integer(result.movement_count, "result.movement_count");
  const transactionCount = integer(result.transaction_count, "result.transaction_count");
  const totalQuantity = quantity(result.total_quantity, "result.total_quantity");
  const headquartersItems = terminalRound?.headquarters_review?.visible_items ?? [];
  const visibleDifferenceIds = new Set(terminalRound?.visible_differences.map((item) => item.difference_id) ?? []);
  const headquartersDifferenceIds = new Set(headquartersItems.map((item) => item.difference_id));
  const statusAllowed = detail.status === "posted"
    || (allowAdvancedVersion && detail.status === "closed");
  if (
    !terminalRound
    || !statusAllowed
    || detail.posted_at === null
    || (detail.status === "posted" && detail.closed_at !== null)
    || (detail.status === "closed" && detail.closed_at === null)
    || detail.state_axes.posting_status !== "recorded"
    || detail.current_round_no !== terminalRound.round_no
    || detail.scopes.length !== expectedScopeCount
    || terminalRound.difference_completion?.visible_difference_count !== differenceCount
    || terminalRound.difference_completion?.covers_all_task_scopes !== true
    || terminalRound.visible_differences.length !== differenceCount
    || terminalRound.region_review?.decision !== "approve"
    || terminalRound.region_review?.covers_all_task_scopes !== true
    || terminalRound.headquarters_review?.decision !== "approve"
    || terminalRound.headquarters_review?.covers_all_task_scopes !== true
    || headquartersItems.length !== differenceCount
    || headquartersDifferenceIds.size !== differenceCount
    || [...visibleDifferenceIds].some((differenceId) => !headquartersDifferenceIds.has(differenceId))
    || headquartersItems.some((item) => !visibleDifferenceIds.has(item.difference_id))
    || headquartersItems.filter((item) => item.decision === "accept_for_posting").length !== acceptedCount
    || headquartersItems.filter((item) => item.decision === "no_adjustment").length !== noAdjustmentCount
    || terminalRound.posting.posting_fact_count !== movementCount
    || terminalRound.posting.inventory_transaction_count !== transactionCount
    || terminalRound.posting.visible_total_quantity !== totalQuantity
    || !terminalRound.posting.covers_all_task_scopes
    || (movementCount > 0 && terminalRound.posting.status !== "recorded")
    || (movementCount === 0 && terminalRound.posting.status !== "not_posted")
    || detail.allowed_actions.includes("post")
  ) fail("精确回读未确认独立盘点差异过账完成事实");
}

export function stocktakeIntentRetryState(intent: FormalStocktakeIntent, detail: FormalStocktakeDetail | null): "retryable" | "handoff_required" {
  if (intent.action === "create_personal" || intent.action === "create_managed") return "retryable";
  if (!detail || !intent.taskId || detail.task_id !== intent.taskId) return "handoff_required";
  if (intent.expectedTaskVersion !== null && detail.version === intent.expectedTaskVersion + 1) {
    if (intent.action === "reconcile") {
      const latest = detail.close_control.latest_reconciliation;
      if (
        detail.status === "posted"
        && detail.closed_at === null
        && detail.state_axes.posting_status === "recorded"
        && detail.state_axes.reconciliation_status === "recorded"
        && detail.state_axes.closure_status === "open"
        && detail.close_control.close_completion === null
        && latest !== null
        && latest.reconciled_task_version === detail.version
      ) return "retryable";
    }
    if (intent.action === "close") {
      const latest = detail.close_control.latest_reconciliation;
      const closed = detail.close_control.close_completion;
      if (
        detail.status === "closed"
        && detail.closed_at !== null
        && detail.state_axes.posting_status === "recorded"
        && detail.state_axes.reconciliation_status === "recorded"
        && detail.state_axes.closure_status === "closed"
        && latest !== null
        && closed !== null
        && closed.closed_task_version === detail.version
        && closed.reconciliation_completion_id === latest.completion_id
        && Date.parse(detail.closed_at) === Date.parse(closed.closed_at)
      ) return "retryable";
    }
  }
  if (detail.version !== intent.expectedTaskVersion) return "handoff_required";
  if (intent.action === "start") return detail.allowed_actions.includes("start") ? "retryable" : "handoff_required";
  if (intent.action === "post" || intent.action === "reconcile" || intent.action === "close") return detail.allowed_actions.includes(intent.action) ? "retryable" : "handoff_required";
  if (intent.action === "submit_initial_count" || intent.action === "submit_recount_count") {
    const scope = detail.scopes.find((row) => row.scope_id === intent.scopeId);
    return scope?.allowed_actions.includes(intent.action) ? "retryable" : "handoff_required";
  }
  const round = detail.rounds.find((row) => row.round_id === intent.roundId);
  return round?.allowed_actions.includes(intent.action as FormalStocktakeAllowedAction) ? "retryable" : "handoff_required";
}

export const formalStocktakeLabels = Object.freeze({
  taskType: { full: "全盘", sample: "抽盘", ad_hoc: "临时盘点", personal: "个人自盘", termination: "离职盘点" },
  status: { draft: "草稿", issued: "已下发", frozen: "已冻结", counting: "盘点中", submitted: "已提交", region_review: "待区域复核", hq_review: "待总部复核", approved: "复核通过", recount_required: "要求复盘", posted: "已过账", closed: "已关闭", cancelled: "已取消" },
  difference: { missing: "盘亏", excess: "盘盈", wrong_location: "库位不符", wrong_condition: "成色不符", wrong_lot: "批次不符", wrong_serial: "SN 不符" },
});
