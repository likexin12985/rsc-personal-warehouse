/** One durable original per person/return, across outbound and shipment; recovery never replays. */
import { canonical, digest, fail, id, integer, object, type Identity } from './formalReturnReceiving';
import { detail, outboundOptions, shipmentOptions } from './formalLossSenderReads';
import { command, previewShape, preview, result, lookup, type Kind } from './formalLossSenderCommands';
export function pending(value: unknown) {
  const r=object(value,['v','kind','person_id','authorization_version','operation_id','detail','options','command','preview']);
  if(r.v!==1||(r.kind!=='outbound_return'&&r.kind!=='ship_return'))fail('原发件请求版本或类型无效');
  const kind:Kind=r.kind, identity={person_id:id(r.person_id),authorization_version:integer(r.authorization_version,1)},operation_id=id(r.operation_id);
  const original=detail(r.detail,identity,operation_id),options=(kind==='outbound_return'?outboundOptions:shipmentOptions)(r.options,identity,original);
  const c=command(kind,r.command,identity.person_id),{expected_plan_hash,request_id:_request,idempotency_key:_key,...body}=c;
  const prepared=previewShape(kind,r.preview,body,options,original);
  if(prepared.plan_hash!==expected_plan_hash)fail('原发件方案与请求不一致');
  return {v:1 as const,kind,...identity,operation_id,detail:original,options,command:c,preview:prepared};
}
export type Pending=ReturnType<typeof pending>;
export async function verifyPending(value: unknown):Promise<Pending> {
  const p=pending(value),{expected_plan_hash:_plan,request_id:_request,idempotency_key:_key,...body}=p.command;
  await preview(p.kind,p.preview,body,p.options,p.detail);return p;
}
export type Context=Identity & {authority_hash:string;can_read:boolean;can_write:boolean};
export type Resolution=Awaited<ReturnType<typeof lookup>>;
export type Transport={
  context():Promise<Context>;detail(value:Pending):Promise<unknown>;options(value:Pending):Promise<unknown>;
  preview(value:Pending):Promise<unknown>;submit(value:Pending):Promise<unknown>;
  lookup(value:Pending):Promise<unknown>;seal(value:Pending):Promise<unknown>;
};
export type Read = { kind: 'missing' | 'corrupt' | 'unavailable' } | { kind: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(value: Pending): void; clearExact(value: Pending): void };
export type Locks = { request<T>(name: string, options: { mode: 'exclusive'; ifAvailable: true }, callback: (lock: unknown | null) => Promise<T>): Promise<T> };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
export type Store = { read(person: string, location: string): Read; list(person: string): Pending[]; withLease<T>(person: string, location: string, work: (lease: Lease) => Promise<T>): Promise<T> };
const PREFIX = 'cloud-oam-loss-sender-v1:';
export function createStore(storage: StorageLike | null, locks: Locks | null): Store {
  const active = new Set<string>(), faults = new Set<string>();
  const key = (person: string, location: string) => `${PREFIX}${id(person)}:${id(location)}`;
  function read(k: string): Read {
    if (!storage || faults.has(k)) return { kind: 'unavailable' };
    try {
      const raw = storage.getItem(k); if (raw === null) return { kind: 'missing' };
      try {
        if (raw.length > 2000000) return { kind: 'corrupt' };
        const data = JSON.parse(raw), value = pending(data);
        return key(value.person_id, value.operation_id) === k && canonical(value) === canonical(data) ? { kind: 'valid', value } : { kind: 'corrupt' };
      } catch { return { kind: 'corrupt' }; }
    } catch { return { kind: 'unavailable' }; }
  }
  return {
    read(person, location) { return read(key(person, location)); },
    list(person) {
      if (!storage) fail('本机原发件请求存储不可用，请保留浏览器数据');
      const prefix = `${PREFIX}${id(person)}:`, values: Pending[] = [];
      try {
        for (let i = 0; i < storage.length; i++) {
          const k = storage.key(i); if (!k?.startsWith(prefix)) continue;
          const row = read(k); if (row.kind !== 'valid') fail();
          values.push(row.value); if (values.length > 500) fail();
        }
      } catch { fail('无法完整读取原发件请求，已停止新发件，请保留本机记录'); }
      return values;
    },
    async withLease(person, location, work) {
      const k = key(person, location);
      if (!storage || !locks) fail('请使用支持 Web Locks 的 HTTPS 浏览器，确保原发件请求可靠保存');
      if (active.has(k)) fail('此退回单发件正在处理'); active.add(k);
      try {
        return await locks.request(k, { mode: 'exclusive', ifAvailable: true }, async lock => {
          if (!lock) fail('另一个页面正在处理此退回单发件');
          let valid = true; const held = () => { if (!valid) fail('操作锁已结束'); };
          const lease: Lease = {
            read() { held(); return read(k); },
            persist(value) {
              held(); const p = pending(value), raw = canonical(p);
              if (key(p.person_id, p.operation_id) !== k || read(k).kind !== 'missing') fail('退回单已有待核验发件请求，禁止覆盖');
              if (raw.length > 2000000) fail('原发件请求超出本机保存上限');
              try { storage.setItem(k, raw); } catch { faults.add(k); fail('原请求保存失败，发件未发送'); }
              const saved = read(k); if (saved.kind !== 'valid' || canonical(saved.value) !== raw) { faults.add(k); fail('保存未确认，发件未发送'); }
            },
            clearExact(value) {
              held(); const saved = read(k);
              if (saved.kind !== 'valid' || canonical(saved.value) !== canonical(value)) fail('原发件请求已变化，禁止清理');
              try { storage.removeItem(k); if (storage.getItem(k) !== null) throw new Error(); } catch { faults.add(k); fail('结果已核验，但本机发件记录清理未确认'); }
            },
          };
          try { return await work(lease); } finally { valid = false; }
        });
      } finally { active.delete(k); }
    },
  };
}
export function browserStore(): Store {
  let storage: StorageLike | null = null, locks: Locks | null = null;
  try { storage = globalThis.localStorage; locks = globalThis.navigator?.locks ?? null; } catch { /* operations fail closed */ }
  return createStore(storage, locks);
}
function context(value:Context,p:Pending,write=false):Context {
  if(id(value.person_id)!==p.person_id||integer(value.authorization_version,1)<p.authorization_version||value.can_read!==true||typeof value.can_write!=='boolean'||(write&&!value.can_write))fail('当前身份或权限无法核验原发件请求');
  return {person_id:value.person_id,authorization_version:value.authorization_version,authority_hash:digest(value.authority_hash),can_read:true,can_write:value.can_write};
}
function present(isCurrent:()=>boolean){if(!isCurrent())fail('页面或身份已变化，原发件请求保留');}
async function saved(lease:Lease,p:Pending){
  const row=lease.read();if(row.kind!=='valid'||canonical(row.value)!==canonical(p))fail('原发件请求已变化或不可读');
  await verifyPending(row.value);
}
async function finish(transport:Transport,lease:Lease,p:Pending,isCurrent:()=>boolean):Promise<Resolution>{
  await saved(lease,p);present(isCurrent);
  const before=context(await transport.context(),p);present(isCurrent);
  const resolution=await lookup(p.kind,await transport.lookup(p),p.command,p.preview);
  const after=context(await transport.context(),p);
  if(canonical(before)!==canonical(after))fail('回查期间权限变化，原发件请求保留');
  present(isCurrent);await saved(lease,p);present(isCurrent);
  if(resolution.status!=='unknown')lease.clearExact(p);
  return resolution;
}
export async function submit(transport:Transport,store:Store,value:Pending,isCurrent=()=>true):Promise<Resolution>{
  const p=await verifyPending(value);
  return store.withLease(p.person_id,p.operation_id,async lease=>{
    if(lease.read().kind!=='missing')fail('退回单已有未知发件请求，请先回查');present(isCurrent);
    const before=context(await transport.context(),p,true);
    if(before.authorization_version!==p.authorization_version)fail('权限版本变化，请重新预检并确认');
    const original=detail(await transport.detail(p),before,p.operation_id);
    if(canonical(original.origin)!==canonical(p.detail.origin)||canonical(original.line)!==canonical(p.detail.line))fail('原退回单已变化');
    const choices=(p.kind==='outbound_return'?outboundOptions:shipmentOptions)(await transport.options(p),before,original);
    const {expected_plan_hash,request_id:_request,idempotency_key:_key,...body}=p.command;
    const checked=await preview(p.kind,await transport.preview(p),body,choices,original);
    if(checked.plan_hash!==expected_plan_hash)fail('发件方案已变化，请重新确认');
    const after=context(await transport.context(),p,true);
    if(canonical(before)!==canonical(after))fail('提交前权限变化');present(isCurrent);
    lease.persist(p);
    await result(p.kind,await transport.submit(p),p.command,p.preview);
    return finish(transport,lease,p,isCurrent);
  });
}
export async function recover(transport:Transport,store:Store,value:Pending,isCurrent=()=>true):Promise<Resolution>{
  const p=await verifyPending(value);
  return store.withLease(p.person_id,p.operation_id,lease=>finish(transport,lease,p,isCurrent));
}
export async function seal(transport:Transport,store:Store,value:Pending,confirmed=false,isCurrent=()=>true):Promise<Resolution>{
  if(!confirmed)fail('永久封存原发件请求需要单独明确确认');
  const p=await verifyPending(value);
  return store.withLease(p.person_id,p.operation_id,async lease=>{
    await saved(lease,p);present(isCurrent);
    const before=context(await transport.context(),p,true),observed=await lookup(p.kind,await transport.lookup(p),p.command,p.preview);
    const after=context(await transport.context(),p,true);
    if(canonical(before)!==canonical(after))fail('封存前权限变化');present(isCurrent);await saved(lease,p);present(isCurrent);
    if(observed.status!=='unknown'){lease.clearExact(p);return observed;}
    await lookup(p.kind,await transport.seal(p),p.command,p.preview);
    return finish(transport,lease,p,isCurrent);
  });
}
