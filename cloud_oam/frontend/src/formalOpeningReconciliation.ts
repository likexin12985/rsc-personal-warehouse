import { ApiError, api, jsonBody, mutationHeaders } from "./api";
import {
  loadFormalOpeningStocktakeDetail,
  validateOpeningStocktakeTaskDetail,
  type OpeningStocktakeTaskDetail,
} from "./formalOpeningStocktake";


export const FORMAL_OPENING_RECONCILIATION_PATH = "/v1/reconciliations/opening";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const ISO_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;
const SIGNED_QUANTITY = /^-?(?:0|[1-9]\d{0,14})\.\d{3}$/;
const LEDGER_CURSOR = /^(?:0|[1-9]\d*)$/;
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_LEDGER_CURSOR = 9_223_372_036_854_775_807n;
const RECONCILIATION_STATUSES = ["differences", "approved"] as const;
const RECONCILIATION_ITEM_STATUSES = ["difference", "explained", "resolved"] as const;
const RECONCILIATION_ACTIONS = ["explain", "approve"] as const;

export type OpeningReconciliationStatus = typeof RECONCILIATION_STATUSES[number];
export type OpeningReconciliationItemStatus = typeof RECONCILIATION_ITEM_STATUSES[number];
export type OpeningReconciliationAllowedAction = typeof RECONCILIATION_ACTIONS[number];

export type OpeningReconciliationSummary = Readonly<{
  reconciliation_run_id: string;
  task_id: string;
  task_no: string;
  region_org_id: string;
  status: OpeningReconciliationStatus;
  version: number;
  item_count: number;
  explained_item_count: number;
  resolved_item_count: number;
  external_snapshot_at: string;
  local_ledger_cursor: string;
  created_at: string;
  approved_at: string | null;
  allowed_actions: OpeningReconciliationAllowedAction[];
}>;

export type OpeningReconciliationPage = Readonly<{
  schema_version: "1.0";
  items: OpeningReconciliationSummary[];
  next_after_id: string | null;
}>;

export type OpeningReconciliationItem = Readonly<{
  reconciliation_item_id: string;
  stocktake_difference_id: string;
  control_snapshot_line_id: string;
  business_key: string;
  material_id: string | null;
  external_qty: string;
  local_qty: string;
  difference: string;
  status: OpeningReconciliationItemStatus;
  version: number;
  explanation: string;
  evidence_reference: string;
  evidence_file_id: string | null;
  explained_at: string | null;
}>;

export type OpeningReconciliationDetail = OpeningReconciliationSummary & Readonly<{
  schema_version: "1.0";
  source_system_id: string;
  round_id: string;
  posting_id: string;
  difference_manifest_sha256: string;
  approval_comment: string;
  items: OpeningReconciliationItem[];
}>;

export type OpeningReconciliationStartResult = Readonly<{
  schema_version: "1.0";
  reconciliation_run_id: string;
  task_id: string;
  status: "differences";
  version: number;
  item_count: number;
  created_at: string;
  replayed: boolean;
}>;

export type OpeningReconciliationExplainResult = Readonly<{
  schema_version: "1.0";
  reconciliation_run_id: string;
  task_id: string;
  status: "differences";
  version: number;
  explained_item_count: number;
  explained_at: string;
  replayed: boolean;
}>;

export type OpeningReconciliationApproveResult = Readonly<{
  schema_version: "1.0";
  reconciliation_run_id: string;
  task_id: string;
  status: "approved";
  version: number;
  resolved_item_count: number;
  approved_at: string;
  replayed: boolean;
}>;

export type OpeningReconciliationExplanationInput = Readonly<{
  reconciliation_item_id: string;
  explanation: string;
  evidence_reference: string;
  evidence_file_id?: string | null;
}>;

export type OpeningReconciliationMutationResult<TBefore, TResult> = Readonly<{
  before: TBefore;
  result: TResult;
  detail: OpeningReconciliationDetail;
}>;

type ReconciliationMutationAction = "start" | "explain" | "approve";
type ReconciliationMutationCoordinates = Pick<RequestInit, "headers">;
type ReconciliationMutationIntent = {
  readonly targetKey: string;
  readonly targetId: string;
  readonly action: ReconciliationMutationAction;
  readonly path: string;
  readonly body: unknown;
  readonly signature: string;
  readonly coordinates: ReconciliationMutationCoordinates;
  readonly before: unknown;
  accepted: boolean;
  acceptedResponse?: unknown;
};

