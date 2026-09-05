import { ApiError } from "./api";
import {
  confirmFormalStocktakePostProjection,
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
  getFormalStocktakePostRecoveryStore,
  validateFormalStocktakePostSentinel,
  type FormalStocktakePostRecoveryStore,
  type FormalStocktakePostSentinel,
  type FormalStocktakePostTaskLease,
} from "./formalStocktakePostRecoveryStore";

type Identity = Readonly<{ person_id: string; authorization_version: number }>;

export type FormalStocktakePostingHistoricalCommand = Readonly<{
  completion_id: string;
  task_id: string;
  terminal_round_id: string;
  resulting_task_status: "posted";
  task_version: number;
  scope_count: number;
  difference_count: number;
  accepted_difference_count: number;
  no_adjustment_count: number;
  transaction_count: number;
  movement_count: number;
  total_quantity: string;
  first_ledger_cursor: number | null;
  last_ledger_cursor: number | null;
  posted_at: string;
}>;

export type FormalStocktakePostingCommandStatus = Readonly<{
  schema_version: "1.0";
  task_id: string;
  actor_person_id: string;
  actor_authorization_version: number;
  trace_request_id: string;
  operation: "post_differences";
}> & (
  | Readonly<{ lookup_status: "not_observed"; command: null }>
  | Readonly<{ lookup_status: "confirmed"; command: FormalStocktakePostingHistoricalCommand }>
);

type RecoveryAdapter = Pick<FormalStocktakeAdapter, "loadAccess" | "detail" | "execute"> & {
  loadIdentityNoReplay?: () => Promise<unknown>;
  loadAccessNoReplay?: () => Promise<FormalStocktakeAccess>;
  detailNoReplay?: (taskId: string) => Promise<FormalStocktakeDetail>;
  postingCommandStatus?: (
    taskId: string,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ) => Promise<unknown>;
};

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;

