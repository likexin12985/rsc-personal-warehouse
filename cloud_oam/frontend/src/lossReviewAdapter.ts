import { canonical, detail, fail, hash, id, identity, object, queue,
  type Evidence, type Identity, type Pending, type Queue, type Report, type Stage } from './formalLossReview';
import type { Context, Transport } from './lossReviewRecovery';
type Requester = (path:string,init?:RequestInit)=>Promise<unknown>;
export type Adapter = Transport & {
  list(stage:Stage,view:'pending'|'all',after?:string|null):Promise<Queue>;
  read(stage:Stage,operation:string):Promise<Report>;
  download(stage:Stage,file:Evidence):Promise<{url:string;expires_at:string}>;
};
const base='/v1/stock-operations/loss-reports';
const headers={'Cache-Control':'no-store',Pragma:'no-cache'};
const noCache:RequestInit={cache:'no-store',headers};
export function createAdapter(personId:string,requestNoReplay:Requester):Adapter {
  const expectedPerson=id(personId);
  async function context(stage:Stage):Promise<Context>{
    const me=object(await requestNoReplay('/auth/me',noCache),['person_id','name','employee_no','organization_code','organization_name','account_status','employment_status','access_mode','authorization_version','role_codes']);
    const access=object(await requestNoReplay('/access/context',noCache),['person_id','account_status','employment_status','authorization_version','access_mode','role_codes','assignments','permissions']);
    const a=identity(me),b=identity(access);
    if(a.person_id!==expectedPerson||canonical(a)!==canonical(b)||[me,access].some(x=>x.account_status!=='active'||x.employment_status!=='active'||x.access_mode!=='active'))fail('登录身份已变化，请重新进入审批页');
    const roles=access.role_codes;
    if(!Array.isArray(roles)||!roles.length||roles.some(r=>!['admin','provincial_manager','technician','star_headquarters_approver'].includes(r))||new Set(roles).size!==roles.length||!Array.isArray(me.role_codes)||canonical([...roles].sort())!==canonical([...me.role_codes].sort()))fail();
    if(!Array.isArray(access.assignments)||access.assignments.length>1000||!Array.isArray(access.permissions)||access.permissions.length>1000)fail();
    const assignments=access.assignments.map(v=>object(v,['assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']));
    const permissions=access.permissions.map(v=>object(v,['resource','action','field_code']));
    for(const g of assignments){id(g.assignment_id);if(typeof g.scope_id!=='string'||typeof g.valid_from!=='string'||!Number.isFinite(Date.parse(g.valid_from)))fail();if(g.valid_to!==null&&(typeof g.valid_to!=='string'||!Number.isFinite(Date.parse(g.valid_to))))fail();}
    const role=stage==='regional'?'provincial_manager':'admin';
    const assigned=roles.includes(role)&&assignments.some(g=>g.role_code===role&&g.scope_type===(stage==='regional'?'organization':'national')&&(stage==='regional'?typeof g.scope_id==='string':g.scope_id==='*'));
    const allows=(action:string)=>assigned&&permissions.some(p=>p.resource==='stock_operation'&&p.action===action&&p.field_code==='');
    const sort=(rows:unknown[])=>[...rows].sort((a,b)=>canonical(a)<canonical(b)?-1:1);
    return {...a,stage,authority_hash:await hash({assignments:sort(assignments),permissions:sort(permissions),roles:[...roles].sort()}),can_read:allows('read'),can_write:allows(stage==='regional'?'review_loss_regional':'finalize_loss')};
  }
  async function checked(stage:Stage){const c=await context(stage);if(!c.can_read)fail('当前没有该阶段的审批查看权限');return c;}
  async function stable(before:Context){const after=await context(before.stage);if(canonical(before)!==canonical(after))fail('查询期间身份或授权范围变化，请刷新');}
  function post(p:Pending,suffix:string,body:unknown){return requestNoReplay(`${base}/${p.command.stage}-reviews${suffix}`,{method:'POST',cache:'no-store',headers:{...headers,'Content-Type':'application/json','X-Request-ID':p.command.original.request_id,'Idempotency-Key':p.command.original.idempotency_key},body:JSON.stringify(body)});}
  return {
    context,
    async detail(stage,operation,current:Identity){return detail(await requestNoReplay(`${base}/reviews/${stage}/${id(operation)}`,noCache),current,stage,operation);},
    async list(stage,view,after=null){const before=await checked(stage);const params=new URLSearchParams({view,limit:'10'});if(after)params.set('after_id',id(after));const result=queue(await requestNoReplay(`${base}/reviews/${stage}?${params}`,noCache),before,stage,view);if(after&&result.next_after_id&&result.next_after_id<=after)fail('分页游标未前进');await stable(before);return result;},
    async read(stage,operation){const before=await checked(stage);const result=detail(await requestNoReplay(`${base}/reviews/${stage}/${id(operation)}`,noCache),before,stage,operation);await stable(before);return result;},
    lookup(p){const c=p.command,o=c.original;return post(p,'/request-lookup',{operation_id:o.operation_id,operator_person_id:p.person_id,expected_submission_plan_hash:o.expected_submission_plan_hash,request_id:o.request_id,idempotency_key:o.idempotency_key,request_hash:c.request_hash});},
    submit(p){return post(p,'',p.command);},
    seal(p){return post(p,'/request-seal',p.command);},
    async download(stage,file){const before=await checked(stage);const r=object(await requestNoReplay(`/v1/files/${id(file.file_id)}/download-intent`,{...noCache,headers:{...headers,'X-Request-ID':crypto.randomUUID()}}),['schema_version','file_id','purpose','status','download']);
      if(r.schema_version!=='1.0'||r.file_id!==file.file_id||r.purpose!=='stock_loss_evidence'||r.status!=='available')fail();const d=object(r.download,['method','url','expires_at']);
      if(d.method!=='GET'||typeof d.url!=='string'||d.url.length>8192||typeof d.expires_at!=='string')fail();const url=new URL(d.url),expiry=Date.parse(d.expires_at);await stable(before);
      if(url.protocol!=='https:'||!url.hostname||url.username||url.password||url.hash||!Number.isFinite(expiry)||expiry<=Date.now()||expiry>Date.now()+630000)fail('照片临时链接无效，请重新申请');return {url:d.url,expires_at:d.expires_at};
    },
  };
}
