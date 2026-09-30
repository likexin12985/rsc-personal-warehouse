// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalReturnReceiving from './FormalReturnReceivingPage';
import ReturnReceiptForm from './ReturnReceiptForm';
import quantity from './test-fixtures/return-receiving/loss-receiving-quantity.json';
import serial from './test-fixtures/return-receiving/loss-receiving-serial.json';
import { history, type History } from './formalReturnReceiving';
import { requestHash } from './formalReturnReceipt';
import { createStore, pending, type Pending } from './returnReceivingRecovery';
import type { Adapter } from './returnReceivingAdapter';
import type { FormalFileUploadClient } from './FormalFileUploadField';
const other = '11111111-1111-4111-8111-111111111111';
const now = () => new Date().toISOString();
beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
function setup(f: typeof quantity | typeof serial = quantity) {
  const identity = f.identity, before = history(f.before, identity, f.before.package.shipment_id);
  let current = before, observed: unknown, posted = false;
  const store = createStore(localStorage, { async request(_n, _o, work) { return work({}); } });
  const upload: FormalFileUploadClient = { prepare: vi.fn(async (file, purpose) => ({ file, purpose, original_filename: file.name, size_bytes: file.size, mime_type: file.type, sha256: 'a'.repeat(64), intent_headers: { 'X-Request-ID': 'upload-request', 'Idempotency-Key': 'upload-original-key' }, complete_headers: { 'X-Request-ID': 'upload-complete-request' } })), execute: vi.fn(async p => ({ file_id: other, purpose: p.purpose, status: 'available' as const, sha256: p.sha256, size_bytes: p.size_bytes, mime_type: p.mime_type, verified_at: now() })) };
  const adapter: Adapter = {
    context: vi.fn(async () => ({ ...identity, can_read: true, can_write: true, authority_hash: 'c'.repeat(64) })),
    list: vi.fn(async () => ({ ...f.directory, schema_version: '1.0' as const, items: [before.package], next_after_id: null })),
    read: vi.fn(async () => current), history: vi.fn(async () => current),
    inboundState: vi.fn(async (_h, receiptId) => ({ ...(posted ? f.inbound.after : f.inbound.before), receipt_id: receiptId, checked_at: now(), ...(posted ? { inbound: { ...f.inbound.after.inbound, posted_at: now() } } : {}) }) as ReturnType<typeof import('./formalReturnInbound').state>),
    state: vi.fn(async () => ({ ...f.inbound.before, checked_at: now() })),
    preview: vi.fn(async (p: Pending) => {
      if (p.kind === 'inbound') return { ...f.inbound.preview, receipt_plan_hash: p.receipt.plan_hash, reason: p.receipt.reason, checked_at: now() };
      const { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...body } = p.command;
      return { ...f.preview, ...body, request_hash: await requestHash(p.package.shipment_id, body), checked_at: now(), lines: body.lines.map(input => {
        const line = f.preview.lines.find(row => row.shipment_line_id === input.shipment_line_id)!;
        return { ...line, accepted_qty: input.accepted_qty, rejected_qty: input.rejected_qty, shortage_qty: input.shortage_qty, damaged_qty: input.damaged_qty,
          accepted_serials: input.accepted_serial_verifications.map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no })), damaged_serial_ids: input.damaged_serial_ids,
          rejected_serials: input.rejected_serial_ids.map(id => p.package.lines.flatMap(l => l.serials).find(s => s.serial_id === id)), shortage_serials: input.shortage_serial_ids.map(id => p.package.lines.flatMap(l => l.serials).find(s => s.serial_id === id)), exceptions: input.exceptions };
      }) };
    }),
    submit: vi.fn(async (p: Pending) => {
      if (p.kind === 'inbound') { posted = true; observed = { ...f.inbound.posted, request_id: p.original.command.request_id, request_hash: p.original.request_hash, plan_hash: p.original.command.expected_plan_hash }; return observed; }
      const view = await adapter.preview(p) as typeof f.preview;
      const row = { ...f.receipt, received_at: p.command.received_at, recorded_at: now(), reason: p.command.reason, request_id: p.command.request_id, request_hash: view.request_hash, plan_hash: p.command.expected_plan_hash, lines: view.lines };
      current = history({ ...f.after, receipts: [row], queried_at: now() }, identity, before.package.shipment_id); observed = current.receipts[0]; return observed;
    }),
    lookup: vi.fn(async () => observed ? { observed: true as const, value: observed } : { observed: false as const }),
    seal: vi.fn(async () => { throw new Error('封存回执丢失'); }),
  };
  return { identity, adapter, store, upload, before, setCurrent(h: History) { current = h; }, setPosted(p: boolean) { posted = p; } };
}
async function fillQuantity(w: ReturnType<typeof setup>) {
  fireEvent.click(await screen.findByText('查看包裹'));
  fireEvent.change(await screen.findByLabelText(`接受数量 ${w.before.package.lines[0].sku_code}`), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '逐件核对本次实物' } });
  fireEvent.click(screen.getByText('核验并预览本次验收'));
  await screen.findByRole('region', { name: '确认本次操作' });
}
function confirm() { fireEvent.click(screen.getByLabelText('已核对本次物料、数量、SN 和目标仓')); }
it('quantity acceptance and independent inbound require separate previews and confirmations', async () => {
  const w = setup(); render(<FormalReturnReceiving {...w} uploader={w.upload} />); await fillQuantity(w);
  expect(w.adapter.submit).not.toHaveBeenCalled(); confirm(); fireEvent.click(screen.getByText('确认提交验收'));
  await screen.findByText('本次实物验收已核验，仍需单独入库。'); await screen.findByText('库存入库：尚未入库');
  expect(w.store.list(w.identity.person_id)).toEqual([]); expect(w.adapter.submit).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText(`预览入库 ${quantity.receipt.receipt_no}`)); await screen.findByRole('region', { name: '确认本次操作' });
  expect(w.adapter.submit).toHaveBeenCalledTimes(1); confirm(); fireEvent.click(screen.getByRole('button', { name: '确认独立入库' }));
  await screen.findByText('独立入库已核验，库存交易已过账。'); await screen.findByText('库存入库：已过账'); expect(w.adapter.submit).toHaveBeenCalledTimes(2);
});
it('lost acceptance response survives re-entry and does not automatically replay', async () => {
  const w = setup(); w.adapter.submit = vi.fn(async () => { throw new Error('网络回执丢失'); });
  const page = render(<FormalReturnReceiving {...w} uploader={w.upload} />); await fillQuantity(w); confirm(); fireEvent.click(screen.getByText('确认提交验收'));
  await screen.findByText('网络回执丢失'); await screen.findByRole('region', { name: '原请求待核验' }); const saved = w.store.list(w.identity.person_id)[0];
  page.unmount(); render(<FormalReturnReceiving {...w} uploader={w.upload} />);
  const button = await screen.findByText('回查原请求'); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button);
  await screen.findByText('尚未观察到原请求结果。已保留请求，请继续回查，勿重新提交。'); expect(w.adapter.submit).toHaveBeenCalledTimes(1); expect(w.store.list(w.identity.person_id)).toEqual([saved]);
});
it('SN proof fields start empty and use actual scanned contents rather than display values', async () => {
  const w = setup(serial); render(<FormalReturnReceiving {...w} uploader={w.upload} />);
  fireEvent.click(await screen.findByText('查看包裹')); const proof = serial.command.lines[0].accepted_serial_verifications[0];
  fireEvent.change(await screen.findByLabelText(`本次结果 ${proof.serial_no}`), { target: { value: 'accepted' } });
  expect((screen.getByLabelText(`实物物料码 ${proof.serial_no}`) as HTMLInputElement).value).toBe('');
  expect((screen.getByLabelText(`实物二维码 ${proof.serial_no}`) as HTMLInputElement).value).toBe('');
  for (const [label, value] of [['实物物料码', proof.sku_code], ['实物 SN', proof.serial_no], ['实物二维码', proof.qr_code]]) fireEvent.change(screen.getByLabelText(`${label} ${proof.serial_no}`), { target: { value } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '扫码逐件核对' } }); fireEvent.click(screen.getByText('核验并预览本次验收'));
  await screen.findByRole('region', { name: '确认本次操作' }); confirm(); fireEvent.click(screen.getByText('确认提交验收')); await screen.findByText('本次实物验收已核验，仍需单独入库。');
  const p = vi.mocked(w.adapter.submit).mock.calls[0][0]; expect(p.kind).toBe('receipt'); if (p.kind === 'receipt') expect(p.command.lines[0].accepted_serial_verifications).toEqual([proof]);
});
it('abnormal receipt cannot prepare with an arbitrary or unconfirmed evidence ID', async () => {
  const w = setup(), prepare = vi.fn(), error = vi.fn(); render(<ReturnReceiptForm current={w.before} disabled={false} uploader={w.upload} onPrepare={prepare} onError={error} />);
  const sku = w.before.package.lines[0].sku_code;
  fireEvent.change(screen.getByLabelText(`短少数量 ${sku}`), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '实物短少' } }); fireEvent.change(screen.getByLabelText(`短少说明 ${sku}`), { target: { value: '包裹为空' } });
  fireEvent.submit(screen.getByRole('form', { name: '填写本次验收' })); expect(prepare).not.toHaveBeenCalled(); expect(error).toHaveBeenCalledWith(expect.stringContaining('上传'));
  fireEvent.change(screen.getByLabelText(`上传短少凭证 ${sku}`), { target: { files: [new File(['test'], '空包.jpg', { type: 'image/jpeg' })] } });
  await screen.findByText('状态：available（已完成严格确认）'); await waitFor(() => expect((screen.getByText('核验并预览本次验收') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.submit(screen.getByRole('form', { name: '填写本次验收' })); expect(prepare).toHaveBeenCalledTimes(1); expect(prepare.mock.calls[0][0].lines[0].exceptions[0]).toEqual({ exception_type: 'shortage', description: '包裹为空', evidence_file_id: other });
});
it('read-only receivers can recover but cannot prepare new writes or seal', async () => {
  const w = setup(); const p = pending({ v: 1, kind: 'receipt', ...w.identity, package: w.before.package, command: quantity.command });
  await w.store.withLease(p.person_id, p.package.shipment_id, async lease => lease.persist(p));
  w.adapter.context = vi.fn(async () => ({ ...w.identity, can_read: true, can_write: false, authority_hash: 'c'.repeat(64) }));
  render(<FormalReturnReceiving {...w} uploader={w.upload} />); const recover = await screen.findByText('回查原请求'); await waitFor(() => expect((recover as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByText('永久封存原请求') as HTMLButtonElement).disabled).toBe(true); fireEvent.click(screen.getByText('查看包裹')); await screen.findByText('当前为只读，可查看并回查已有原请求。'); expect(screen.queryByText('核验并预览本次验收')).toBeNull();
});
it('sealing is explicit and unknown sealing keeps the original', async () => {
  const w = setup(), p = pending({ v: 1, kind: 'receipt', ...w.identity, package: w.before.package, command: quantity.command });
  await w.store.withLease(p.person_id, p.package.shipment_id, async lease => lease.persist(p)); render(<FormalReturnReceiving {...w} uploader={w.upload} />);
  const button = await screen.findByText('永久封存原请求'); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button); expect(w.adapter.seal).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole('alertdialog')).getByText('确认永久封存')); await screen.findByText('封存回执丢失'); expect(w.store.list(p.person_id)).toEqual([p]);
});

