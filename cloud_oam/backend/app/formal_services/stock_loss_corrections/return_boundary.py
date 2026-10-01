"""Portable, independently reproducible unshipped-return plan coordinates.

The caller must prove the original facts, the complete fulfillment graph and
current authority. Raw row fingerprints remain a separate reread fence; this
document avoids driver-specific datetime/Decimal representations in SQL proofs.
"""
from app.formal_services import stock_loss_sources as sources


def document(root, child, lines):
    if root.return_operation_id != child.id or len(lines) != 1:
        raise ValueError('exact original unshipped return line required')
    items = []
    for line in lines:
        pending = next(share for share in line.shares if share.stage == 'not_outbound')
        if (line.original_quantity != root.quantity or pending.quantity != root.quantity
                or any(share.quantity != 0 for share in line.shares if share.stage != 'not_outbound')
                or [str(value) for value in pending.serial_ids] != root.plan_jsonb['serial_ids']):
            raise ValueError('whole original quantity and serials must remain unshipped')
        items.append(dict(operation_line_id=str(line.operation_line_id),
            quantity=format(line.original_quantity, '.3f'), serial_ids=[str(value) for value in pending.serial_ids]))
    origin = dict(schema_version='unshipped_return_boundary/1',
        root_disposition_id=str(root.id), root_request_hash=root.request_hash,
        root_plan_hash=root.plan_hash, return_operation_id=str(child.id),
        return_request_hash=child.request_hash, return_plan_hash=child.plan_hash, lines=items)
    return dict(disposition_id=str(root.id), operation_id=str(child.id),
        cancellations=[], outbounds=[], shipments=[], receipts=[], inbounds=[],
        evidence_fingerprint=sources._hash(origin), lines=items)
