// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Inbox from './FormalReturnConditionInbox';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';
import type { ConditionInbox } from './returnConditionAdapter';
afterEach(cleanup);
function setup() {
  const identity = { person_id: conditionId(30), authorization_version: 1 };
  const value: ConditionInbox = { ...identity, view: 'pending', items: [conditionFixture()], next_after_id: conditionId(1) };
  const adapter = { inbox: vi.fn(async (): Promise<ConditionInbox> => structuredClone(value)) };
  return { identity, value, adapter, onOpen: vi.fn() };
}
it('opens the verified inbound without manual coordinates and clears stale items on next-page failure', async () => {
  const p = setup(); render(<Inbox {...p} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看并办理' }));
  expect(p.onOpen).toHaveBeenCalledWith(conditionId(1));
  expect(p.adapter.inbox).toHaveBeenCalledWith('pending', null);
  p.adapter.inbox.mockRejectedValueOnce(new Error('scope changed'));
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('button', { name: '查看并办理' })).toBeNull();
  expect(p.adapter.inbox).toHaveBeenLastCalledWith('pending', conditionId(1));
});
it('discards late results after identity changes and keeps pending/all views separate', async () => {
  const p = setup(); let complete!: (value: ConditionInbox) => void;
  p.adapter.inbox.mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  const view = render(<Inbox {...p} />);
  const changed = { person_id: conditionId(31), authorization_version: 2 };
  p.adapter.inbox.mockResolvedValue({ ...p.value, ...changed, items: [], next_after_id: null });
  view.rerender(<Inbox {...p} identity={changed} />);
  await screen.findByText('当前范围没有符合条件的案件。');
  await act(async () => { complete(p.value); });
  expect(screen.queryByRole('button', { name: '查看并办理' })).toBeNull();
  p.adapter.inbox.mockResolvedValue({ ...p.value, ...changed, view: 'all', next_after_id: null });
  fireEvent.change(screen.getByLabelText('案件范围'), { target: { value: 'all' } });
  await screen.findByRole('button', { name: '查看并办理' });
  expect(p.adapter.inbox).toHaveBeenLastCalledWith('all', null);
});
