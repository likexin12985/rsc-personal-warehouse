// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ConditionSerialCheck from './ConditionSerialCheck';
import { conditionId } from './returnConditionHistoryFixtures';
afterEach(cleanup);
const serials = [1, 2].map(n => ({ serial_id: conditionId(n), serial_no: `SN-${n}`, qr_code: `QR-${n}` }));
it('requires physical input for every selected item and rejects duplicates or wrong material', () => {
  const onVerified = vi.fn(); render(<ConditionSerialCheck sku="SKU" serials={serials} disabled={false} onVerified={onVerified} />);
  expect(onVerified).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('实物物料号'), { target: { value: 'WRONG' } });
  fireEvent.change(screen.getByLabelText('实物标识'), { target: { value: 'SN-1' } });
  expect((screen.getByRole('button', { name: '核对这件物资' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('实物物料号'), { target: { value: 'SKU' } });
  for (const code of ['SN-1', 'SN-1', 'UNKNOWN']) {
    fireEvent.change(screen.getByLabelText('实物标识'), { target: { value: code } });
    fireEvent.keyDown(screen.getByLabelText('实物标识'), { key: 'Enter' });
    expect(onVerified.mock.lastCall![0]).toEqual([]);
  }
  expect(screen.getByText('已核对 1 / 2 件')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('标识类型'), { target: { value: 'qr_code' } });
  fireEvent.change(screen.getByLabelText('实物标识'), { target: { value: 'QR-2' } });
  fireEvent.click(screen.getByRole('button', { name: '核对这件物资' }));
  expect(onVerified.mock.lastCall![0]).toEqual(serials.map(s => ({ ...s, sku_code: 'SKU' })));
  fireEvent.change(screen.getByLabelText('实物物料号'), { target: { value: 'SKU-CHANGED' } });
  expect(onVerified.mock.lastCall![0]).toEqual([]); expect(screen.getByText('已核对 0 / 2 件')).toBeTruthy();
});
it('rejects an ambiguous physical identifier instead of selecting the first record', () => {
  const onVerified = vi.fn(); render(<ConditionSerialCheck sku="SKU" serials={serials.map(s => ({ ...s, qr_code: 'SAME' }))} disabled={false} onVerified={onVerified} />);
  fireEvent.change(screen.getByLabelText('实物物料号'), { target: { value: 'SKU' } });
  fireEvent.change(screen.getByLabelText('标识类型'), { target: { value: 'qr_code' } });
  fireEvent.change(screen.getByLabelText('实物标识'), { target: { value: 'SAME' } });
  fireEvent.click(screen.getByRole('button', { name: '核对这件物资' }));
  expect(screen.getByText('已核对 0 / 2 件')).toBeTruthy(); expect(onVerified.mock.lastCall![0]).toEqual([]);
});