function fail(message = "盘点过账恢复证据与原请求不一致，继续保持待核验"): never {
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

function nonNegative(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) fail(`${name}无效`);
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

function activeIdentity(value: unknown, expected: Identity): Identity {
  const row = exact(value, [
    "person_id", "name", "employee_no", "organization_code", "organization_name",
    "account_status", "employment_status", "access_mode", "authorization_version", "role_codes",
  ]);
  const current = identity({ person_id: row.person_id, authorization_version: row.authorization_version });
  if (current.person_id !== expected.person_id || current.authorization_version !== expected.authorization_version) {
    fail("登录人员或权限已变化，原过账继续保持待核验");
  }
  if (row.account_status !== "active" || row.employment_status !== "active" || row.access_mode !== "active") {
    fail("当前身份不能核验盘点过账");
  }
  for (const [field, limit] of [["name", 160], ["employee_no", 80], ["organization_code", 120], ["organization_name", 240]] as const) {
    const text = row[field];
    if (typeof text !== "string" || !text || text.trim() !== text || text.length > limit || /[\u0000-\u001f\u007f]/.test(text)) fail();
  }
  if (!Array.isArray(row.role_codes) || !row.role_codes.length || new Set(row.role_codes).size !== row.role_codes.length || row.role_codes.some((role) => !["admin", "provincial_manager", "technician", "star_headquarters_approver"].includes(String(role))) || !row.role_codes.some((role) => ["admin", "provincial_manager", "technician"].includes(String(role)))) {
    fail("当前身份角色不能核验盘点过账");
  }
  return current;
}

function sameIdentity(value: Identity, expected: Identity): void {
  if (value.person_id !== expected.person_id || value.authorization_version !== expected.authorization_version) {
    fail("登录人员或权限已变化，原过账继续保持待核验");
  }
}

function requireAdapter(adapter: RecoveryAdapter): asserts adapter is RecoveryAdapter & {
  loadIdentityNoReplay: () => Promise<unknown>;
  loadAccessNoReplay: () => Promise<FormalStocktakeAccess>;
  detailNoReplay: (taskId: string) => Promise<FormalStocktakeDetail>;
  postingCommandStatus: (
    taskId: string,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ) => Promise<unknown>;
} {
  if (
    typeof adapter.loadIdentityNoReplay !== "function"
    || typeof adapter.loadAccessNoReplay !== "function"
    || typeof adapter.detailNoReplay !== "function"
    || typeof adapter.postingCommandStatus !== "function"
  ) {
    fail("当前盘点客户端未提供只读过账恢复能力，已停止写入");
  }
}

function requirePostAccess(access: FormalStocktakeAccess, expected: Identity): void {
  if (access.schema_version !== "1.0" || access.person_id !== expected.person_id || access.authorization_version !== expected.authorization_version) {
    fail("正式权限版本与原过账不一致");
  }
  if (!access.can_read || !access.can_post) fail("当前正式权限不能核验盘点过账");
}

function validateSentinelAgainstIntent(intent: FormalStocktakeIntent, expected: Identity): FormalStocktakePostSentinel {
  if (intent.action !== "post" || !intent.taskId || intent.expectedTaskVersion === null) fail("只允许恢复正式日常盘点过账");
  const body = exact(intent.body, ["expected_task_version"]);
  if (positive(body.expected_task_version, "expected_task_version") !== intent.expectedTaskVersion) fail("过账版本坐标不一致");
  const trace = intent.headers["X-Request-ID"];
  if (typeof trace !== "string" || !TRACE.test(trace)) fail("过账追踪坐标无效");
  return validateFormalStocktakePostSentinel({
    v: 1,
    kind: "formal_stocktake_post",
    task_id: uuid(intent.taskId, "task_id"),
    expected_task_version: intent.expectedTaskVersion,
    actor_person_id: expected.person_id,
    actor_authorization_version: expected.authorization_version,
    trace_request_id: trace,
  });
}

export function validateFormalStocktakePostCommandStatus(
  value: unknown,
  original: FormalStocktakePostSentinel,
): FormalStocktakePostingCommandStatus {
  const sentinel = validateFormalStocktakePostSentinel(original);
  const row = exact(value, [
    "schema_version", "task_id", "actor_person_id", "actor_authorization_version",
    "trace_request_id", "operation", "lookup_status", "command",
  ]);
  const anchors = {
    schema_version: "1.0" as const,
    task_id: sentinel.task_id,
    actor_person_id: sentinel.actor_person_id,
    actor_authorization_version: sentinel.actor_authorization_version,
    trace_request_id: sentinel.trace_request_id,
    operation: "post_differences" as const,
  };
  if (
    row.schema_version !== "1.0"
    || uuid(row.task_id, "task_id") !== sentinel.task_id
    || uuid(row.actor_person_id, "actor_person_id") !== sentinel.actor_person_id
    || positive(row.actor_authorization_version, "actor_authorization_version") !== sentinel.actor_authorization_version
    || typeof row.trace_request_id !== "string"
    || row.trace_request_id !== sentinel.trace_request_id
    || row.operation !== "post_differences"
  ) fail();
  if (row.lookup_status === "not_observed") {
    if (row.command !== null) fail();
    return Object.freeze({ ...anchors, lookup_status: "not_observed", command: null });
  }
  if (row.lookup_status !== "confirmed") fail();
  const command = exact(row.command, [
    "completion_id", "task_id", "terminal_round_id", "resulting_task_status", "task_version",
    "scope_count", "difference_count", "accepted_difference_count", "no_adjustment_count",
    "transaction_count", "movement_count", "total_quantity", "first_ledger_cursor",
    "last_ledger_cursor", "posted_at",
  ]);
  const checked = {
    completion_id: uuid(command.completion_id, "completion_id"),
    task_id: uuid(command.task_id, "command.task_id"),
    terminal_round_id: uuid(command.terminal_round_id, "terminal_round_id"),
    resulting_task_status: command.resulting_task_status,
    task_version: positive(command.task_version, "task_version"),
    scope_count: positive(command.scope_count, "scope_count"),
    difference_count: nonNegative(command.difference_count, "difference_count"),
    accepted_difference_count: nonNegative(command.accepted_difference_count, "accepted_difference_count"),
    no_adjustment_count: nonNegative(command.no_adjustment_count, "no_adjustment_count"),
    transaction_count: nonNegative(command.transaction_count, "transaction_count"),
    movement_count: nonNegative(command.movement_count, "movement_count"),
    total_quantity: command.total_quantity,
    first_ledger_cursor: command.first_ledger_cursor === null ? null : positive(command.first_ledger_cursor, "first_ledger_cursor"),
    last_ledger_cursor: command.last_ledger_cursor === null ? null : positive(command.last_ledger_cursor, "last_ledger_cursor"),
    posted_at: timestamp(command.posted_at, "posted_at"),
  } as FormalStocktakePostingHistoricalCommand;
  if (
    checked.task_id !== sentinel.task_id
    || checked.resulting_task_status !== "posted"
    || typeof checked.total_quantity !== "string"
    || !QUANTITY.test(checked.total_quantity)
    || checked.accepted_difference_count + checked.no_adjustment_count !== checked.difference_count
    || checked.movement_count !== checked.accepted_difference_count
    || (checked.transaction_count === 0
      ? checked.first_ledger_cursor !== null || checked.last_ledger_cursor !== null || checked.movement_count !== 0 || checked.total_quantity !== "0.000"
      : checked.first_ledger_cursor === null || checked.last_ledger_cursor === null
        || checked.last_ledger_cursor - checked.first_ledger_cursor + 1 !== checked.transaction_count
        || checked.movement_count <= 0
        || checked.total_quantity === "0.000")
  ) fail("盘点过账历史命令数量或流水摘要无效");
  if (checked.task_version !== sentinel.expected_task_version + 1) fail("盘点过账历史命令版本无效");
  return Object.freeze({ ...anchors, lookup_status: "confirmed", command: Object.freeze(checked) });
}

export function validateFormalStocktakePostRecoveredProjection(
  value: unknown,
  sentinel: FormalStocktakePostSentinel,
  command: FormalStocktakePostingHistoricalCommand,
): FormalStocktakeDetail {
  const checkedSentinel = validateFormalStocktakePostSentinel(sentinel);
  if (command.task_id !== checkedSentinel.task_id) fail();
  const detail = validateFormalStocktakeDetail(value);
  // This helper retains the exact posting graph checks used by the direct
  // adapter, while allowing later reconciliation/close versions.
  confirmFormalStocktakePostProjection(
    {
      task_id: command.task_id,
      terminal_round_id: command.terminal_round_id,
      task_version: command.task_version,
      scope_count: command.scope_count,
      difference_count: command.difference_count,
      accepted_difference_count: command.accepted_difference_count,
      no_adjustment_count: command.no_adjustment_count,
      movement_count: command.movement_count,
      transaction_count: command.transaction_count,
      total_quantity: command.total_quantity,
    },
    detail,
    checkedSentinel.expected_task_version,
    true,
  );
  if (detail.posted_at === null || instant(detail.posted_at) !== instant(command.posted_at)) {
    fail("盘点过账时间与历史完成事实不一致");
  }
  return detail;
}

export class FormalStocktakePostSubmissionPendingError extends ApiError {
  readonly task_id: string;
  readonly expected_task_version: number;
  readonly actor_person_id: string;
  readonly actor_authorization_version: number;
  readonly trace_request_id: string;

  constructor(sentinel: FormalStocktakePostSentinel) {
    super(409, "原盘点过账仍待只读核验；不能重新提交");
    this.name = "FormalStocktakePostSubmissionPendingError";
    this.task_id = sentinel.task_id;
    this.expected_task_version = sentinel.expected_task_version;
    this.actor_person_id = sentinel.actor_person_id;
    this.actor_authorization_version = sentinel.actor_authorization_version;
    this.trace_request_id = sentinel.trace_request_id;
  }
}

async function requireCurrent(adapter: RecoveryAdapter, expected: Identity): Promise<void> {
  requireAdapter(adapter);
  sameIdentity(activeIdentity(await adapter.loadIdentityNoReplay(), expected), expected);
  requirePostAccess(await adapter.loadAccessNoReplay(), expected);
}

/**
 * Read-only recovery.  This function has no POST path and never constructs a
 * new idempotency key.  `not_observed` deliberately remains a blocking state.
 */
export async function recoverFormalStocktakePost(
  lease: FormalStocktakePostTaskLease,
  value: FormalStocktakePostSentinel,
  adapter: RecoveryAdapter,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: FormalStocktakePostingHistoricalCommand; detail: FormalStocktakeDetail }>> {
  const sentinel = validateFormalStocktakePostSentinel(value);
  const stored = lease.read();
  if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel)) fail();
  const expected = { person_id: sentinel.actor_person_id, authorization_version: sentinel.actor_authorization_version };
  await requireCurrent(adapter, expected);
  requireAdapter(adapter);
  const status = validateFormalStocktakePostCommandStatus(
    await adapter.postingCommandStatus(sentinel.task_id, sentinel.actor_person_id, sentinel.actor_authorization_version, sentinel.trace_request_id),
    sentinel,
  );
  if (status.lookup_status !== "confirmed") fail("暂未查到原盘点过账的确定结果；不能据此重新提交");
  const detail = validateFormalStocktakePostRecoveredProjection(await adapter.detailNoReplay(sentinel.task_id), sentinel, status.command);
  await requireCurrent(adapter, expected);
  if (!canCommit()) fail("核验页面已变化，继续保留盘点过账恢复记录");
  lease.clearExact(sentinel);
  return Object.freeze({ command: status.command, detail });
}