const uncertainReconciliationIntents = new Map<string, ReconciliationMutationIntent>();
const MAX_UNCERTAIN_RECONCILIATION_INTENTS = 64;

export const __openingReconciliationIntentTestOnly = import.meta.env.MODE === "test"
  ? Object.freeze({
      reset(): void {
        uncertainReconciliationIntents.clear();
      },
    })
  : undefined;

function invalid(message: string): never {
  throw new ApiError(409, message);
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalid("正式期初对账响应不是有效对象");
  }
  return value as Record<string, unknown>;
}

function field(object: Record<string, unknown>, name: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(object, name)) {
    return invalid(`正式期初对账响应缺少字段 ${name}`);
  }
  return object[name];
}

function schemaVersion(object: Record<string, unknown>): void {
  if (field(object, "schema_version") !== "1.0") {
    invalid("正式期初对账响应版本不受支持");
  }
}

function exactEnum<T extends string>(value: unknown, allowed: readonly T[], name: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return invalid(`正式期初对账响应包含未知 ${name}`);
  }
  return value as T;
}

function uuid(value: unknown, name: string): string {
  if (
    typeof value !== "string"
    || !UUID.test(value)
    || value.toLowerCase() === ZERO_UUID
  ) return invalid(`正式期初对账响应中的 ${name} 无效`);
  return value.toLowerCase();
}

function nullableUuid(value: unknown, name: string): string | null {
  return value === null ? null : uuid(value, name);
}

function nonnegativeInteger(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    return invalid(`正式期初对账响应中的 ${name} 无效`);
  }
  return value as number;
}

function positiveInteger(value: unknown, name: string): number {
  const checked = nonnegativeInteger(value, name);
  if (checked === 0) return invalid(`正式期初对账响应中的 ${name} 必须为正数`);
  return checked;
}

function booleanValue(value: unknown, name: string): boolean {
  if (typeof value !== "boolean") {
    return invalid(`正式期初对账响应中的 ${name} 无效`);
  }
  return value;
}

function timestamp(value: unknown, name: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  if (
    typeof value !== "string"
    || !ISO_TIMESTAMP.test(value)
    || !Number.isFinite(Date.parse(value))
  ) return invalid(`正式期初对账响应中的 ${name} 无效`);
  return value;
}

function exactText(
  value: unknown,
  name: string,
  options: Readonly<{ minimum?: number; maximum: number; allowEmpty?: boolean }>,
): string {
  if (typeof value !== "string" || value !== value.trim()) {
    return invalid(`正式期初对账响应中的 ${name} 无效`);
  }
  const minimum = options.allowEmpty && value === "" ? 0 : options.minimum ?? 1;
  if (value.length < minimum || value.length > options.maximum || /[\u0000-\u001f\u007f]/.test(value)) {
    return invalid(`正式期初对账响应中的 ${name} 无效`);
  }
  return value;
}

function quantity(value: unknown, name: string, signed = false): string {
  const pattern = signed ? SIGNED_QUANTITY : QUANTITY;
  if (typeof value !== "string" || !pattern.test(value) || value === "-0.000") {
    return invalid(`正式期初对账响应中的 ${name} 无效`);
  }
  return value;
}

function scaledQuantity(value: string): bigint {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [integer, fraction] = unsigned.split(".");
  const scaled = BigInt(integer) * 1000n + BigInt(fraction);
  return negative ? -scaled : scaled;
}

function ledgerCursor(value: unknown): string {
  if (
    typeof value !== "string"
    || !LEDGER_CURSOR.test(value)
    || BigInt(value) > MAX_LEDGER_CURSOR
  ) return invalid("正式期初对账本地账本游标无效");
  return value;
}

function allowedActions(value: unknown): OpeningReconciliationAllowedAction[] {
  if (!Array.isArray(value)) invalid("正式期初对账允许操作不是数组");
  const checked = value.map((action) => exactEnum(
    action,
    RECONCILIATION_ACTIONS,
    "allowed_action",
  ));
  if (new Set(checked).size !== checked.length) {
    invalid("正式期初对账包含重复允许操作");
  }
  return checked;
}

