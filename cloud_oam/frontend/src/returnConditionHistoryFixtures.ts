/** Synthetic transport/UI fixture, not native database or user acceptance evidence. */
import type { ConditionHistory } from './returnConditionHistory';
export const conditionId = (n: number) => `40000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
export function conditionFixture(): ConditionHistory {
  const base = { schema_version: 'condition_result/1' as const, case_id: conditionId(10), inbound_line_id: conditionId(1),
    quantity: '0.125', actor_user_id: 'synthetic-reviewer', actor_person_id: conditionId(30), authorization_version: 1,
    request_id: 'synthetic-condition', request_hash: 'a'.repeat(64), plan_hash: 'b'.repeat(64), reason: '合成核验意见' };
  return { schema_version: 'condition_history/1', result_scope: 'verified_condition_history', inbound_line_id: conditionId(1),
    root_disposition_id: conditionId(2), material_id: conditionId(3), source_account_id: conditionId(4),
    sku_code: 'SYNTHETIC-MAT', material_name: '合成测试物料', base_unit: '件', serials: [],
    recorded_condition: 'new', required_condition: 'damaged', historical_damaged_quantity: '0.375', held_quantity: '0.125',
    corrected_quantity: '0.000', unclaimed_quantity: '0.250', cases: [{ case_id: conditionId(10), status: 'approved', quantity: '0.125',
      serial_ids: [], latest_event_id: conditionId(13), latest_event_hash: 'a'.repeat(64) }],
    events: [
      { sequence: 1, occurred_at: '2026-10-06T01:00:00Z', previous_event_id: null, evidence_file_ids: [conditionId(40)],
        fact: { ...base, event_id: conditionId(11), action: 'submit', status: 'awaiting_regional', stock_effect: 'freeze', posting_transaction_id: conditionId(41), posting_movement_id: conditionId(42) } },
      { sequence: 2, occurred_at: '2026-10-06T02:00:00Z', previous_event_id: conditionId(11), evidence_file_ids: [],
        fact: { ...base, event_id: conditionId(12), action: 'verify_region', status: 'awaiting_headquarters', stock_effect: 'none', posting_transaction_id: null, posting_movement_id: null } },
      { sequence: 3, occurred_at: '2026-10-06T03:00:00Z', previous_event_id: conditionId(12), evidence_file_ids: [],
        fact: { ...base, event_id: conditionId(13), action: 'approve_hq', status: 'approved', stock_effect: 'none', posting_transaction_id: null, posting_movement_id: null } },
    ], observed_ledger_cursor: 20, history_fingerprint: 'c'.repeat(64), current_stock_verified: false,
    write_authorization_provided: false, retry_allowed: false, stock_effect: 'none' };
}
