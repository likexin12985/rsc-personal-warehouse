import { fixture as historyFixture } from './lossReturnHistoryFixtures';
import { returnHistory } from './lossReturnHistory';
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalLossCorrection from './FormalLossCorrection';
import { identity } from './formalLossReview';
import { fixtures, saved } from './lossCorrectionFixtures';
import { sources, type Flow, type Pending } from './lossCorrectionContracts';
import { createStore, type Context } from './lossCorrectionRecovery';
import type { Adapter } from './lossCorrectionAdapter';
import { MemoryStorage, locks } from './lossExecutionTestSupport';

beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const previewNames = { inverses: '预览冲销', approvals: '核对审批决定', executions: '预览纠正执行' };
const confirmNames = { inverses: '确认冲销', approvals: '确认批准纠正', executions: '确认执行纠正' };

async function world(flow: Flow = 'inverses', sealed = false) {
  const f = fixtures.find(f => f.flow === flow && f.name.startsWith('quantity'))!, p = await saved(f, sealed);
  const store = createStore(new MemoryStorage(), locks());
  const context: Context = { ...identity(p), authority_hash: 'a'.repeat(64), can_read: true, can_write: { inverses: true, approvals: true, executions: true } };
  let current = p.source;
  const unavailableStop = vi.fn(async () => { throw new Error('Synthetic dedicated stop source unavailable'); });
  const adapter: Adapter = {
    returnStop: { context: vi.fn(async () => structuredClone(context)), read: unavailableStop, source: unavailableStop, prepare: unavailableStop, execute: unavailableStop, lookup: unavailableStop, seal: unavailableStop },
    history: vi.fn(async () => { throw new Error('Explicit return history request required'); }),
    context: vi.fn(async () => structuredClone(context)),
    list: vi.fn(async () => { throw new Error('This page cannot guess another root'); }),
    read: vi.fn(async () => current), source: vi.fn(async () => current),
    describe: vi.fn(async () => ({ source: current, report: f.data.origin.report,
      line: f.data.origin.report.lines.find(l => l.line_id === current.line_id)! })),
    prepare: vi.fn(async () => p),
    execute: vi.fn(async () => { current = sources(f.data.after, identity(p), p.command.root_disposition_id); return f.data.found.result; }),
    lookup: vi.fn(async () => f.data.found), seal: vi.fn(async () => f.data.sealed),
  };
  const props = { identity: identity(p), rootId: p.command.root_disposition_id, adapter, store, onBack: vi.fn() };
  return { f, p, store, context, adapter, props, setSource: (s: typeof current) => { current = s; } };
}
async function prepareUI(w: Awaited<ReturnType<typeof world>>) {
  await screen.findByRole('region', { name: '原报损明细' });
  fireEvent.change(screen.getByRole('textbox', { name: '本次操作理由' }), { target: { value: w.p.command.reason } });
  if (w.p.flow === 'approvals') fireEvent.change(screen.getByRole('combobox', { name: '纠正处置' }), { target: { value: w.p.command.disposition } });
  if (w.p.flow === 'executions') fireEvent.change(screen.getByRole('combobox', { name: '已批准的纠正决定' }), { target: { value: w.p.command.correction_decision_id } });
  fireEvent.click(screen.getByRole('button', { name: previewNames[w.p.flow] }));
  return screen.findByRole('button', { name: confirmNames[w.p.flow] });
}

it.each(['inverses', 'approvals', 'executions'] as const)('%s needs explicit confirmation and reports its own state only', async flow => {
  const w = await world(flow); render(<FormalLossCorrection {...w.props} />);
  const confirm = await prepareUI(w); expect((confirm as HTMLButtonElement).disabled).toBe(true); expect(w.adapter.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(confirm);
  await screen.findByText(flow === 'approvals' ? /纠正决定已批准，库存未改变/ : flow === 'inverses' ? /冲销已记账，对应物资恢复冻结/ : /本次纠正已记账。当前库存请到库存页核验/);
  expect(w.adapter.execute).toHaveBeenCalledExactlyOnceWith(w.p); expect(w.store.list(w.p.person_id)).toEqual([]);
});

it('persists a lost response across remount and permits only read recovery after write revocation', async () => {
  const w = await world(); vi.mocked(w.adapter.execute).mockRejectedValueOnce(new Error('网络响应丢失'));
  const view = render(<FormalLossCorrection {...w.props} />); const confirm = await prepareUI(w);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(confirm); await screen.findByText('网络响应丢失');
  expect(w.store.list(w.p.person_id)).toEqual([w.p]); view.unmount(); w.context.can_write.inverses = false;
  w.setSource(sources(w.f.data.after, identity(w.p), w.p.command.root_disposition_id));
  render(<FormalLossCorrection {...w.props} />); const recover = await screen.findByRole('button', { name: '回查原请求' });
  await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false)); expect(w.adapter.lookup).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '永久封存原请求' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(recover); await screen.findByText(/冲销已记账，对应物资恢复冻结/);
  expect(w.adapter.execute).toHaveBeenCalledTimes(1); expect(w.store.list(w.p.person_id)).toEqual([]);
});

