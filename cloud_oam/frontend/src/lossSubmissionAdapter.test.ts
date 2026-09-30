import { beforeAll, expect, it, vi } from 'vitest';
import fixture from './test-fixtures/loss-submission/loss-submission-quantity-found.json';
import { createAdapter } from './lossSubmissionAdapter';
import { input, pending, selectionInput } from './formalLossSubmission';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
const p = () => pending({ v: 1, ...fixture.identity, sources: fixture.sources, command: fixture.command, preview: fixture.preview });
function api() {
  const me = { ...fixture.identity, name: '合成测试', employee_no: 'SYNTHETIC', organization_code: 'SYNTHETIC', organization_name: '合成区域', account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['technician'] };
  const access = { ...fixture.identity, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['technician'], assignments: [{ assignment_id: '11111111-1111-4111-8111-111111111111', role_code: 'technician', scope_type: 'person', scope_id: fixture.identity.person_id, valid_from: '2020-01-01T00:00:00Z', valid_to: null as string | null }], permissions: [{ resource: 'stock_operation', action: 'read', field_code: '' }, { resource: 'stock_operation', action: 'submit_loss', field_code: '' }] };
  const request = vi.fn(async (path: string, _init?: RequestInit): Promise<unknown> => {
    if (path === '/auth/me') return structuredClone(me);
    if (path === '/access/context') return structuredClone(access);
    if (path.endsWith('/sources')) return fixture.sources;
    if (path.endsWith('/source-preview')) return fixture.selection;
    if (path.endsWith('/preview')) return fixture.preview;
    if (path.endsWith('/request-lookup')) return fixture.observed;
    return fixture.result;
  });
  return { me, access, request, adapter: createAdapter(fixture.identity.person_id, request) };
}
it('binds sources, selection and preview to current person and authority', async () => {
  const { adapter, request } = api();
  expect((await adapter.readSources()).items.length).toBe(fixture.sources.items.length);
  expect((await adapter.select(selectionInput(fixture.selection_input, fixture.identity.person_id))).selection.selection_hash).toBe(fixture.selection.selection_hash);
  expect((await adapter.prepare(input(fixture.preview_input, fixture.identity.person_id))).preview.plan_hash).toBe(fixture.preview.plan_hash);
  for (const [, init] of request.mock.calls) expect(init?.cache).toBe('no-store');
});
it('sends exactly original coordinates to separate lookup, submit and seal routes', async () => {
  const { adapter, request } = api(), original = p();
  await adapter.lookup(original); await adapter.submit(original); await adapter.seal(original);
  const calls = request.mock.calls;
  expect(calls.map(([path]) => path)).toEqual(['/v1/stock-operations/loss-reports/request-lookup', '/v1/stock-operations/loss-reports', '/v1/stock-operations/loss-reports/request-seal']);
  expect(JSON.parse(String(calls[0][1]?.body))).toEqual(fixture.lookup_input);
  expect(JSON.parse(String(calls[1][1]?.body))).toEqual(original.command);
  expect(JSON.parse(String(calls[2][1]?.body))).toEqual({ ...fixture.lookup_input, source_location_id: fixture.sources.location_id });
  expect(new Headers(calls[1][1]?.headers).get('Idempotency-Key')).toBe(original.command.idempotency_key);
  expect(new Headers(calls[2][1]?.headers).get('X-Request-ID')).toBe(original.command.request_id);
});
it('lookup errors remain errors rather than fabricated absence or retry', async () => {
  const { adapter, request } = api(); request.mockRejectedValue(new Error('503'));
  await expect(adapter.lookup(p())).rejects.toThrow('503'); expect(request).toHaveBeenCalledTimes(1);
});
it('expired and external-only assignments cannot submit even with a permission entry', async () => {
  const { adapter, access, me, request } = api(); access.assignments[0].valid_to = '2021-01-01T00:00:00Z';
  expect((await adapter.context()).can_write).toBe(false); await expect(adapter.readSources()).rejects.toThrow('权限');
  expect(request.mock.calls.some(([path]) => path.endsWith('/sources'))).toBe(false);
  access.assignments[0].valid_to = null; access.assignments[0].role_code = 'star_headquarters_approver'; access.role_codes = ['star_headquarters_approver']; me.role_codes = ['star_headquarters_approver'];
  expect((await adapter.context()).can_write).toBe(false);
});
it('read-only authority remains available for request recovery without fetching sources', async () => {
  const { adapter, access, request } = api(); access.permissions = access.permissions.filter(p => p.action === 'read');
  const c = await adapter.context(); expect(c.can_read).toBe(true); expect(c.can_write).toBe(false);
  await adapter.lookup(p()); expect(request.mock.calls.some(([path]) => path.endsWith('/sources'))).toBe(false);
});
it('identity change and foreign pending requests are rejected', async () => {
  const { adapter, me, request } = api(); me.person_id = '22222222-2222-4222-8222-222222222222';
  await expect(adapter.context()).rejects.toThrow(); request.mockClear();
  expect(() => adapter.submit({ ...p(), person_id: me.person_id })).toThrow(); expect(request).not.toHaveBeenCalled();
});

it('a prepared source has the exact wire shape and can be persisted as an original request', async () => {
  const { adapter } = api(), body = input(fixture.preview_input, fixture.identity.person_id);
  const prepared = await adapter.prepare(body);
  expect(Object.keys(prepared.sources).sort()).toEqual(Object.keys(fixture.sources).sort());
  expect(pending({ v: 1, ...fixture.identity, sources: prepared.sources, preview: prepared.preview, command: fixture.command }).person_id).toBe(fixture.identity.person_id);
});

import serialFixture from './test-fixtures/loss-submission/loss-source-serial-options.json';
import { sources as parseSources } from './formalLossSubmission';
it('queries SN by exact account and query string and checks its source snapshot', async () => {
  const f = serialFixture, w = api(); w.me.person_id = f.identity.person_id; w.access.person_id = f.identity.person_id;
  w.me.authorization_version = f.identity.authorization_version; w.access.authorization_version = f.identity.authorization_version;
  w.access.assignments[0].scope_id = f.identity.person_id;
  w.request.mockImplementation(async path => path === '/auth/me' ? w.me : path === '/access/context' ? w.access : f.page);
  const adapter = createAdapter(f.identity.person_id, w.request), current = parseSources(f.sources, f.identity), key = current.items[0].stock_account_id;
  expect((await adapter.serials(current, key)).items).toEqual(f.page.items);
  expect(w.request).toHaveBeenCalledWith(`/v1/inventory/personal/me/accounts/${key}/serials?limit=50`, expect.objectContaining({ cache: 'no-store' }));
  w.request.mockImplementation(async path => path === '/auth/me' ? w.me : path === '/access/context' ? w.access : { ...f.page, ledger_cursor: f.page.ledger_cursor + 1 });
  await expect(adapter.serials(current, key)).rejects.toThrow('快照');
});