function validateSummary(value: unknown): OpeningReconciliationSummary {
  const object = record(value);
  uuid(field(object, "reconciliation_run_id"), "reconciliation_run_id");
  uuid(field(object, "task_id"), "task_id");
  exactText(field(object, "task_no"), "task_no", { maximum: 100 });
  uuid(field(object, "region_org_id"), "region_org_id");
  const status = exactEnum(field(object, "status"), RECONCILIATION_STATUSES, "status");
  nonnegativeInteger(field(object, "version"), "version");
  const itemCount = positiveInteger(field(object, "item_count"), "item_count");
  const explainedCount = nonnegativeInteger(
    field(object, "explained_item_count"),
    "explained_item_count",
  );
  const resolvedCount = nonnegativeInteger(
    field(object, "resolved_item_count"),
    "resolved_item_count",
  );
  if (
    explainedCount + resolvedCount > itemCount
    || (explainedCount !== 0 && explainedCount !== itemCount)
    || (resolvedCount !== 0 && resolvedCount !== itemCount)
    || (explainedCount > 0 && resolvedCount > 0)
  ) invalid("正式期初对账汇总数量关系无效");
  const externalSnapshotAt = timestamp(
    field(object, "external_snapshot_at"),
    "external_snapshot_at",
  ) as string;
  ledgerCursor(field(object, "local_ledger_cursor"));
  const createdAt = timestamp(field(object, "created_at"), "created_at") as string;
  const approvedAt = timestamp(field(object, "approved_at"), "approved_at", true);
  if (Date.parse(externalSnapshotAt) > Date.parse(createdAt)) {
    invalid("正式期初对账外部快照晚于对账创建时间");
  }
  const actions = allowedActions(field(object, "allowed_actions"));
  if (status === "approved") {
    if (
      approvedAt === null
      || Date.parse(approvedAt) < Date.parse(createdAt)
      || explainedCount !== 0
      || resolvedCount !== itemCount
      || actions.length > 0
    ) invalid("正式期初对账批准状态证据不完整");
  } else if (approvedAt !== null || resolvedCount !== 0) {
    invalid("未批准期初对账夹带批准或解决证据");
  }
  if (actions.includes("approve") && explainedCount !== itemCount) {
    invalid("正式期初对账批准操作与汇总状态不一致");
  }
  return object as OpeningReconciliationSummary;
}

function validateItem(value: unknown): OpeningReconciliationItem {
  const object = record(value);
  uuid(field(object, "reconciliation_item_id"), "reconciliation_item_id");
  uuid(field(object, "stocktake_difference_id"), "stocktake_difference_id");
  uuid(field(object, "control_snapshot_line_id"), "control_snapshot_line_id");
  exactText(field(object, "business_key"), "business_key", { maximum: 300 });
  nullableUuid(field(object, "material_id"), "material_id");
  const externalQuantity = quantity(field(object, "external_qty"), "external_qty");
  const localQuantity = quantity(field(object, "local_qty"), "local_qty");
  const difference = quantity(field(object, "difference"), "difference", true);
  if (scaledQuantity(difference) !== scaledQuantity(externalQuantity) - scaledQuantity(localQuantity)) {
    invalid("正式期初对账左右账差异计算不一致");
  }
  const status = exactEnum(
    field(object, "status"),
    RECONCILIATION_ITEM_STATUSES,
    "item_status",
  );
  nonnegativeInteger(field(object, "version"), "item_version");
  const explanation = exactText(field(object, "explanation"), "explanation", {
    minimum: 4,
    maximum: 4000,
    allowEmpty: true,
  });
  const evidenceReference = exactText(
    field(object, "evidence_reference"),
    "evidence_reference",
    { minimum: 4, maximum: 1000, allowEmpty: true },
  );
  const evidenceFileId = nullableUuid(field(object, "evidence_file_id"), "evidence_file_id");
  const explainedAt = timestamp(field(object, "explained_at"), "explained_at", true);
  if (status === "difference") {
    if (
      explanation !== ""
      || evidenceReference !== ""
      || evidenceFileId !== null
      || explainedAt !== null
    ) invalid("未解释期初对账项夹带解释证据");
  } else if (!explanation || !evidenceReference || explainedAt === null) {
    invalid("已解释期初对账项缺少原因或证据引用");
  }
  return object as OpeningReconciliationItem;
}

