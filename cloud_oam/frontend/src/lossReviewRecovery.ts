import { canonical, checkTarget, fail, id, identity, pending, report, resolution, verifyCommand,
  type Identity, type Pending, type Resolution, type Stage } from './formalLossReview';
export type Context = Identity & { stage: Stage; authority_hash: string; can_read: boolean; can_write: boolean };
export type Transport = {
  context(stage: Stage): Promise<Context>;
  detail(stage: Stage, operation: string, current: Identity): Promise<unknown>;
  lookup(value: Pending): Promise<unknown>;
  submit(value: Pending): Promise<unknown>;
  seal(value: Pending): Promise<unknown>;
};
export type Read = { kind: 'missing' | 'corrupt' | 'unavailable' } | { kind: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(value: Pending): void; clearExact(value: Pending): void };
export type Locks = { request<T>(name: string, options: { mode: 'exclusive'; ifAvailable: true }, callback: (lock: unknown | null)=>Promise<T>): Promise<T> };
type StorageLike = Pick<Storage,'getItem'|'setItem'|'removeItem'|'key'|'length'>;
export type Store = { read(person: string, stage: Stage, operation: string): Read; list(person: string): Pending[];
  withLease<T>(person: string, stage: Stage, operation: string, fn: (lease: Lease)=>Promise<T>): Promise<T> };
