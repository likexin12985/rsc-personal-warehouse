import {test} from 'vitest';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {pending,verifyPending,createStore,submit,recover,seal} from './lossSenderRecovery';
const missing={lookup_status:'not_observed',retry_allowed:false};
function memory(){const map=new Map();return {map,get length(){return map.size},key:i=>[...map.keys()][i]??null,getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};}
const locks={request:async(_key,_options,fn)=>fn({})};
function fixture(tracking,kind){return JSON.parse(readFileSync(new URL(`./test-fixtures/loss-sender/h5-write-contracts/${tracking}-${kind}.json`,import.meta.url),'utf8'));}
function original(f,absent=false){return pending({v:1,kind:f.kind,...f.identity,operation_id:f.detail.origin.operation_id,detail:f.detail,options:f.options,command:absent?f.missingCommand:f.command,preview:f.preview});}
function transport(f){
 const count={},hooks={},context={...f.identity,authority_hash:'a'.repeat(64),can_read:true,can_write:true};
 const defaults={context:()=>context,detail:()=>f.detail,options:()=>f.options,preview:()=>f.preview,submit:()=>f.result,lookup:()=>({lookup_status:'found',retry_allowed:false,operation_type:f.kind,result:f.result}),seal:()=>f.seal};
 const t=Object.fromEntries(Object.keys(defaults).map(k=>[k,async(...args)=>{count[k]=(count[k]??0)+1;return (hooks[k]??defaults[k])(...args)}]));
 return {t,count,hooks,context};
}
async function retain(store,p){await store.withLease(p.person_id,p.operation_id,async lease=>lease.persist(p));}
for(const tracking of ['quantity','serial'])for(const kind of ['outbound_return','ship_return']){
 const name=`${tracking}-${kind}`;
 test(`${name}: durable original precedes exactly one POST and verified lookup clears`,async()=>{
  const f=fixture(tracking,kind),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);
  hooks.submit=()=>{assert.equal(store.read(p.person_id,p.operation_id).kind,'valid');assert.deepEqual(store.list(p.person_id),[p]);return f.result};
  assert.equal((await submit(t,store,p)).status,'found');assert.equal(count.submit,1);assert.equal(count.lookup,1);assert.equal(storage.length,0);
 });
 test(`${name}: lost response survives refresh, missing lookup never permits another POST`,async()=>{
  const f=fixture(tracking,kind),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);
  hooks.submit=()=>{throw Error('lost response')};await assert.rejects(submit(t,store,p),/lost response/);
  const reopened=createStore(storage,locks),saved=reopened.list(p.person_id)[0];assert.deepEqual(saved,p);
  hooks.lookup=()=>missing;assert.equal((await recover(t,reopened,saved)).status,'unknown');assert.equal(storage.length,1);
  await assert.rejects(submit(t,reopened,p),/未知发件/);assert.equal(count.submit,1);
  delete hooks.lookup;assert.equal((await recover(t,reopened,saved)).status,'found');assert.equal(count.submit,1);assert.equal(storage.length,0);
 });
 test(`${name}: changed authority and plan or failed persistence prevent writes`,async()=>{
  const f=fixture(tracking,kind),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,hooks,context}=transport(f);
  hooks.preview=()=>({...f.preview,plan_hash:'f'.repeat(64)});await assert.rejects(submit(t,store,p),/方案/);delete hooks.preview;
  context.authorization_version++;await assert.rejects(submit(t,store,p),/权限版本/);context.authorization_version--;
  storage.setItem=()=>{throw Error('quota')};await assert.rejects(submit(t,store,p),/保存失败/);assert.equal(count.submit,undefined);
 });
 test(`${name}: read-only identity may recover but may not seal`,async()=>{
  const f=fixture(tracking,kind),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,context}=transport(f);await retain(store,p);
  context.authorization_version++;context.can_write=false;
  await assert.rejects(seal(t,store,p,true),/权限/);assert.equal(count.seal,undefined);
  assert.equal((await recover(t,store,p)).status,'found');assert.equal(count.submit,undefined);assert.equal(storage.length,0);
 });
 test(`${name}: explicit sealing requires terminal exact readback`,async()=>{
  const f=fixture(tracking,kind),p=original(f,true),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);await retain(store,p);
  await assert.rejects(seal(t,store,p),/明确确认/);assert.equal(count.seal,undefined);
  hooks.lookup=()=>count.seal?f.seal:missing;
  assert.equal((await seal(t,store,p,true)).status,'sealed');assert.equal(count.seal,1);assert.equal(count.submit,undefined);assert.equal(storage.length,0);
 });
 test(`${name}: failed seal response and readback retain original`,async()=>{
  const f=fixture(tracking,kind),p=original(f,true),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);await retain(store,p);
  hooks.lookup=()=>missing;hooks.seal=()=>{throw Error('unknown seal')};await assert.rejects(seal(t,store,p,true),/unknown seal/);assert.equal(storage.length,1);
  hooks.lookup=()=>f.seal;assert.equal((await recover(t,store,p)).status,'sealed');assert.equal(count.seal,1);assert.equal(count.submit,undefined);
 });
 test(`${name}: late navigation and conflicting readback preserve original`,async()=>{
  const f=fixture(tracking,kind),p=original(f),storage=memory(),store=createStore(storage,locks),{t,hooks,context}=transport(f);let current=true;
  hooks.submit=()=>{current=false;return f.result};await assert.rejects(submit(t,store,p,()=>current),/页面/);assert.equal(storage.length,1);
  hooks.lookup=()=>({lookup_status:'found',retry_allowed:false,operation_type:kind,result:{...f.result,request_id:'another-request'}});
  await assert.rejects(recover(t,store,p));assert.equal(storage.length,1);
  hooks.lookup=()=>{context.authority_hash='b'.repeat(64);return {lookup_status:'found',retry_allowed:false,operation_type:kind,result:f.result}};
  await assert.rejects(recover(t,store,p),/权限变化/);assert.equal(storage.length,1);
 });
}
test('corrupt storage, incomplete proofs and dishonest storage stay closed',async()=>{
 const f=fixture('serial','outbound_return'),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count}=transport(f);await retain(store,p);
 const key=storage.key(0);storage.setItem(key,'{broken');assert.throws(()=>store.list(p.person_id));await assert.rejects(recover(t,store,p));assert.equal(count.lookup,undefined);
 const corrupt=structuredClone(p);corrupt.preview.request_hash='f'.repeat(64);storage.setItem(key,JSON.stringify(corrupt));
 await assert.rejects(verifyPending(store.list(p.person_id)[0]));await assert.rejects(recover(t,store,corrupt));assert.equal(count.lookup,undefined);
 const fake=memory();fake.setItem=()=>{};await assert.rejects(submit(t,createStore(fake,locks),p),/保存未确认/);assert.equal(count.submit,undefined);
});
test('same object lock and stale lease prevent duplicate or escaped writes',async()=>{
 const f=fixture('quantity','outbound_return'),p=original(f),storage=memory(),store=createStore(storage,locks);let escaped;
 await store.withLease(p.person_id,p.operation_id,async lease=>{escaped=lease;await assert.rejects(retain(store,p),/正在处理/);lease.persist(p)});
 assert.throws(()=>escaped.read(),/锁已结束/);assert.throws(()=>escaped.clearExact(p),/锁已结束/);
 const {t,count}=transport(f);await assert.rejects(submit(t,createStore(storage,{request:async(_k,_o,fn)=>fn(null)}),p),/另一个页面/);assert.equal(count.submit,undefined);
 await assert.rejects(submit(t,createStore(memory(),null),p),/Web Locks/);
});
test('successful POST or seal never clears after unavailable lookup or failed deletion',async()=>{
 const f=fixture('quantity','ship_return'),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);
 hooks.lookup=()=>{throw Error('503')};await assert.rejects(submit(t,store,p),/503/);assert.equal(storage.length,1);
 delete hooks.lookup;storage.removeItem=()=>{};await assert.rejects(recover(t,store,p),/清理未确认/);assert.equal(storage.length,1);assert.equal(count.submit,1);
});
test('navigation during asynchronous final proof cannot clear the original or initiate a seal',async()=>{
 for(const action of ['recover','seal']){
  const f=fixture('quantity','ship_return'),p=original(f),storage=memory(),store=createStore(storage,locks),{t,count,hooks}=transport(f);await retain(store,p);
  let current=true,afterLookup=false;const get=storage.getItem;
  storage.getItem=k=>{const value=get(k);if(afterLookup)queueMicrotask(()=>{current=false});return value};
  hooks.lookup=()=>{afterLookup=true;return action==='seal'?missing:{lookup_status:'found',retry_allowed:false,operation_type:f.kind,result:f.result}};
  await assert.rejects(action==='seal'?seal(t,store,p,true,()=>current):recover(t,store,p,()=>current),/页面/);
  assert.equal(storage.length,1);assert.equal(count.seal,undefined);
 }
});