it('a missing result retains the request; permanent sealing has its own cancelable confirmation', async () => {
  const w = await world('approvals', true);
  await w.store.withLease(w.p.person_id, w.p.command.root_disposition_id, async l => l.persist(w.p));
  const missing = { ...(w.f.data.missing as object), request_id: w.p.command.request_id, request_hash: w.p.request_hash };
  vi.mocked(w.adapter.lookup).mockResolvedValue(missing); render(<FormalLossCorrection {...w.props} />);
  const button = await screen.findByRole('button', { name: '回查原请求' }); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button); await screen.findByText(/尚未查到原请求结果/); expect(w.store.list(w.p.person_id)).toEqual([w.p]);
  fireEvent.click(screen.getByRole('button', { name: '永久封存原请求' })); await screen.findByRole('alertdialog');
  fireEvent.click(screen.getByRole('button', { name: '取消封存' })); expect(w.adapter.seal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '永久封存原请求' }));
  vi.mocked(w.adapter.lookup).mockResolvedValueOnce(missing).mockResolvedValue(w.f.data.sealed);
  fireEvent.click(screen.getByRole('button', { name: '确认永久封存' })); await screen.findByText(/本次原请求已永久封存/);
  expect(w.adapter.seal).toHaveBeenCalledExactlyOnceWith(w.p); expect(w.adapter.execute).not.toHaveBeenCalled();
});

it('does not auto-select approval, and unsupported dedicated flows cannot be executed', async () => {
  const w = await world('executions'); const s = structuredClone(w.p.source);
  const dedicated = { correction_decision_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', disposition: 'return_to_region' as const,
    reason: '合成独立退回决定', execution_mode: 'dedicated_flow_required' as const, preview_reference: null };
  s.approval_choices.push(dedicated); w.setSource(s); render(<FormalLossCorrection {...w.props} />);
  await screen.findByRole('region', { name: '原报损明细' });
  const selector = screen.getByRole('combobox', { name: '已批准的纠正决定' }); expect((selector as HTMLSelectElement).value).toBe('');
  expect((screen.getByRole('button', { name: '预览纠正执行' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(selector, { target: { value: dedicated.correction_decision_id } });
  await screen.findByText(/本页暂不能执行，物资仍保持冻结/); expect(screen.queryByRole('button', { name: '预览纠正执行' })).toBeNull();
  expect(w.adapter.prepare).not.toHaveBeenCalled(); expect(w.adapter.execute).not.toHaveBeenCalled();
});

it('discards a late preview after identity changes', async () => {
  const w = await world(); let resolve!: (p: Pending) => void;
  vi.mocked(w.adapter.prepare).mockImplementation(() => new Promise(r => { resolve = r; }));
  const view = render(<FormalLossCorrection {...w.props} />); await screen.findByRole('region', { name: '原报损明细' });
  fireEvent.change(screen.getByRole('textbox', { name: '本次操作理由' }), { target: { value: w.p.command.reason } });
  fireEvent.click(screen.getByRole('button', { name: '预览冲销' })); await waitFor(() => expect(w.adapter.prepare).toHaveBeenCalledTimes(1));
  view.rerender(<FormalLossCorrection {...w.props} identity={{ ...w.props.identity, authorization_version: w.props.identity.authorization_version + 1 }} />);
  resolve(w.p); await screen.findByText('当前身份或读取权限变化，请刷新');
  expect(screen.queryByRole('button', { name: '确认冲销' })).toBeNull(); expect(w.adapter.execute).not.toHaveBeenCalled();
});

it('unreadable local recovery storage stops new operations', async () => {
  const w = await world(); vi.spyOn(w.store, 'list').mockImplementation(() => { throw new Error('原请求存储损坏'); });
  render(<FormalLossCorrection {...w.props} />); await screen.findByRole('region', { name: '原报损明细' });
  fireEvent.change(screen.getByRole('textbox', { name: '本次操作理由' }), { target: { value: '核验原处置' } });
  expect((screen.getByRole('button', { name: '预览冲销' }) as HTMLButtonElement).disabled).toBe(true);
  expect(w.adapter.prepare).not.toHaveBeenCalled();
});


it('opens the read-only return history panel from the matching original return disposition', async () => {
  const w = await world(), source = structuredClone(w.p.source);
  source.chain_state = 'dedicated_compensation_required'; source.inverse_preview_reference = null;
  source.history.find(h => h.kind === 'original_execution')!.disposition = 'return_to_region';
  w.setSource(source);
  const history = historyFixture(); history.root_disposition_id = w.props.rootId;
  history.lines[0].original_quantity = source.quantity; history.lines[0].shares[5].quantity = source.quantity;
  vi.mocked(w.adapter.history).mockResolvedValue(returnHistory(history,
    { root: w.props.rootId, quantity: source.quantity, serials: source.serial_ids }));
  render(<FormalLossCorrection {...w.props} />);
  const query = await screen.findByRole('button', { name: '查询退回历史' });
  expect(screen.getByRole('region', { name: '退回停止与恢复' })).toBeTruthy();
  expect(w.adapter.history).not.toHaveBeenCalled(); fireEvent.click(query);
  await screen.findByText('历史入库成色需要核查');
  expect(w.adapter.history).toHaveBeenCalledExactlyOnceWith(w.props.rootId);
  expect(w.adapter.prepare).not.toHaveBeenCalled(); expect(w.adapter.execute).not.toHaveBeenCalled();
});
