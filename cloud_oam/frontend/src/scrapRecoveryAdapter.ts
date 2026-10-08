/** One-shot recovery commands; all resumed requests use their saved original only. */
import { canonical, hash, identity } from './formalLossReview';
import { command, digest, id, object, prepare, verifyPending, type Pending, type Command, type ExecuteCommand } from './formalScrapCommands';
import { fail, type Kind } from './formalScrapFacts';
import type { Context, Transport } from './formalScrapRecovery';
import { source, queue, stage, fresh, type Stage, type Source, type Queue, type Serial } from './scrapRecoverySources';
export type Preview={planning_status:'preview_only';stock_effect:'none';root_disposition_id:string;original_execution_id:string;
  original_transaction_id:string;original_movement_id:string;source_account_id:null;target_account_id:string;
  target_condition:string;quantity:string;serial_ids:string[];plan_hash:string;checked_at:string};
export type Input={reason:string;evidence?:readonly string[];decision?:string;verifiedSerials?:readonly Serial[]};
export type Prepared={pending:Pending;source:Source;preview:Preview|null};
export type Adapter=Transport & {list(k:Stage,after?:string|null):Promise<Queue>;read(k:Stage,scrap:string):Promise<Source>;
  prepare(k:Stage,scrap:string,input:Input):Promise<Prepared>;download(k:Stage,scrap:string,file:string):Promise<{url:string;expires_at:string}>};
