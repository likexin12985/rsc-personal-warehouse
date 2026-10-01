import { canonical, identity, id } from './formalLossReview';
import { checkTarget, fail, pending, resolution, verifyPending, type Pending, type Resolution, type Sources } from './lossExecutionContracts';
import type { Locks } from './lossReviewRecovery';
export type Context = {person_id:string;authorization_version:number;authority_hash:string;can_read:boolean;can_write:boolean};
export type Transport = { context():Promise<Context>; source(operation:string,current:Context):Promise<Sources>; lookup(p:Pending):Promise<unknown>; execute(p:Pending):Promise<unknown>; seal(p:Pending):Promise<unknown> };
export type Read = {kind:'missing'|'corrupt'|'unavailable'}|{kind:'valid';value:Pending};
export type Lease = {read():Read;persist(p:Pending):void;clearExact(p:Pending):void};
export type Store = {list(person:string):Pending[];read(person:string,decision:string):Read;withLease<T>(person:string,decision:string,fn:(lease:Lease)=>Promise<T>):Promise<T>};
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
const PREFIX='cloud-oam-loss-execution-v1:';
export function createStore(storage:StorageLike|null,locks:Locks|null):Store {
  const active=new Set<string>(),faults=new Set<string>();
  const key=(person:string,decision:string)=>`${PREFIX}${id(person)}:${id(decision)}`;
  function read(k:string):Read {
    if(!storage||faults.has(k))return {kind:'unavailable'};
    try {const raw=storage.getItem(k);if(raw===null)return {kind:'missing'};try {const p=pending(JSON.parse(raw));return key(p.person_id,p.command.headquarters_decision_id)===k?{kind:'valid',value:p}:{kind:'corrupt'};}catch{return {kind:'corrupt'};}}
    catch{return {kind:'unavailable'};}
  }
  return {
    read(person,decision){return read(key(person,decision));},
    list(person){
      if(!storage)fail('本机处置恢复记录不可读，已停止新处置');const prefix=PREFIX+id(person)+':',values:Pending[]=[];
      try {for(let i=0;i<storage.length;i++){const k=storage.key(i);if(!k?.startsWith(prefix))continue;const r=read(k);if(r.kind!=='valid')fail();values.push(r.value);if(values.length>500)fail();}}
      catch{fail('本机处置恢复记录不完整，请保留浏览器数据');}return values;
    },
    async withLease(person,decision,work){
      const k=key(person,decision);if(!locks||!storage)fail('需要支持 Web Locks 的 HTTPS 浏览器以保存原处置请求');
      if(active.has(k))fail('该处置正在操作，请勿重复提交');active.add(k);
      try{return await locks.request(k,{mode:'exclusive',ifAvailable:true},async lock=>{
        if(!lock)fail('另一页面正在处理同一处置');let valid=true;
        const requireLease=()=>{if(!valid)fail('处置操作锁已结束');};
        const lease:Lease={read(){requireLease();return read(k);},persist(value){
          requireLease();const p=pending(value);if(key(p.person_id,p.command.headquarters_decision_id)!==k||read(k).kind!=='missing')fail('已有原请求待核验，禁止覆盖');
          try{storage.setItem(k,canonical(p));}catch{faults.add(k);fail('原请求保存失败，未发送处置');}
          const got=read(k);if(got.kind!=='valid'||canonical(got.value)!==canonical(p)){faults.add(k);fail('原请求保存未确认，未发送处置');}
        },clearExact(value){
          requireLease();const got=read(k);if(got.kind!=='valid'||canonical(got.value)!==canonical(pending(value)))fail('本机原请求变化，禁止清理');
          try{storage.removeItem(k);if(storage.getItem(k)!==null)throw new Error();}catch{faults.add(k);fail('结果已核验，但本机原请求清理未确认');}
        }};try{return await work(lease);}finally{valid=false;}
      });}finally{active.delete(k);}
    },
  };
}
export function browserStore():Store {let storage:StorageLike|null=null,locks:Locks|null=null;try{storage=globalThis.localStorage;locks=globalThis.navigator?.locks??null;}catch{/* store fails closed */}return createStore(storage,locks);}
function context(c:Context,p:Pending,write=false):Context {
  const i=identity(c);if(i.person_id!==p.person_id||i.authorization_version<p.authorization_version||c.can_read!==true||(write&&c.can_write!==true)||typeof c.can_write!=='boolean'||typeof c.authority_hash!=='string'||!/^[a-f0-9]{64}$/.test(c.authority_hash))fail('当前身份或权限无法核验，请保留原请求');return {...i,authority_hash:c.authority_hash,can_read:true,can_write:c.can_write};
}
function present(current:()=>boolean){if(!current())fail('页面或身份已变化，保留原请求');}
async function saved(lease:Lease,p:Pending){const r=lease.read();if(r.kind!=='valid'||canonical(r.value)!==canonical(p))fail('原处置请求已变化或不可读');return verifyPending(r.value);}
async function finish(t:Transport,lease:Lease,p:Pending,current:()=>boolean):Promise<Resolution>{
  await saved(lease,p);present(current);const before=context(await t.context(),p);present(current);
  const result=resolution(await t.lookup(p),p),after=context(await t.context(),p);
  if(canonical(before)!==canonical(after))fail('回查期间授权变化，保留原请求');present(current);await saved(lease,p);
  if(result.status!=='pending')lease.clearExact(p);return result;
}
export async function execute(t:Transport,store:Store,value:Pending,current=()=>true):Promise<Resolution>{
  const p=await verifyPending(value);return store.withLease(p.person_id,p.command.headquarters_decision_id,async lease=>{
    if(lease.read().kind!=='missing')fail('该批准已有原请求待核验');present(current);
    const before=context(await t.context(),p,true);checkTarget(p,await t.source(p.operation_id,before));
    const after=context(await t.context(),p,true);if(canonical(before)!==canonical(after))fail('提交前授权变化，请重新预览');present(current);
    lease.persist(p);
    // One write only. Any exception leaves the complete saved command intact.
    resolution({lookup_status:'found',retry_permitted:false,result_scope:'original_command',disposition:await t.execute(p)},p);
    return finish(t,lease,p,current);
  });
}
export async function recover(t:Transport,store:Store,value:Pending,current=()=>true):Promise<Resolution>{const p=await verifyPending(value);return store.withLease(p.person_id,p.command.headquarters_decision_id,lease=>finish(t,lease,p,current));}
export async function seal(t:Transport,store:Store,value:Pending,current=()=>true):Promise<Resolution>{
  const p=await verifyPending(value);return store.withLease(p.person_id,p.command.headquarters_decision_id,async lease=>{
    await saved(lease,p);present(current);const before=context(await t.context(),p,true);present(current);
    const observed=resolution(await t.lookup(p),p),after=context(await t.context(),p,true);
    if(canonical(before)!==canonical(after))fail('封存前授权变化，保留原请求');present(current);await saved(lease,p);
    if(observed.status!=='pending'){lease.clearExact(p);return observed;}
    resolution(await t.seal(p),p);return finish(t,lease,p,current);
  });
}
