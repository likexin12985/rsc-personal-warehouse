import { expect, it, vi } from 'vitest';
import commands from './test-fixtures/stock-scrap/command-hashes.json';
import outcomes from './test-fixtures/stock-scrap/committed-quantity-results.json';
import { command, prepare, target, type Pending } from './formalScrapCommands';
import { createStore, submit, recover, seal, type Context, type Lease, type Transport } from './formalScrapRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import type { Kind } from './formalScrapFacts';

const kinds:Kind[]=['original','correction','apply','regional','headquarters','execute'];
async function world(k:Kind='original'){
  const sample=commands.samples.find(s=>s.kind===k)!;
  const p=await prepare(sample.person_id,sample.authorization_version,k,command(k,sample.original));
  const storage=new MemoryStorage(),lock=locks(),store=createStore(storage,lock);
  const c:Context={person_id:p.person_id,authorization_version:p.authorization_version,kind:k,
    authority_hash:'a'.repeat(64),can_read:true,can_write:true};
  // Real committed public fact shape; synthetic command coordinates below
  // exercise client orchestration, not a native transaction.
  const result={...outcomes.samples.find(s=>s.kind===k)!.fact,
    request_id:p.original.request_id,request_hash:p.request_hash};
  const c0=p.original;
  if('execution_reason' in c0){
    Object.assign(result,{plan_hash:c0.expected_plan_hash});
    if(c0.source.kind==='correction')Object.assign(result,{root_disposition_id:c0.source.root_disposition_id});
  }else{
    Object.assign(result,{actor_person_id:p.person_id,authorization_version:p.authorization_version,reason:c0.reason});
    if(c0.action==='execute_scrap_recovery')Object.assign(result,{plan_hash:c0.expected_plan_hash});
    else{
      Object.assign(result,{scrap_line_id:c0.source.scrap_line_id});
      if(c0.action!=='apply_scrap_recovery')Object.assign(result,{recovery_request_id:c0.recovery_request_id,decision:c0.decision});
      if(c0.action==='review_scrap_recovery_headquarters')Object.assign(result,{regional_review_id:c0.regional_review_id});
    }
  }
  const base={request_id:p.original.request_id,request_hash:p.request_hash,retry_allowed:false};
  const found={...base,request_state:'found',result_scope:'historical_original_outcome',result};
  const missing={...base,request_state:'not_found',result_scope:'unconfirmed_request',result:null};
  const sealed={...base,request_state:'sealed',result_scope:'closed_original_request',result:null,
    seal:{seal_id:crypto.randomUUID(),kind:k,loss_operation_id:crypto.randomUUID(),loss_line_id:crypto.randomUUID(),
      root_disposition_id:crypto.randomUUID(),sealed_at:new Date().toISOString(),stock_effect:'none'}};
  const t:Transport={context:vi.fn(async()=>({...c})),verifySource:vi.fn(async()=>{}),
    submit:vi.fn(async()=>{
      expect(store.read(p.person_id,p.kind,target(p))).toEqual({status:'valid',value:p});return result;
    }),lookup:vi.fn(async()=>found),seal:vi.fn(async()=>sealed)};
  const retain=()=>store.withLease(p,async lease=>lease.persist(p));
  return {p,storage,lock,store,c,t,found,missing,sealed,result,retain};
}

