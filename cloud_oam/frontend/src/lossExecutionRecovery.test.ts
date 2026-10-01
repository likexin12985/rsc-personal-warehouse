import { expect, it, vi } from 'vitest';
import { identity } from './formalLossReview';
import { sources, type Pending } from './lossExecutionContracts';
import { createStore, execute, recover, seal, type Transport } from './lossExecutionRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import { fixture, saved } from './lossExecutionFixtures';
async function world(sealed=false){
  const p=await saved(fixture,sealed),storage=new MemoryStorage(),lock=locks(),store=createStore(storage,lock);
  const c={...identity(p),can_read:true,can_write:true,authority_hash:'a'.repeat(64)};
  const t:Transport={context:vi.fn(async()=>({...c})),source:vi.fn(async()=>sources(fixture.source,identity(p),p.operation_id)),lookup:vi.fn(async()=>fixture.found),execute:vi.fn(async()=>{
    expect(store.read(p.person_id,p.command.headquarters_decision_id)).toEqual({kind:'valid',value:p});return fixture.found.disposition;
  }),seal:vi.fn(async()=>fixture.sealed)};
  const retain=()=>store.withLease(p.person_id,p.command.headquarters_decision_id,async l=>l.persist(p));
  return {p,storage,lock,store,c,t,retain};
}
it('persists exact full request before one write, rereads and clears only its found result',async()=>{const w=await world();expect(await execute(w.t,w.store,w.p)).toEqual({status:'found'});expect(w.t.execute).toHaveBeenCalledTimes(1);expect(w.t.lookup).toHaveBeenCalledTimes(1);expect(w.store.list(w.p.person_id)).toEqual([]);});
it('retains after a lost response and recovers across remount without any replay',async()=>{
  const w=await world();vi.mocked(w.t.execute).mockRejectedValueOnce(new Error('lost response'));await expect(execute(w.t,w.store,w.p)).rejects.toThrow('lost response');
  const fresh=createStore(w.storage,w.lock);expect(fresh.list(w.p.person_id)).toEqual([w.p]);w.c.can_write=false;
  await expect(execute(w.t,fresh,w.p)).rejects.toThrow();expect(await recover(w.t,fresh,w.p)).toEqual({status:'found'});expect(w.t.execute).toHaveBeenCalledTimes(1);expect(w.t.seal).not.toHaveBeenCalled();
});
it('not_found retains original and never permits a new submission',async()=>{const w=await world();await w.retain();vi.mocked(w.t.lookup).mockResolvedValue(fixture.missing);expect(await recover(w.t,w.store,w.p)).toEqual({status:'pending'});await expect(execute(w.t,w.store,w.p)).rejects.toThrow();expect(w.t.execute).not.toHaveBeenCalled();expect(w.store.list(w.p.person_id)).toEqual([w.p]);});
it('explicit seal looks up first, sends exact original once, then rereads before clearing',async()=>{const w=await world(true);await w.retain();vi.mocked(w.t.lookup).mockResolvedValueOnce(fixture.missing).mockResolvedValueOnce(fixture.sealed);expect(await seal(w.t,w.store,w.p)).toEqual({status:'sealed'});expect(w.t.seal).toHaveBeenCalledExactlyOnceWith(w.p);expect(w.t.execute).not.toHaveBeenCalled();expect(w.store.list(w.p.person_id)).toEqual([]);});
it('seal of an already found request never sends a seal write',async()=>{const w=await world();await w.retain();expect(await seal(w.t,w.store,w.p)).toEqual({status:'found'});expect(w.t.seal).not.toHaveBeenCalled();});
it('lost seal response retains the full request and allows lookup after write revocation',async()=>{const w=await world(true);await w.retain();vi.mocked(w.t.lookup).mockResolvedValueOnce(fixture.missing);vi.mocked(w.t.seal).mockRejectedValueOnce(new Error('lost seal'));await expect(seal(w.t,w.store,w.p)).rejects.toThrow();expect(w.store.list(w.p.person_id)).toEqual([w.p]);w.c.can_write=false;vi.mocked(w.t.lookup).mockResolvedValue(fixture.sealed);expect(await recover(w.t,w.store,w.p)).toEqual({status:'sealed'});expect(w.t.seal).toHaveBeenCalledTimes(1);});
it.each(['lookup-failure','wrong-fact','authority-change','identity-change','page-left','read-revoked','record-changed'])('keeps original when recovery cannot be certified: %s',async fault=>{
  const w=await world();await w.retain();let current=true;
  vi.mocked(w.t.lookup).mockImplementation(async()=>{
    if(fault==='lookup-failure')throw new Error('network');
    if(fault==='wrong-fact')return {...fixture.found,disposition:{...fixture.found.disposition,quantity:'9.000'}};
    if(fault==='authority-change')w.c.authority_hash='b'.repeat(64);
    if(fault==='identity-change')w.c.person_id='10000000-0000-4000-8000-000000000001';
    if(fault==='page-left')current=false;
    if(fault==='read-revoked')w.c.can_read=false;
    if(fault==='record-changed'){const k=w.storage.key(0)!;w.storage.setItem(k,w.storage.getItem(k)!.replace(w.p.command.idempotency_key,'changed-key'));}
    return fixture.found;
  });await expect(recover(w.t,w.store,w.p,()=>current)).rejects.toThrow();expect(w.storage.length).toBe(1);expect(w.t.execute).not.toHaveBeenCalled();expect(w.t.seal).not.toHaveBeenCalled();
});
it.each(['write-fails','readback-mismatch','no-locks','corrupt-record','wrong-source','stale-permission','left-page'])('never writes when persistence/authority checks fail: %s',async fault=>{
  const w=await world();let store=w.store,current=true;
  if(fault==='write-fails')w.storage.setItem=()=>{throw new Error('quota');};
  if(fault==='readback-mismatch')w.storage.setItem=()=>{};
  if(fault==='no-locks')store=createStore(w.storage,null);
  if(fault==='corrupt-record'){await w.retain();w.storage.setItem(w.storage.key(0)!,'corrupt');}
  if(fault==='wrong-source')vi.mocked(w.t.source).mockResolvedValue(sources(fixture.after,identity(w.p),w.p.operation_id));
  if(fault==='stale-permission')w.c.can_write=false;
  if(fault==='left-page')vi.mocked(w.t.source).mockImplementation(async()=>{current=false;return fixture.source;});
  await expect(execute(w.t,store,w.p,()=>current)).rejects.toThrow();expect(w.t.execute).not.toHaveBeenCalled();
});
it('coordinates competing tabs and different request keys for the same approved decision',async()=>{
  const w=await world();const other=createStore(w.storage,w.lock);let release!:(v:unknown)=>void;
  vi.mocked(w.t.execute).mockImplementation(()=>new Promise(resolve=>{release=resolve;}));
  const running=execute(w.t,w.store,w.p);await vi.waitFor(()=>expect(w.t.execute).toHaveBeenCalledTimes(1));
  await expect(recover(w.t,other,w.p)).rejects.toThrow('另一页面');release(fixture.found.disposition);await running;
});
it('prevents use of an escaped lease after its callback ends',async()=>{const w=await world();let persist!:(p:Pending)=>void;await w.store.withLease(w.p.person_id,w.p.command.headquarters_decision_id,async l=>{persist=l.persist;});expect(()=>persist(w.p)).toThrow('操作锁');});
