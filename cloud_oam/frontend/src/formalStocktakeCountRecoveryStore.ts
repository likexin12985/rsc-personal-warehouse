/**
 * Durable, non-sensitive coordinates for a non-opening stocktake scope count.
 *
 * The count body, quantities, evidence identifiers, idempotency key, request
 * body and credentials are deliberately excluded.  A marker is cleared only
 * after a matching read-only historical command proof has been validated.
 */

export type FormalStocktakeCountOperation = "initial_count" | "recount_count";

export type FormalStocktakeCountSentinel = Readonly<{
  v: 1;
  kind: "formal_scope_count";
  task_id: string;
  round_id: string;
  round_no: number;
  scope_id: string;
  operation: FormalStocktakeCountOperation;
  actor_person_id: string;
  actor_authorization_version: number;
  trace_request_id: string;
}>;

export type FormalStocktakeCountSentinelRead =
  | Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: FormalStocktakeCountSentinel }>;

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem"> &
  Partial<Pick<Storage, "length" | "key">>;

export interface FormalStocktakeCountLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>,
  ): Promise<T>;
}

export type FormalStocktakeCountTaskLease = Readonly<{
  read(): FormalStocktakeCountSentinelRead;
  persist(value: FormalStocktakeCountSentinel): void;
  clearExact(value: FormalStocktakeCountSentinel): void;
}>;

export type FormalStocktakeCountRecoveryStore = Readonly<{
  read(value: FormalStocktakeCountSentinel): FormalStocktakeCountSentinelRead;
  /** Scan all markers, or only markers belonging to one task. */
  readPending(taskId?: string): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly FormalStocktakeCountSentinel[];
  }>;
  withScopeLease<T>(
    value: FormalStocktakeCountSentinel,
    work: (lease: FormalStocktakeCountTaskLease) => Promise<T>,
  ): Promise<T>;
}>;

const PREFIX = "cloud-oam-formal-stocktake-count-sentinel-v1:";
const LOCK_PREFIX = "cloud-oam-formal-stocktake-count-scope-v1:";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const FIELDS = [
  "v", "kind", "task_id", "round_id", "round_no", "scope_id", "operation",
  "actor_person_id", "actor_authorization_version", "trace_request_id",
] as const;

function invalid(message = "日常盘点恢复记录无效，已停止写入"): never {
  throw new Error(message);
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value)) invalid(`${name}无效`);
  return value.toLowerCase();
}

function taskId(value: string): string {
  return uuid(value, "task_id");
}

function operation(value: unknown): FormalStocktakeCountOperation {
  if (value !== "initial_count" && value !== "recount_count") invalid("日常盘点操作无效");
  return value;
}

export function validateFormalStocktakeCountSentinel(value: unknown): FormalStocktakeCountSentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).length !== FIELDS.length
    || FIELDS.some((key) => !Object.hasOwn(row, key))
    || row.v !== 1
    || row.kind !== "formal_scope_count"
    || !Number.isSafeInteger(row.round_no) || (row.round_no as number) < 1
    || !Number.isSafeInteger(row.actor_authorization_version)
    || (row.actor_authorization_version as number) < 1
    || typeof row.trace_request_id !== "string"
    || !TRACE.test(row.trace_request_id)
  ) invalid();
  const roundNo = row.round_no as number;
  const checkedOperation = operation(row.operation);
  // An initial command can only target round one; a recount cannot target it.
  if ((checkedOperation === "initial_count") !== (roundNo === 1)) invalid("日常盘点轮次与操作不一致");
  return Object.freeze({
    v: 1,
    kind: "formal_scope_count",
    task_id: uuid(row.task_id, "task_id"),
    round_id: uuid(row.round_id, "round_id"),
    round_no: roundNo,
    scope_id: uuid(row.scope_id, "scope_id"),
    operation: checkedOperation,
    actor_person_id: uuid(row.actor_person_id, "actor_person_id"),
    actor_authorization_version: row.actor_authorization_version as number,
    trace_request_id: row.trace_request_id as string,
  });
}

function keyOf(value: FormalStocktakeCountSentinel): string {
  const checked = validateFormalStocktakeCountSentinel(value);
  return `${checked.task_id}:${checked.round_id}:${checked.scope_id}:${checked.operation}`;
}

function browserStorage(): StorageLike | undefined {
  try { return typeof localStorage === "undefined" ? undefined : localStorage; } catch { return undefined; }
}

function browserLocks(): FormalStocktakeCountLockManager | undefined {
  try { return typeof navigator === "undefined" ? undefined : navigator.locks; } catch { return undefined; }
}

let browserStore: FormalStocktakeCountRecoveryStore | undefined;

export function getFormalStocktakeCountRecoveryStore(): FormalStocktakeCountRecoveryStore {
  browserStore ??= createFormalStocktakeCountRecoveryStore();
  return browserStore;
}

