import { api, ApiError } from "./api";
import { createFormalStocktakeAdapter, type FormalStocktakeAccess } from "./formalStocktakeAdapter";
import { validateOpeningStocktakeTaskDetail, type OpeningStocktakeTaskDetail } from "./formalOpeningStocktake";
import { validateOpeningCountSentinel, type OpeningCountSentinel, type OpeningCountTaskLease } from "./openingCountRecoveryStore";

type Identity = Readonly<{ person_id: string; authorization_version: number }>;
type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export type OpeningCountHistoricalCommand = Readonly<{
  completion_id: string; completed_at: string; round_no: number;
  scope_completed: true; caused_round_submission: boolean;
}>;
export type OpeningCountCommandStatus = Readonly<{
  schema_version: "1.0"; task_id: string; round_id: string; scope_id: string;
  actor_person_id: string; actor_authorization_version: number; trace_request_id: string;
}> & (Readonly<{ lookup_status: "not_observed"; command: null }>
  | Readonly<{ lookup_status: "confirmed"; command: OpeningCountHistoricalCommand }>);
export type OpeningCountRecoveryAdapter = Readonly<{
  loadIdentity(): Promise<Identity>;
  loadAccess(): Promise<FormalStocktakeAccess>;
  commandStatus(sentinel: OpeningCountSentinel): Promise<unknown>;
  detail(taskId: string): Promise<unknown>;
}>;

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const NO_STORE = { method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } } as const;
function fail(message = "盘点恢复证据与原请求不一致，继续保持待核验"): never { throw new ApiError(409, message); }
function instant(value: string): bigint {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match) fail();
  const base = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(base) || new Date(base).toISOString().slice(0, 19) !== match[1]) fail();
  const hours = Number(match[5] ?? 0);
  const minutes = Number(match[6] ?? 0);
  if (hours > 23 || minutes > 59) fail();
  const offset = (hours * 60 + minutes) * 60_000 * (match[4] === "-" ? -1 : 1);
  // PostgreSQL microseconds must not disappear through Date millisecond rounding.
  return BigInt(base - offset) * 1000n + BigInt((match[2] ?? "").padEnd(6, "0"));
}
function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== fields.length || fields.some((field) => !Object.hasOwn(row, field))) fail();
  return row;
}
function checkedIdentity(value: unknown): Identity {
  const row = exact(value, ["person_id", "authorization_version"]);
  if (typeof row.person_id !== "string" || !UUID.test(row.person_id)
    || !Number.isSafeInteger(row.authorization_version) || (row.authorization_version as number) < 1) fail();
  return Object.freeze({ person_id: row.person_id, authorization_version: row.authorization_version as number });
}
function sameIdentity(value: unknown, expected: Identity): void {
  const current = checkedIdentity(value);
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) {
    fail("登录人员或权限已变化，原盘点继续保持待核验");
  }
}
function activeIdentity(value: unknown, expected: Identity): Identity {
  const row = exact(value, ["person_id", "name", "employee_no", "organization_code", "organization_name",
    "account_status", "employment_status", "access_mode", "authorization_version", "role_codes"]);
  const identity = checkedIdentity({ person_id: row.person_id, authorization_version: row.authorization_version });
  sameIdentity(identity, expected);
  if (row.account_status !== "active" || row.employment_status !== "active" || row.access_mode !== "active") fail("当前身份不能核验盘点");
  for (const [field, limit] of [["name", 160], ["employee_no", 80], ["organization_code", 120], ["organization_name", 240]] as const) {
    const text = row[field];
    if (typeof text !== "string" || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail();
  }
  const roles = row.role_codes;
  if (!Array.isArray(roles) || roles.length === 0 || new Set(roles).size !== roles.length
    || roles.some((role) => !["admin", "provincial_manager", "technician", "star_headquarters_approver"].includes(role))
    || !roles.some((role) => ["admin", "provincial_manager", "technician"].includes(role))) fail();
  return identity;
}
function requireReadAndCount(access: FormalStocktakeAccess, expected: Identity): void {
  sameIdentity({ person_id: access.person_id, authorization_version: access.authorization_version }, expected);
  if (access.schema_version !== "1.0" || access.can_read !== true || access.can_count !== true) {
    fail("当前正式权限不能核验原盘点范围，继续保留恢复记录");
  }
}

export function createOpeningCountRecoveryAdapter(expectedIdentity: Identity, requester: Requester = api): OpeningCountRecoveryAdapter {
  const expected = checkedIdentity(expectedIdentity);
  const stocktake = createFormalStocktakeAdapter(expected, requester);
  return Object.freeze({
    async loadIdentity() { return activeIdentity(await requester("/auth/me", NO_STORE), expected); },
    loadAccess: () => stocktake.loadAccess(),
    commandStatus(value: OpeningCountSentinel) {
      const sentinel = validateOpeningCountSentinel(value);
      sameIdentity({ person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version }, expected);
      const query = new URLSearchParams({ actor_person_id: sentinel.actor_person_id,
        actor_authorization_version: String(sentinel.actor_authorization_version), trace_request_id: sentinel.trace_request_id });
      return requester(`/v1/stocktakes/opening/${sentinel.task_id}/rounds/${sentinel.round_id}/scopes/${sentinel.scope_id}/count-command-status?${query}`, NO_STORE);
    },
    detail(id: string) {
      if (!UUID.test(id)) fail();
      return requester(`/v1/stocktakes/opening/${id}`, NO_STORE);
    },
  });
}

export function validateOpeningCountCommandStatus(value: unknown, original: OpeningCountSentinel): OpeningCountCommandStatus {
  const sentinel = validateOpeningCountSentinel(original);
  const row = exact(value, ["schema_version", "task_id", "round_id", "scope_id", "actor_person_id",
    "actor_authorization_version", "trace_request_id", "lookup_status", "command"]);
  if (row.schema_version !== "1.0" || row.task_id !== sentinel.task_id || row.round_id !== sentinel.round_id
    || row.scope_id !== sentinel.scope_id || row.actor_person_id !== sentinel.actor_person_id
    || row.actor_authorization_version !== sentinel.actor_authorization_version || row.trace_request_id !== sentinel.trace_request_id) fail();
  const anchors = { schema_version: "1.0" as const, task_id: sentinel.task_id, round_id: sentinel.round_id,
    scope_id: sentinel.scope_id, actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version, trace_request_id: sentinel.trace_request_id };
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) fail();
    return Object.freeze({ ...anchors, lookup_status: "not_observed", command: null });
  }
  if (row.lookup_status !== "confirmed") fail();
  const command = exact(row.command, ["completion_id", "completed_at", "round_no", "scope_completed", "caused_round_submission"]);
  if (typeof command.completion_id !== "string" || !UUID.test(command.completion_id)
    || typeof command.completed_at !== "string" || !TIMESTAMP.test(command.completed_at) || !Number.isFinite(Date.parse(command.completed_at))
    || command.round_no !== sentinel.round_no || command.scope_completed !== true || typeof command.caused_round_submission !== "boolean") fail();
  instant(command.completed_at);
  return Object.freeze({ ...anchors, lookup_status: "confirmed", command: Object.freeze({
    completion_id: command.completion_id, completed_at: command.completed_at, round_no: sentinel.round_no,
    scope_completed: true, caused_round_submission: command.caused_round_submission,
  }) });
}

