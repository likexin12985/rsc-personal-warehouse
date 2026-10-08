import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestFreshIdentity, validateFormalMaterialRequestAccess } from "./formalMaterialRequestAdapter";
import { closureObject, closureId, closureVersion } from "./materialRequestClosure";
import { type ReturnCompensationInput, validateReturnCompensationInput, returnCompensationFingerprint,
  validateReturnCompensation, validateReturnCompensationCandidates, validateReturnCompensationStatus } from "./materialRequestReturnCompensation";
import { validateVersionedMaterialRequestRemainder } from "./materialRequestRemainder";
export type ReturnCompensationSentinel = Readonly<{ v: 1; trace: string; key: string; person_id: string; authorization_version: number;
  request_id: string; input: ReturnCompensationInput; fingerprint: string }>;
export function validateReturnCompensationSentinel(value: unknown): ReturnCompensationSentinel {
  const row = closureObject(value, ["v", "trace", "key", "person_id", "authorization_version", "request_id", "input", "fingerprint"]);
  if (row.v !== 1 || typeof row.trace !== "string" || !/^[A-Za-z0-9._:-]{8,160}$/.test(row.trace)
      || typeof row.key !== "string" || !/^[A-Za-z0-9._:-]{16,128}$/.test(row.key)
      || typeof row.fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(row.fingerprint)) throw new Error("原补偿请求坐标无效，请保留浏览器记录");
  closureId(row.person_id); closureId(row.request_id); closureVersion(row.authorization_version); validateReturnCompensationInput(row.input);
  return row as ReturnCompensationSentinel;
}
type Stored = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: ReturnCompensationSentinel };
export type ReturnCompensationStore = Readonly<{ read(): Stored; persist(value: ReturnCompensationSentinel): void; clear(trace: string): void }>;
export function createReturnCompensationStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): ReturnCompensationStore {
  const name = "cloud-oam-material-request-return-compensation-v1", target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): Stored {
    if (unavailable) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(name); } catch { unavailable = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateReturnCompensationSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  }
  return { read, persist(value) {
    const checked = validateReturnCompensationSentinel(value);
    if (read().kind !== "missing") throw new Error("已有补偿请求待核验，禁止覆盖");
    try { target().setItem(name, JSON.stringify(checked)); } catch { unavailable = true; throw new Error("无法保存原补偿请求，已停止提交"); }
    const saved = read();
    if (saved.kind !== "valid" || JSON.stringify(saved.value) !== JSON.stringify(checked)) { unavailable = true; throw new Error("原补偿请求未可靠保存，已停止提交"); }
  }, clear(trace) {
    const saved = read();
    if (saved.kind !== "valid" || saved.value.trace !== trace) throw new Error("原补偿请求已变化，禁止清理");
    try { target().removeItem(name); } catch { unavailable = true; throw new Error("原补偿请求未能清理"); }
    if (read().kind !== "missing") throw new Error("原补偿请求未能清理");
  } };
}
type Adapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
  "returnCompensationCandidates" | "returnCompensationStatusNoReplay" | "detailNoReplay" | "remainingFulfillment" | "loadIdentityNoReplay" | "loadAccessNoReplay">>;
export function hasReturnCompensationRecovery(adapter: FormalMaterialRequestAdapter): adapter is Adapter {
  return [adapter.returnCompensationCandidates, adapter.returnCompensationStatusNoReplay, adapter.detailNoReplay,
    adapter.remainingFulfillment, adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay].every(fn => typeof fn === "function");
}
export async function recoverReturnCompensation(adapter: Adapter, store: ReturnCompensationStore, original: ReturnCompensationSentinel, canCommit = () => true) {
  const saved = validateReturnCompensationSentinel(original);
  if (await returnCompensationFingerprint(saved.input) !== saved.fingerprint) throw new Error("原补偿内容与指纹不一致，保留请求");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== saved.person_id || access.person_id !== identity.person_id
      || access.authorization_version !== identity.authorization_version || !access.can_read) throw new Error("当前身份无法核验原补偿请求");
  const status = validateReturnCompensationStatus(await adapter.returnCompensationStatusNoReplay(saved.request_id, saved.key, saved.fingerprint));
  if (status.lookup_status !== "confirmed" || !status.command) throw new Error("尚未查到原补偿结果，保留请求；不要重复提交");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(saved.request_id), saved.request_id);
  const fact = validateReturnCompensation(status.command, detail);
  if (fact.inbound_id !== saved.input.inbound_id || fact.request_version !== saved.input.expected_request_version
      || fact.cancelled_qty !== saved.input.cancelled_qty) throw new Error("原补偿数量或来源不一致，保留请求");
  const candidates = validateReturnCompensationCandidates(await adapter.returnCompensationCandidates(saved.request_id), detail);
  const source = candidates.items.find(row => row.inbound_id === fact.inbound_id);
  if (!source?.compensation || source.compensation.compensation_id !== fact.compensation_id
      || source.compensation.evidence_sha256 !== fact.evidence_sha256 || source.compensation.request_hash !== fact.request_hash
      || source.inbound_request_hash !== saved.input.inbound_request_hash || source.inbound_plan_hash !== saved.input.inbound_plan_hash) throw new Error("当前退回入账与原补偿不一致");
  const partition = validateVersionedMaterialRequestRemainder(await adapter.remainingFulfillment(saved.request_id), detail);
  const line = partition.lines.find(row => row.request_line_id === fact.request_line_id);
  if (partition.schema_version !== "2.0" || !line || !("return_compensated_qty" in line)
      || BigInt(String(line.return_compensated_qty).replace(".", "")) < BigInt(fact.cancelled_qty.replace(".", ""))) throw new Error("退回补偿分区未核验，保留请求");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (JSON.stringify(identity) !== JSON.stringify(afterIdentity) || JSON.stringify(access) !== JSON.stringify(afterAccess) || !canCommit()) throw new Error("核验期间身份或页面变化，保留请求");
  const current = store.read();
  if (current.kind !== "valid" || JSON.stringify(current.value) !== JSON.stringify(saved)) throw new Error("原补偿记录已变化，禁止清理");
  store.clear(saved.trace);
  return { detail, candidates };
}
