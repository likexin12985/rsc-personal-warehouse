/** Exact sender commands and recovery proofs; a missing fact never permits replay. */
import { canonical, digest, fail, id, integer, list, micros, object, quantity, requestId, text, timestamp, unique, units,
  type Identity } from './formalReturnReceiving';
import { origin, destination, outboundOptions, shipmentOptions, type Detail } from './formalLossSenderReads';
export type Kind = 'outbound_return'|'ship_return';
export type Options = ReturnType<typeof outboundOptions>|ReturnType<typeof shipmentOptions>;
function kind(value: unknown): Kind { if(value!=='outbound_return'&&value!=='ship_return')fail('发件操作类型无效'); return value; }
async function hash(value: unknown) {
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)));
  return [...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');
}
function utc(value: unknown): string {
  const n=micros(value);if(n<0n)fail('发件时间无效');
  return new Date(Number(n/1000n)).toISOString().slice(0,19)+'.'+(n%1000000n).toString().padStart(6,'0')+'Z';
}
function reason(value: unknown) {
  const s=text(value,500).trim();if(!s||[...s].some(c=>c.charCodeAt(0)<32&&c!=='\n'&&c!=='\t'))fail('请填写明确的发件说明');return s;
}
function parcelText(value: unknown) {
  const s=text(value,100).trim();if(!s||/[\u0000-\u001f\u007f]/.test(s))fail('承运商和运单号无效');return s;
}
function amount(value: unknown) {
  if(typeof value!=='string')fail('数量必须使用精确十进制文本');
  const match=/^(0|[1-9][0-9]{0,14})(?:\.([0-9]{1,3}))?$/.exec(value);if(!match)fail('数量精度无效');
  const result=match[1]+'.'+(match[2]??'').padEnd(3,'0');if(units(result)<=0n)fail('发件数量必须为正数');return result;
}
const common=['operator_person_id','reason','lines'];
const submitKeys=['expected_plan_hash','request_id','idempotency_key'];
export function input(operation: Kind, value: unknown, person: string) {
  kind(operation);
  const row=object(value,[...common,...(operation==='outbound_return'?['outbound_at']:['shipped_at','carrier','tracking_no'])]);
  if(id(row.operator_person_id)!==id(person))fail('发件操作人必须是当前人员');
  const base={operator_person_id:id(row.operator_person_id),reason:reason(row.reason)};
  if(operation==='outbound_return'){
    const lines=list(row.lines,value=>{
      const r=object(value,['operation_line_id','quantity','serial_verifications']);
      const selected_quantity=amount(r.quantity);
      const proofs=list(r.serial_verifications,value=>{
        const p=object(value,['serial_id','sku_code','serial_no','qr_code']);
        return {serial_id:id(p.serial_id),sku_code:text(p.sku_code,80),serial_no:text(p.serial_no,200),qr_code:text(p.qr_code,250)};
      },1000).sort((a,b)=>a.serial_id.localeCompare(b.serial_id));
      unique(proofs.map(p=>p.serial_id));
      return {operation_line_id:id(r.operation_line_id),quantity:selected_quantity,serial_verifications:proofs};
    },100,1).sort((a,b)=>a.operation_line_id.localeCompare(b.operation_line_id));
    unique(lines.map(l=>l.operation_line_id));unique(lines.flatMap(l=>l.serial_verifications.map(p=>p.serial_id)));
    return {...base,outbound_at:utc(row.outbound_at),lines};
  }
  const lines=list(row.lines,value=>{
    const r=object(value,['outbound_line_id','quantity','serial_ids']);const selected_quantity=amount(r.quantity);
    const serial_ids=list(r.serial_ids,id,1000).sort();unique(serial_ids);
    return {outbound_line_id:id(r.outbound_line_id),quantity:selected_quantity,serial_ids};
  },100,1).sort((a,b)=>a.outbound_line_id.localeCompare(b.outbound_line_id));
  unique(lines.map(l=>l.outbound_line_id));unique(lines.flatMap(l=>l.serial_ids));
  return {...base,shipped_at:utc(row.shipped_at),carrier:parcelText(row.carrier),tracking_no:parcelText(row.tracking_no),lines};
}
export function command(operation: Kind, value: unknown, person: string) {
  const r=object(value,[...common,...submitKeys,...(kind(operation)==='outbound_return'?['outbound_at']:['shipped_at','carrier','tracking_no'])]);
  const body=Object.fromEntries(Object.entries(r).filter(([key])=>!submitKeys.includes(key)));
  const key=text(r.idempotency_key,200);if(!/^[A-Za-z0-9._:-]{8,200}$/.test(key))fail('原幂等键无效');
  return {...input(operation,body,person),expected_plan_hash:digest(r.expected_plan_hash),request_id:requestId(r.request_id),idempotency_key:key};
}
export type Command=ReturnType<typeof command>;
function body(operation: Kind, value: Command) {
  const {expected_plan_hash:_plan,request_id:_request,idempotency_key:_key,...contents}=value;
  return input(operation,contents,value.operator_person_id);
}
export async function requestHash(operation: Kind, operationId: string, value: unknown, person: string) {
  return hash({operation_type:kind(operation),operation_id:id(operationId),...input(operation,value,person)});
}
const meta=['schema_version','planning_status','operation_id','operation_no','operator_person_id','authorization_version','reason','ledger_cursor','checked_at','destination','request_hash','plan_hash','lines','origin'];
function selectedLines(operation: Kind, value: unknown, contents: ReturnType<typeof input>, choices: Options) {
  const commands=contents.lines;
  const lines=list(value,(value)=>{
    if(operation==='outbound_return'){
      const row=value as Record<string,unknown>;
      const opts=choices as ReturnType<typeof outboundOptions>;
      const option=opts.lines.find(l=>l.operation_line_id===row?.operation_line_id);
      const selected=commands.find(l=>'operation_line_id' in l&&l.operation_line_id===row?.operation_line_id);
      if(!option||!selected||!('serial_verifications' in selected))fail('预检明细不属于本次出库选择');
      if(units(selected.quantity)>units(option.selectable_quantity))fail('出库选择超过当前可选数量');
      const {tracking_mode,quantity_scale,allow_fraction,serials,selectable_quantity,...original}=option;
      const tracked=tracking_mode==='serial'||tracking_mode==='lot_and_serial';
      const step=10n**BigInt(3-(allow_fraction&&!tracked?quantity_scale:0));
      if(units(selected.quantity)%step!==0n||(tracked?BigInt(selected.serial_verifications.length)*1000n!==units(selected.quantity):selected.serial_verifications.length>0))fail('出库数量或 SN 不符合策略');
      const selected_serials=selected.serial_verifications.map(p=>{
        const sn=serials.find(s=>s.serial_id===p.serial_id&&s.serial_no===p.serial_no);
        if(!sn||p.sku_code!==option.sku_code)fail('扫码证明与所选原 SN 不匹配');return sn;
      });
      const expected={...original,selected_quantity:selected.quantity,selected_serials};
      object(value,Object.keys(expected));if(canonical(expected)!==canonical(value))fail('出库预检与原选择或库存快照不一致');
      return expected;
    }
    const row=value as Record<string,unknown>;
    const opts=choices as ReturnType<typeof shipmentOptions>;
    const option=opts.lines.find(l=>l.outbound_line_id===row?.outbound_line_id);
    const selected=commands.find(l=>'outbound_line_id' in l&&l.outbound_line_id===row?.outbound_line_id);
    if(!option||!selected||!('serial_ids' in selected))fail('预检明细不属于本次交运选择');
    if(units(selected.quantity)>units(option.selectable_quantity))fail('交运数量超过原出库剩余量');
    const {tracking_mode,quantity_scale,allow_fraction,serials,selectable_quantity,outbound_at:_time,...original}=option;
    const tracked=tracking_mode==='serial'||tracking_mode==='lot_and_serial';
    const step=10n**BigInt(3-(allow_fraction&&!tracked?quantity_scale:0));
    if(units(selected.quantity)%step!==0n||(tracked?BigInt(selected.serial_ids.length)*1000n!==units(selected.quantity):selected.serial_ids.length>0))fail('交运数量或 SN 不符合策略');
    const selected_serials=selected.serial_ids.map(key=>{const sn=serials.find(s=>s.serial_id===key);if(!sn)fail('交运 SN 未从实际出库明细选择');return sn;});
    const expected={...original,selected_quantity:selected.quantity,selected_serials};
    object(value,Object.keys(expected));if(canonical(expected)!==canonical(value))fail('交运预检与原选择或在途快照不一致');
    return expected;
  },100,1);
  const keys=lines.map(l=>'outbound_line_id' in l?l.outbound_line_id:l.operation_line_id);unique(keys);
  if(lines.length!==commands.length)fail('预检未完整覆盖本次选择');
  unique(lines.flatMap(l=>l.selected_serials.map(s=>s.serial_id)));
  return lines;
}
export function previewShape(operation: Kind, value: unknown, raw: unknown, choices: Options, original: Detail) {
  const contents=input(operation,raw,choices.person_id);
  const extra=operation==='outbound_return'?['outbound_at']:['shipped_at','carrier','tracking_no'];
  const r=object(value,[...meta,...extra]);
  const proof=origin(r.origin,choices,choices.operation_id),route=destination(r.destination);
  if(r.schema_version!=='1.0'||r.planning_status!=='preview_only'||r.operation_id!==choices.operation_id||r.operation_no!==choices.operation_no
      ||r.operator_person_id!==choices.person_id||r.authorization_version!==choices.authorization_version||r.reason!==contents.reason
      ||canonical(proof)!==canonical(original.origin)||canonical(route)!==canonical(choices.destination))fail('发件预检对象或授权快照不一致');
  const at=operation==='outbound_return'?'outbound_at':'shipped_at';
  if(micros(r[at])!==micros(contents[at as keyof typeof contents])||micros(r[at])<micros(original.origin.submitted_at)
      ||micros(r.checked_at)<micros(r[at])||micros(r.checked_at)<micros(choices.queried_at))fail('发件预检时间无效');
  if(operation==='ship_return'&&('carrier' in contents)&&(r.carrier!==contents.carrier||r.tracking_no!==contents.tracking_no))fail('交运预检运单不一致');
  const lines=selectedLines(operation,r.lines,contents,choices);
  return {schema_version:'1.0' as const,planning_status:'preview_only' as const,operation_id:choices.operation_id,operation_no:choices.operation_no,
    operator_person_id:choices.person_id,authorization_version:integer(r.authorization_version,1),reason:contents.reason,
    ledger_cursor:integer(r.ledger_cursor),checked_at:timestamp(r.checked_at),destination:route,origin:proof,
    request_hash:digest(r.request_hash),plan_hash:digest(r.plan_hash),lines,
    ...(operation==='outbound_return'?{outbound_at:timestamp(r.outbound_at)}:{shipped_at:timestamp(r.shipped_at),carrier:parcelText(r.carrier),tracking_no:parcelText(r.tracking_no)})};
}
export async function preview(operation: Kind, value: unknown, raw: unknown, choices: Options, original: Detail) {
  const prepared=previewShape(operation,value,raw,choices,original);
  if(prepared.request_hash!==await requestHash(operation,choices.operation_id,raw,choices.person_id))fail('发件请求摘要不一致');
  return prepared;
}
export type Preview=ReturnType<typeof previewShape>;
export async function result(operation: Kind, value: unknown, raw: Command, prepared: Preview) {
  const c=command(operation,raw,prepared.operator_person_id),isDeparture=operation==='outbound_return';
  const r=object(value,['schema_version','status',isDeparture?'outbound_id':'shipment_id',isDeparture?'outbound_no':'shipment_no',
    'operation_id','operator_person_id',isDeparture?'outbound_at':'shipped_at','recorded_at','reason','request_id','request_hash','plan_hash','destination','lines','origin',
    ...(isDeparture?['posting_transaction_id']:['carrier','tracking_no'])]);
  const expectedHash=await requestHash(operation,prepared.operation_id,body(operation,c),c.operator_person_id);
  if(r.schema_version!=='1.0'||r.status!==(isDeparture?'outbound':'shipped')||r.operation_id!==prepared.operation_id
      ||r.operator_person_id!==c.operator_person_id||r.request_id!==c.request_id||r.reason!==c.reason||r.request_hash!==expectedHash
      ||prepared.request_hash!==expectedHash||r.plan_hash!==c.expected_plan_hash||r.plan_hash!==prepared.plan_hash
      ||canonical(r.lines)!==canonical(prepared.lines)||canonical(r.origin)!==canonical(prepared.origin)
      ||canonical(r.destination)!==canonical(prepared.destination)||micros(r.recorded_at)<micros(prepared.checked_at))fail('发件结果与完整原请求不一致，请保留原请求');
  const at=isDeparture?'outbound_at':'shipped_at';if(micros(r[at])!==micros(prepared[at as keyof Preview]))fail('实际发件时间不一致');
  if(!isDeparture&&('carrier' in prepared)&&(r.carrier!==prepared.carrier||r.tracking_no!==prepared.tracking_no))fail('实际运单不一致');
  const identifier=id(r[isDeparture?'outbound_id':'shipment_id']),number=text(r[isDeparture?'outbound_no':'shipment_no'],100);
  return {...r,recorded_at:timestamp(r.recorded_at),...(isDeparture?{outbound_id:identifier,outbound_no:number,posting_transaction_id:id(r.posting_transaction_id)}:{shipment_id:identifier,shipment_no:number})};
}
export async function lookup(operation: Kind, value: unknown, raw: Command, prepared: Preview) {
  if(!value||typeof value!=='object'||Array.isArray(value))fail();
  const status=(value as Record<string,unknown>).lookup_status;
  if(status==='not_observed'){
    const r=object(value,['lookup_status','retry_allowed']);if(r.retry_allowed!==false)fail('缺失结果不能作为重试许可');
    return {status:'unknown' as const,retry_allowed:false as const};
  }
  if(status==='found'){
    const r=object(value,['lookup_status','retry_allowed','operation_type','result']);
    if(r.retry_allowed!==false||r.operation_type!==operation)fail();
    return {status:'found' as const,retry_allowed:false as const,result:await result(operation,r.result,raw,prepared)};
  }
  const r=object(value,['lookup_status','retry_allowed','seal']);if(status!=='sealed'||r.retry_allowed!==false)fail();
  const c=command(operation,raw,prepared.operator_person_id),s=object(r.seal,['seal_id','seal_scope','operation_type','operation_id','operator_person_id','source_loss_disposition_id','request_id','request_hash','sealed_at']);
  const expectedHash=await requestHash(operation,prepared.operation_id,body(operation,c),c.operator_person_id);
  if(s.seal_scope!=='actor_request_id'||s.operation_type!==operation||s.operation_id!==prepared.operation_id
      ||s.operator_person_id!==c.operator_person_id||s.source_loss_disposition_id!==prepared.origin.disposition_id
      ||s.request_id!==c.request_id||s.request_hash!==expectedHash||expectedHash!==prepared.request_hash
      ||micros(s.sealed_at)<micros(prepared.checked_at))fail('封存结果未绑定原发件请求');
  return {status:'sealed' as const,retry_allowed:false as const,seal:{...s,seal_id:id(s.seal_id),sealed_at:timestamp(s.sealed_at)}};
}
