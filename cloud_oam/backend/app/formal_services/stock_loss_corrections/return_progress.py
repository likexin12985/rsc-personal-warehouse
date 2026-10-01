"""Disjoint fulfillment shares from a completely verified loss-return graph.

These are cumulative business shares, not current stock or a compensation plan.
Shortage observations can repeat; damaged quantity is a subset of acceptance.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Share:
    stage: str
    quantity: Decimal
    serial_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class LineProgress:
    operation_line_id: UUID
    original_quantity: Decimal
    shares: tuple[Share, ...]
    damaged_accepted_quantity: Decimal
    shortage_observations: tuple[tuple[UUID, Decimal], ...]


def project_lines(groups):
    """Reject cross-links, duplicate confirmed SN and any negative residual."""
    by_id = {key: {row.id: row for row in rows} for key, rows in groups.items()}
    links = (
        ('outbounds', 'operation_id', 'orders'), ('outbound_lines', 'outbound_id', 'outbounds'),
        ('outbound_lines', 'operation_line_id', 'lines'), ('outbound_serials', 'line_id', 'outbound_lines'),
        ('shipments', 'operation_id', 'orders'), ('shipment_lines', 'shipment_id', 'shipments'),
        ('shipment_lines', 'outbound_line_id', 'outbound_lines'), ('shipment_serials', 'line_id', 'shipment_lines'),
        ('shipment_serials', 'outbound_line_id', 'outbound_lines'), ('receipts', 'shipment_id', 'shipments'),
        ('receipt_lines', 'receipt_id', 'receipts'), ('receipt_lines', 'shipment_line_id', 'shipment_lines'),
        ('receipt_serials', 'line_id', 'receipt_lines'), ('receipt_serials', 'shipment_line_id', 'shipment_lines'),
        ('receipt_exceptions', 'line_id', 'receipt_lines'), ('receipt_exceptions', 'receipt_id', 'receipts'),
        ('inbounds', 'receipt_id', 'receipts'), ('inbounds', 'shipment_id', 'shipments'),
        ('inbound_lines', 'inbound_id', 'inbounds'), ('inbound_lines', 'receipt_line_id', 'receipt_lines'),
        ('inbound_serials', 'line_id', 'inbound_lines'), ('inbound_serials', 'inbound_id', 'inbounds'),
        ('inbound_serials', 'receipt_serial_id', 'receipt_serials'), ('inbound_postings', 'inbound_id', 'inbounds'),
    )
    for group, field, parent in links:
        if any(getattr(row, field) not in by_id[parent] for row in groups[group]):
            raise ValueError('foreign fulfillment edge')

    def serials(name, ids, outcome=None):
        rows = [row.serial_id for row in groups[name] if row.line_id in ids
            and (outcome is None or row.result == outcome)]
        if len(rows) != len(set(rows)):
            raise ValueError('duplicate fulfillment serial')
        return set(rows)

    output = []
    for line in groups['lines']:
        departed = [r for r in groups['outbound_lines'] if r.operation_line_id == line.id]
        departed_ids = {r.id for r in departed}
        parcels = [r for r in groups['shipment_lines'] if r.outbound_line_id in departed_ids]
        parcel_ids = {r.id for r in parcels}
        received = [r for r in groups['receipt_lines'] if r.shipment_line_id in parcel_ids]
        receipt_ids = {r.id for r in received}
        posted = [r for r in groups['inbound_lines'] if r.receipt_line_id in receipt_ids]
        total = lambda rows, key: sum((getattr(r, key) for r in rows), Decimal(0))
        d, s = total(departed, 'quantity'), total(parcels, 'quantity')
        a, r, i = total(received, 'accepted_qty'), total(received, 'rejected_qty'), total(posted, 'accepted_qty')
        qsn = serials('serials', {line.id})
        dsn, ssn = serials('outbound_serials', departed_ids), serials('shipment_serials', parcel_ids)
        asn, rsn = serials('receipt_serials', receipt_ids, 'accepted'), serials('receipt_serials', receipt_ids, 'rejected')
        isn = serials('inbound_serials', {row.id for row in posted})
        if not (isn <= asn <= ssn <= dsn <= qsn) or not rsn <= ssn or asn & rsn:
            raise ValueError('inconsistent fulfillment serial subsets')
        shares = tuple(Share(name, amount, tuple(sorted(ids, key=str))) for name, amount, ids in (
            ('not_outbound', line.quantity-d, qsn-dsn), ('outbound_not_shipped', d-s, dsn-ssn),
            ('shipped_unconfirmed', s-a-r, ssn-asn-rsn), ('accepted_not_inbound', a-i, asn-isn),
            ('rejected', r, rsn), ('posted_inbound', i, isn)))
        if any(share.quantity < 0 or (qsn and share.quantity != len(share.serial_ids)) for share in shares):
            raise ValueError('invalid fulfillment quantity partition')
        if sum((share.quantity for share in shares), Decimal(0)) != line.quantity:
            raise ValueError('fulfillment quantity does not conserve')
        output.append(LineProgress(line.id, line.quantity, shares, total(received, 'damaged_qty'),
            tuple((row.id, row.shortage_qty) for row in received if row.shortage_qty > 0)))
    return tuple(output)
