import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type LogisticsEventInput, type LogisticsEventResult, validateLogisticsEventInput, validateLogisticsEventResult } from "./materialRequestShipment";

const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
export type LogisticsSentinel = Readonly<{
  v: 1; kind: "logistics-event"; trace: string; key: string; person_id: string;
  authorization_version: number; request_id: string; shipment_id: string; input: LogisticsEventInput;
}>;
export type LogisticsCommandStatus = Readonly<{
  schema_version: "1.0"; lookup_status: "confirmed" | "not_observed"; command: LogisticsEventResult | null;
}>;
function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || !same(Object.keys(value).sort(), [...keys].sort())) throw new Error("物流核验记录字段无效");
  return value as Record<string, unknown>;
}
function timestamp(value: string): number {
  const time = Date.parse(value);
  if (!/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(time)) throw new Error("物流事件时间必须是有效的带时区时间");
  return time;
}
export function validateLogisticsSentinel(value: unknown): LogisticsSentinel {
  const r = exact(value, ["v", "kind", "trace", "key", "person_id", "authorization_version", "request_id", "shipment_id", "input"]);
  if (r.v !== 1 || r.kind !== "logistics-event" || typeof r.trace !== "string" || !COORDINATE.test(r.trace)
      || typeof r.key !== "string" || !COORDINATE.test(r.key) || typeof r.person_id !== "string" || !UUID.test(r.person_id)
      || typeof r.request_id !== "string" || !UUID.test(r.request_id) || typeof r.shipment_id !== "string" || !UUID.test(r.shipment_id)
      || !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version) < 1) throw new Error("原物流事件坐标无效");
  const input = validateLogisticsEventInput(r.input); timestamp(input.event_at);
  return { v: 1, kind: "logistics-event", trace: r.trace, key: r.key, person_id: r.person_id,
    authorization_version: r.authorization_version as number, request_id: r.request_id, shipment_id: r.shipment_id, input };
}
export function validateLogisticsCommandStatus(value: unknown): LogisticsCommandStatus {
  const r = exact(value, ["schema_version", "lookup_status", "command"]);
  if (r.schema_version !== "1.0") throw new Error("物流核验版本无效");
  if (r.lookup_status === "not_observed" && r.command === null) return { schema_version: "1.0", lookup_status: "not_observed", command: null };
  if (r.lookup_status !== "confirmed") throw new Error("物流核验状态无效");
  const command = validateLogisticsEventResult(r.command); timestamp(command.event_at);
  if (!command.idempotency_replayed) throw new Error("物流核验缺少原命令标记");
  return { schema_version: "1.0", lookup_status: "confirmed", command };
}
type Read = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: LogisticsSentinel };
export type LogisticsStore = Readonly<{ read: () => Read; persist: (value: LogisticsSentinel) => void; clear: (trace: string) => void }>;
export function createLogisticsStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): LogisticsStore {
  const key = "cloud-oam-material-request-logistics-v1", target = () => storage ?? window.localStorage;
  let fault = false;
  const read = (): Read => {
    if (fault) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(key); } catch { fault = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateLogisticsSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  };
  return { read, persist(value) {
    const checked = validateLogisticsSentinel(value);
    if (read().kind !== "missing") throw new Error("已有待核验物流事件，禁止覆盖原请求");
    try { target().setItem(key, JSON.stringify(checked)); } catch { fault = true; throw new Error("无法保存物流核验记录，提交已停止"); }
    const stored = read();
    if (stored.kind !== "valid" || !same(stored.value, checked)) { fault = true; throw new Error("物流核验记录未可靠保存，提交已停止"); }
  }, clear(trace) {
    const current = read();
    if (current.kind !== "valid" || current.value.trace !== trace) throw new Error("原物流坐标已变化，禁止清理");
    try { target().removeItem(key); } catch { fault = true; throw new Error("物流核验记录清理失败"); }
    if (read().kind !== "missing") { fault = true; throw new Error("物流核验记录清理失败"); }
  } };
}
export function matchLogisticsResult(s: LogisticsSentinel, event: LogisticsEventResult): void {
  const lower = (value: string | null) => value?.toLowerCase() ?? null;
  if (lower(event.shipment_id) !== s.shipment_id || event.event_type !== s.input.event_type || event.source !== s.input.source
      || lower(event.evidence_file_id) !== lower(s.input.evidence_file_id) || event.external_ref !== s.input.external_ref
      || timestamp(event.event_at) !== timestamp(s.input.event_at)) throw new Error("物流事件与原包裹、时间、来源或证据不一致，保留原请求");
}
type RecoveryAdapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
  "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay" | "logisticsCommandStatusNoReplay">>;
export function hasLogisticsRecovery(a: FormalMaterialRequestAdapter): a is RecoveryAdapter {
  return [a.loadIdentityNoReplay, a.loadAccessNoReplay, a.detailNoReplay, a.logisticsCommandStatusNoReplay].every(x => typeof x === "function");
}
export async function recoverLogistics(adapter: RecoveryAdapter, store: LogisticsStore, original: LogisticsSentinel,
  canCommit: () => boolean = () => true, expectedResult?: LogisticsEventResult) {
  const sentinel = validateLogisticsSentinel(original);
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== sentinel.person_id || identity.authorization_version !== sentinel.authorization_version
      || access.person_id !== sentinel.person_id || access.authorization_version !== sentinel.authorization_version || !access.can_read) throw new Error("物流事件身份或权限已变化，保留原请求");
  const status = validateLogisticsCommandStatus(await adapter.logisticsCommandStatusNoReplay(sentinel.request_id, sentinel.shipment_id, sentinel.key));
  if (!status.command) throw new Error("尚未观察到原物流事件，继续保留请求；不能据此再次提交");
  matchLogisticsResult(sentinel, status.command);
  if (expectedResult && status.command.event_id !== expectedResult.event_id) throw new Error("物流写入和回读不是同一个事件，保留原请求");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(sentinel.request_id), sentinel.request_id);
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (!same(identity, afterIdentity) || !same(access, afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留原请求");
  const stored = store.read();
  if (stored.kind !== "valid" || !same(stored.value, sentinel)) throw new Error("原物流记录已变化，禁止清理");
  store.clear(sentinel.trace);
  return { event: status.command, detail };
}
