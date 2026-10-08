import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestFreshIdentity, validateFormalMaterialRequestAccess } from "./formalMaterialRequestAdapter";
import { closureObject, closureId, closureVersion } from "./materialRequestClosure";
import { remainingStages, validateVersionedMaterialRequestRemainder } from "./materialRequestRemainder";
import { remainingCancelFingerprint, validateRemainingCancelInput, validateRemainingCancellation, validateRemainingCancellationState, validateRemainingCancellationStatus, type RemainingCancelInput } from "./materialRequestRemainingCancellation";
export type RemainingCancellationSentinel = Readonly<{ v: 1; trace: string; key: string; person_id: string; authorization_version: number;
  request_id: string; input: RemainingCancelInput; fingerprint: string }>;
export function validateRemainingCancellationSentinel(value: unknown): RemainingCancellationSentinel {
  const row = closureObject(value, ["v", "trace", "key", "person_id", "authorization_version", "request_id", "input", "fingerprint"]);
  if (row.v !== 1 || typeof row.trace !== "string" || !/^[A-Za-z0-9._:-]{8,160}$/.test(row.trace)
      || typeof row.key !== "string" || !/^[A-Za-z0-9._:-]{16,128}$/.test(row.key)
      || typeof row.fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(row.fingerprint)) throw new Error("原取消请求坐标无效，请保留浏览器记录");
  closureId(row.person_id); closureId(row.request_id); closureVersion(row.authorization_version); validateRemainingCancelInput(row.input);
  return row as RemainingCancellationSentinel;
}
type Stored = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: RemainingCancellationSentinel };
export type RemainingCancellationStore = Readonly<{ read(): Stored; persist(value: RemainingCancellationSentinel): void; clear(trace: string): void }>;
export function createRemainingCancellationStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): RemainingCancellationStore {
  const name = "cloud-oam-material-request-remaining-cancellation-v1", target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): Stored {
    if (unavailable) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(name); } catch { unavailable = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateRemainingCancellationSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  }
  return { read, persist(value) {
    const checked = validateRemainingCancellationSentinel(value);
    if (read().kind !== "missing") throw new Error("已有取消请求待核验，禁止覆盖");
    try { target().setItem(name, JSON.stringify(checked)); } catch { unavailable = true; throw new Error("无法保存原取消请求，已停止提交"); }
    const saved = read();
    if (saved.kind !== "valid" || JSON.stringify(saved.value) !== JSON.stringify(checked)) { unavailable = true; throw new Error("原取消请求未可靠保存，已停止提交"); }
  }, clear(trace) {
    const saved = read();
    if (saved.kind !== "valid" || saved.value.trace !== trace) throw new Error("原取消请求已变化，禁止清理");
    try { target().removeItem(name); } catch { unavailable = true; throw new Error("原取消请求未能清理"); }
    if (read().kind !== "missing") throw new Error("原取消请求未能清理");
  } };
}
type Adapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
  "remainingCancellationStatusNoReplay" | "remainingCancellationState" | "detailNoReplay" | "loadIdentityNoReplay" | "loadAccessNoReplay">>;
export function hasRemainingCancellationRecovery(adapter: FormalMaterialRequestAdapter): adapter is Adapter {
  return [adapter.remainingCancellationStatusNoReplay, adapter.remainingCancellationState, adapter.detailNoReplay, adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay].every(fn => typeof fn === "function");
}
export async function recoverRemainingCancellation(adapter: Adapter, store: RemainingCancellationStore, original: RemainingCancellationSentinel, canCommit = () => true) {
  const saved = validateRemainingCancellationSentinel(original);
  if (await remainingCancelFingerprint(saved.input) !== saved.fingerprint) throw new Error("原取消内容与指纹不一致，保留请求");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== saved.person_id || access.person_id !== identity.person_id
      || access.authorization_version !== identity.authorization_version || !access.can_read) throw new Error("当前身份或读取权限无法核验原取消请求");
  const status = validateRemainingCancellationStatus(await adapter.remainingCancellationStatusNoReplay(saved.request_id, saved.key, saved.fingerprint));
  if (status.lookup_status !== "confirmed" || !status.command) throw new Error("尚未查到原取消结果，保留请求；请稍后核验，不要重复提交");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(saved.request_id), saved.request_id);
  const command = validateRemainingCancellation(status.command, detail);
  if (command.request_id !== saved.request_id || command.request_version !== saved.input.expected_request_version) throw new Error("原取消结果版本不一致，保留请求");
  const recorded = new Map(command.lines.map(line => [line.request_line_id, line.cancelled_qty]));
  if (saved.input.lines.length !== recorded.size || saved.input.lines.some(line => recorded.get(line.request_line_id) !== line.cancelled_qty)) throw new Error("原取消数量与当前事实不一致，保留请求");
  if (detail.lines.some(line => line.cancelled_qty !== (recorded.get(line.request_line_id) ?? "0.000"))) {
    if (!adapter.remainingFulfillment) throw new Error("缺少退回补偿数量依据，保留请求");
    const quantities = validateVersionedMaterialRequestRemainder(await adapter.remainingFulfillment(saved.request_id), detail);
    if (quantities.schema_version !== "2.0" || quantities.lines.some(line =>
      line.unfulfilled_cancelled_qty !== (recorded.get(line.request_line_id) ?? "0.000")
      || line.returned_pending_compensation_qty !== "0.000"
      || Object.keys(remainingStages).some(key => line[key as keyof typeof remainingStages] !== "0.000"))) {
      throw new Error("原取消与退回补偿数量不一致，保留请求");
    }
  }
  const state = validateRemainingCancellationState(await adapter.remainingCancellationState(saved.request_id), detail);
  if (!state.cancellation || state.cancellation.cancellation_id !== command.cancellation_id || state.cancellation.evidence_sha256 !== command.evidence_sha256) throw new Error("当前取消记录与原请求不一致");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (JSON.stringify(identity) !== JSON.stringify(afterIdentity) || JSON.stringify(access) !== JSON.stringify(afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留请求");
  const current = store.read();
  if (current.kind !== "valid" || JSON.stringify(current.value) !== JSON.stringify(saved)) throw new Error("原取消记录已变化，禁止清理");
  store.clear(saved.trace);
  return { state, detail };
}
