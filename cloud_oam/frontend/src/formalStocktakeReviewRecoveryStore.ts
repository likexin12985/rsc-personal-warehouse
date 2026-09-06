/**
 * Durable coordinates for non-opening stocktake review commands.
 *
 * Only public recovery coordinates are persisted.  Review bodies (including
 * comments and per-difference decisions), idempotency keys and request hashes
 * never enter browser storage.  A marker remains sticky until a matching,
 * read-only historical proof is verified.
 */

export type FormalStocktakeReviewStage = "region" | "headquarters";

export type FormalStocktakeReviewSentinel = Readonly<{
  v: 1;
  kind: "formal_stocktake_review";
  task_id: string;
  round_id: string;
  review_stage: FormalStocktakeReviewStage;
  actor_person_id: string;
  actor_authorization_version: number;
  expected_task_version: number;
  trace_request_id: string;
}>;

export type FormalStocktakeReviewSentinelRead =
  | Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: FormalStocktakeReviewSentinel }>;

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem"> &
  Partial<Pick<Storage, "length" | "key">>;

export interface FormalStocktakeReviewLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>,
  ): Promise<T>;
}

export type FormalStocktakeReviewTaskLease = Readonly<{
  read(): FormalStocktakeReviewSentinelRead;
  persist(value: FormalStocktakeReviewSentinel): void;
  clearExact(value: FormalStocktakeReviewSentinel): void;
}>;

export type FormalStocktakeReviewRecoveryStore = Readonly<{
  read(taskId: string): FormalStocktakeReviewSentinelRead;
  readPending(): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly FormalStocktakeReviewSentinel[];
  }>;
  withTaskLease<T>(
    taskId: string,
    work: (lease: FormalStocktakeReviewTaskLease) => Promise<T>,
  ): Promise<T>;
}>;

const PREFIX = "cloud-oam-formal-stocktake-review-sentinel-v1:";
const LOCK_PREFIX = "cloud-oam-formal-stocktake-review-task-v1:";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const FIELDS = [
  "v", "kind", "task_id", "round_id", "review_stage", "actor_person_id",
  "actor_authorization_version", "expected_task_version", "trace_request_id",
] as const;

function invalid(message = "盘点复核恢复记录无效，已停止写入"): never {
  throw new Error(message);
}

function taskId(value: string): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid("盘点复核任务坐标无效");
  return value.toLowerCase();
}

export function validateFormalStocktakeReviewSentinel(
  value: unknown,
): FormalStocktakeReviewSentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).length !== FIELDS.length
    || FIELDS.some((key) => !Object.hasOwn(row, key))
    || row.v !== 1
    || row.kind !== "formal_stocktake_review"
    || typeof row.task_id !== "string"
    || !UUID.test(row.task_id)
    || typeof row.round_id !== "string"
    || !UUID.test(row.round_id)
    || (row.review_stage !== "region" && row.review_stage !== "headquarters")
    || typeof row.actor_person_id !== "string"
    || !UUID.test(row.actor_person_id)
    || !Number.isSafeInteger(row.actor_authorization_version)
    || (row.actor_authorization_version as number) < 1
    || !Number.isSafeInteger(row.expected_task_version)
    || (row.expected_task_version as number) < 0
    || typeof row.trace_request_id !== "string"
    || !TRACE.test(row.trace_request_id)
  ) invalid();
  return Object.freeze({
    v: 1,
    kind: "formal_stocktake_review",
    task_id: (row.task_id as string).toLowerCase(),
    round_id: (row.round_id as string).toLowerCase(),
    review_stage: row.review_stage as FormalStocktakeReviewStage,
    actor_person_id: (row.actor_person_id as string).toLowerCase(),
    actor_authorization_version: row.actor_authorization_version as number,
    expected_task_version: row.expected_task_version as number,
    trace_request_id: row.trace_request_id as string,
  });
}

function browserStorage(): StorageLike | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

function browserLocks(): FormalStocktakeReviewLockManager | undefined {
  try {
    return typeof navigator === "undefined" ? undefined : navigator.locks;
  } catch {
    return undefined;
  }
}

let browserStore: FormalStocktakeReviewRecoveryStore | undefined;

export function getFormalStocktakeReviewRecoveryStore(): FormalStocktakeReviewRecoveryStore {
  browserStore ??= createFormalStocktakeReviewRecoveryStore();
  return browserStore;
}

