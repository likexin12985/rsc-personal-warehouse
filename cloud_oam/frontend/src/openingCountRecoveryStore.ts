/** Durable, non-sensitive count coordinates. Business payloads and keys never enter storage. */
export type OpeningCountSentinel = Readonly<{
  v: 1;
  kind: "opening_scope_count";
  task_id: string;
  round_id: string;
  round_no: number;
  scope_id: string;
  actor_person_id: string;
  actor_authorization_version: number;
  trace_request_id: string;
}>;
export type OpeningCountSentinelRead = Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: OpeningCountSentinel }>;
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export interface OpeningCountLockManager {
  request<T>(name: string, options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>): Promise<T>;
}
export type OpeningCountTaskLease = Readonly<{
  read(): OpeningCountSentinelRead;
  persist(value: OpeningCountSentinel): void;
  clearExact(value: OpeningCountSentinel): void;
}>;
export type OpeningCountRecoveryStore = Readonly<{
  read(taskId: string): OpeningCountSentinelRead;
  withTaskLease<T>(taskId: string, work: (lease: OpeningCountTaskLease) => Promise<T>): Promise<T>;
}>;

const PREFIX = "cloud-oam-opening-count-sentinel-v1:";
const LOCK_PREFIX = "cloud-oam-opening-count-task-v1:";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const FIELDS = ["v", "kind", "task_id", "round_id", "round_no", "scope_id", "actor_person_id",
  "actor_authorization_version", "trace_request_id"];

function invalid(message = "盘点恢复记录无效，已停止写入"): never { throw new Error(message); }
function taskId(value: string): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
  return value;
}

export function validateOpeningCountSentinel(value: unknown): OpeningCountSentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== FIELDS.length || FIELDS.some((key) => !Object.hasOwn(row, key))
    || row.v !== 1 || row.kind !== "opening_scope_count"
    || ![row.task_id, row.round_id, row.scope_id, row.actor_person_id].every((id) => typeof id === "string" && UUID.test(id))
    || !Number.isSafeInteger(row.round_no) || (row.round_no as number) < 1
    || !Number.isSafeInteger(row.actor_authorization_version) || (row.actor_authorization_version as number) < 1
    || typeof row.trace_request_id !== "string" || !TRACE.test(row.trace_request_id)) invalid();
  // Fixed field order makes byte comparison independent of caller object order.
  return Object.freeze({ v: 1, kind: "opening_scope_count", task_id: row.task_id as string,
    round_id: row.round_id as string, round_no: row.round_no as number, scope_id: row.scope_id as string,
    actor_person_id: row.actor_person_id as string,
    actor_authorization_version: row.actor_authorization_version as number,
    trace_request_id: row.trace_request_id });
}

function browserStorage(): StorageLike | undefined {
  try { return typeof localStorage === "undefined" ? undefined : localStorage; } catch { return undefined; }
}
function browserLocks(): OpeningCountLockManager | undefined {
  try { return typeof navigator === "undefined" ? undefined : navigator.locks; } catch { return undefined; }
}

let browserStore: OpeningCountRecoveryStore | undefined;
/** One per-page fault latch, backed by durable cross-page coordinates. */
export function getOpeningCountRecoveryStore(): OpeningCountRecoveryStore {
  browserStore ??= createOpeningCountRecoveryStore();
  return browserStore;
}

/** Other commands may not race a count, or bypass its unresolved history. */
export async function withNoPendingOpeningCount<T>(
  id: string, work: () => Promise<T>, store: OpeningCountRecoveryStore = getOpeningCountRecoveryStore(),
): Promise<T> {
  return store.withTaskLease(id, async (lease) => {
    if (lease.read().kind !== "missing") {
      invalid("该任务有盘点请求待核验或恢复记录不可用；请先在盘点中心只读核验，禁止其他写入");
    }
    return work();
  });
}

