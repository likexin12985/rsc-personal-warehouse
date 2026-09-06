import { ApiError } from "./api";
import {
  validateFormalStocktakeDetail,
  type FormalStocktakeDetail,
  type FormalStocktakeIntent,
} from "./formalStocktakes";
import type {
  FormalStocktakeAccess,
  FormalStocktakeAdapter,
  FormalStocktakeExecuteOptions,
  FormalStocktakeExpectedIdentity,
} from "./formalStocktakeAdapter";
import {
  validateFormalStocktakeReviewSentinel,
  type FormalStocktakeReviewRecoveryStore,
  type FormalStocktakeReviewSentinel,
  type FormalStocktakeReviewStage,
  type FormalStocktakeReviewTaskLease,
  getFormalStocktakeReviewRecoveryStore,
} from "./formalStocktakeReviewRecoveryStore";

type Identity = Readonly<{ person_id: string; authorization_version: number }>;

export type FormalStocktakeReviewHistoricalCommand = Readonly<{
  review_id: string;
  task_id: string;
  round_id: string;
  review_stage: FormalStocktakeReviewStage;
  decision: "approve" | "recount" | "reject";
  resulting_task_status: "hq_review" | "recount_required" | "approved";
  expected_task_version: number;
  resulting_task_version: number;
  task_version: number;
  item_count: number;
  pending_verification_count: number;
  ready_for_posting: boolean;
  reviewed_at: string;
}>;

export type FormalStocktakeReviewCommandStatus = Readonly<{
  schema_version: "1.0";
  task_id: string;
  round_id: string;
  review_stage: FormalStocktakeReviewStage;
  actor_person_id: string;
  actor_authorization_version: number;
  trace_request_id: string;
}> & (
  | Readonly<{ lookup_status: "not_observed"; command: null }>
  | Readonly<{ lookup_status: "confirmed"; command: FormalStocktakeReviewHistoricalCommand }>
);

type RecoveryAdapter = Pick<FormalStocktakeAdapter, "loadAccess" | "detail" | "execute"> & {
  loadIdentityNoReplay?: () => Promise<unknown>;
  loadAccessNoReplay?: () => Promise<FormalStocktakeAccess>;
  detailNoReplay?: (taskId: string) => Promise<FormalStocktakeDetail>;
  reviewCommandStatus?: (
    taskId: string,
    roundId: string,
    reviewStage: FormalStocktakeReviewStage,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ) => Promise<unknown>;
};

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;

function fail(message = "盘点复核恢复证据与原请求不一致，继续保持待核验"): never {
  throw new ApiError(409, message);
}

function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== fields.length || fields.some((field) => !Object.hasOwn(row, field))) fail();
  return row;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) fail(`${name}无效`);
  return value.toLowerCase();
}

function integer(value: unknown, name: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) fail(`${name}无效`);
  return value as number;
}

function timestamp(value: unknown, name: string): string {
  if (typeof value !== "string" || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) fail(`${name}无效`);
  return value;
}

function instant(value: string): bigint {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) fail("时间戳无效");
  const base = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(base) || new Date(base).toISOString().slice(0, 19) !== match[1]) fail("时间戳无效");
  const hours = Number(match[5] ?? 0);
  const minutes = Number(match[6] ?? 0);
  if (hours > 23 || minutes > 59) fail("时间戳无效");
  const offset = (hours * 60 + minutes) * 60_000 * (match[4] === "-" ? -1 : 1);
  return BigInt(base - offset) * 1000n + BigInt((match[2] ?? "").padEnd(6, "0"));
}

function identity(value: unknown): Identity {
  const row = exact(value, ["person_id", "authorization_version"]);
  return Object.freeze({
    person_id: uuid(row.person_id, "person_id"),
    authorization_version: integer(row.authorization_version, "authorization_version", 1),
  });
}

