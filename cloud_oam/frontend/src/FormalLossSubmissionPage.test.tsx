// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalLossSubmissionPage from './FormalLossSubmissionPage';
import quantity from './test-fixtures/loss-submission/loss-submission-quantity-found.json';
import serial from './test-fixtures/loss-submission/loss-submission-serial-found.json';
import { input, pending, preview, requestHash, sources, type Input, type Pending } from './formalLossSubmission';
import { createStore } from './lossSubmissionRecovery';
import type { Adapter } from './lossSubmissionAdapter';
import type { FormalFileUploadClient } from './FormalFileUploadField';
beforeEach(async () => { const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>('node:crypto'); vi.stubGlobal('crypto', webcrypto); });
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
const now = () => new Date().toISOString();
function setup(f: typeof quantity | typeof serial = quantity) {
  const current = sources(f.sources, f.identity); let observed: unknown = f.missing;
  async function wire(body: Input) {
    return { ...f.preview, checked_at: now(), reason: body.reason, request_hash: await requestHash(body), evidence: f.preview.evidence.filter(e => body.evidence_file_ids.includes(e.file_id)),
      lines: body.lines.map(l => ({ source: current.items.find(s => s.stock_account_id === l.stock_account_id)!, selected_quantity: l.quantity, selected_serials: l.serial_verifications.map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no })) })) };
  }
  const store = createStore(localStorage, { async request(_n, _o, fn) { return fn({}); } });
  const uploader: FormalFileUploadClient = {
    prepare: vi.fn(async (file, purpose) => ({ file, purpose, original_filename: file.name, size_bytes: file.size, mime_type: file.type, sha256: 'a'.repeat(64), intent_headers: { 'X-Request-ID': 'upload-request', 'Idempotency-Key': 'upload-original-key' }, complete_headers: { 'X-Request-ID': 'upload-complete-request' } })),
    execute: vi.fn(async p => ({ file_id: f.preview.evidence[0].file_id, purpose: p.purpose, status: 'available' as const, sha256: p.sha256, size_bytes: p.size_bytes, mime_type: p.mime_type, verified_at: now() })),
  };
  const adapter: Adapter = {
    context: vi.fn(async () => ({ ...f.identity, can_read: true, can_write: true, authority_hash: 'c'.repeat(64) })),
    sources: vi.fn(async () => current), readSources: vi.fn(async () => current),
    serials: vi.fn(async (_current, accountId) => ({ stock_account_id: accountId, total_serials: Number(current.items[0].quantity), ledger_cursor: current.ledger_cursor, items: f.selection.lines[0].selected_serials.map(s => ({ ...s, lifecycle_status: 'active' as const })), next_after_id: null })),
    select: vi.fn(async () => { throw new Error('unused selection'); }),
    prepare: vi.fn(async body => ({ sources: current, preview: await preview(await wire(body), body, current) })),
    preview: vi.fn(async p => { const { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...body } = p.command; return wire(body); }),
    submit: vi.fn(async p => { observed = { lookup_status: 'found', retry_permitted: false, submission: { ...f.result, reason: p.command.reason, request_id: p.command.request_id, request_hash: p.preview.request_hash, plan_hash: p.preview.plan_hash, submitted_at: now(), lines: p.preview.lines, evidence: p.preview.evidence } }; return (observed as { submission: unknown }).submission; }),
    lookup: vi.fn(async () => observed),
    seal: vi.fn(async () => { throw new Error('unknown seal response'); }),
  };
  return { identity: f.identity, adapter, store, uploader, current };
}
async function evidence() {
  fireEvent.change(screen.getByLabelText('报损原因'), { target: { value: '实物检查发现损坏' } });
  fireEvent.change(screen.getByLabelText('上传报损凭证'), { target: { files: [new File(['proof'], '破损.jpg', { type: 'image/jpeg' })] } });
  await screen.findByText('状态：available（已完成严格确认）');
  await waitFor(() => expect((screen.getByText('核验并预览本次报损') as HTMLButtonElement).disabled).toBe(false));
}
async function fillQuantity(w: ReturnType<typeof setup>) {
  fireEvent.change(await screen.findByLabelText(`报损数量 ${w.current.items[0].sku_code}`), { target: { value: '1' } });
  await evidence(); fireEvent.click(screen.getByText('核验并预览本次报损'));
  await screen.findByRole('region', { name: '确认本次报损' });
}
const confirm = () => fireEvent.click(screen.getByLabelText('已核对本次物料、数量、SN、原因和凭证'));
it('requires uploaded evidence and separate confirmation before reporting a frozen submission', async () => {
  const w = setup(); render(<FormalLossSubmissionPage {...w} />); await fillQuantity(w);
  expect(w.adapter.submit).not.toHaveBeenCalled(); expect((screen.getByText('确认提交报损') as HTMLButtonElement).disabled).toBe(true);
  confirm(); fireEvent.click(screen.getByText('确认提交报损'));
  await screen.findByText(/已核验提交并冻结对应库存/); expect(w.adapter.submit).toHaveBeenCalledTimes(1); expect(w.store.list(w.identity.person_id)).toEqual([]);
});
it('lost submission survives remount and only looks up its original request', async () => {
  const w = setup(); w.adapter.submit = vi.fn(async () => { throw new Error('回执丢失'); });
  const view = render(<FormalLossSubmissionPage {...w} />); await fillQuantity(w); confirm(); fireEvent.click(screen.getByText('确认提交报损'));
  await screen.findByText('回执丢失'); const original = w.store.list(w.identity.person_id)[0]; expect(original).toBeTruthy();
  view.unmount(); render(<FormalLossSubmissionPage {...w} />);
  const button = await screen.findByText('回查原报损请求'); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button); await screen.findByText(/尚未观察到原报损结果/);
  expect(w.adapter.submit).toHaveBeenCalledTimes(1); expect(w.store.list(w.identity.person_id)).toEqual([original]); expect(screen.queryByText('核验并预览本次报损')).toBeNull();
});
it('SN selection never autofills physical SKU, SN or QR proof', async () => {
  const w = setup(serial); render(<FormalLossSubmissionPage {...w} />);
  const load = await screen.findByText('查询可选 SN'); fireEvent.click(load);
  const proof = serial.command.lines[0].serial_verifications[0]; fireEvent.click(await screen.findByLabelText(`选择 SN ${proof.serial_no}`));
  for (const [label, value] of [['实物物料码', proof.sku_code], ['实物 SN', proof.serial_no], ['实物二维码', proof.qr_code]]) {
    const field = screen.getByLabelText(`${label} ${proof.serial_no}`) as HTMLInputElement; expect(field.value).toBe(''); fireEvent.change(field, { target: { value } });
  }
  await evidence(); fireEvent.click(screen.getByText('核验并预览本次报损')); await screen.findByRole('region', { name: '确认本次报损' });
  expect(screen.getByText(`本次 SN：${proof.serial_no}`)).toBeTruthy(); confirm(); fireEvent.click(screen.getByText('确认提交报损'));
  await screen.findByText(/已核验提交并冻结对应库存/); expect(vi.mocked(w.adapter.submit).mock.calls[0][0].command.lines[0].serial_verifications).toEqual([proof]);
});
it('failed source reads are unknown rather than an empty warehouse', async () => {
  const w = setup(); w.adapter.readSources = vi.fn(async () => { throw new Error('库存读取超时'); });
  render(<FormalLossSubmissionPage {...w} />); await screen.findByText('库存读取超时');
  expect(screen.getByText(/本人可用库存待核验/)).toBeTruthy(); expect(screen.queryByText(/没有可用于报损/)).toBeNull(); expect(screen.queryByText('核验并预览本次报损')).toBeNull();
});
it('requires real upload completion and refuses a missing evidence file', async () => {
  const w = setup(); render(<FormalLossSubmissionPage {...w} />);
  fireEvent.change(await screen.findByLabelText(`报损数量 ${w.current.items[0].sku_code}`), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('报损原因'), { target: { value: '检查损坏' } }); fireEvent.submit(screen.getByRole('form', { name: '填写本人报损' }));
  await screen.findByText('请先上传并核验报损凭证'); expect(w.adapter.prepare).not.toHaveBeenCalled(); expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('read-only recovery works without loading write-protected loss sources', async () => {
  const w = setup(), p = pending({ v: 1, ...quantity.identity, sources: quantity.sources, command: quantity.command, preview: quantity.preview });
  await w.store.withLease(p.person_id, p.sources.location_id, async lease => lease.persist(p));
  w.adapter.context = vi.fn(async () => ({ ...w.identity, can_read: true, can_write: false, authority_hash: 'c'.repeat(64) }));
  render(<FormalLossSubmissionPage {...w} />); const button = await screen.findByText('回查原报损请求'); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByText('永久封存原报损请求') as HTMLButtonElement).disabled).toBe(true); fireEvent.click(button);
  await screen.findByText(/尚未观察到原报损结果/); expect(w.adapter.readSources).not.toHaveBeenCalled(); expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('corrupt local evidence blocks all new submissions', async () => {
  const w = setup(); localStorage.setItem(`cloud-oam-loss-submission-v1:${w.identity.person_id}:${w.current.location_id}`, '{bad');
  render(<FormalLossSubmissionPage {...w} />); await screen.findByText(/本机原报损请求不可读/);
  await screen.findByLabelText(`报损数量 ${w.current.items[0].sku_code}`);
  expect((screen.getByText('核验并预览本次报损') as HTMLButtonElement).disabled).toBe(true); expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('retains selected physical proofs when a subsequent SN query fails', async () => {
  const w = setup(serial), proof = serial.command.lines[0].serial_verifications[0];
  render(<FormalLossSubmissionPage {...w} />); fireEvent.click(await screen.findByText('查询可选 SN'));
  fireEvent.click(await screen.findByLabelText(`选择 SN ${proof.serial_no}`));
  fireEvent.change(screen.getByLabelText(`实物二维码 ${proof.serial_no}`), { target: { value: proof.qr_code } });
  w.adapter.serials = vi.fn(async () => { throw new Error('SN 查询超时'); });
  fireEvent.click(screen.getByText('查询可选 SN')); await screen.findByText('SN 待核验：SN 查询超时');
  expect((screen.getByLabelText(`实物二维码 ${proof.serial_no}`) as HTMLInputElement).value).toBe(proof.qr_code);
  expect(screen.queryByText('本次查询未找到匹配 SN。')).toBeNull(); expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('does not preview or submit while evidence upload is unresolved', async () => {
  const w = setup(); vi.mocked(w.uploader.execute).mockImplementation(() => new Promise(() => {}));
  render(<FormalLossSubmissionPage {...w} />);
  fireEvent.change(await screen.findByLabelText(`报损数量 ${w.current.items[0].sku_code}`), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('报损原因'), { target: { value: '损坏' } });
  fireEvent.change(screen.getByLabelText('上传报损凭证'), { target: { files: [new File(['proof'], 'proof.jpg', { type: 'image/jpeg' })] } });
  await screen.findByText('状态：上传并核验');
  expect((screen.getByText('核验并预览本次报损') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole('form', { name: '填写本人报损' }));
  expect(w.adapter.prepare).not.toHaveBeenCalled(); expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('requires separate seal confirmation and retains evidence on a lost seal response', async () => {
  const w = setup(), p = pending({ v: 1, ...quantity.identity, sources: quantity.sources, command: quantity.command, preview: quantity.preview });
  await w.store.withLease(p.person_id, p.sources.location_id, async lease => lease.persist(p));
  render(<FormalLossSubmissionPage {...w} />);
  const button = await screen.findByText('永久封存原报损请求'); await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button); await screen.findByRole('alertdialog', { name: '确认封存原报损请求' });
  expect(w.adapter.seal).not.toHaveBeenCalled(); fireEvent.click(screen.getByText('取消封存'));
  expect(w.adapter.seal).not.toHaveBeenCalled(); fireEvent.click(button); fireEvent.click(screen.getByText('确认永久封存'));
  await screen.findByText('unknown seal response');
  expect(w.adapter.lookup).toHaveBeenCalledTimes(1); expect(w.adapter.seal).toHaveBeenCalledTimes(1);
  expect(w.adapter.submit).not.toHaveBeenCalled(); expect(w.store.list(w.identity.person_id)).toEqual([p]);
});
it('blocks repeated confirmation while the first submission is unresolved', async () => {
  const w = setup(); w.adapter.submit = vi.fn(() => new Promise(() => {}));
  render(<FormalLossSubmissionPage {...w} />); await fillQuantity(w); confirm();
  const button = screen.getByText('确认提交报损'); fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(w.adapter.submit).toHaveBeenCalledTimes(1));
  expect((button as HTMLButtonElement).disabled).toBe(true); expect(w.store.list(w.identity.person_id)).toHaveLength(1);
});