export type FormalStocktakePostSubmissionResult = Readonly<{
  recovered: boolean;
  command: FormalStocktakePostingHistoricalCommand;
  detail: FormalStocktakeDetail;
}>;

/** One persisted coordinate, one POST at most, then GET-only proof. */
export async function submitDurableFormalStocktakePost(options: Readonly<{
  intent: FormalStocktakeIntent;
  expectedIdentity: FormalStocktakeExpectedIdentity;
  adapter: RecoveryAdapter;
  store?: FormalStocktakePostRecoveryStore;
  canCommit?: () => boolean;
}>): Promise<FormalStocktakePostSubmissionResult> {
  const {
    intent,
    expectedIdentity,
    adapter,
    store = getFormalStocktakePostRecoveryStore(),
    canCommit = () => true,
  } = options;
  requireAdapter(adapter);
  const expected = identity(expectedIdentity);
  const sentinel = validateSentinelAgainstIntent(intent, expected);
  return store.withTaskLease(sentinel.task_id, async (lease) => {
    const existing = lease.read();
    if (existing.kind === "corrupt" || existing.kind === "unavailable") fail("盘点过账持久恢复记录不可用，已停止写入");
    if (existing.kind === "valid") {
      try {
        return Object.freeze({ recovered: true, ...(await recoverFormalStocktakePost(lease, existing.value, adapter, canCommit)) });
      } catch {
        throw new FormalStocktakePostSubmissionPendingError(existing.value);
      }
    }
    await requireCurrent(adapter, expected);
    if (!canCommit()) fail("当前盘点页面已变化，未发送过账请求");
    let persisted = false;
    const executeOptions: FormalStocktakeExecuteOptions = {
      beforeWrite: async () => {
        await requireCurrent(adapter, expected);
        if (!canCommit()) fail("当前盘点页面已变化，未发送过账请求");
        lease.persist(sentinel);
        const stored = lease.read();
        if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
          throw new FormalStocktakePostSubmissionPendingError(sentinel);
        }
        persisted = true;
      },
    };
    try {
      await adapter.execute(intent, { ...executeOptions, noReplayReads: true });
    } catch (error) {
      if (!persisted) throw error;
      // Never replay the write.  Even a definite HTTP rejection is followed
      // only by the historical, no-body status lookup.
    }
    try {
      const recovered = await recoverFormalStocktakePost(lease, sentinel, adapter, canCommit);
      return Object.freeze({ recovered: true, ...recovered });
    } catch {
      throw new FormalStocktakePostSubmissionPendingError(sentinel);
    }
  });
}
