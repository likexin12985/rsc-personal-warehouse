import { describe, expect, it } from 'vitest';
import fixture from './test-fixtures/stock-scrap/committed-quantity-results.json';
import { fact, resolution, type Kind } from './formalScrapFacts';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const another = '11111111-1111-4111-8111-111111111111';
const samples = fixture.samples.map(row => ({ kind: row.kind as Kind, value: row.fact }));
const found = (value: { request_id: string; request_hash: string }) => ({
  request_state: 'found', retry_allowed: false, request_id: value.request_id,
  request_hash: value.request_hash, result_scope: 'historical_original_outcome', result: value,
});

describe('actual committed PostgreSQL scrap and found-stock results', () => {
  it('covers all six request stages with two complete business generations', () => {
    expect(fixture.scope).toBe('synthetic-owned-PG16');
    expect(fixture.proof_scope).toBe('committed-business-payloads-only');
    expect(samples).toHaveLength(10);
    expect(new Set(samples.map(s => s.kind))).toEqual(new Set(['original', 'correction', 'apply', 'regional', 'headquarters', 'execute']));
  });
  for (const [index, { kind, value }] of samples.entries()) {
    it(`${kind}/${index}: parses exact native event bytes and binds historical lookup`, () => {
      expect(fact(kind, value)).toEqual(value);
      expect(resolution(found(value), kind, value)).toEqual({ status: 'found', result: value });
    });
    it(`${kind}/${index}: rejects another request and nested outcome`, () => {
      expect(() => resolution({ ...found(value), request_id: 'different-original-request' }, kind, value)).toThrow();
      expect(() => resolution({ ...found(value), request_hash: 'f'.repeat(64) }, kind, value)).toThrow();
      expect(() => resolution({ ...found(value), result: { ...value, request_id: 'different-original-request' } }, kind, value)).toThrow();
      expect(() => resolution({ ...found(value), result: { ...value, request_hash: 'f'.repeat(64) } }, kind, value)).toThrow();
    });
    it(`${kind}/${index}: never accepts missing proof, internal fields or a retry instruction`, () => {
      const missing: Record<string, unknown> = clone(value); delete missing.request_hash;
      expect(() => fact(kind, missing)).toThrow();
      expect(() => fact(kind, { ...value, command_jsonb: { private_key: 'secret' } })).toThrow();
      expect(() => resolution({ ...found(value), retry_allowed: true }, kind, value)).toThrow();
      expect(() => resolution({ ...found(value), result_scope: 'current_stock' }, kind, value)).toThrow();
    });
  }
  for (const { kind, value } of samples.filter(s => ['apply', 'regional', 'headquarters'].includes(s.kind))) {
    it(`${kind}: approval can never be represented as posted stock`, () => {
      expect(() => fact(kind, { ...value, stock_effect: 'restores_original_frozen_share' })).toThrow();
      expect(() => fact(kind, { ...value, status: 'posted' })).toThrow();
      expect(() => fact(kind, { ...value, authorization_version: 0 })).toThrow();
      expect(() => fact(kind, { ...value, authorization_version: Number.MAX_SAFE_INTEGER + 1 })).toThrow();
      expect(() => fact(kind, { ...value, actor_person_id: null })).toThrow();
    });
  }
  for (const { kind, value } of samples.filter(s => ['original', 'correction', 'execute'].includes(s.kind))) {
    it(`${kind}: exact asset boundary, stock effect and posting proofs are required`, () => {
      const input = value as Record<string, unknown>;
      expect(() => fact(kind, { ...value, posting_transaction_id: null })).toThrow();
      expect(() => fact(kind, { ...value, posting_movement_id: another, plan_hash: '' })).toThrow();
      expect(() => fact(kind, { ...value, quantity: '0.000' })).toThrow();
      expect(() => fact(kind, { ...value, quantity: 1 })).toThrow();
      expect(() => fact(kind, { ...value, stock_effect: 'frozen_to_available' })).toThrow();
      expect(() => fact(kind, { ...value, [kind === 'execute' ? 'source_account_id' : 'target_account_id']: another })).toThrow();
      if (kind === 'execute') expect(() => fact(kind, { ...value, original_execution_id: another })).toThrow();
      else expect(() => fact(kind, { ...value, source_kind: kind === 'original' ? 'correction' : 'original' })).toThrow();
      expect(input.quantity).toBeTruthy();
    });
  }
  it('missing remains unresolved and cannot stand in for a business failure or successful retry', () => {
    const { kind, value } = samples[0];
    const missing = { ...found(value), request_state: 'not_found', result_scope: 'unconfirmed_request', result: null };
    expect(resolution(missing, kind, value)).toEqual({ status: 'pending' });
    expect(() => resolution({ ...missing, retry_allowed: true }, kind, value)).toThrow();
    expect(() => resolution({ ...missing, result: value }, kind, value)).toThrow();
    expect(() => resolution({ ...missing, result_scope: 'closed_original_request' }, kind, value)).toThrow();
    expect(() => resolution({ request_state: 'not_found' }, kind, value)).toThrow();
  });
  for (const kind of ['original', 'correction', 'apply', 'regional', 'headquarters', 'execute'] as const) {
    it(`${kind}: seal proves closure of this request without inventory posting`, () => {
      const { value } = samples.find(s => s.kind === kind)!;
      const seal = { seal_id: another, kind, loss_operation_id: another, loss_line_id: another,
        root_disposition_id: kind === 'original' ? null : another,
        sealed_at: '2026-10-02T12:34:56.123456Z', stock_effect: 'none' };
      const answer = { ...found(value), request_state: 'sealed', result_scope: 'closed_original_request', result: null, seal };
      expect(resolution(answer, kind, value)).toEqual({ status: 'sealed', seal });
      expect(() => resolution({ ...answer, seal: { ...seal, kind: kind === 'original' ? 'execute' : 'original' } }, kind, value)).toThrow();
      expect(() => resolution({ ...answer, seal: { ...seal, stock_effect: 'posted' } }, kind, value)).toThrow();
      expect(() => resolution({ ...answer, seal: { ...seal, sealed_at: 'invalid' } }, kind, value)).toThrow();
      expect(() => resolution({ ...answer, result: value }, kind, value)).toThrow();
    });
  }
});
