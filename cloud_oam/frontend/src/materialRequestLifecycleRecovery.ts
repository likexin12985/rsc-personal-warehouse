import { ApiError } from "./api";
import {
  type FormalMaterialRequestAccess,
  type FormalMaterialRequestAdapter,
  validateFormalMaterialRequestAccess,
  validateFormalMaterialRequestFreshIdentity,
} from "./formalMaterialRequestAdapter";
import {
  type MaterialRequestDetail,
  type MaterialRequestLifecycleCommand,
  validateMaterialRequestDetail,
  validateMaterialRequestLifecycleCommandStatus,
} from "./formalMaterialRequests";

const SAFE_COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const SENTINEL_KEY = "cloud-oam-material-request-lifecycle-sentinel-v1";

export type MaterialRequestLifecycleSentinel = Readonly<{
  v: 1;
  kind: "material_request_lifecycle";
  x_request_id: string;
  created_at: number;
}>;

type SessionStorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type MaterialRequestLifecycleSentinelRead =
  | Readonly<{ kind: "missing" }>
  | Readonly<{ kind: "valid"; value: MaterialRequestLifecycleSentinel }>
  | Readonly<{ kind: "corrupt" }>
  | Readonly<{ kind: "unavailable" }>;

export interface MaterialRequestLifecycleRecoveryStore {
  read(): MaterialRequestLifecycleSentinelRead;
  persist(xRequestId: string): MaterialRequestLifecycleSentinel;
  clear(xRequestId: string): void;
}

export type MaterialRequestLifecycleRecoveryOutcome =
  | Readonly<{
    kind: "confirmed";
    access: FormalMaterialRequestAccess;
    command: MaterialRequestLifecycleCommand;
    detail: MaterialRequestDetail;
  }>
  | Readonly<{
    kind: "blocked";
    access: FormalMaterialRequestAccess | null;
    message: string;
  }>;

function isSentinel(value: unknown): value is MaterialRequestLifecycleSentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<MaterialRequestLifecycleSentinel>;
  return Object.keys(value).sort().join(",") === "created_at,kind,v,x_request_id"
    && candidate.v === 1
    && candidate.kind === "material_request_lifecycle"
    && typeof candidate.x_request_id === "string"
    && SAFE_COORDINATE.test(candidate.x_request_id)
    && typeof candidate.created_at === "number"
    && Number.isSafeInteger(candidate.created_at)
    && candidate.created_at > 0;
}

function recoveryError(message: string): never {
  throw new ApiError(409, message);
}

function readStorage(storage: SessionStorageLike | undefined): MaterialRequestLifecycleSentinelRead {
  if (!storage) return { kind: "unavailable" };
  let raw: string | null;
  try {
    raw = storage.getItem(SENTINEL_KEY);
  } catch {
    return { kind: "unavailable" };
  }
  if (raw === null) return { kind: "missing" };
  try {
    const value: unknown = JSON.parse(raw);
    return isSentinel(value) ? { kind: "valid", value: Object.freeze(value) } : { kind: "corrupt" };
  } catch {
    return { kind: "corrupt" };
  }
}

function browserSessionStorage(): SessionStorageLike | undefined {
  try {
    return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
  } catch {
    return undefined;
  }
}

/**
 * Stores only the non-secret trace coordinate needed to ask the server whether a lifecycle command
 * committed. The idempotency key, request target, action, reason, body and identity never enter
 * browser storage. There is deliberately no TTL: an unknown outcome cannot expire into permission
 * to manufacture a replacement command.
 */
export function createMaterialRequestLifecycleRecoveryStore(
  storage: SessionStorageLike | undefined = browserSessionStorage(),
  now: () => number = Date.now,
): MaterialRequestLifecycleRecoveryStore {
  return Object.freeze({
    read() {
      return readStorage(storage);
    },
    persist(xRequestId: string) {
      if (!SAFE_COORDINATE.test(xRequestId)) return recoveryError("生命周期命令恢复坐标无效");
      const existing = readStorage(storage);
      if (existing.kind === "valid") {
        if (existing.value.x_request_id !== xRequestId) {
          return recoveryError("已有其他生命周期命令待核验，禁止覆盖恢复坐标");
        }
        return existing.value;
      }
      if (existing.kind !== "missing" || !storage) {
        return recoveryError("生命周期命令恢复存储不可用或已损坏，已停止写入");
      }
      const createdAt = now();
      if (!Number.isSafeInteger(createdAt) || createdAt <= 0) {
        return recoveryError("生命周期命令恢复时间无效，已停止写入");
      }
      const sentinel: MaterialRequestLifecycleSentinel = Object.freeze({
        v: 1,
        kind: "material_request_lifecycle",
        x_request_id: xRequestId,
        created_at: createdAt,
      });
      try {
        storage.setItem(SENTINEL_KEY, JSON.stringify(sentinel));
      } catch {
        return recoveryError("无法持久化生命周期命令恢复坐标，已停止写入");
      }
      const written = readStorage(storage);
      if (written.kind !== "valid" || written.value.x_request_id !== xRequestId
          || written.value.created_at !== createdAt) {
        return recoveryError("生命周期命令恢复坐标持久化回读失败，已停止写入");
      }
      return written.value;
    },
    clear(xRequestId: string) {
      if (!SAFE_COORDINATE.test(xRequestId)) return recoveryError("生命周期命令恢复坐标无效");
      const existing = readStorage(storage);
      if (existing.kind !== "valid" || existing.value.x_request_id !== xRequestId || !storage) {
        return recoveryError("生命周期命令恢复坐标缺失、损坏或不匹配，禁止清理");
      }
      try {
        storage.removeItem(SENTINEL_KEY);
      } catch {
        return recoveryError("无法清理已核验的生命周期命令恢复坐标");
      }
      if (readStorage(storage).kind !== "missing") {
        return recoveryError("生命周期命令恢复坐标清理回读失败");
      }
    },
  });
}

