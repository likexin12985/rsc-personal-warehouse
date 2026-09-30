import { beforeAll, describe, expect, it, vi } from 'vitest';
import fixture from './test-fixtures/return-receiving/loss-receiving-serial.json';
import { canonical, history, source } from './formalReturnReceiving';
import { pending, createStore, submit, recover, seal, type Context, type Observation, type Pending, type Transport } from './returnReceivingRecovery';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
const other = '11111111-1111-4111-8111-111111111111';
function original(kind: 'receipt' | 'inbound'): Pending {
  const h = history(fixture.after, fixture.identity, fixture.after.package.shipment_id);
  return pending({ v: 1, kind, ...fixture.identity, package: h.package, ...(kind === 'receipt' ? { command: fixture.command } : {
    receipt: h.receipts[0], original: { receipt_id: fixture.receipt.receipt_id, shipment_id: h.package.shipment_id, target_location_id: h.package.target_location_id, target_custody_assignment_id: h.package.custody_assignment_id, request_hash: fixture.inbound.posted.request_hash, command: fixture.inbound.command },
  }) });
}
function memory() {
  const map = new Map<string, string>();
  return { map, get length() { return map.size; }, key: (i: number) => [...map.keys()][i] ?? null, getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => { map.set(k, v); }, removeItem: (k: string) => { map.delete(k); } };
}
const locks: NonNullable<Parameters<typeof createStore>[1]> = { request: async (_key, _options, fn) => fn({}) };
function transport(p: Pending) {
  const fact = p.kind === 'receipt' ? fixture.receipt : fixture.inbound.posted;
  const sealFact = { schema_version: '1.0', lookup_status: 'sealed', seal: p.kind === 'receipt' ? {
    seal_id: other, operation_type: 'receive_return', operator_person_id: p.person_id, ...source(p.package), operation_id: p.package.operation_id, shipment_id: p.package.shipment_id, request_id: p.command.request_id, request_hash: fixture.receipt.request_hash, sealed_at: fixture.inbound.after.checked_at,
  } : { seal_id: other, receipt_id: p.receipt.receipt_id, shipment_id: p.package.shipment_id, request_id: p.original.command.request_id, request_hash: p.original.request_hash, sealed_at: fixture.inbound.after.checked_at } };
  const t = {
    context: vi.fn(async (): Promise<Context> => ({ ...fixture.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: true })),
    history: vi.fn(async () => p.kind === 'receipt' ? fixture.before : fixture.after),
    state: vi.fn(async () => fixture.inbound.before),
    preview: vi.fn(async () => p.kind === 'receipt' ? fixture.preview : fixture.inbound.preview),
    lookup: vi.fn(async (): Promise<Observation> => ({ observed: true, value: fact })),
    submit: vi.fn(async (): Promise<unknown> => fact),
    seal: vi.fn(async (): Promise<unknown> => sealFact),
  } satisfies Transport;
  return { t, sealFact };
}
async function retain(store: ReturnType<typeof createStore>, p: Pending) {
  await store.withLease(p.person_id, p.package.shipment_id, async l => l.persist(p));
}
describe('receipt and inbound original-request recovery', () => {
  for (const kind of ['receipt', 'inbound'] as const) {
    it(`${kind}: posts once, then only exact current-authority readback clears`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p);
      t.submit.mockImplementation(async () => { expect(store.read(p.person_id, p.package.shipment_id).kind).toBe('valid'); return kind === 'receipt' ? fixture.receipt : fixture.inbound.posted; });
      expect((await submit(t, store, p)).status).toBe(kind === 'receipt' ? 'accepted' : 'posted');
      expect(t.submit).toHaveBeenCalledTimes(1); expect(t.lookup).toHaveBeenCalledTimes(1); expect(storage.length).toBe(0);
    });
    it(`${kind}: lost POST and refresh recover only the saved original`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p);
      t.submit.mockRejectedValueOnce(new Error('response lost after COMMIT'));
      await expect(submit(t, store, p)).rejects.toThrow('response lost');
      const reopened = createStore(storage, locks), saved = reopened.list(p.person_id)[0];
      expect(saved).toEqual(p);
      expect((await recover(t, reopened, saved)).status).toBe(kind === 'receipt' ? 'accepted' : 'posted');
      expect(t.submit).toHaveBeenCalledTimes(1); expect(storage.length).toBe(0);
    });
    it(`${kind}: not-observed remains pending; write permission revoked still permits exact reading`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p); await retain(store, p);
      t.context.mockResolvedValue({ ...fixture.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: false });
      t.lookup.mockResolvedValue({ observed: false });
      expect(await recover(t, store, p)).toEqual({ status: 'pending' }); expect(storage.length).toBe(1); expect(t.submit).not.toHaveBeenCalled();
      await expect(seal(t, store, p, true)).rejects.toThrow(); expect(t.seal).not.toHaveBeenCalled();
    });
    it(`${kind}: a POST success with unproven GET cannot clear`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p);
      t.lookup.mockRejectedValueOnce(new Error('503 gateway error'));
      await expect(submit(t, store, p)).rejects.toThrow('503'); expect(storage.length).toBe(1);
      await expect(submit(t, store, p)).rejects.toThrow(); expect(t.submit).toHaveBeenCalledTimes(1);
    });
    it(`${kind}: identity or authority change during readback retains the record`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p); await retain(store, p);
      t.context.mockResolvedValueOnce({ ...fixture.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: true });
      t.context.mockResolvedValueOnce({ ...fixture.identity, authority_hash: 'b'.repeat(64), can_read: true, can_write: true });
      await expect(recover(t, store, p)).rejects.toThrow(); expect(storage.length).toBe(1);
      t.context.mockResolvedValue({ ...fixture.identity, person_id: other, authority_hash: 'a'.repeat(64), can_read: true, can_write: true });
      await expect(recover(t, store, p)).rejects.toThrow(); expect(storage.length).toBe(1);
    });
    it(`${kind}: permanent sealing requires confirmation and a matching readback`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t, sealFact } = transport(p); await retain(store, p);
      await expect(seal(t, store, p)).rejects.toThrow(); expect(t.seal).not.toHaveBeenCalled();
      t.lookup.mockResolvedValueOnce({ observed: false }).mockResolvedValueOnce({ observed: true, value: sealFact });
      expect((await seal(t, store, p, true)).status).toBe('sealed'); expect(storage.length).toBe(0); expect(t.seal).toHaveBeenCalledTimes(1);
    });
    it(`${kind}: stale preflight and read-only scope cause zero POST`, async () => {
      const p = original(kind), storage = memory(), store = createStore(storage, locks), { t } = transport(p);
      const v = kind === 'receipt' ? fixture.preview : fixture.inbound.preview;
      t.preview.mockResolvedValue({ ...v, plan_hash: 'b'.repeat(64) });
      await expect(submit(t, store, p)).rejects.toThrow(); expect(t.submit).not.toHaveBeenCalled(); expect(storage.length).toBe(0);
      t.context.mockResolvedValue({ ...fixture.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: false });
      await expect(submit(t, store, p)).rejects.toThrow(); expect(t.submit).not.toHaveBeenCalled();
    });
  }
  it('one shared parcel lock blocks acceptance while an inbound request is pending', async () => {
    const storage = memory(), store = createStore(storage, locks), inbound = original('inbound'), acceptance = original('receipt'); await retain(store, inbound);
    const { t } = transport(acceptance); await expect(submit(t, store, acceptance)).rejects.toThrow();
    expect(t.submit).not.toHaveBeenCalled(); expect(store.list(inbound.person_id)).toEqual([inbound]);
  });
  it('storage error and unavailable Web Locks fail before writing', async () => {
    const p = original('receipt'), { t } = transport(p), storage = memory();
    storage.setItem = () => { throw new Error('quota'); };
    await expect(submit(t, createStore(storage, locks), p)).rejects.toThrow(); expect(t.submit).not.toHaveBeenCalled();
    await expect(submit(t, createStore(memory(), null), p)).rejects.toThrow();
    const busy: typeof locks = { request: async (_k, _o, fn) => fn(null) };
    await expect(submit(t, createStore(memory(), busy), p)).rejects.toThrow(); expect(t.submit).not.toHaveBeenCalled();
  });
  it('corrupt storage and changed original cannot be overwritten or cleared', async () => {
    const p = original('inbound'), storage = memory(), store = createStore(storage, locks), { t } = transport(p); await retain(store, p);
    const key = storage.key(0)!; storage.setItem(key, '{broken');
    expect(store.read(p.person_id, p.package.shipment_id).kind).toBe('corrupt'); expect(() => store.list(p.person_id)).toThrow();
    await expect(recover(t, store, p)).rejects.toThrow(); expect(t.lookup).not.toHaveBeenCalled(); expect(storage.length).toBe(1);
    storage.setItem(key, canonical({ ...p, original: { ...(p.kind === 'inbound' ? p.original : {}), request_hash: 'f'.repeat(64) } }));
    await expect(recover(t, store, store.list(p.person_id)[0])).rejects.toThrow(); expect(storage.length).toBe(1);
  });
  it('navigation during an unknown POST retains the original even after it succeeds', async () => {
    const p = original('receipt'), storage = memory(), store = createStore(storage, locks), { t } = transport(p); let mounted = true;
    t.submit.mockImplementation(async () => { mounted = false; return fixture.receipt; });
    await expect(submit(t, store, p, () => mounted)).rejects.toThrow(); expect(storage.length).toBe(1); expect(t.lookup).not.toHaveBeenCalled();
  });
});
