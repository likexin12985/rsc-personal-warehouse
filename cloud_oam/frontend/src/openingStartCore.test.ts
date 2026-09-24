import { describe, expect, it, vi } from 'vitest';
import { createStartStore, recoverOpeningStart, sealOpeningStart, submitOpeningStart, validateStartInput, validateStartMarker,
  validateStartRecovery, validateStartResult, startErrorMessage, START_STORAGE_PREFIX, OpeningStartPendingError,
  type StartActor, type StartInput, type StartLocks, type StartMarker, type StartPorts, type StartResult } from './openingStartCore';
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const actor: StartActor = { person_id: id(1), authorization_version: 7 };
const input: StartInput = { region_org_id: id(2), publication_id: id(3), task_no: 'OPEN-NEW', blind_count: true, deadline: null, note: '',
  scopes: [{ owner_org_id: id(2), location_id: id(4), assignee_user_id: 'user-count', freeze_mode: 'hard' }] };
const marker: StartMarker = { v: 1, kind: 'opening_start', region_org_id: id(2), publication_id: id(3), actor_person_id: id(1), actor_authorization_version: 7, trace_request_id: 'request-00000001' };
const result: StartResult = { schema_version: '1.0', task_id: id(5), task_no: 'OPEN-NEW', status: 'counting', cutoff_ledger_cursor: 0,
  initial_round_id: id(6), scope_count: 1, snapshot_line_count: 0, control_line_count: 0, replayed: false };
function found(who = actor) { return { schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: who.person_id,
  authorization_version: who.authorization_version, region_org_id: input.region_org_id, publication_id: input.publication_id,
  outcome: 'found', automatic_retry_allowed: false, seal: null, result: { ...result, replayed: true } }; }
