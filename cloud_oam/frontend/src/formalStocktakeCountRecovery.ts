import { apiNoReplay, ApiError } from "./api";
import {
  validateFormalStocktakeDetail,
  type FormalStocktakeDetail,
} from "./formalStocktakes";
import type {
  FormalStocktakeAccess,
  FormalStocktakeAdapter,
  FormalStocktakeExpectedIdentity,
} from "./formalStocktakeAdapter";
import { createFormalStocktakeAdapter } from "./formalStocktakeAdapter";
import {
  validateFormalStocktakeCountSentinel,
  type FormalStocktakeCountOperation,
  type FormalStocktakeCountSentinel,
  type FormalStocktakeCountTaskLease,
} from "./formalStocktakeCountRecoveryStore";

type Identity = Readonly<{ person_id: string; authorization_version: number }>;

export type FormalStocktakeCountHistoricalCommand = Readonly<{
  completion_id: string;
  completed_at: string;
  round_no: number;
  scope_completed: true;
  caused_round_submission: boolean;
}>;

export type FormalStocktakeCountCommandStatus = Readonly<{
  schema_version: "1.0";
  task_id: string;
  round_id: string;
  scope_id: string;
  actor_person_id: string;
  actor_authorization_version: number;
  trace_request_id: string;
  operation: FormalStocktakeCountOperation;
}> & (
  | Readonly<{ lookup_status: "not_observed"; command: null }>
  | Readonly<{ lookup_status: "confirmed"; command: FormalStocktakeCountHistoricalCommand }>
);

export type FormalStocktakeCountRecoveryAdapter = Readonly<{
  loadIdentity(): Promise<unknown>;
  loadAccess(): Promise<FormalStocktakeAccess>;
  commandStatus(value: FormalStocktakeCountSentinel): Promise<unknown>;
  detail(taskId: string): Promise<unknown>;
}>;

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const OPERATIONAL_ROLES = new Set(["admin", "provincial_manager", "technician"]);

function fail(message = "日常盘点范围计数恢复证据与原请求不一致，继续保持待核验"): never {
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

function positive(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) fail(`${name}无效`);
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
  return Object.freeze({ person_id: uuid(row.person_id, "person_id"), authorization_version: positive(row.authorization_version, "authorization_version") });
}

function sameIdentity(value: unknown, expected: Identity): void {
  const current = identity(value);
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) {
    fail("登录人员或授权版本已变化，原范围计数继续保持待核验");
  }
}

function activeIdentity(value: unknown, expected: Identity): Identity {
  const row = exact(value, [
    "person_id", "name", "employee_no", "organization_code", "organization_name",
    "account_status", "employment_status", "access_mode", "authorization_version", "role_codes",
  ]);
  const current = identity({ person_id: row.person_id, authorization_version: row.authorization_version });
  sameIdentity(current, expected);
  if (row.account_status !== "active" || row.employment_status !== "active" || row.access_mode !== "active") fail("当前身份不能核验日常盘点范围计数");
  for (const [field, limit] of [["name", 160], ["employee_no", 80], ["organization_code", 120], ["organization_name", 240]] as const) {
    const text = row[field];
    if (typeof text !== "string" || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail();
  }
  if (!Array.isArray(row.role_codes) || !row.role_codes.length || new Set(row.role_codes).size !== row.role_codes.length
    || row.role_codes.some((role) => typeof role !== "string" || ![...OPERATIONAL_ROLES, "star_headquarters_approver"].includes(role))
    || !row.role_codes.some((role) => typeof role === "string" && OPERATIONAL_ROLES.has(role))) fail("当前身份角色不能核验日常盘点范围计数");
  return current;
}

function requireReadAndCount(value: FormalStocktakeAccess, expected: Identity): void {
  sameIdentity({ person_id: value.person_id, authorization_version: value.authorization_version }, expected);
  if (value.schema_version !== "1.0" || value.can_read !== true || value.can_count !== true) fail("当前正式权限不能核验原范围计数");
}

export function createFormalStocktakeCountRecoveryAdapter(
  expectedIdentity: FormalStocktakeExpectedIdentity,
  requester: (path: string, init?: RequestInit) => Promise<unknown> = apiNoReplay,
): FormalStocktakeCountRecoveryAdapter {
  const expected = identity(expectedIdentity);
  const formal = createFormalStocktakeAdapter(expected, requester, requester, requester);
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await formal.loadIdentityNoReplay!(), expected); },
    async loadAccess() { return formal.loadAccessNoReplay!(); },
    commandStatus(value: FormalStocktakeCountSentinel) {
      const sentinel = validateFormalStocktakeCountSentinel(value);
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected);
      return formal.countCommandStatus!(sentinel.task_id, sentinel.round_id, sentinel.scope_id, sentinel.operation, sentinel.actor_person_id, sentinel.actor_authorization_version, sentinel.trace_request_id);
    },
    detail(taskId: string) {
      return formal.detailNoReplay!(uuid(taskId, "task_id"));
    },
  });
}

