/** A lost report response retains its original command; recovery never repeats submission. */
import { canonical, digest, fail, id, integer, type Identity } from './formalReturnReceiving';
import { pending, preview, sources, lookup, result, verifyPending, type Pending } from './formalLossSubmission';
export type Context = Identity & { authority_hash: string; can_read: boolean; can_write: boolean };
export type Resolution = Awaited<ReturnType<typeof lookup>>;
export type Transport = {
  context(): Promise<Context>;
  sources(): Promise<unknown>;
  preview(value: Pending): Promise<unknown>;
  lookup(value: Pending): Promise<unknown>;
  submit(value: Pending): Promise<unknown>;
  seal(value: Pending): Promise<unknown>;
};
export type Read = { kind: 'missing' | 'corrupt' | 'unavailable' } | { kind: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(value: Pending): void; clearExact(value: Pending): void };
export type Locks = { request<T>(name: string, options: { mode: 'exclusive'; ifAvailable: true }, callback: (lock: unknown | null) => Promise<T>): Promise<T> };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
export type Store = { read(person: string, location: string): Read; list(person: string): Pending[]; withLease<T>(person: string, location: string, work: (lease: Lease) => Promise<T>): Promise<T> };
const PREFIX = 'cloud-oam-loss-submission-v1:';
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
        return key(value.person_id, value.sources.location_id) === k && canonical(value) === canonical(data) ? { kind: 'valid', value } : { kind: 'corrupt' };
      } catch { return { kind: 'corrupt' }; }
    } catch { return { kind: 'unavailable' }; }
  }
  return {
    read(person, location) { return read(key(person, location)); },
    list(person) {
      if (!storage) fail('本机原报损请求存储不可用，请保留浏览器数据');
      const prefix = `${PREFIX}${id(person)}:`, values: Pending[] = [];
      try {
        for (let i = 0; i < storage.length; i++) {
          const k = storage.key(i); if (!k?.startsWith(prefix)) continue;
          const row = read(k); if (row.kind !== 'valid') fail();
          values.push(row.value); if (values.length > 500) fail();
        }
      } catch { fail('无法完整读取原报损请求，已停止新报损，请保留本机记录'); }
      return values;
    },
    async withLease(person, location, work) {
      const k = key(person, location);
      if (!storage || !locks) fail('请使用支持 Web Locks 的 HTTPS 浏览器，确保原报损请求可靠保存');
      if (active.has(k)) fail('此个人仓报损正在处理'); active.add(k);
      try {
        return await locks.request(k, { mode: 'exclusive', ifAvailable: true }, async lock => {
          if (!lock) fail('另一个页面正在处理此个人仓报损');
          let valid = true; const held = () => { if (!valid) fail('操作锁已结束'); };
          const lease: Lease = {
            read() { held(); return read(k); },
            persist(value) {
              held(); const p = pending(value), raw = canonical(p);
              if (key(p.person_id, p.sources.location_id) !== k || read(k).kind !== 'missing') fail('个人仓已有待核验报损请求，禁止覆盖');
              if (raw.length > 2000000) fail('原报损请求超出本机保存上限');
              try { storage.setItem(k, raw); } catch { faults.add(k); fail('原请求保存失败，报损未发送'); }
              const saved = read(k); if (saved.kind !== 'valid' || canonical(saved.value) !== raw) { faults.add(k); fail('保存未确认，报损未发送'); }
            },
            clearExact(value) {
              held(); const saved = read(k);
              if (saved.kind !== 'valid' || canonical(saved.value) !== canonical(value)) fail('原报损请求已变化，禁止清理');
              try { storage.removeItem(k); if (storage.getItem(k) !== null) throw new Error(); } catch { faults.add(k); fail('结果已核验，但本机报损记录清理未确认'); }
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
function context(value: Context, p: Pending, write = false): Context {
  if (id(value.person_id) !== p.person_id || integer(value.authorization_version, 1) < p.authorization_version || value.can_read !== true || typeof value.can_write !== 'boolean' || (write && !value.can_write)) fail('当前身份或权限无法核验原报损请求');
  return { person_id: value.person_id, authorization_version: value.authorization_version, authority_hash: digest(value.authority_hash), can_read: true, can_write: value.can_write };
}
function present(isCurrent: () => boolean) { if (!isCurrent()) fail('页面或身份已变化，原报损请求保留'); }
async function saved(lease: Lease, p: Pending) {
  const r = lease.read(); if (r.kind !== 'valid' || canonical(r.value) !== canonical(p)) fail('原报损请求已变化或不可读');
  await verifyPending(r.value);
}
async function finish(transport: Transport, lease: Lease, p: Pending, isCurrent: () => boolean): Promise<Resolution> {
  await saved(lease, p); present(isCurrent);
  const before = context(await transport.context(), p); present(isCurrent);
  const resolution = await lookup(await transport.lookup(p), p.command, p.preview);
  const after = context(await transport.context(), p);
  if (canonical(before) !== canonical(after)) fail('回查期间权限变化，原报损请求保留');
  present(isCurrent); await saved(lease, p);
  if (resolution.status !== 'unknown') lease.clearExact(p);
  return resolution;
}
export async function submit(transport: Transport, store: Store, value: Pending, isCurrent = () => true): Promise<Resolution> {
  const p = await verifyPending(value);
  return store.withLease(p.person_id, p.sources.location_id, async lease => {
    if (lease.read().kind !== 'missing') fail('个人仓已有未知报损请求，请先回查'); present(isCurrent);
    const before = context(await transport.context(), p, true);
    if (before.authorization_version !== p.authorization_version) fail('权限版本变化，请重新预检并确认');
    const current = sources(await transport.sources(), before);
    if (current.location_id !== p.sources.location_id || current.custody_effective_from !== p.sources.custody_effective_from) fail('个人仓保管责任已变化');
    const { expected_plan_hash, request_id: _request, idempotency_key: _key, ...body } = p.command;
    const checked = await preview(await transport.preview(p), body, current);
    if (checked.plan_hash !== expected_plan_hash) fail('报损方案已变化，请重新确认');
    const after = context(await transport.context(), p, true);
    if (canonical(before) !== canonical(after)) fail('提交前权限变化'); present(isCurrent);
    lease.persist(p);
    // Exactly one submission; any error or navigation leaves the original intact.
    await result(await transport.submit(p), p.command, p.preview);
    return finish(transport, lease, p, isCurrent);
  });
}
export async function recover(transport: Transport, store: Store, value: Pending, isCurrent = () => true): Promise<Resolution> {
  const p = await verifyPending(value);
  return store.withLease(p.person_id, p.sources.location_id, lease => finish(transport, lease, p, isCurrent));
}
export async function seal(transport: Transport, store: Store, value: Pending, confirmed = false, isCurrent = () => true): Promise<Resolution> {
  if (!confirmed) fail('永久封存原报损请求需要单独明确确认');
  const p = await verifyPending(value);
  return store.withLease(p.person_id, p.sources.location_id, async lease => {
    await saved(lease, p); present(isCurrent);
    const before = context(await transport.context(), p, true), observed = await lookup(await transport.lookup(p), p.command, p.preview);
    const after = context(await transport.context(), p, true);
    if (canonical(before) !== canonical(after)) fail('封存前权限变化'); present(isCurrent); await saved(lease, p);
    if (observed.status !== 'unknown') { lease.clearExact(p); return observed; }
    await lookup(await transport.seal(p), p.command, p.preview);
    return finish(transport, lease, p, isCurrent);
  });
}
