import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity, type FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { canonical, commandHash, commandResult, demand, exact, hash, id, inboundInput, receiptInput, version, type PersonalCommand } from "./myFulfillmentContract";
import type { MyFulfillmentAdapter } from "./myFulfillmentAdapter";
export type PersonalPending = { v: 1; request_id: string; person_id: string; authorization_version: number; key: string; trace: string; fingerprint: string; command: PersonalCommand };
export function personalPending(value: unknown): PersonalPending {
  const row = exact<PersonalPending>(value, "v request_id person_id authorization_version key trace fingerprint command"); demand(row.v === 1); id(row.request_id); id(row.person_id); version(row.authorization_version); hash(row.fingerprint); demand(typeof row.key === "string" && /^[A-Za-z0-9._:-]{16,128}$/.test(row.key) && typeof row.trace === "string" && /^[A-Za-z0-9._:-]{8,160}$/.test(row.trace));
  exact(row.command, "kind input"); demand(row.command.kind === "receipt" || row.command.kind === "inbound");
  if (row.command.kind === "receipt") receiptInput(row.command.input); else inboundInput(row.command.input); return row;
}
export type PersonalStored = { kind: "missing" | "corrupt" | "unavailable" } | { kind: "valid"; value: PersonalPending };
export type PersonalStore = { read(): PersonalStored; persist(value: PersonalPending): void; clear(expected: PersonalPending): void };
export function createPersonalStore(storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">): PersonalStore {
  const name = "cloud-oam-personal-fulfillment-v1", target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): PersonalStored {
    if (unavailable) return { kind: "unavailable" }; let value: string | null;
    try { value = target().getItem(name); } catch { unavailable = true; return { kind: "unavailable" }; }
    if (value === null) return { kind: "missing" }; try { return { kind: "valid", value: personalPending(JSON.parse(value)) }; } catch { return { kind: "corrupt" }; }
  }
  return { read, persist(value) { personalPending(value); demand(read().kind === "missing", "已有本人收货或入账待核验，禁止覆盖"); try { target().setItem(name, JSON.stringify(value)); } catch { unavailable = true; throw new Error("原请求无法保存，已停止提交"); } const saved = read(); demand(saved.kind === "valid" && canonical(saved.value) === canonical(value), "原请求未可靠保存，已停止提交"); },
    clear(expected) { const current = read(); demand(current.kind === "valid" && canonical(current.value) === canonical(expected), "原请求已变化，禁止清理"); try { target().removeItem(name); } catch { unavailable = true; throw new Error("原请求未能清理"); } demand(read().kind === "missing", "原请求未能清理"); } };
}
export async function personalAuthority(adapter: FormalMaterialRequestAdapter, personId: string) {
  demand(adapter.loadIdentityNoReplay && adapter.loadAccessNoReplay, "当前无法只读核验登录身份");
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  demand(identity.person_id === personId && access.person_id === personId && identity.authorization_version === access.authorization_version && access.can_read && access.can_read_material_catalog, "身份或本人库存读取权限已变化，请使用原身份核验");
  return { identity, access };
}
export async function recoverPersonal(adapter: FormalMaterialRequestAdapter, personal: MyFulfillmentAdapter, store: PersonalStore, original: PersonalPending, current = () => true) {
  const pending = personalPending(original); demand(await commandHash(pending.request_id, pending.person_id, pending.command) === pending.fingerprint, "原请求内容和指纹不一致");
  const before = await personalAuthority(adapter, pending.person_id);
  const lookup = async (value: unknown) => {
    const row = exact<{ schema_version: string; lookup_status: string; command: unknown }>(value, "schema_version lookup_status command"); demand(row.schema_version === "1.0");
    demand(row.lookup_status === "confirmed" && row.command !== null, "尚未查到原结果，请保留记录稍后只读核验，不要重复提交");
    const result = await commandResult(row.command, pending.request_id, pending.person_id, pending.command, pending.fingerprint); demand(result.idempotency_replayed === true); return result;
  };
  const result = await lookup(await personal.status(pending.request_id, pending.command.kind, pending.key));
  const traced = await lookup(await personal.trace(pending.request_id, pending.command.kind, pending.trace));
  demand(canonical(result) === canonical(traced), "原键和原请求编号的结果不一致");
  demand(adapter.detailNoReplay, "当前无法只读核验需求"); const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(pending.request_id), pending.request_id);
  demand(detail.request_version >= pending.command.input.expected_request_version + 1, "需求版本尚未反映原结果");
  const after = await personalAuthority(adapter, pending.person_id); demand(canonical(before) === canonical(after) && current(), "核验期间身份、权限或页面已变化，请保留原请求");
  store.clear(pending); return { result, detail };
}