export function createFormalStocktakeCountRecoveryStore(options: Readonly<{
  storage?: StorageLike | null;
  locks?: FormalStocktakeCountLockManager | null;
}> = {}): FormalStocktakeCountRecoveryStore {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const locks = options.locks === undefined ? browserLocks() : options.locks;
  const active = new Set<string>();
  const storageFaults = new Set<string>();

  function readChecked(value: FormalStocktakeCountSentinel): FormalStocktakeCountSentinelRead {
    const checked = validateFormalStocktakeCountSentinel(value);
    const id = keyOf(checked);
    if (!storage || storageFaults.has(id)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(PREFIX + id);
      if (raw === null) return { kind: "missing" };
      try {
        const decoded = validateFormalStocktakeCountSentinel(JSON.parse(raw));
        return keyOf(decoded) === id ? { kind: "valid", value: decoded } : { kind: "corrupt" };
      } catch { return { kind: "corrupt" }; }
    } catch {
      storageFaults.add(id);
      return { kind: "unavailable" };
    }
  }

  function readPending(taskFilter?: string): Readonly<{
    kind: "missing" | "corrupt" | "unavailable" | "valid";
    values?: readonly FormalStocktakeCountSentinel[];
  }> {
    const checkedTask = taskFilter === undefined ? undefined : taskId(taskFilter);
    if (!storage || typeof storage.length !== "number" || typeof storage.key !== "function") return { kind: "unavailable" };
    try {
      const values: FormalStocktakeCountSentinel[] = [];
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (!key || !key.startsWith(PREFIX)) continue;
        const suffix = key.slice(PREFIX.length);
        const parts = suffix.split(":");
        if (parts.length !== 4 || !UUID.test(parts[0]) || !UUID.test(parts[1]) || !UUID.test(parts[2]) || !["initial_count", "recount_count"].includes(parts[3]) || suffix !== suffix.toLowerCase()) return { kind: "corrupt" };
        const raw = storage.getItem(key);
        if (raw === null) return { kind: "unavailable" };
        let decoded: FormalStocktakeCountSentinel;
        try { decoded = validateFormalStocktakeCountSentinel(JSON.parse(raw)); } catch { return { kind: "corrupt" }; }
        if (keyOf(decoded) !== suffix) return { kind: "corrupt" };
        if (checkedTask === undefined || decoded.task_id === checkedTask) values.push(decoded);
      }
      return values.length ? { kind: "valid", values: Object.freeze(values) } : { kind: "missing" };
    } catch {
      if (checkedTask !== undefined) storageFaults.add(`task:${checkedTask}`);
      return { kind: "unavailable" };
    }
  }

  return Object.freeze({
    read(value: FormalStocktakeCountSentinel) {
      return readChecked(validateFormalStocktakeCountSentinel(value));
    },
    readPending,
    async withScopeLease<T>(value: FormalStocktakeCountSentinel, work: (lease: FormalStocktakeCountTaskLease) => Promise<T>): Promise<T> {
      const checked = validateFormalStocktakeCountSentinel(value);
      const id = keyOf(checked);
      if (!locks || !storage) invalid("日常盘点持久恢复或跨页面协调不可用，已停止写入");
      if (storageFaults.has(id) || active.has(id)) invalid("同一日常盘点范围正在核验，请勿重复提交");
      if (typeof work !== "function") invalid("日常盘点协调回调无效");
      active.add(id);
      try {
        return await locks.request(LOCK_PREFIX + id, { mode: "exclusive", ifAvailable: true }, async (lock) => {
          if (lock === null || lock === undefined) invalid("其他页面正在核验同一日常盘点范围，已停止本次操作");
          let live = true;
          let persistedByThisLease: string | null = null;
          const requireLease = () => {
            if (!live || storageFaults.has(id)) invalid("日常盘点协调已结束或存储异常，禁止写入");
          };
          const expected = (input: FormalStocktakeCountSentinel) => {
            requireLease();
            const candidate = validateFormalStocktakeCountSentinel(input);
            if (keyOf(candidate) !== id) invalid("日常盘点恢复坐标与当前范围不一致");
            return candidate;
          };
          const lease: FormalStocktakeCountTaskLease = Object.freeze({
            read() { requireLease(); return readChecked(checked); },
            persist(input) {
              const candidate = expected(input);
              const serialized = JSON.stringify(candidate);
              const before = readChecked(checked);
              if (before.kind === "valid") {
                if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return;
                invalid("原日常盘点请求仍待核验，禁止覆盖坐标");
              }
              if (before.kind !== "missing") invalid("日常盘点恢复记录不可用，禁止覆盖或新建请求");
              try {
                storage.setItem(PREFIX + id, serialized);
                const after = readChecked(checked);
                if (after.kind !== "valid" || JSON.stringify(after.value) !== serialized) invalid();
                persistedByThisLease = serialized;
              } catch {
                storageFaults.add(id);
                invalid("日常盘点恢复记录写后核验失败，已停止发送请求");
              }
            },
            clearExact(input) {
              const candidate = expected(input);
              const before = readChecked(checked);
              if (before.kind !== "valid" || JSON.stringify(before.value) !== JSON.stringify(candidate)) invalid("日常盘点恢复坐标不匹配，禁止清理");
              try {
                storage.removeItem(PREFIX + id);
                if (readChecked(checked).kind !== "missing") invalid();
                persistedByThisLease = null;
              } catch {
                storageFaults.add(id);
                invalid("日常盘点恢复记录清理未确认，继续阻塞写入");
              }
            },
          });
          try {
            const initial = lease.read();
            if (initial.kind === "corrupt" || initial.kind === "unavailable") invalid("日常盘点持久恢复记录不可用，已停止写入");
            return await work(lease);
          } finally {
            live = false;
          }
        });
      } finally {
        active.delete(id);
      }
    },
  });
}