export function validateOpeningReconciliationPage(value: unknown): OpeningReconciliationPage {
  const object = record(value);
  schemaVersion(object);
  const items = field(object, "items");
  if (!Array.isArray(items)) invalid("正式期初对账列表不是数组");
  if (items.length > 20) invalid("正式期初对账列表超过分页上限");
  const runIds = new Set<string>();
  const taskIds = new Set<string>();
  let previousRunId: string | null = null;
  for (const source of items as unknown[]) {
    const checked = validateSummary(source);
    const runId = checked.reconciliation_run_id.toLowerCase();
    const taskId = checked.task_id.toLowerCase();
    if (runIds.has(runId) || taskIds.has(taskId)) {
      invalid("正式期初对账列表包含重复任务或对账批次");
    }
    if (previousRunId !== null && runId <= previousRunId) {
      invalid("正式期初对账列表未按批次标识严格递增");
    }
    runIds.add(runId);
    taskIds.add(taskId);
    previousRunId = runId;
  }
  const nextAfterId = nullableUuid(field(object, "next_after_id"), "next_after_id");
  if (nextAfterId !== null && (previousRunId === null || nextAfterId !== previousRunId)) {
    invalid("正式期初对账下一页游标与本页末项不一致");
  }
  return object as OpeningReconciliationPage;
}

export function validateOpeningReconciliationDetail(value: unknown): OpeningReconciliationDetail {
  const object = record(value);
  schemaVersion(object);
  const summary = validateSummary(object);
  uuid(field(object, "source_system_id"), "source_system_id");
  uuid(field(object, "round_id"), "round_id");
  uuid(field(object, "posting_id"), "posting_id");
  const manifest = field(object, "difference_manifest_sha256");
  if (typeof manifest !== "string" || !SHA256.test(manifest)) {
    invalid("正式期初对账差异清单摘要无效");
  }
  const approvalComment = exactText(
    field(object, "approval_comment"),
    "approval_comment",
    { minimum: 4, maximum: 4000, allowEmpty: true },
  );
  const items = field(object, "items");
  if (!Array.isArray(items) || items.length !== summary.item_count) {
    invalid("正式期初对账详情项数与汇总不一致");
  }
  const itemIds = new Set<string>();
  const differenceIds = new Set<string>();
  const controlLineIds = new Set<string>();
  const businessKeys = new Set<string>();
  let explainedCount = 0;
  let resolvedCount = 0;
  for (const source of items as unknown[]) {
    const item = validateItem(source);
    const itemId = item.reconciliation_item_id.toLowerCase();
    const differenceId = item.stocktake_difference_id.toLowerCase();
    const controlLineId = item.control_snapshot_line_id.toLowerCase();
    if (
      itemIds.has(itemId)
      || differenceIds.has(differenceId)
      || controlLineIds.has(controlLineId)
      || businessKeys.has(item.business_key)
    ) invalid("正式期初对账详情包含重复差异证据");
    itemIds.add(itemId);
    differenceIds.add(differenceId);
    controlLineIds.add(controlLineId);
    businessKeys.add(item.business_key);
    if (item.status === "explained") explainedCount += 1;
    if (item.status === "resolved") resolvedCount += 1;
  }
  if (
    explainedCount !== summary.explained_item_count
    || resolvedCount !== summary.resolved_item_count
  ) invalid("正式期初对账详情状态数量与汇总不一致");
  if (summary.status === "approved") {
    if (resolvedCount !== summary.item_count || !approvalComment) {
      invalid("已批准期初对账缺少逐项解决事实或批准说明");
    }
  } else if (resolvedCount !== 0 || approvalComment !== "") {
    invalid("未批准期初对账夹带解决结果或批准说明");
  }
  return object as OpeningReconciliationDetail;
}

function validateStartResult(value: unknown): OpeningReconciliationStartResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "reconciliation_run_id"), "reconciliation_run_id");
  uuid(field(object, "task_id"), "task_id");
  if (field(object, "status") !== "differences") invalid("正式期初对账创建结果状态无效");
  nonnegativeInteger(field(object, "version"), "version");
  positiveInteger(field(object, "item_count"), "item_count");
  timestamp(field(object, "created_at"), "created_at");
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningReconciliationStartResult;
}

function validateExplainResult(value: unknown): OpeningReconciliationExplainResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "reconciliation_run_id"), "reconciliation_run_id");
  uuid(field(object, "task_id"), "task_id");
  if (field(object, "status") !== "differences") invalid("正式期初对账解释结果状态无效");
  positiveInteger(field(object, "version"), "version");
  positiveInteger(field(object, "explained_item_count"), "explained_item_count");
  timestamp(field(object, "explained_at"), "explained_at");
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningReconciliationExplainResult;
}

