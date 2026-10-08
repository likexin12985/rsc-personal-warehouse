import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestFreshIdentity, validateFormalMaterialRequestAccess } from "./formalMaterialRequestAdapter";
import { closureObject, closureId, closureVersion, closeInputFingerprint, validateCloseInput, validateClosureResult, validateClosureState, validateClosureStatus, type CloseInput } from "./materialRequestClosure";
export type ClosureSentinel = Readonly<{ v: 1; trace: string; key: string; person_id: string; authorization_version: number;
  request_id: string; input: CloseInput; fingerprint: string }>;
export function validateClosureSentinel(value: unknown): ClosureSentinel {
  const row = closureObject(value, ["v", "trace", "key", "person_id", "authorization_version", "request_id", "input", "fingerprint"]);
  if (row.v !== 1 || typeof row.trace !== "string" || !/^[A-Za-z0-9._:-]{8,160}$/.test(row.trace)
      || typeof row.key !== "string" || !/^[A-Za-z0-9._:-]{16,128}$/.test(row.key)
      || typeof row.fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(row.fingerprint)) throw new Error("原关闭请求坐标无效，请保留浏览器记录");
  closureId(row.person_id); closureId(row.request_id); closureVersion(row.authorization_version); validateCloseInput(row.input);
  return row as ClosureSentinel;
}
type Stored = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: ClosureSentinel };
export type ClosureStore = Readonly<{ read(): Stored; persist(value: ClosureSentinel): void; clear(trace: string): void }>;
export function createClosureStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): ClosureStore {
  const name = "cloud-oam-material-request-closure-v1", target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): Stored {
    if (unavailable) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(name); } catch { unavailable = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateClosureSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  }
  return { read, persist(value) {
    const checked = validateClosureSentinel(value);
    if (read().kind !== "missing") throw new Error("已有关闭请求待核验，禁止覆盖");
    try { target().setItem(name, JSON.stringify(checked)); } catch { unavailable = true; throw new Error("无法保存原关闭请求，已停止提交"); }
    const saved = read();
    if (saved.kind !== "valid" || JSON.stringify(saved.value) !== JSON.stringify(checked)) { unavailable = true; throw new Error("原关闭请求未可靠保存，已停止提交"); }
  }, clear(trace) {
    const saved = read();
    if (saved.kind !== "valid" || saved.value.trace !== trace) throw new Error("原关闭请求已变化，禁止清理");
    try { target().removeItem(name); } catch { unavailable = true; throw new Error("原关闭请求未能清理"); }
    if (read().kind !== "missing") throw new Error("原关闭请求未能清理");
  } };
}
type Adapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
  "closureCommandStatusNoReplay" | "closureState" | "detailNoReplay" | "loadIdentityNoReplay" | "loadAccessNoReplay">>;
export function hasClosureRecovery(adapter: FormalMaterialRequestAdapter): adapter is Adapter {
  return [adapter.closureCommandStatusNoReplay, adapter.closureState, adapter.detailNoReplay, adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay].every(fn => typeof fn === "function");
}
export async function recoverClosure(adapter: Adapter, store: ClosureStore, original: ClosureSentinel, canCommit = () => true) {
  const saved = validateClosureSentinel(original);
  if (await closeInputFingerprint(saved.input) !== saved.fingerprint) throw new Error("原关闭内容与指纹不一致，保留请求");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== saved.person_id || access.person_id !== identity.person_id
      || access.authorization_version !== identity.authorization_version || !access.can_read) throw new Error("当前身份或读取权限无法核验原关闭请求");
  const status = validateClosureStatus(await adapter.closureCommandStatusNoReplay(saved.request_id, saved.key, saved.fingerprint));
  if (status.lookup_status !== "confirmed" || !status.command) throw new Error("尚未查到原关闭结果，保留请求；请稍后核验，不要重复提交");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(saved.request_id), saved.request_id);
  const command = validateClosureResult(status.command, detail);
  if (command.request_id !== saved.request_id || command.request_version !== saved.input.expected_request_version) throw new Error("原关闭结果版本不一致，保留请求");
  const state = validateClosureState(await adapter.closureState(saved.request_id), detail);
  if (!state.closure || state.closure.closure_id !== command.closure_id || state.closure.evidence_sha256 !== command.evidence_sha256) throw new Error("当前关闭记录与原请求不一致");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (JSON.stringify(identity) !== JSON.stringify(afterIdentity) || JSON.stringify(access) !== JSON.stringify(afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留请求");
  const current = store.read();
  if (current.kind !== "valid" || JSON.stringify(current.value) !== JSON.stringify(saved)) throw new Error("原关闭记录已变化，禁止清理");
  store.clear(saved.trace);
  return { state, detail };
}