export function createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(
  expectedIdentity: FormalStocktakeExpectedIdentity,
  adapter: FormalStocktakeAdapter,
): FormalStocktakeCountRecoveryAdapter {
  const expected = identity(expectedIdentity);
  if (typeof adapter.loadIdentityNoReplay !== "function"
    || typeof adapter.loadAccessNoReplay !== "function"
    || typeof adapter.detailNoReplay !== "function"
    || typeof adapter.countCommandStatus !== "function") {
    fail("当前盘点客户端未提供只读日常计数恢复能力，已停止写入");
  }
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await adapter.loadIdentityNoReplay!(), expected); },
    async loadAccess() {
      const access = await adapter.loadAccessNoReplay!();
      requireReadAndCount(access, expected);
      return access;
    },
    commandStatus(value: FormalStocktakeCountSentinel) {
      const sentinel = validateFormalStocktakeCountSentinel(value);
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected);
      return adapter.countCommandStatus!(sentinel.task_id, sentinel.round_id, sentinel.scope_id, sentinel.operation, sentinel.actor_person_id, sentinel.actor_authorization_version, sentinel.trace_request_id);
    },
    detail(taskId: string) { return adapter.detailNoReplay!(uuid(taskId, "task_id")); },
  });
}

export function validateFormalStocktakeCountCommandStatus(
  value: unknown,
  original: FormalStocktakeCountSentinel,
): FormalStocktakeCountCommandStatus {
  const sentinel = validateFormalStocktakeCountSentinel(original);
  const row = exact(value, ["schema_version", "task_id", "round_id", "scope_id", "actor_person_id", "actor_authorization_version", "trace_request_id", "operation", "lookup_status", "command"]);
  const anchors = {
    schema_version: "1.0" as const,
    task_id: sentinel.task_id,
    round_id: sentinel.round_id,
    scope_id: sentinel.scope_id,
    actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version,
    trace_request_id: sentinel.trace_request_id,
    operation: sentinel.operation,
  };
  if (row.schema_version !== "1.0" || uuid(row.task_id, "task_id") !== sentinel.task_id || uuid(row.round_id, "round_id") !== sentinel.round_id || uuid(row.scope_id, "scope_id") !== sentinel.scope_id || uuid(row.actor_person_id, "actor_person_id") !== sentinel.actor_person_id || positive(row.actor_authorization_version, "actor_authorization_version") !== sentinel.actor_authorization_version || row.trace_request_id !== sentinel.trace_request_id || row.operation !== sentinel.operation) fail();
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) fail();
    return Object.freeze({ ...anchors, lookup_status: "not_observed", command: null });
  }
  if (row.lookup_status !== "confirmed") fail();
  const command = exact(row.command, ["completion_id", "round_no", "completed_at", "scope_completed", "caused_round_submission"]);
  if (uuid(command.completion_id, "completion_id") === "") fail();
  const roundNo = positive(command.round_no, "round_no");
  if (roundNo !== sentinel.round_no || command.scope_completed !== true || typeof command.caused_round_submission !== "boolean") fail();
  const completedAt = timestamp(command.completed_at, "completed_at");
  instant(completedAt);
  return Object.freeze({ ...anchors, lookup_status: "confirmed", command: Object.freeze({ completion_id: uuid(command.completion_id, "completion_id"), completed_at: completedAt, round_no: roundNo, scope_completed: true, caused_round_submission: command.caused_round_submission }) });
}

