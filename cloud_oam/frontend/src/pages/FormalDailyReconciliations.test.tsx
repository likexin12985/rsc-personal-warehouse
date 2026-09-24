// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api';
import { dailyDetail, dailyExcluded, dailyHistory, dailyId, dailyItems, otherDailyId } from '../__fixtures__/dailyReconciliation';
import Page from './FormalDailyReconciliations';
const listeners = vi.hoisted(() => new Map<string, () => void>());
vi.mock('../api', async original => ({ ...await original<typeof import('../api')>(), api: vi.fn(),
  subscribeAuthenticationTerminalLogout: (fn: () => void) => { listeners.set('logout', fn); return () => listeners.delete('logout'); },
  subscribeAuthenticationEstablished: (fn: () => void) => { listeners.set('login', fn); return () => listeners.delete('login'); },
}));
function normal(path: string): unknown {
  if (path.includes('daily?')) return { items: [dailyDetail()], next_after_id: null };
  if (path.includes('/items?')) return dailyItems();
  if (path.includes('/excluded-quantities?')) return dailyExcluded();
  if (path.includes('/history?')) return dailyHistory();
  return dailyDetail();
}
async function open() { fireEvent.click(await screen.findByRole('button', { name: `查看 2026-09-21 ${dailyId}` })); }
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockImplementation(async path => normal(path)); });
afterEach(cleanup);
it('shows independent review and exact quantities without any command request', async () => {
  render(<Page />); await open();
  const detail = await screen.findByRole('region', { name: '日终对账详情' });
  expect(within(detail).getByText('999999999999999.999')).toBeTruthy();
  expect(within(detail).getByText('0.001')).toBeTruthy();
  expect(within(detail).getByText('待总部复核')).toBeTruthy();
  expect(within(detail).getByText('数量有差异')).toBeTruthy();
  expect(within(detail).getByText(/合成数量差异说明/)).toBeTruthy();
  expect(vi.mocked(api).mock.calls.every(args => args.length === 1 && !args[0].includes('commands'))).toBe(true);
});
it('shows empty results only after a successful empty query', async () => {
  vi.mocked(api).mockResolvedValue({ items: [], next_after_id: null });
  render(<Page />); expect(await screen.findByText('暂无可见日终档案')).toBeTruthy();
});
it('does not turn a failed query into empty inventory or an empty report', async () => {
  vi.mocked(api).mockRejectedValue(new ApiError(503, 'unavailable'));
  render(<Page />); expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('暂无可见日终档案')).toBeNull();
});
it('applies date and region filters only when explicitly queried', async () => {
  render(<Page />); await screen.findByRole('button', { name: `查看 2026-09-21 ${dailyId}` });
  fireEvent.change(screen.getByLabelText('对账日期'), { target: { value: '2026-09-20' } });
  fireEvent.change(screen.getByLabelText('区域标识'), { target: { value: dailyId } });
  expect(api).toHaveBeenCalledTimes(1);
  vi.mocked(api).mockResolvedValue({ items: [], next_after_id: null });
  fireEvent.click(screen.getByRole('button', { name: '查询' }));
  await screen.findByText('暂无可见日终档案');
  expect(api).toHaveBeenLastCalledWith(`/v1/reconciliations/daily?limit=20&business_date=2026-09-20&region_org_id=${dailyId}`);
});
it.each([401, 403, 404, 409])('clears the previous detail when fresh authorization/version fails (%s)', async status => {
  render(<Page />); await open(); await screen.findByRole('region', { name: '日终对账详情' });
  vi.mocked(api).mockRejectedValue(new ApiError(status, 'refused'));
  fireEvent.click(screen.getByRole('button', { name: '刷新当前对账' }));
  await screen.findByRole('alert'); expect(screen.queryByRole('region', { name: '日终对账详情' })).toBeNull();
});
it('does not display a partial bundle when history has advanced', async () => {
  vi.mocked(api).mockImplementation(async path => path.includes('/history?') ? { ...dailyHistory(), review_version: 2 } : normal(path));
  render(<Page />); await open(); await screen.findByRole('alert');
  expect(screen.queryByRole('region', { name: '日终对账详情' })).toBeNull();
});
it.each(['login', 'logout'])('removes private data and ignores an in-flight response on %s', async event => {
  render(<Page />); await open(); await screen.findByRole('region', { name: '日终对账详情' });
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole('button', { name: '刷新当前对账' }));
  act(() => listeners.get(event)!());
  await act(async () => resolve(dailyDetail()));
  expect(screen.queryByRole('region', { name: '日终对账详情' })).toBeNull();
  expect(screen.queryByRole('button', { name: `查看 2026-09-21 ${dailyId}` })).toBeNull();
});
it('ignores an older detail response after another report is selected', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('daily?')) return { items: [dailyDetail(), { ...dailyDetail(), cutoff_id: otherDailyId }], next_after_id: null };
    if (path.endsWith(dailyId)) return new Promise(r => { resolve = r; });
    return { ...normal(path) as object, cutoff_id: otherDailyId };
  });
  render(<Page />); await open();
  fireEvent.click(screen.getByRole('button', { name: `查看 2026-09-21 ${otherDailyId}` }));
  await screen.findByRole('region', { name: '日终对账详情' });
  const calls = vi.mocked(api).mock.calls.length;
  await act(async () => resolve({ ...dailyDetail(), business_date: '2026-09-19' }));
  expect(screen.queryByRole('heading', { name: '2026-09-19 日终对账' })).toBeNull();
  expect(api).toHaveBeenCalledTimes(calls);
});
it('drops all detail pages when the next item page reports a changed version', async () => {
  vi.mocked(api).mockImplementation(async path => {
    if (path.includes('/items?')) {
      if (path.includes('after_ordinal=100')) throw new ApiError(409, 'changed');
      return { ...dailyItems(), items: Array.from({ length: 100 }, (_, i) => ({ ...dailyItems().items[0], ordinal: i + 1,
        review: { ...dailyItems().items[0].review, ordinal: i + 1 } })), next_after_ordinal: 100 };
    }
    if (path.endsWith(dailyId)) return { ...dailyDetail(), item_count: 101 };
    return normal(path);
  });
  render(<Page />); await open();
  fireEvent.click(await screen.findByRole('button', { name: '下一页明细' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '审核已变化，请刷新这份对账后重新查看');
  expect(screen.queryByRole('region', { name: '日终对账详情' })).toBeNull();
});
it('does not issue dependent reads after unmount', async () => {
  let resolve!: (value: unknown) => void;
  const { unmount } = render(<Page />); await screen.findByRole('button', { name: `查看 2026-09-21 ${dailyId}` });
  vi.mocked(api).mockImplementation(() => new Promise(r => { resolve = r; }));
  await open(); unmount(); const calls = vi.mocked(api).mock.calls.length;
  await act(async () => resolve(dailyDetail()));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(calls));
});
