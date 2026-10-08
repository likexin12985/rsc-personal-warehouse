import { expect, it } from 'vitest';
import { conditionHistory, type ConditionHistory } from './returnConditionHistory';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';
const expected = { inbound: conditionId(1), root: conditionId(2), material: conditionId(3) };
it('keeps approval separate from stock correction and conserves fractional quantities exactly', () => {
  const value = conditionFixture(); expect(conditionHistory(value, expected)).toEqual(value);
  expect(conditionHistory(value, expected).corrected_quantity).toBe('0.000');
});
const mutations: ((v: ConditionHistory) => void)[] = [
  v => { v.events.splice(1, 1); }, v => { v.events[2].previous_event_id = conditionId(99); },
  v => { v.events[2].fact.stock_effect = 'status_change'; }, v => { v.events[2].fact.case_id = conditionId(99); },
  v => { v.cases[0].latest_event_hash = 'f'.repeat(64); }, v => { v.held_quantity = '0.124'; },
  v => { v.cases[0].status = 'executed'; }, v => { v.events[1].occurred_at = 'invalid'; },
  v => { v.cases[0].serial_ids = [conditionId(20)]; }, v => { v.events.push(v.events[0]); },
  v => { v.events[0].evidence_file_ids.push(v.events[0].evidence_file_ids[0]); },
  v => { Object.assign(v, { command_jsonb: {} }); }, v => { Object.assign(v, { current_stock_verified: true }); },
];
it.each(mutations.map((mutate, index) => ({ mutate, index })))('rejects corrupted public history $index', ({ mutate }) => {
  const value = conditionFixture(); mutate(value); expect(() => conditionHistory(value, expected)).toThrow();
});
it.each(['root', 'inbound', 'material'] as const)('rejects a different selected %s', key => {
  expect(() => conditionHistory(conditionFixture(), { ...expected, [key]: conditionId(99) })).toThrow();
});
