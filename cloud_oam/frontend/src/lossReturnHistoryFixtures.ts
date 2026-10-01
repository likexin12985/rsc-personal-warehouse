import { stages } from './lossReturnHistory';
export const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const expected = { root: uuid(1), quantity: '1.000', serials: [] as string[] };
export function fixture(serial = false) {
  return { schema_version: '1.0', result_scope: 'verified_loss_return_history', stock_effect: 'none',
    current_stock_verified: false, write_authorization_provided: false, root_disposition_id: uuid(1), operation_id: uuid(2),
    observed_ledger_cursor: 9, evidence_fingerprint: 'a'.repeat(64),
    coordinates: { outbounds: [uuid(3)], shipments: [uuid(4)], receipts: [uuid(5)], inbounds: [uuid(6)] },
    lines: [{ operation_line_id: uuid(7), original_quantity: '1.000',
      shares: stages.map(stage => ({ stage, quantity: stage === 'posted_inbound' ? '1.000' : '0',
        serial_ids: stage === 'posted_inbound' && serial ? [uuid(8)] : [] })),
      damaged_accepted_quantity: serial ? '1.000' : '0.375',
      shortage_observations: [{ receipt_line_id: uuid(12), quantity: '1.000' }] }],
    classification_exceptions: [{ inbound_id: uuid(6), receipt_line_id: uuid(9), inbound_line_id: uuid(10),
      original_target_account_id: uuid(11), recorded_condition: 'new', required_condition: 'damaged',
      affected_quantity: serial ? '1.000' : '0.375', affected_serial_ids: serial ? [uuid(8)] : [],
      code: 'legacy_damaged_acceptance_classification', current_stock_verified: false, correction_authorized: false }] };
}