function validateApproveResult(value: unknown): OpeningReconciliationApproveResult {
  const object = record(value);
  schemaVersion(object);
  uuid(field(object, "reconciliation_run_id"), "reconciliation_run_id");
  uuid(field(object, "task_id"), "task_id");
  if (field(object, "status") !== "approved") invalid("正式期初对账批准结果状态无效");
  positiveInteger(field(object, "version"), "version");
  positiveInteger(field(object, "resolved_item_count"), "resolved_item_count");
  timestamp(field(object, "approved_at"), "approved_at");
  booleanValue(field(object, "replayed"), "replayed");
  return object as OpeningReconciliationApproveResult;
}

export function openingReconciliationPath(runId: string): string {
  return `${FORMAL_OPENING_RECONCILIATION_PATH}/${uuid(runId, "reconciliation_run_id")}`;
}

export async function loadOpeningReconciliations(
  afterId: string | null = null,
): Promise<OpeningReconciliationPage> {
  const query = new URLSearchParams({ limit: "20" });
  if (afterId !== null) query.set("after_id", uuid(afterId, "after_id"));
  const page = validateOpeningReconciliationPage(await api<unknown>(
    `${FORMAL_OPENING_RECONCILIATION_PATH}?${query.toString()}`,
  ));
  if (
    afterId !== null
    && page.items.length > 0
    && page.items[0].reconciliation_run_id.toLowerCase() <= afterId.toLowerCase()
  ) invalid("正式期初对账下一页未越过请求游标");
  return page;
}

export async function loadOpeningReconciliationDetail(
  runId: string,
): Promise<OpeningReconciliationDetail> {
  const checkedRunId = uuid(runId, "reconciliation_run_id");
  const detail = validateOpeningReconciliationDetail(await api<unknown>(
    openingReconciliationPath(checkedRunId),
  ));
  if (detail.reconciliation_run_id.toLowerCase() !== checkedRunId) {
    invalid("正式期初对账详情与目标批次不一致");
  }
  return detail;
}

function transportedJsonValue(value: unknown): unknown {
  const transported = JSON.stringify(value);
  if (transported === undefined) invalid("正式期初对账写入内容不可序列化");
  return JSON.parse(transported);
}

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
  return JSON.stringify(sortedJsonValue(transportedJsonValue(value)));
}

function intentSignature(options: Readonly<{
  targetKey: string;
  action: ReconciliationMutationAction;
  path: string;
  body: unknown;
}>): string {
  return canonicalJson({
    target_key: options.targetKey,
    action: options.action,
    path: options.path,
    body: options.body,
  });
}

function safeRequestId(coordinates: ReconciliationMutationCoordinates): string {
  try {
    const value = new Headers(coordinates.headers).get("X-Request-ID");
    return value && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
      ? value
      : "unavailable";
  } catch {
    return "unavailable";
  }
}

export class OpeningReconciliationHandoffError extends ApiError {
  readonly code = "opening_reconciliation_handoff_required";
  readonly target_id: string;
  readonly action: ReconciliationMutationAction;
  readonly path: string;
  readonly request_id: string;

  constructor(intent: ReconciliationMutationIntent, reason: "different_request" | "state_advanced") {
    const requestId = safeRequestId(intent.coordinates);
    super(
      409,
      `正式期初对账存在未确认写入，已停止同一对象的新写；请人工核验 action=${intent.action} request_id=${requestId} reason=${reason}`,
    );
    this.name = "OpeningReconciliationHandoffError";
    this.target_id = intent.targetId;
    this.action = intent.action;
    this.path = intent.path;
    this.request_id = requestId;
  }
}

function handoff(
  intent: ReconciliationMutationIntent,
  reason: "different_request" | "state_advanced",
): never {
  throw new OpeningReconciliationHandoffError(intent, reason);
}

function isDefinitiveClientRejection(error: unknown): boolean {
  return error instanceof ApiError
    && error.responseReceived
    && error.status >= 400
    && error.status < 500;
}

function exactPendingIntent(
  targetKey: string,
  targetId: string,
  action: ReconciliationMutationAction,
  path: string,
  body: unknown,
): ReconciliationMutationIntent | undefined {
  const intent = uncertainReconciliationIntents.get(targetKey);
  if (!intent) return undefined;
  const signature = intentSignature({ targetKey, action, path, body });
  if (
    intent.targetId !== targetId
    || intent.action !== action
    || intent.signature !== signature
  ) handoff(intent, "different_request");
  return intent;
}

