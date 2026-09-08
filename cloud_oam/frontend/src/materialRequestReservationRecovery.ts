import {
  type FormalMaterialRequestAdapter,
  validateFormalMaterialRequestAccess,
  validateFormalMaterialRequestFreshIdentity,
} from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail, validateMaterialRequestStateAxes } from "./formalMaterialRequests";
import {
  type MaterialRequestReservationCommand,
  validateMaterialRequestReservationCommandStatus,
} from "./formalMaterialRequestReservationCommandStatus";

const KEY = "cloud-oam-material-request-reservation-sentinel-v1";
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const QUANTITY = /^(?:0|[1-9]\d{0,14})\.\d{3}$/;

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type ReservationSentinel = Readonly<{
  v: 2;
  kind: "material_request_reservation";
  x_request_id: string;
  person_id: string;
  authorization_version: number;
  request_id: string;
  request_line_id: string;
  allocation_id: string;
  request_version: number;
  reserved_qty: string;
  revision_id: string;
  revision_no: number;
  serial_ids: readonly string[];
  state_axes: MaterialRequestDetail["states"];
  source_stock_account_id: string;
  source_balance_version: number;
  source_ledger_cursor: number;
}>;

export type ReservationSentinelRead =
  | Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: ReservationSentinel }>;

export type ReservationRecoveryStore = Readonly<{
  read(): ReservationSentinelRead;
  persist(value: ReservationSentinel): void;
  clear(trace: string): void;
}>;

function validAxes(value: unknown): boolean {
  try { validateMaterialRequestStateAxes(value); return true; } catch { return false; }
}
export function reservationSnapshotsMatch(left: unknown, right: unknown): boolean {
  const stable = (value: unknown): unknown => Array.isArray(value) ? value.map(stable)
    : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, stable(item)])) : value;
  return JSON.stringify(stable(left)) === JSON.stringify(stable(right));
}
function valid(value: unknown): value is ReservationSentinel {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const keys = [
    "v", "kind", "x_request_id", "person_id", "authorization_version", "request_id",
    "request_line_id", "allocation_id", "request_version", "reserved_qty",
    "source_stock_account_id", "source_balance_version", "source_ledger_cursor", "revision_id", "revision_no", "serial_ids", "state_axes",
  ].sort();
  return Object.keys(row).sort().join(",") === keys.join(",")
    && row.v === 2
    && row.kind === "material_request_reservation"
    && typeof row.x_request_id === "string" && TRACE.test(row.x_request_id)
    && typeof row.person_id === "string" && UUID.test(row.person_id)
    && typeof row.request_id === "string" && UUID.test(row.request_id)
    && typeof row.request_line_id === "string" && UUID.test(row.request_line_id)
    && typeof row.allocation_id === "string" && UUID.test(row.allocation_id)
    && typeof row.source_stock_account_id === "string" && UUID.test(row.source_stock_account_id)
    && typeof row.revision_id === "string" && UUID.test(row.revision_id)
    && Number.isSafeInteger(row.revision_no) && (row.revision_no as number) > 0
    && validAxes(row.state_axes)
    && Array.isArray(row.serial_ids) && row.serial_ids.length <= 1000
    && row.serial_ids.every((serialId) => typeof serialId === "string" && UUID.test(serialId))
    && new Set(row.serial_ids.map((serialId) => serialId.toLowerCase())).size === row.serial_ids.length
    && (row.serial_ids.length === 0 || row.reserved_qty === `${row.serial_ids.length}.000`)
    && row.reserved_qty !== "0.000"
    && typeof row.reserved_qty === "string" && QUANTITY.test(row.reserved_qty)
    && Number.isSafeInteger(row.authorization_version) && (row.authorization_version as number) > 0
    && Number.isSafeInteger(row.request_version) && (row.request_version as number) >= 0
    && Number.isSafeInteger(row.source_balance_version) && (row.source_balance_version as number) >= 0
    && Number.isSafeInteger(row.source_ledger_cursor) && (row.source_ledger_cursor as number) >= 0;
}

function session(): StorageLike | undefined {
  try {
    return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
  } catch {
    return undefined;
  }
}

export function createReservationRecoveryStore(
  storage: StorageLike | undefined = session(),
): ReservationRecoveryStore {
  function read(): ReservationSentinelRead {
    if (!storage) return { kind: "unavailable" };
    let raw: string | null;
    try {
      raw = storage.getItem(KEY);
    } catch {
      return { kind: "unavailable" };
    }
    if (raw === null) return { kind: "missing" };
    try {
      const parsed: unknown = JSON.parse(raw);
      return valid(parsed)
        ? { kind: "valid", value: Object.freeze(parsed) }
        : { kind: "corrupt" };
    } catch {
      return { kind: "corrupt" };
    }
  }

  return Object.freeze({
    read,
    persist(value: ReservationSentinel) {
      if (!valid(value)) throw new Error("预留恢复记录无效，已停止写入");
      const existing = read();
      if (existing.kind === "valid") {
        if (JSON.stringify(existing.value) === JSON.stringify(value)) return;
        throw new Error("已有预留操作待核验，禁止覆盖原请求坐标");
      }
      if (existing.kind !== "missing" || !storage) {
        throw new Error("预留恢复存储不可用，已停止写入");
      }
      storage.setItem(KEY, JSON.stringify(value));
      const reread = read();
      if (reread.kind !== "valid" || JSON.stringify(reread.value) !== JSON.stringify(value)) {
        throw new Error("预留恢复记录写后核验失败");
      }
    },
    clear(trace: string) {
      const existing = read();
      if (!storage || existing.kind !== "valid" || existing.value.x_request_id !== trace) {
        throw new Error("预留恢复坐标不匹配，禁止清理");
      }
      storage.removeItem(KEY);
      if (read().kind !== "missing") throw new Error("预留恢复记录清理失败");
    },
  });
}

