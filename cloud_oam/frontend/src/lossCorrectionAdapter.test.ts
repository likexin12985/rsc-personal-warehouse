import { fixture as historyFixture } from './lossReturnHistoryFixtures';
import { expect, it, vi } from 'vitest';
import { ACTIONS, verifyPending } from './lossCorrectionContracts';
import { createAdapter } from './lossCorrectionAdapter';
import { fixtures, saved } from './lossCorrectionFixtures';

function harness(f: typeof fixtures[number]) {
  const who = { person_id: f.data.source.person_id, authorization_version: f.data.source.authorization_version,
    account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin'] };
  const me = { ...who, name: '合成总部测试人员', employee_no: 'SYNTHETIC', organization_code: 'TEST', organization_name: '合成组织' };
  const access = { ...who, assignments: [{ assignment_id: '10000000-0000-4000-8000-000000000001', role_code: 'admin', scope_type: 'national', scope_id: '*', valid_from: '2020-01-01T00:00:00Z', valid_to: null }],
    permissions: ['read', ...Object.values(ACTIONS)].map(action => ({ resource: 'stock_operation', action, field_code: '' })) };
  const request = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
    expect(init?.cache).toBe('no-store');
    if (path === '/auth/me') return structuredClone(me);
    if (path === '/access/context') return structuredClone(access);
    if (path.includes('/corrections/sources/')) return structuredClone(f.data.source);
    if (path.includes('/execution-sources/')) return structuredClone(f.data.origin);
    if (path.endsWith('/preview')) return structuredClone(f.data.preview);
    if (path.endsWith('/request-lookup')) return structuredClone(f.data.found);
    if (path.endsWith('/request-seal')) return structuredClone(f.data.sealed);
    if (path.endsWith('/' + f.flow)) return structuredClone(f.data.found.result);
    throw new Error('Unexpected synthetic request: ' + path);
  });
  return { request, access, me, adapter: createAdapter(who.person_id, request) };
}

it.each(fixtures)('$name prepares only from verified references and does not issue a business write', async f => {
  const h = harness(f), original = f.data.original;
  const choice = f.flow === 'approvals' ? original.disposition : original.correction_decision_id;
  const p = await h.adapter.prepare(original.root_disposition_id, f.flow, original.reason, choice);
  await expect(verifyPending(p)).resolves.toEqual(p);
  expect(p.command.root_disposition_id).toBe(original.root_disposition_id);
  expect(p.command.request_id).not.toBe(original.request_id);
  expect(p.command.idempotency_key).not.toBe(original.idempotency_key);
  const writes = h.request.mock.calls.filter(([, init]) => init?.method === 'POST');
  expect(writes).toHaveLength(f.flow === 'approvals' ? 0 : 1);
  if (writes.length) expect(writes[0][0]).toBe(`/v1/stock-operations/loss-reports/corrections/${f.flow}/preview`);
});

it.each(fixtures)('$name read survives write revocation, and foreign action grants cannot authorize preparation', async f => {
  const h = harness(f);
  h.access.permissions = h.access.permissions.filter(p => p.action !== ACTIONS[f.flow]);
  expect((await h.adapter.context()).can_write[f.flow]).toBe(false);
  await expect(h.adapter.read(f.data.original.root_disposition_id)).resolves.toEqual({ ...f.data.source,
    frozen_share_in_verified_history: f.flow === 'inverses' ? '0.000' : f.data.source.quantity });
  await expect(h.adapter.prepare(f.data.original.root_disposition_id, f.flow, f.data.original.reason,
    f.flow === 'approvals' ? f.data.original.disposition : f.data.original.correction_decision_id)).rejects.toThrow();
  expect(h.request.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
});

it.each(fixtures)('$name preserves full original command and matching headers on every explicit endpoint', async f => {
  const h = harness(f), p = await saved(f);
  for (const [method, suffix] of [['lookup', '/request-lookup'], ['execute', ''], ['seal', '/request-seal']] as const) {
    await h.adapter[method](p);
    const [url, options] = h.request.mock.lastCall!;
    expect(url).toBe(`/v1/stock-operations/loss-reports/corrections/${f.flow}${suffix}`);
    expect(JSON.parse(options!.body as string)).toEqual(p.command);
    const headers = new Headers(options!.headers);
    expect(headers.get('X-Request-ID')).toBe(p.command.request_id);
    expect(headers.get('Idempotency-Key')).toBe(p.command.idempotency_key);
  }
  expect(h.request).toHaveBeenCalledTimes(3);
});

it.each(fixtures)('$name derives material labels from the matching original report', async f => {
  const h = harness(f);
  const detail = await h.adapter.describe(f.data.original.root_disposition_id);
  expect(detail.report.operation_id).toBe(detail.source.operation_id);
  expect(detail.line.line_id).toBe(detail.source.line_id);
  expect(detail.line.material_name).toBeTruthy();
});

it('requires explicit approval selection and rejects changed identity before returning a prepared command', async () => {
  const f = fixtures.find(f => f.flow === 'executions')!, h = harness(f);
  await expect(h.adapter.prepare(f.data.original.root_disposition_id, f.flow, '明确选择审批')).rejects.toThrow();
  expect(h.request.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  h.me.authorization_version += 1;
  await expect(h.adapter.read(f.data.original.root_disposition_id)).rejects.toThrow();
});


it.each(['success', 'revoked', 'network', 'wrong-root'] as const)('return history %s uses only checked no-cache GETs', async mode => {
  const f = structuredClone(fixtures.find(f => f.name.startsWith('quantity') && f.flow === 'inverses')!);
  const root = f.data.source.root_disposition_id;
  f.data.source.chain_state = 'dedicated_compensation_required';
  f.data.source.inverse_preview_reference = null;
  f.data.source.history[0].disposition = 'return_to_region';
  const h = harness(f), original = h.request.getMockImplementation()!;
  const value = historyFixture();
  value.root_disposition_id = root;
  value.lines[0].original_quantity = f.data.source.quantity;
  value.lines[0].shares[5].quantity = f.data.source.quantity;
  h.access.permissions = h.access.permissions.filter(p => p.action === 'read');
  h.request.mockImplementation(async (url, init) => {
    if (!url.includes('/return-history/')) return original(url, init);
    expect(url).toBe(`/v1/stock-operations/loss-reports/corrections/return-history/${root}`);
    expect(init?.cache).toBe('no-store'); expect(init?.method ?? 'GET').toBe('GET');
    if (mode === 'network') throw new Error('history unavailable');
    if (mode === 'revoked') h.access.permissions = [];
    if (mode === 'wrong-root') value.root_disposition_id = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
    return value;
  });
  if (mode === 'success') expect((await h.adapter.history(root)).root_disposition_id).toBe(root);
  else await expect(h.adapter.history(root)).rejects.toThrow();
  expect(h.request.mock.calls.filter(([path]) => path.includes('/return-history/'))).toHaveLength(1);
  expect(h.request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(0);
});
