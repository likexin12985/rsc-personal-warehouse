import { parseDailyReference, type DailyReference } from './dailyReviewProtocol';
export type DailyReviewSentinel = Readonly<DailyReference & { v: 1; kind: 'daily_review' }>;

export type DailyReviewSentinelRead =
  | Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: DailyReviewSentinel }>;

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem"> &
  Partial<Pick<Storage, "length" | "key">>;

export interface DailyReviewLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>,
  ): Promise<T>;
}

export type DailyReviewTaskLease = Readonly<{
  read(): DailyReviewSentinelRead;
  persist(value: DailyReviewSentinel): void;
  clearExact(value: DailyReviewSentinel): void;
}>;

export type DailyReviewRecoveryStore = Readonly<{
  read(taskId: string): DailyReviewSentinelRead;
  /** Read every marker in this browser profile; malformed markers fail closed. */
  readPending(): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly DailyReviewSentinel[];
  }>;
  withCutoffLease<T>(
    taskId: string,
    work: (lease: DailyReviewTaskLease) => Promise<T>,
  ): Promise<T>;
}>;

const PREFIX = "cloud-oam-daily-review-sentinel-v1:";
const LOCK_PREFIX = "cloud-oam-daily-review-cutoff-v1:";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid(message = "日终审核恢复记录无效，已停止写入"): never {
  throw new Error(message);
}

function taskId(value: string): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid();
  return value.toLowerCase();
}

export function validateDailyReviewSentinel(value: unknown): DailyReviewSentinel {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid();
  const { v, kind, ...reference } = value as Record<string, unknown>;
  if (v !== 1 || kind !== 'daily_review') invalid();
  return Object.freeze({ v: 1, kind: 'daily_review', ...parseDailyReference(reference) });
}

function browserStorage(): StorageLike | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

function browserLocks(): DailyReviewLockManager | undefined {
  try {
    return typeof navigator === "undefined" ? undefined : navigator.locks;
  } catch {
    return undefined;
  }
}

let browserStore: DailyReviewRecoveryStore | undefined;

export function getDailyReviewRecoveryStore(): DailyReviewRecoveryStore {
  browserStore ??= createDailyReviewRecoveryStore();
  return browserStore;
}

export function createDailyReviewRecoveryStore(options: Readonly<{
  storage?: StorageLike | null;
  locks?: DailyReviewLockManager | null;
}> = {}): DailyReviewRecoveryStore {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const locks = options.locks === undefined ? browserLocks() : options.locks;
  const active = new Set<string>();
  const storageFaults = new Set<string>();

  function readChecked(checkedTaskId: string): DailyReviewSentinelRead {
    if (!storage || storageFaults.has(checkedTaskId)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(PREFIX + checkedTaskId);
      if (raw === null) return { kind: "missing" };
      try {
        const value = validateDailyReviewSentinel(JSON.parse(raw));
        return value.cutoff_id === checkedTaskId ? { kind: "valid", value } : { kind: "corrupt" };
      } catch {
        return { kind: "corrupt" };
      }
    } catch {
      return { kind: "unavailable" };
    }
  }

  function readPending(): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly DailyReviewSentinel[];
  }> {
    if (!storage || typeof storage.length !== "number" || typeof storage.key !== "function") {
      return { kind: "unavailable" };
    }
    try {
      const values: DailyReviewSentinel[] = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !key.startsWith(PREFIX)) continue;
        const suffix = key.slice(PREFIX.length);
        if (!UUID.test(suffix)) return { kind: "corrupt" };
        if (suffix !== suffix.toLowerCase()) return { kind: "corrupt" };
        const row = readChecked(suffix.toLowerCase());
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
    async withCutoffLease<T>(id: string, work: (lease: DailyReviewTaskLease) => Promise<T>): Promise<T> {
      const checkedTaskId = taskId(id);
      if (!locks || !storage || storageFaults.has(checkedTaskId)) {
        invalid("日终审核持久恢复或跨页面协调不可用，已停止写入");
      }
      if (active.has(checkedTaskId)) invalid("同一日终审核正在核验，请勿重复提交");
      active.add(checkedTaskId);
      try {
        return await locks.request(LOCK_PREFIX + checkedTaskId, { mode: "exclusive", ifAvailable: true }, async (lock) => {
          if (lock === null || lock === undefined) invalid("其他页面正在核验同一日终审核，已停止本次操作");
          let live = true;
          let persistedByThisLease: string | null = null;
          const requireLease = () => {
            if (!live || storageFaults.has(checkedTaskId)) invalid("日终审核协调已结束或存储异常，禁止写入");
          };
          const expected = (value: DailyReviewSentinel): DailyReviewSentinel => {
            requireLease();
            const checked = validateDailyReviewSentinel(value);
            if (checked.cutoff_id !== checkedTaskId) invalid("日终审核恢复记录与当前任务不一致");
            return checked;
          };
          const lease: DailyReviewTaskLease = Object.freeze({
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
                invalid("原日终审核仍待核验，禁止覆盖坐标或切换人员");
              }
              if (before.kind !== "missing") invalid("日终审核恢复记录不可用，禁止覆盖或新建请求");
              try {
                storage.setItem(PREFIX + checkedTaskId, serialized);
                const after = readChecked(checkedTaskId);
                if (after.kind !== "valid" || JSON.stringify(after.value) !== serialized) invalid();
                persistedByThisLease = serialized;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("日终审核恢复记录写后核验失败，已停止发送请求");
              }
            },
            clearExact(value) {
              const checked = expected(value);
              const before = readChecked(checkedTaskId);
              if (before.kind !== "valid" || JSON.stringify(before.value) !== JSON.stringify(checked)) {
                invalid("日终审核恢复坐标不匹配，禁止清理");
              }
              try {
                storage.removeItem(PREFIX + checkedTaskId);
                if (readChecked(checkedTaskId).kind !== "missing") invalid();
                persistedByThisLease = null;
              } catch {
                storageFaults.add(checkedTaskId);
                invalid("日终审核恢复记录清理未确认，继续停止写入");
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
