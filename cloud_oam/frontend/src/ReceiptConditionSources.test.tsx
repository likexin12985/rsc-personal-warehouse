// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ReceiptConditionSources from './ReceiptConditionSources';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';
afterEach(cleanup);
function props() { return { identity: { person_id: conditionId(30), authorization_version: 1 }, receipt: conditionId(60), shipment: conditionId(61),
  root: conditionId(2), read: vi.fn(async () => [conditionFixture()]), onOpen: vi.fn(), disabled: false }; }
it('requires explicit lookup and opens only an exact verified inbound line', async () => {
  const p = props(); render(<ReceiptConditionSources {...p} />); expect(p.read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '核查本次入库成色' }));
  const open = await screen.findByRole('button', { name: '查看并办理成色纠正' });
  expect(p.read).toHaveBeenCalledExactlyOnceWith(p.receipt, p.shipment, p.root);
  fireEvent.click(open); expect(p.onOpen).toHaveBeenCalledExactlyOnceWith(conditionId(1));
  p.read.mockRejectedValueOnce(new Error('private error')); fireEvent.click(screen.getByRole('button', { name: '核查本次入库成色' }));
  await screen.findByRole('alert'); expect(screen.queryByRole('button', { name: '查看并办理成色纠正' })).toBeNull(); expect(screen.queryByText('private error')).toBeNull();
});
it('discards a late result after the receipt or identity changes', async () => {
  const p = props(); let resolve!: (v: ReturnType<typeof conditionFixture>[]) => void;
  p.read.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<ReceiptConditionSources {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '核查本次入库成色' }));
  await waitFor(() => expect(p.read).toHaveBeenCalledTimes(1));
  view.rerender(<ReceiptConditionSources {...p} receipt={conditionId(62)} />); resolve([conditionFixture()]);
  await waitFor(() => expect(screen.queryByText('正在核验原入库记录…')).toBeNull());
  expect(screen.queryByRole('button', { name: '查看并办理成色纠正' })).toBeNull(); expect(p.onOpen).not.toHaveBeenCalled();
});
