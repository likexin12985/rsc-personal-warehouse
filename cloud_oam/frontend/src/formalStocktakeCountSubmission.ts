import { ApiError } from "./api";
import type {
  FormalStocktakeAdapter,
  FormalStocktakeExpectedIdentity,
} from "./formalStocktakeAdapter";
import {
  createFormalStocktakeCountRecoveryAdapterFromFormalAdapter,
  recoverFormalStocktakeCount,
  FormalStocktakeCountSubmissionPendingError,
  type FormalStocktakeCountHistoricalCommand,
} from "./formalStocktakeCountRecovery";
import {
  getFormalStocktakeCountRecoveryStore,
  validateFormalStocktakeCountSentinel,
  type FormalStocktakeCountRecoveryStore,
  type FormalStocktakeCountSentinel,
} from "./formalStocktakeCountRecoveryStore";
import type { FormalStocktakeIntent } from "./formalStocktakes";

export type FormalStocktakeCountSubmissionResult = Readonly<{
  recovered: boolean;
  command: FormalStocktakeCountHistoricalCommand;
  detail: import("./formalStocktakes").FormalStocktakeDetail;
}>;

function fail(message: string): never { throw new ApiError(409, message); }

function sentinelFromIntent(
  intent: FormalStocktakeIntent,
  expected: FormalStocktakeExpectedIdentity,
  roundNo: number,
): FormalStocktakeCountSentinel {
  if ((intent.action !== "submit_initial_count" && intent.action !== "submit_recount_count")
    || !intent.taskId || !intent.roundId || !intent.scopeId
    || !Number.isSafeInteger(roundNo) || roundNo < 1
    || intent.expectedTaskVersion === null) fail("只允许恢复正式日常盘点初盘或复盘计数");
  const expectedVersion = intent.action === "submit_initial_count" ? 1 : roundNo;
  if (roundNo !== expectedVersion) fail("日常盘点轮次与操作不一致");
  const trace = intent.headers["X-Request-ID"];
  if (typeof trace !== "string") fail("日常盘点追踪坐标无效");
  return validateFormalStocktakeCountSentinel({
    v: 1,
    kind: "formal_scope_count",
    task_id: intent.taskId,
    round_id: intent.roundId,
    round_no: roundNo,
    scope_id: intent.scopeId,
    operation: intent.action === "submit_initial_count" ? "initial_count" : "recount_count",
    actor_person_id: expected.person_id,
    actor_authorization_version: expected.authorization_version,
    trace_request_id: trace,
  });
}

function sameCoordinate(left: FormalStocktakeCountSentinel, right: FormalStocktakeCountSentinel): boolean {
  return left.task_id === right.task_id && left.round_id === right.round_id && left.scope_id === right.scope_id && left.operation === right.operation;
}

/**
 * At most one count POST is sent for this coordinate.  Once the sentinel is
 * durable, every result path (including a definite HTTP error) is GET-only
 * historical recovery.  `not_observed` leaves the marker in place.
 */
export async function submitDurableFormalStocktakeCount(options: Readonly<{
  intent: FormalStocktakeIntent;
  expectedIdentity: FormalStocktakeExpectedIdentity;
  roundNo: number;
  adapter: FormalStocktakeAdapter;
  store?: FormalStocktakeCountRecoveryStore;
  canCommit?: () => boolean;
}>): Promise<FormalStocktakeCountSubmissionResult> {
  const {
    intent,
    expectedIdentity,
    roundNo,
    adapter,
    store = getFormalStocktakeCountRecoveryStore(),
    canCommit = () => true,
  } = options;
  const sentinel = sentinelFromIntent(intent, expectedIdentity, roundNo);
  const recoveryAdapter = createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(expectedIdentity, adapter);
  const pending = store.readPending(sentinel.task_id);
  if (pending.kind === "corrupt" || pending.kind === "unavailable") fail("日常盘点持久恢复记录不可用，已停止写入");
  if (pending.kind === "valid" && pending.values?.some((value) => !sameCoordinate(value, sentinel))) {
    throw new FormalStocktakeCountSubmissionPendingError(pending.values[0]);
  }
  return store.withScopeLease(sentinel, async (lease) => {
    const existing = lease.read();
    if (existing.kind === "corrupt" || existing.kind === "unavailable") fail("日常盘点持久恢复记录不可用，已停止写入");
    if (existing.kind === "valid") {
      try {
        return Object.freeze({ recovered: true, ...(await recoverFormalStocktakeCount(lease, existing.value, recoveryAdapter, canCommit)) });
      } catch {
        throw new FormalStocktakeCountSubmissionPendingError(existing.value);
      }
    }
    if (!canCommit()) fail("当前盘点页面已变化，未发送计数请求");
    let persisted = false;
    try {
      await adapter.execute(intent, {
        noReplayReads: true,
        beforeWrite: async () => {
          // The adapter performs its own no-replay preflight first.  This final
          // identity/permission read and write-then-read storage check are the
          // last gates immediately before the single POST.
          await recoveryAdapter.loadIdentity();
          await recoveryAdapter.loadAccess();
          if (!canCommit()) fail("当前盘点页面已变化，未发送计数请求");
          lease.persist(sentinel);
          const stored = lease.read();
          if (stored.kind !== "valid" || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
            throw new FormalStocktakeCountSubmissionPendingError(sentinel);
          }
          persisted = true;
        },
      });
    } catch (error) {
      if (!persisted) throw error;
      // The POST may have committed, been rejected after commit, or become
      // unknown.  Never replay it: only the immutable command-status GET can
      // decide whether the marker may be cleared.
    }
    try {
      const recovered = await recoverFormalStocktakeCount(lease, sentinel, recoveryAdapter, canCommit);
      return Object.freeze({ recovered: true, ...recovered });
    } catch {
      throw new FormalStocktakeCountSubmissionPendingError(sentinel);
    }
  });
}

export { sentinelFromIntent };