async function executeMutationIntent<TResult>(options: Readonly<{
  targetKey: string;
  targetId: string;
  action: ReconciliationMutationAction;
  prefix: string;
  path: string;
  body: unknown;
  before: unknown;
  accept: (value: unknown) => TResult;
  reread: () => Promise<unknown>;
}>): Promise<Readonly<{ result: TResult; intent: ReconciliationMutationIntent }>> {
  const signature = intentSignature(options);
  let intent = uncertainReconciliationIntents.get(options.targetKey);
  if (intent && intent.signature !== signature) handoff(intent, "different_request");
  if (!intent) {
    if (uncertainReconciliationIntents.size >= MAX_UNCERTAIN_RECONCILIATION_INTENTS) {
      invalid("存在过多结果未确认的正式期初对账写入，已停止创建新请求坐标");
    }
    intent = {
      targetKey: options.targetKey,
      targetId: options.targetId,
      action: options.action,
      path: options.path,
      body: transportedJsonValue(options.body),
      signature,
      coordinates: mutationHeaders(options.prefix),
      before: transportedJsonValue(options.before),
      accepted: false,
    };
    uncertainReconciliationIntents.set(options.targetKey, intent);
  }
  if (intent.accepted) {
    return {
      result: options.accept(transportedJsonValue(intent.acceptedResponse)),
      intent,
    };
  }
  try {
    const response = transportedJsonValue(await api<unknown>(intent.path, {
      method: "POST",
      ...intent.coordinates,
      ...jsonBody(intent.body),
    }));
    const result = options.accept(transportedJsonValue(response));
    intent.acceptedResponse = response;
    intent.accepted = true;
    return { result, intent };
  } catch (error) {
    if (isDefinitiveClientRejection(error)) {
      if (uncertainReconciliationIntents.get(options.targetKey) === intent) {
        uncertainReconciliationIntents.delete(options.targetKey);
      }
    } else {
      try {
        await options.reread();
      } catch {
        // Preserve the original uncertain mutation outcome as the actionable error.
      }
    }
    throw error;
  }
}

function confirmIntent(targetKey: string, intent: ReconciliationMutationIntent): void {
  if (uncertainReconciliationIntents.get(targetKey) === intent) {
    uncertainReconciliationIntents.delete(targetKey);
  }
}

function exactTaskDetail(value: unknown, taskId: string): OpeningStocktakeTaskDetail {
  const detail = validateOpeningStocktakeTaskDetail(value);
  if (detail.task_id.toLowerCase() !== taskId.toLowerCase()) {
    invalid("正式期初对账创建前盘点详情与目标任务不一致");
  }
  return detail;
}

function exactRunDetail(value: unknown, runId: string): OpeningReconciliationDetail {
  const detail = validateOpeningReconciliationDetail(value);
  if (detail.reconciliation_run_id.toLowerCase() !== runId.toLowerCase()) {
    invalid("正式期初对账详情与目标批次不一致");
  }
  return detail;
}

function requireAction(
  detail: OpeningReconciliationDetail,
  action: OpeningReconciliationAllowedAction,
): void {
  if (!detail.allowed_actions.includes(action)) {
    invalid("服务端未授权当前正式期初对账操作");
  }
}

export async function startOpeningReconciliation(
  taskId: string,
): Promise<OpeningReconciliationMutationResult<OpeningStocktakeTaskDetail, OpeningReconciliationStartResult>> {
  const checkedTaskId = uuid(taskId, "task_id");
  const targetKey = `task:${checkedTaskId}`;
  const path = `${FORMAL_OPENING_RECONCILIATION_PATH}/tasks/${checkedTaskId}`;
  const pending = uncertainReconciliationIntents.get(targetKey);
  let before: OpeningStocktakeTaskDetail;
  if (pending) {
    if (pending.action !== "start") handoff(pending, "different_request");
    before = exactTaskDetail(transportedJsonValue(pending.before), checkedTaskId);
  } else {
    before = await loadFormalOpeningStocktakeDetail(checkedTaskId);
    if (before.status !== "posted") invalid("只有已独立过账的期初任务可以创建控制账对账");
  }
  const body = { expected_task_version: before.task_version };
  const exactPending = exactPendingIntent(
    targetKey,
    checkedTaskId,
    "start",
    path,
    body,
  );
  if (exactPending && !exactPending.accepted) {
    const current = await loadFormalOpeningStocktakeDetail(checkedTaskId);
    if (current.status !== "posted" || current.task_version !== before.task_version) {
      handoff(exactPending, "state_advanced");
    }
  }
  const attempt = await executeMutationIntent({
    targetKey,
    targetId: checkedTaskId,
    action: "start",
    prefix: "opening-reconciliation-start",
    path,
    body,
    before,
    accept: (value) => {
      const result = validateStartResult(value);
      if (result.task_id.toLowerCase() !== checkedTaskId) {
        invalid("正式期初对账创建结果与目标任务不一致");
      }
      return result;
    },
    reread: () => loadFormalOpeningStocktakeDetail(checkedTaskId),
  });
  const detail = await loadOpeningReconciliationDetail(
    attempt.result.reconciliation_run_id,
  );
  if (
    detail.task_id.toLowerCase() !== checkedTaskId
    || detail.version < attempt.result.version
    || detail.item_count !== attempt.result.item_count
    || detail.created_at !== attempt.result.created_at
  ) invalid("正式期初对账创建写后详情未包含创建结果");
  confirmIntent(targetKey, attempt.intent);
  return { before, result: attempt.result, detail };
}

