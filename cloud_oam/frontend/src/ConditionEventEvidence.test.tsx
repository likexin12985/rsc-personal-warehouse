// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Evidence from './ConditionEventEvidence';
import { conditionId } from './returnConditionHistoryFixtures';
afterEach(() => { cleanup(); vi.useRealTimers(); });
function setup() {
  const value = { url: 'https://synthetic.invalid/private?signature=test', expires_at: new Date(Date.now() + 60000).toISOString() };
  return { adapter: { download: vi.fn(async () => value) }, inbound: conditionId(1), event: conditionId(11), files: [conditionId(40)], value };
}
it('requires explicit verification, clears expired links, and clears old success on a failed recheck', async () => {
  vi.useFakeTimers(); const p = setup(); render(<Evidence {...p} />);
  expect(p.adapter.download).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核验附件 1' })); });
  const link = screen.getByRole('link', { name: '打开附件 1' });
  expect(link.getAttribute('href')).toBe(p.value.url); expect(link.getAttribute('referrerpolicy')).toBe('no-referrer');
  act(() => { vi.advanceTimersByTime(60001); });
  expect(screen.queryByRole('link')).toBeNull(); expect(screen.getByRole('alert').textContent).toContain('过期');
  p.adapter.download.mockRejectedValue(new Error('scope revoked'));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '核验附件 1' })); });
  expect(screen.queryByRole('link')).toBeNull(); expect(screen.getByRole('alert').textContent).toContain('查看权限');
});
it('discards a late link after the original event changes', async () => {
  const p = setup(); let finish!: (value: typeof p.value) => void;
  p.adapter.download.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<Evidence {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '核验附件 1' }));
  view.rerender(<Evidence {...p} event={conditionId(12)} />);
  await act(async () => { finish(p.value); });
  expect(screen.queryByRole('link')).toBeNull(); expect(p.adapter.download).toHaveBeenCalledTimes(1);
});
