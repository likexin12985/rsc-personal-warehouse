import { expect, it, vi } from 'vitest';
import fixtures from './test-fixtures/return-condition/command-hashes.json';
import { prepare, type Pending } from './returnConditionCommands';
import { createStore, submit, recover, seal, type Context, type Transport, type Lease } from './returnConditionRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import type { Action } from './returnConditionHistory';

async function world(sample = fixtures.samples[0]) {
  const p = await prepare(sample.person_id, sample.authorization_version, sample.inbound_line_id, sample.original);
  const c = p.original, kind = sample.kind as Action;
  const storage = new MemoryStorage(), lock = locks(), store = createStore(storage, lock);
  const context: Context = { person_id: p.person_id, authorization_version: 1, action: kind,
    authority_hash: 'a'.repeat(64), can_read: true, can_write: true };
  const states: Record<Action, string> = { submit: 'awaiting_regional', supplement: 'awaiting_regional', withdraw: 'cancelled_pending_release',
    verify_region: 'awaiting_headquarters', return_evidence: 'needs_evidence', reject_region: 'rejected_pending_release',
    return_region: 'awaiting_regional', reject_hq: 'rejected_pending_release', approve_hq: 'approved',
    cancel_approved: 'cancelled_pending_release', execute: 'executed', release: 'released_cancelled' };
  const effect = ({ submit: 'freeze', execute: 'status_change', release: 'unfreeze' } as Record<string, string>)[kind] ?? 'none';
  // Synthetic business outcomes isolate transport uncertainty; hashes above use real Python canonicalization.
  const fact = { schema_version: 'condition_result/1', case_id: 'case_id' in c ? c.case_id : crypto.randomUUID(),
    event_id: crypto.randomUUID(), inbound_line_id: p.inbound_line_id, action: kind, status: states[kind], quantity: '2.000',
    actor_user_id: 'synthetic-user', actor_person_id: p.person_id, authorization_version: 1, request_id: c.request_id,
    request_hash: 'd'.repeat(64), plan_hash: 'e'.repeat(64), posting_transaction_id: effect !== 'none' ? crypto.randomUUID() : null,
    posting_movement_id: effect !== 'none' ? crypto.randomUUID() : null, stock_effect: effect, reason: c.reason };
  const base = { request_id: c.request_id, retry_allowed: false, current_stock_verified: false, observed_ledger_cursor: 25 };
  const found = { ...base, request_state: 'found', result_scope: 'historical_original_outcome', result: fact,
    absence_sealed: false, current_case_status: 'executed', original_input_hash: p.original_input_hash };
  const unknown = { ...base, request_state: 'unknown', result_scope: 'historical_original_outcome', result: null, absence_sealed: false };
  const sealed = { ...base, request_state: 'sealed', result_scope: 'closed_original_request', result: null,
    absence_sealed: true, original_preflight_verified: false, stock_effect: 'none', original_input_hash: p.original_input_hash,
    seal: { seal_id: crypto.randomUUID(), kind, inbound_line_id: p.inbound_line_id, case_id: 'case_id' in c ? c.case_id : null,
      expected_event_id: 'expected_event_id' in c ? c.expected_event_id : null, sealed_at: new Date().toISOString(), stock_effect: 'none' } };
  const t: Transport = { context: vi.fn(async () => ({ ...context })), verifySource: vi.fn(async () => {}),
    submit: vi.fn(async () => { expect(store.read(p.person_id, p.inbound_line_id)).toEqual({ status: 'valid', value: p }); return fact; }),
    lookup: vi.fn(async () => found), seal: vi.fn(async () => sealed) };
  const retain = () => store.withLease(p, async lease => lease.persist(p));
  return { p, context, storage, lock, store, t, fact, found, unknown, sealed, retain };
}
it.each(fixtures.samples)('$kind saves before one write, then clears only after exact readback', async sample => {
  const w = await world(sample); expect((await submit(w.t, w.store, w.p)).status).toBe('found');
  expect(w.t.submit).toHaveBeenCalledTimes(1); expect(w.t.lookup).toHaveBeenCalledTimes(1);
  expect(w.store.list(w.p.person_id)).toEqual([]);
  expect(w.fact.request_hash).not.toBe(w.p.original_input_hash);
});
it.each(fixtures.samples)('$kind survives lost response, refresh and removal of write permission without replay', async sample => {
  const w = await world(sample); vi.mocked(w.t.submit).mockRejectedValueOnce(new Error('lost response'));
  await expect(submit(w.t, w.store, w.p)).rejects.toThrow('lost response');
  const fresh = createStore(w.storage, w.lock); w.context.can_write = false; w.context.authorization_version++;
  expect((await recover(w.t, fresh, w.p)).status).toBe('found');
  expect(w.t.submit).toHaveBeenCalledTimes(1); expect(w.t.verifySource).toHaveBeenCalledTimes(1);
  expect(w.t.seal).not.toHaveBeenCalled(); expect(fresh.list(w.p.person_id)).toEqual([]);
});
it.each(fixtures.samples)('$kind unknown blocks replacement requests on the same inbound', async sample => {
  const w = await world(sample); await w.retain(); vi.mocked(w.t.lookup).mockResolvedValue(w.unknown);
  expect(await recover(w.t, w.store, w.p)).toEqual({ status: 'pending' });
  const replacement = await prepare(w.p.person_id, 1, w.p.inbound_line_id, { ...w.p.original,
    request_id: 'different-request', idempotency_key: 'different-key' });
  await expect(submit(w.t, w.store, replacement)).rejects.toThrow();
  expect(w.t.submit).not.toHaveBeenCalled(); expect(w.store.list(w.p.person_id)).toEqual([w.p]);
});
it.each(fixtures.samples)('$kind explicit seal reads first and verifies the final result before clearing', async sample => {
  const w = await world(sample); await w.retain();
  vi.mocked(w.t.lookup).mockResolvedValueOnce(w.unknown).mockResolvedValueOnce(w.sealed);
  expect(await seal(w.t, w.store, w.p)).toEqual({ status: 'sealed' });
  expect(w.t.seal).toHaveBeenCalledExactlyOnceWith(w.p); expect(w.t.submit).not.toHaveBeenCalled();
  expect(w.t.lookup).toHaveBeenCalledTimes(2); expect(w.store.list(w.p.person_id)).toEqual([]);
});
it('retains a lost seal response and later recovers it without a second seal', async () => {
  const w = await world(); await w.retain(); vi.mocked(w.t.lookup).mockResolvedValue(w.unknown);
  vi.mocked(w.t.seal).mockRejectedValue(new Error('lost closure'));
  await expect(seal(w.t, w.store, w.p)).rejects.toThrow('lost closure');
  expect(w.store.list(w.p.person_id)).toEqual([w.p]); w.context.can_write = false;
  vi.mocked(w.t.lookup).mockResolvedValue(w.sealed);
  expect((await recover(w.t, w.store, w.p)).status).toBe('sealed'); expect(w.t.seal).toHaveBeenCalledTimes(1);
});
it('found avoids sending a seal, and malformed results never clear a saved request', async () => {
  const w = await world(); await w.retain();
  for (const change of [{ original_input_hash: w.fact.request_hash }, { retry_allowed: true }, { current_stock_verified: true },
    { result: { ...w.fact, inbound_line_id: crypto.randomUUID() } }, { result: { ...w.fact, actor_person_id: crypto.randomUUID() } },
    { result: { ...w.fact, reason: 'different' } }]) {
    vi.mocked(w.t.lookup).mockResolvedValue({ ...w.found, ...change });
    await expect(recover(w.t, w.store, w.p)).rejects.toThrow(); expect(w.store.list(w.p.person_id)).toEqual([w.p]);
  }
  vi.mocked(w.t.lookup).mockResolvedValue(w.found);
  expect((await seal(w.t, w.store, w.p)).status).toBe('found'); expect(w.t.seal).not.toHaveBeenCalled();
});
it('does not write if persistent storage, identity, current source or permission validation fails', async () => {
  for (const failure of ['storage', 'permission', 'identity', 'source'] as const) {
    const w = await world();
    if (failure === 'storage') vi.spyOn(w.storage, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    if (failure === 'permission') w.context.can_write = false;
    if (failure === 'identity') w.context.authorization_version++;
    if (failure === 'source') vi.mocked(w.t.verifySource).mockRejectedValue(new Error('source moved'));
    await expect(submit(w.t, w.store, w.p)).rejects.toThrow(); expect(w.t.submit).not.toHaveBeenCalled();
  }
});
it('retains request if identity or current page changes during lookup', async () => {
  for (const mode of ['identity', 'page'] as const) {
    const w = await world(); await w.retain(); let current = true;
    vi.mocked(w.t.lookup).mockImplementation(async () => { if (mode === 'identity') w.context.authorization_version++;
      else current = false; return w.found; });
    await expect(recover(w.t, w.store, w.p, () => current)).rejects.toThrow();
    expect(w.store.list(w.p.person_id)).toEqual([w.p]);
  }
});
it('coordinates two browser tabs across different actions and expires the lease', async () => {
  const w = await world(), second = createStore(w.storage, w.lock); let old!: Lease;
  await w.store.withLease(w.p, async lease => {
    old = lease; await expect(second.withLease(w.p, async () => {})).rejects.toThrow();
    lease.persist(w.p);
  });
  expect(() => old.read()).toThrow(); expect(second.list(w.p.person_id)).toEqual([w.p]);
});
it('refuses a corrupt saved request instead of discarding it', async () => {
  const w = await world(); await w.retain(); const key = w.storage.key(0)!;
  const corrupted: Pending = { ...w.p, original_input_hash: '0'.repeat(64) };
  w.storage.setItem(key, JSON.stringify(corrupted));
  await expect(recover(w.t, w.store, corrupted)).rejects.toThrow();
  expect(w.storage.getItem(key)).not.toBeNull(); expect(w.t.lookup).not.toHaveBeenCalled();
});
