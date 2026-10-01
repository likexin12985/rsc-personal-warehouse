/** A recovered posting is an original historical fact, never current inventory. */
import { canonical, decision, hash, id, identity, object, report, type Disposition, type Identity, type Report } from './formalLossReview';
export type Flow = 'disposition' | 'return';
export type Reference = { headquarters_decision_id: string; expected_headquarters_review_hash: string; expected_submission_plan_hash: string };
export type Intent = Reference & { target_location_id?: string; transit_location_id?: string };
export type Command = Intent & { expected_plan_hash: string; request_id: string; idempotency_key: string };
export type OriginalPosting = { result_scope: 'original_posting'; disposition_id: string; executor_person_id: string; posting_transaction_id: string; quantity: string; return_operation_id: string | null };
export type ApprovedLine = { line_id: string; headquarters_decision_id: string; disposition: Disposition; reason: string; preview_reference: Reference; original_posting: OriginalPosting | null };
export type Route = { source_location_id: string; target_location_id: string; target_location_code: string; target_location_name: string; transit_location_id: string; transit_location_code: string; transit_location_name: string; region_org_id: string; custody_assignment_id: string; custodian_person_id: string; custody_effective_from: string };
export type Sources = Identity & { schema_version: '1.0'; queried_at: string; report: Report; decisions: ApprovedLine[]; return_routes_status: 'not_required' | 'available' | 'unavailable'; return_routes: Route[]; return_routes_reason: string | null };
type CommonPreview = { plan_hash: string; checked_at: string; planning_status: 'preview_only'; stock_effect: 'none'; quantity: string; material_id: string; source_account_id: string; serial_ids: string[]; reason: string };
export type AccountPreview = CommonPreview & { operation_id: string; line_id: string; disposition: Exclude<Disposition,'return_to_region'|'scrap'>; target_account_id: string; source_condition: string; target_condition: string };
export type ReturnPreview = CommonPreview & { loss_operation_id: string; loss_line_id: string; headquarters_decision_id: string; derived_return_operation_id: string; pending_account_id: string; condition_code: string; return_fulfillment_required: true };
export type Preview = AccountPreview | ReturnPreview;
export type Pending = Identity & { v: 1; flow: Flow; operation_id: string; operation_no: string; line_id: string; material_name: string; disposition: Exclude<Disposition,'scrap'>; preview: Preview; command: Command; request_hash: string };
export type Resolution = { status: 'pending' | 'found' | 'sealed'; return_operation_id?: string };
export function fail(message='报损处置记录无法核验，已停止操作'): never { throw new Error(message); }
function text(x:unknown):string { if(typeof x!=='string'||!x.length||x.length>1000||/\u0000/.test(x))fail();return x; }
function digest(x:unknown):string { if(typeof x!=='string'||!/^[a-f0-9]{64}$/.test(x))fail();return x; }
function date(x:unknown):string { const s=text(x);if(!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(s)||!Number.isFinite(Date.parse(s)))fail();return s; }
function qty(x:unknown):string { if(typeof x!=='string'||!/^\d{1,15}(?:\.\d{1,3})?$/.test(x))fail();const [n,f='']=x.split('.');if(BigInt(n+f.padEnd(3,'0'))<=0n)fail();return `${BigInt(n)}.${f.padEnd(3,'0')}`; }
function unique(xs:string[]) { if(new Set(xs).size!==xs.length)fail(); }
function array<T>(x:unknown,parse:(v:unknown)=>T,max=100):T[] { if(!Array.isArray(x)||x.length>max)fail();return x.map(parse); }
export function flow(x:unknown):Flow { if(x!=='disposition'&&x!=='return')fail();return x; }
export function intent(x:unknown,f:Flow):Intent {
  const r=object(x,['headquarters_decision_id','expected_headquarters_review_hash','expected_submission_plan_hash',...(f==='return'?['target_location_id','transit_location_id']:[])]);
  const result:Intent={headquarters_decision_id:id(r.headquarters_decision_id),expected_headquarters_review_hash:digest(r.expected_headquarters_review_hash),expected_submission_plan_hash:digest(r.expected_submission_plan_hash)};
  if(f==='return'){result.target_location_id=id(r.target_location_id);result.transit_location_id=id(r.transit_location_id);if(result.target_location_id===result.transit_location_id)fail();}return result;
}
export function sources(x:unknown,expected:Identity,operation:string):Sources {
  const r=object(x,['schema_version','person_id','authorization_version','queried_at','report','decisions','return_routes_status','return_routes','return_routes_reason']);
  if(r.schema_version!=='1.0'||canonical(identity(r))!==canonical(identity(expected)))fail('身份已变化，请刷新处置页');date(r.queried_at);
  const doc=report(r.report);if(doc.operation_id!==id(operation))fail();
  const decisions=array(r.decisions,v=>{
    const d=object(v,['line_id','headquarters_decision_id','disposition','reason','preview_reference','original_posting']);
    const approved=decision({line_id:d.line_id,disposition:d.disposition,reason:d.reason});const ref=intent(d.preview_reference,'disposition');
    const original=doc.headquarters_review?.decisions.find(a=>a.line_id===approved.line_id);
    if(!original||canonical(original)!==canonical(approved)||ref.headquarters_decision_id!==id(d.headquarters_decision_id)||ref.expected_headquarters_review_hash!==doc.headquarters_review?.request_hash||ref.expected_submission_plan_hash!==doc.submission_plan_hash)fail();
    let posting:OriginalPosting|null=null;
    if(d.original_posting!==null){const p=object(d.original_posting,['result_scope','disposition_id','executor_person_id','posting_transaction_id','quantity','return_operation_id']);
      if(p.result_scope!=='original_posting'||qty(p.quantity)!==doc.lines.find(l=>l.line_id===approved.line_id)?.quantity||(p.return_operation_id!==null)!==(approved.disposition==='return_to_region'))fail();
      posting={result_scope:'original_posting',disposition_id:id(p.disposition_id),executor_person_id:id(p.executor_person_id),posting_transaction_id:id(p.posting_transaction_id),quantity:qty(p.quantity),return_operation_id:p.return_operation_id===null?null:id(p.return_operation_id)};
    }
    return {...approved,headquarters_decision_id:id(d.headquarters_decision_id),preview_reference:ref,original_posting:posting};
  });unique(decisions.map(d=>d.line_id));unique(decisions.map(d=>d.headquarters_decision_id));
  if(decisions.length!==(doc.headquarters_review?.decisions.length??0))fail();
  const routes=array(r.return_routes,v=>{
    const p=object(v,['source_location_id','target_location_id','target_location_code','target_location_name','transit_location_id','transit_location_code','transit_location_name','region_org_id','custody_assignment_id','custodian_person_id','custody_effective_from']);
    for(const k of ['source_location_id','target_location_id','transit_location_id','region_org_id','custody_assignment_id','custodian_person_id'])id(p[k]);
    for(const k of ['target_location_code','target_location_name','transit_location_code','transit_location_name'])text(p[k]);date(p.custody_effective_from);
    if(p.source_location_id!==doc.source_location_id||new Set([p.source_location_id,p.target_location_id,p.transit_location_id]).size!==3)fail();return p as Route;
  },20);unique(routes.map(p=>p.target_location_id+':'+p.transit_location_id));
  const needsReturn=decisions.some(d=>d.disposition==='return_to_region');
  if(!['not_required','available','unavailable'].includes(String(r.return_routes_status))||needsReturn===(r.return_routes_status==='not_required')||(r.return_routes_status==='available')!==(routes.length>0))fail();
  if(r.return_routes_status==='unavailable')text(r.return_routes_reason);else if(r.return_routes_reason!==null)fail();
  return {...r,...identity(r),report:doc,decisions,return_routes:routes} as Sources;
}
export function preview(x:unknown,f:Flow):Preview {
  const r=object(x,['plan_hash','checked_at','planning_status','stock_effect','quantity','material_id','source_account_id','serial_ids','reason',...(f==='disposition'?['operation_id','line_id','disposition','target_account_id','source_condition','target_condition']:['loss_operation_id','loss_line_id','headquarters_decision_id','derived_return_operation_id','pending_account_id','condition_code','return_fulfillment_required'])]);
  digest(r.plan_hash);date(r.checked_at);if(r.planning_status!=='preview_only'||r.stock_effect!=='none')fail();id(r.material_id);id(r.source_account_id);text(r.reason);
  const serials=array(r.serial_ids,id,10000);unique(serials);const quantity=qty(r.quantity);if(serials.length&&quantity!==`${serials.length}.000`)fail();
  if(f==='disposition'){for(const k of ['operation_id','line_id','target_account_id'])id(r[k]);if(!['restore_available','convert_used','convert_damaged'].includes(String(r.disposition)))fail();
    if(!['new','used','damaged'].includes(String(r.source_condition))||r.target_condition!==(r.disposition==='restore_available'?r.source_condition:r.disposition==='convert_used'?'used':'damaged'))fail();
  }else{for(const k of ['loss_operation_id','loss_line_id','headquarters_decision_id','derived_return_operation_id','pending_account_id'])id(r[k]);if(r.return_fulfillment_required!==true||!['new','used','damaged'].includes(String(r.condition_code)))fail();}
  if(r.source_account_id===(f==='return'?r.pending_account_id:r.target_account_id))fail();return {...r,quantity,serial_ids:serials} as Preview;
}
export function command(x:unknown,f:Flow):Command {
  const r=object(x,['headquarters_decision_id','expected_headquarters_review_hash','expected_submission_plan_hash','expected_plan_hash','request_id','idempotency_key',...(f==='return'?['target_location_id','transit_location_id']:[])]);
  const {expected_plan_hash,request_id,idempotency_key,...reference}=r;
  if(typeof request_id!=='string'||!/^[A-Za-z0-9._:-]{8,160}$/.test(request_id)||typeof idempotency_key!=='string'||!/^[A-Za-z0-9._:-]{8,200}$/.test(idempotency_key))fail();
  return {...intent(reference,f),expected_plan_hash:digest(expected_plan_hash),request_id,idempotency_key};
}
export function pending(x:unknown):Pending {
  const r=object(x,['v','person_id','authorization_version','flow','operation_id','operation_no','line_id','material_name','disposition','preview','command','request_hash']);
  if(r.v!==1)fail();const f=flow(r.flow),p=preview(r.preview,f),c=command(r.command,f);id(r.operation_id);id(r.line_id);text(r.operation_no);text(r.material_name);digest(r.request_hash);
  if(c.expected_plan_hash!==p.plan_hash)fail();
  if(f==='return'){const v=p as ReturnPreview;if(r.disposition!=='return_to_region'||r.operation_id!==v.loss_operation_id||r.line_id!==v.loss_line_id||c.headquarters_decision_id!==v.headquarters_decision_id)fail();}
  else{const v=p as AccountPreview;if(r.operation_id!==v.operation_id||r.line_id!==v.line_id||r.disposition!==v.disposition)fail();}
  return {...r,...identity(r),preview:p,command:c} as Pending;
}
export async function requestHash(c:Command):Promise<string> {const {request_id,expected_plan_hash,idempotency_key:_key,...intent}=c;return hash({intent,request_id,expected_plan_hash});}
export async function verifyPending(x:unknown):Promise<Pending> {const p=pending(x);if(await requestHash(p.command)!==p.request_hash)fail('原处置请求摘要不一致，请保留记录');return p;}
export function checkTarget(p:Pending,s:Sources){
  const d=s.decisions.find(d=>d.headquarters_decision_id===p.command.headquarters_decision_id),line=s.report.lines.find(l=>l.line_id===p.line_id);
  const {expected_plan_hash:_plan,request_id:_r,idempotency_key:_k,target_location_id,transit_location_id,...ref}=p.command;
  if(s.person_id!==p.person_id||s.authorization_version<p.authorization_version||s.report.operation_id!==p.operation_id||!d||d.original_posting||d.line_id!==p.line_id||d.disposition!==p.disposition||canonical(d.preview_reference)!==canonical(ref)||!line||line.quantity!==p.preview.quantity||line.material_id!==p.preview.material_id||canonical(line.serials.map(s=>s.serial_id).sort())!==canonical([...p.preview.serial_ids].sort())||p.preview.reason!==d.reason)fail('原批准、物料或处置状态已变化，请重新核验');
  if(p.flow==='return'&&(s.return_routes_status!=='available'||!s.return_routes.some(r=>r.target_location_id===target_location_id&&r.transit_location_id===transit_location_id)))fail('原退回路线已不可用');
  if((p.flow==='return'?(p.preview as ReturnPreview).condition_code:(p.preview as AccountPreview).source_condition)!==line.condition_code)fail();
}
export async function prepare(s:Sources,d:ApprovedLine,rawPreview:unknown,route?:Route):Promise<Pending> {
  if(d.disposition==='scrap')fail('报废执行尚未开放');const f=d.disposition==='return_to_region'?'return':'disposition';
  if(f==='return'&&!route||f==='disposition'&&route)fail();
  const p=preview(rawPreview,f),c=command({...d.preview_reference,...(route?{target_location_id:route.target_location_id,transit_location_id:route.transit_location_id}:{}),expected_plan_hash:p.plan_hash,request_id:crypto.randomUUID(),idempotency_key:crypto.randomUUID()},f);
  const result=pending({v:1,...identity(s),flow:f,operation_id:s.report.operation_id,operation_no:s.report.operation_no,line_id:d.line_id,material_name:s.report.lines.find(l=>l.line_id===d.line_id)?.material_name,disposition:d.disposition,preview:p,command:c,request_hash:await requestHash(c)});checkTarget(result,s);return result;
}
export function resolution(x:unknown,p:Pending):Resolution {
  const r=x as Record<string,unknown>;if(!r||r.retry_permitted!==false||r.result_scope!=='original_command')fail();
  const common=['lookup_status','retry_permitted','result_scope'];if(r.lookup_status==='not_found'){object(r,common);return {status:'pending'};}
  if(r.lookup_status==='sealed'){
    object(r,[...common,'seal']);const s=object(r.seal,['seal_id','flow','operation_id','line_id','headquarters_decision_id','executor_person_id','request_id','request_hash','plan_hash','sealed_at','stock_effect']);
    id(s.seal_id);date(s.sealed_at);if(s.flow!==p.flow||s.stock_effect!=='none')fail();binding(s,p);return {status:'sealed'};
  }
  if(r.lookup_status!=='found')fail();object(r,[...common,'disposition']);
  const s=object(r.disposition,['disposition_id','operation_id','line_id','headquarters_decision_id','executor_person_id','authorization_version','posting_transaction_id','posting_movement_id','quantity','source_account_id','target_account_id','request_id','request_hash','plan_hash','status','disposition',...(p.flow==='return'?['return_operation_id','origin_kind','stock_effect','return_fulfillment_required']:[])]);
  binding(s,p);for(const k of ['disposition_id','posting_transaction_id','posting_movement_id'])id(s[k]);
  const target=p.flow==='return'?(p.preview as ReturnPreview).pending_account_id:(p.preview as AccountPreview).target_account_id;
  if(s.status!=='posted'||s.disposition!==p.disposition||qty(s.quantity)!==p.preview.quantity||s.source_account_id!==p.preview.source_account_id||s.target_account_id!==target||!Number.isSafeInteger(s.authorization_version)||Number(s.authorization_version)<p.authorization_version)fail();
  if(p.flow==='return'){if(s.return_operation_id!==(p.preview as ReturnPreview).derived_return_operation_id||s.origin_kind!=='loss_report'||s.stock_effect!=='frozen_to_return_pending'||s.return_fulfillment_required!==true)fail();return {status:'found',return_operation_id:id(s.return_operation_id)};}
  return {status:'found'};
}
function binding(s:Record<string,unknown>,p:Pending){if(s.operation_id!==p.operation_id||s.line_id!==p.line_id||s.headquarters_decision_id!==p.command.headquarters_decision_id||s.executor_person_id!==p.person_id||s.request_id!==p.command.request_id||s.request_hash!==p.request_hash||s.plan_hash!==p.command.expected_plan_hash)fail('响应与原处置请求不符，保留原请求');}
