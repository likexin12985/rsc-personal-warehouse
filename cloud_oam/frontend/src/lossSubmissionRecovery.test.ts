import { beforeAll, expect, it, vi } from 'vitest';
import quantity from './test-fixtures/loss-submission/loss-submission-quantity-found.json';
import serial from './test-fixtures/loss-submission/loss-submission-serial-found.json';
import sealedFixture from './test-fixtures/loss-submission/loss-submission-quantity-sealed.json';
import { canonical } from './formalReturnReceiving';
import { pending, verifyPending, type Pending } from './formalLossSubmission';
import { createStore, submit, recover, seal, type Context, type Transport } from './lossSubmissionRecovery';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
const fixtures = [quantity, serial];
type Fixture = typeof quantity | typeof serial | typeof sealedFixture;
function original(f: Fixture = quantity) { return pending({ v: 1, ...f.identity, sources: f.sources, command: f.command, preview: f.preview }); }
function memory() {
  const map = new Map<string, string>();
  return { map, get length() { return map.size; }, key: (i: number) => [...map.keys()][i] ?? null,
    getItem: (k: string) => map.get(k) ?? null, setItem: (k: string, v: string) => { map.set(k, v); }, removeItem: (k: string) => { map.delete(k); } };
}
const locks: NonNullable<Parameters<typeof createStore>[1]> = { request: async (_key, _options, fn) => fn({}) };
function transport(f: Fixture = quantity) {
  return {
    context: vi.fn(async (): Promise<Context> => ({ ...f.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: true })),
    sources: vi.fn(async (): Promise<unknown> => f.sources), preview: vi.fn(async (): Promise<unknown> => f.preview),
    lookup: vi.fn(async (): Promise<unknown> => f.observed), submit: vi.fn(async (): Promise<unknown> => f.result),
    seal: vi.fn(async (): Promise<unknown> => sealedFixture.observed),
  } satisfies Transport;
}
async function retain(store: ReturnType<typeof createStore>, p: Pending) { await store.withLease(p.person_id, p.sources.location_id, async lease => lease.persist(p)); }
for (const [i, f] of fixtures.entries()) {
  it(`${i}: persists before one submission and clears only after exact readback`, async () => {
    const p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f);
    t.submit.mockImplementation(async () => { expect(store.read(p.person_id, p.sources.location_id).kind).toBe('valid'); return f.result; });
    expect((await submit(t, store, p)).status).toBe('submitted');
    expect(t.submit).toHaveBeenCalledTimes(1); expect(t.lookup).toHaveBeenCalledTimes(1); expect(storage.length).toBe(0);
  });
  it(`${i}: lost response survives refresh without a second submission`, async () => {
    const p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f);
    t.submit.mockRejectedValueOnce(new Error('unknown COMMIT response'));
    await expect(submit(t, store, p)).rejects.toThrow('unknown COMMIT');
    const reopened = createStore(storage, locks); expect(reopened.list(p.person_id)).toEqual([p]);
    t.lookup.mockResolvedValueOnce(f.missing);
    expect(await recover(t, reopened, p)).toEqual({ status: 'unknown' }); expect(storage.length).toBe(1);
    await expect(submit(t, reopened, p)).rejects.toThrow('未知报损');
    expect((await recover(t, reopened, p)).status).toBe('submitted'); expect(t.submit).toHaveBeenCalledTimes(1);
  });
  it(`${i}: a successful POST cannot clear after a failed lookup`, async () => {
    const p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f);
    t.lookup.mockRejectedValueOnce(new Error('503'));
    await expect(submit(t, store, p)).rejects.toThrow('503'); expect(storage.length).toBe(1);
    await expect(submit(t, store, p)).rejects.toThrow(); expect(t.submit).toHaveBeenCalledTimes(1);
  });
  it(`${i}: write revocation preserves read-only recovery and forbids sealing`, async () => {
    const p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f); await retain(store, p);
    t.context.mockResolvedValue({ ...f.identity, authorization_version: f.identity.authorization_version + 1, authority_hash: 'b'.repeat(64), can_read: true, can_write: false });
    await expect(seal(t, store, p, true)).rejects.toThrow(); expect(t.seal).not.toHaveBeenCalled();
    expect((await recover(t, store, p)).status).toBe('submitted'); expect(t.submit).not.toHaveBeenCalled();
  });
  it(`${i}: stale preview, changed authority and quota failure produce zero submissions`, async () => {
    const p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f);
    t.preview.mockResolvedValueOnce({ ...f.preview, plan_hash: 'f'.repeat(64) });
    await expect(submit(t, store, p)).rejects.toThrow('方案');
    t.context.mockResolvedValueOnce({ ...f.identity, authorization_version: f.identity.authorization_version + 1, authority_hash: 'a'.repeat(64), can_read: true, can_write: true });
    await expect(submit(t, store, p)).rejects.toThrow('权限版本');
    storage.setItem = () => { throw new Error('quota'); };
    await expect(submit(t, store, p)).rejects.toThrow('保存失败'); expect(t.submit).not.toHaveBeenCalled();
  });
}
it('permanent sealing needs explicit confirmation and exact terminal readback', async () => {
  const f = sealedFixture, p = original(f), storage = memory(), store = createStore(storage, locks), t = transport(f); await retain(store, p);
  await expect(seal(t, store, p)).rejects.toThrow('明确确认'); expect(t.seal).not.toHaveBeenCalled();
  t.lookup.mockResolvedValueOnce(f.missing).mockResolvedValueOnce(f.observed);
  expect((await seal(t, store, p, true)).status).toBe('sealed'); expect(t.seal).toHaveBeenCalledTimes(1); expect(storage.length).toBe(0);
});
it('already executed original is recovered instead of sending a seal', async () => {
  const p = original(), storage = memory(), store = createStore(storage, locks), t = transport(); await retain(store, p);
  expect((await seal(t, store, p, true)).status).toBe('submitted'); expect(t.seal).not.toHaveBeenCalled();
});
it('authority change during recovery retains original', async () => {
  const p = original(), storage = memory(), store = createStore(storage, locks), t = transport(); await retain(store, p);
  t.context.mockResolvedValueOnce({ ...quantity.identity, authority_hash: 'a'.repeat(64), can_read: true, can_write: true })
    .mockResolvedValueOnce({ ...quantity.identity, authority_hash: 'b'.repeat(64), can_read: true, can_write: true });
  await expect(recover(t, store, p)).rejects.toThrow('权限变化'); expect(storage.length).toBe(1);
});
it('malformed or modified saved request cannot be read as a successful recovery', async () => {
  const p = original(), storage = memory(), store = createStore(storage, locks), t = transport(); await retain(store, p);
  const k = storage.key(0)!; storage.setItem(k, '{broken');
  expect(() => store.list(p.person_id)).toThrow(); await expect(recover(t, store, p)).rejects.toThrow(); expect(t.lookup).not.toHaveBeenCalled();
  const corrupt = { ...p, preview: { ...p.preview, request_hash: 'f'.repeat(64) } };
  storage.setItem(k, canonical(corrupt));
  await expect(recover(t, store, store.list(p.person_id)[0])).rejects.toThrow(); expect(t.lookup).not.toHaveBeenCalled(); expect(storage.length).toBe(1);
});
it('navigation after write preserves the original even when the response is successful', async () => {
  const p = original(), storage = memory(), store = createStore(storage, locks), t = transport(); let visible = true;
  t.submit.mockImplementation(async () => { visible = false; return quantity.result; });
  await expect(submit(t, store, p, () => visible)).rejects.toThrow(); expect(storage.length).toBe(1); expect(t.lookup).not.toHaveBeenCalled();
});
it('storage readback, missing locks and competing tab fail before submission', async () => {
  const p = original(), t = transport(), storage = memory(); storage.setItem = () => {};
  await expect(submit(t, createStore(storage, locks), p)).rejects.toThrow('保存未确认');
  await expect(submit(t, createStore(memory(), null), p)).rejects.toThrow('Web Locks');
  const busy: typeof locks = { request: async (_k, _o, fn) => fn(null) };
  await expect(submit(t, createStore(memory(), busy), p)).rejects.toThrow('另一个页面'); expect(t.submit).not.toHaveBeenCalled();
});
it('rechecks original command digest', async () => {
  const p = original(); expect(await verifyPending(p)).toEqual(p);
  await expect(verifyPending({ ...p, command: { ...p.command, reason: 'modified reason' } })).rejects.toThrow();
});
