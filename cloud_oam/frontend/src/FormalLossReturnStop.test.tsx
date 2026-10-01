// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalLossReturnStop from './FormalLossReturnStop';
import { identity, type LossLine } from './formalLossReview';
import { fixtures, saved } from './lossReturnStopFixtures';
import { stopSource, type StopPending } from './lossReturnStopContracts';
import { createStore } from './lossReturnStopRecovery';
import type { StopAdapter } from './lossReturnStopAdapter';
import type { Context } from './lossCorrectionRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';

beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function world(name = 'quantity', sealed = false) {
  const f = fixtures.find(f => f.name === name)!, p = await saved(f, sealed), who = identity(p);
  const store = createStore(new MemoryStorage(), locks());
  const context: Context = { ...who, authority_hash: 'a'.repeat(64), can_read: true, can_write: { inverses: true, approvals: false, executions: false } };
  let source = p.source;
  const adapter: StopAdapter = {
    context: vi.fn(async () => structuredClone(context)), read: vi.fn(async () => source), source: vi.fn(async () => source),
    prepare: vi.fn(async () => p), execute: vi.fn(async () => { source = stopSource(f.data.after, who, p.command.root_disposition_id); return f.data.found.result; }),
    lookup: vi.fn(async () => f.data.found), seal: vi.fn(async () => f.data.sealed),
  };
  const line: LossLine = { line_id: p.source.report_line_id, material_id: '10000000-0000-4000-8000-000000000001',
    sku_code: 'SYNTHETIC-STOP', material_name: '合成测试备件', base_unit: '件', condition_code: 'new', lot_id: null, lot_no: null,
    quantity: p.source.quantity, serials: p.source.serial_ids.map((serial_id, i) => ({ serial_id, serial_no: `SN-${i + 1}` })) };
  const props = { identity: who, rootId: p.command.root_disposition_id, line, adapter, store, onSettled: vi.fn() };
  const persist = () => store.withLease(p.person_id, p.command.root_disposition_id, async lease => lease.persist(p));
  return { f, p, props, context, adapter, store, persist, setSource: (value: typeof source) => { source = value; } };
}
async function prepare(w: Awaited<ReturnType<typeof world>>) {
  const reason = await screen.findByRole('textbox', { name: '退回停止理由' });
  fireEvent.change(reason, { target: { value: w.p.command.reason } });
  fireEvent.click(screen.getByRole('button', { name: '预览退回停止' }));
  return screen.findByRole('button', { name: '确认停止退回' });
}
it.each(['quantity', 'serial'])('%s requires explicit confirmation and reports frozen history only', async name => {
  const w = await world(name); render(<FormalLossReturnStop {...w.props} />);
  const confirm = await prepare(w); expect((confirm as HTMLButtonElement).disabled).toBe(true);
  expect(w.adapter.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(confirm); fireEvent.click(confirm);
  await screen.findByText(/已核验原请求：退回已停止/);
  await waitFor(() => expect(w.props.onSettled).toHaveBeenCalledTimes(1));
  expect(w.adapter.execute).toHaveBeenCalledExactlyOnceWith(w.p); expect(w.store.list(w.p.person_id)).toEqual([]);
  expect(screen.queryByRole('button', { name: '预览退回停止' })).toBeNull();
  expect(screen.getByText(/当前库存需单独查询/)).toBeTruthy();
});
it('retains a lost reply across remount and permits recovery after write revocation', async () => {
  const w = await world(); vi.mocked(w.adapter.execute).mockRejectedValueOnce(new Error('提交响应丢失'));
  const view = render(<FormalLossReturnStop {...w.props} />); const confirm = await prepare(w);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(confirm); await screen.findByText('提交响应丢失');
  expect(w.store.list(w.p.person_id)).toEqual([w.p]); view.unmount();
  w.context.can_write.inverses = false; w.setSource(stopSource(w.f.data.after, identity(w.p), w.props.rootId));
  render(<FormalLossReturnStop {...w.props} />);
  const recover = await screen.findByRole('button', { name: '回查原停止请求' });
  await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false));
  expect(w.adapter.lookup).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: '永久封存停止请求' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(recover); await screen.findByText(/已核验原请求：退回已停止/);
  await waitFor(() => expect(w.store.list(w.p.person_id)).toEqual([])); expect(w.adapter.execute).toHaveBeenCalledTimes(1);
});
it('does not interpret a missing result as permission to replay; sealing needs separate confirmation', async () => {
  const w = await world('quantity', true); await w.persist();
  const missing = { ...w.f.data.missing, request_id: w.p.command.request_id, request_hash: w.p.request_hash };
  vi.mocked(w.adapter.lookup).mockResolvedValue(missing); render(<FormalLossReturnStop {...w.props} />);
  const recover = await screen.findByRole('button', { name: '回查原停止请求' });
  await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(recover); await screen.findByText(/尚未查到原停止请求结果/);
  expect(w.store.list(w.p.person_id)).toEqual([w.p]); expect(w.props.onSettled).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '永久封存停止请求' }));
  await screen.findByRole('alertdialog', { name: '确认永久封存停止请求' });
  fireEvent.click(screen.getByRole('button', { name: '取消封存停止请求' })); expect(w.adapter.seal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '永久封存停止请求' }));
  vi.mocked(w.adapter.lookup).mockResolvedValueOnce(missing).mockResolvedValue(w.f.data.sealed);
  fireEvent.click(screen.getByRole('button', { name: '确认封存停止请求' })); await screen.findByText(/本次原停止请求已永久封存/);
  await waitFor(() => expect(w.store.list(w.p.person_id)).toEqual([]));
  expect(w.adapter.seal).toHaveBeenCalledExactlyOnceWith(w.p); expect(w.adapter.execute).not.toHaveBeenCalled();
});
it('rejects downstream fulfillment and never prepares a whole stop', async () => {
  const w = await world(); w.setSource({ ...w.p.source, state: 'downstream_compensation_required', preview_reference: null });
  render(<FormalLossReturnStop {...w.props} />); await screen.findByText(/已有出库或后续履约事实/);
  expect(screen.queryByRole('button', { name: '预览退回停止' })).toBeNull(); expect(w.adapter.prepare).not.toHaveBeenCalled();
});
it.each(['identity', 'storage'])('discards a late preview when %s changes', async mode => {
  const w = await world(); let resolve!: (value: StopPending) => void;
  vi.mocked(w.adapter.prepare).mockImplementation(() => new Promise(r => { resolve = r; }));
  const view = render(<FormalLossReturnStop {...w.props} />);
  fireEvent.change(await screen.findByRole('textbox', { name: '退回停止理由' }), { target: { value: w.p.command.reason } });
  fireEvent.click(screen.getByRole('button', { name: '预览退回停止' }));
  await waitFor(() => expect(w.adapter.prepare).toHaveBeenCalledTimes(1));
  if (mode === 'identity') view.rerender(<FormalLossReturnStop {...w.props} identity={{ ...w.props.identity, authorization_version: w.p.authorization_version + 1 }} />);
  else fireEvent(window, new StorageEvent('storage'));
  resolve(w.p);
  await waitFor(() => expect((screen.getByRole('button', { name: '刷新退回停止状态' }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.queryByRole('button', { name: '确认停止退回' })).toBeNull(); expect(w.adapter.execute).not.toHaveBeenCalled();
});
it('removes stale information when refresh fails', async () => {
  const w = await world(); render(<FormalLossReturnStop {...w.props} />); await screen.findByRole('textbox', { name: '退回停止理由' });
  vi.mocked(w.adapter.read).mockRejectedValueOnce(new Error('当前查询失败'));
  fireEvent.click(screen.getByRole('button', { name: '刷新退回停止状态' })); await screen.findByText('当前查询失败');
  expect(screen.queryByRole('button', { name: '预览退回停止' })).toBeNull();
});
it.each(['wrong-line', 'wrong-quantity', 'storage-unavailable'])('%s blocks a new stop', async mode => {
  const w = await world();
  if (mode === 'wrong-line') w.setSource({ ...w.p.source, report_line_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' });
  if (mode === 'wrong-quantity') w.setSource({ ...w.p.source, quantity: '999.000' });
  if (mode === 'storage-unavailable') vi.spyOn(w.store, 'list').mockImplementation(() => { throw new Error('停止存储损坏'); });
  render(<FormalLossReturnStop {...w.props} />);
  if (mode === 'storage-unavailable') {
    fireEvent.change(await screen.findByRole('textbox', { name: '退回停止理由' }), { target: { value: 'Explicit test' } });
    expect((screen.getByRole('button', { name: '预览退回停止' }) as HTMLButtonElement).disabled).toBe(true);
  } else await screen.findByText('退回明细或当前身份无法对应，请重新核验');
  expect(w.adapter.prepare).not.toHaveBeenCalled(); expect(w.adapter.execute).not.toHaveBeenCalled();
});
