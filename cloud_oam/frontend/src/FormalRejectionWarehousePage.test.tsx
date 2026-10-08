// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalRejectionWarehousePage from './FormalRejectionWarehousePage';
import fixture from './test-fixtures/rejection-warehouse/quantity.json';
import serial from './test-fixtures/rejection-warehouse/serial.json';
import RejectionWarehouseReceiptForm from './RejectionWarehouseReceiptForm';
import { detail, preview, type Detail } from './rejectionWarehouse';
import { createStore, type Adapter, type Pending } from './rejectionWarehouseAdapter';
const other = '11111111-1111-4111-8111-111111111111';
beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function setup() {
  const identity = fixture.identity, store = createStore(localStorage);
  const current = detail(fixture.acceptance, identity, fixture.preview.return_id);
  current.receive_permitted = true; current.receipts.forEach(h => { h.post_permitted = h.receipt.amounts.accepted_qty !== '0.000'; });
  let state = current;
  const adapter: Adapter = {
    context: vi.fn(async () => ({ ...identity, can_read: true, can_write: true, authority_hash: 'c'.repeat(64) })),
    list: vi.fn(async () => ({ schema_version: '1.0' as const, ...identity, items: [{ return_id: current.source.return_id, verification_status: 'verified', message: '已核验', detail: state }], next_after_id: null })),
    read: vi.fn(async () => state), preview: vi.fn(async (value, receiptId) => preview(fixture.preview, identity, value, receiptId)),
    submit: vi.fn(async (p: Pending) => {
      expect(store.read()).toEqual({ kind: 'valid', value: p });
      if (p.command.kind === 'receipt') {
        const c = p.command.input, original = current.receipts.find(r => r.receipt.amounts.accepted_qty !== '0.000')!.receipt;
        state = { ...state, accepted_qty: '3.000', unconfirmed_qty: '0.000', pending_inbound_qty: '3.000', receive_permitted: false,
          receipts: [...state.receipts, { receipt: { ...original, receipt_id: other, amounts: c.amounts, reason: c.reason, request_hash: 'b'.repeat(64),
            received_at: c.received_at, recorded_at: c.received_at }, inbound: null, post_permitted: true }] };
      } else state = detail(fixture.posted, identity, fixture.preview.return_id);
    }),
    recover: vi.fn(async (p, saved, valid = () => true) => { if (!valid()) throw new Error('页面已离开'); saved.clear(p); return state; }),
  };
  return { identity, adapter, store, onBack: vi.fn(), current, setState(value: Detail) { state = value; } };
}
async function open() {
  fireEvent.click(screen.getByText('刷新本人仓库退回')); fireEvent.click(await screen.findByText('查看退回与验收'));
  await screen.findByRole('region', { name: '退回详情' });
}
async function prepareReceipt() {
  fireEvent.change(screen.getByLabelText('接受数量'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '实物逐件核验' } });
  fireEvent.change(screen.getByLabelText('本次接受实物物料码'), { target: { value: fixture.acceptance.source.sku_code } });
  fireEvent.click(screen.getByText('核验本次验收'));
  await screen.findByRole('region', { name: '确认仓库操作' });
}
it('shows native quantity partitions and accepts only an explicitly confirmed receipt', async () => {
  const w = setup(); render(<FormalRejectionWarehousePage {...w} />); await open(); await prepareReceipt();
  expect(w.adapter.submit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('确认提交本次验收')); await screen.findByText('本次验收已核验。接受的实物需另行确认入账。');
  expect(w.adapter.submit).toHaveBeenCalledTimes(1); expect(w.store.read().kind).toBe('missing');
  expect(vi.mocked(w.adapter.submit).mock.calls[0][0].command.kind).toBe('receipt');
  expect(within(screen.getByRole('region', { name: '本人仓库退回' })).getByText('待确认 0.000 · 待入账 3.000 · 已入账 0.000')).toBeTruthy();
});
it('independent inbound requires its own warehouse preview, reason and confirmation', async () => {
  const w = setup(); render(<FormalRejectionWarehousePage {...w} />); await open();
  fireEvent.click(screen.getByText('预览本次实物入账')); await screen.findByRole('region', { name: '独立入账预览' });
  expect(screen.getByText(/新品 1.000/)).toBeTruthy(); expect(screen.getByText(/坏件 1.000/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('入账说明'), { target: { value: '按实物分成色入账' } }); fireEvent.click(screen.getByText('核验入账内容'));
  expect(w.adapter.submit).not.toHaveBeenCalled(); fireEvent.click(screen.getByText('确认提交本次入账'));
  await screen.findByText('本次库存入账已核验。'); expect(screen.getByText('已独立入账')).toBeTruthy();
  const p = vi.mocked(w.adapter.submit).mock.calls[0][0];
  expect(p.command).toMatchObject({ kind: 'inbound', receipt_id: fixture.preview.receipt_id, input: { expected_plan_hash: fixture.preview.plan_hash } });
  expect(within(screen.getByRole('region', { name: '本人仓库退回' })).getByText('待确认 1.000 · 待入账 0.000 · 已入账 2.000')).toBeTruthy();
});
it('a lost response survives remount and recovers read-only without a second POST', async () => {
  const w = setup(); vi.mocked(w.adapter.submit).mockRejectedValueOnce(new Error('响应丢失'));
  const mounted = render(<FormalRejectionWarehousePage {...w} />); await open(); await prepareReceipt();
  fireEvent.click(screen.getByText('确认提交本次验收')); await screen.findByText('响应丢失');
  expect(w.store.read().kind).toBe('valid'); expect(w.adapter.recover).not.toHaveBeenCalled(); mounted.unmount();
  render(<FormalRejectionWarehousePage {...w} />); fireEvent.click(screen.getByText('只读核验原请求'));
  await screen.findByText('原请求及完整历史已核验，没有重新提交。');
  expect(w.adapter.submit).toHaveBeenCalledTimes(1); expect(w.adapter.recover).toHaveBeenCalledTimes(1);
});
it('does not post if persistence fails, and leaves an in-flight result on navigation', async () => {
  const w = setup(); vi.spyOn(w.store, 'persist').mockImplementation(() => { throw new Error('保存失败'); });
  render(<FormalRejectionWarehousePage {...w} />); await open(); await prepareReceipt(); fireEvent.click(screen.getByText('确认提交本次验收'));
  await screen.findByText('保存失败'); expect(w.adapter.submit).not.toHaveBeenCalled(); cleanup(); vi.restoreAllMocks();
  const next = setup(); let finish!: () => void;
  vi.mocked(next.adapter.submit).mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
  const view = render(<FormalRejectionWarehousePage {...next} />); await open(); await prepareReceipt(); fireEvent.click(screen.getByText('确认提交本次验收'));
  await waitFor(() => expect(next.adapter.submit).toHaveBeenCalledTimes(1)); view.unmount();
  await act(async () => { finish(); }); expect(next.store.read().kind).toBe('valid');
});
it('blocks over-receipt, wrong scanned SKU and another user original without submitting', async () => {
  const w = setup(); render(<FormalRejectionWarehousePage {...w} />); await open();
  fireEvent.change(screen.getByLabelText('接受数量'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '核对数量' } });
  fireEvent.change(screen.getByLabelText('本次接受实物物料码'), { target: { value: 'WRONG' } });
  fireEvent.click(screen.getByText('核验本次验收')); expect(await screen.findByRole('alert')).toBeTruthy(); expect(w.adapter.submit).not.toHaveBeenCalled();
  cleanup(); w.store.persist({ v: 1, ...w.identity, person_id: other, return_id: w.current.source.return_id, trace: 'original-trace', key: 'original-command-key',
    fingerprint: fixture.inbound_fingerprint, command: { kind: 'inbound', receipt_id: fixture.preview.receipt_id, input: fixture.inbound_command } });
  render(<FormalRejectionWarehousePage {...w} />); expect((screen.getByText('只读核验原请求') as HTMLButtonElement).disabled).toBe(true);
});
it('requires actual SKU, SN and QR entry for the exact remaining serial', async () => {
  const current = detail(serial.acceptance, serial.identity, serial.preview.return_id); current.receive_permitted = true;
  const onPrepare = vi.fn(), onError = vi.fn(), sn = current.unconfirmed_serials[0];
  render(<RejectionWarehouseReceiptForm current={current} disabled={false} onPrepare={onPrepare} onError={onError} />);
  fireEvent.change(screen.getByLabelText(`本次结果 ${sn.serial_no}`), { target: { value: 'accepted' } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '核对剩余实物序列号' } });
  fireEvent.change(screen.getByLabelText(`实物物料码 ${sn.serial_no}`), { target: { value: current.source.sku_code } });
  fireEvent.change(screen.getByLabelText(`实物 SN ${sn.serial_no}`), { target: { value: sn.serial_no } });
  fireEvent.change(screen.getByLabelText(`实物二维码 ${sn.serial_no}`), { target: { value: 'SYNTHETIC-PHYSICAL-SCAN' } });
  fireEvent.change(screen.getByLabelText('本次接受实物物料码'), { target: { value: current.source.sku_code } });
  fireEvent.click(screen.getByText('核验本次验收'));
  expect(onError).not.toHaveBeenCalled(); expect(onPrepare).toHaveBeenCalledTimes(1);
  expect(onPrepare.mock.calls[0][0].amounts.accepted_serial_verifications).toEqual([
    { serial_id: sn.serial_id, sku_code: current.source.sku_code, serial_no: sn.serial_no, qr_code: 'SYNTHETIC-PHYSICAL-SCAN' },
  ]);
});
