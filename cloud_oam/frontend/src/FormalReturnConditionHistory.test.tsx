// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import FormalReturnConditionHistory from './FormalReturnConditionHistory';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';
import type { ConditionHistory } from './returnConditionHistory';
afterEach(cleanup);
function props() { return { identity: { person_id: conditionId(50), authorization_version: 1 },
  inboundId: conditionId(1), rootId: conditionId(2), unit: '件', read: vi.fn(async () => conditionFixture()) }; }
it('reads only on demand and visibly distinguishes approved, frozen and corrected', async () => {
  const p = props(); render(<FormalReturnConditionHistory {...p} />);
  expect(p.read).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '查看成色纠正历史' }));
  await screen.findByText('第 1 笔 · 已批准，待执行');
  expect(p.read).toHaveBeenCalledExactlyOnceWith(p.rootId, p.inboundId);
  expect(screen.getByText(/已纠正 0.000/)).toBeTruthy(); expect(screen.getAllByText(/本次未变动库存/)).toHaveLength(2);
});
it('does not render old identity results and clears history on failed refresh without replay', async () => {
  const p = props(); let resolve!: (v: ConditionHistory) => void;
  p.read.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<FormalReturnConditionHistory {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '查看成色纠正历史' }));
  view.rerender(<FormalReturnConditionHistory {...p} identity={{ ...p.identity, authorization_version: 2 }} />);
  resolve(conditionFixture()); await waitFor(() => expect(screen.queryByText(/第 1 笔/)).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: '查看成色纠正历史' })); await screen.findByText(/第 1 笔/);
  p.read.mockRejectedValueOnce(new Error('PRIVATE-SQL'));
  fireEvent.click(screen.getByRole('button', { name: '查看成色纠正历史' }));
  await screen.findByRole('alert'); expect(screen.queryByText(/第 1 笔/)).toBeNull();
  expect(screen.queryByText('PRIVATE-SQL')).toBeNull(); expect(p.read).toHaveBeenCalledTimes(3);
});
