import { expect, it, vi } from 'vitest';
import { fixtures, saved } from './lossCorrectionFixtures';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import { createStore, execute, recover, seal, type Context, type Transport } from './lossCorrectionRecovery';
import type { Pending } from './lossCorrectionContracts';

async function harness(f: typeof fixtures[number], sealed = false) {
  const p = await saved(f, sealed), storage = new MemoryStorage(), mutex = locks(), store = createStore(storage, mutex);
  const context: Context = { person_id: p.person_id, authorization_version: p.authorization_version, authority_hash: 'a'.repeat(64),
    can_read: true, can_write: { inverses: true, approvals: true, executions: true } };
  const transport = {
    context: vi.fn(async () => structuredClone(context)),
    source: vi.fn(async () => structuredClone(p.source)),
    lookup: vi.fn(async (): Promise<unknown> => structuredClone(f.data.found)),
    execute: vi.fn(async (value: Pending) => {
      expect(store.read(p.person_id, p.command.root_disposition_id)).toEqual({ kind: 'valid', value });
      return structuredClone(f.data.found.result);
    }),
    seal: vi.fn(async () => structuredClone(f.data.sealed)),
  } satisfies Transport;
  const persist = () => store.withLease(p.person_id, p.command.root_disposition_id, async lease => lease.persist(p));
  return { p, storage, mutex, store, context, transport, persist };
}

it.each(fixtures)('$name persists before exactly one write, then clears only after exact lookup', async f => {
  const h = await harness(f);
  await expect(execute(h.transport, h.store, h.p)).resolves.toEqual({ status: 'found' });
  expect(h.transport.execute).toHaveBeenCalledTimes(1);
  expect(h.transport.lookup).toHaveBeenCalledTimes(1);
  expect(h.transport.seal).not.toHaveBeenCalled();
  expect(h.store.list(h.p.person_id)).toEqual([]);
});

it.each(fixtures)('$name retains unknown writes across reload; misses cannot authorize replay', async f => {
  const h = await harness(f);
  h.transport.execute.mockRejectedValueOnce(new Error('synthetic response lost'));
  await expect(execute(h.transport, h.store, h.p)).rejects.toThrow('response lost');
  expect(h.store.list(h.p.person_id)).toEqual([h.p]);
  const reloaded = createStore(h.storage, h.mutex);
  h.transport.lookup.mockResolvedValueOnce(f.data.missing);
  await expect(recover(h.transport, reloaded, h.p)).resolves.toEqual({ status: 'pending' });
  await expect(execute(h.transport, reloaded, h.p)).rejects.toThrow();
  expect(h.transport.execute).toHaveBeenCalledTimes(1);
  expect(h.transport.seal).not.toHaveBeenCalled();
  h.context.can_write[h.p.flow] = false;
  await expect(recover(h.transport, reloaded, h.p)).resolves.toEqual({ status: 'found' });
  expect(reloaded.list(h.p.person_id)).toEqual([]);
});

it.each(fixtures)('$name read revocation or mismatched response never clears the original', async f => {
  const h = await harness(f); await h.persist();
  h.context.can_read = false;
  await expect(recover(h.transport, h.store, h.p)).rejects.toThrow();
  expect(h.transport.lookup).not.toHaveBeenCalled();
  h.context.can_read = true;
  h.transport.lookup.mockResolvedValueOnce({ ...f.data.found, request_hash: 'f'.repeat(64) });
  await expect(recover(h.transport, h.store, h.p)).rejects.toThrow();
  expect(h.store.list(h.p.person_id)).toEqual([h.p]);
  expect(h.transport.execute).not.toHaveBeenCalled(); expect(h.transport.seal).not.toHaveBeenCalled();
});

it.each(fixtures)('$name requires its exact action right, never borrows another write right', async f => {
  const h = await harness(f); h.context.can_write[h.p.flow] = false;
  await expect(execute(h.transport, h.store, h.p)).rejects.toThrow();
  expect(h.storage.length).toBe(0); expect(h.transport.execute).not.toHaveBeenCalled();
  await h.persist(); await expect(seal(h.transport, h.store, h.p)).rejects.toThrow();
  expect(h.transport.seal).not.toHaveBeenCalled(); expect(h.store.list(h.p.person_id)).toEqual([h.p]);
});

it.each(fixtures)('$name explicit seal checks absence and verifies the same sealed request', async f => {
  const h = await harness(f, true); await h.persist();
  h.transport.lookup.mockResolvedValueOnce({ ...(f.data.missing as object), request_id: h.p.command.request_id, request_hash: h.p.request_hash })
    .mockResolvedValueOnce(f.data.sealed);
  await expect(seal(h.transport, h.store, h.p)).resolves.toEqual({ status: 'sealed' });
  expect(h.transport.seal).toHaveBeenCalledExactlyOnceWith(h.p); expect(h.transport.execute).not.toHaveBeenCalled();
  expect(h.transport.lookup).toHaveBeenCalledTimes(2); expect(h.store.list(h.p.person_id)).toEqual([]);
});

it('failed local persistence and a competing tab prevent HTTP writes', async () => {
  const h = await harness(fixtures[0]);
  vi.spyOn(h.storage, 'setItem').mockImplementationOnce(() => { throw new Error('synthetic quota'); });
  await expect(execute(h.transport, h.store, h.p)).rejects.toThrow();
  expect(h.transport.execute).not.toHaveBeenCalled();
  const other = await harness(fixtures[1]); const second = createStore(other.storage, other.mutex);
  await other.store.withLease(other.p.person_id, other.p.command.root_disposition_id, async () => {
    await expect(execute(other.transport, second, other.p)).rejects.toThrow();
  });
  expect(other.transport.execute).not.toHaveBeenCalled();
});
