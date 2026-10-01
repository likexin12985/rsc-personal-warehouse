// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import FormalLossReturnHistory from './FormalLossReturnHistory';
import { returnHistory, type ReturnHistory } from './lossReturnHistory';
import type { LossLine } from './formalLossReview';
import { fixture, expected, uuid } from './lossReturnHistoryFixtures';

afterEach(cleanup);
function props() {
  const value = returnHistory(fixture(), expected);
  const line: LossLine = { line_id: uuid(20), material_id: uuid(21), sku_code: 'TEST', material_name: '合成物料',
    base_unit: '件', condition_code: 'new', lot_id: null, lot_no: null, quantity: '1.000', serials: [] };
  return { identity: { person_id: uuid(30), authorization_version: 1 }, rootId: expected.root, line,
    read: vi.fn(async () => value) };
}
it('requires explicit read and separates historical anomaly from correction', async () => {
  const p = props(); render(<FormalLossReturnHistory {...p} />);
  expect(p.read).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '查询退回历史' }));
  await screen.findByText('历史入库成色需要核查');
  expect(p.read).toHaveBeenCalledExactlyOnceWith(p.rootId);
  expect(screen.getByText(/历史短少记录/)).toBeTruthy();
  expect(screen.getByText(/尚未核验物料现状，也未执行库存纠正/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: /冲销|纠正|批准/ })).toBeNull();
});
it('discards a late result after identity changes', async () => {
  const p = props(); let resolve!: (value: ReturnHistory) => void;
  p.read.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(<FormalLossReturnHistory {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '查询退回历史' }));
  view.rerender(<FormalLossReturnHistory {...p} identity={{ ...p.identity, authorization_version: 2 }} />);
  resolve(returnHistory(fixture(), expected));
  await waitFor(() => expect(screen.queryByText('历史入库成色需要核查')).toBeNull());
});
it('removes a previous result when refresh fails and never retries automatically', async () => {
  const p = props(); render(<FormalLossReturnHistory {...p} />);
  fireEvent.click(screen.getByRole('button', { name: '查询退回历史' }));
  await screen.findByText('历史入库成色需要核查'); p.read.mockRejectedValueOnce(new Error('读取权限已撤销'));
  fireEvent.click(screen.getByRole('button', { name: '查询退回历史' }));
  await screen.findByText('读取权限已撤销');
  expect(screen.queryByText('历史入库成色需要核查')).toBeNull();
  expect(p.read).toHaveBeenCalledTimes(2);
});