function activeIdentity(value: unknown, expected: Identity): Identity {
  const row = exact(value, [
    "person_id", "name", "employee_no", "organization_code", "organization_name",
    "account_status", "employment_status", "access_mode", "authorization_version", "role_codes",
  ]);
  const current = identity({ person_id: row.person_id, authorization_version: row.authorization_version });
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) {
    fail("登录人员或权限已变化，原盘点复核继续保持待核验");
  }
  if (row.account_status !== "active" || row.employment_status !== "active" || row.access_mode !== "active") {
    fail("当前身份不能核验盘点复核");
  }
  for (const [field, limit] of [["name", 160], ["employee_no", 80], ["organization_code", 120], ["organization_name", 240]] as const) {
    const text = row[field];
    if (typeof text !== "string" || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail();
  }
  if (!Array.isArray(row.role_codes) || !row.role_codes.length || new Set(row.role_codes).size !== row.role_codes.length
    || row.role_codes.some((role) => !["admin", "provincial_manager", "technician", "star_headquarters_approver"].includes(String(role)))
    || !row.role_codes.some((role) => ["admin", "provincial_manager", "technician"].includes(String(role)))) {
    fail("当前身份角色不能核验盘点复核");
  }
  return current;
}

function sameIdentity(value: Identity, expected: Identity): void {
  if (value.person_id !== expected.person_id || value.authorization_version !== expected.authorization_version) {
    fail("登录人员或权限已变化，原盘点复核继续保持待核验");
  }
}

function requireReviewAccess(access: FormalStocktakeAccess, expected: Identity, stage: FormalStocktakeReviewStage): void {
  if (access.schema_version !== "1.0" || access.person_id !== expected.person_id || access.authorization_version !== expected.authorization_version || !access.can_read) {
    fail("当前正式权限不能核验盘点复核");
  }
  if (stage === "region" ? !access.can_review_region : !access.can_review_headquarters) {
    fail("当前正式权限不能核验该阶段盘点复核");
  }
}

function requireAdapter(adapter: RecoveryAdapter): asserts adapter is RecoveryAdapter & {
  loadIdentityNoReplay: () => Promise<unknown>;
  loadAccessNoReplay: () => Promise<FormalStocktakeAccess>;
  detailNoReplay: (taskId: string) => Promise<FormalStocktakeDetail>;
  reviewCommandStatus: (
    taskId: string,
    roundId: string,
    reviewStage: FormalStocktakeReviewStage,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ) => Promise<unknown>;
} {
  if (
    typeof adapter.loadIdentityNoReplay !== "function"
    || typeof adapter.loadAccessNoReplay !== "function"
    || typeof adapter.detailNoReplay !== "function"
    || typeof adapter.reviewCommandStatus !== "function"
  ) fail("当前盘点客户端未提供只读复核恢复能力，已停止写入");
}

function expectedResultingStatus(stage: FormalStocktakeReviewStage, decision: FormalStocktakeReviewHistoricalCommand["decision"]): FormalStocktakeReviewHistoricalCommand["resulting_task_status"] {
  if (decision !== "approve") return "recount_required";
  return stage === "region" ? "hq_review" : "approved";
}

function validateSentinelAgainstIntent(intent: FormalStocktakeIntent, expected: Identity): FormalStocktakeReviewSentinel {
  const stage: FormalStocktakeReviewStage = intent.action === "review_region" ? "region" : intent.action === "review_headquarters" ? "headquarters" : fail("只允许恢复盘点复核");
  if (!intent.taskId || !intent.roundId || intent.expectedTaskVersion === null) fail("盘点复核意图缺少完整坐标");
  const body = exact(intent.body, ["expected_task_version", "decision", "items", "comment"]);
  if (integer(body.expected_task_version, "expected_task_version") !== intent.expectedTaskVersion) fail("复核版本坐标不一致");
  const trace = intent.headers["X-Request-ID"];
  if (typeof trace !== "string" || !TRACE.test(trace)) fail("复核追踪坐标无效");
  return validateFormalStocktakeReviewSentinel({
    v: 1,
    kind: "formal_stocktake_review",
    task_id: uuid(intent.taskId, "task_id"),
    round_id: uuid(intent.roundId, "round_id"),
    review_stage: stage,
    actor_person_id: expected.person_id,
    actor_authorization_version: expected.authorization_version,
    expected_task_version: intent.expectedTaskVersion,
    trace_request_id: trace,
  });
}

