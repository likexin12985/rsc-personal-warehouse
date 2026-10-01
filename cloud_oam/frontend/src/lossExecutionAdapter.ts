import { canonical, hash, id, identity, object, type Queue } from './formalLossReview';
import { fail, intent, prepare, sources, type Pending, type Sources } from './lossExecutionContracts';
import { createAdapter as createReviewAdapter } from './lossReviewAdapter';
import type { Context, Transport } from './lossExecutionRecovery';
type Requester=(path:string,init?:RequestInit)=>Promise<unknown>;
export type Adapter=Transport & {list(after?:string|null):Promise<Queue>;read(operation:string):Promise<Sources>;prepare(operation:string,decision:string,route?:string):Promise<Pending>};
const base='/v1/stock-operations/loss-reports';
const headers={'Cache-Control':'no-store',Pragma:'no-cache'};
const noCache:RequestInit={cache:'no-store',headers};
export function createAdapter(personId:string,requestNoReplay:Requester):Adapter {
  const expectedPerson=id(personId),reviews=createReviewAdapter(personId,requestNoReplay);
  async function context():Promise<Context>{
    const me=object(await requestNoReplay('/auth/me',noCache),['person_id','name','employee_no','organization_code','organization_name','account_status','employment_status','access_mode','authorization_version','role_codes']);
    const access=object(await requestNoReplay('/access/context',noCache),['person_id','account_status','employment_status','authorization_version','access_mode','role_codes','assignments','permissions']);
    const a=identity(me),b=identity(access);
    if(a.person_id!==expectedPerson||canonical(a)!==canonical(b)||[me,access].some(x=>x.account_status!=='active'||x.employment_status!=='active'||x.access_mode!=='active'))fail('登录身份已变化，请重新进入处置页');
    const roles=access.role_codes;
    if(!Array.isArray(roles)||!roles.length||roles.some(r=>!['admin','provincial_manager','technician','star_headquarters_approver'].includes(r))||new Set(roles).size!==roles.length||!Array.isArray(me.role_codes)||canonical([...roles].sort())!==canonical([...me.role_codes].sort()))fail();
    if(!Array.isArray(access.assignments)||access.assignments.length>1000||!Array.isArray(access.permissions)||access.permissions.length>1000)fail();
    const assignments=access.assignments.map(v=>object(v,['assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']));
    const permissions=access.permissions.map(v=>object(v,['resource','action','field_code']));
    for(const g of assignments){id(g.assignment_id);if(typeof g.scope_id!=='string'||typeof g.valid_from!=='string'||!Number.isFinite(Date.parse(g.valid_from)))fail();if(g.valid_to!==null&&(typeof g.valid_to!=='string'||!Number.isFinite(Date.parse(g.valid_to))))fail();}
    const role='admin';
    const now=Date.now();
    const assigned=roles.includes(role)&&assignments.some(g=>g.role_code===role&&g.scope_type==='national'&&g.scope_id==='*'&&Date.parse(g.valid_from as string)<=now&&(g.valid_to===null||Date.parse(g.valid_to as string)>now));
    const allows=(action:string)=>assigned&&permissions.some(p=>p.resource==='stock_operation'&&p.action===action&&p.field_code==='');
    const sort=(rows:unknown[])=>[...rows].sort((a,b)=>canonical(a)<canonical(b)?-1:1);
    return {...a,authority_hash:await hash({assignments:sort(assignments),permissions:sort(permissions),roles:[...roles].sort()}),can_read:allows('read'),can_write:allows('dispose_loss')};
  }

  async function checked(write=false){const c=await context();if(!c.can_read||(write&&!c.can_write))fail('当前缺少报损处置权限');return c;}
  async function stable(before:Context){if(canonical(before)!==canonical(await context()))fail('读取期间身份或授权变化，请重新核验');}
  async function source(operation:string,current:Context){return sources(await requestNoReplay(`${base}/execution-sources/${id(operation)}`,noCache),current,operation);}
  function post(p:Pending,suffix:string,body:unknown){return requestNoReplay(`${base}/${p.flow==='return'?'derived-returns':'dispositions'}${suffix}`,{method:'POST',cache:'no-store',headers:{...headers,'Content-Type':'application/json','X-Request-ID':p.command.request_id,'Idempotency-Key':p.command.idempotency_key},body:JSON.stringify(body)});}
  return {
    context,source,
    async list(after=null){const before=await checked();const result=await reviews.list('headquarters','all',after);await stable(before);return result;},
    async read(operation){const before=await checked();const result=await source(operation,before);await stable(before);return result;},
    async prepare(operation,decision,routeKey){
      const before=await checked(true),s=await source(operation,before),d=s.decisions.find(d=>d.headquarters_decision_id===id(decision));
      if(!d||d.original_posting||d.disposition==='scrap')fail('此批准当前不能执行');
      const flow=d.disposition==='return_to_region'?'return':'disposition';
      const route=s.return_routes.find(r=>r.target_location_id+':'+r.transit_location_id===routeKey);
      if(flow==='return'&&(!route||s.return_routes_status!=='available')||flow==='disposition'&&routeKey)fail('请选择已核验的退回路线');
      const request=intent({...d.preview_reference,...(route?{target_location_id:route.target_location_id,transit_location_id:route.transit_location_id}:{})},flow);
      const preview=await requestNoReplay(`${base}/${flow==='return'?'derived-returns':'dispositions'}/preview`,{...noCache,method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(request)});
      await stable(before);return prepare(s,d,preview,route);
    },
    lookup(p){return post(p,'/request-lookup',p.command);},
    execute(p){return post(p,'',p.command);},
    seal(p){return post(p,'/request-seal',{operator_person_id:p.person_id,original:p.command});},
  };
}
