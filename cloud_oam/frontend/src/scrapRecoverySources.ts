/** Public discovery DTOs, never write authority or substitutes for saved original requests. */
import { canonical, type Identity } from './formalLossReview';
import { digest, id, object, type RecoverySource } from './formalScrapCommands';
import { fact, fail, type RecoveryFact, type ReviewFact } from './formalScrapFacts';
export type Serial = {serial_id:string;serial_no:string;qr_code:string};
export type Stage = 'apply'|'regional'|'headquarters'|'execute';
export type State = 'awaiting_application'|'awaiting_regional'|'awaiting_headquarters'|'needs_evidence'|'approved_pending_execution'|'recovered';
export type Reference = {stage:'apply';source:RecoverySource}
  | {stage:'regional';source:RecoverySource;recovery_request_id:string;expected_request_hash:string}
  | {stage:'headquarters';source:RecoverySource;recovery_request_id:string;expected_request_hash:string;regional_review_id:string;expected_regional_hash:string}
  | {stage:'execute';source:RecoverySource;recovery_request_id:string;expected_request_hash:string;headquarters_review_id:string;expected_headquarters_hash:string};
export type Application = {application:ReviewFact;evidence_file_ids:string[];regional_reviews:ReviewFact[];headquarters_reviews:ReviewFact[];status:State};
export type Source = Identity & {schema_version:'1.0';availability:'verified';result_scope:'verified_scrap_recovery_references';
  write_authorization_provided:false;stock_effect:'none';queried_at:string;observed_ledger_cursor:number;requested_stage:Stage;
  scrap_reference:RecoverySource;root_disposition_id:string;operation_id:string;operation_no:string;line_id:string;owner_org_id:string;
  requester_person_id:string;requester_name:string;material_id:string;sku_code:string;material_name:string;base_unit:string;
  condition_code:'new'|'used'|'damaged';quantity:string;serial_ids:string[];serials:Serial[];is_current_scrap:boolean;state:State;
  next_reference:Reference|null;applications:Application[];recovery_posting:RecoveryFact|null};
export type Queue = Identity & {schema_version:'1.0';requested_stage:Stage;queried_at:string;
  items:(Source|{availability:'blocked';scrap_line_id:string})[];next_after_id:string|null};