it.each(kinds)('saves full %s command before one write and clears only after exact readback',async k=>{
  const w=await world(k);expect((await submit(w.t,w.store,w.p)).status).toBe('found');
  expect(w.t.submit).toHaveBeenCalledTimes(1);expect(w.t.lookup).toHaveBeenCalledTimes(1);
  expect(w.store.list(w.p.person_id)).toEqual([]);
});
it.each(kinds)('recovers a lost %s response after remount with write permission revoked',async k=>{
  const w=await world(k);vi.mocked(w.t.submit).mockRejectedValueOnce(new Error('connection lost'));
  await expect(submit(w.t,w.store,w.p)).rejects.toThrow('connection lost');
  const fresh=createStore(w.storage,w.lock);expect(fresh.list(w.p.person_id)).toEqual([w.p]);
  w.c.can_write=false;w.c.authorization_version++;
  expect((await recover(w.t,fresh,w.p)).status).toBe('found');
  expect(w.t.submit).toHaveBeenCalledTimes(1);expect(w.t.verifySource).toHaveBeenCalledTimes(1);
  expect(w.t.seal).not.toHaveBeenCalled();
});
it.each(kinds)('%s not_found keeps request and blocks a second submission',async k=>{
  const w=await world(k);await w.retain();vi.mocked(w.t.lookup).mockResolvedValue(w.missing);
  expect(await recover(w.t,w.store,w.p)).toEqual({status:'pending'});
  await expect(submit(w.t,w.store,w.p)).rejects.toThrow();expect(w.t.submit).not.toHaveBeenCalled();
  expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it.each(kinds)('explicit %s seal looks up first, sends once and rereads before clearing',async k=>{
  const w=await world(k);await w.retain();
  vi.mocked(w.t.lookup).mockResolvedValueOnce(w.missing).mockResolvedValueOnce(w.sealed);
  expect((await seal(w.t,w.store,w.p)).status).toBe('sealed');
  expect(w.t.seal).toHaveBeenCalledExactlyOnceWith(w.p);expect(w.t.submit).not.toHaveBeenCalled();
  expect(w.store.list(w.p.person_id)).toEqual([]);
});
it('never sends a seal after readback already found the outcome',async()=>{
  const w=await world();await w.retain();expect((await seal(w.t,w.store,w.p)).status).toBe('found');
  expect(w.t.seal).not.toHaveBeenCalled();
});
it('lost seal response stays recoverable without replaying closure or business write',async()=>{
  const w=await world();await w.retain();vi.mocked(w.t.lookup).mockResolvedValue(w.missing);
  vi.mocked(w.t.seal).mockRejectedValue(new Error('lost seal'));
  await expect(seal(w.t,w.store,w.p)).rejects.toThrow();
  w.c.can_write=false;vi.mocked(w.t.lookup).mockResolvedValue(w.sealed);
  expect((await recover(w.t,w.store,w.p)).status).toBe('sealed');
  expect(w.t.seal).toHaveBeenCalledTimes(1);expect(w.t.submit).not.toHaveBeenCalled();
});
it.each(['request_id','request_hash','retry_allowed','result_scope'])('retains saved request on invalid %s',async field=>{
  const w=await world();await w.retain();
  vi.mocked(w.t.lookup).mockResolvedValue({...w.found,[field]:'wrong'});
  await expect(recover(w.t,w.store,w.p)).rejects.toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it.each(['actor_person_id','scrap_line_id','recovery_request_id','reason'])('rejects a found review with a mismatched %s even with matching request hash',async field=>{
  const w=await world('regional');await w.retain();
  vi.mocked(w.t.lookup).mockResolvedValue({...w.found,result:{...w.result,[field]:field==='reason'?'other reason':crypto.randomUUID()}});
  await expect(recover(w.t,w.store,w.p)).rejects.toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it('does not clear a posted scrap result with a different approved plan',async()=>{
  const w=await world();await w.retain();
  vi.mocked(w.t.lookup).mockResolvedValue({...w.found,result:{...w.result,plan_hash:'0'.repeat(64)}});
  await expect(recover(w.t,w.store,w.p)).rejects.toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it.each(['storage_throw','storage_silent','no_locks','corrupt'])('does not send when persistence cannot be confirmed: %s',async fault=>{
  const w=await world();
  if(fault==='storage_throw')vi.spyOn(w.storage,'setItem').mockImplementation(()=>{throw new Error();});
  if(fault==='storage_silent')vi.spyOn(w.storage,'setItem').mockImplementation(()=>{});
  if(fault==='corrupt'){await w.retain();const key=w.storage.key(0)!;w.storage.setItem(key,'{broken');}
  const store=fault==='no_locks'?createStore(w.storage,null):w.store;
  await expect(submit(w.t,store,w.p)).rejects.toThrow();expect(w.t.submit).not.toHaveBeenCalled();
});
it('refuses old preview authorization before persist/send',async()=>{
  const w=await world();w.c.authorization_version++;
  await expect(submit(w.t,w.store,w.p)).rejects.toThrow();expect(w.t.submit).not.toHaveBeenCalled();
  expect(w.store.list(w.p.person_id)).toEqual([]);
});
it('retains an outcome if permission graph changes during readback',async()=>{
  const w=await world();await w.retain();
  vi.mocked(w.t.lookup).mockImplementation(async()=>{w.c.authority_hash='b'.repeat(64);return w.found;});
  await expect(recover(w.t,w.store,w.p)).rejects.toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it('retains outcome when page or identity changes after write',async()=>{
  const w=await world();let present=true;
  vi.mocked(w.t.submit).mockImplementation(async()=>{present=false;return w.result;});
  await expect(submit(w.t,w.store,w.p,()=>present)).rejects.toThrow();
  expect(w.store.list(w.p.person_id)).toEqual([w.p]);expect(w.t.lookup).not.toHaveBeenCalled();
});
it('shares one cross-page lease while an uncertain write is in flight',async()=>{
  const w=await world(),other=createStore(w.storage,w.lock);let release!:(value:unknown)=>void;
  vi.mocked(w.t.submit).mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
  const running=submit(w.t,w.store,w.p);await vi.waitFor(()=>expect(w.t.submit).toHaveBeenCalledTimes(1));
  await expect(recover(w.t,other,w.p)).rejects.toThrow();
  release(w.result);await running;expect(w.t.submit).toHaveBeenCalledTimes(1);
});
it('does not reuse an escaped lease or clear a changed complete private key',async()=>{
  const w=await world();let escaped!:Lease;
  await w.store.withLease(w.p,async lease=>{escaped=lease;lease.persist(w.p);
    const altered:Pending={...w.p,original:{...w.p.original,idempotency_key:'another-original-key'}};
    expect(()=>lease.clearExact(altered)).toThrow();
  });
  expect(()=>escaped.clearExact(w.p)).toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
