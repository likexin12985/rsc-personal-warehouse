"""Classification exceptions in already proved historical inbound facts.

The caller must authorize and prove the complete graph first. This projection
does not infer present stock, authorize reversal or rewrite original requests.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class InboundConditionException:
    inbound_id: UUID
    receipt_line_id: UUID
    inbound_line_id: UUID
    original_target_account_id: UUID
    recorded_condition: str
    required_condition: str
    affected_quantity: Decimal
    affected_serial_ids: tuple[UUID, ...]
    code: str = 'legacy_damaged_acceptance_classification'
    current_stock_verified: bool = False
    correction_authorized: bool = False


def classification_exceptions(groups):
    """Flag preserved v1 damage misclassification without calling it corrupt history."""
    receipts = {row.id: row for row in groups['receipt_lines']}
    inbounds = {row.id: row for row in groups['inbounds']}
    output = []
    for line in sorted(groups['inbound_lines'], key=lambda row: str(row.id)):
        header, receipt = inbounds[line.inbound_id], receipts[line.receipt_line_id]
        version = header.plan_jsonb['schema_version']
        if version not in ('1.0', '2.0'):
            raise ValueError('unknown historical inbound contract')
        if version != '1.0' or receipt.damaged_qty == 0 or line.condition_code == 'damaged':
            continue
        if line.condition_code not in ('new', 'used') or line.accepted_qty != receipt.accepted_qty:
            raise ValueError('unproved legacy inbound partition')
        identifiers = tuple(sorted((row.serial_id for row in groups['receipt_serials']
            if row.line_id == receipt.id and row.result == 'accepted' and row.damaged), key=str))
        output.append(InboundConditionException(header.id, receipt.id, line.id,
            line.target_account_id, line.condition_code, 'damaged', receipt.damaged_qty, identifiers))
    return tuple(output)