function locks(): StartLocks {
  const held = new Set<string>();
  return { async request(name, _, work) {
    if (held.has(name)) return work(null);
    held.add(name); try { return await work({}); } finally { held.delete(name); }
  } };
}
function world() {
  const rows = new Map<string,string>(), lock = locks(), calls: string[] = [];
  const storage = { getItem: (key: string) => rows.get(key) ?? null,
    setItem: vi.fn((key: string, value: string) => { rows.set(key,value); }), removeItem: vi.fn((key: string) => { rows.delete(key); }) };
  const store = createStartStore(storage, lock);
  const ports: StartPorts = {
    identity: vi.fn(async () => { calls.push('identity'); }), verifySelection: vi.fn(async () => { calls.push('selection'); }),
    coordinates: vi.fn(() => ({ requestId: marker.trace_request_id, idempotencyKey: 'idempotency-00000001' })),
    post: vi.fn(async () => {
      calls.push('post'); expect(store.read(input.region_org_id)).toEqual({ kind: 'valid', value: marker }); return result;
    }),
    seal: vi.fn(async () => ({})),
    lookup: vi.fn(async () => { calls.push('lookup'); return found(); }),
    detail: vi.fn(async () => ({ task_id: result.task_id, task_no: result.task_no, region_org_id: input.region_org_id })),
  };
  const options = { input, actor, store, ports, enabled: true, canContinue: () => true };
  const save = () => rows.set(START_STORAGE_PREFIX + marker.region_org_id, JSON.stringify(marker));
  return { rows, storage, lock, store, ports, options, calls, save };
}
describe('opening startup durable protocol', () => {
  it('rejects unsafe assignee references and sanitizes preflight transport failures before persistence', async () => {
    for (const user of ['user?query', '/prefix', 'user#fragment', 'user name']) {
      expect(() => validateStartInput({ ...input, scopes: [{ ...input.scopes[0], assignee_user_id: user }] })).toThrow();
    }
    const w = world(); vi.mocked(w.ports.verifySelection).mockRejectedValue(new Error('private upstream details'));
    await expect(submitOpeningStart(w.options)).rejects.toThrow('所选范围与批次未核验通过');
    expect(w.rows.size).toBe(0); expect(w.ports.coordinates).not.toHaveBeenCalled(); expect(w.ports.post).not.toHaveBeenCalled();
    expect(startErrorMessage(new Error('private upstream details'))).not.toContain('private');
  });
  it('persists coordinates before exactly one POST and clears only after two independent matching readbacks', async () => {
    const w = world(); expect(await submitOpeningStart(w.options)).toEqual({ result: { ...result, replayed: true }, recovered: false });
    expect(w.calls).toEqual(['identity','selection','identity','post','identity','lookup','identity','lookup','identity']);
    expect(w.ports.post).toHaveBeenCalledTimes(1); expect(w.store.read(input.region_org_id)).toEqual({ kind: 'missing' });
    const saved = w.storage.setItem.mock.calls[0][1];
    expect(JSON.parse(saved)).toEqual(marker); expect(saved).not.toMatch(/idempotency|scopes|task_no|assignee|note|quantity|token/);
  });
  it('handles explicit zero control and book counts without inventing inventory', async () => {
    expect(validateStartResult(result).control_line_count).toBe(0); expect(validateStartResult(result).snapshot_line_count).toBe(0);
    expect(() => validateStartResult({ ...result, scope_count: 0 })).toThrow();
  });
  it('uses only reads for a durable prior request even when a new form selects another publication and new starts are disabled', async () => {
    const w = world(); w.save();
    await submitOpeningStart({ ...w.options, enabled: false, input: { ...input, publication_id: id(99) } });
    expect(w.ports.post).not.toHaveBeenCalled(); expect(w.ports.coordinates).not.toHaveBeenCalled();
    expect(w.ports.lookup).toHaveBeenCalledWith(marker);
  });
  it('reopens from storage in a new store instance and recovers under a later current grant for the same person', async () => {
    const w = world(); w.save(); const newer = { ...actor, authorization_version: 8 };
    vi.mocked(w.ports.lookup).mockResolvedValue(found(newer));
    await recoverOpeningStart({ ...w.options, region: input.region_org_id, actor: newer, store: createStartStore(w.storage, w.lock) });
    expect(w.rows.size).toBe(0); expect(w.ports.post).not.toHaveBeenCalled();
  });
  it.each([{ ...actor, person_id: id(99) }, { ...actor, authorization_version: 6 }])('does not clear or send for another person or older authorization %j', async (who) => {
    const w = world(); w.save();
    await expect(recoverOpeningStart({ ...w.options, region: input.region_org_id, actor: who })).rejects.toBeInstanceOf(OpeningStartPendingError);
    expect(w.store.read(input.region_org_id).kind).toBe('valid'); expect(w.ports.lookup).not.toHaveBeenCalled();
  });
  it.each(['not_observed','network','malformed','wrong_actor','wrong_region','unsafe_cursor','late_change'])('retains original coordinates after uncertain recovery: %s', async (fault) => {
    const w = world(); w.save();
    if (fault === 'network') vi.mocked(w.ports.lookup).mockRejectedValue(new Error('private response'));
    else if (fault === 'late_change') vi.mocked(w.ports.lookup).mockResolvedValueOnce(found()).mockResolvedValue({ ...found(), result: { ...result, replayed: true, initial_round_id: id(99) } });
    else vi.mocked(w.ports.lookup).mockResolvedValue(fault === 'not_observed' ? { ...found(), outcome: 'not_observed', result: null }
      : fault === 'malformed' ? { ...found(), extra: 'private' }
        : fault === 'wrong_actor' ? { ...found(), actor_person_id: id(99) }
          : fault === 'wrong_region' ? { ...found(), region_org_id: id(99) }
            : { ...found(), result: { ...result, replayed: true, cutoff_ledger_cursor: Number.MAX_SAFE_INTEGER + 1 } });
    await expect(recoverOpeningStart({ ...w.options, region: input.region_org_id })).rejects.toThrow('原启动请求仍待核验');
    expect(w.store.read(input.region_org_id)).toEqual({ kind: 'valid', value: marker }); expect(w.ports.post).not.toHaveBeenCalled();
  });
  it('recovers an accepted POST after transport loss without posting twice', async () => {
    const w = world(); vi.mocked(w.ports.post).mockRejectedValue(new Error('timeout'));
    expect((await submitOpeningStart(w.options)).recovered).toBe(true); expect(w.ports.post).toHaveBeenCalledTimes(1);
  });
  it('generic failure plus not_observed remains pending across a second submit', async () => {
    const w = world(); vi.mocked(w.ports.post).mockRejectedValue({ responseReceived: true, status: 500 });
    vi.mocked(w.ports.lookup).mockResolvedValue({ ...found(), outcome: 'not_observed', result: null });
    await expect(submitOpeningStart(w.options)).rejects.toThrow('待核验');
    await expect(submitOpeningStart(w.options)).rejects.toThrow('待核验');
    expect(w.ports.post).toHaveBeenCalledTimes(1); expect(w.ports.coordinates).toHaveBeenCalledTimes(1);
  });
  it('a named first POST admission rejection clears only after fresh matching access', async () => {
    const w = world(); vi.mocked(w.ports.post).mockRejectedValue({ responseReceived: true, status: 412, category: 'precondition_failed', code: 'control_publication_not_admissible' });
    await expect(submitOpeningStart(w.options)).rejects.toThrow('所选批次已不能启动');
    expect(w.store.read(input.region_org_id).kind).toBe('missing'); expect(w.ports.lookup).not.toHaveBeenCalled();
  });
  it('the same admission error from a later GET never clears an accepted POST marker', async () => {
    const w = world(); vi.mocked(w.ports.lookup).mockRejectedValue({ responseReceived: true, status: 412, category: 'precondition_failed', code: 'control_publication_not_admissible' });
    await expect(submitOpeningStart(w.options)).rejects.toThrow('待核验'); expect(w.store.read(input.region_org_id).kind).toBe('valid');
  });
  it('clear failure remains fail closed in the current store', async () => {
    const w = world(); w.save(); w.storage.removeItem.mockImplementation(() => { throw new Error('storage unavailable'); });
    await expect(recoverOpeningStart({ ...w.options, region: input.region_org_id })).rejects.toThrow('待核验');
    expect(w.store.read(input.region_org_id).kind).toBe('unavailable'); expect(w.ports.post).not.toHaveBeenCalled();
  });
  it.each(['corrupt','unavailable','write_mismatch','no_locks'])('does not send when persistence is %s', async (fault) => {
    const w = world();
    if (fault === 'corrupt') w.rows.set(START_STORAGE_PREFIX + input.region_org_id, '');
    if (fault === 'unavailable') w.options.store = createStartStore(null, w.lock);
    if (fault === 'write_mismatch') w.storage.setItem.mockImplementation((key) => { w.rows.set(key,'{}'); });
    if (fault === 'no_locks') w.options.store = createStartStore(w.storage, null);
    await expect(submitOpeningStart(w.options)).rejects.toThrow(); expect(w.ports.post).not.toHaveBeenCalled();
  });
  it('does not release a marker when page/identity changes after POST', async () => {
    const w = world(); let live = true; w.options.canContinue = () => live;
    vi.mocked(w.ports.post).mockImplementation(async () => { live = false; return result; });
    await expect(submitOpeningStart(w.options)).rejects.toThrow('待核验'); expect(w.store.read(input.region_org_id).kind).toBe('valid');
  });
  it('cannot queue a second same-region command through another store or another publication', async () => {
    const w = world(); let release!: () => void; const barrier = new Promise<void>((resolve) => { release = resolve; });
    vi.mocked(w.ports.post).mockImplementation(async () => { await barrier; return result; });
    const first = submitOpeningStart(w.options);
    await vi.waitFor(() => expect(w.ports.post).toHaveBeenCalledTimes(1));
    await expect(submitOpeningStart({ ...w.options, store: createStartStore(w.storage, w.lock), input: { ...input, publication_id: id(99) } })).rejects.toThrow('其他页面');
    // A separate region acquires its own lease while the original is held.
    await createStartStore(w.storage, w.lock).withRegionLease(id(88), async (lease) => { expect(lease.read().kind).toBe('missing'); });
    release(); await first; expect(w.ports.post).toHaveBeenCalledTimes(1);
  });
  it('rejects extra control evidence, duplicate ranges and invalid dates before persistence', () => {
    for (const bad of [{ ...input, control_lines: [] }, { ...input, scopes: [...input.scopes,...input.scopes] },
      { ...input, deadline: '2026-02-30T00:00:00Z' }, { ...input, scopes: [{ ...input.scopes[0], freeze_mode: 'none' }] }]) expect(() => validateStartInput(bad)).toThrow();
    expect(() => validateStartMarker({ ...marker, idempotencyKey: 'secret' })).toThrow();
    expect(() => validateStartRecovery({ ...found(), automatic_retry_allowed: true }, marker, actor)).toThrow();
  });
  it('uses the captured command despite form mutation during preflight', async () => {
    const w = world(), draft = JSON.parse(JSON.stringify(input));
    vi.mocked(w.ports.verifySelection).mockImplementation(async () => { draft.scopes[0].assignee_user_id = 'changed'; });
    await submitOpeningStart({ ...w.options, input: draft });
    expect(vi.mocked(w.ports.post).mock.calls[0][0].scopes[0].assignee_user_id).toBe('user-count');
  });
  it('keeps escaped leases unusable and does not invent a release for absent evidence', async () => {
    const w = world(); let escaped: any;
    await w.store.withRegionLease(input.region_org_id, async (lease) => { escaped = lease; });
    expect(() => escaped.persist(marker)).toThrow();
    await expect(recoverOpeningStart({ ...w.options, region: input.region_org_id })).rejects.toThrow('没有可核验');
  });
});

