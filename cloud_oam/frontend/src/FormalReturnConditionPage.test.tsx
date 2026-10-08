// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Page from './FormalReturnConditionPage';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';
import { prepare, type Pending } from './returnConditionCommands';
import { createStore } from './returnConditionRecovery';
import type { ConditionAdapter } from './returnConditionAdapter';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
afterEach(cleanup);
function setup() {
  const history = conditionFixture(), identity = { person_id: conditionId(30), authorization_version: 1 };
  const store = createStore(new MemoryStorage(), locks());
  const adapter: ConditionAdapter = {
    inbox: vi.fn(),
    download: vi.fn(),
    receipt: vi.fn(),
    context: vi.fn(async action => ({ ...identity, action, authority_hash: 'a'.repeat(64), can_read: true, can_write: action !== 'submit' })),
    read: vi.fn(async () => structuredClone(history)), source: vi.fn(), verifySource: vi.fn(),
    prepare: vi.fn(async (inbound, kind, caseId, intent) => ({ history, source: null,
      pending: await prepare(identity.person_id, 1, inbound, { action: kind, case_id: caseId,
        expected_event_id: history.cases[0].latest_event_id, expected_event_hash: history.cases[0].latest_event_hash,
        reason: intent.reason, evidence_file_ids: intent.evidence_file_ids, serial_verifications: intent.scans,
        request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() }) })),
    submit: vi.fn(async () => { throw new Error('synthetic response lost'); }), lookup: vi.fn(), seal: vi.fn(),
  };
  return { identity, inboundId: history.inbound_line_id, history, adapter, store, onBack: vi.fn() };
}
async function selectExecution() {
  await screen.findByRole('heading', { name: '合成测试物料' });
  await waitFor(() => expect((screen.getByLabelText('办理案件') as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('办理案件'), { target: { value: conditionId(10) } });
  fireEvent.change(screen.getByLabelText('本次动作'), { target: { value: 'execute' } });
  await waitFor(() => expect((screen.getByLabelText('办理说明') as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('办理说明'), { target: { value: '现场重新核对坏件并执行' } });
}
async function confirmExecution() {
  fireEvent.click(screen.getByLabelText('已现场核对本次实物、数量和成色'));
  fireEvent.click(screen.getByRole('button', { name: '核对本次办理' }));
  await screen.findByRole('region', { name: '确认成色纠正办理' });
  fireEvent.click(screen.getByLabelText('已核对本次对象、证据和库存影响，确认继续'));
  fireEvent.click(screen.getByRole('button', { name: '确认提交本次办理' }));
}
function executionFact(p: Pending) {
  return { ...conditionFixture().events[2].fact, event_id: conditionId(100), action: 'execute', status: 'executed',
    stock_effect: 'status_change', request_id: p.original.request_id, reason: p.original.reason,
    actor_person_id: p.person_id, posting_transaction_id: conditionId(101), posting_movement_id: conditionId(102) };
}
it('keeps approved separate from execution and saves an uncertain write for recovery without replay', async () => {
  const p = setup(); render(<Page {...p} />); await selectExecution();
  expect(p.adapter.prepare).not.toHaveBeenCalled(); expect(p.adapter.submit).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '核对本次办理' }) as HTMLButtonElement).disabled).toBe(true);
  vi.mocked(p.adapter.submit).mockImplementation(async pending => {
    expect(p.store.read(pending.person_id, pending.inbound_line_id)).toEqual({ status: 'valid', value: pending });
    throw new Error('synthetic response lost');
  });
  await confirmExecution();
  await screen.findByText(/本次核验或操作未能完整确认/);
  const saved = p.store.list(p.identity.person_id); expect(saved).toHaveLength(1);
  expect(p.adapter.submit).toHaveBeenCalledTimes(1); expect(p.adapter.lookup).not.toHaveBeenCalled();
  vi.mocked(p.adapter.lookup).mockResolvedValue({ request_state: 'found', request_id: saved[0].original.request_id,
    result_scope: 'historical_original_outcome', result: executionFact(saved[0]), original_input_hash: saved[0].original_input_hash,
    absence_sealed: false, retry_allowed: false, current_stock_verified: false, observed_ledger_cursor: 21, current_case_status: 'executed' });
  // Lost write responses are recovered through reads; revocation of write does not block this.
  vi.mocked(p.adapter.context).mockImplementation(async action => ({ ...p.identity, action, authority_hash: 'b'.repeat(64), can_read: true, can_write: false }));
  fireEvent.click(await screen.findByRole('button', { name: '查询原结果' }));
  await waitFor(() => expect(p.store.list(p.identity.person_id)).toEqual([]));
  expect(p.adapter.submit).toHaveBeenCalledTimes(1); await waitFor(() => expect(p.adapter.read).toHaveBeenCalledTimes(2));
});
it('rechecks authority before sending and does not persist or post after permission revocation', async () => {
  const p = setup(); render(<Page {...p} />); await selectExecution();
  fireEvent.click(screen.getByLabelText('已现场核对本次实物、数量和成色'));
  fireEvent.click(screen.getByRole('button', { name: '核对本次办理' }));
  await screen.findByRole('region', { name: '确认成色纠正办理' });
  vi.mocked(p.adapter.context).mockImplementation(async action => ({ ...p.identity, action, authority_hash: 'b'.repeat(64), can_read: true, can_write: false }));
  fireEvent.click(screen.getByLabelText('已核对本次对象、证据和库存影响，确认继续'));
  fireEvent.click(screen.getByRole('button', { name: '确认提交本次办理' }));
  await screen.findByText(/本次核验或操作未能完整确认/);
  expect(p.adapter.submit).not.toHaveBeenCalled(); expect(p.store.list(p.identity.person_id)).toEqual([]);
});
it('drops prepared confirmation when the identity changes', async () => {
  const p = setup(), view = render(<Page {...p} />); await selectExecution();
  fireEvent.click(screen.getByLabelText('已现场核对本次实物、数量和成色'));
  fireEvent.click(screen.getByRole('button', { name: '核对本次办理' }));
  await screen.findByRole('region', { name: '确认成色纠正办理' });
  view.rerender(<Page {...p} identity={{ ...p.identity, authorization_version: 2 }} />);
  await screen.findByText(/本次核验或操作未能完整确认/);
  expect(screen.queryByRole('region', { name: '确认成色纠正办理' })).toBeNull(); expect(p.adapter.submit).not.toHaveBeenCalled();
});
it('shows original event attachments and drops a verified link when changing action', async () => {
  const p = setup(); vi.mocked(p.adapter.download).mockResolvedValue({ url: 'https://synthetic.invalid/proof', expires_at: new Date(Date.now() + 60000).toISOString() });
  render(<Page {...p} />); await selectExecution();
  fireEvent.click(screen.getByRole('button', { name: '核验附件 1' }));
  await screen.findByRole('link', { name: '打开附件 1' });
  expect(p.adapter.download).toHaveBeenCalledWith(p.inboundId, p.history.events[0].fact.event_id, p.history.events[0].evidence_file_ids[0]);
  fireEvent.change(screen.getByLabelText('本次动作'), { target: { value: 'cancel_approved' } });
  expect(screen.queryByRole('link', { name: '打开附件 1' })).toBeNull();
});