/**
 * The task-specific Web Lock spans the whole caller operation, not only the
 * storage write. ifAvailable forbids queued double clicks from becoming new
 * requests after an earlier rejection clears its sentinel. Other tasks use
 * independent locks. Unsupported browsers fail closed; no in-memory fallback.
 */
export function createOpeningCountRecoveryStore(options: Readonly<{
  storage?: StorageLike | null;
  locks?: OpeningCountLockManager | null;
}> = {}): OpeningCountRecoveryStore {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const locks = options.locks === undefined ? browserLocks() : options.locks;
  const active = new Set<string>();
  const storageFaults = new Set<string>();

  function read(checkedTaskId: string): OpeningCountSentinelRead {
    if (!storage || storageFaults.has(checkedTaskId)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(PREFIX + checkedTaskId);
      if (raw === null) return { kind: "missing" };
      try {
        const value = validateOpeningCountSentinel(JSON.parse(raw));
        return value.task_id === checkedTaskId ? { kind: "valid", value } : { kind: "corrupt" };
      } catch { return { kind: "corrupt" }; }
    } catch { return { kind: "unavailable" }; }
  }

  return Object.freeze({
    read(id: string) { return read(taskId(id)); },
    async withTaskLease<T>(id: string, work: (lease: OpeningCountTaskLease) => Promise<T>): Promise<T> {
      const checkedTaskId = taskId(id);
      if (!locks || !storage || storageFaults.has(checkedTaskId)) invalid("盘点持久恢复或跨页面协调不可用，已停止写入");
      if (active.has(checkedTaskId)) invalid("同一盘点正在核验，请勿重复提交");
      active.add(checkedTaskId);
      try {
        return await locks.request(LOCK_PREFIX + checkedTaskId, { mode: "exclusive", ifAvailable: true }, async (lock) => {
          if (lock === null || lock === undefined) invalid("其他页面正在核验同一盘点，已停止本次操作");
          let live = true;
          let persistedByThisLease: string | null = null;
          function requireLease(): void {
            if (!live || storageFaults.has(checkedTaskId)) invalid("盘点协调已结束或存储异常，禁止写入");
          }
          function expected(value: OpeningCountSentinel): OpeningCountSentinel {
            requireLease();
            const checked = validateOpeningCountSentinel(value);
            if (checked.task_id !== checkedTaskId) invalid("盘点恢复记录与当前任务不一致");
            return checked;
          }
          const lease: OpeningCountTaskLease = Object.freeze({
            read() { requireLease(); return read(checkedTaskId); },
            persist(value: OpeningCountSentinel) {
              const checked = expected(value);
              const serialized = JSON.stringify(checked);
              const before = read(checkedTaskId);
              if (before.kind === "valid") {
                if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return;
                invalid("原盘点请求仍待核验，禁止覆盖坐标或切换人员");
              }
              if (before.kind !== "missing") invalid("盘点恢复记录不可用，禁止覆盖或新建请求");
              try {
                storage.setItem(PREFIX + checkedTaskId, serialized);
                const after = read(checkedTaskId);
                if (after.kind !== "valid" || JSON.stringify(after.value) !== serialized) invalid();
                persistedByThisLease = serialized;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("盘点恢复记录写后核验失败，已停止发送请求");
              }
            },
            clearExact(value: OpeningCountSentinel) {
              const checked = expected(value);
              const before = read(checkedTaskId);
              if (before.kind !== "valid" || JSON.stringify(before.value) !== JSON.stringify(checked)) {
                invalid("盘点恢复坐标不匹配，禁止清理");
              }
              try {
                storage.removeItem(PREFIX + checkedTaskId);
                if (read(checkedTaskId).kind !== "missing") invalid();
                persistedByThisLease = null;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("盘点恢复记录清理未确认，继续停止写入");
              }
            },
          });
          try { return await work(lease); } finally { live = false; }
        });
      } finally { active.delete(checkedTaskId); }
    },
  });
}
