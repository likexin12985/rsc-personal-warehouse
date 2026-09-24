// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiNoReplay, ApiError } from '../api';
import Actions from './DailyReviewActions';
import { dailyAccess, dailyMarker, dailyPrepared, dailyReceipt, dailyRecovery, dailyStore, DailyMemoryStorage } from '../__fixtures__/dailyReview';
import { dailyDetail, dailyId, dailyItem, otherDailyId } from '../__fixtures__/dailyReconciliation';
import type { DailyReference } from '../dailyReviewProtocol';
import type { AvailableFormalFile } from '../FormalFileUploadField';
const callbacks = vi.hoisted(() => new Map<string, () => void>());
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), apiNoReplay: vi.fn(),
  subscribeAuthenticationTerminalLogout: (fn: () => void) => { callbacks.set('logout', fn); return () => callbacks.delete('logout'); },
  subscribeAuthenticationEstablished: (fn: () => void) => { callbacks.set('login', fn); return () => callbacks.delete('login'); },
}));
vi.mock('../FormalFileUploadField', () => ({ default: (props: { purpose: string; label: string; disabled: boolean;
  onBlockingChange: (v: boolean) => void; onAvailableChange: (v: AvailableFormalFile[]) => void }) => <>
  <button disabled={props.disabled} onClick={() => props.onBlockingChange(true)}>开始{props.label}</button>
  <button disabled={props.disabled} onClick={() => { props.onAvailableChange([{ file_id: dailyId, purpose: props.purpose as 'daily_reconciliation_evidence', status: 'available',
    verified_at: '2026-09-21T01:00:00Z', sha256: 'a'.repeat(64), size_bytes: 10, mime_type: 'image/png', original_filename: '证据.png' }]); props.onBlockingChange(false); }}>完成{props.label}</button>
  </> }));
