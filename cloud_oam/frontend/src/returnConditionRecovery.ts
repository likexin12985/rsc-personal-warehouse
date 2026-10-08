import { canonical, id } from './formalLossReview';
import { action, checkFact, digest, fail, pending, resolution, same, verifyPending, type Pending, type Resolution } from './returnConditionCommands';
import type { Action } from './returnConditionHistory';
import type { Locks } from './lossReviewRecovery';

export type Context = { person_id: string; authorization_version: number; action: Action;
  authority_hash: string; can_read: boolean; can_write: boolean };
export type Transport = { context(action: Action): Promise<Context>; verifySource(p: Pending, c: Context): Promise<void>;
  submit(p: Pending): Promise<unknown>; lookup(p: Pending): Promise<unknown>; seal(p: Pending): Promise<unknown> };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
export type Read = { status: 'missing' | 'corrupt' | 'unavailable' } | { status: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(p: Pending): void; clearExact(p: Pending): void };
export type Store = { read(person: string, inbound: string): Read; list(person: string): Pending[];
  withLease<T>(p: Pending, work: (lease: Lease) => Promise<T>): Promise<T> };
const PREFIX = 'cloud-oam-condition-original-v1:';

export function createStore(storage: StorageLike | null, locks: Locks | null): Store {
  const active = new Set<string>(), faults = new Set<string>();
  // All actions/cases on one original inbound share this lease and pending slot.
  const key = (person: string, inbound: string) => `${PREFIX}${id(person)}:${id(inbound)}`;
  const keyOf = (p: Pending) => key(p.person_id, p.inbound_line_id);
  function read(k: string): Read {
    if (!storage || faults.has(k)) return { status: 'unavailable' };
    try { const raw = storage.getItem(k); if (raw === null) return { status: 'missing' };
      try { const p = pending(JSON.parse(raw)); return keyOf(p) === k ? { status: 'valid', value: p } : { status: 'corrupt' }; }
      catch { return { status: 'corrupt' }; }
    } catch { return { status: 'unavailable' }; }
  }
  return {
    read(person, inbound) { return read(key(person, inbound)); },
    list(person) {
      if (!storage) fail('无法读取本机原请求，已停止新操作');
      const prefix = PREFIX + id(person) + ':', result: Pending[] = [];
      try { for (let i = 0; i < storage.length; i++) {
        const k = storage.key(i); if (!k?.startsWith(prefix)) continue;
        const r = read(k); if (r.status !== 'valid') fail(); result.push(r.value); if (result.length > 500) fail();
      } } catch { fail('本机原请求损坏或不可读，请保留记录并联系管理员'); } return result;
    },
    async withLease(value, work) {
      const p = pending(value), k = keyOf(p);
      if (!storage || !locks || active.has(k)) fail('无法取得原请求操作锁，请使用支持 Web Locks 的 HTTPS 浏览器并关闭重复操作');
      active.add(k);
      try { return await locks.request(k, { mode: 'exclusive', ifAvailable: true }, async lock => {
        if (!lock) fail('另一页面正在处理同一入库明细，请稍后回查');
        let live = true; const requireLease = () => { if (!live) fail(); };
        const lease: Lease = {
          read() { requireLease(); return read(k); },
          persist(value) {
            requireLease(); const candidate = pending(value);
            if (keyOf(candidate) !== k || read(k).status !== 'missing') fail('存在待核验原请求，不能覆盖或创建替代请求');
            try { storage.setItem(k, canonical(candidate)); } catch { faults.add(k); fail('原请求保存失败，本次未发送'); }
            const stored = read(k);
            if (stored.status !== 'valid' || !same(stored.value, candidate)) { faults.add(k); fail('原请求保存未确认，本次未发送'); }
          },
          clearExact(value) {
            requireLease(); const stored = read(k);
            if (stored.status !== 'valid' || !same(stored.value, value)) fail();
            try { storage.removeItem(k); if (storage.getItem(k) !== null) throw new Error(); }
            catch { faults.add(k); fail('结果已读取，但本机原请求清理未确认，请勿重复操作'); }
          },
        };
        try { return await work(lease); } finally { live = false; }
      }); } finally { active.delete(k); }
    },
  };
}
export function browserStore(): Store {
  let storage: StorageLike | null = null, locks: Locks | null = null;
  try { storage = globalThis.localStorage; locks = globalThis.navigator?.locks ?? null; } catch { /* fail closed */ }
  return createStore(storage, locks);
}
function context(value: Context, p: Pending, write = false, fresh = false): Context {
  if (value.person_id !== p.person_id || value.action !== action(p.original) || !Number.isSafeInteger(value.authorization_version)
      || value.authorization_version < p.authorization_version || (fresh && value.authorization_version !== p.authorization_version)
      || value.can_read !== true || typeof value.can_write !== 'boolean' || (write && !value.can_write)) fail('当前身份或权限已变化，请保留原请求并重新核验');
  return { person_id: id(value.person_id), authorization_version: value.authorization_version, action: value.action,
    authority_hash: digest(value.authority_hash), can_read: true, can_write: value.can_write };
}
function present(current: () => boolean) { if (!current()) fail('页面或账户已变化，请回查原请求'); }
async function saved(lease: Lease, p: Pending) {
  const r = lease.read(); if (r.status !== 'valid' || !same(r.value, p)) fail(); await verifyPending(r.value);
}
async function readStable(t: Transport, lease: Lease, p: Pending, current: () => boolean, write = false): Promise<Resolution> {
  await saved(lease, p); present(current);
  const before = context(await t.context(action(p.original)), p, write); present(current);
  const result = resolution(await t.lookup(p), p);
  const after = context(await t.context(action(p.original)), p, write);
  if (canonical(before) !== canonical(after)) fail(); present(current); await saved(lease, p);
  return result;
}
async function finish(t: Transport, lease: Lease, p: Pending, current: () => boolean): Promise<Resolution> {
  const result = await readStable(t, lease, p, current);
  if (result.status !== 'pending') lease.clearExact(p);
  return result;
}
export async function submit(t: Transport, store: Store, value: Pending, current = () => true): Promise<Resolution> {
  const p = await verifyPending(value);
  return store.withLease(p, async lease => {
    store.list(p.person_id); if (lease.read().status !== 'missing') fail(); present(current);
    const before = context(await t.context(action(p.original)), p, true, true); present(current);
    await t.verifySource(p, before);
    const after = context(await t.context(action(p.original)), p, true, true);
    if (canonical(before) !== canonical(after)) fail(); present(current);
    lease.persist(p);
    checkFact(await t.submit(p), p); // exactly one attempt; any uncertainty retains the saved command
    return finish(t, lease, p, current);
  });
}
export async function recover(t: Transport, store: Store, value: Pending, current = () => true): Promise<Resolution> {
  const p = await verifyPending(value);
  return store.withLease(p, lease => finish(t, lease, p, current));
}
export async function seal(t: Transport, store: Store, value: Pending, current = () => true): Promise<Resolution> {
  const p = await verifyPending(value);
  return store.withLease(p, async lease => {
    const observed = await readStable(t, lease, p, current, true);
    if (observed.status !== 'pending') { lease.clearExact(p); return observed; }
    const closed = resolution(await t.seal(p), p); if (closed.status === 'pending') fail();
    return finish(t, lease, p, current);
  });
}