export function validateFormalStocktakeReviewCommandStatus(
  value: unknown,
  original: FormalStocktakeReviewSentinel,
): FormalStocktakeReviewCommandStatus {
  const sentinel = validateFormalStocktakeReviewSentinel(original);
  const row = exact(value, [
    "schema_version", "task_id", "round_id", "review_stage", "actor_person_id",
    "actor_authorization_version", "trace_request_id", "lookup_status", "command",
  ]);
  const anchors = {
    schema_version: "1.0" as const,
    task_id: sentinel.task_id,
    round_id: sentinel.round_id,
    review_stage: sentinel.review_stage,
    actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version,
    trace_request_id: sentinel.trace_request_id,
  };
  if (
    row.schema_version !== "1.0"
    || uuid(row.task_id, "task_id") !== sentinel.task_id
    || uuid(row.round_id, "round_id") !== sentinel.round_id
    || row.review_stage !== sentinel.review_stage
    || uuid(row.actor_person_id, "actor_person_id") !== sentinel.actor_person_id
    || integer(row.actor_authorization_version, "actor_authorization_version", 1) !== sentinel.actor_authorization_version
    || typeof row.trace_request_id !== "string"
    || row.trace_request_id !== sentinel.trace_request_id
  ) fail();
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) fail();
    return Object.freeze({ ...anchors, lookup_status: "not_observed", command: null });
  }
  if (row.lookup_status !== "confirmed") fail();
  const command = exact(row.command, [
    "review_id", "task_id", "round_id", "review_stage", "decision", "resulting_task_status",
    "expected_task_version", "resulting_task_version", "task_version", "item_count",
    "pending_verification_count", "ready_for_posting", "reviewed_at",
  ]);
  const decision = command.decision;
  if (decision !== "approve" && decision !== "recount" && decision !== "reject") fail("复核结论无效");
  const checked = {
    review_id: uuid(command.review_id, "review_id"),
    task_id: uuid(command.task_id, "command.task_id"),
    round_id: uuid(command.round_id, "command.round_id"),
    review_stage: command.review_stage,
    decision,
    resulting_task_status: command.resulting_task_status,
    expected_task_version: integer(command.expected_task_version, "expected_task_version"),
    resulting_task_version: integer(command.resulting_task_version, "resulting_task_version", 1),
    task_version: integer(command.task_version, "task_version", 1),
    item_count: integer(command.item_count, "item_count"),
    pending_verification_count: integer(command.pending_verification_count, "pending_verification_count"),
    ready_for_posting: command.ready_for_posting,
    reviewed_at: timestamp(command.reviewed_at, "reviewed_at"),
  } as FormalStocktakeReviewHistoricalCommand;
  if (
    checked.task_id !== sentinel.task_id
    || checked.round_id !== sentinel.round_id
    || checked.review_stage !== sentinel.review_stage
    || checked.expected_task_version !== sentinel.expected_task_version
    || checked.resulting_task_version !== checked.expected_task_version + 1
    || checked.task_version !== checked.resulting_task_version
    || checked.pending_verification_count > checked.item_count
    || checked.resulting_task_status !== expectedResultingStatus(checked.review_stage, checked.decision)
    || checked.ready_for_posting !== (checked.review_stage === "headquarters" && checked.decision === "approve" && checked.pending_verification_count === 0)
  ) fail("盘点复核历史命令版本、状态或数量摘要无效");
  instant(checked.reviewed_at);
  return Object.freeze({ ...anchors, lookup_status: "confirmed", command: Object.freeze(checked) });
}

