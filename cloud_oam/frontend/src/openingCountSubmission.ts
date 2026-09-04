import { api, apiNoReplay, ApiError, jsonBody, mutationHeaders } from "./api";
import {
  resolveOpeningScopeCount, validateCountResult, validateOpeningStocktakeTaskDetail,
  type OpeningCountResult, type OpeningScopeCountInput, type OpeningStocktakeTaskDetail,
} from "./formalOpeningStocktake";
import type { FormalStocktakeExpectedIdentity } from "./formalStocktakeAdapter";
import {
  createOpeningCountRecoveryAdapter, recoverOpeningCountCommand,
  type OpeningCountHistoricalCommand, type OpeningCountRecoveryAdapter,
} from "./openingCountRecovery";
import {
  getOpeningCountRecoveryStore, validateOpeningCountSentinel,
  type OpeningCountRecoveryStore, type OpeningCountSentinel, type OpeningCountTaskLease,
} from "./openingCountRecoveryStore";

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
const defaultRequester: Requester = (path, init) => init?.method?.toUpperCase() === "POST"
  ? apiNoReplay(path, init) : api(path, init);
export type OpeningCountSubmissionResult = Readonly<{
  recovered: boolean;
  command: OpeningCountHistoricalCommand;
  detail: OpeningStocktakeTaskDetail;
}>;

/** Only public recovery coordinates; never retain an original error or payload. */
export class OpeningCountSubmissionPendingError extends ApiError {
  readonly task_id: string;
  readonly round_id: string;
  readonly scope_id: string;
  readonly trace_request_id: string;

  constructor(sentinel: OpeningCountSentinel) {
    super(409, "原盘点仍待核验，请保留恢复记录；不能据此重新提交");
    this.name = "OpeningCountSubmissionPendingError";
    this.task_id = sentinel.task_id;
    this.round_id = sentinel.round_id;
    this.scope_id = sentinel.scope_id;
    this.trace_request_id = sentinel.trace_request_id;
  }
}

const RESULT_FIELDS = ["schema_version", "task_id", "round_id", "scope_id", "task_status",
  "round_status", "scope_completed", "round_sealed", "has_pending_verification", "replayed"];

function stop(message: string): never { throw new ApiError(409, message); }

function freezeJson<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    Object.values(value).forEach(freezeJson);
    Object.freeze(value);
  }
  return value;
}

async function requireCurrentAccess(adapter: OpeningCountRecoveryAdapter): Promise<void> {
  // Both adapter methods bind their fresh responses to the captured identity.
  await adapter.loadIdentity();
  const access = await adapter.loadAccess();
  if (access.can_read !== true || access.can_count !== true) stop("当前正式权限不允许提交盘点");
}

function acceptedResult(value: unknown, sentinel: OpeningCountSentinel): OpeningCountResult {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).length !== RESULT_FIELDS.length
    || RESULT_FIELDS.some((field) => !Object.hasOwn(value, field))) stop("盘点提交结果契约无效");
  const result = validateCountResult(value);
  if (result.task_id !== sentinel.task_id || result.round_id !== sentinel.round_id
    || result.scope_id !== sentinel.scope_id) stop("盘点提交结果与原请求不一致");
  return Object.freeze({ ...result });
}

function definitiveFirstPostRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && error.responseReceived
    && ((error.status === 400 && error.category === "invalid_request"
      && ["idempotency_key_invalid", "x_request_id_invalid"].includes(error.code ?? ""))
      || (error.status === 412 && error.category === "precondition_failed"
        && error.code === "opening_count_state_invalid"));
}

async function recover(
  lease: OpeningCountTaskLease, sentinel: OpeningCountSentinel,
  adapter: OpeningCountRecoveryAdapter, canCommit: () => boolean,
  directResult: OpeningCountResult | null = null,
): Promise<OpeningCountSubmissionResult> {
  try {
    const confirmed = await recoverOpeningCountCommand(lease, sentinel, adapter, canCommit);
    return Object.freeze({
      // A historical completion is not a claim that a new form was submitted.
      // Even a well-formed POST response is only a signal until independently proved.
      recovered: directResult === null || directResult.replayed
        || directResult.round_sealed !== confirmed.command.caused_round_submission,
      command: confirmed.command, detail: confirmed.detail,
    });
  } catch {
    throw new OpeningCountSubmissionPendingError(sentinel);
  }
}

/**
 * Exactly one newly allocated POST, or only read-only recovery of an existing
 * sentinel. The native task lease covers every preflight, persistence and readback.
 * There is no POST retry and no in-memory legacy count intent in this path.
 */
