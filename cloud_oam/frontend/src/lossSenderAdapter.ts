/** Inject apiNoReplay at the page boundary; no transport retry or outcome inference here. */
import {canonical,fail,id,integer,object,text,timestamp,digest} from './formalReturnReceiving';
import {directory,detail,outboundOptions,shipmentOptions} from './formalLossSenderReads';
import {input,preview,type Kind} from './formalLossSenderCommands';
import {pending,type Pending,type Transport,type Context} from './lossSenderRecovery';
export type Requester=(path:string,init?:RequestInit)=>Promise<unknown>;
const BASE='/v1/stock-operations/loss-reports/returns';
const headers={'Cache-Control':'no-store',Pragma:'no-cache'};
const read:RequestInit={cache:'no-store',headers};
const ROLES=['admin','provincial_manager','technician','star_headquarters_approver'];
async function hash(value:unknown){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)));return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');}
export function createAdapter(person:string,kind:Kind,request:Requester){
 const personId=id(person);if(kind!=='outbound_return'&&kind!=='ship_return')fail('发件类型无效');
 const suffix=kind==='outbound_return'?'outbounds':'shipments';
  async function context(): Promise<Context> {
    const me = object(await request('/auth/me', read), ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes']);
    const access = object(await request('/access/context', read), ['person_id', 'account_status', 'employment_status', 'authorization_version', 'access_mode', 'role_codes', 'assignments', 'permissions']);
    if ([me, access].some(r => r.person_id !== personId || r.account_status !== 'active' || r.employment_status !== 'active' || r.access_mode !== 'active') || integer(me.authorization_version, 1) !== integer(access.authorization_version, 1)) fail('当前发件身份已变化');
    const roles = access.role_codes;
    if (!Array.isArray(roles) || roles.some(r => !ROLES.includes(r)) || new Set(roles).size !== roles.length || !Array.isArray(me.role_codes) || canonical([...roles].sort()) !== canonical([...me.role_codes].sort())) fail();
    if (!Array.isArray(access.assignments) || access.assignments.length > 1000 || !Array.isArray(access.permissions) || access.permissions.length > 1000) fail();
    const assignments = access.assignments.map(v => {
      const r = object(v, ['assignment_id', 'role_code', 'scope_type', 'scope_id', 'valid_from', 'valid_to']);
      id(r.assignment_id); if (!ROLES.includes(String(r.role_code))) fail(); text(r.scope_type, 100); text(r.scope_id, 100);
      timestamp(r.valid_from); if (r.valid_to !== null) timestamp(r.valid_to); return r;
    });
    const permissions = access.permissions.map(v => {
      const r = object(v, ['resource', 'action', 'field_code']); text(r.resource, 100); text(r.action, 100); if (typeof r.field_code !== 'string') fail(); return r;
    });
    const now = Date.now();
    const assigned = assignments.some(r => roles.includes(r.role_code) && ['admin', 'provincial_manager', 'technician'].includes(String(r.role_code))
      && Date.parse(String(r.valid_from)) <= now && (r.valid_to === null || Date.parse(String(r.valid_to)) > now));
    const allows = (action: string) => assigned && permissions.some(r => r.resource === 'stock_operation' && r.action === action && r.field_code === '');
    const sorted = (rows: unknown[]) => [...rows].sort((a, b) => canonical(a) < canonical(b) ? -1 : canonical(a) > canonical(b) ? 1 : 0);
    return { person_id: personId, authorization_version: integer(me.authorization_version, 1), can_read: allows('read'), can_write: allows(kind), authority_hash: await hash({ assignments: sorted(assignments), permissions: sorted(permissions), roles: [...roles].sort() }) };
  }
 const checked=(value:Pending)=>{const p=pending(value);if(p.person_id!==personId||p.kind!==kind)fail('原发件请求不属于当前人员或操作类型');return p;};
 const route=(p:Pending)=>`${BASE}/${p.operation_id}/${suffix}`;
 const post=(path:string,body:unknown,extra:Record<string,string>={})=>request(path,{method:'POST',cache:'no-store',headers:{...headers,'Content-Type':'application/json',...extra},body:JSON.stringify(body)});
 async function readable(write=false){const c=await context();if(!c.can_read||(write&&!c.can_write))fail('当前没有所需发件权限，已有请求可按读权限回查');return c;}
 async function stable(before:Context){if(canonical(before)!==canonical(await context()))fail('查询期间身份或权限变化');}
 const transport:Transport={context,
  detail(value){const p=checked(value);return request(`${BASE}/${p.operation_id}`,read);},
  options(value){return request(`${route(checked(value))}/options`,read);},
  preview(value){const p=checked(value),{expected_plan_hash:_plan,request_id:_request,idempotency_key:_key,...body}=p.command;return post(`${route(p)}/preview`,body);},
  submit(value){const p=checked(value);return post(route(p),p.command,{'X-Request-ID':p.command.request_id,'Idempotency-Key':p.command.idempotency_key});},
  lookup(value){const p=checked(value);return post(`${route(p)}/requests/lookup`,p.command);},
  seal(value){const p=checked(value);return post(`${route(p)}/requests/seal`,p.command,{'X-Request-ID':p.command.request_id});},
 };
 return {...transport,
  async list(after:string|null=null,snapshotHash?:string){
   const before=await readable(),params=new URLSearchParams({limit:'20'});
   if(after!==null){params.set('after_id',id(after));if(!snapshotHash)fail('续页缺少快照');}
   if(snapshotHash!==undefined)params.set('snapshot_hash',digest(snapshotHash));
   const result=directory(await request(`${BASE}/my-sending?${params}`,read),before,20,after,snapshotHash);await stable(before);return result;
  },
  async readDetail(operationId:string){
   const before=await readable(),result=detail(await request(`${BASE}/${id(operationId)}`,read),before,operationId);await stable(before);return result;
  },
  async choices(operationId:string){
   const before=await readable(true),key=id(operationId),original=detail(await request(`${BASE}/${key}`,read),before,key);
   const options=(kind==='outbound_return'?outboundOptions:shipmentOptions)(await request(`${BASE}/${key}/${suffix}/options`,read),before,original);
   await stable(before);return {detail:original,options};
  },
  async prepare(operationId:string,value:unknown){
   const before=await readable(true),key=id(operationId),body=input(kind,value,personId);
   const original=detail(await request(`${BASE}/${key}`,read),before,key);
   const options=(kind==='outbound_return'?outboundOptions:shipmentOptions)(await request(`${BASE}/${key}/${suffix}/options`,read),before,original);
   const prepared=await preview(kind,await post(`${BASE}/${key}/${suffix}/preview`,body),body,options,original);
   await stable(before);return {detail:original,options,preview:prepared};
  },
 };
}
export type Adapter=ReturnType<typeof createAdapter>;