export function axesExceptReservationMatch(
  before: MaterialRequestDetail["states"],
  after: MaterialRequestDetail["states"],
): boolean {
  return Object.keys(before)
    .filter((key) => key !== "reservation_status")
    .every((key) => before[key as keyof typeof before] === after[key as keyof typeof after]);
}

export async function recoverReservationCommand(
  adapter: FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
    "reservationCommandStatusNoReplay" | "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay">>,
  store: ReservationRecoveryStore,
  sentinel: ReservationSentinel,
  canCommit: () => boolean = () => true,
): Promise<Readonly<{ command: MaterialRequestReservationCommand; detail: MaterialRequestDetail }>> {
  if (!valid(sentinel)) throw new Error("预留恢复记录无效");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  if (identity.person_id !== sentinel.person_id
      || identity.authorization_version !== sentinel.authorization_version) {
    throw new Error("登录身份或权限已变化，原预留操作继续保持待核验");
  }
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (!access.can_read || !access.can_read_allocation_options
      || access.person_id !== sentinel.person_id
      || access.authorization_version !== sentinel.authorization_version) {
    throw new Error("当前权限无法核验原预留操作");
  }
  const status = validateMaterialRequestReservationCommandStatus(
    await adapter.reservationCommandStatusNoReplay(sentinel.x_request_id),
  );
  if (status.lookup_status !== "confirmed" || !status.command) {
    throw new Error("暂未查到预留操作的确定结果，继续保留原请求坐标；请稍后核验");
  }
  const command = status.command;
  if (command.request_id !== sentinel.request_id
      || command.request_line_id !== sentinel.request_line_id
      || command.allocation_id !== sentinel.allocation_id
      || command.request_version !== sentinel.request_version + 1
      || (command.current_request_version !== null
        && command.current_request_version < command.request_version)
      || command.source_stock_account_id !== sentinel.source_stock_account_id
      || command.reserved_qty !== sentinel.reserved_qty
      || command.revision_id !== sentinel.revision_id || command.revision_no !== sentinel.revision_no
      || command.source_balance_version !== sentinel.source_balance_version
      || command.source_ledger_cursor !== sentinel.source_ledger_cursor
      || !reservationSnapshotsMatch([...command.serial_ids].sort(), [...sentinel.serial_ids].sort())
      || !axesExceptReservationMatch(sentinel.state_axes, command.state_axes)) {
    throw new Error("预留命令与原请求锚点不一致，继续保持待核验");
  }
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(command.request_id));
  const line = detail.lines.find((item) => item.request_line_id === command.request_line_id);
  if (!line || line.revision_id !== command.revision_id || line.revision_no !== command.revision_no
      || detail.request_id !== command.request_id
      || detail.request_version < (command.current_request_version ?? command.request_version)
      || detail.current_revision_id !== command.revision_id
      || detail.current_revision_no !== command.revision_no
      || !axesExceptReservationMatch(detail.states, command.state_axes)
      // The command stores the historical axes, while current_request_version is
      // only a lower bound for the fresh read. Later reservations may complete
      // a partial reservation without changing the original confirmed fact.
      || detail.states.reservation_status === "not_reserved"
      || (command.state_axes.reservation_status === "reserved"
        && detail.states.reservation_status === "pending")
      || (detail.request_version === command.request_version
        && !reservationSnapshotsMatch(detail.states, command.state_axes))) {
    throw new Error("预留命令已登记，但当前需求回读未能建立一致关系，继续保持待核验");
  }
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (afterIdentity.person_id !== sentinel.person_id
      || afterIdentity.authorization_version !== sentinel.authorization_version
      || !afterAccess.can_read
      || !afterAccess.can_read_allocation_options
      || afterAccess.person_id !== sentinel.person_id
      || afterAccess.authorization_version !== sentinel.authorization_version
      || !reservationSnapshotsMatch(access, afterAccess)
      || !canCommit()) {
    throw new Error("核验页面或权限已变化，继续保留原预留坐标");
  }
  const current = store.read();
  if (current.kind !== "valid" || !reservationSnapshotsMatch(current.value, sentinel)) throw new Error("预留原请求坐标已变化，禁止清理");
  store.clear(sentinel.x_request_id);
  return { command, detail };
}

