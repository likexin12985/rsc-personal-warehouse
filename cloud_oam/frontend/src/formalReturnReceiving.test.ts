import { describe, expect, it } from 'vitest';
import lossQuantity from './test-fixtures/return-receiving/loss-receiving-quantity.json';
import lossSerial from './test-fixtures/return-receiving/loss-receiving-serial.json';
import workQuantity from './test-fixtures/return-receiving/work-order-receiving-quantity.json';
import workSerial from './test-fixtures/return-receiving/work-order-receiving-serial.json';
import { directory, detail, history, micros, parcel, quantity, source, units } from './formalReturnReceiving';
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const other = '11111111-1111-4111-8111-111111111111';

describe('return receiving service contracts', () => {
  for (const [name, fixture] of Object.entries({ lossQuantity, lossSerial, workQuantity, workSerial })) {
    it(`${name}: reads actual service directory and before/after acceptance without claiming posting`, () => {
      const { identity, before, after } = fixture;
      expect(directory(fixture.directory, identity).items).toEqual(fixture.directory.items);
      expect(history(before, identity, before.package.shipment_id)).toEqual(before);
      expect(history(after, identity, after.package.shipment_id)).toEqual(after);
      expect(() => detail({ ...fixture.directory, items: undefined }, identity, before.package.shipment_id)).toThrow();
    });
  }
  it('reads the independent parcel detail contract', () => {
    const { items, next_after_id: _next, ...base } = lossQuantity.directory;
    expect(detail({ ...base, package: items[0] }, lossQuantity.identity, items[0].shipment_id).package).toEqual(items[0]);
  });
  it.each([null, {}, { origin: null }, { work_order_id: other, origin: lossQuantity.before.package.origin }])('rejects missing or mixed source %j', raw => {
    expect(() => source(raw)).toThrow();
  });
  it('requires all five loss origin fields and rejects an injected request key', () => {
    const pkg = clone(lossQuantity.before.package);
    expect(() => parcel({ ...pkg, origin: { ...pkg.origin, disposition_id: null } }, lossQuantity.identity)).toThrow();
    expect(() => parcel({ ...pkg, idempotency_key: 'secret-key' }, lossQuantity.identity)).toThrow();
  });
  it('allows new loss stock but never broadens ordinary work-order conditions', () => {
    expect(parcel(lossQuantity.before.package, lossQuantity.identity).lines[0].condition_code).toBe('new');
    const pkg = clone(workQuantity.before.package); pkg.lines[0].condition_code = 'new';
    expect(() => parcel(pkg, workQuantity.identity)).toThrow();
  });
  it('binds current person, authorization version, parcel and custody', () => {
    const f = lossQuantity;
    expect(() => directory(f.directory, { ...f.identity, person_id: other })).toThrow();
    expect(() => history(f.after, { ...f.identity, authorization_version: 8 }, f.after.package.shipment_id)).toThrow();
    expect(() => history(f.after, f.identity, other)).toThrow();
    const h = clone(f.after); h.receipts[0].target_custody_assignment_id = other;
    expect(() => history(h, f.identity, h.package.shipment_id)).toThrow();
  });
  it('rejects a valid-looking receipt taken from a different loss disposition', () => {
    const h = clone(lossQuantity.after); h.receipts[0].origin.disposition_id = other;
    expect(() => history(h, lossQuantity.identity, h.package.shipment_id)).toThrow();
  });
  it('validates requested page size and cursor including unavailable objects', () => {
    const base = lossQuantity.directory, blocked = (id: number) => ({ verification_status: 'unavailable', shipment_id: `11111111-1111-4111-8111-${String(id).padStart(12, '0')}`, code: 'stock_return_receiving_verification_required', message: '原单待核验' });
    const rows = Array.from({ length: 20 }, (_, i) => blocked(i + 1));
    expect(directory({ ...base, items: rows, next_after_id: rows[19].shipment_id }, lossQuantity.identity, 20).items).toHaveLength(20);
    expect(() => directory({ ...base, items: rows, next_after_id: rows[19].shipment_id }, lossQuantity.identity, 10)).toThrow();
    expect(() => directory({ ...base, items: [rows[0]], next_after_id: rows[0].shipment_id }, lossQuantity.identity, 20)).toThrow();
    expect(() => directory({ ...base, items: rows, next_after_id: rows[19].shipment_id }, lossQuantity.identity, 20, rows[0].shipment_id)).toThrow();
    expect(() => directory({ ...base, items: [rows[1], rows[0]], next_after_id: null }, lossQuantity.identity)).toThrow();
  });
  it.each(['1', '1.0000', '01.000', '-1.000', '1000000000000000.000', 'NaN', 1.001])('rejects noncanonical or out of range quantity %s', raw => {
    expect(() => quantity(raw)).toThrow();
  });
  it('uses integer milliunits beyond JS safe integer precision', () => {
    expect(units('999999999999999.999') - units('999999999999999.998')).toBe(1n);
  });
  it('rejects impossible dates, preserves microseconds and normalizes offset comparison', () => {
    expect(() => micros('2026-02-30T00:00:00Z')).toThrow();
    expect(micros('2026-09-30T08:00:00.000001+08:00') - micros('2026-09-30T00:00:00Z')).toBe(1n);
    const h = clone(lossQuantity.after); h.queried_at = h.receipts[0].recorded_at.replace(/\.\d+Z$/, '.000000Z');
    expect(() => history(h, lossQuantity.identity, h.package.shipment_id)).toThrow();
  });
  it('rejects forged progress, incomplete histories and repeated receipts', () => {
    const f = lossQuantity, h = clone(f.after); h.lines[0].accepted_qty = '0.000';
    expect(() => history(h, f.identity, h.package.shipment_id)).toThrow();
    expect(() => history({ ...f.after, receipts: [] }, f.identity, h.package.shipment_id)).toThrow();
    expect(() => history({ ...f.after, receipts: [...f.after.receipts, f.after.receipts[0]] }, f.identity, h.package.shipment_id)).toThrow();
  });
  it('rejects omitted SN, substituted serial numbers and duplicate serials', () => {
    const f = lossSerial, h = clone(f.after);
    h.receipts[0].lines[0].accepted_serials = [];
    expect(() => history(h, f.identity, h.package.shipment_id)).toThrow();
    const substituted = clone(f.after); substituted.receipts[0].lines[0].accepted_serials[0].serial_no += '-changed';
    expect(() => history(substituted, f.identity, h.package.shipment_id)).toThrow();
    const duplicated = clone(f.before.package); duplicated.lines[0].serials.push(duplicated.lines[0].serials[0]);
    expect(() => parcel(duplicated, f.identity)).toThrow();
  });
  it('shortage leaves stock/SN unconfirmed and permits the same SN in a later receipt', () => {
    const f = lossSerial, h = history(clone(f.after), f.identity, f.after.package.shipment_id), first = clone(h.receipts[0]), line = first.lines[0];
    line.shortage_qty = line.accepted_qty; line.accepted_qty = '0.000'; line.shortage_serials = line.accepted_serials; line.accepted_serials = [];
    line.exceptions = [{ exception_type: 'shortage', description: '本批短少，等待补交', evidence_file_id: other }];
    first.status = 'exception'; first.receipt_id = other; first.request_id = 'shortage-original-request';
    h.receipts = [first]; h.lines = clone(f.before.lines);
    expect(history(h, f.identity, h.package.shipment_id).lines[0].unconfirmed_serials).toEqual(f.before.lines[0].unconfirmed_serials);
    h.receipts.push(history(clone(f.after), f.identity, f.after.package.shipment_id).receipts[0]); h.lines = clone(f.after.lines);
    expect(history(h, f.identity, h.package.shipment_id).lines).toEqual(f.after.lines);
  });
  it('damage is a subset of accepted quantity/SN and requires evidence', () => {
    const f = lossSerial, h = history(clone(f.after), f.identity, f.after.package.shipment_id), line = h.receipts[0].lines[0];
    line.damaged_qty = line.accepted_qty; line.damaged_serial_ids = line.accepted_serials.map(s => s.serial_id);
    line.exceptions = [{ exception_type: 'damaged', description: '到货破损', evidence_file_id: other }];
    h.receipts[0].status = 'exception'; h.lines[0].damaged_qty = line.damaged_qty;
    expect(history(h, f.identity, h.package.shipment_id).lines[0].accepted_qty).toBe(f.after.lines[0].accepted_qty);
    line.damaged_serial_ids = [other];
    expect(() => history(h, f.identity, h.package.shipment_id)).toThrow();
    line.damaged_serial_ids = line.accepted_serials.map(s => s.serial_id); line.exceptions = [];
    expect(() => history(h, f.identity, h.package.shipment_id)).toThrow();
  });
});