export async function submitDurableOpeningScopeCount(options: Readonly<{
  taskId: string;
  scopeId: string;
  input: OpeningScopeCountInput;
  expectedIdentity: FormalStocktakeExpectedIdentity;
  store?: OpeningCountRecoveryStore;
  requester?: Requester;
  canCommit?: () => boolean;
}>): Promise<OpeningCountSubmissionResult> {
  const { taskId, scopeId, input, expectedIdentity, store = getOpeningCountRecoveryStore(),
    requester = defaultRequester, canCommit = () => true } = options;
  const expected = Object.freeze({ ...expectedIdentity });
  return store.withTaskLease(taskId, async (lease) => {
    const existing = lease.read();
    if (existing.kind === "corrupt" || existing.kind === "unavailable") {
      stop("盘点持久恢复记录不可用，已停止写入");
    }
    let adapter: OpeningCountRecoveryAdapter;
    try { adapter = createOpeningCountRecoveryAdapter(expected, requester); } catch {
      if (existing.kind === "valid") throw new OpeningCountSubmissionPendingError(existing.value);
      stop("当前盘点身份无效，已停止写入");
    }
    if (existing.kind === "valid") return recover(lease, existing.value, adapter, canCommit);

    await requireCurrentAccess(adapter);
    const before = validateOpeningStocktakeTaskDetail(await adapter.detail(taskId));
    const round = before.current_round;
    if (before.task_id !== taskId || !Number.isSafeInteger(before.task_version) || before.task_version < 1
      || before.status !== "counting" || !before.allowed_actions.includes("count") || !round
      || round.round_type !== (round.round_no === 1 ? "initial" : "recount")) {
      stop("当前详情未授权精确盘点任务或轮次，已停止写入");
    }
    const roundNo = round.round_no;

    // Serialize once before persisting any recovery marker. Re-validate the actual
    // serialized body, so later form mutation (or toJSON) cannot change the command.
    let body: Pick<RequestInit, "body">;
    let prepared: ReturnType<typeof resolveOpeningScopeCount>;
    try {
      body = Object.freeze(jsonBody(input));
      if (typeof body.body !== "string") stop("盘点正文无效");
      const frozen = freezeJson(JSON.parse(body.body)) as OpeningScopeCountInput;
      if (!frozen || typeof frozen !== "object" || Object.keys(frozen).length !== 2
        || !Object.hasOwn(frozen, "zero_confirmed") || !Array.isArray(frozen.physical_observations)) {
        stop("盘点正文无效");
      }
      prepared = resolveOpeningScopeCount(taskId, scopeId, frozen, before);
    } catch { stop("盘点正文或目标范围无效，未发送请求"); }

    await requireCurrentAccess(adapter);
    if (!canCommit()) stop("当前盘点页面已变化，未发送请求");
    const coordinates = mutationHeaders("opening-count");
    const headers = new Headers(coordinates.headers);
    const sentinel = validateOpeningCountSentinel({
      v: 1, kind: "opening_scope_count", task_id: taskId, round_id: prepared.roundId,
      round_no: roundNo, scope_id: scopeId, actor_person_id: expected.person_id,
      actor_authorization_version: expected.authorization_version,
      trace_request_id: headers.get("X-Request-ID"),
    });
    // Persist is write-then-reread; retain an explicit exact read at the transport boundary.
    lease.persist(sentinel);
    try {
      const stored = lease.read();
      if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
        throw new OpeningCountSubmissionPendingError(sentinel);
      }
    } catch { throw new OpeningCountSubmissionPendingError(sentinel); }

    let raw: unknown;
    try {
      raw = await requester(prepared.path, { method: "POST", ...coordinates, ...body });
    } catch (error) {
      // Deliberately catches only the first direct POST, never a later GET.
      if (definitiveFirstPostRejection(error)) {
        try {
          // A cross-tab account switch must not release another identity's marker.
          // Failures here are GET failures, not fresh evidence of POST rejection.
          await requireCurrentAccess(adapter);
          if (!canCommit()) throw new OpeningCountSubmissionPendingError(sentinel);
          lease.clearExact(sentinel);
        } catch { throw new OpeningCountSubmissionPendingError(sentinel); }
        throw new ApiError(error.status, "本次盘点请求已明确拒绝，未完成提交；请刷新后检查", {
          code: error.code, category: error.category,
        });
      }
      return recover(lease, sentinel, adapter, canCommit);
    }
    let result: OpeningCountResult | null = null;
    try { result = acceptedResult(raw, sentinel); } catch { /* Unknown contract: only independent recovery. */ }
    return recover(lease, sentinel, adapter, canCommit, result);
  });
}
