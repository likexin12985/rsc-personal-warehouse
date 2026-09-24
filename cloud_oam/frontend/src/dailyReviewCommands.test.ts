import { beforeEach, expect, it, vi } from 'vitest';
import { apiNoReplay, ApiError } from './api';
import { parseDailyReference, parseDailyRecovery, prepareDailyCommand, requestDailyCommand } from './dailyReviewProtocol';
import { createDailyReviewRecoveryStore, type DailyReviewTaskLease } from './dailyReviewRecoveryStore';
import { recoverDailyCommand, submitDailyCommand } from './dailyReviewCommands';
import { dailyAccess, dailyLocks, dailyMarker, DailyMemoryStorage, dailyPrepared, dailyReceipt, dailyRecovery, dailyStore } from './__fixtures__/dailyReview';
import { dailyDetail, dailyId } from './__fixtures__/dailyReconciliation';
vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), apiNoReplay: vi.fn() }));
beforeEach(() => { vi.mocked(apiNoReplay).mockReset(); });
function wire(ref: ReturnType<typeof dailyPrepared>['reference']) {
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? dailyAccess() : path.endsWith('/commands') ? { receipt: dailyReceipt(ref) } : dailyRecovery(ref, 'found'));
}
it('persists only safe coordinates before sending; clears only after a separately bound proof and current identity', async () => {
  const storage = new DailyMemoryStorage(), store = dailyStore(storage), p = dailyPrepared();
  vi.mocked(apiNoReplay).mockImplementation(async (path, init) => {
    if (path === '/access/context') return dailyAccess();
    expect(store.read(dailyId).kind).toBe('valid');
    const raw = [...storage.values.values()][0];
    expect(raw).not.toContain(p.command.idempotency_key); expect(raw).not.toContain('expected_cutoff_sha256');
    if (path.endsWith('/commands')) {
      expect(JSON.parse(init!.body as string).command).toEqual(p.command);
      expect(init!.headers).toEqual({ 'X-Request-ID': p.command.request_id, 'Idempotency-Key': p.command.idempotency_key });
      return { receipt: dailyReceipt(p.reference) };
    }
    return dailyRecovery(p.reference, 'found');
  });
  await expect(submitDailyCommand(store, p, dailyAccess(), () => true)).resolves.toMatchObject({ outcome: 'found' });
  expect(store.read(dailyId).kind).toBe('missing'); expect(apiNoReplay).toHaveBeenCalledTimes(4);
});
it.each([401, 403, 409, 503])('retains unknown/rejected commands across refresh and never automatically replays (%s)', async status => {
  const storage = new DailyMemoryStorage(), p = dailyPrepared();
  vi.mocked(apiNoReplay).mockImplementation(async path => { if (path === '/access/context') return dailyAccess(); throw new ApiError(status, 'refused'); });
  await expect(submitDailyCommand(dailyStore(storage), p, dailyAccess(), () => true)).rejects.toThrow();
  const refreshed = dailyStore(storage); expect(refreshed.read(dailyId).kind).toBe('valid');
  await expect(submitDailyCommand(refreshed, dailyPrepared(), dailyAccess(), () => true)).rejects.toThrow();
  expect(vi.mocked(apiNoReplay).mock.calls.filter(([p]) => p.endsWith('/commands'))).toHaveLength(1);
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? dailyAccess() : dailyRecovery(p.reference));
  await expect(recoverDailyCommand(refreshed, dailyMarker(p.reference), dailyAccess(), false, () => true)).resolves.toEqual({ outcome: 'not_observed' });
  expect(refreshed.read(dailyId).kind).toBe('valid');
});
it.each(['found', 'sealed'] as const)('clears a refreshed marker only after exact %s evidence', async outcome => {
  const store = dailyStore(), marker = dailyMarker(); await store.withCutoffLease(dailyId, async l => l.persist(marker));
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? dailyAccess() : dailyRecovery(marker, outcome));
  // Strip storage envelope before producing the API reference.
  const { v, kind, ...ref } = marker;
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? dailyAccess() : dailyRecovery(ref, outcome));
  await expect(recoverDailyCommand(store, marker, dailyAccess(), true, () => true)).resolves.toMatchObject({ outcome });
  const call = vi.mocked(apiNoReplay).mock.calls.find(([p]) => p.endsWith('/request-seal'))!;
  expect(JSON.parse(call[1]!.body as string).confirmation).toBe('permanently_prevent_original_daily_review_request');
  expect(store.read(dailyId).kind).toBe('missing');
});
it('uses the original authorization coordinates under a new current authorization version', async () => {
  const store = dailyStore(), marker = dailyMarker(), { v, kind, ...ref } = marker;
  await store.withCutoffLease(dailyId, async l => l.persist(marker));
  const access = { ...dailyAccess(), authorization_version: 2 };
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? access : dailyRecovery(ref, 'sealed', 2));
  await recoverDailyCommand(store, marker, access, true, () => true);
  expect(store.read(dailyId).kind).toBe('missing');
});
it.each(['wrong_actor', 'changed_after', 'unmounted', 'wrong_proof', 'receipt_disagrees'])('retains evidence on %s', async fault => {
  const p = dailyPrepared(), store = dailyStore(); let alive = true, reads = 0;
  vi.mocked(apiNoReplay).mockImplementation(async path => {
    if (path === '/access/context') { reads++; return fault === 'wrong_actor' || (fault === 'changed_after' && reads === 2) ? { ...dailyAccess(), person_id: dailyId } : dailyAccess(); }
    if (path.endsWith('/commands')) { if (fault === 'unmounted') alive = false; return { receipt: dailyReceipt(p.reference) }; }
    return fault === 'wrong_proof' ? dailyRecovery({ ...p.reference, operation: 'approve' }, 'found') : fault === 'receipt_disagrees'
      ? { ...dailyRecovery(p.reference, 'found'), receipt: { ...dailyReceipt(p.reference), event_sha256: 'f'.repeat(64) } } : dailyRecovery(p.reference, 'found');
  });
  await expect(submitDailyCommand(store, p, dailyAccess(), () => alive)).rejects.toThrow();
  expect(store.read(dailyId).kind).toBe(fault === 'wrong_actor' ? 'missing' : 'valid');
});
it('prevents another tab from submitting while the first owns the object lock', async () => {
  const storage = new DailyMemoryStorage(), locks = dailyLocks(), a = dailyStore(storage, locks), b = dailyStore(storage, locks), p = dailyPrepared();
  let release!: () => void, started!: () => void; const entered = new Promise<void>(r => started = r);
  const task = a.withCutoffLease(dailyId, async lease => { lease.persist(dailyMarker(p.reference)); started(); await new Promise<void>(r => release = r); });
  await entered; wire(p.reference);
  await expect(submitDailyCommand(b, p, dailyAccess(), () => true)).rejects.toThrow(/其他页面/);
  expect(apiNoReplay).not.toHaveBeenCalled(); release(); await task;
  await expect(submitDailyCommand(b, p, dailyAccess(), () => true)).rejects.toThrow(/原日终审核/);
});
it.each(['no_locks', 'no_storage', 'write_throws', 'write_lies', 'corrupt'])('stops before transport when durable storage fails: %s', async fault => {
  const storage = new DailyMemoryStorage(), p = dailyPrepared(); wire(p.reference);
  if (fault === 'write_throws') storage.setItem = () => { throw new Error('quota'); };
  if (fault === 'write_lies') storage.setItem = () => {};
  if (fault === 'corrupt') storage.values.set('cloud-oam-daily-review-sentinel-v1:' + dailyId, '{broken');
  const store = createDailyReviewRecoveryStore({ storage: fault === 'no_storage' ? null : storage, locks: fault === 'no_locks' ? null : dailyLocks() });
  await expect(submitDailyCommand(store, p, dailyAccess(), () => true)).rejects.toThrow();
  expect(vi.mocked(apiNoReplay).mock.calls.every(([p]) => p === '/access/context')).toBe(true);
});
it('keeps expired leases unusable and refuses clearing a replacement marker', async () => {
  const store = dailyStore(), marker = dailyMarker(); let old!: DailyReviewTaskLease;
  await store.withCutoffLease(dailyId, async l => { old = l; l.persist(marker); });
  expect(() => old.clearExact(marker)).toThrow();
  await expect(store.withCutoffLease(dailyId, async l => l.clearExact(dailyMarker()))).rejects.toThrow();
  expect(store.read(dailyId).kind).toBe('valid');
});
it.each(['trace', 'actor', 'operation', 'version', 'retry', 'contradictory', 'private_field', 'zero_seal'])('rejects unbound recovery data: %s', fault => {
  const ref = dailyPrepared().reference, value = dailyRecovery(ref, 'sealed');
  if (fault === 'trace') value.reference = { ...ref, trace_request_id: 'different-trace' };
  if (fault === 'actor') value.reference = { ...ref, actor_person_id: dailyId };
  if (fault === 'operation') value.reference = { ...ref, operation: 'explain' };
  if (fault === 'version') value.current_authorization_version = 2;
  if (fault === 'retry') value.automatic_retry_allowed = true;
  if (fault === 'contradictory') value.receipt = dailyReceipt(ref);
  if (fault === 'private_field') Object.assign(value, { access_token: 'secret' });
  if (fault === 'zero_seal') value.seal!.seal_id = '00000000-0000-0000-0000-000000000000';
  expect(() => parseDailyRecovery(value, ref, 1)).toThrow();
});
it.each(['idempotency_key', 'access_token', 'explanation', 'command'])('refuses private reference field %s', field => {
  expect(() => parseDailyReference({ ...dailyPrepared().reference, [field]: 'private' })).toThrow();
});
it('rejects a mismatched original body before calling the network', async () => {
  const p = dailyPrepared(); await expect(requestDailyCommand({ ...p, command: { ...p.command, request_id: 'different-trace' } }, dailyAccess())).rejects.toThrow();
  expect(apiNoReplay).not.toHaveBeenCalled();
});
it('prepares partial explanations with exact item versions and completed evidence references', () => {
  const p = prepareDailyCommand(dailyDetail(), dailyAccess(), { operation: 'explain', items: [{ ordinal: 1, expected_item_version: 1, explanation: '合成差异需要说明', evidence_file_id: dailyId, evidence_sha256: 'a'.repeat(64) }] });
  expect(p.command.items).toHaveLength(1); expect(Object.isFrozen(p.command.items![0])).toBe(true);
  expect(() => prepareDailyCommand(dailyDetail(), dailyAccess(), { operation: 'approve', comment: '核验通过', ordinals: [1] })).toThrow();
});
it.each(['no_action', 'other_person', 'old_authorization', 'missing_context', 'inactive', 'unexplained', 'already_open'])('refuses a new command before storage or transport when capability is %s', async fault => {
  const detail = dailyDetail(), access = dailyAccess('admin'), store = dailyStore();
  if (fault === 'no_action') detail.allowed_actions = [];
  if (fault === 'other_person') detail.action_context.person_id = dailyId;
  if (fault === 'old_authorization') access.authorization_version = 2;
  if (fault === 'missing_context') Object.assign(detail, { action_context: undefined });
  if (fault === 'inactive') access.account_status = 'frozen';
  if (fault === 'unexplained') Object.assign(detail, { review_status: 'awaiting_explanations' });
  if (fault === 'already_open') detail.allowed_actions = ['open'];
  await expect((async () => {
    const command = prepareDailyCommand(detail, access, { operation: fault === 'already_open' ? 'open' : 'approve', comment: '总部独立核验' });
    return submitDailyCommand(store, command, access, () => true);
  })()).rejects.toThrow();
  expect(apiNoReplay).not.toHaveBeenCalled();
  expect(store.read(dailyId).kind).toBe('missing');
});