/** Validate the current projection against the immutable review fact. */
export function validateFormalStocktakeReviewRecoveredProjection(
  value: unknown,
  sentinel: FormalStocktakeReviewSentinel,
  command: FormalStocktakeReviewHistoricalCommand,
): FormalStocktakeDetail {
  const checkedSentinel = validateFormalStocktakeReviewSentinel(sentinel);
  if (
    command.task_id !== checkedSentinel.task_id
    || command.round_id !== checkedSentinel.round_id
    || command.review_stage !== checkedSentinel.review_stage
    || command.expected_task_version !== checkedSentinel.expected_task_version
  ) fail();
  const detail = validateFormalStocktakeDetail(value);
  if (detail.task_id !== checkedSentinel.task_id || detail.version < command.task_version || detail.status === "draft" || detail.status === "cancelled") fail("当前盘点详情与复核历史事实不一致");
  const round = detail.rounds.find((row) => row.round_id === checkedSentinel.round_id);
  if (!round) fail("当前盘点详情缺少原复核轮次");
  const review = checkedSentinel.review_stage === "region" ? round.region_review : round.headquarters_review;
  if (!review || review.review_id !== command.review_id || review.review_stage !== command.review_stage || review.decision !== command.decision || review.reviewer_person_id !== checkedSentinel.actor_person_id || instant(review.reviewed_at) !== instant(command.reviewed_at) || !review.covers_all_task_scopes) {
    fail("当前盘点详情缺少匹配的复核责任链");
  }
  if (round.visible_differences.length !== command.item_count || review.visible_items.length !== command.item_count) fail("当前盘点详情复核范围与历史摘要不一致");
  const differenceIds = new Set(round.visible_differences.map((item) => item.difference_id));
  const itemIds = new Set(review.visible_items.map((item) => item.difference_id));
  if (differenceIds.size !== command.item_count || itemIds.size !== command.item_count || [...differenceIds].some((id) => !itemIds.has(id))) fail("当前盘点详情复核逐项责任链不完整");
  const pending = round.visible_differences.filter((item) => item.posting_blocked_by_pending_verification).length;
  if (pending !== command.pending_verification_count || command.ready_for_posting !== (command.review_stage === "headquarters" && command.decision === "approve" && pending === 0)) fail("当前盘点详情待核验摘要不一致");
  if (detail.version === command.task_version && detail.status !== command.resulting_task_status) fail("复核历史命令与当前任务状态不一致");
  return detail;
}

export class FormalStocktakeReviewSubmissionPendingError extends ApiError {
  readonly task_id: string;
  readonly round_id: string;
  readonly review_stage: FormalStocktakeReviewStage;
  readonly expected_task_version: number;
  readonly actor_person_id: string;
  readonly actor_authorization_version: number;
  readonly trace_request_id: string;

  constructor(sentinel: FormalStocktakeReviewSentinel) {
    super(409, "原盘点复核仍待只读核验；不能重新提交");
    this.name = "FormalStocktakeReviewSubmissionPendingError";
    this.task_id = sentinel.task_id;
    this.round_id = sentinel.round_id;
    this.review_stage = sentinel.review_stage;
    this.expected_task_version = sentinel.expected_task_version;
    this.actor_person_id = sentinel.actor_person_id;
    this.actor_authorization_version = sentinel.actor_authorization_version;
    this.trace_request_id = sentinel.trace_request_id;
  }
}

async function requireCurrent(adapter: RecoveryAdapter, expected: Identity, stage: FormalStocktakeReviewStage): Promise<void> {
  requireAdapter(adapter);
  sameIdentity(activeIdentity(await adapter.loadIdentityNoReplay(), expected), expected);
  requireReviewAccess(await adapter.loadAccessNoReplay(), expected, stage);
}