it.each([['rejected', '拒收', '拒收 SN'], ['shortage', '短少', '短少 SN'], ['damaged', '破损', '接受中破损 SN']] as const)('confirmation identifies the exact %s serial before any write', async (choice, label, summary) => {
  const w = setup(serial); render(<FormalReturnReceiving {...w} uploader={w.upload} />);
  fireEvent.click(await screen.findByText('查看包裹'));
  const proof = serial.command.lines[0].accepted_serial_verifications[0];
  fireEvent.change(await screen.findByLabelText(`本次结果 ${proof.serial_no}`), { target: { value: choice === 'damaged' ? 'accepted' : choice } });
  if (choice === 'damaged') {
    for (const [name, value] of [['实物物料码', proof.sku_code], ['实物 SN', proof.serial_no], ['实物二维码', proof.qr_code]]) fireEvent.change(screen.getByLabelText(`${name} ${proof.serial_no}`), { target: { value } });
    fireEvent.click(screen.getByLabelText('该接受件有破损'));
  }
  fireEvent.change(screen.getByLabelText('验收说明'), { target: { value: '核对异常实物' } });
  fireEvent.change(screen.getByLabelText(`${label}说明 ${proof.sku_code}`), { target: { value: '已按包裹逐件核对' } });
  fireEvent.change(screen.getByLabelText(`上传${label}凭证 ${proof.sku_code}`), { target: { files: [new File(['test'], '验收.jpg', { type: 'image/jpeg' })] } });
  await screen.findByText('状态：available（已完成严格确认）');
  await waitFor(() => expect((screen.getByText('核验并预览本次验收') as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText('核验并预览本次验收'));
  const region = await screen.findByRole('region', { name: '确认本次操作' });
  expect(within(region).getByText(`${summary}：${proof.serial_no}`)).toBeTruthy();
  expect((within(region).getByText('确认提交验收') as HTMLButtonElement).disabled).toBe(true);
  expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('independent inbound confirmation lists accepted serials before posting', async () => {
  const w = setup(serial); w.setCurrent(history(serial.after, w.identity, serial.before.package.shipment_id));
  render(<FormalReturnReceiving {...w} uploader={w.upload} />);
  fireEvent.click(await screen.findByText('查看包裹'));
  fireEvent.click(await screen.findByText(`预览入库 ${serial.receipt.receipt_no}`));
  const region = await screen.findByRole('region', { name: '确认本次操作' });
  expect(within(region).getByText(`本次入库 SN：${serial.command.lines[0].accepted_serial_verifications[0].serial_no}`)).toBeTruthy();
  expect((within(region).getByRole('button', { name: '确认独立入库' }) as HTMLButtonElement).disabled).toBe(true);
  expect(w.adapter.submit).not.toHaveBeenCalled();
});
