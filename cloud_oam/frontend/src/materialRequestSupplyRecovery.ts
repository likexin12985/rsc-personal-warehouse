import {
  type FormalMaterialRequestAdapter,
  validateFormalMaterialRequestAccess,
  validateFormalMaterialRequestFreshIdentity,
} from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail } from "./formalMaterialRequests";
import { SUPPLY_ACTIONS, type SupplyAction, type SupplyCommand, validateSupplyCommandStatus } from "./formalMaterialRequestSupply";

const KEY = "cloud-oam-material-request-supply-sentinel-v1";
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export type SupplySentinel = Readonly<{
  v: 1; kind: "material_request_supply"; x_request_id: string;
  person_id: string; authorization_version: number; request_id: string;
  action: SupplyAction; request_version: number; task_id: string | null; task_version: number | null;
}>;
export type SupplySentinelRead = Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: SupplySentinel }>;
export type SupplyRecoveryStore = Readonly<{
  read(): SupplySentinelRead;
  persist(sentinel: SupplySentinel): void;
  clear(trace: string): void;
}>;

function valid(value: unknown): value is SupplySentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return Object.keys(row).sort().join(",") === ["v", "kind", "x_request_id", "person_id", "authorization_version",
    "request_id", "action", "request_version", "task_id", "task_version"].sort().join(",")
    && row.v === 1 && row.kind === "material_request_supply"
    && typeof row.x_request_id === "string" && TRACE.test(row.x_request_id)
    && typeof row.person_id === "string" && UUID.test(row.person_id)
    && typeof row.request_id === "string" && UUID.test(row.request_id)
    && Number.isSafeInteger(row.authorization_version) && (row.authorization_version as number) > 0
    && Number.isSafeInteger(row.request_version) && (row.request_version as number) >= 0
    && SUPPLY_ACTIONS.includes(row.action as SupplyAction)
    && (row.action === "create_supply_task" ? row.task_id === null && row.task_version === null
      : typeof row.task_id === "string" && UUID.test(row.task_id)
        && Number.isSafeInteger(row.task_version) && (row.task_version as number) >= 0);
}
function session(): StorageLike | undefined {
  try { return typeof sessionStorage === "undefined" ? undefined : sessionStorage; } catch { return undefined; }
}
export function createSupplyRecoveryStore(storage: StorageLike | undefined = session()): SupplyRecoveryStore {
  function read(): SupplySentinelRead {
    if (!storage) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = storage.getItem(KEY); } catch { return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try {
      const parsed: unknown = JSON.parse(raw);
      return valid(parsed) ? { kind: "valid", value: Object.freeze(parsed) } : { kind: "corrupt" };
    } catch { return { kind: "corrupt" }; }
  }
  return Object.freeze({
    read,
    persist(sentinel: SupplySentinel) {
      if (!valid(sentinel)) throw new Error("供给恢复记录无效，已停止写入");
      const existing = read();
      if (existing.kind === "valid") {
        if (JSON.stringify(existing.value) === JSON.stringify(sentinel)) return;
        throw new Error("已有供给操作待核验，禁止覆盖原请求坐标");
      }
      if (existing.kind !== "missing" || !storage) throw new Error("供给恢复存储不可用，已停止写入");
      storage.setItem(KEY, JSON.stringify(sentinel));
      const reread = read();
      if (reread.kind !== "valid" || JSON.stringify(reread.value) !== JSON.stringify(sentinel)) {
        throw new Error("供给恢复记录写后核验失败，已停止写入");
      }
    },
    clear(trace: string) {
      const existing = read();
      if (!storage || existing.kind !== "valid" || existing.value.x_request_id !== trace) {
        throw new Error("供给恢复坐标不匹配，禁止清理");
      }
      storage.removeItem(KEY);
      if (read().kind !== "missing") throw new Error("供给恢复记录清理失败");
    },
  });
}

export function supplyRecoveryBlocked(read: SupplySentinelRead): boolean {
  return read.kind !== "missing";
}

/** Confirmation concerns the historical command; the reread can show a later task state. */
export async function recoverSupplyCommand(
  adapter: FormalMaterialRequestAdapter,
  store: SupplyRecoveryStore,
  sentinel: SupplySentinel,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: SupplyCommand; detail: MaterialRequestDetail }>> {
  if (!valid(sentinel)) throw new Error("供给恢复记录无效");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
  if (identity.person_id !== sentinel.person_id || identity.authorization_version !== sentinel.authorization_version) {
    throw new Error("登录身份或权限已变化，原供给操作继续保持待核验");
  }
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccess());
  if (!access.can_read || access.person_id !== sentinel.person_id || access.authorization_version !== sentinel.authorization_version) {
    throw new Error("当前权限无法核验原供给操作");
  }
  const status = validateSupplyCommandStatus(await adapter.supplyCommandStatus(sentinel.x_request_id));
  if (status.lookup_status !== "confirmed" || !status.command) {
    throw new Error("暂未查到供给操作的确定结果，继续保留原请求坐标；请稍后核验");
  }
  const command = status.command;
  if (command.request_id !== sentinel.request_id || command.action !== sentinel.action
      || command.request_version !== sentinel.request_version + 1
      || (sentinel.task_id !== null && (command.supply_task_id !== sentinel.task_id
        || command.task_version !== (sentinel.task_version as number) + 1))) {
    throw new Error("供给命令与原请求锚点不一致，继续保持待核验");
  }
  const detail = validateMaterialRequestDetail(await adapter.detail(command.request_id));
  const task = detail.supply_tasks.find((row) => row.id === command.supply_task_id);
  if (!task || detail.request_id !== command.request_id || detail.request_version < command.request_version
      || detail.current_revision_id !== command.revision_id || detail.current_revision_no !== command.revision_no
      || detail.approval_instance?.instance_id !== command.approval_instance_id
      || detail.approval_instance?.attempt_no !== command.approval_attempt_no
      || task.task_no !== command.task_no || task.version < command.task_version
      || (task.version === command.task_version && task.status !== command.task_status)
      || (detail.request_version === command.request_version && (task.version !== command.task_version
        || JSON.stringify(detail.states) !== JSON.stringify(command.states)))) {
    throw new Error("供给命令已登记，但当前需求或任务回读未能建立一致关系，继续保持待核验");
  }
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
  if (afterIdentity.person_id !== sentinel.person_id || afterIdentity.authorization_version !== sentinel.authorization_version) {
    throw new Error("核验期间登录权限发生变化，继续保留原供给坐标");
  }
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccess());
  if (!afterAccess.can_read || afterAccess.person_id !== sentinel.person_id
      || afterAccess.authorization_version !== sentinel.authorization_version || !canCommit()) {
    throw new Error("核验页面或权限已变化，继续保留原供给坐标");
  }
  store.clear(sentinel.x_request_id);
  return { command, detail };
}