function commandMatchesDetail(command: MaterialRequestLifecycleCommand, detail: MaterialRequestDetail): boolean {
  const approval = detail.approval_instance;
  return command.request_id === detail.request_id
    && command.request_version === detail.request_version
    && command.revision_id === detail.current_revision_id
    && command.revision_no === detail.current_revision_no
    && command.approval_instance_id === approval?.instance_id
    && command.approval_attempt_no === approval?.attempt_no
    && command.current_step_id === (approval?.current_step_id ?? null)
    && Object.keys(command.states).every((key) => (
      command.states[key as keyof typeof command.states] === detail.states[key as keyof typeof detail.states]
    ));
}

function retryableStatusError(error: unknown): boolean {
  return !(error instanceof ApiError
    && error.responseReceived
    && error.status >= 400
    && error.status < 500
    && error.status !== 408
    && error.status !== 425);
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "生命周期命令状态核验失败";
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

export async function recoverMaterialRequestLifecycleCommand(options: Readonly<{
  adapter: FormalMaterialRequestAdapter;
  store: MaterialRequestLifecycleRecoveryStore;
  sentinel: MaterialRequestLifecycleSentinel;
  retryDelaysMs?: readonly number[];
  wait?: (milliseconds: number) => Promise<void>;
}>): Promise<MaterialRequestLifecycleRecoveryOutcome> {
  let access: FormalMaterialRequestAccess | null = null;
  try {
    const identity = validateFormalMaterialRequestFreshIdentity(await options.adapter.loadIdentity());
    access = validateFormalMaterialRequestAccess(await options.adapter.loadAccess());
    if (
      identity.person_id !== access.person_id
      || identity.authorization_version !== access.authorization_version
    ) {
      return recoveryError("新鲜登录身份与访问授权不一致，生命周期命令保持待核验");
    }
    const delays = options.retryDelaysMs ?? [0, 100, 300];
    const pause = options.wait ?? wait;
    if (delays.length === 0 || delays.some((delay) => !Number.isSafeInteger(delay) || delay < 0)) {
      return recoveryError("生命周期命令核验退避配置无效");
    }
    let lastMessage = "服务端尚未观察到该请求坐标";
    for (let index = 0; index < delays.length; index += 1) {
      if (delays[index] > 0) await pause(delays[index]);
      try {
        const status = validateMaterialRequestLifecycleCommandStatus(
          await options.adapter.lifecycleCommandStatus(options.sentinel.x_request_id),
        );
        if (status.lookup_status === "not_observed") {
          lastMessage = "服务端尚未观察到该请求坐标";
          continue;
        }
        const command = status.command;
        if (command === null) {
          return recoveryError("已确认生命周期命令缺少服务端命令事实");
        }
        const permitted = command.action === "withdraw" ? access.can_withdraw : access.can_cancel;
        if (!access.can_read || !permitted) {
          return recoveryError("当前新鲜授权不允许核验该生命周期动作，恢复坐标已保留");
        }
        const detail = validateMaterialRequestDetail(
          await options.adapter.detail(command.request_id),
          command.request_id,
        );
        if (!commandMatchesDetail(command, detail)) {
          return recoveryError("已确认命令与需求版本、修订、审批锚点或十状态轴回读不一致");
        }
        options.store.clear(options.sentinel.x_request_id);
        return { kind: "confirmed", access, command, detail };
      } catch (error) {
        lastMessage = messageOf(error);
        if (!retryableStatusError(error)) break;
      }
    }
    return {
      kind: "blocked",
      access,
      message: `${lastMessage}；恢复坐标已保留，禁止生成新的撤回/取消请求坐标`,
    };
  } catch (error) {
    return {
      kind: "blocked",
      access,
      message: `${messageOf(error)}；恢复坐标已保留，禁止生成新的撤回/取消请求坐标`,
    };
  }
}

export function lifecycleSentinelBlockingMessage(
  read: MaterialRequestLifecycleSentinelRead,
): string {
  if (read.kind === "valid") {
    return `检测到待核验的生命周期请求坐标 ${read.value.x_request_id}，正在向服务端核验。`;
  }
  if (read.kind === "corrupt") {
    return "生命周期恢复标记已损坏；原值已保留，撤回和取消均已失败关闭。";
  }
  if (read.kind === "unavailable") {
    return "当前标签页无法读取生命周期恢复存储；撤回和取消均已失败关闭。";
  }
  return "";
}
