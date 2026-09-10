import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type ShipmentInput, type ShipmentResult, shipmentQuantity, validateShipmentInput, validateShipmentResult } from "./materialRequestShipment";

export type ShipmentCommandStatus = Readonly<{ schema_version: "1.0"; lookup_status: "confirmed" | "not_observed"; request_hash: string | null; command: ShipmentResult | null }>;
export function validateShipmentCommandStatus(value: unknown): ShipmentCommandStatus {
  const row = exact(value, ["schema_version", "lookup_status", "request_hash", "command"]);
  if (row.schema_version !== "1.0") throw new Error("发运核验响应版本无效");
  if (row.lookup_status === "not_observed" && row.request_hash === null && row.command === null) return { schema_version: "1.0", lookup_status: "not_observed", request_hash: null, command: null };
  if (row.lookup_status !== "confirmed" || typeof row.request_hash !== "string" || !/^[a-f0-9]{64}$/.test(row.request_hash)) throw new Error("发运核验响应无效");
  const command = validateShipmentResult(row.command);
  if (!command.idempotency_replayed) throw new Error("发运历史标记无效");
  return { schema_version: "1.0", lookup_status: "confirmed", request_hash: row.request_hash, command };
}
function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || !same(Object.keys(value).sort(), [...keys].sort())) throw new Error("发运核验记录字段无效");
  return value as Record<string, unknown>;
}
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const COORDINATE = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
export type ShipmentSentinel = Readonly<{ v: 1; kind: "shipment"; trace: string; key: string; person_id: string; authorization_version: number; request_id: string; input: ShipmentInput }>;
export function validateShipmentSentinel(value: unknown): ShipmentSentinel {
  const r = exact(value, ["v", "kind", "trace", "key", "person_id", "authorization_version", "request_id", "input"]);
  if (r.v !== 1 || r.kind !== "shipment" || typeof r.trace !== "string" || !COORDINATE.test(r.trace) || typeof r.key !== "string" || !COORDINATE.test(r.key)
      || typeof r.person_id !== "string" || !UUID.test(r.person_id) || typeof r.request_id !== "string" || !UUID.test(r.request_id)
      || !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version) < 1) throw new Error("原发运请求坐标无效");
  return { v: 1, kind: "shipment", trace: r.trace, key: r.key, person_id: r.person_id, authorization_version: r.authorization_version as number, request_id: r.request_id, input: validateShipmentInput(r.input) };
}
type Read = { kind: "missing" } | { kind: "corrupt" } | { kind: "unavailable" } | { kind: "valid"; value: ShipmentSentinel };
export type ShipmentStore = Readonly<{ read: () => Read; persist: (value: ShipmentSentinel) => void; clear: (trace: string) => void }>;
export function createShipmentStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): ShipmentStore {
  const key = "cloud-oam-material-request-shipment-v1", target = () => storage ?? window.localStorage;
  let fault = false;
  const read = (): Read => {
    if (fault) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(key); } catch { fault = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateShipmentSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  };
  return { read, persist(value) {
    const checked = validateShipmentSentinel(value);
    if (read().kind !== "missing") throw new Error("已有待核验发运，禁止覆盖原请求");
    try { target().setItem(key, JSON.stringify(checked)); } catch { fault = true; throw new Error("无法保存发运核验记录，提交已停止"); }
    const saved = read();
    if (saved.kind !== "valid" || !same(saved.value, checked)) { fault = true; throw new Error("发运核验记录未可靠保存，提交已停止"); }
  }, clear(trace) {
    const current = read();
    if (current.kind !== "valid" || current.value.trace !== trace) throw new Error("原发运坐标已变化，禁止清理");
    try { target().removeItem(key); } catch { fault = true; throw new Error("发运核验记录清理失败"); }
    if (read().kind !== "missing") { fault = true; throw new Error("发运核验记录清理失败"); }
  } };
}

