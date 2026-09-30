import { beforeAll, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import fixture from './test-fixtures/return-receiving/loss-receiving-serial.json';
import { createAdapter } from './returnReceivingAdapter';
import { pending, type Pending } from './returnReceivingRecovery';
const other = '11111111-1111-4111-8111-111111111111';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
function original(kind: 'receipt' | 'inbound') {
  return pending({ v: 1, kind, ...fixture.identity, package: fixture.after.package, ...(kind === 'receipt' ? { command: fixture.command } : { receipt: fixture.receipt, original: { receipt_id: fixture.receipt.receipt_id, shipment_id: fixture.receipt.shipment_id, target_location_id: fixture.receipt.target_location_id, target_custody_assignment_id: fixture.receipt.target_custody_assignment_id, request_hash: fixture.inbound.posted.request_hash, command: fixture.inbound.command } }) });
}
const auth = { ...fixture.identity, name: '合成负责人', employee_no: '1', organization_code: 'R1', organization_name: '区域', account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['provincial_manager'] };
function access() { return { ...fixture.identity, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['provincial_manager'], assignments: [{ assignment_id: other, role_code: 'provincial_manager', scope_type: 'organization', scope_id: other, valid_from: '2020-01-01T00:00:00Z', valid_to: null as string | null }], permissions: [{ resource: 'stock_operation', action: 'read', field_code: '' }, { resource: 'stock_operation', action: 'receive_return', field_code: '' }] }; }
for (const kind of ['receipt', 'inbound'] as const) {
  it(`${kind}: original POST uses the right object, body and original headers once`, async () => {
    const p = original(kind), request = vi.fn(async (_path: string, _init?: RequestInit) => { throw new Error('network lost'); });
    await expect(createAdapter(p.person_id, request).submit(p)).rejects.toThrow('network lost'); expect(request).toHaveBeenCalledTimes(1);
    const [path, init] = request.mock.calls[0], command = p.kind === 'receipt' ? p.command : p.original.command;
    expect(path).toBe(kind === 'receipt' ? `/v1/stock-returns/my-receiving/${p.package.shipment_id}/receipts` : `/v1/stock-returns/my-receiving/${fixture.receipt.receipt_id}/inbound`);
    expect(JSON.parse(init!.body as string)).toEqual(command); expect(init!.headers).toMatchObject({ 'X-Request-ID': command.request_id, 'Idempotency-Key': command.idempotency_key });
  });
  it(`${kind}: only an explicit endpoint-specific 404 is not-observed`, async () => {
    const p = original(kind), code = kind === 'receipt' ? 'stock_return_receipt_not_observed' : 'stock_return_inbound_not_observed';
    for (const error of [new Error('timeout'), new ApiError(404, 'no response'), new ApiError(404, 'other', { code: 'different_object' }), new ApiError(503, 'unavailable', { code })]) {
      const request = vi.fn(async () => { throw error; }); await expect(createAdapter(p.person_id, request).lookup(p)).rejects.toBe(error); expect(request).toHaveBeenCalledTimes(1);
    }
    const request = vi.fn(async () => { throw new ApiError(404, 'not yet observed', { code }); });
    expect(await createAdapter(p.person_id, request).lookup(p)).toEqual({ observed: false });
  });
  it(`${kind}: seal has original request header and no new idempotency key`, async () => {
    const p = original(kind), request = vi.fn(async (_path: string, _init?: RequestInit) => ({}));
    await createAdapter(p.person_id, request).seal(p); const [path, init] = request.mock.calls[0];
    expect(path.endsWith('/seal')).toBe(true); expect(new Headers(init!.headers).has('Idempotency-Key')).toBe(false);
    expect(JSON.parse(init!.body as string)).toEqual(kind === 'receipt' ? { operator_person_id: p.person_id, request_hash: fixture.receipt.request_hash } : { request_hash: fixture.inbound.posted.request_hash });
  });
}
it('uses current auth and scope fingerprints while retaining read-only recovery', async () => {
  let ctx = access(); const request = vi.fn(async (path: string) => path === '/auth/me' ? auth : ctx), adapter = createAdapter(fixture.identity.person_id, request);
  const before = await adapter.context(); expect(before.can_read && before.can_write).toBe(true);
  ctx = { ...ctx, permissions: ctx.permissions.filter(p => p.action === 'read') }; const after = await adapter.context();
  expect(after.can_read).toBe(true); expect(after.can_write).toBe(false); expect(after.authority_hash).not.toBe(before.authority_hash);
  ctx.assignments[0].valid_to = '2020-01-02T00:00:00Z'; expect((await adapter.context()).can_read).toBe(false);
});
it('does not display another user or an authority change midway through a directory read', async () => {
  let calls = 0; const request = vi.fn(async (path: string) => {
    if (path === '/auth/me') return auth;
    if (path === '/access/context') { calls++; return { ...access(), authorization_version: calls === 1 ? fixture.identity.authorization_version : 99 }; }
    return fixture.directory;
  });
  await expect(createAdapter(fixture.identity.person_id, request).list()).rejects.toThrow();
  await expect(createAdapter(other, request).context()).rejects.toThrow();
});
it('rejects a stored request from another person before any transport', async () => {
  const request = vi.fn(async () => ({}));
  expect(() => createAdapter(other, request).submit(original('receipt') as Pending)).toThrow(); expect(request).not.toHaveBeenCalled();
});
