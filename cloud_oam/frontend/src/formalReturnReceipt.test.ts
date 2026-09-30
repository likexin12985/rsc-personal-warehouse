import { beforeAll, describe, expect, it, vi } from 'vitest';
import lossQuantity from './test-fixtures/return-receiving/loss-receiving-quantity.json';
import lossSerial from './test-fixtures/return-receiving/loss-receiving-serial.json';
import workQuantity from './test-fixtures/return-receiving/work-order-receiving-quantity.json';
import workSerial from './test-fixtures/return-receiving/work-order-receiving-serial.json';
import { history, micros } from './formalReturnReceiving';
import { checkSelection, command, input, preview, requestHash, result, trimmed } from './formalReturnReceipt';
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
beforeAll(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
const other = '11111111-1111-4111-8111-111111111111';
describe('return receipt original request', () => {
  for (const [name, f] of Object.entries({ lossQuantity, lossSerial, workQuantity, workSerial })) {
    it(`${name}: matches backend canonical intent, preview and exact receipt`, async () => {
      const c = command(f.command, f.identity.person_id), { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...body } = c;
      const h = history(f.before, f.identity, f.before.package.shipment_id);
      expect(await requestHash(h.package.shipment_id, body)).toBe(f.receipt.request_hash);
      expect(checkSelection(body, h)).toBeUndefined();
      const view = await preview(f.preview, f.identity, body, h);
      expect(view).toEqual({ ...f.preview, received_at: body.received_at });
      expect(await result(f.receipt, f.identity, h.package, c)).toEqual(f.receipt);
    });
  }
  const f = lossSerial;
  const h = () => history(f.before, f.identity, f.before.package.shipment_id);
  const body = () => { const { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...b } = command(f.command, f.identity.person_id); return b; };
  it('normalizes Python whitespace without losing microseconds', () => {
    const b = body(), shifted = input({ ...b, received_at: '2026-09-30T08:00:00.123456+08:00', reason: '\u0085真实验收\u001c' }, f.identity.person_id);
    expect(shifted.received_at).toBe('2026-09-30T00:00:00.123456Z');
    expect(micros(shifted.received_at)).toBe(micros('2026-09-30T08:00:00.123456+08:00'));
    expect(shifted.reason).toBe('真实验收');
    expect(() => trimmed('错误\u000b原因', 500, true)).toThrow();
  });
  it('refuses another operator, duplicate lines and an already accepted parcel', () => {
    expect(() => input({ ...body(), operator_person_id: other }, f.identity.person_id)).toThrow();
    expect(() => input({ ...body(), lines: [body().lines[0], body().lines[0]] }, f.identity.person_id)).toThrow();
    expect(() => checkSelection(body(), history(f.after, f.identity, f.after.package.shipment_id))).toThrow();
  });
  it('requires actual matching SKU, SN and a nonempty QR proof', () => {
    const b = body(); b.lines[0].accepted_serial_verifications[0].sku_code += '-wrong';
    expect(() => checkSelection(b, h())).toThrow();
    const sn = body(); sn.lines[0].accepted_serial_verifications[0].serial_id = other;
    expect(() => checkSelection(sn, h())).toThrow();
    const qr = body(); qr.lines[0].accepted_serial_verifications[0].qr_code = '';
    expect(() => checkSelection(qr, h())).toThrow();
  });
  it('rejects changed preflight identity, source, timestamp, quantities and authority', async () => {
    for (const fields of [{ operator_person_id: other }, { operation_id: other }, { authorization_version: 99 }, { received_at: '2020-01-01T00:00:00Z' }, { reason: '替换验收原因' }, { request_hash: 'a'.repeat(64) }]) {
      await expect(preview({ ...f.preview, ...fields }, f.identity, body(), h())).rejects.toThrow();
    }
    const altered = clone(f.preview); altered.lines[0].previously_accepted_qty = '1.000';
    await expect(preview(altered, f.identity, body(), h())).rejects.toThrow();
    await expect(preview({ ...f.preview, lines: [] }, f.identity, body(), h())).rejects.toThrow();
  });
  it('cannot resolve a different original request or a forged result', async () => {
    const c = command(f.command, f.identity.person_id);
    for (const fields of [{ request_id: 'another-request' }, { plan_hash: 'a'.repeat(64) }, { reason: '不同内容' }, { request_hash: 'a'.repeat(64) }, { lines: [] }]) {
      await expect(result({ ...f.receipt, ...fields }, f.identity, h().package, c)).rejects.toThrow();
    }
  });
});
