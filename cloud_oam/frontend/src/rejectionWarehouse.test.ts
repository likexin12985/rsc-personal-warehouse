import { beforeAll, expect, it, vi } from 'vitest';
import quantity from './test-fixtures/rejection-warehouse/quantity.json';
import serial from './test-fixtures/rejection-warehouse/serial.json';
import { detail, inbox, preview, receiptInput, inboundInput, fingerprint, checkSelection } from './rejectionWarehouse';
import { createAdapter, createStore, pending } from './rejectionWarehouseAdapter';
const other = '11111111-1111-4111-8111-111111111111';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
const f = quantity;
function writable() {
  const current = detail(f.acceptance, f.identity, f.acceptance.source.return_id);
  current.receive_permitted = true; current.receipts.forEach(h => { h.post_permitted = h.receipt.amounts.accepted_qty !== '0.000'; }); return current;
}
it('consumes real native HTTP facts without treating acceptance or shortage as inventory posting', () => {
  const d = detail(f.acceptance, f.identity, f.acceptance.source.return_id), posted = detail(f.posted, f.identity, d.source.return_id);
  expect(d.unconfirmed_qty).toBe('1.000'); expect(d.pending_inbound_qty).toBe('2.000'); expect(d.posted_qty).toBe('0.000');
  expect(posted.posted_qty).toBe('2.000'); expect(posted.unconfirmed_qty).toBe('1.000'); expect(posted.pending_inbound_qty).toBe('0.000');
  expect(inbox(f.inbox, f.identity).items[0].detail).toEqual(d);
  expect(preview(f.preview, f.identity, writable(), f.preview.receipt_id).parts.map(p => [p.condition_code, p.quantity])).toEqual([['new', '1.000'], ['damaged', '1.000']]);
});
it('matches Python canonical fingerprints for original receipt and independent inbound', async () => {
  expect(await fingerprint({ kind: 'receipt', input: receiptInput(f.receipt_command) })).toBe(f.receipt_fingerprint);
  expect(await fingerprint({ kind: 'inbound', receipt_id: f.preview.receipt_id, input: inboundInput(f.inbound_command) })).toBe(f.inbound_fingerprint);
});
it('rejects wrong custody identity, inconsistent quantity and duplicate receipt history', () => {
  expect(() => detail(f.acceptance, { ...f.identity, person_id: other }, f.acceptance.source.return_id)).toThrow();
  for (const field of ['accepted_qty', 'posted_qty', 'unconfirmed_qty', 'pending_inbound_qty'] as const) {
    expect(() => detail({ ...f.acceptance, [field]: '999.000' }, f.identity, f.acceptance.source.return_id)).toThrow();
  }
  expect(() => detail({ ...f.acceptance, receipts: [...f.acceptance.receipts, f.acceptance.receipts[0]] }, f.identity, f.acceptance.source.return_id)).toThrow();
  expect(() => inbox({ ...f.inbox, items: [{ ...f.inbox.items[0], verification_status: 'blocked' }] }, f.identity)).toThrow();
});
it('rejects fake inventory targets, swapped condition splits and already-posted preview', () => {
  for (const change of [{ target_location_id: other }, { parts: [{ ...f.preview.parts[0], quantity: '2.000' }] }, { parts: [...f.preview.parts, f.preview.parts[0]] }]) {
    expect(() => preview({ ...f.preview, ...change }, f.identity, writable(), f.preview.receipt_id)).toThrow();
  }
  expect(() => preview(f.preview, f.identity, detail(f.posted, f.identity, f.preview.return_id), f.preview.receipt_id)).toThrow();
});
it('limits a new receipt to remaining quantity and exact current handover', () => {
  const d = writable(), input = receiptInput({ ...f.receipt_command, amounts: { ...f.receipt_command.amounts, accepted_qty: '1.000', damaged_qty: '0.000', exceptions: [] } });
  expect(() => checkSelection(input, d)).not.toThrow();
  expect(() => checkSelection(receiptInput(f.receipt_command), d)).toThrow();
  expect(() => checkSelection({ ...input, handover_id: other }, d)).toThrow();
  expect(() => checkSelection({ ...input, observed_sku_code: 'WRONG' }, d)).toThrow();
});
it('validates actual serial HTTP partitions and Python fingerprints, rejecting swapped SN groups', async () => {
  const f = serial, d = detail(f.acceptance, f.identity, f.preview.return_id);
  expect(d.unconfirmed_serials).toHaveLength(1);
  expect(detail(f.posted, f.identity, f.preview.return_id).posted_qty).toBe('2.000');
  expect(inbox(f.inbox, f.identity).items[0].detail).toEqual(d);
  expect(await fingerprint({ kind: 'receipt', input: receiptInput(f.receipt_command) })).toBe(f.receipt_fingerprint);
  d.receipts.forEach(h => { h.post_permitted = h.receipt.amounts.accepted_qty !== '0.000'; });
  expect(preview(f.preview, f.identity, d, f.preview.receipt_id).parts.every(p => p.serials.length === 1)).toBe(true);
  const swapped = { ...f.preview, parts: f.preview.parts.map((p, i) => ({ ...p, serials: f.preview.parts[1 - i].serials })) };
  expect(() => preview(swapped, f.identity, d, f.preview.receipt_id)).toThrow();
  expect(() => detail({ ...f.acceptance, unconfirmed_serials: f.acceptance.source.serials }, f.identity, f.preview.return_id)).toThrow();
});
function memory() {
  const map = new Map<string, string>();
  return { getItem: (key: string) => map.get(key) ?? null, setItem: (key: string, value: string) => { map.set(key, value); }, removeItem: (key: string) => { map.delete(key); } };
}
function original(kind: 'receipt' | 'inbound') {
  return pending({ v: 1, ...f.identity, return_id: f.preview.return_id, key: 'native-original-command-key', trace: 'native-original-command-trace',
    fingerprint: kind === 'receipt' ? f.receipt_fingerprint : f.inbound_fingerprint,
    command: kind === 'receipt' ? { kind, input: f.receipt_command } : { kind, receipt_id: f.preview.receipt_id, input: f.inbound_command } });
}
function authentication(write: boolean) {
  const shared = { ...f.identity, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['provincial_manager'] };
  return { auth: { ...shared, name: '合成负责人', employee_no: '1', organization_code: 'R1', organization_name: '区域' },
    access: { ...shared, assignments: [{ assignment_id: other, role_code: 'provincial_manager', scope_type: 'organization', scope_id: other, valid_from: '2020-01-01T00:00:00Z', valid_to: null }],
      permissions: [{ resource: 'stock_operation', action: 'read', field_code: '' }, ...(write ? [{ resource: 'stock_operation', action: 'receive_return', field_code: '' }] : [])] } };
}
for (const kind of ['receipt', 'inbound'] as const) {
  it(`${kind}: recovers the exact original after write revocation using GET only`, async () => {
    const p = original(kind), storage = memory(), store = createStore(storage); store.persist(p);
    const { auth, access } = authentication(false), current = kind === 'receipt' ? f.acceptance : f.posted;
    const row = current.receipts.find(r => r.receipt.receipt_id === f.preview.receipt_id)!;
    const request = vi.fn(async (path: string, _init?: RequestInit) => path === '/auth/me' ? auth : path === '/access/context' ? access
      : path.endsWith('command-status') ? { lookup_status: 'confirmed', command: { ...(kind === 'receipt' ? row.receipt : row.inbound), replayed: true } } : current);
    await createAdapter(p.person_id, request).recover(p, store);
    expect(store.read().kind).toBe('missing'); expect(request.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
    const lookup = request.mock.calls.find(([path]) => path.endsWith('command-status'))!;
    expect(lookup[1]?.headers).toMatchObject({ 'Idempotency-Key': p.key, 'X-Request-Fingerprint': p.fingerprint });
  });
  it(`${kind}: preserves original on missing result, identity drift or changed history`, async () => {
    for (const failure of ['absent', 'identity', 'history', 'unmounted']) {
      const p = original(kind), store = createStore(memory()); store.persist(p);
      const { auth, access } = authentication(false); let identities = 0;
      const current = kind === 'receipt' ? f.acceptance : f.posted, row = current.receipts.find(r => r.receipt.receipt_id === f.preview.receipt_id)!;
      const request = vi.fn(async (path: string) => {
        if (path === '/auth/me') { identities++; return failure === 'identity' && identities > 1 ? { ...auth, person_id: other } : auth; }
        if (path === '/access/context') return access;
        if (path.endsWith('command-status')) return failure === 'absent' ? { lookup_status: 'not_observed', command: null }
          : { lookup_status: 'confirmed', command: { ...(kind === 'receipt' ? row.receipt : row.inbound), replayed: true } };
        return failure === 'history' ? { ...current, posted_qty: '999.000' } : current;
      });
      await expect(createAdapter(p.person_id, request).recover(p, store, () => failure !== 'unmounted')).rejects.toThrow();
      expect(store.read()).toEqual({ kind: 'valid', value: p });
    }
  });
}
it('sends an inbound once after exact original persistence, leaving a lost response recoverable', async () => {
  const p = original('inbound'), store = createStore(memory()); store.persist(p);
  const { auth, access } = authentication(true), current = writable();
  const request = vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === 'POST') throw new Error('response lost');
    return path === '/auth/me' ? auth : path === '/access/context' ? access : path.endsWith('/preview') ? f.preview : current;
  });
  await expect(createAdapter(p.person_id, request).submit(p, store)).rejects.toThrow('response lost');
  expect(store.read()).toEqual({ kind: 'valid', value: p });
  const posts = request.mock.calls.filter(([, init]) => init?.method === 'POST'); expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0][1]!.body as string)).toEqual(f.inbound_command);
});
it('blocks corrupt storage, unreliable persistence and replacement of a pending request', () => {
  const storage = memory(), store = createStore(storage), p = original('receipt'); store.persist(p);
  expect(() => store.persist(original('inbound'))).toThrow(); expect(() => store.clear({ ...p, trace: 'another-trace' })).toThrow();
  const broken = createStore({ ...memory(), setItem: () => { throw new Error('full'); } }); expect(() => broken.persist(p)).toThrow();
  storage.setItem('cloud-oam-rejection-warehouse-v1', '{invalid'); expect(store.read().kind).toBe('corrupt');
});
