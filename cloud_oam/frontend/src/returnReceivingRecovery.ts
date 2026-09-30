/** One original request per recipient/parcel. Unknown writes are never replayed. */
import { canonical, fail, history, id, integer, object, parcel, receipt, type Identity, type Parcel, type Receipt } from './formalReturnReceiving';
import * as acceptance from './formalReturnReceipt';
import * as inbound from './formalReturnInbound';

type Base = Identity & { v: 1; package: Parcel };
export type Pending = Base & ({ kind: 'receipt'; command: acceptance.Command } | { kind: 'inbound'; receipt: Receipt; original: inbound.Original });
export type Context = Identity & { authority_hash: string; can_read: boolean; can_write: boolean };
export type Observation = { observed: false } | { observed: true; value: unknown };
export type Resolution = { status: 'pending' | 'accepted' | 'posted' | 'sealed' };
export type Transport = {
  context(): Promise<Context>;
  history(shipment: string): Promise<unknown>;
  state(value: Pending): Promise<unknown>;
  preview(value: Pending): Promise<unknown>;
  lookup(value: Pending): Promise<Observation>;
  submit(value: Pending): Promise<unknown>;
  seal(value: Pending): Promise<unknown>;
};
export function pending(value: unknown): Pending {
  const kind = (value as { kind?: unknown })?.kind;
  if (kind !== 'receipt' && kind !== 'inbound') fail();
  const r = object(value, ['v', 'kind', 'person_id', 'authorization_version', 'package', ...(kind === 'receipt' ? ['command'] : ['receipt', 'original'])]);
  if (r.v !== 1) fail();
  const who = { person_id: id(r.person_id), authorization_version: integer(r.authorization_version, 1) }, pkg = parcel(r.package, who);
  const base = { v: 1 as const, ...who, package: pkg };
  if (kind === 'receipt') return { ...base, kind, command: acceptance.command(r.command, who.person_id) };
  const original = object(r.original, ['receipt_id', 'shipment_id', 'target_location_id', 'target_custody_assignment_id', 'request_hash', 'command']);
  const accepted = receipt(r.receipt, who, pkg), c = inbound.command(original.command);
  if (c.operator_person_id !== who.person_id || original.receipt_id !== accepted.receipt_id || original.shipment_id !== pkg.shipment_id || original.target_location_id !== pkg.target_location_id || original.target_custody_assignment_id !== pkg.custody_assignment_id || typeof original.request_hash !== 'string' || !/^[a-f0-9]{64}$/.test(original.request_hash)) fail();
  return { ...base, kind, receipt: accepted, original: { receipt_id: accepted.receipt_id, shipment_id: pkg.shipment_id, target_location_id: pkg.target_location_id, target_custody_assignment_id: pkg.custody_assignment_id, request_hash: original.request_hash, command: c } };
}
async function verify(p: Pending): Promise<void> { if (p.kind === 'inbound') await inbound.original(p.original); }
export type Read = { kind: 'missing' | 'corrupt' | 'unavailable' } | { kind: 'valid'; value: Pending };
export type Lease = { read(): Read; persist(value: Pending): void; clearExact(value: Pending): void };
type Locks = { request<T>(name: string, options: { mode: 'exclusive'; ifAvailable: true }, fn: (lock: unknown | null) => Promise<T>): Promise<T> };
type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;
export type Store = { list(person: string): Pending[]; read(person: string, shipment: string): Read; withLease<T>(person: string, shipment: string, work: (lease: Lease) => Promise<T>): Promise<T> };
const PREFIX = 'cloud-oam-return-receiving-v1:';
export function createStore(storage: StorageLike | null, locks: Locks | null): Store {
  const active = new Set<string>(), faults = new Set<string>();
  const key = (person: string, shipment: string) => `${PREFIX}${id(person)}:${id(shipment)}`;
  const read = (k: string): Read => {
    if (!storage || faults.has(k)) return { kind: 'unavailable' };
    try {
      const raw = storage.getItem(k); if (raw === null) return { kind: 'missing' };
      try {
        if (raw.length > 2000000) return { kind: 'corrupt' };
        const data = JSON.parse(raw), p = pending(data);
        if (key(p.person_id, p.package.shipment_id) !== k || canonical(data) !== canonical(p)) return { kind: 'corrupt' };
        return { kind: 'valid', value: p };
      } catch { return { kind: 'corrupt' }; }
    } catch { return { kind: 'unavailable' }; }
  };
  return {
    read(person, shipment) { return read(key(person, shipment)); },
    list(person) {
      if (!storage) fail('本机请求存储不可用，已停止新验收和入库');
      const rows: Pending[] = [], prefix = `${PREFIX}${id(person)}:`;
      try {
        for (let i = 0; i < storage.length; i++) {
          const k = storage.key(i); if (!k?.startsWith(prefix)) continue;
          const row = read(k); if (row.kind !== 'valid') fail(); rows.push(row.value); if (rows.length > 500) fail();
        }
      } catch { fail('无法完整读取待核验原请求，请保留本机记录'); }
      return rows;
    },
    async withLease(person, shipment, work) {
      const k = key(person, shipment);
      if (!storage || !locks) fail('请使用支持 Web Locks 的 HTTPS 浏览器，确保原请求可靠保存');
      if (active.has(k)) fail('此包裹正在处理'); active.add(k);
      try {
        return await locks.request(k, { mode: 'exclusive', ifAvailable: true }, async lock => {
          if (!lock) fail('另一个页面正在处理此包裹'); let valid = true;
          const held = () => { if (!valid) fail('操作锁已结束'); };
          const lease: Lease = {
            read() { held(); return read(k); },
            persist(value) {
              held(); const p = pending(value), raw = canonical(p);
              if (key(p.person_id, p.package.shipment_id) !== k || read(k).kind !== 'missing') fail('包裹已有未知请求，禁止覆盖');
              if (raw.length > 2000000) fail('原请求超出本机保存上限');
              try { storage.setItem(k, raw); } catch { faults.add(k); fail('原请求保存失败，未发送操作'); }
              const saved = read(k); if (saved.kind !== 'valid' || canonical(saved.value) !== raw) { faults.add(k); fail('保存未确认，未发送操作'); }
            },
            clearExact(value) {
              held(); const saved = read(k); if (saved.kind !== 'valid' || canonical(saved.value) !== canonical(value)) fail('原请求已变化，禁止清理');
              try { storage.removeItem(k); if (storage.getItem(k) !== null) throw new Error(); } catch { faults.add(k); fail('结果已核验，但本机记录清理未确认'); }
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
  try { storage = globalThis.localStorage; locks = globalThis.navigator?.locks ?? null; } catch { /* store fails closed */ }
  return createStore(storage, locks);
}
function context(value: Context, p: Pending, write = false): Context {
  if (id(value.person_id) !== p.person_id || integer(value.authorization_version, 1) < p.authorization_version || value.can_read !== true || typeof value.can_write !== 'boolean' || (write && !value.can_write) || !/^[a-f0-9]{64}$/.test(value.authority_hash)) fail('当前身份或权限无法核验原请求');
  return { person_id: value.person_id, authorization_version: value.authorization_version, authority_hash: value.authority_hash, can_read: true, can_write: value.can_write };
}
function present(isCurrent: () => boolean) { if (!isCurrent()) fail('页面或身份已变化，原请求保留'); }
async function saved(lease: Lease, p: Pending): Promise<void> {
  const r = lease.read(); if (r.kind !== 'valid' || canonical(r.value) !== canonical(p)) fail('原请求已变化或不可读'); await verify(p);
}
async function resolution(raw: unknown, p: Pending): Promise<Resolution> {
  return p.kind === 'receipt' ? acceptance.lookup(raw, p, p.package, p.command) : inbound.lookup(raw, p.original);
}
async function finish(transport: Transport, lease: Lease, p: Pending, isCurrent: () => boolean): Promise<Resolution> {
  await saved(lease, p); present(isCurrent);
  const before = context(await transport.context(), p); present(isCurrent);
  const observed = await transport.lookup(p), result = observed.observed ? await resolution(observed.value, p) : { status: 'pending' as const };
  const after = context(await transport.context(), p);
  if (canonical(before) !== canonical(after)) fail('回查期间权限变化，原请求保留');
  present(isCurrent); await saved(lease, p);
  if (result.status !== 'pending') lease.clearExact(p); return result;
}
export async function recover(transport: Transport, store: Store, value: Pending, isCurrent = () => true): Promise<Resolution> {
  const p = pending(value); await verify(p);
  return store.withLease(p.person_id, p.package.shipment_id, lease => finish(transport, lease, p, isCurrent));
}
export async function submit(transport: Transport, store: Store, value: Pending, isCurrent = () => true): Promise<Resolution> {
  const p = pending(value); await verify(p);
  return store.withLease(p.person_id, p.package.shipment_id, async lease => {
    if (lease.read().kind !== 'missing') fail('此包裹已有待核验原请求'); present(isCurrent);
    const before = context(await transport.context(), p, true);
    // A changed authorization version requires a newly confirmed preview before first send.
    if (before.authorization_version !== p.authorization_version) fail('权限版本变化，请重新预检并确认');
    const current = history(await transport.history(p.package.shipment_id), before, p.package.shipment_id);
    if (canonical(current.package) !== canonical(p.package)) fail('原包裹或接收责任已变化');
    if (p.kind === 'receipt') {
      const { expected_plan_hash, request_id: _r, idempotency_key: _k, ...body } = p.command;
      const plan = await acceptance.preview(await transport.preview(p), before, body, current);
      if (plan.plan_hash !== expected_plan_hash) fail('验收方案变化，请重新确认');
    } else {
      const accepted = current.receipts.find(r => r.receipt_id === p.receipt.receipt_id);
      if (!accepted || canonical(accepted) !== canonical(p.receipt)) fail('原验收事实无法核验');
      if (inbound.state(await transport.state(p), before, accepted).status !== 'not_posted') fail('此验收已入库，请刷新');
      if (inbound.preview(await transport.preview(p), before, accepted).plan_hash !== p.original.command.expected_plan_hash) fail('入库方案变化，请重新确认');
    }
    const after = context(await transport.context(), p, true); if (canonical(before) !== canonical(after)) fail('提交前权限变化'); present(isCurrent);
    lease.persist(p);
    // Exactly one POST; any error, lost response or interrupted UI keeps the original.
    await resolution(await transport.submit(p), p);
    return finish(transport, lease, p, isCurrent);
  });
}
export async function seal(transport: Transport, store: Store, value: Pending, confirmed = false, isCurrent = () => true): Promise<Resolution> {
  if (!confirmed) fail('永久封存需要单独明确确认');
  const p = pending(value); await verify(p);
  return store.withLease(p.person_id, p.package.shipment_id, async lease => {
    await saved(lease, p); present(isCurrent);
    const before = context(await transport.context(), p, true), observed = await transport.lookup(p);
    const resolved = observed.observed ? await resolution(observed.value, p) : null;
    const after = context(await transport.context(), p, true); if (canonical(before) !== canonical(after)) fail('封存前权限变化'); present(isCurrent); await saved(lease, p);
    if (resolved) { lease.clearExact(p); return resolved; }
    await resolution(await transport.seal(p), p);
    return finish(transport, lease, p, isCurrent);
  });
}
