import { expect, it, vi } from 'vitest';
import { createAdapter } from './scrapOriginalAdapter';
import { fixture } from './lossExecutionFixtures';
import { createStore, recover, submit } from './formalScrapRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import outcomes from './test-fixtures/stock-scrap/committed-quantity-results.json';

function world(){
  // Synthetic source based on the existing source DTO, not native PG evidence.
  const source=structuredClone(fixture.source);source.decisions[0].disposition='scrap';
  source.report.headquarters_review!.decisions[0].disposition='scrap';
  const identity={person_id:source.person_id,authorization_version:source.authorization_version,
    account_status:'active',employment_status:'active',access_mode:'active',role_codes:['admin']};
  const me={...identity,name:'测试',employee_no:'SYNTHETIC',organization_code:'TEST',organization_name:'测试'};
  const access={...identity,assignments:[{assignment_id:crypto.randomUUID(),role_code:'admin',scope_type:'national',scope_id:'*',
    valid_from:'2020-01-01T00:00:00Z',valid_to:null}],permissions:['read','dispose_loss'].map(action=>({resource:'stock_operation',action,field_code:''}))};
  const d=source.decisions[0],line=source.report.lines[0];
  const preview={planning_status:'preview_only',stock_effect:'none',operation_id:source.report.operation_id,line_id:d.line_id,
    decision_id:d.headquarters_decision_id,predecessor_reversal_id:null,source_account_id:crypto.randomUUID(),target_account_id:null,
    source_condition:line.condition_code,quantity:line.quantity,serial_ids:line.serials.map(sn=>sn.serial_id),plan_hash:'a'.repeat(64),checked_at:new Date().toISOString()};
  const request=vi.fn(async(path:string,_init?:RequestInit):Promise<unknown>=>{
    if(path==='/auth/me')return me;if(path==='/access/context')return access;
    if(path.includes('/execution-sources/'))return source;if(path.endsWith('/preview'))return preview;
    throw new Error('connection lost');
  });
  const adapter=createAdapter(identity.person_id,request);
  const prepare=()=>adapter.prepare(source.report.operation_id,d.headquarters_decision_id,'已核对报废实物',[crypto.randomUUID()]);
  return {source,me,access,preview,request,adapter,prepare};
}
it('previews exact approved scrap without sending a business write',async()=>{
  const w=world(),p=await w.prepare();expect(p.pending.kind).toBe('original');
  const writes=w.request.mock.calls.filter(([,i])=>i?.method==='POST');expect(writes).toHaveLength(1);
  expect(writes[0][0]).toMatch(/\/scraps\/originals\/preview$/);
  expect(JSON.parse(writes[0][1]!.body as string).source).toEqual({kind:'original',...w.source.decisions[0].preview_reference});
});
it.each(['quantity','decision_id','operation_id','line_id','source_condition','predecessor_reversal_id','target_account_id','stock_effect','serial_ids','checked_at'])('rejects mismatched preview %s',async field=>{
  const w=world();Object.assign(w.preview,{[field]:field==='serial_ids'?[crypto.randomUUID()]:field==='checked_at'?'2000-01-01T00:00:00Z':'wrong'});
  await expect(w.prepare()).rejects.toThrow();
});
it.each(['unapproved','posted','no-write','wrong-person','changed-authority'])('blocks new preparation on %s',async fault=>{
  const w=world();if(fault==='unapproved')w.source.decisions[0].disposition='convert_used';
  if(fault==='posted')w.source.decisions[0].original_posting=fixture.after.decisions[0].original_posting;
  if(fault==='no-write')w.access.permissions.pop();if(fault==='wrong-person')w.me.person_id=crypto.randomUUID();
  if(fault==='changed-authority'){const real=w.request.getMockImplementation()!;w.request.mockImplementation(async(p,i)=>{const r=await real(p,i);if(p.endsWith('/preview'))w.access.permissions.pop();return r;});}
  await expect(w.prepare()).rejects.toThrow();
});
it('persists before a single failed write and recovers after remount without another preview or write grant',async()=>{
  const w=world(),p=(await w.prepare()).pending,store=createStore(new MemoryStorage(),locks());
  await expect(submit(w.adapter,store,p)).rejects.toThrow('connection lost');expect(store.list(p.person_id)).toEqual([p]);
  const posted=w.request.mock.calls.filter(([path])=>path.endsWith('/scraps/originals'));expect(posted).toHaveLength(1);
  expect(JSON.parse(posted[0][1]!.body as string)).toEqual(p.original);
  if(!('expected_plan_hash' in p.original))throw new Error('expected scrap plan');
  const expected={...outcomes.samples.find(s=>s.kind==='original')!.fact,request_id:p.original.request_id,
    request_hash:p.request_hash,plan_hash:p.original.expected_plan_hash};
  w.access.permissions.pop();const real=w.request.getMockImplementation()!;
  w.request.mockImplementation(async(path,init)=>{
    if(path.endsWith('/request-lookup'))return {request_id:p.original.request_id,request_hash:p.request_hash,
      request_state:'found',result_scope:'historical_original_outcome',retry_allowed:false,result:expected};
    if(path.endsWith('/preview')||path.endsWith('/scraps/originals'))throw new Error('must not write or preview');
    return real(path,init);
  });
  const before=w.request.mock.calls.length;
  expect((await recover(createAdapter(p.person_id,w.request),store,p)).status).toBe('found');
  expect(store.list(p.person_id)).toEqual([]);
  expect(w.request.mock.calls.slice(before).filter(([,i])=>i?.method==='POST').map(([path])=>path)).toEqual(['/v1/stock-operations/loss-reports/scraps/originals/request-lookup']);
});
it('rechecks the selected source and plan before saving or writing',async()=>{
  const w=world(),p=(await w.prepare()).pending,store=createStore(new MemoryStorage(),locks());w.preview.plan_hash='b'.repeat(64);
  await expect(submit(w.adapter,store,p)).rejects.toThrow();expect(store.list(p.person_id)).toEqual([]);
  expect(w.request.mock.calls.some(([path])=>path.endsWith('/scraps/originals'))).toBe(false);
});
it.each(['submit','lookup','seal'] as const)('sends %s once with private headers and exact original body',async method=>{
  const w=world(),p=(await w.prepare()).pending;w.request.mockClear();
  await expect(w.adapter[method](p)).rejects.toThrow('connection lost');expect(w.request).toHaveBeenCalledTimes(1);
  const [path,init]=w.request.mock.calls[0];expect(path).toBe('/v1/stock-operations/loss-reports/scraps/originals'+({submit:'',lookup:'/request-lookup',seal:'/request-seal'}[method]));
  expect(init).toMatchObject({cache:'no-store',method:'POST',headers:{'X-Request-ID':p.original.request_id,'Idempotency-Key':p.original.idempotency_key}});
  expect(JSON.parse(init!.body as string)).toEqual(method==='submit'?p.original:{operator_person_id:p.person_id,original:p.original});
});
