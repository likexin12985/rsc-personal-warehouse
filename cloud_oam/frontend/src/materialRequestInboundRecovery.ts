import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type InboundOrderResult, validateInboundOrderResult } from "./materialRequestShipment";
const UUID=/^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const COORDINATE=/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
export type InboundSentinel=Readonly<{v:1;kind:"inbound-create"|"inbound-post";trace:string;key:string|null;person_id:string;authorization_version:number;request_id:string;expected_version:number;receipt_id:string;target_location_id:string;target_person_id:string;inbound_order_id?:string}>;
function exact(v:unknown,keys:string[]){if(!v||typeof v!=="object"||Array.isArray(v)||JSON.stringify(Object.keys(v).sort())!==JSON.stringify([...keys].sort()))throw new Error("入账核验记录字段无效");return v as Record<string,unknown>}
export function validateInboundSentinel(v:unknown):InboundSentinel{const r=exact(v,["v","kind","trace","key","person_id","authorization_version","request_id","expected_version","receipt_id","target_location_id","target_person_id",...(v&&typeof v==="object"&&"inbound_order_id" in v?["inbound_order_id"]:[])]);if(r.v!==1||(r.kind!=="inbound-create"&&r.kind!=="inbound-post")||typeof r.trace!=="string"||!COORDINATE.test(r.trace)||(r.key!==null&&(typeof r.key!=="string"||!COORDINATE.test(r.key)))||(r.kind==="inbound-create"&&(r.key!==null||"inbound_order_id" in r))||(r.kind==="inbound-post"&&r.key===null)||!Number.isSafeInteger(r.authorization_version)||Number(r.authorization_version)<1||typeof r.person_id!=="string"||!UUID.test(r.person_id)||typeof r.request_id!=="string"||!UUID.test(r.request_id)||!Number.isSafeInteger(r.expected_version)||Number(r.expected_version)<1||typeof r.receipt_id!=="string"||!UUID.test(r.receipt_id)||typeof r.target_location_id!=="string"||!UUID.test(r.target_location_id)||typeof r.target_person_id!=="string"||!UUID.test(r.target_person_id)||(r.kind==="inbound-post"&&(typeof r.inbound_order_id!=="string"||!UUID.test(r.inbound_order_id))))throw new Error("原入账请求坐标无效");return r as InboundSentinel}
type Read={kind:"missing"|"corrupt"|"unavailable"}|{kind:"valid";value:InboundSentinel};
export type InboundStore=Readonly<{read:()=>Read;persist:(v:InboundSentinel)=>void;clear:(trace:string)=>void}>;
export function createInboundStore(storage?:Pick<Storage,"getItem"|"setItem"|"removeItem">):InboundStore{const key="cloud-oam-material-request-inbound-v1",target=()=>storage??window.localStorage;let fault=false;const read=():Read=>{if(fault)return{kind:"unavailable"};let raw:string|null;try{raw=target().getItem(key)}catch{fault=true;return{kind:"unavailable"}}if(raw===null)return{kind:"missing"};try{return{kind:"valid",value:validateInboundSentinel(JSON.parse(raw))}}catch{return{kind:"corrupt"}}};return{read,persist(v){const checked=validateInboundSentinel(v);if(read().kind!=="missing")throw new Error("已有待核验入账，禁止覆盖原请求");try{target().setItem(key,JSON.stringify(checked))}catch{fault=true;throw new Error("无法保存入账核验记录，提交已停止")}const saved=read();if(saved.kind!=="valid"||JSON.stringify(saved.value)!==JSON.stringify(checked)){fault=true;throw new Error("入账核验记录未可靠保存，提交已停止")}},clear(trace){const current=read();if(current.kind!=="valid"||current.value.trace!==trace)throw new Error("原入账坐标已变化，禁止清理");try{target().removeItem(key)}catch{fault=true;throw new Error("入账核验记录清理失败")}if(read().kind!=="missing")throw new Error("入账核验记录清理失败")}}}
type RecoveryAdapter=FormalMaterialRequestAdapter&Required<Pick<FormalMaterialRequestAdapter,"loadIdentityNoReplay"|"loadAccessNoReplay"|"listInboundOrders"|"detailNoReplay">>;
export function hasInboundRecovery(a:FormalMaterialRequestAdapter):a is RecoveryAdapter{return [a.loadIdentityNoReplay,a.loadAccessNoReplay,a.listInboundOrders,a.detailNoReplay].every(x=>typeof x==="function")}
export async function recoverInbound(adapter: RecoveryAdapter, store: InboundStore, original: InboundSentinel, canCommit = () => true) {
  const s = validateInboundSentinel(original);
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== s.person_id || identity.authorization_version !== s.authorization_version
      || access.person_id !== s.person_id || access.authorization_version !== s.authorization_version || !access.can_read)
    throw new Error("入账身份或权限已变化，保留原请求");
  const rows = (await adapter.listInboundOrders(s.request_id)).map(validateInboundOrderResult);
  const matches = rows.filter(row => s.kind === "inbound-post"
    ? row.inbound_order_id === s.inbound_order_id : row.receipt_id === s.receipt_id);
  if (matches.length !== 1) throw new Error("尚未观察到唯一原入账结果，继续保留请求；不能据此再次提交");
  const found = matches[0];
  if (found.receipt_id !== s.receipt_id || found.target_location_id !== s.target_location_id || found.target_person_id !== s.target_person_id)
    throw new Error("入账结果与原收货目标不一致，保留原请求");
  if (found.status !== "pending" && found.status !== "posted") throw new Error("入账状态尚不能确认，保留原请求");
  if ((found.status === "posted") !== (found.posting_transaction_id !== null)) throw new Error("入账状态与库存事务不一致，保留原请求");
  if (s.kind === "inbound-post" && found.status !== "posted") throw new Error("原入账单尚未过账，继续保留请求；不能据此再次提交");
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(s.request_id), s.request_id);
  if (detail.request_version < s.expected_version) throw new Error("需求回读版本落后，保留原请求");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (JSON.stringify(identity) !== JSON.stringify(afterIdentity) || JSON.stringify(access) !== JSON.stringify(afterAccess) || !canCommit())
    throw new Error("核验期间身份、权限或页面已变化，保留原请求");
  const saved = store.read();
  if (saved.kind !== "valid" || JSON.stringify(saved.value) !== JSON.stringify(s)) throw new Error("原入账记录已变化，禁止清理");
  store.clear(s.trace);
  return { order: found, detail };
}