function normalizedExplanationInput(
  value: OpeningReconciliationExplanationInput,
): Readonly<{
  reconciliation_item_id: string;
  explanation: string;
  evidence_reference: string;
  evidence_file_id: string | null;
}> {
  return {
    reconciliation_item_id: uuid(value.reconciliation_item_id, "reconciliation_item_id"),
    explanation: exactText(value.explanation, "explanation", { minimum: 4, maximum: 4000 }),
    evidence_reference: exactText(value.evidence_reference, "evidence_reference", {
      minimum: 4,
      maximum: 1000,
    }),
    evidence_file_id: value.evidence_file_id == null
      ? null
      : uuid(value.evidence_file_id, "evidence_file_id"),
  };
}

function explanationBody(
  detail: OpeningReconciliationDetail,
  inputs: readonly OpeningReconciliationExplanationInput[],
): Readonly<{
  expected_version: number;
  items: ReadonlyArray<{
    reconciliation_item_id: string;
    expected_version: number;
    explanation: string;
    evidence_reference: string;
    evidence_file_id: string | null;
  }>;
}> {
  requireAction(detail, "explain");
  if (detail.items.some((item) => item.status === "resolved")) {
    invalid("已批准解决的正式期初对账差异不得重新解释");
  }
  const normalized = new Map<string, ReturnType<typeof normalizedExplanationInput>>();
  for (const input of inputs) {
    const checked = normalizedExplanationInput(input);
    if (normalized.has(checked.reconciliation_item_id)) {
      invalid("正式期初对账解释包含重复差异项");
    }
    normalized.set(checked.reconciliation_item_id, checked);
  }
  if (normalized.size !== detail.items.length) {
    invalid("正式期初对账解释必须逐项覆盖全部待核差异");
  }
  const items = detail.items.map((item) => {
    const input = normalized.get(item.reconciliation_item_id.toLowerCase());
    if (!input) invalid("正式期初对账解释项不属于当前批次");
    return {
      reconciliation_item_id: item.reconciliation_item_id,
      expected_version: item.version,
      explanation: input.explanation,
      evidence_reference: input.evidence_reference,
      evidence_file_id: input.evidence_file_id,
    };
  });
  return { expected_version: detail.version, items };
}

function sameNullableUuid(left: string | null, right: string | null): boolean {
  return left === null ? right === null : right?.toLowerCase() === left.toLowerCase();
}