// Matches Python json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=True).
export async function shipmentRequestHash(requestId: string, input: ShipmentInput): Promise<string> {
  const checked = validateShipmentInput(input);
  const payload = { request_id: requestId.toLowerCase(), expected_version: checked.expected_request_version,
    target_location_id: checked.target_location_id.toLowerCase(), target_person_id: checked.target_person_id?.toLowerCase() ?? null,
    carrier: checked.carrier, tracking_no: checked.tracking_no, shipped_at: checked.shipped_at,
    lines: checked.lines.map(line => ({ posting_id: line.outbound_posting_id.toLowerCase(), qty: shipmentQuantity(line.shipped_qty), serial_ids: line.serial_ids.map(s => s.toLowerCase()).sort() })) };
  const ordered = (value: unknown): unknown => Array.isArray(value) ? value.map(ordered)
    : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, item]) => [key, ordered(item)])) : value;
  const encoded = JSON.stringify(ordered(payload)).replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(encoded));
  return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, "0")).join("");
}
export function matchShipmentResult(original: ShipmentSentinel, result: ShipmentResult): void {
  const input = original.input, lower = (v: string | null) => v?.toLowerCase() ?? null;
  const lines = (rows: ShipmentInput["lines"]) => rows.map(line => ({ outbound_posting_id: lower(line.outbound_posting_id), shipped_qty: line.shipped_qty, serial_ids: line.serial_ids.map(s => s.toLowerCase()).sort() })).sort((a, b) => a.outbound_posting_id!.localeCompare(b.outbound_posting_id!));
  if (lower(result.request_id) !== original.request_id || lower(result.target_location_id) !== lower(input.target_location_id)
      || lower(result.target_person_id) !== lower(input.target_person_id) || result.carrier !== input.carrier || result.tracking_no !== input.tracking_no
      || !Number.isFinite(Date.parse(result.shipped_at)) || Date.parse(result.shipped_at) !== Date.parse(input.shipped_at)
      || !same(lines(result.lines), lines(input.lines))) throw new Error("发运结果与原需求、目标、数量或 SN 不一致，保留原请求核验");
}
type RecoveryAdapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter, "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay" | "shipmentCommandStatusNoReplay">>;
export function hasShipmentRecovery(adapter: FormalMaterialRequestAdapter): adapter is RecoveryAdapter {
  return [adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay, adapter.detailNoReplay, adapter.shipmentCommandStatusNoReplay].every(f => typeof f === "function");
}
export async function recoverShipment(adapter: RecoveryAdapter, store: ShipmentStore, original: ShipmentSentinel, canCommit: () => boolean = () => true, expectedResult?: ShipmentResult) {
  const sentinel = validateShipmentSentinel(original);
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== sentinel.person_id || identity.authorization_version !== sentinel.authorization_version
      || access.person_id !== sentinel.person_id || access.authorization_version !== sentinel.authorization_version || !access.can_read || !access.can_read_allocation_options) throw new Error("发运身份或权限已变化，保留原请求");
  const status = validateShipmentCommandStatus(await adapter.shipmentCommandStatusNoReplay(sentinel.request_id, sentinel.key));
  if (!status.command || status.lookup_status !== "confirmed") throw new Error("尚未观察到原发运结果，继续保留请求；不能据此再次提交");
  if (status.request_hash !== await shipmentRequestHash(sentinel.request_id, sentinel.input)) throw new Error("原发运请求内容指纹不一致，暂停处理");
  matchShipmentResult(sentinel, status.command);
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(sentinel.request_id), sentinel.request_id);
  if (detail.request_version < sentinel.input.expected_request_version) throw new Error("需求回读版本落后，保留原请求");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (!same(identity, afterIdentity) || !same(access, afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留原请求");
  const stored = store.read();
  if (stored.kind !== "valid" || !same(stored.value, sentinel)) throw new Error("原发运记录已变化，禁止清理");
  if (expectedResult && (status.command.shipment_id !== expectedResult.shipment_id || status.command.shipment_no !== expectedResult.shipment_no
      || !same([...status.command.lines].sort((a,b) => a.shipment_line_id.localeCompare(b.shipment_line_id)), [...expectedResult.lines].sort((a,b) => a.shipment_line_id.localeCompare(b.shipment_line_id))))) throw new Error("发运写入与回读包裹绑定不一致，保留原请求");
  store.clear(sentinel.trace);
  return { command: status.command, detail };
}