beforeEach(() => { vi.mocked(apiNoReplay).mockReset(); }); afterEach(cleanup);
function props() { return { access: dailyAccess(), detail: dailyDetail(), items: [dailyItem()] as never[], selected: dailyId, readBusy: false, onChanged: vi.fn(), store: dailyStore() }; }
function transport(access = dailyAccess(), failed = false) {
  let reference: DailyReference;
  vi.mocked(apiNoReplay).mockImplementation(async (path, init) => {
    if (path === '/access/context') return access;
    const body = JSON.parse(init!.body as string);
    if (path.endsWith('/commands')) {
      const c = body.command; reference = { cutoff_id: c.cutoff_id, operation: c.operation, original_review_version: c.expected_version,
        original_authorization_version: body.expected_authorization_version, actor_person_id: access.person_id, trace_request_id: c.request_id };
      if (failed) throw new ApiError(503, 'unknown');
      return { receipt: dailyReceipt(reference) };
    }
    reference = body.reference;
    return dailyRecovery(reference, failed ? 'not_observed' : 'found');
  });
}
it('opens an existing cutoff and refreshes only after the exact receipt is recovered', async () => {
  const p = props(); p.detail = { ...p.detail, review_version: 0, review_status: 'not_recorded', review_updated_at: null, allowed_actions: ['open'] } as never;
  transport(); render(<Actions {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: '开启审核' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalledWith(dailyId));
  expect(await screen.findByRole('status')).toHaveProperty('textContent', expect.stringContaining('原提交已确认'));
  expect(p.store.read(dailyId).kind).toBe('missing');
});
it('requires a completed purpose-bound file before sending a partial explanation', async () => {
  const p = props(); transport(); render(<Actions {...p} />);
  fireEvent.click(await screen.findByLabelText('选择差异 1'));
  fireEvent.change(screen.getByLabelText('第 1 项解释'), { target: { value: '盘点口径差异需要说明' } });
  expect(screen.getByRole('button', { name: '提交本页已选差异解释' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '开始第 1 项证据' }));
  expect(screen.getByRole('button', { name: '提交本页已选差异解释' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '完成第 1 项证据' }));
  expect(await screen.findByText('已核验：证据.png')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '提交本页已选差异解释' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalled());
  const call = vi.mocked(apiNoReplay).mock.calls.find(([path]) => path.endsWith('/commands'))!;
  expect(JSON.parse(call[1]!.body as string).command.items).toEqual([{ ordinal: 1, expected_item_version: 1,
    explanation: '盘点口径差异需要说明', evidence_file_id: dailyId, evidence_sha256: 'a'.repeat(64) }]);
});
it('does not show one cutoff receipt as the result for another selected cutoff', async () => {
  const p = props(); p.detail = { ...p.detail, review_version: 0, review_status: 'not_recorded', review_updated_at: null, allowed_actions: ['open'] } as never;
  transport(); const { rerender } = render(<Actions {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: '开启审核' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalledWith(dailyId));
  expect(screen.getByRole('status').textContent).toContain('原提交已确认');
  rerender(<Actions {...p} selected={otherDailyId} detail={{ ...p.detail, cutoff_id: otherDailyId }} />);
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.getByRole('button', { name: '开启审核' })).toHaveProperty('disabled', false);
  rerender(<Actions {...p} />);
  expect(screen.getByRole('status').textContent).toContain('原提交已确认');
});
it.each(['approve', 'request_changes'])('submits independent headquarters %s with an explicit comment', async operation => {
  const p = props(); p.access = dailyAccess('admin'); transport(p.access); render(<Actions {...p} />);
  fireEvent.change(await screen.findByLabelText('总部审核说明'), { target: { value: '总部逐项核验证据' } });
  if (operation === 'request_changes') fireEvent.click(screen.getByLabelText('选择差异 1'));
  fireEvent.click(screen.getByRole('button', { name: operation === 'approve' ? '确认总部审核' : '退回本页已选差异补证' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalled());
  const body = JSON.parse(vi.mocked(apiNoReplay).mock.calls.find(([p]) => p.endsWith('/commands'))![1]!.body as string);
  expect(body.command.operation).toBe(operation); expect(body.command.ordinals).toEqual(operation === 'approve' ? [] : [1]);
});
it('retains drafts after unknown results and after refresh/version changes', async () => {
  const p = props(); p.access = dailyAccess('admin'); transport(p.access, true);
  const { rerender } = render(<Actions {...p} />);
  fireEvent.change(await screen.findByLabelText('总部审核说明'), { target: { value: '保留原来的审核说明' } });
  fireEvent.click(screen.getByRole('button', { name: '确认总部审核' }));
  expect(await screen.findByText(/操作未确认/)).toBeTruthy();
  expect(p.store.read(dailyId).kind).toBe('valid');
  rerender(<Actions {...p} detail={undefined} readBusy />);
  rerender(<Actions {...p} detail={{ ...p.detail, review_version: 2 }} />);
  expect(await screen.findByLabelText('总部审核说明')).toHaveProperty('value', '保留原来的审核说明');
  expect(screen.getByText(/审核版本已变化/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '确认总部审核' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '查询原提交结果' }));
  expect(await screen.findByText(/暂未查到原提交结果/)).toBeTruthy();
  expect(p.store.read(dailyId).kind).toBe('valid');
});
it('restores an unknown request after remount and requires explicit confirmation before sealing it', async () => {
  const storage = new DailyMemoryStorage(), marker = dailyMarker(), p = props(); p.store = dailyStore(storage);
  await p.store.withCutoffLease(dailyId, async l => l.persist(marker));
  const { v, kind, ...ref } = marker;
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? p.access : dailyRecovery(ref, 'sealed'));
  const first = render(<Actions {...p} />); first.unmount(); render(<Actions {...p} store={dailyStore(storage)} />);
  const button = await screen.findByRole('button', { name: '终结原请求' }); expect(button).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByLabelText('确认终结原请求；已提交的操作仍保留原回执')); fireEvent.click(button);
  expect(await screen.findByText(/原请求已永久终结/)).toBeTruthy(); expect(p.store.read(dailyId).kind).toBe('missing');
});
it('refuses another person and external-region writes without discarding existing markers', async () => {
  const p = props(), marker = dailyMarker({ ...dailyPrepared().reference, actor_person_id: dailyId });
  await p.store.withCutoffLease(dailyId, async l => l.persist(marker));
  p.access.assignments[0].scope_id = otherDailyId;
  render(<Actions {...p} />); expect(await screen.findByText(/属于其他人员/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '查询原提交结果' })).toBeNull();
  expect(screen.queryByRole('button', { name: '提交本页已选差异解释' })).toBeNull();
  expect(p.store.read(dailyId).kind).toBe('valid'); expect(apiNoReplay).not.toHaveBeenCalled();
});
it.each(['login', 'logout'])('drops draft contents and ignores late results on %s', async event => {
  const p = props(); p.access = dailyAccess('admin'); transport(p.access);
  let resolve!: (value: unknown) => void;
  vi.mocked(apiNoReplay).mockImplementation(async path => path === '/access/context' ? p.access : new Promise(r => resolve = r));
  render(<Actions {...p} />);
  fireEvent.change(await screen.findByLabelText('总部审核说明'), { target: { value: '不会跨人员保留的正文' } });
  fireEvent.click(screen.getByRole('button', { name: '确认总部审核' }));
  await waitFor(() => expect(p.store.read(dailyId).kind).toBe('valid'));
  act(() => callbacks.get(event)!());
  await act(async () => resolve({ receipt: dailyReceipt(dailyPrepared().reference) }));
  expect(screen.queryByDisplayValue('不会跨人员保留的正文')).toBeNull();
  expect(p.onChanged).not.toHaveBeenCalled(); expect(p.store.read(dailyId).kind).toBe('valid');
});
it('keeps explanation and completed evidence in memory across detail reload', async () => {
  const p = props(), { rerender } = render(<Actions {...p} />);
  fireEvent.click(await screen.findByLabelText('选择差异 1'));
  fireEvent.change(screen.getByLabelText('第 1 项解释'), { target: { value: '刷新仍保留的解释正文' } });
  fireEvent.click(screen.getByRole('button', { name: '完成第 1 项证据' }));
  rerender(<Actions {...p} detail={undefined} readBusy />); rerender(<Actions {...p} />);
  expect(await screen.findByLabelText('第 1 项解释')).toHaveProperty('value', '刷新仍保留的解释正文');
  expect(screen.getByText('已核验：证据.png')).toBeTruthy();
});
it.each(['missing_actions', 'missing_context', 'wrong_person', 'wrong_authorization', 'no_actions'])('fails closed for %s without hiding original-request recovery', async reason => {
  const p = props(); p.access = dailyAccess('admin');
  if (reason === 'missing_actions') delete (p.detail as Partial<typeof p.detail>).allowed_actions;
  if (reason === 'missing_context') delete (p.detail as Partial<typeof p.detail>).action_context;
  if (reason === 'wrong_person') p.detail.action_context.person_id = dailyId;
  if (reason === 'wrong_authorization') p.detail.action_context.authorization_version = 2;
  if (reason === 'no_actions') p.detail.allowed_actions = [];
  const marker = dailyMarker(); await p.store.withCutoffLease(dailyId, async lease => lease.persist(marker));
  transport(p.access, true); render(<Actions {...p} />);
  expect(await screen.findByText('当前没有可执行的审核操作。')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '确认总部审核' })).toBeNull();
  expect(screen.queryByRole('button', { name: '退回本页已选差异补证' })).toBeNull();
  expect(screen.queryByLabelText('总部审核说明')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '查询原提交结果' }));
  expect(await screen.findByText(/暂未查到原提交结果/)).toBeTruthy();
  expect(p.store.read(dailyId).kind).toBe('valid');
  expect(vi.mocked(apiNoReplay).mock.calls.some(([path]) => path.endsWith('/commands'))).toBe(false);
});
it.each(['account_status', 'employment_status', 'access_mode'] as const)('does not offer fresh writes when %s is inactive', async field => {
  const p = props(); p.access = { ...dailyAccess('admin'), [field]: 'inactive' } as typeof p.access;
  render(<Actions {...p} />);
  expect(await screen.findByText('当前没有可执行的审核操作。')).toBeTruthy();
  expect(screen.queryByLabelText('总部审核说明')).toBeNull(); expect(apiNoReplay).not.toHaveBeenCalled();
});
it('requires the existing permission and matching scope even when the server lists an action', async () => {
  const p = props(); p.access = dailyAccess('admin'); p.access.permissions = p.access.permissions.filter(v => v.action !== 'approve_daily');
  const { rerender } = render(<Actions {...p} />);
  expect(await screen.findByText('当前没有可执行的审核操作。')).toBeTruthy();
  p.access = dailyAccess('admin'); p.access.assignments[0].scope_type = 'organization'; p.access.assignments[0].scope_id = dailyId;
  rerender(<Actions {...p} />);
  expect(screen.queryByLabelText('总部审核说明')).toBeNull(); expect(apiNoReplay).not.toHaveBeenCalled();
});
it('shows only the allowed headquarters action', async () => {
  const p = props(); p.access = dailyAccess('admin'); p.detail.allowed_actions = ['approve'];
  render(<Actions {...p} />);
  expect(await screen.findByRole('button', { name: '确认总部审核' })).toBeTruthy();
  expect(screen.queryByRole('button', { name: '退回本页已选差异补证' })).toBeNull();
  expect(screen.queryByLabelText('选择差异 1')).toBeNull();
});
it('can return explained differences while remaining explanations still prevent approval', async () => {
  const p = props(); p.access = dailyAccess('admin');
  p.detail = { ...p.detail, review_status: 'awaiting_explanations', allowed_actions: ['request_changes'], item_count: 4 } as never;
  const row = dailyItem();
  p.items = [row,
    { ...row, ordinal: 2, review: { ...row.review, ordinal: 2, explanation: '', evidence: null, explained_by_person_id: null } },
    { ...row, ordinal: 3, review: { ...row.review, ordinal: 3, revision_requested: true } },
    { ...row, ordinal: 4, status: 'matched', review: { ...row.review, ordinal: 4 } }] as never[];
  transport(p.access); render(<Actions {...p} />);
  fireEvent.change(await screen.findByLabelText('总部审核说明'), { target: { value: '此项证据不足请补证' } });
  expect(screen.queryByRole('button', { name: '确认总部审核' })).toBeNull();
  expect(screen.getByLabelText('选择差异 2')).toHaveProperty('disabled', true);
  expect(screen.getByLabelText('选择差异 3')).toHaveProperty('disabled', true);
  expect(screen.queryByLabelText('选择差异 4')).toBeNull();
  fireEvent.click(screen.getByLabelText('选择差异 1'));
  fireEvent.click(screen.getByRole('button', { name: '退回本页已选差异补证' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalledWith(dailyId));
  const body = JSON.parse(vi.mocked(apiNoReplay).mock.calls.find(([path]) => path.endsWith('/commands'))![1]!.body as string);
  expect(body.command.operation).toBe('request_changes'); expect(body.command.ordinals).toEqual([1]);
});
it.each(['unexplained', 'already_returned', 'matched'])('blocks an entire mixed return selection when a selected row becomes %s', async state => {
  const p = props(); p.access = dailyAccess('admin'); p.detail.allowed_actions = ['request_changes'];
  const row = dailyItem(), second = { ...row, ordinal: 2, review: { ...row.review, ordinal: 2 } };
  p.items = [row, second] as never[]; transport(p.access);
  const { rerender } = render(<Actions {...p} />);
  fireEvent.change(await screen.findByLabelText('总部审核说明'), { target: { value: '请重新核验全部证据' } });
  fireEvent.click(screen.getByLabelText('选择差异 1')); fireEvent.click(screen.getByLabelText('选择差异 2'));
  expect(screen.getByRole('button', { name: '退回本页已选差异补证' })).toHaveProperty('disabled', false);
  p.items = [row, { ...second, status: state === 'matched' ? 'matched' : 'difference', review: { ...second.review,
    explanation: state === 'unexplained' ? '' : second.review.explanation, revision_requested: state === 'already_returned' } }] as never[];
  rerender(<Actions {...p} />);
  expect(screen.getByRole('alert').textContent).toContain('本次不会部分提交');
  const submit = screen.getByRole('button', { name: '退回本页已选差异补证' }); expect(submit).toHaveProperty('disabled', true);
  fireEvent.click(submit); expect(apiNoReplay).not.toHaveBeenCalled(); expect(p.store.read(dailyId).kind).toBe('missing');
  fireEvent.click(screen.getByRole('button', { name: '清空本页选择' }));
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(screen.getByLabelText('选择差异 1'));
  fireEvent.click(screen.getByRole('button', { name: '退回本页已选差异补证' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalledWith(dailyId));
  const body = JSON.parse(vi.mocked(apiNoReplay).mock.calls.find(([path]) => path.endsWith('/commands'))![1]!.body as string);
  expect(body.command.ordinals).toEqual([1]);
});
it('keeps an explanation editable even when the difference was already returned', async () => {
  const p = props(); p.detail.allowed_actions = ['explain'];
  const row = dailyItem(); p.items = [{ ...row, review: { ...row.review, revision_requested: true } }] as never[];
  transport(); render(<Actions {...p} />);
  fireEvent.click(await screen.findByLabelText('选择差异 1'));
  fireEvent.change(screen.getByLabelText('第 1 项解释'), { target: { value: '根据退回要求重新补充解释' } });
  fireEvent.click(screen.getByRole('button', { name: '完成第 1 项证据' }));
  fireEvent.click(screen.getByRole('button', { name: '提交本页已选差异解释' }));
  await waitFor(() => expect(p.onChanged).toHaveBeenCalledWith(dailyId));
  const body = JSON.parse(vi.mocked(apiNoReplay).mock.calls.find(([path]) => path.endsWith('/commands'))![1]!.body as string);
  expect(body.command.operation).toBe('explain'); expect(body.command.items.map((v: { ordinal: number }) => v.ordinal)).toEqual([1]);
});
