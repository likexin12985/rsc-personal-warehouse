import {test} from 'vitest';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createAdapter} from './lossSenderAdapter';
import {pending,createStore,submit,recover} from './lossSenderRecovery';
function fixture(tracking,kind){return JSON.parse(readFileSync(new URL(`./test-fixtures/loss-sender/h5-write-contracts/${tracking}-${kind}.json`,import.meta.url),'utf8'));}
function api(f){
 const calls=[],me={...f.identity,name:'合成',employee_no:'SYNTHETIC',organization_code:'SYNTHETIC',organization_name:'合成',account_status:'active',employment_status:'active',access_mode:'active',role_codes:['technician']};
 const access={...f.identity,account_status:'active',employment_status:'active',access_mode:'active',role_codes:['technician'],assignments:[{assignment_id:'11111111-1111-4111-8111-111111111111',role_code:'technician',scope_type:'person',scope_id:f.identity.person_id,valid_from:'2020-01-01T00:00:00Z',valid_to:null}],permissions:['read',f.kind].map(action=>({resource:'stock_operation',action,field_code:''}))};
 let error=null;
 const request=async(path,init)=>{
  calls.push([path,init]);if(error)throw error;
  if(path==='/auth/me')return structuredClone(me);if(path==='/access/context')return structuredClone(access);
  if(path.endsWith('/options'))return f.options;if(path.endsWith('/preview'))return f.preview;
  if(path.endsWith('/requests/seal'))return f.seal;
  if(path.endsWith('/requests/lookup'))return {lookup_status:'found',retry_allowed:false,operation_type:f.kind,result:f.result};
  if(path.endsWith(f.detail.origin.operation_id))return f.detail;return f.result;
 };
 return {calls,me,access,adapter:createAdapter(f.identity.person_id,f.kind,request),setError:e=>error=e};
}
function original(f){return pending({v:1,kind:f.kind,...f.identity,operation_id:f.detail.origin.operation_id,detail:f.detail,options:f.options,command:f.command,preview:f.preview});}
for(const tracking of ['quantity','serial'])for(const kind of ['outbound_return','ship_return']){
 const name=`${tracking}-${kind}`;
 test(`${name}: preparation uses actual contracts and no-store reads`,async()=>{
  const f=fixture(tracking,kind),{adapter,calls}=api(f),p=original(f),{expected_plan_hash,request_id,idempotency_key,...body}=p.command;
  assert.deepEqual(await adapter.choices(p.operation_id),{detail:f.detail,options:f.options});
  assert.deepEqual(await adapter.readDetail(p.operation_id),f.detail);
  assert.deepEqual(await adapter.prepare(p.operation_id,body),{detail:f.detail,options:f.options,preview:f.preview});
  assert.equal(calls.filter(([path])=>path.endsWith('/preview')).length,1);
  for(const [,init]of calls){assert.equal(init.cache,'no-store');assert.equal(new Headers(init.headers).get('Cache-Control'),'no-store');}
 });
 test(`${name}: submit/lookup/seal preserve full original and independent request coordinates`,async()=>{
  const f=fixture(tracking,kind),p=original(f),{adapter,calls}=api(f),suffix=kind==='outbound_return'?'outbounds':'shipments';
  await adapter.lookup(p);await adapter.submit(p);await adapter.seal(p);
  assert.deepEqual(calls.map(([path])=>path),[`/v1/stock-operations/loss-reports/returns/${p.operation_id}/${suffix}/requests/lookup`,`/v1/stock-operations/loss-reports/returns/${p.operation_id}/${suffix}`,`/v1/stock-operations/loss-reports/returns/${p.operation_id}/${suffix}/requests/seal`]);
  for(const [,init]of calls){assert.equal(init.method,'POST');assert.deepEqual(JSON.parse(init.body),p.command);}
  assert.equal(new Headers(calls[0][1].headers).get('Idempotency-Key'),null);
  assert.equal(new Headers(calls[1][1].headers).get('Idempotency-Key'),p.command.idempotency_key);
  assert.equal(new Headers(calls[2][1].headers).get('Idempotency-Key'),null);
  assert.equal(new Headers(calls[2][1].headers).get('X-Request-ID'),p.command.request_id);
 });
 test(`${name}: write permission is specific to physical action; read-only recovery has no options query`,async()=>{
  const f=fixture(tracking,kind),p=original(f),{adapter,calls,access}=api(f);
  access.permissions=access.permissions.map(r=>r.action===kind?{...r,action:kind==='outbound_return'?'ship_return':'outbound_return'}:r);
  const c=await adapter.context();assert.equal(c.can_read,true);assert.equal(c.can_write,false);
  await assert.rejects(adapter.choices(p.operation_id),/权限/);await adapter.lookup(p);
  assert.equal(calls.some(([path])=>path.endsWith('/options')),false);
 });
}
test('transport failure is propagated with no replay or fabricated absence',async()=>{
 const f=fixture('quantity','ship_return'),{adapter,calls,setError}=api(f);setError(Error('503'));
 await assert.rejects(adapter.lookup(original(f)),/503/);assert.equal(calls.length,1);
});
test('expired, external-only, or mismatched identity never allows a new preview',async()=>{
 const f=fixture('quantity','ship_return'),{adapter,calls,me,access}=api(f),key=f.detail.origin.operation_id;
 access.assignments[0].valid_to='2021-01-01T00:00:00Z';await assert.rejects(adapter.choices(key),/权限/);
 access.assignments[0].valid_to=null;access.assignments[0].role_code='star_headquarters_approver';access.role_codes=['star_headquarters_approver'];me.role_codes=access.role_codes;
 await assert.rejects(adapter.choices(key),/权限/);me.person_id='22222222-2222-4222-8222-222222222222';await assert.rejects(adapter.context(),/身份/);
 assert.equal(calls.some(([path])=>path.includes('/returns/')),false);
});
test('malformed object coordinates and foreign kind stop before business calls',async()=>{
 const f=fixture('quantity','ship_return'),{adapter,calls}=api(f),p=original(f);
 assert.throws(()=>adapter.submit({...p,person_id:'22222222-2222-4222-8222-222222222222'}));
 assert.throws(()=>adapter.lookup(original(fixture('quantity','outbound_return'))));assert.equal(calls.length,0);
 await assert.rejects(adapter.list(p.operation_id),/快照/);assert.equal(calls.some(([path])=>path.includes('/returns/')),false);
});
test('real directory wire contract preserves pagination snapshot',async()=>{
 const f=JSON.parse(readFileSync(new URL('./test-fixtures/loss-sender/h5-read-contracts/quantity-pending_departure.json',import.meta.url),'utf8'));
 const base=api({...f,kind:'outbound_return'}),calls=[];
 const a=createAdapter(f.identity.person_id,'outbound_return',async(path,init)=>{calls.push(path);if(path==='/auth/me')return base.me;if(path==='/access/context')return base.access;return f.directory});
 assert.deepEqual(await a.list(),f.directory);assert.equal(calls.includes('/v1/stock-operations/loss-reports/returns/my-sending?limit=20'),true);
});
test('controller plus adapter retains original across uncertain POST and uses lookup only after refresh',async()=>{
 const f=fixture('serial','ship_return'),p=original(f),base=api(f),map=new Map(),calls=[];let lost=true;
 const storage={get length(){return map.size},key:i=>[...map.keys()][i]??null,getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};
 const locks={request:async(_key,_options,fn)=>fn({})};
 const adapter=createAdapter(f.identity.person_id,f.kind,async(path,init)=>{
  calls.push(path);if(path==='/auth/me')return base.me;if(path==='/access/context')return base.access;
  if(path.endsWith('/options'))return f.options;if(path.endsWith('/preview'))return f.preview;
  if(path.endsWith('/requests/lookup'))return {lookup_status:'found',retry_allowed:false,operation_type:f.kind,result:f.result};
  if(path.endsWith(p.operation_id))return f.detail;
  assert.equal(map.size,1);if(lost){lost=false;throw Error('lost POST response')}return f.result;
 });
 await assert.rejects(submit(adapter,createStore(storage,locks),p),/lost POST/);
 const reopened=createStore(storage,locks);assert.equal((await recover(adapter,reopened,reopened.list(p.person_id)[0])).status,'found');
 assert.equal(calls.filter(path=>path.endsWith('/shipments')).length,1);assert.equal(map.size,0);
});
