import { describe, expect, it } from 'vitest';
import { identity } from './formalLossReview';
import { requestHash } from './lossCorrectionContracts';
import { checkStopTarget, prepareStop, stopIntent, stopPending, stopPosted, stopResolution, stopSource, verifyStopPending } from './lossReturnStopContracts';
import quantity from './test-fixtures/loss-return-stop/quantity.json';
import serial from './test-fixtures/loss-return-stop/serial.json';

for (const [name, fixture] of Object.entries({ quantity, serial })) {
  describe(`${name} actual PG16 HTTP stop response`, () => {
    const root = fixture.original.root_disposition_id, who = identity(fixture.source);
    const source = () => stopSource(fixture.source, who, root);
    const saved = () => stopPending({ v: 1, flow: 'return-stop', ...who, source: source(),
      preview: fixture.preview, command: fixture.original, request_hash: fixture.request_hash });
    it('preserves dedicated source and exact native request hash', async () => {
      const pending = await verifyStopPending(saved());
      expect(await requestHash(pending.command, 'inverses')).toBe(fixture.request_hash);
      expect(pending.flow).toBe('return-stop');
      expect(pending.source.result_scope).toBe('verified_loss_return_stop_references');
      expect(stopResolution(fixture.missing, pending)).toEqual({ status: 'pending' });
      expect(stopResolution(fixture.found, pending)).toEqual({ status: 'found' });
      const sealed = { ...pending, command: fixture.sealed_command, request_hash: await requestHash(fixture.sealed_command, 'inverses') };
      expect(stopResolution(fixture.sealed, await verifyStopPending(sealed))).toEqual({ status: 'sealed' });
      const after = stopSource(fixture.after, who, root);
      expect(after.state).toBe('stopped'); expect(after.preview_reference).toBeNull();
      expect(after.stop?.reversal_id).toBe(fixture.found.result.reversal_id);
      expect(() => checkStopTarget(pending, after)).toThrow();
    });
    it('prepares a fresh explicit command without inventing a generic available state', async () => {
      const pending = await prepareStop(source(), fixture.original.reason, fixture.preview);
      expect(pending.command.request_id).not.toBe(fixture.original.request_id);
      expect(pending.command.idempotency_key).not.toBe(fixture.original.idempotency_key);
      expect(pending.source.state).toBe('preview_required');
      expect(await verifyStopPending(pending)).toEqual(pending);
    });
    it.each([
      { write_authorization_provided: true }, { current_stock_verified: true }, { stock_effect: 'posted' },
      { state: 'stopped' }, { state: 'downstream_compensation_required' }, { preview_reference: null },
      { authorization_version: who.authorization_version + 1 }, { observed_ledger_cursor: 0 },
      { quantity: '0' }, { quantity: 1 }, { stop: {} }, { command_jsonb: {} },
    ])('rejects contradictory or private source fields %j', patch => {
      expect(() => stopSource({ ...fixture.source, ...patch }, who, root)).toThrow();
    });
    it('allows downstream history without creating a stop action', () => {
      const downstream = stopSource({ ...fixture.source, state: 'downstream_compensation_required', preview_reference: null }, who, root);
      expect(() => stopIntent(downstream, 'New reason')).toThrow();
      expect(() => checkStopTarget(saved(), downstream)).toThrow();
    });
    it.each(['return_operation_id', 'return_line_id', 'original_execution_id', 'plan_hash', 'quantity'])('rejects changed preview %s', field => {
      const p = saved(), changed = field === 'quantity' ? '999.000' : field === 'plan_hash' ? 'f'.repeat(64) : crypto.randomUUID();
      expect(() => stopPending({ ...p, preview: { ...p.preview, [field]: changed } })).toThrow();
    });
    it('rejects generic flow, a changed request body, and a reply for another request', async () => {
      const p = saved(); expect(() => stopPending({ ...p, flow: 'inverses' })).toThrow();
      await expect(verifyStopPending({ ...p, command: { ...p.command, request_id: 'different-request' } })).rejects.toThrow();
      expect(() => stopResolution({ ...fixture.found, retry_allowed: true }, p)).toThrow();
      expect(() => stopResolution({ ...fixture.found, request_id: 'different-request' }, p)).toThrow();
      expect(() => stopPosted({ ...fixture.found.result, return_operation_id: p.source.return_operation_id }, p)).toThrow();
      expect(() => stopPosted({ ...fixture.found.result, line_id: crypto.randomUUID() }, p)).toThrow();
    });
    it('keeps physical fulfillment history distinct from stop posting', () => {
      expect(fixture.history_after.lines).toEqual(fixture.history_before.lines);
      expect(fixture.history_after.coordinates).toEqual(fixture.history_before.coordinates);
      expect(fixture.after.current_stock_verified).toBe(false);
    });
  });
}
