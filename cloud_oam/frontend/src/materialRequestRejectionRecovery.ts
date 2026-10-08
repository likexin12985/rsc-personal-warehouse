import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestFreshIdentity, validateFormalMaterialRequestAccess } from "./formalMaterialRequestAdapter";
import { closureObject, closureId, closureVersion } from "./materialRequestClosure";
import { type RejectionCommand, type RejectionPage, type RejectionHistory, rejectionCommand, rejectionFingerprint,
  rejectionPage, rejectionResult, progressResult, rejectionStatus } from "./materialRequestRejection";
import { canonical, demand, units } from "./myFulfillmentContract";
export type RejectionSentinel = Readonly<{ v: 1; trace: string; key: string; person_id: string; authorization_version: number;
  request_id: string; command: RejectionCommand; fingerprint: string }>;
export function validateRejectionSentinel(value: unknown): RejectionSentinel {
  const row = closureObject(value, ["v", "trace", "key", "person_id", "authorization_version", "request_id", "command", "fingerprint"]);
  if (row.v !== 1 || typeof row.trace !== "string" || !/^[A-Za-z0-9._:-]{8,160}$/.test(row.trace)
      || typeof row.key !== "string" || !/^[A-Za-z0-9._:-]{16,128}$/.test(row.key)
      || typeof row.fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(row.fingerprint)) throw new Error("原退回请求坐标无效，请保留浏览器记录");
  closureId(row.person_id); closureId(row.request_id); closureVersion(row.authorization_version); rejectionCommand(row.command as RejectionCommand);
  return row as RejectionSentinel;
}
type Stored = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: RejectionSentinel };
export type RejectionStore = Readonly<{ read(): Stored; persist(value: RejectionSentinel): void; clear(trace: string): void }>;
export function createRejectionStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): RejectionStore {
  const name = "cloud-oam-material-request-rejection-return-v1", target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): Stored {
    if (unavailable) return { kind: "unavailable" };
    let raw: string | null;
    try { raw = target().getItem(name); } catch { unavailable = true; return { kind: "unavailable" }; }
    if (raw === null) return { kind: "missing" };
    try { return { kind: "valid", value: validateRejectionSentinel(JSON.parse(raw)) }; } catch { return { kind: "corrupt" }; }
  }
  return { read, persist(value) {
    const checked = validateRejectionSentinel(value);
    if (read().kind !== "missing") throw new Error("已有退回请求待核验，禁止覆盖");
    try { target().setItem(name, JSON.stringify(checked)); } catch { unavailable = true; throw new Error("无法保存原退回请求，已停止提交"); }
    const saved = read();
    if (saved.kind !== "valid" || JSON.stringify(saved.value) !== JSON.stringify(checked)) { unavailable = true; throw new Error("原退回请求未可靠保存，已停止提交"); }
  }, clear(trace) {
    const saved = read();
    if (saved.kind !== "valid" || saved.value.trace !== trace) throw new Error("原退回请求已变化，禁止清理");
    try { target().removeItem(name); } catch { unavailable = true; throw new Error("原退回请求未能清理"); }
    if (read().kind !== "missing") throw new Error("原退回请求未能清理");
  } };
}
type Adapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
  "rejectionCandidates" | "rejectionStatusNoReplay" | "detailNoReplay" | "loadIdentityNoReplay" | "loadAccessNoReplay">>;
export function hasRejectionRecovery(adapter: FormalMaterialRequestAdapter): adapter is Adapter {
  return [adapter.rejectionCandidates, adapter.rejectionStatusNoReplay, adapter.detailNoReplay,
    adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay].every(fn => typeof fn === "function");
}
export async function recoverRejection(adapter: Adapter, store: RejectionStore, original: RejectionSentinel, canCommit = () => true) {
  const saved = validateRejectionSentinel(original), command = rejectionCommand(saved.command);
  demand(await rejectionFingerprint(command) === saved.fingerprint, "原退回输入与指纹不一致，保留请求");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  demand(identity.person_id === saved.person_id && access.person_id === identity.person_id && access.authorization_version === identity.authorization_version && access.can_read,
    "当前身份无法核验原退回请求");
  const returnId = command.kind === "progress" ? command.return_id : null;
  const status = rejectionStatus(await adapter.rejectionStatusNoReplay(saved.request_id, returnId, saved.key, saved.fingerprint), saved.request_id, returnId);
  demand(status.lookup_status === "confirmed" && status.command, "尚未查到原退回结果，保留请求，请勿重复提交");
  const fact = command.kind === "register" ? rejectionResult(status.command, saved.request_id) : progressResult(status.command, saved.request_id, command.return_id);
  demand(fact.request_version === command.input.expected_request_version, "原退回版本不一致");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(saved.request_id), saved.request_id);
  demand(detail.request_version >= fact.request_version);
  let found: RejectionHistory | undefined, after: string | null = null, firstPage: RejectionPage | undefined;
  for (let i = 0; i < 200; i += 1) {
    const page = rejectionPage(await adapter.rejectionCandidates(saved.request_id, after), saved.request_id, after);
    demand(page.request_version === detail.request_version, "退回来源版本已变化"); firstPage ??= page;
    found = page.items.flatMap(s => s.lines.flatMap(l => l.registrations)).find(h => h.registration.return_id === fact.return_id);
    if (found || page.next_after_id === null) break;
    after = page.next_after_id;
  }
  demand(found && firstPage, "未在当前原拒收来源中核验到本次退回，保留请求");
  if (command.kind === "register") {
    const r = rejectionResult(fact, saved.request_id);
    demand(r.receipt_id === command.input.receipt_id && r.receipt_line_id === command.input.receipt_line_id && r.receipt_request_hash === command.input.receipt_request_hash
      && units(r.quantity) === units(command.input.quantity) && canonical(r.serial_ids) === canonical(command.input.serial_ids));
    demand(canonical({ ...r, replayed: true }) === canonical(found.registration), "原退回登记与来源历史不一致");
  } else {
    const e = progressResult(fact, saved.request_id, command.return_id);
    const actual = found.progress.events.find(event => event.event_id === e.event_id);
    demand(actual && canonical({ ...e, replayed: true }) === canonical(actual), "原退回进展与来源历史不一致");
    const { event_id: _e, return_id: _r, request_id: _q, request_version: v, recorded_at: _t, request_hash: _h, replayed: _p, ...input } = e;
    demand(canonical({ ...input, expected_request_version: v }) === canonical(command.input), "退回进展内容不一致");
  }
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  demand(canonical(identity) === canonical(afterIdentity) && canonical(access) === canonical(afterAccess) && canCommit(), "核验期间身份或页面变化，保留原请求");
  const current = store.read();
  demand(current.kind === "valid" && canonical(current.value) === canonical(saved), "原退回记录变化，禁止清理");
  store.clear(saved.trace); return { detail, page: firstPage };
}