/** Read-only recovery: never creates coordinates or replays a review POST. */
export async function recoverFormalStocktakeReview(
  lease: FormalStocktakeReviewTaskLease,
  value: FormalStocktakeReviewSentinel,
  adapter: RecoveryAdapter,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: FormalStocktakeReviewHistoricalCommand; detail: FormalStocktakeDetail }>> {
  const sentinel = validateFormalStocktakeReviewSentinel(value);
  const stored = lease.read();
  if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail();
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version };
  await requireCurrent(adapter, expected, sentinel.review_stage);
  requireAdapter(adapter);
  const status = validateFormalStocktakeReviewCommandStatus(
    await adapter.reviewCommandStatus(
      sentinel.task_id,
      sentinel.round_id,
      sentinel.review_stage,
      sentinel.actor_person_id,
      sentinel.actor_authorization_version,
      sentinel.trace_request_id,
    ),
    sentinel,
  );
  if (status.lookup_status !== "confirmed") fail("暂未查到原盘点复核的确定结果；不能据此重新提交");
  const detail = validateFormalStocktakeReviewRecoveredProjection(await adapter.detailNoReplay(sentinel.task_id), sentinel, status.command);
  await requireCurrent(adapter, expected, sentinel.review_stage);
  if (!canCommit()) fail("核验页面已变化，继续保留盘点复核恢复记录");
  lease.clearExact(sentinel);
  return Object.freeze({ command: status.command, detail });
}

export type FormalStocktakeReviewSubmissionResult = Readonly<{
  recovered: boolean;
  command: FormalStocktakeReviewHistoricalCommand;
  detail: FormalStocktakeDetail;
}>;

/** Persist coordinates before the single POST; subsequent attempts are GET-only. */
export async function submitDurableFormalStocktakeReview(options: Readonly<{
  intent: FormalStocktakeIntent;
  expectedIdentity: FormalStocktakeExpectedIdentity;
  adapter: RecoveryAdapter;
  store?: FormalStocktakeReviewRecoveryStore;
  canCommit?: () => boolean;
}>): Promise<FormalStocktakeReviewSubmissionResult> {
  const {
    intent,
    expectedIdentity,
    adapter,
    store = getFormalStocktakeReviewRecoveryStore(),
    canCommit = () => true,
  } = options;
  requireAdapter(adapter);
  const expected = identity(expectedIdentity);
  const sentinel = validateSentinelAgainstIntent(intent, expected);
  return store.withTaskLease(sentinel.task_id, async (lease) => {
    const existing = lease.read();
    if (existing.kind === "corrupt" || existing.kind === "unavailable") fail("盘点复核持久恢复记录不可用，已停止写入");
    if (existing.kind === "valid") {
      try {
        return Object.freeze({ recovered: true, ...(await recoverFormalStocktakeReview(lease, existing.value, adapter, canCommit)) });
      } catch {
        throw new FormalStocktakeReviewSubmissionPendingError(existing.value);
      }
    }
    await requireCurrent(adapter, expected, sentinel.review_stage);
    if (!canCommit()) fail("当前盘点页面已变化，未发送复核请求");
    let persisted = false;
    const executeOptions: FormalStocktakeExecuteOptions = {
      beforeWrite: async () => {
        await requireCurrent(adapter, expected, sentinel.review_stage);
        if (!canCommit()) fail("当前盘点页面已变化，未发送复核请求");
        lease.persist(sentinel);
        const stored = lease.read();
        if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
          throw new FormalStocktakeReviewSubmissionPendingError(sentinel);
        }
        persisted = true;
      },
    };
    try {
      await adapter.execute(intent, { ...executeOptions, noReplayReads: true });
    } catch (error) {
      if (!persisted) throw error;
      // Once coordinates are durable, even a definite HTTP rejection is not
      // replayed.  The only follow-up is the historical status lookup below.
    }
    try {
      const recovered = await recoverFormalStocktakeReview(lease, sentinel, adapter, canCommit);
      return Object.freeze({ recovered: true, ...recovered });
    } catch {
      throw new FormalStocktakeReviewSubmissionPendingError(sentinel);
    }
  });
}

