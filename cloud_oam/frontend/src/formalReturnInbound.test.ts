import { beforeAll, describe, expect, it, vi } from 'vitest';
import lossQuantity from './test-fixtures/return-receiving/loss-receiving-quantity.json';
import lossSerial from './test-fixtures/return-receiving/loss-receiving-serial.json';
import workQuantity from './test-fixtures/return-receiving/work-order-receiving-quantity.json';
import workSerial from './test-fixtures/return-receiving/work-order-receiving-serial.json';
import legacyLossSerial from './test-fixtures/return-receiving/loss-receiving-serial-v1.json';
import { history } from './formalReturnReceiving';
import { hash, lookup, original, preview, result, state, type Original } from './formalReturnInbound';
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const other = '11111111-1111-4111-8111-111111111111';
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });

describe('return receipt independent inbound', () => {
  it('retains the original version-one request hash and posted-result recovery', async () => {
    const f = legacyLossSerial, v = f.inbound;
    const receipt = history(f.after, f.identity, f.after.package.shipment_id).receipts[0];
    expect(v.preview.schema_version).toBe('1.0');
    expect(preview(v.preview, f.identity, receipt)).toEqual(v.preview);
    const expected = await original({ receipt_id: receipt.receipt_id, shipment_id: receipt.shipment_id,
      target_location_id: receipt.target_location_id, target_custody_assignment_id: receipt.target_custody_assignment_id,
      command: v.command, request_hash: v.posted.request_hash });
    expect(lookup(v.posted, expected)).toEqual({ status: 'posted', inbound: v.posted });
  });
  for (const [name, f] of Object.entries({ lossQuantity, lossSerial, workQuantity, workSerial })) {
    it(`${name}: exact real preview, original hash and posting proof match`, async () => {
      const receipt = history(f.after, f.identity, f.after.package.shipment_id).receipts[0], v = f.inbound;
      expect(state(v.before, f.identity, receipt)).toEqual(v.before);
      expect(state(v.after, f.identity, receipt)).toEqual(v.after);
      expect(preview(v.preview, f.identity, receipt)).toEqual(v.preview);
      const expected = await original({ receipt_id: receipt.receipt_id, shipment_id: receipt.shipment_id, target_location_id: receipt.target_location_id, target_custody_assignment_id: receipt.target_custody_assignment_id, command: v.command, request_hash: v.posted.request_hash });
      expect(result(v.posted, expected)).toEqual(v.posted);
      expect(lookup(v.posted, expected).status).toBe('posted');
      expect(await hash({ receipt_id: receipt.receipt_id, request_id: v.command.request_id, plan_hash: v.command.expected_plan_hash })).toBe(v.posted.request_hash);
    });
  }
  const f = lossSerial;
  const receipt = () => history(f.after, f.identity, f.after.package.shipment_id).receipts[0];
  const marker = (): Original => ({ receipt_id: f.receipt.receipt_id, shipment_id: f.receipt.shipment_id, target_location_id: f.receipt.target_location_id, target_custody_assignment_id: f.receipt.target_custody_assignment_id, request_hash: f.inbound.posted.request_hash, command: f.inbound.command });
  it('never infers posting from receipt acceptance or a status label', () => {
    expect(() => state(f.receipt, f.identity, receipt())).toThrow();
    expect(() => state({ ...f.inbound.before, status: 'posted' }, f.identity, receipt())).toThrow();
    const v = clone(f.inbound.after); v.inbound.posting_transaction_id = '';
    expect(() => state(v, f.identity, receipt())).toThrow();
    expect(() => state({ ...f.inbound.after, status: 'not_posted' }, f.identity, receipt())).toThrow();
  });
  it('rejects another receipt, actor, custody, target or authority version', () => {
    for (const fields of [{ receipt_id: other }, { shipment_id: other }, { operator_person_id: other }, { authorization_version: 99 }]) {
      expect(() => state({ ...f.inbound.after, ...fields }, f.identity, receipt())).toThrow();
    }
    expect(() => preview({ ...f.inbound.preview, target_custody_assignment_id: other }, f.identity, receipt())).toThrow();
    expect(() => state({ ...f.inbound.after, inbound: { ...f.inbound.after.inbound, target_location_id: other } }, f.identity, receipt())).toThrow();
  });
  it('binds the loss disposition and original receipt plan', () => {
    expect(() => preview({ ...f.inbound.preview, origin: { ...f.inbound.preview.origin, disposition_id: other } }, f.identity, receipt())).toThrow();
    expect(() => preview({ ...f.inbound.preview, receipt_plan_hash: 'a'.repeat(64) }, f.identity, receipt())).toThrow();
    expect(() => preview({ ...f.inbound.preview, work_order_id: other }, f.identity, receipt())).toThrow();
  });
  it('rejects missing accepted quantity, substituted SN and a silent condition conversion', () => {
    const v = clone(f.inbound.preview); v.lines[0].accepted_qty = '0.000';
    expect(() => preview(v, f.identity, receipt())).toThrow();
    const s = clone(f.inbound.preview); s.lines[0].serial_ids = [other];
    expect(() => preview(s, f.identity, receipt())).toThrow();
    const c = clone(f.inbound.preview); c.lines[0].condition_code = 'damaged';
    expect(() => preview(c, f.identity, receipt())).toThrow();
    expect(() => preview({ ...f.inbound.preview, lines: [] }, f.identity, receipt())).toThrow();
  });
  it('does not allow a changed original hash or invalid request key', async () => {
    await expect(original({ ...marker(), request_hash: 'a'.repeat(64) })).rejects.toThrow();
    await expect(original({ ...marker(), command: { ...marker().command, idempotency_key: 'bad' } })).rejects.toThrow();
  });
  it('version two partitions mixed quantity acceptance into original and damaged conditions', () => {
    const r = history(lossQuantity.after, lossQuantity.identity, lossQuantity.after.package.shipment_id).receipts[0];
    r.lines[0].damaged_qty = '0.375';
    const base = lossQuantity.inbound.preview, one = base.lines[0];
    const v = { ...clone(base), schema_version: '2.0', lines: [
      { ...one, accepted_qty: '0.625' },
      { ...one, condition_code: 'damaged', target_account_id: other, accepted_qty: '0.375' },
    ] };
    expect(preview(v, lossQuantity.identity, r).lines).toEqual(v.lines);
    const wrong = clone(v); wrong.lines[0].accepted_qty = '0.750'; wrong.lines[1].accepted_qty = '0.250';
    expect(() => preview(wrong, lossQuantity.identity, r)).toThrow();
    expect(() => preview({ ...v, schema_version: '1.0' }, lossQuantity.identity, r)).toThrow();
    expect(() => preview({ ...v, lines: v.lines.map(l => ({ ...l, target_account_id: other })) }, lossQuantity.identity, r)).toThrow();
  });
  it('version two requires exactly the accepted damaged SN in the damaged account', () => {
    const r = receipt();
    r.lines[0].damaged_qty = r.lines[0].accepted_qty;
    r.lines[0].damaged_serial_ids = r.lines[0].accepted_serials.map(s => s.serial_id);
    const v = { ...clone(f.inbound.preview), schema_version: '2.0' };
    v.lines[0].condition_code = 'damaged';
    expect(preview(v, f.identity, r).lines[0].condition_code).toBe('damaged');
    v.lines[0].condition_code = 'new';
    expect(() => preview(v, f.identity, r)).toThrow();
  });
  it('does not clear from a generic missing/pending response', () => {
    expect(() => lookup({ lookup_status: 'not_found' }, marker())).toThrow();
    expect(() => lookup(null, marker())).toThrow();
    expect(() => result({ ...f.inbound.posted, posting_transaction_id: '' }, marker())).toThrow();
    expect(() => result({ ...f.inbound.posted, plan_hash: 'a'.repeat(64) }, marker())).toThrow();
  });
  it('a seal proves only the exact original request, not inventory posting', () => {
    const m = marker(), seal = { schema_version: '1.0', lookup_status: 'sealed', seal: { seal_id: other, receipt_id: m.receipt_id, shipment_id: m.shipment_id, request_id: m.command.request_id, request_hash: m.request_hash, sealed_at: f.inbound.after.checked_at } };
    expect(lookup(seal, m).status).toBe('sealed');
    expect(() => lookup({ ...seal, seal: { ...seal.seal, receipt_id: other } }, m)).toThrow();
  });
});
