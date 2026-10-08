import { canonical } from './formalLossReview';
import { fact, fail, resolution, type Fact, type Kind, type Resolution } from './formalScrapFacts';
import { digest, id, kind, pending, same, target, verifyPending, type Pending } from './formalScrapCommands';
import type { Locks } from './lossReviewRecovery';

export type Context = { person_id: string; authorization_version: number; kind: Kind;
  authority_hash: string; can_read: boolean; can_write: boolean };
export type Transport = {
  context(stage: Kind): Promise<Context>;
  verifySource(p: Pending, current: Context): Promise<void>;
  submit(p: Pending): Promise<unknown>;
  lookup(p: Pending): Promise<unknown>;
  seal(p: Pending): Promise<unknown>;
};
export type Read = { status: 'missing' | 'corrupt' | 'unavailable' } | { status: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(p: Pending): void; clearExact(p: Pending): void };
export type Store = { read(person: string, stage: Kind, object: string): Read; list(person: string): Pending[];
  withLease<T>(p: Pending, work: (lease: Lease) => Promise<T>): Promise<T> };
type StorageLike = Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
const PREFIX='cloud-oam-scrap-original-v1:';

export function createStore(storage: StorageLike | null, locks: Locks | null): Store {
  const active=new Set<string>(),faults=new Set<string>();
  const key=(person:string,stage:Kind,object:string)=>`${PREFIX}${id(person)}:${kind(stage)}:${id(object)}`;
  const keyOf=(p:Pending)=>key(p.person_id,p.kind,target(p));
  function read(k:string): Read {
    if (!storage || faults.has(k)) return {status:'unavailable'};
    try {
      const raw=storage.getItem(k);
      if(raw===null)return {status:'missing'};
      try {const p=pending(JSON.parse(raw));return keyOf(p)===k ? {status:'valid',value:p} : {status:'corrupt'};}
      catch{return {status:'corrupt'};}
    } catch {return {status:'unavailable'};}
  }
  return {
    read(person,stage,object){return read(key(person,stage,object));},
    list(person){
      if(!storage)fail();const prefix=PREFIX+id(person)+':',result:Pending[]=[];
      try {for(let i=0;i<storage.length;i++){
        const k=storage.key(i);if(!k?.startsWith(prefix))continue;
        const r=read(k);if(r.status!=='valid')fail();result.push(r.value);if(result.length>500)fail();
      }}catch{fail();}return result;
    },
    async withLease(value,work){
      const p=pending(value),k=keyOf(p);
      if(!locks||!storage||active.has(k))fail();active.add(k);
      try{return await locks.request(k,{mode:'exclusive',ifAvailable:true},async lock=>{
        if(!lock)fail();let live=true;
        function requireLease(){if(!live)fail();}
        const lease:Lease={read(){requireLease();return read(k);},persist(value){
          requireLease();const candidate=pending(value);
          if(keyOf(candidate)!==k||read(k).status!=='missing')fail();
          try{storage.setItem(k,canonical(candidate));}catch{faults.add(k);fail();}
          const reread=read(k);
          if(reread.status!=='valid'||!same(reread.value,candidate)){faults.add(k);fail();}
        },clearExact(value){
          requireLease();const reread=read(k);
          if(reread.status!=='valid'||!same(reread.value,value))fail();
          try{storage.removeItem(k);if(storage.getItem(k)!==null)throw new Error();}
          catch{faults.add(k);fail();}
        }};
        try{return await work(lease);}finally{live=false;}
      });}finally{active.delete(k);}
    },
  };
}
export function browserStore(): Store {
  let storage:StorageLike|null=null,locks:Locks|null=null;
  try{storage=globalThis.localStorage;locks=globalThis.navigator?.locks??null;}catch{/* access fails closed */}
  return createStore(storage,locks);
}
function context(c:Context,p:Pending,write=false,newCommand=false): Context {
  if(c.person_id!==p.person_id||c.kind!==p.kind||!Number.isSafeInteger(c.authorization_version)
      ||c.authorization_version<p.authorization_version||(newCommand&&c.authorization_version!==p.authorization_version)
      ||c.can_read!==true||typeof c.can_write!=='boolean'||(write&&!c.can_write))fail();
  return {person_id:id(c.person_id),authorization_version:c.authorization_version,kind:kind(c.kind),
    authority_hash:digest(c.authority_hash),can_read:true,can_write:c.can_write};
}
function present(current:()=>boolean){if(!current())fail();}
async function saved(lease:Lease,p:Pending){
  const r=lease.read();if(r.status!=='valid'||!same(r.value,p))fail();await verifyPending(r.value);
}
function resolve(value:unknown,p:Pending): Resolution {
  const answer=resolution(value,p.kind,{request_id:p.original.request_id,request_hash:p.request_hash});
  if(answer.status==='found')checkOutcome(answer.result,p);
  return answer;
}
function checkOutcome(outcome:Fact,p:Pending){
  const c=p.original;
  if(outcome.request_id!==c.request_id||outcome.request_hash!==p.request_hash)fail();
  if('execution_reason' in c){
    if(!('source_kind' in outcome)||outcome.source_kind!==c.source.kind||outcome.plan_hash!==c.expected_plan_hash)fail();
    if(c.source.kind==='correction'&&outcome.root_disposition_id!==c.source.root_disposition_id)fail();
  }else{
    if(!('actor_person_id' in outcome)||outcome.actor_person_id!==p.person_id
        ||outcome.authorization_version<p.authorization_version||outcome.reason!==c.reason)fail();
    if(c.action==='execute_scrap_recovery'){
      if(!('reversal_id' in outcome)||outcome.plan_hash!==c.expected_plan_hash)fail();
    }else{
      if(!('stage' in outcome)||outcome.scrap_line_id!==c.source.scrap_line_id)fail();
      if(c.action!=='apply_scrap_recovery'&&(outcome.recovery_request_id!==c.recovery_request_id||outcome.decision!==c.decision))fail();
      if(c.action==='review_scrap_recovery_headquarters'&&outcome.regional_review_id!==c.regional_review_id)fail();
    }
  }
}
async function readStable(t:Transport,lease:Lease,p:Pending,current:()=>boolean,write=false): Promise<Resolution>{
  await saved(lease,p);present(current);
  const before=context(await t.context(p.kind),p,write);present(current);
  const result=resolve(await t.lookup(p),p);
  const after=context(await t.context(p.kind),p,write);
  if(canonical(before)!==canonical(after))fail();present(current);await saved(lease,p);
  return result;
}
async function finish(t:Transport,lease:Lease,p:Pending,current:()=>boolean): Promise<Resolution>{
  const result=await readStable(t,lease,p,current);
  if(result.status!=='pending')lease.clearExact(p);
  return result;
}
export async function submit(t:Transport,store:Store,value:Pending,current=()=>true): Promise<Resolution>{
  const p=await verifyPending(value);
  return store.withLease(p,async lease=>{
    store.list(p.person_id);if(lease.read().status!=='missing')fail();present(current);
    const before=context(await t.context(p.kind),p,true,true);present(current);
    await t.verifySource(p,before);
    const after=context(await t.context(p.kind),p,true,true);
    if(canonical(before)!==canonical(after))fail();present(current);
    lease.persist(p);
    // Exactly one write. Failure, cancellation or malformed response retains
    // the entire command. Only an exact stable readback can clear it.
    const outcome=fact(p.kind,await t.submit(p));
    checkOutcome(outcome,p);
    return finish(t,lease,p,current);
  });
}
export async function recover(t:Transport,store:Store,value:Pending,current=()=>true): Promise<Resolution>{
  const p=await verifyPending(value);
  return store.withLease(p,lease=>finish(t,lease,p,current));
}
export async function seal(t:Transport,store:Store,value:Pending,current=()=>true): Promise<Resolution>{
  const p=await verifyPending(value);
  return store.withLease(p,async lease=>{
    const observed=await readStable(t,lease,p,current,true);
    if(observed.status!=='pending'){lease.clearExact(p);return observed;}
    const result=resolve(await t.seal(p),p);
    if(result.status==='pending')fail();
    return finish(t,lease,p,current);
  });
}
