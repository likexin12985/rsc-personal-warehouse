import { describe, expect, it } from 'vitest';
import { returnHistory } from './lossReturnHistory';

import { fixture, expected, uuid } from './lossReturnHistoryFixtures';

it.each([false, true])('keeps cumulative damage/shortage distinct and validates exact SN (%s)', serial => {
  const raw = fixture(serial), copy = structuredClone(raw);
  const value = returnHistory(raw, { ...expected, serials: serial ? [uuid(8)] : [] });
  expect(value.lines[0].shares[0].quantity).toBe('0.000');
  expect(value.lines[0].shares[5].quantity).toBe('1.000');
  expect(value.lines[0].shortage_observations[0].quantity).toBe('1.000');
  expect(value.classification_exceptions[0].correction_authorized).toBe(false);
  expect(raw).toEqual(copy);
});

describe('unproved responses are rejected', () => {
  it.each(['root', 'quantity', 'missing-stage', 'duplicate-stage', 'damage', 'exception-total', 'outside-inbound',
    'duplicate-exception', 'stock-claim', 'write-claim', 'extra-command', 'float-quantity', 'precision'])('%s', problem => {
    const value = fixture();
    if (problem === 'root') value.root_disposition_id = uuid(90);
    if (problem === 'quantity') value.lines[0].shares[0].quantity = '1.000';
    if (problem === 'missing-stage') value.lines[0].shares.pop();
    if (problem === 'duplicate-stage') value.lines[0].shares[0].stage = 'posted_inbound';
    if (problem === 'damage') value.lines[0].damaged_accepted_quantity = '2';
    if (problem === 'exception-total') value.classification_exceptions[0].affected_quantity = '0.5';
    if (problem === 'outside-inbound') value.classification_exceptions[0].inbound_id = uuid(90);
    if (problem === 'duplicate-exception') value.classification_exceptions.push(value.classification_exceptions[0]);
    if (problem === 'stock-claim') value.current_stock_verified = true;
    if (problem === 'write-claim') value.write_authorization_provided = true;
    if (problem === 'extra-command') Object.assign(value, { command_jsonb: {} });
    if (problem === 'float-quantity') Object.assign(value.lines[0], { original_quantity: 1 });
    if (problem === 'precision') value.lines[0].original_quantity = '1.0001';
    expect(() => returnHistory(value, expected)).toThrow();
  });
  it.each(['missing', 'duplicate', 'foreign', 'exception-foreign'])('rejects %s serial evidence', problem => {
    const value = fixture(true);
    if (problem === 'missing') value.lines[0].shares[5].serial_ids = [];
    if (problem === 'duplicate') value.lines[0].shares[0].serial_ids = [uuid(8)];
    if (problem === 'foreign') value.lines[0].shares[5].serial_ids = [uuid(90)];
    if (problem === 'exception-foreign') value.classification_exceptions[0].affected_serial_ids = [uuid(90)];
    expect(() => returnHistory(value, { ...expected, serials: [uuid(8)] })).toThrow();
  });
});
