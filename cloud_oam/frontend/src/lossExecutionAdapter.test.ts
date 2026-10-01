import { expect, it, vi } from 'vitest';
import { createAdapter } from './lossExecutionAdapter';
import { fixtures, fixture, saved } from './lossExecutionFixtures';
const id='10000000-0000-4000-8000-000000000001';
function world(f=fixture){
  const identity={person_id:f.source.person_id,authorization_version:f.source.authorization_version,account_status:'active',employment_status:'active',access_mode:'active',role_codes:['admin']};
  const me={...identity,name:'处置测试',employee_no:'SYNTHETIC',organization_code:'SYNTHETIC',organization_name:'测试组织'};
  const access={...identity,assignments:[{assignment_id:id,role_code:'admin',scope_type:'national',scope_id:'*',valid_from:'2026-01-01T00:00:00Z',valid_to:null as string|null}],permissions:[{resource:'stock_operation',action:'read',field_code:''},{resource:'stock_operation',action:'dispose_loss',field_code:''}]};
  const request=vi.fn(async(path:string,_init?:RequestInit):Promise<unknown>=>{
    if(path==='/auth/me')return me;if(path==='/access/context')return access;
    if(path.includes('/execution-sources/'))return f.source;if(path.endsWith('/preview'))return f.preview;
    throw new Error('Unexpected request: '+path);
  });return {me,access,request,adapter:createAdapter(identity.person_id,request)};
}
it('requires dispose_loss, independently of finalize_loss, and preserves read-only recovery access',async()=>{
  const w=world();expect((await w.adapter.context()).can_write).toBe(true);w.access.permissions[1].action='finalize_loss';const c=await w.adapter.context();expect(c.can_read).toBe(true);expect(c.can_write).toBe(false);
});
it.each(['wrong-person','wrong-version','wrong-roles','inactive'])('rejects changed identity: %s',async fault=>{
  const w=world();if(fault==='wrong-person')w.access.person_id=id;if(fault==='wrong-version')w.access.authorization_version++;if(fault==='wrong-roles')w.me.role_codes=[];if(fault==='inactive')w.access.account_status='disabled';await expect(w.adapter.context()).rejects.toThrow();
});
it('does not infer national authority from an admin role name or unrelated assignments',async()=>{const w=world();w.access.assignments[0].scope_id=id;const c=await w.adapter.context();expect(c.can_read||c.can_write).toBe(false);});
it.each(['expired','future'])('does not enable an assignment outside its validity interval: %s',async kind=>{const w=world();if(kind==='expired')w.access.assignments[0].valid_to='2020-01-01T00:00:00Z';else w.access.assignments[0].valid_from='2099-01-01T00:00:00Z';const c=await w.adapter.context();expect(c.can_read||c.can_write).toBe(false);});
it.each(fixtures)('previews the exact approved intent with no execution: $name',async({data:f})=>{
  const w=world(f),d=f.source.decisions[0],route=f.source.return_routes[0];const result=await w.adapter.prepare(f.source.report.operation_id,d.headquarters_decision_id,route?route.target_location_id+':'+route.transit_location_id:undefined);
  const posts=w.request.mock.calls.filter(([_path,init])=>init?.method==='POST');expect(posts).toHaveLength(1);expect(posts[0][0]).toMatch(/\/preview$/);const {expected_plan_hash:_h,request_id:_r,idempotency_key:_k,...reference}=f.original;expect(JSON.parse(posts[0][1]!.body as string)).toEqual(reference);expect(result.preview.plan_hash).toBe(f.preview.plan_hash);
});
it('does not send a preview for a guessed route',async()=>{const f=fixtures.find(f=>f.name==='quantity-return_to_region.json')!.data,w=world(f);await expect(w.adapter.prepare(f.source.report.operation_id,f.source.decisions[0].headquarters_decision_id,'guessed')).rejects.toThrow();expect(w.request.mock.calls.some(([_p,i])=>i?.method==='POST')).toBe(false);});
it('rejects a source response when authority changes without a version bump',async()=>{const w=world(),request=w.request.getMockImplementation()!;w.request.mockImplementation(async(p,i)=>{const value=await request(p,i);if(p.includes('/execution-sources/'))w.access.assignments[0].scope_id=id;return value;});await expect(w.adapter.read(fixture.source.report.operation_id)).rejects.toThrow();});
it.each(['lookup','execute','seal'] as const)('uses one no-replay call and the exact original command for %s',async method=>{
  const p=await saved(),request=vi.fn(async(_p:string,_i?:RequestInit)=>{throw new Error('connection lost');}),adapter=createAdapter(p.person_id,request);
  await expect(adapter[method](p)).rejects.toThrow('connection lost');expect(request).toHaveBeenCalledTimes(1);const [path,init]=request.mock.calls[0];expect(path).toBe('/v1/stock-operations/loss-reports/dispositions'+({lookup:'/request-lookup',execute:'',seal:'/request-seal'}[method]));
  expect(init).toMatchObject({method:'POST',cache:'no-store',headers:{'X-Request-ID':p.command.request_id,'Idempotency-Key':p.command.idempotency_key}});expect(JSON.parse(init!.body as string)).toEqual(method==='seal'?{operator_person_id:p.person_id,original:p.command}:p.command);
});