export function createFormalStocktakeReviewRecoveryStore(options: Readonly<{
  storage?: StorageLike | null;
  locks?: FormalStocktakeReviewLockManager | null;
}> = {}): FormalStocktakeReviewRecoveryStore {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const locks = options.locks === undefined ? browserLocks() : options.locks;
  const active = new Set<string>();
  const storageFaults = new Set<string>();

  function readChecked(checkedTaskId: string): FormalStocktakeReviewSentinelRead {
    if (!storage || storageFaults.has(checkedTaskId)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(PREFIX + checkedTaskId);
      if (raw === null) return { kind: "missing" };
      try {
        const value = validateFormalStocktakeReviewSentinel(JSON.parse(raw));
        return value.task_id === checkedTaskId ? { kind: "valid", value } : { kind: "corrupt" };
      } catch {
        return { kind: "corrupt" };
      }
    } catch {
      return { kind: "unavailable" };
    }
  }

  function readPending(): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly FormalStocktakeReviewSentinel[];
  }> {
    if (!storage || typeof storage.length !== "number" || typeof storage.key !== "function") {
      return { kind: "unavailable" };
    }
    try {
      const values: FormalStocktakeReviewSentinel[] = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !key.startsWith(PREFIX)) continue;
        const suffix = key.slice(PREFIX.length);
        if (!UUID.test(suffix) || suffix !== suffix.toLowerCase()) return { kind: "corrupt" };
        const row = readChecked(suffix);
        if (row.kind === "unavailable" || row.kind === "corrupt") return { kind: row.kind };
        if (row.kind === "valid") values.push(row.value);
      }
      return values.length ? { kind: "valid", values: Object.freeze(values) } : { kind: "missing" };
    } catch {
      return { kind: "unavailable" };
    }
  }

  return Object.freeze({
    read(id: string) {
      return readChecked(taskId(id));
    },
    readPending,
    async withTaskLease<T>(id: string, work: (lease: FormalStocktakeReviewTaskLease) => Promise<T>): Promise<T> {
      const checkedTaskId = taskId(id);
      if (!locks || !storage || storageFaults.has(checkedTaskId)) {
        invalid("盘点复核持久恢复或跨页面协调不可用，已停止写入");
      }
      if (active.has(checkedTaskId)) invalid("同一盘点复核正在核验，请勿重复提交");
      active.add(checkedTaskId);
      try {
        return await locks.request(LOCK_PREFIX + checkedTaskId, { mode: "exclusive", ifAvailable: true }, async (lock) => {
          if (lock === null || lock === undefined) invalid("其他页面正在核验同一盘点复核，已停止本次操作");
          let live = true;
          let persistedByThisLease: string | null = null;
          const requireLease = () => {
            if (!live || storageFaults.has(checkedTaskId)) invalid("盘点复核协调已结束或存储异常，禁止写入");
          };
          const expected = (value: FormalStocktakeReviewSentinel): FormalStocktakeReviewSentinel => {
            requireLease();
            const checked = validateFormalStocktakeReviewSentinel(value);
            if (checked.task_id !== checkedTaskId) invalid("盘点复核恢复记录与当前任务不一致");
            return checked;
          };
          const lease: FormalStocktakeReviewTaskLease = Object.freeze({
            read() {
              requireLease();
              return readChecked(checkedTaskId);
            },
            persist(value) {
              const checked = expected(value);
              const serialized = JSON.stringify(checked);
              const before = readChecked(checkedTaskId);
              if (before.kind === "valid") {
                if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return;
                invalid("原盘点复核仍待核验，禁止覆盖坐标或切换人员");
              }
              if (before.kind !== "missing") invalid("盘点复核恢复记录不可用，禁止覆盖或新建请求");
              try {
                storage.setItem(PREFIX + checkedTaskId, serialized);
                const after = readChecked(checkedTaskId);
                if (after.kind !== "valid" || JSON.stringify(after.value) !== serialized) invalid();
                persistedByThisLease = serialized;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("盘点复核恢复记录写后核验失败，已停止发送请求");
              }
            },
            clearExact(value) {
              const checked = expected(value);
              const before = readChecked(checkedTaskId);
              if (before.kind !== "valid" || JSON.stringify(before.value) !== JSON.stringify(checked)) {
                invalid("盘点复核恢复坐标不匹配，禁止清理");
              }
              try {
                storage.removeItem(PREFIX + checkedTaskId);
                if (readChecked(checkedTaskId).kind !== "missing") invalid();
                persistedByThisLease = null;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("盘点复核恢复记录清理未确认，继续停止写入");
              }
            },
          });
          try {
            return await work(lease);
          } finally {
            live = false;
          }
        });
      } finally {
        active.delete(checkedTaskId);
      }
    },
  });
}