function completionFor(detail: FormalStocktakeDetail, sentinel: FormalStocktakeCountSentinel, command: FormalStocktakeCountHistoricalCommand): void {
  const round = detail.rounds.find((item) => item.round_id === sentinel.round_id);
  const scope = detail.scopes.find((item) => item.scope_id === sentinel.scope_id);
  if (!round || !scope || round.round_no !== sentinel.round_no || round.round_type !== (sentinel.operation === "initial_count" ? "initial" : "recount") || scope.assigned_to_me !== true) fail("当前盘点详情未确认原范围授权");
  if (round.status !== "counting" && round.status !== "submitted" && round.status !== "superseded") fail("当前盘点轮次不能承接原范围完成事实");
  const completion = round.visible_scope_completions.find((row) => row && typeof row === "object" && !Array.isArray(row) && (row as Record<string, unknown>).scope_id === sentinel.scope_id);
  if (!completion || typeof completion !== "object" || Array.isArray(completion)) fail("当前盘点详情未确认原范围完成事实");
  const row = completion as Record<string, unknown>;
  if (uuid(row.completion_id, "completion_id") !== command.completion_id || uuid(row.completed_by_person_id, "completed_by_person_id") !== sentinel.actor_person_id || timestamp(row.completed_at, "completed_at") !== command.completed_at) fail("当前盘点详情未确认原范围完成事实");
  if (instant(command.completed_at) < instant(round.started_at)) fail("原范围完成时间早于轮次开始");
  if (round.submitted_at !== null && instant(command.completed_at) > instant(round.submitted_at)) fail("原范围完成时间晚于轮次封轮");
  if (command.caused_round_submission && (round.status !== "submitted" || round.submitted_at === null || round.submitted_at !== command.completed_at)) fail("轮次封存事实与历史范围完成不一致");
}

export function validateFormalStocktakeCountRecoveredProjection(
  value: unknown,
  sentinel: FormalStocktakeCountSentinel,
  command: FormalStocktakeCountHistoricalCommand,
): FormalStocktakeDetail {
  const checked = validateFormalStocktakeCountSentinel(sentinel);
  if (command.round_no !== checked.round_no || command.scope_completed !== true) fail();
  const detail = validateFormalStocktakeDetail(value);
  if (detail.task_id !== checked.task_id) fail();
  completionFor(detail, checked, command);
  return detail;
}

function requireAdapter(adapter: FormalStocktakeCountRecoveryAdapter): void {
  if (!adapter || typeof adapter.loadIdentity !== "function" || typeof adapter.loadAccess !== "function" || typeof adapter.commandStatus !== "function" || typeof adapter.detail !== "function") fail("当前盘点客户端未提供只读日常计数恢复能力，已停止写入");
}

export async function recoverFormalStocktakeCount(
  lease: FormalStocktakeCountTaskLease,
  value: FormalStocktakeCountSentinel,
  adapter: FormalStocktakeCountRecoveryAdapter,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: FormalStocktakeCountHistoricalCommand; detail: FormalStocktakeDetail }>> {
  requireAdapter(adapter);
  const sentinel = validateFormalStocktakeCountSentinel(value);
  const stored = lease.read();
  if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail();
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version };
  await adapter.loadIdentity().then((identityValue) => sameIdentity(identityValue, expected));
  requireReadAndCount(await adapter.loadAccess(), expected);
  const status = validateFormalStocktakeCountCommandStatus(await adapter.commandStatus(sentinel), sentinel);
  // `not_observed` is unknown, not a negative acknowledgement.  Keep the
  // marker and deliberately avoid detail reads or any POST/replay path.
  if (status.lookup_status !== "confirmed") fail("暂未查到原范围计数的确定结果，继续保留恢复记录；不能据此重新提交");
  const detail = validateFormalStocktakeCountRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command);
  await adapter.loadIdentity().then((identityValue) => sameIdentity(identityValue, expected));
  requireReadAndCount(await adapter.loadAccess(), expected);
  if (!canCommit()) fail("核验页面已变化，继续保留日常盘点恢复记录");
  lease.clearExact(sentinel);
  return Object.freeze({ command: status.command, detail });
}

export class FormalStocktakeCountSubmissionPendingError extends ApiError {
  readonly task_id: string;
  readonly round_id: string;
  readonly scope_id: string;
  readonly operation: FormalStocktakeCountOperation;
  readonly actor_person_id: string;
  readonly actor_authorization_version: number;
  readonly trace_request_id: string;

  constructor(sentinel: FormalStocktakeCountSentinel) {
    super(409, "原日常盘点范围计数仍待只读核验；不能重新提交");
    this.name = "FormalStocktakeCountSubmissionPendingError";
    this.task_id = sentinel.task_id;
    this.round_id = sentinel.round_id;
    this.scope_id = sentinel.scope_id;
    this.operation = sentinel.operation;
    this.actor_person_id = sentinel.actor_person_id;
    this.actor_authorization_version = sentinel.actor_authorization_version;
    this.trace_request_id = sentinel.trace_request_id;
  }
}
