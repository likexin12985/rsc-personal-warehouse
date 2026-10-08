// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConditionRequestRecovery from './ConditionRequestRecovery';
import fixtures from './test-fixtures/return-condition/command-hashes.json';
import { prepare, action } from './returnConditionCommands';
import { createStore, type Transport } from './returnConditionRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
afterEach(cleanup);
async function props() {
  const sample = fixtures.samples[0], p = await prepare(sample.person_id, 1, sample.inbound_line_id, sample.original);
  const store = createStore(new MemoryStorage(), locks()); await store.withLease(p, async lease => lease.persist(p));
  const unknown = { request_state: 'unknown', request_id: p.original.request_id, result_scope: 'historical_original_outcome', result: null,
    retry_allowed: false, current_stock_verified: false, observed_ledger_cursor: 20, absence_sealed: false };
  const sealed = { ...unknown, request_state: 'sealed', result_scope: 'closed_original_request', absence_sealed: true,
    original_input_hash: p.original_input_hash, original_preflight_verified: false, stock_effect: 'none',
    seal: { seal_id: crypto.randomUUID(), kind: action(p.original), inbound_line_id: p.inbound_line_id,
      case_id: null, expected_event_id: null, sealed_at: new Date().toISOString(), stock_effect: 'none' } };
  const transport: Transport = { context: vi.fn(async kind => ({ person_id: p.person_id, authorization_version: 1, action: kind,
    authority_hash: 'a'.repeat(64), can_read: true, can_write: true })), verifySource: vi.fn(), submit: vi.fn(),
    lookup: vi.fn(async () => unknown), seal: vi.fn(async () => sealed) };
  return { p, sealed, identity: { person_id: p.person_id, authorization_version: 1 }, store, transport };
}
it('does not send on mount, and an unknown result keeps the original with no replay option', async () => {
  const p = await props(); render(<ConditionRequestRecovery {...p} />);
  expect(p.transport.lookup).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '查询原结果' }));
  await screen.findByText(/暂未查到确定结果/);
  expect(p.store.list(p.identity.person_id)).toEqual([p.p]); expect(p.transport.submit).not.toHaveBeenCalled(); expect(p.transport.seal).not.toHaveBeenCalled();
});
it('shows the original outcome separately from the case’s later status', async () => {
  const p = await props(), c = p.p.original;
  const fact = { schema_version: 'condition_result/1', case_id: crypto.randomUUID(), event_id: crypto.randomUUID(),
    inbound_line_id: p.p.inbound_line_id, action: 'submit', status: 'awaiting_regional', quantity: '2.000',
    actor_user_id: 'synthetic-user', actor_person_id: p.p.person_id, authorization_version: 1,
    request_id: c.request_id, request_hash: 'd'.repeat(64), plan_hash: 'e'.repeat(64),
    posting_transaction_id: crypto.randomUUID(), posting_movement_id: crypto.randomUUID(), stock_effect: 'freeze', reason: c.reason };
  vi.mocked(p.transport.lookup).mockResolvedValue({ request_state: 'found', request_id: c.request_id,
    result_scope: 'historical_original_outcome', result: fact, absence_sealed: false, retry_allowed: false,
    current_stock_verified: false, observed_ledger_cursor: 25, current_case_status: 'executed', original_input_hash: p.p.original_input_hash });
  render(<ConditionRequestRecovery {...p} />); fireEvent.click(screen.getByRole('button', { name: '查询原结果' }));
  await screen.findByRole('heading', { name: '提交并冻结 · 待区域复核' });
  expect(screen.queryByRole('heading', { name: /已完成成色纠正/ })).toBeNull();
  expect(screen.getByText(/后续案件进展请查看成色纠正历史/)).toBeTruthy();
  expect(p.transport.submit).not.toHaveBeenCalled(); expect(p.store.list(p.identity.person_id)).toEqual([]);
});
it('requires explicit permanent closure confirmation and then rereads before removing the request', async () => {
  const p = await props(); render(<ConditionRequestRecovery {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '结束未执行的原请求' }));
  await screen.findByRole('dialog'); expect(p.transport.seal).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '确认结束原请求' }) as HTMLButtonElement).disabled).toBe(true);
  vi.mocked(p.transport.lookup).mockResolvedValueOnce(await p.transport.lookup(p.p)).mockResolvedValueOnce(p.sealed);
  vi.mocked(p.transport.lookup).mockClear();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '确认结束原请求' }));
  await screen.findByText(/原请求已永久关闭/);
  expect(p.transport.lookup).toHaveBeenCalledTimes(2); expect(p.transport.seal).toHaveBeenCalledTimes(1);
  expect(p.transport.submit).not.toHaveBeenCalled(); expect(p.store.list(p.identity.person_id)).toEqual([]);
});
it('a lost closure response is shown as uncertain and does not discard the saved original', async () => {
  const p = await props(); vi.mocked(p.transport.seal).mockRejectedValue(new Error('PRIVATE-ERROR'));
  render(<ConditionRequestRecovery {...p} />); fireEvent.click(screen.getByRole('button', { name: '结束未执行的原请求' }));
  await screen.findByRole('dialog'); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '确认结束原请求' }));
  await screen.findByRole('alert'); expect(screen.queryByText('PRIVATE-ERROR')).toBeNull();
  expect(p.store.list(p.identity.person_id)).toEqual([p.p]); expect(p.transport.seal).toHaveBeenCalledTimes(1);
});