/** Historical completion and current task projection are separate evidence. */
export function validateOpeningCountRecoveredProjection(
  value: unknown, sentinel: OpeningCountSentinel, command: OpeningCountHistoricalCommand,
): OpeningStocktakeTaskDetail {
  const detail = validateOpeningStocktakeTaskDetail(value);
  const round = detail.current_round;
  const scope = detail.scopes.find((row) => row.scope_id === sentinel.scope_id);
  if (detail.task_id !== sentinel.task_id || !round || !scope || round.round_no < sentinel.round_no) fail();
  if (round.round_type !== (round.round_no === 1 ? "initial" : "recount") || round.status === "superseded") fail();
  if (round.round_no === sentinel.round_no) {
    if (round.round_id !== sentinel.round_id || scope.completion_status !== "completed"
      || instant(round.started_at) > instant(command.completed_at)
      || (round.status === "submitted" && (!round.submitted_at || instant(command.completed_at) > instant(round.submitted_at)))
      || scope.completed_at !== command.completed_at || (command.caused_round_submission
        && (round.status !== "submitted" || round.submitted_at !== command.completed_at))) fail();
  } else if (round.round_id === sentinel.round_id || instant(round.started_at) < instant(command.completed_at)) fail();
  return detail;
}

/** Never POST, create new coordinates, retry by timer, or clear not_observed. */
export async function recoverOpeningCountCommand(
  lease: OpeningCountTaskLease, value: OpeningCountSentinel, adapter: OpeningCountRecoveryAdapter,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: OpeningCountHistoricalCommand; detail: OpeningStocktakeTaskDetail }>> {
  const sentinel = validateOpeningCountSentinel(value);
  const stored = lease.read();
  if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail();
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version };
  sameIdentity(await adapter.loadIdentity(), expected);
  requireReadAndCount(await adapter.loadAccess(), expected);
  const status = validateOpeningCountCommandStatus(await adapter.commandStatus(sentinel), sentinel);
  if (status.lookup_status !== "confirmed") fail("暂未查到原盘点的确定结果，继续保留恢复记录；不能据此重新提交");
  const detail = validateOpeningCountRecoveredProjection(await adapter.detail(sentinel.task_id), sentinel, status.command);
  sameIdentity(await adapter.loadIdentity(), expected);
  requireReadAndCount(await adapter.loadAccess(), expected);
  if (!canCommit()) fail("核验页面已变化，继续保留盘点恢复记录");
  lease.clearExact(sentinel);
  return Object.freeze({ command: status.command, detail });
}