export async function explainOpeningReconciliation(
  runId: string,
  inputs: readonly OpeningReconciliationExplanationInput[],
): Promise<OpeningReconciliationMutationResult<OpeningReconciliationDetail, OpeningReconciliationExplainResult>> {
  const checkedRunId = uuid(runId, "reconciliation_run_id");
  const targetKey = `run:${checkedRunId}`;
  const path = `${openingReconciliationPath(checkedRunId)}/explanations`;
  const pending = uncertainReconciliationIntents.get(targetKey);
  let before: OpeningReconciliationDetail;
  if (pending) {
    if (pending.action !== "explain") handoff(pending, "different_request");
    before = exactRunDetail(transportedJsonValue(pending.before), checkedRunId);
  } else {
    before = await loadOpeningReconciliationDetail(checkedRunId);
  }
  const body = explanationBody(before, inputs);
  const exactPending = exactPendingIntent(
    targetKey,
    checkedRunId,
    "explain",
    path,
    body,
  );
  if (exactPending && !exactPending.accepted) {
    const current = await loadOpeningReconciliationDetail(checkedRunId);
    try {
      const currentBody = explanationBody(current, inputs);
      if (
        current.version !== before.version
        || intentSignature({ targetKey, action: "explain", path, body: currentBody })
          !== exactPending.signature
      ) handoff(exactPending, "state_advanced");
    } catch (error) {
      if (error instanceof OpeningReconciliationHandoffError) throw error;
      handoff(exactPending, "state_advanced");
    }
  }
  const attempt = await executeMutationIntent({
    targetKey,
    targetId: checkedRunId,
    action: "explain",
    prefix: "opening-reconciliation-explain",
    path,
    body,
    before,
    accept: (value) => {
      const result = validateExplainResult(value);
      if (
        result.reconciliation_run_id.toLowerCase() !== checkedRunId
        || result.task_id.toLowerCase() !== before.task_id.toLowerCase()
        || result.version !== before.version + 1
        || result.explained_item_count !== before.item_count
      ) invalid("正式期初对账解释结果与目标批次或版本不一致");
      return result;
    },
    reread: () => loadOpeningReconciliationDetail(checkedRunId),
  });
  const detail = await loadOpeningReconciliationDetail(checkedRunId);
  if (
    detail.version < attempt.result.version
    || detail.explained_item_count < attempt.result.explained_item_count
  ) invalid("正式期初对账解释写后详情未包含解释结果");
  for (const row of body.items) {
    const item = detail.items.find(
      (candidate) => candidate.reconciliation_item_id.toLowerCase()
        === row.reconciliation_item_id.toLowerCase(),
    );
    if (
      !item
      || item.status === "difference"
      || item.explanation !== row.explanation
      || item.evidence_reference !== row.evidence_reference
      || !sameNullableUuid(item.evidence_file_id, row.evidence_file_id)
    ) invalid("正式期初对账解释写后详情与提交证据不一致");
  }
  confirmIntent(targetKey, attempt.intent);
  return { before, result: attempt.result, detail };
}

export async function approveOpeningReconciliation(
  runId: string,
  comment: string,
): Promise<OpeningReconciliationMutationResult<OpeningReconciliationDetail, OpeningReconciliationApproveResult>> {
  const checkedRunId = uuid(runId, "reconciliation_run_id");
  const targetKey = `run:${checkedRunId}`;
  const path = `${openingReconciliationPath(checkedRunId)}/approve`;
  const pending = uncertainReconciliationIntents.get(targetKey);
  let before: OpeningReconciliationDetail;
  if (pending) {
    if (pending.action !== "approve") handoff(pending, "different_request");
    before = exactRunDetail(transportedJsonValue(pending.before), checkedRunId);
  } else {
    before = await loadOpeningReconciliationDetail(checkedRunId);
  }
  requireAction(before, "approve");
  const checkedComment = exactText(comment, "comment", { minimum: 4, maximum: 4000 });
  const body = { expected_version: before.version, comment: checkedComment };
  const exactPending = exactPendingIntent(
    targetKey,
    checkedRunId,
    "approve",
    path,
    body,
  );
  if (exactPending && !exactPending.accepted) {
    const current = await loadOpeningReconciliationDetail(checkedRunId);
    if (
      current.version !== before.version
      || !current.allowed_actions.includes("approve")
    ) handoff(exactPending, "state_advanced");
  }
  const attempt = await executeMutationIntent({
    targetKey,
    targetId: checkedRunId,
    action: "approve",
    prefix: "opening-reconciliation-approve",
    path,
    body,
    before,
    accept: (value) => {
      const result = validateApproveResult(value);
      if (
        result.reconciliation_run_id.toLowerCase() !== checkedRunId
        || result.task_id.toLowerCase() !== before.task_id.toLowerCase()
        || result.version !== before.version + 1
        || result.resolved_item_count !== before.item_count
      ) invalid("正式期初对账批准结果与目标批次或版本不一致");
      return result;
    },
    reread: () => loadOpeningReconciliationDetail(checkedRunId),
  });
  const detail = await loadOpeningReconciliationDetail(checkedRunId);
  if (
    detail.status !== "approved"
    || detail.version < attempt.result.version
    || detail.resolved_item_count !== detail.item_count
    || detail.approval_comment !== checkedComment
    || detail.approved_at !== attempt.result.approved_at
  ) invalid("正式期初对账批准写后详情未包含批准结果");
  confirmIntent(targetKey, attempt.intent);
  return { before, result: attempt.result, detail };
}