type Requester=(path:string,init?:RequestInit)=>Promise<unknown>;
const base='/v1/stock-operations/loss-reports/scraps/recovery';
const headers={'Cache-Control':'no-store',Pragma:'no-cache','Content-Type':'application/json'};
const noCache:RequestInit={cache:'no-store',headers};
const endpoints={apply:'applications',regional:'regional-reviews',headquarters:'headquarters-reviews',execute:'executions'};
const actions={apply:'apply_scrap_recovery',regional:'review_scrap_recovery_region',headquarters:'review_scrap_recovery_headquarters',execute:'execute_scrap_recovery'};
const permissions={apply:'apply_scrap_recovery',regional:'review_scrap_recovery_regional',headquarters:'review_scrap_recovery_headquarters',execute:'execute_scrap_recovery'};
export function createAdapter(personId:string,requestNoReplay:Requester):Adapter {
  const person=id(personId);
  const physicalChecks=new Map<string,string>();
  async function context(kind:Kind):Promise<Context>{
    const k=stage(kind),me=object(await requestNoReplay('/auth/me',noCache),['person_id','name','employee_no','organization_code','organization_name','account_status','employment_status','access_mode','authorization_version','role_codes']);
    const access=object(await requestNoReplay('/access/context',noCache),['person_id','account_status','employment_status','authorization_version','access_mode','role_codes','assignments','permissions']);
    const a=identity(me),b=identity(access);if(a.person_id!==person||canonical(a)!==canonical(b)||[me,access].some(v=>v.account_status!=='active'||v.employment_status!=='active'||v.access_mode!=='active'))fail();
    const roles=access.role_codes;if(!Array.isArray(roles)||!roles.length||roles.some(r=>!['technician','provincial_manager','admin','star_headquarters_approver'].includes(r))||new Set(roles).size!==roles.length||!Array.isArray(me.role_codes)||canonical([...roles].sort())!==canonical([...me.role_codes].sort()))fail();
    if(!Array.isArray(access.assignments)||access.assignments.length>1000||!Array.isArray(access.permissions)||access.permissions.length>1000)fail();
    const assignments=access.assignments.map(v=>object(v,['assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']));
    const grants=access.permissions.map(v=>object(v,['resource','action','field_code']));
    const role=k==='apply'?'technician':k==='regional'?'provincial_manager':'admin',scope=k==='apply'?'person':k==='regional'?'organization':'national';
    const now=Date.now();
    const assigned=roles.includes(role)&&assignments.some(g=>{
      id(g.assignment_id);if(typeof g.valid_from!=='string'||!Number.isFinite(Date.parse(g.valid_from))||(g.valid_to!==null&&(typeof g.valid_to!=='string'||!Number.isFinite(Date.parse(g.valid_to)))))fail();
      if(g.role_code!==role||g.scope_type!==scope)return false;
      if(scope==='organization')id(g.scope_id);else if(g.scope_id!==(scope==='person'?person:'*'))return false;
      return Date.parse(g.valid_from)<=now&&(g.valid_to===null||Date.parse(g.valid_to as string)>now);
    });
    const allows=(action:string)=>assigned&&grants.some(g=>g.resource==='stock_operation'&&g.action===action&&g.field_code==='');
    const sort=(rows:unknown[])=>[...rows].sort((a,b)=>canonical(a).localeCompare(canonical(b)));
    return {...a,kind:k,authority_hash:await hash({assignments:sort(assignments),permissions:sort(grants),roles:[...roles].sort()}),can_read:allows('read'),can_write:allows(permissions[k])};
  }
  async function checked(k:Stage,write=false){const c=await context(k);if(!c.can_read||write&&!c.can_write)fail();return c;}
  async function stable(before:Context){if(canonical(before)!==canonical(await context(before.kind)))fail();}
  async function read(k:Stage,scrap:string){const before=await checked(k);const s=source(await requestNoReplay(`${base}/sources/${id(scrap)}?stage=${k}`,noCache),before,k,scrap);await stable(before);return s;}
  async function plan(c:ExecuteCommand,s:Source):Promise<Preview>{
    const {action:_action,expected_plan_hash:_hash,request_id:_request,idempotency_key:_key,...body}=c;
    const r=object(await requestNoReplay(base+'/executions/preview',{...noCache,method:'POST',body:JSON.stringify(body)}),['planning_status','stock_effect','root_disposition_id','original_execution_id','original_transaction_id','original_movement_id','source_account_id','target_account_id','target_condition','quantity','serial_ids','plan_hash','checked_at']);
    for(const key of ['original_execution_id','original_transaction_id','original_movement_id','target_account_id'])id(r[key]);digest(r.plan_hash);fresh(r.checked_at);
    if(r.planning_status!=='preview_only'||r.stock_effect!=='none'||r.root_disposition_id!==s.root_disposition_id||r.source_account_id!==null||r.target_condition!==s.condition_code||r.quantity!==s.quantity||!Array.isArray(r.serial_ids)||canonical([...r.serial_ids].sort())!==canonical([...s.serial_ids].sort()))fail();
    return {...r} as Preview;
  }
  async function post(value:Pending,suffix:string){const p=await verifyPending(value),k=stage(p.kind);if(p.person_id!==person)fail();
    return requestNoReplay(`${base}/${endpoints[k]}${suffix}`,{...noCache,method:'POST',headers:{...headers,'X-Request-ID':p.original.request_id,'Idempotency-Key':p.original.idempotency_key},body:JSON.stringify(suffix?{operator_person_id:person,original:p.original}:p.original)});}
  function reference(p:Pending){const c=p.original;if('execution_reason' in c)fail();
    if(c.action==='apply_scrap_recovery')return {stage:'apply',source:c.source};
    const binding={source:c.source,recovery_request_id:c.recovery_request_id,expected_request_hash:c.expected_request_hash};
    if(c.action==='review_scrap_recovery_region')return {stage:'regional',...binding};
    if(c.action==='review_scrap_recovery_headquarters')return {stage:'headquarters',...binding,regional_review_id:c.regional_review_id,expected_regional_hash:c.expected_regional_hash};
    return {stage:'execute',...binding,headquarters_review_id:c.headquarters_review_id,expected_headquarters_hash:c.expected_headquarters_hash};
  }
  return {context,read,
    async list(k,after=null){const before=await checked(k),params=new URLSearchParams({stage:k,limit:'5'});if(after)params.set('after_id',id(after));
      const result=queue(await requestNoReplay(`${base}/sources?${params}`,noCache),before,k,after);await stable(before);return result;},
    async prepare(k,scrap,input){
      const before=await checked(k,true),s=await read(k,scrap);if(s.authorization_version!==before.authorization_version||!s.next_reference)fail();
      if(s.serials.length&&(k==='apply'||k==='execute'||k==='regional'&&input.decision==='verified')&&canonical(input.verifiedSerials)!==canonical(s.serials))fail();
      const {stage:_stage,...ref}=s.next_reference;
      let c=command(k,{...ref,action:actions[k],reason:input.reason,request_id:crypto.randomUUID(),idempotency_key:crypto.randomUUID(),
        ...(k==='apply'?{evidence_file_ids:[...(input.evidence??[])]}:k==='execute'?{expected_plan_hash:'0'.repeat(64)}:{decision:input.decision})});
      const preview=k==='execute'?await plan(c as ExecuteCommand,s):null;
      if(preview)c={...c,expected_plan_hash:preview.plan_hash} as Command;
      await stable(before);
      const pending=await prepare(person,before.authorization_version,k,c);
      if(s.serials.length&&(k==='apply'||k==='execute'||k==='regional'&&input.decision==='verified'))physicalChecks.set(c.request_id,canonical(s.serials));
      return {pending,source:s,preview};
    },
    async verifySource(value,current){const p=await verifyPending(value),k=stage(p.kind);if(p.person_id!==person||current.person_id!==person||current.kind!==k)fail();
      const c=p.original;if('execution_reason' in c)fail();const s=await read(k,c.source.scrap_line_id);
      if(s.authorization_version!==current.authorization_version||canonical(s.next_reference)!==canonical(reference(p)))fail();
      if(s.serials.length&&(k==='apply'||k==='execute'||c.action==='review_scrap_recovery_region'&&c.decision==='verified')&&physicalChecks.get(c.request_id)!==canonical(s.serials))fail();
      if(c.action==='execute_scrap_recovery'&&(await plan(c,s)).plan_hash!==c.expected_plan_hash)fail();
    },
    submit:p=>post(p,''),lookup:p=>post(p,'/request-lookup'),seal:p=>post(p,'/request-seal'),
    async download(k,scrap,file){const before=await checked(k),s=await read(k,scrap);if(!s.applications.some(a=>a.evidence_file_ids.includes(id(file))))fail();
      const r=object(await requestNoReplay(`/v1/files/${file}/download-intent`,{...noCache,headers:{...headers,'X-Request-ID':crypto.randomUUID()}}),['file_id','purpose','download']);
      if(r.file_id!==file||r.purpose!=='stock_loss_evidence')fail();const d=object(r.download,['url','expires_at']);
      if(typeof d.url!=='string'||typeof d.expires_at!=='string')fail();const url=new URL(d.url),expiry=Date.parse(d.expires_at);
      if(url.protocol!=='https:'||!url.hostname||url.username||url.password||url.hash||!Number.isFinite(expiry)||expiry<=Date.now()||expiry>Date.now()+630000)fail();
      await stable(before);return {url:d.url,expires_at:d.expires_at};
    },
  };
}