export const stageLabels:Record<Stage,string>={apply:'找回申请',regional:'区域核实',headquarters:'总部审批',execute:'找回入库'};
export const stateLabels:Record<State,string>={awaiting_application:'待申请找回',awaiting_regional:'待区域核实',awaiting_headquarters:'待总部审批',needs_evidence:'需补充实物证据',approved_pending_execution:'已批准，待找回入库',recovered:'已找回入库（历史记录）'};
export function stage(value:unknown):Stage {if(!Object.hasOwn(stageLabels,String(value)))fail();return value as Stage;}
function state(value:unknown):State {if(!Object.hasOwn(stateLabels,String(value)))fail();return value as State;}
export function fresh(value:unknown):string {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value))fail();
  const at=Date.parse(value);if(!Number.isFinite(at)||at>Date.now()+30000||at<Date.now()-300000)fail();return value;
}
function identity(r:Record<string,unknown>,who:Identity,k:Stage){
  if(r.schema_version!=='1.0'||r.person_id!==who.person_id||r.authorization_version!==who.authorization_version||r.requested_stage!==k)fail();fresh(r.queried_at);
}
function ids(value:unknown,max=10000):string[]{if(!Array.isArray(value)||value.length>max)fail();const out=value.map(id);if(new Set(out).size!==out.length)fail();return out;}
function ref(value:unknown):RecoverySource {const r=object(value,['scrap_line_id','expected_scrap_request_hash']);return {scrap_line_id:id(r.scrap_line_id),expected_scrap_request_hash:digest(r.expected_scrap_request_hash)};}
function next(value:unknown,k:Stage,binding:RecoverySource):Reference|null {
  if(value===null)return null;
  const r=object(value,['stage','source',...(k==='apply'?[]:['recovery_request_id','expected_request_hash',...(k==='regional'?[]:k==='headquarters'?['regional_review_id','expected_regional_hash']:['headquarters_review_id','expected_headquarters_hash'])])]);
  if(r.stage!==k||canonical(ref(r.source))!==canonical(binding))fail();
  for(const [key,value] of Object.entries(r)){if(key.endsWith('_id'))id(value);if(key.endsWith('_hash'))digest(value);}
  return {...r,source:ref(r.source)} as Reference;
}
const fields=['schema_version','availability','result_scope','write_authorization_provided','stock_effect','person_id','authorization_version','queried_at','observed_ledger_cursor','requested_stage','scrap_reference','root_disposition_id','operation_id','operation_no','line_id','owner_org_id','requester_person_id','requester_name','material_id','sku_code','material_name','base_unit','condition_code','quantity','serial_ids','serials','is_current_scrap','state','next_reference','applications','recovery_posting'];
export function source(value:unknown,who:Identity,k:Stage,expected?:string):Source {
  const r=object(value,fields);identity(r,who,k);
  if(r.availability!=='verified'||r.result_scope!=='verified_scrap_recovery_references'||r.write_authorization_provided!==false||r.stock_effect!=='none'||!Number.isSafeInteger(r.observed_ledger_cursor)||Number(r.observed_ledger_cursor)<1)fail();
  for(const key of ['root_disposition_id','operation_id','line_id','owner_org_id','requester_person_id','material_id'])id(r[key]);
  for(const key of ['operation_no','requester_name','sku_code','material_name','base_unit'])if(typeof r[key]!=='string'||!r[key]||String(r[key]).length>1000)fail();
  if(!['new','used','damaged'].includes(String(r.condition_code))||typeof r.quantity!=='string'||!/^(?:0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(r.quantity)||!/[1-9]/.test(r.quantity)||typeof r.is_current_scrap!=='boolean')fail();
  const binding=ref(r.scrap_reference),s=state(r.state),serials=ids(r.serial_ids),n=next(r.next_reference,k,binding);
  if(!Array.isArray(r.serials)||r.serials.length!==serials.length)fail();
  const details=r.serials.map((v,i)=>{const d=object(v,['serial_id','serial_no','qr_code']);
    if(id(d.serial_id)!==serials[i]||typeof d.serial_no!=='string'||!d.serial_no||d.serial_no.length>200||typeof d.qr_code!=='string'||!d.qr_code||d.qr_code.length>250)fail();
    return {...d} as Serial;});
  if(new Set(details.map(d=>d.serial_no)).size!==details.length||new Set(details.map(d=>d.qr_code)).size!==details.length)fail();
  if(expected&&binding.scrap_line_id!==id(expected))fail();
  if(serials.length&&BigInt(r.quantity.replace('.',''))!==BigInt(serials.length)*1000n)fail();
  if(!Array.isArray(r.applications)||r.applications.length>1000)fail();
  const seen=new Set<string>();let count=0;
  const applications=r.applications.map(value=>{
    const h=object(value,['application','evidence_file_ids','regional_reviews','headquarters_reviews','status']);
    const a=fact('apply',h.application) as ReviewFact,evidence=ids(h.evidence_file_ids,20);if(!evidence.length)fail();
    function checked(f:ReviewFact){if(f.scrap_line_id!==binding.scrap_line_id||f.recovery_request_id!==a.fact_id||seen.has(f.fact_id))fail();seen.add(f.fact_id);if(++count>1000)fail();return f;}
    checked(a);const review=(v:unknown,t:'regional'|'headquarters')=>{if(!Array.isArray(v)||v.length>1000)fail();return v.map(x=>checked(fact(t,x) as ReviewFact));};
    const regional=review(h.regional_reviews,'regional'),hq=review(h.headquarters_reviews,'headquarters');
    if(regional.some(f=>f.actor_person_id===a.actor_person_id)||hq.some(f=>{const prior=regional.find(p=>p.fact_id===f.regional_review_id);return !prior||prior.decision!=='verified'||f.actor_person_id===a.actor_person_id||f.actor_person_id===prior.actor_person_id;}))fail();
    return {application:a,evidence_file_ids:evidence,regional_reviews:regional,headquarters_reviews:hq,status:state(h.status)};
  });
  const posting=r.recovery_posting===null?null:fact('execute',r.recovery_posting) as RecoveryFact;
  if((s==='recovered')!==!!posting||r.is_current_scrap===!!posting)fail();
  if(posting&&(posting.root_disposition_id!==r.root_disposition_id||posting.operation_id!==r.operation_id||posting.line_id!==r.line_id||posting.requester_person_id!==r.requester_person_id||posting.quantity!==r.quantity))fail();
  if(n){
    if(posting||!r.is_current_scrap)fail();
    if(n.stage==='apply'){if(!['awaiting_application','needs_evidence'].includes(s))fail();}
    else{
      const h=applications.find(h=>h.application.fact_id===n.recovery_request_id);
      if(!h||h.application.request_hash!==n.expected_request_hash)fail();
      if(n.stage==='regional'&&s!=='awaiting_regional')fail();
      if(n.stage==='headquarters'&&(s!=='awaiting_headquarters'||!h.regional_reviews.some(f=>f.fact_id===n.regional_review_id&&f.request_hash===n.expected_regional_hash&&f.decision==='verified')))fail();
      if(n.stage==='execute'&&(s!=='approved_pending_execution'||!h.headquarters_reviews.some(f=>f.fact_id===n.headquarters_review_id&&f.request_hash===n.expected_headquarters_hash&&f.decision==='approve')))fail();
    }
  }
  return {...r,scrap_reference:binding,serial_ids:serials,serials:details,next_reference:n,applications,recovery_posting:posting} as Source;
}
export function queue(value:unknown,who:Identity,k:Stage,after?:string|null):Queue {
  const r=object(value,['schema_version','person_id','authorization_version','requested_stage','queried_at','items','next_after_id']);identity(r,who,k);
  if(!Array.isArray(r.items)||r.items.length>5)fail();
  let prior=after? id(after):null;
  const items=r.items.map(v=>{
    const blocked=!!v&&typeof v==='object'&&(v as {availability?:unknown}).availability==='blocked';
    const item=blocked?object(v,['availability','scrap_line_id']):source(v,who,k);
    const key=blocked?id((item as Record<string,unknown>).scrap_line_id):(item as Source).scrap_reference.scrap_line_id;
    if(prior&&key<=prior)fail();prior=key;
    return item as Queue['items'][number];
  });
  if(r.next_after_id!==null&&(id(r.next_after_id)!==prior||items.length!==5))fail();
  return {...r,items} as Queue;
}