const PREFIX='cloud-oam-loss-review-v1:';
export function createStore(storage: StorageLike | null, locks: Locks | null): Store {
  const active=new Set<string>(),faults=new Set<string>();
  function key(person: string, stage: Stage, operation: string) { if(stage!=='regional'&&stage!=='headquarters')fail();return `${PREFIX}${id(person)}:${stage}:${id(operation)}`; }
  function readKey(k: string): Read {
    if(!storage||faults.has(k))return {kind:'unavailable'};
    try { const raw=storage.getItem(k);if(raw===null)return {kind:'missing'};
      try { const value=pending(JSON.parse(raw));return key(value.person_id,value.command.stage,value.command.original.operation_id)===k?{kind:'valid',value}:{kind:'corrupt'}; }
      catch{return {kind:'corrupt'};}
    } catch { return {kind:'unavailable'}; }
  }
  return {
    read(person,stage,operation){return readKey(key(person,stage,operation));},
    list(person){
      if(!storage)fail('无法读取本机原审批请求，已停止新审批');const prefix=PREFIX+id(person)+':';const values:Pending[]=[];
      try { for(let i=0;i<storage.length;i++){const k=storage.key(i);if(!k?.startsWith(prefix))continue;const r=readKey(k);if(r.kind!=='valid')fail('本机审批恢复记录损坏或不可读，请保留记录并联系管理员');values.push(r.value);if(values.length>500)fail();} }
      catch{fail('无法完整读取本机原审批请求，已停止新审批');}return values;
    },
    async withLease(person,stage,operation,work){
      const k=key(person,stage,operation);if(!locks||!storage)fail('浏览器无法安全保存并协调审批，请使用支持 Web Locks 的 HTTPS 浏览器');
      if(active.has(k))fail('该单正在操作，请勿重复提交');active.add(k);
      try {return await locks.request(k,{mode:'exclusive',ifAvailable:true},async lock=>{
        if(!lock)fail('其他页面正在处理此审批，请稍后回查');let valid=true;
        const requireLease=()=>{if(!valid)fail('审批操作锁已结束');};
        const lease:Lease={read(){requireLease();return readKey(k);},persist(value){
          requireLease();const checked=pending(value);if(key(checked.person_id,checked.command.stage,checked.command.original.operation_id)!==k||readKey(k).kind!=='missing')fail('已有待核验原请求，禁止覆盖');
          try {storage.setItem(k,canonical(checked));}catch{faults.add(k);fail('无法保存原请求，审批未发送');}
          const reread=readKey(k);if(reread.kind!=='valid'||canonical(reread.value)!==canonical(checked)){faults.add(k);fail('原请求保存未确认，审批未发送');}
        },clearExact(value){
          requireLease();const before=readKey(k);if(before.kind!=='valid'||canonical(before.value)!==canonical(pending(value)))fail('本机原请求已变化，禁止清理');
          try{storage.removeItem(k);if(storage.getItem(k)!==null)throw new Error();}catch{faults.add(k);fail('审批已核验，但本机记录清理未确认');}
        }};
        try{return await work(lease);}finally{valid=false;}
      });}finally{active.delete(k);}
    },
  };
}
export function browserStore(): Store {
  let storage:StorageLike|null=null,locks:Locks|null=null;
  try{storage=globalThis.localStorage;locks=globalThis.navigator?.locks??null;}catch{/* fail closed in store */}
  return createStore(storage,locks);
}
function currentContext(value: Context, original: Pending, write=false): Context {
  const i=identity(value);
  if(i.person_id!==original.person_id||i.authorization_version<original.authorization_version||value.stage!==original.command.stage||value.can_read!==true||(write&&value.can_write!==true)||typeof value.can_write!=='boolean'||typeof value.authority_hash!=='string'||!/^[a-f0-9]{64}$/.test(value.authority_hash))fail('当前身份或权限无法核验该审批，请保留原请求');
  return {...i,stage:value.stage,authority_hash:value.authority_hash,can_read:true,can_write:value.can_write};
}
function present(test: ()=>boolean) { if(!test())fail('页面或身份已变化，保留原请求'); }
async function checkedPending(lease:Lease, expected:Pending) {
  const saved=lease.read();if(saved.kind!=='valid'||canonical(saved.value)!==canonical(expected))fail('本机原审批请求已变化或不可读');await verifyCommand(saved.value.command);return saved.value;
}
async function finish(transport:Transport,lease:Lease,value:Pending,isCurrent:()=>boolean):Promise<Resolution> {
  const original=await checkedPending(lease,value);present(isCurrent);
  const before=currentContext(await transport.context(original.command.stage),original);present(isCurrent);
  const result=resolution(await transport.lookup(original),original);
  const after=currentContext(await transport.context(original.command.stage),original);
  if(canonical(before)!==canonical(after))fail('回查期间权限变化，保留原请求');present(isCurrent);
  await checkedPending(lease,original);
  if(result.status!=='pending')lease.clearExact(original);
  return result;
}
export async function submit(transport:Transport,store:Store,value:Pending,isCurrent=()=>true):Promise<Resolution> {
  const original=pending(value);await verifyCommand(original.command);
  return store.withLease(original.person_id,original.command.stage,original.command.original.operation_id,async lease=>{
    if(lease.read().kind!=='missing')fail('此单已有待核验请求，请先回查');present(isCurrent);
    const before=currentContext(await transport.context(original.command.stage),original,true);
    checkTarget(original,report(await transport.detail(original.command.stage,original.command.original.operation_id,before)));
    const after=currentContext(await transport.context(original.command.stage),original,true);
    if(canonical(before)!==canonical(after))fail('提交前权限发生变化，请重新核验');present(isCurrent);
    lease.persist(original);
    // Exactly one write. Any transport or validation error keeps the original.
    const result=await transport.submit(original);
    resolution({lookup_status:'found',retry_permitted:false,review:result},original);
    return finish(transport,lease,original,isCurrent);
  });
}
export async function recover(transport:Transport,store:Store,value:Pending,isCurrent=()=>true):Promise<Resolution> {
  const original=pending(value);return store.withLease(original.person_id,original.command.stage,original.command.original.operation_id,
    lease=>finish(transport,lease,original,isCurrent));
}
export async function seal(transport:Transport,store:Store,value:Pending,isCurrent=()=>true):Promise<Resolution> {
  const original=pending(value);await verifyCommand(original.command);
  return store.withLease(original.person_id,original.command.stage,original.command.original.operation_id,async lease=>{
    await checkedPending(lease,original);present(isCurrent);
    const before=currentContext(await transport.context(original.command.stage),original,true);
    const observed=resolution(await transport.lookup(original),original);
    const after=currentContext(await transport.context(original.command.stage),original,true);
    if(canonical(before)!==canonical(after))fail('封存前权限已变化，保留原请求');present(isCurrent);
    await checkedPending(lease,original);
    if(observed.status!=='pending'){lease.clearExact(original);return observed;}
    resolution(await transport.seal(original),original);
    return finish(transport,lease,original,isCurrent);
  });
}
