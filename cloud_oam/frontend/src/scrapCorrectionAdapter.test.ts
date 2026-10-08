import { expect, it, vi } from 'vitest';
import { createAdapter } from './scrapCorrectionAdapter';
import { fixtures } from './lossCorrectionFixtures';
import { createStore, recover, submit } from './formalScrapRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import outcomes from './test-fixtures/stock-scrap/committed-quantity-results.json';

function world(mode = 'quantity') {
  // Synthetic DTO adaptation; this is client validation, not native PG evidence.
  const data = structuredClone(fixtures.find(f => f.name.startsWith(mode) && f.flow === 'executions')!.data);
  const source = data.source, choice = source.approval_choices[0];
  choice.disposition = 'scrap'; choice.execution_mode = 'dedicated_flow_required'; choice.preview_reference = null;
  source.history.find(h => h.fact_id === choice.correction_decision_id)!.disposition = 'scrap';
  const identity = { person_id: source.person_id, authorization_version: source.authorization_version,
    account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin'] };
  const me = { ...identity, name: '测试', employee_no: 'SYNTHETIC', organization_code: 'TEST', organization_name: '测试' };
  const access = { ...identity, assignments: [{ assignment_id: crypto.randomUUID(), role_code: 'admin', scope_type: 'national', scope_id: '*',
    valid_from: '2020-01-01T00:00:00Z', valid_to: null }], permissions: ['read', 'correct_loss'].map(action => ({ resource: 'stock_operation', action, field_code: '' })) };
  const line = data.origin.report.lines.find(l => l.line_id === source.line_id)!;
  const preview = { planning_status: 'preview_only', stock_effect: 'none', operation_id: source.operation_id, line_id: source.line_id,
    decision_id: choice.correction_decision_id, predecessor_reversal_id: source.approval_reference!.reversal_id,
    source_account_id: crypto.randomUUID(), target_account_id: null, source_condition: line.condition_code,
    quantity: source.quantity, serial_ids: source.serial_ids, plan_hash: 'a'.repeat(64), checked_at: new Date().toISOString() };
  const request = vi.fn(async (path: string, _init?: RequestInit): Promise<unknown> => {
    if (path === '/auth/me') return me; if (path === '/access/context') return access;
    if (path.includes('/corrections/sources/')) return source;
    if (path.includes('/execution-sources/')) return data.origin;
    if (path.endsWith('/preview')) return preview;
    throw new Error('connection lost');
  });
  const adapter = createAdapter(source.person_id, request);
  const prepare = () => adapter.prepare(source.root_disposition_id, choice.correction_decision_id, '核验实物后纠正报废', [crypto.randomUUID()]);
  return { source, choice, me, access, preview, request, adapter, prepare };
}
it.each(['quantity', 'serial'])('previews %s from the selected verified approval without business writes', async mode => {
  const w = world(mode), p = await w.prepare();
  expect(p.pending.kind).toBe('correction');
  expect(p.preview.serial_ids).toEqual(w.source.serial_ids);
  const posts = w.request.mock.calls.filter(([, init]) => init?.method === 'POST'); expect(posts).toHaveLength(1);
  expect(posts[0][0]).toBe('/v1/stock-operations/loss-reports/scraps/corrections/preview');
  expect(JSON.parse(posts[0][1]!.body as string).source).toEqual({ kind: 'correction', ...w.source.approval_reference,
    correction_decision_id: w.choice.correction_decision_id,
    expected_correction_decision_hash: w.source.history.find(h => h.fact_id === w.choice.correction_decision_id)!.request_hash });
});
it.each(['quantity', 'decision_id', 'operation_id', 'line_id', 'source_condition', 'predecessor_reversal_id', 'target_account_id', 'stock_effect', 'serial_ids', 'checked_at'])('rejects mismatched correction preview %s', async field => {
  const w = world(); Object.assign(w.preview, { [field]: field === 'serial_ids' ? [crypto.randomUUID()] : field === 'checked_at' ? '2000-01-01T00:00:00Z' : 'wrong' });
  await expect(w.prepare()).rejects.toThrow();
});
it.each(['not-scrap', 'missing-approval', 'consumed', 'no-write', 'wrong-person', 'authority-changed'])('rejects %s before a correction write', async fault => {
  const w = world();
  if (fault === 'not-scrap') { w.choice.disposition = 'return_to_region'; w.source.history.find(h => h.fact_id === w.choice.correction_decision_id)!.disposition = 'return_to_region'; }
  if (fault === 'missing-approval') w.source.history = w.source.history.filter(h => h.fact_id !== w.choice.correction_decision_id);
  if (fault === 'consumed') w.source.chain_state = 'dedicated_compensation_required';
  if (fault === 'no-write') w.access.permissions.pop();
  if (fault === 'wrong-person') w.me.person_id = crypto.randomUUID();
  if (fault === 'authority-changed') { const real = w.request.getMockImplementation()!; w.request.mockImplementation(async (p, i) => { const r = await real(p, i); if (p.endsWith('/preview')) w.access.permissions.pop(); return r; }); }
  await expect(w.prepare()).rejects.toThrow();
  expect(w.request.mock.calls.some(([path]) => path.endsWith('/scraps/corrections'))).toBe(false);
});
it('rereads source and plan before saving a new request', async () => {
  const w = world(), p = (await w.prepare()).pending, store = createStore(new MemoryStorage(), locks());
  w.preview.plan_hash = 'b'.repeat(64);
  await expect(submit(w.adapter, store, p)).rejects.toThrow(); expect(store.list(p.person_id)).toEqual([]);
  expect(w.request.mock.calls.some(([path]) => path.endsWith('/scraps/corrections'))).toBe(false);
});
it('recovers a lost response after remount and write revocation without source, preview or resubmission', async () => {
  const w = world(), p = (await w.prepare()).pending, store = createStore(new MemoryStorage(), locks());
  await expect(submit(w.adapter, store, p)).rejects.toThrow('connection lost'); expect(store.list(p.person_id)).toEqual([p]);
  if (!('execution_reason' in p.original) || p.original.source.kind !== 'correction') throw new Error('correction required');
  const result = { ...outcomes.samples.find(s => s.kind === 'original')!.fact, source_kind: 'correction', correction_execution_id: crypto.randomUUID(),
    root_disposition_id: p.original.source.root_disposition_id, request_id: p.original.request_id,
    request_hash: p.request_hash, plan_hash: p.original.expected_plan_hash };
  w.access.permissions.pop(); const real = w.request.getMockImplementation()!;
  w.request.mockImplementation(async (path, init) => {
    if (path.endsWith('/request-lookup')) return { request_id: p.original.request_id, request_hash: p.request_hash,
      request_state: 'found', result_scope: 'historical_original_outcome', retry_allowed: false, result };
    if (path !== '/auth/me' && path !== '/access/context') throw new Error('must only look up');
    return real(path, init);
  });
  const before = w.request.mock.calls.length;
  expect((await recover(createAdapter(p.person_id, w.request), store, p)).status).toBe('found');
  expect(store.list(p.person_id)).toEqual([]);
  expect(w.request.mock.calls.slice(before).filter(([, init]) => init?.method === 'POST').map(([path]) => path)).toEqual(['/v1/stock-operations/loss-reports/scraps/corrections/request-lookup']);
});
