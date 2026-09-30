/** Own-stock loss submission: preview does not freeze inventory or approve disposal. */
import { canonical, digest, fail, id, integer, list, micros, object, quantity, requestId, serials, text, timestamp, unique, units, type Identity } from './formalReturnReceiving';
import { hash } from './formalReturnInbound';
import { trimmed } from './formalReturnReceipt';
const ACCOUNT_KEYS = ['stock_account_id','owner_org_id','owner_org_code','owner_org_name','location_owner_org_id','location_owner_org_code','location_owner_org_name','location_id','location_code','location_name','location_type','location_parent_id','custodian_person_id','custodian_person_name','material_id','sku_code','material_name','base_unit','tracking_mode','condition_code','availability_bucket','lot_id','lot_no','quantity_status','quantity','balance_version','ledger_cursor'];
function account(value: unknown, person: string, location: string) {
  const r = object(value, ACCOUNT_KEYS);
  if (r.custodian_person_id !== person || r.location_id !== location || r.location_type !== 'personal' || r.availability_bucket !== 'available' || r.quantity_status !== 'available' || !['new','used','damaged'].includes(String(r.condition_code)) || !['none','lot','serial','lot_and_serial'].includes(String(r.tracking_mode)) || units(r.quantity) <= 0n) fail('报损来源不是本人当前可用库存');
  if ((r.lot_id === null) !== (r.lot_no === null)) fail();
  if ((r.tracking_mode === 'lot' || r.tracking_mode === 'lot_and_serial') && r.lot_id === null) fail();
  if ((r.tracking_mode === 'serial' || r.tracking_mode === 'lot_and_serial') && units(r.quantity) % 1000n !== 0n) fail();
  return { stock_account_id:id(r.stock_account_id),owner_org_id:id(r.owner_org_id),owner_org_code:text(r.owner_org_code),owner_org_name:text(r.owner_org_name),location_owner_org_id:id(r.location_owner_org_id),location_owner_org_code:text(r.location_owner_org_code),location_owner_org_name:text(r.location_owner_org_name),location_id:id(r.location_id),location_code:text(r.location_code),location_name:text(r.location_name),location_type:'personal' as const,location_parent_id:id(r.location_parent_id),custodian_person_id:id(r.custodian_person_id),custodian_person_name:text(r.custodian_person_name),material_id:id(r.material_id),sku_code:text(r.sku_code,80),material_name:text(r.material_name),base_unit:text(r.base_unit,100),tracking_mode:r.tracking_mode as 'none'|'lot'|'serial'|'lot_and_serial',condition_code:r.condition_code as 'new'|'used'|'damaged',availability_bucket:'available' as const,lot_id:r.lot_id===null?null:id(r.lot_id),lot_no:r.lot_no===null?null:text(r.lot_no),quantity_status:'available' as const,quantity:quantity(r.quantity),balance_version:integer(r.balance_version),ledger_cursor:integer(r.ledger_cursor) };
}
export function sources(value: unknown, identity: Identity) {
  const r=object(value,['schema_version','person_id','authorization_version','location_id','custody_effective_from','ledger_cursor','projected_at','queried_at','items']);
  if(r.schema_version!=='1.0'||r.person_id!==identity.person_id||integer(r.authorization_version,1)!==identity.authorization_version) fail('报损来源身份已变化');
  const location_id=id(r.location_id),queried_at=timestamp(r.queried_at),custody_effective_from=timestamp(r.custody_effective_from),projected_at=r.projected_at===null?null:timestamp(r.projected_at),ledger_cursor=integer(r.ledger_cursor);
  if(micros(custody_effective_from)>micros(queried_at)||(projected_at!==null&&micros(projected_at)>micros(queried_at)))fail();
  const items=list(r.items,v=>account(v,identity.person_id,location_id),10000);
  unique(items.map(v=>v.stock_account_id));
  if(items.some((v,i)=>v.ledger_cursor>ledger_cursor||(i>0&&items[i-1].stock_account_id>=v.stock_account_id)))fail();
  return {schema_version:'1.0' as const,person_id:id(identity.person_id),authorization_version:integer(identity.authorization_version,1),location_id,custody_effective_from,ledger_cursor,projected_at,queried_at,items};
}
export type Sources=ReturnType<typeof sources>;
/** SN list proves current membership only; scanned SKU/SN/QR remain user input. */
export function serialOptions(value: unknown, current: Sources, accountId: string, after: string | null = null, exact: string | null = null, limit = 50) {
  integer(limit, 1, 100); if (after !== null) id(after);
  if (exact !== null && (text(exact, 200) !== exact.trim() || /[\u0000-\u001f\u007f]/.test(exact) || after !== null)) fail('请使用完整 SN 精确查询');
  const account = current.items.find(r => r.stock_account_id === id(accountId));
  if (!account || !['serial', 'lot_and_serial'].includes(account.tracking_mode)) fail('所选库存不支持 SN 查询');
  const r = object(value, ['schema_version', 'projection_status', 'opening_balance_status', 'projected_at', 'ledger_cursor', 'person_id', 'location_id', 'stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'tracking_mode', 'condition_code', 'availability_bucket', 'lot_id', 'lot_no', 'total_serials', 'items', 'next_after_id']);
  if (r.schema_version !== '1.0' || r.projection_status !== 'ready' || r.opening_balance_status !== 'established' || r.person_id !== current.person_id || r.location_id !== current.location_id || r.ledger_cursor !== current.ledger_cursor || r.projected_at !== current.projected_at) fail('库存快照变化，请重新读取报损来源');
  for (const key of ['stock_account_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'tracking_mode', 'condition_code', 'availability_bucket', 'lot_id', 'lot_no'] as const) if (r[key] !== account[key]) fail('SN 来源与所选库存不一致');
  const total = integer(r.total_serials);
  if (BigInt(total) * 1000n !== units(account.quantity)) fail('SN 总数与账户库存不一致');
  let previous = after;
  const items = list(r.items, value => {
    const row = object(value, ['serial_id', 'serial_no', 'lifecycle_status']), serial_id = id(row.serial_id), serial_no = text(row.serial_no, 200);
    if (row.lifecycle_status !== 'active' || (previous !== null && serial_id <= previous) || (exact !== null && serial_no !== exact)) fail('SN 查询结果不属于当前选择');
    previous = serial_id; return { serial_id, serial_no, lifecycle_status: 'active' as const };
  }, limit);
  const next_after_id = r.next_after_id === null ? null : id(r.next_after_id);
  if (items.length > total || (next_after_id !== null && (exact !== null || items.length !== limit || next_after_id !== previous)) || (exact !== null && items.length > 1)) fail('SN 分页证据不完整');
  if (exact === null && after === null && next_after_id === null && items.length !== total) fail('SN 首批结果缺失');
  return { stock_account_id: account.stock_account_id, total_serials: total, ledger_cursor: current.ledger_cursor, items, next_after_id };
}
export type SerialOptions = ReturnType<typeof serialOptions>;
function amount(value: unknown) {
  if(typeof value!=='string'||!/^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?$/.test(value))fail('报损数量必须为最多三位小数的十进制字符串');
  const [whole,fraction='']=value.split('.'),result=`${whole}.${fraction.padEnd(3,'0')}`;
  if(units(result)<=0n)fail();return result;
}
export function selectionInput(value: unknown,person:string) {
  const r=object(value,['operator_person_id','lines']);if(r.operator_person_id!==id(person))fail();
  const lines=list(r.lines,v=>{
    const l=object(v,['stock_account_id','quantity','serial_verifications']);
    const proofs=list(l.serial_verifications,v=>{const s=object(v,['serial_id','sku_code','serial_no','qr_code']);return {serial_id:id(s.serial_id),sku_code:text(s.sku_code,80),serial_no:text(s.serial_no,200),qr_code:text(s.qr_code,250)};},1000).sort((a,b)=>a.serial_id.localeCompare(b.serial_id));
    return {stock_account_id:id(l.stock_account_id),quantity:amount(l.quantity),serial_verifications:proofs};
  },100,1).sort((a,b)=>a.stock_account_id.localeCompare(b.stock_account_id));
  unique(lines.map(l=>l.stock_account_id));unique(lines.flatMap(l=>l.serial_verifications.map(s=>s.serial_id)));
  return {operator_person_id:id(person),lines};
}
export function input(value:unknown,person:string) {
  const r=object(value,['operator_person_id','lines','reason','evidence_file_ids']);
  const evidence_file_ids=list(r.evidence_file_ids,id,20,1).sort();unique(evidence_file_ids);
  return {...selectionInput({operator_person_id:r.operator_person_id,lines:r.lines},person),reason:trimmed(r.reason,500,true),evidence_file_ids};
}
export type Input=ReturnType<typeof input>;
export function command(value:unknown,person:string) {
  const r=object(value,['operator_person_id','lines','reason','evidence_file_ids','expected_plan_hash','request_id','idempotency_key']);
  const {expected_plan_hash,request_id,idempotency_key,...body}=r;
  if(typeof idempotency_key!=='string'||!/^[A-Za-z0-9._:-]{8,200}$/.test(idempotency_key))fail();
  return {...input(body,person),expected_plan_hash:digest(expected_plan_hash),request_id:requestId(request_id),idempotency_key};
}
export type Command=ReturnType<typeof command>;
export function checkSelection(body:ReturnType<typeof selectionInput>,current:Sources) {
  if(body.operator_person_id!==current.person_id)fail();
  for(const l of body.lines){
    const source=current.items.find(s=>s.stock_account_id===l.stock_account_id);
    if(!source||units(l.quantity)>units(source.quantity))fail('报损数量超出当前已核验的库存');
    const tracked=['serial','lot_and_serial'].includes(source.tracking_mode);
    if(tracked?units(l.quantity)!==BigInt(l.serial_verifications.length)*1000n:l.serial_verifications.length!==0)fail('报损数量与实物SN不一致');
    if(l.serial_verifications.some(s=>s.sku_code!==source.sku_code))fail('实物物料码与所选库存不一致');
  }
}
export async function requestHash(value:Input){return hash({operation_type:'loss_report',...input(value,value.operator_person_id)});}
function selectedLines(value:unknown,body:ReturnType<typeof selectionInput>,current:Sources){
  checkSelection(body,current);
  const lines=list(value,v=>{
    const l=object(v,['source','selected_quantity','selected_serials']),source=account(l.source,current.person_id,current.location_id);
    const expected=current.items.find(s=>s.stock_account_id===source.stock_account_id),chosen=body.lines.find(l=>l.stock_account_id===source.stock_account_id);
    if(!expected||!chosen||canonical(expected)!==canonical(source)||quantity(l.selected_quantity)!==chosen.quantity)fail('预检库存与原选择不一致');
    const sn=serials(l.selected_serials);
    if(canonical(sn)!==canonical(chosen.serial_verifications.map(s=>({serial_id:s.serial_id,serial_no:s.serial_no}))))fail('预检SN与实物证明不一致');
    return {source,selected_quantity:chosen.quantity,selected_serials:sn};
  },100,1).sort((a,b)=>a.source.stock_account_id.localeCompare(b.source.stock_account_id));
  unique(lines.map(l=>l.source.stock_account_id));if(lines.length!==body.lines.length)fail();return lines;
}
function evidence(value:unknown,ids:string[]){
  const rows=list(value,v=>{const r=object(v,['file_id','original_filename','sha256','size_bytes','mime_type']);return {file_id:id(r.file_id),original_filename:text(r.original_filename,255),sha256:digest(r.sha256),size_bytes:integer(r.size_bytes,1),mime_type:text(r.mime_type,100)};},20,1).sort((a,b)=>a.file_id.localeCompare(b.file_id));
  unique(rows.map(r=>r.file_id));if(canonical(rows.map(r=>r.file_id))!==canonical(ids))fail('预检凭证与原上传选择不一致');return rows;
}
function basis(r:Record<string,unknown>,current:Sources){
  if(r.schema_version!=='1.0'||r.operator_person_id!==current.person_id||r.authorization_version!==current.authorization_version||r.location_id!==current.location_id||integer(r.ledger_cursor)!==current.ledger_cursor||micros(r.checked_at)<micros(current.queried_at))fail('库存或身份在预检期间变化，请重新读取');
  return {schema_version:'1.0' as const,operator_person_id:current.person_id,authorization_version:current.authorization_version,location_id:current.location_id,ledger_cursor:current.ledger_cursor,checked_at:timestamp(r.checked_at)};
}
export async function selection(value:unknown,body:ReturnType<typeof selectionInput>,current:Sources){
  const r=object(value,['schema_version','planning_status','operator_person_id','authorization_version','location_id','ledger_cursor','checked_at','selection_hash','basis_hash','lines']);
  if(r.planning_status!=='source_selection_only'||r.selection_hash!==await hash({kind:'stock_loss_source_selection',...body}))fail();
  return {...basis(r,current),planning_status:'source_selection_only' as const,selection_hash:digest(r.selection_hash),basis_hash:digest(r.basis_hash),lines:selectedLines(r.lines,body,current)};
}
function previewShape(value:unknown,body:Input,current:Sources){
  const r=object(value,['schema_version','planning_status','operator_person_id','authorization_version','location_id','ledger_cursor','checked_at','reason','request_hash','plan_hash','lines','evidence']);
  if(r.planning_status!=='preview_only'||r.reason!==body.reason)fail('报损预检与本次原因或物料不一致');
  return {...basis(r,current),planning_status:'preview_only' as const,reason:body.reason,request_hash:digest(r.request_hash),plan_hash:digest(r.plan_hash),lines:selectedLines(r.lines,body,current),evidence:evidence(r.evidence,body.evidence_file_ids)};
}
export async function preview(value:unknown,body:Input,current:Sources){
  const result=previewShape(value,body,current);
  if(result.request_hash!==await requestHash(body))fail('报损预检与本次内容摘要不一致');
  return result;
}
export type Preview=Awaited<ReturnType<typeof preview>>;
export function lookupInput(value:Command,request_hash:string){return {operator_person_id:value.operator_person_id,request_id:value.request_id,idempotency_key:value.idempotency_key,request_hash:digest(request_hash),expected_plan_hash:value.expected_plan_hash};}
export async function result(value:unknown,original:Command,prepared:Preview){
  const r=object(value,['schema_version','operation_id','operation_no','requester_id','source_location_id','status','reason','request_id','request_hash','plan_hash','posting_transaction_id','submitted_at','lines','evidence']);
  if(r.schema_version!=='1.0'||r.status!=='submitted'||r.requester_id!==original.operator_person_id||r.source_location_id!==prepared.location_id||r.reason!==original.reason||r.request_id!==original.request_id||r.request_hash!==await requestHash(input({operator_person_id:original.operator_person_id,lines:original.lines,reason:original.reason,evidence_file_ids:original.evidence_file_ids},original.operator_person_id))||r.plan_hash!==original.expected_plan_hash||r.plan_hash!==prepared.plan_hash||canonical(r.lines)!==canonical(prepared.lines)||canonical(r.evidence)!==canonical(prepared.evidence)||micros(r.submitted_at)<micros(prepared.checked_at))fail('报损结果与原请求不一致，请保留原请求');
  return {schema_version:'1.0' as const,status:'submitted' as const,operation_id:id(r.operation_id),operation_no:text(r.operation_no,100),requester_id:original.operator_person_id,source_location_id:prepared.location_id,reason:original.reason,request_id:original.request_id,request_hash:digest(r.request_hash),plan_hash:original.expected_plan_hash,posting_transaction_id:id(r.posting_transaction_id),submitted_at:timestamp(r.submitted_at),lines:prepared.lines,evidence:prepared.evidence};
}
export async function lookup(value:unknown,original:Command,prepared:Preview){
  if(!value||typeof value!=='object'||Array.isArray(value))fail();
  const status=(value as Record<string,unknown>).lookup_status;
  const r=object(value,['lookup_status','retry_permitted',...(status==='found'?['submission']:status==='sealed'?['seal']:[])]);
  if(r.retry_permitted!==false)fail('不允许重发结果未知的报损请求');
  if(status==='not_found')return {status:'unknown' as const};
  if(status==='found')return {status:'submitted' as const,submission:await result(r.submission,original,prepared)};
  if(status!=='sealed')fail();
  const s=object(r.seal,['seal_id','operator_person_id','source_location_id','request_id','request_hash','plan_hash','sealed_at']);
  if(s.operator_person_id!==original.operator_person_id||s.source_location_id!==prepared.location_id||s.request_id!==original.request_id||s.request_hash!==prepared.request_hash||s.plan_hash!==original.expected_plan_hash||micros(s.sealed_at)<micros(prepared.checked_at))fail();
  return {status:'sealed' as const,seal_id:id(s.seal_id),sealed_at:timestamp(s.sealed_at)};
}

/** Persist the full original command and confirmed proof, never only a key. */
export function pending(value: unknown) {
  const r = object(value, ['v', 'person_id', 'authorization_version', 'sources', 'command', 'preview']);
  if (r.v !== 1) fail();
  const identity = { person_id: id(r.person_id), authorization_version: integer(r.authorization_version, 1) };
  const current = sources(r.sources, identity), original = command(r.command, identity.person_id);
  const { expected_plan_hash, request_id: _request, idempotency_key: _key, ...body } = original;
  const prepared = previewShape(r.preview, body, current);
  if (prepared.plan_hash !== expected_plan_hash) fail('原报损预检摘要与命令不一致');
  return { v: 1 as const, ...identity, sources: current, command: original, preview: prepared };
}
export type Pending = ReturnType<typeof pending>;
export async function verifyPending(value: unknown): Promise<Pending> {
  const p = pending(value), { expected_plan_hash: _plan, request_id: _request, idempotency_key: _key, ...body } = p.command;
  await preview(p.preview, body, p.sources);
  return p;
}