function sealed(who = actor) {
  return { ...found(who), outcome: 'sealed', result: null, seal: { seal_id: id(80), actor_person_id: who.person_id,
    authorization_version: who.authorization_version, region_org_id: marker.region_org_id, publication_id: marker.publication_id,
    trace_request_id: marker.trace_request_id, sealed_at: '2026-09-20T12:00:00Z', permanent_nonexecution: true } };
}
it('explicit seal resolves an unknown POST only through two independent reads without regenerating any startup key', async () => {
  const w = world(); w.save(); const confirm = vi.fn(async () => true);
  vi.mocked(w.ports.lookup).mockResolvedValueOnce({ ...found(), outcome: 'not_observed', result: null }).mockResolvedValue(sealed());
  vi.mocked(w.ports.seal).mockRejectedValue(new Error('network timeout'));
  const result = await sealOpeningStart({ ...w.options, region: input.region_org_id, confirm });
  expect(result).toEqual({ result: null, recovered: true, seal: sealed().seal });
  expect(w.ports.seal).toHaveBeenCalledExactlyOnceWith(marker); expect(confirm).toHaveBeenCalledTimes(1);
  expect(w.ports.lookup).toHaveBeenCalledTimes(3); expect(w.ports.coordinates).not.toHaveBeenCalled();
  expect(w.ports.post).not.toHaveBeenCalled(); expect(w.ports.detail).not.toHaveBeenCalled(); expect(w.rows.size).toBe(0);
});
it('cancelling a seal leaves the same marker and sends nothing', async () => {
  const w = world(); w.save(); vi.mocked(w.ports.lookup).mockResolvedValue({ ...found(), outcome: 'not_observed', result: null });
  await expect(sealOpeningStart({ ...w.options, region: input.region_org_id, confirm: async () => false })).rejects.toThrow('已取消终结');
  expect(w.store.read(id(2))).toEqual({ kind: 'valid', value: marker }); expect(w.ports.seal).not.toHaveBeenCalled();
});
it('a start that won before terminal intent recovers the original task without asking to seal', async () => {
  const w = world(); w.save(); const confirm = vi.fn(async () => true);
  const result = await sealOpeningStart({ ...w.options, region: id(2), confirm });
  expect(result.result?.task_id).toBe(id(5)); expect(confirm).not.toHaveBeenCalled(); expect(w.ports.seal).not.toHaveBeenCalled();
});
it.each(['absent','changed','wrong-request','wrong-person','wrong-version','extra','contradiction','hidden'])('retains original marker after unconfirmed seal evidence: %s', async (fault) => {
  const w = world(); w.save(); let live = true;
  const proof = sealed();
  if (fault === 'wrong-request') proof.seal.trace_request_id = 'another-original-request';
  if (fault === 'wrong-person') proof.seal.actor_person_id = id(99);
  if (fault === 'wrong-version') proof.seal.authorization_version = 6;
  if (fault === 'extra') Object.assign(proof.seal, { retry: true });
  const absent = { ...found(), outcome: 'not_observed', result: null };
  vi.mocked(w.ports.lookup).mockResolvedValueOnce(absent).mockResolvedValue(fault === 'absent' ? absent : fault === 'contradiction' ? { ...proof, result } : proof);
  if (fault === 'changed') vi.mocked(w.ports.lookup).mockReset().mockResolvedValueOnce(absent).mockResolvedValueOnce(proof).mockResolvedValueOnce({ ...proof, seal: { ...proof.seal, seal_id: id(81) } });
  vi.mocked(w.ports.seal).mockImplementation(async () => { if (fault === 'hidden') live = false; return proof; });
  await expect(sealOpeningStart({ ...w.options, region: id(2), confirm: async () => true, canContinue: () => live })).rejects.toThrow();
  expect(w.store.read(id(2))).toEqual({ kind: 'valid', value: marker }); expect(w.ports.post).not.toHaveBeenCalled();
  expect(w.ports.seal).toHaveBeenCalledTimes(1);
});
