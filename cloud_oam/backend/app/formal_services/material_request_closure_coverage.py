"""Per-line quantity requirement for closure, after evidence verification.

This pure rule is necessary but not sufficient for closing a request. Approval
and cancellation inputs must come from verified immutable facts; pending work,
current authorization, concurrent writes and the closure fact are handled by
the command boundary. It does not read or write request state projections.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

ZERO = Decimal("0.000")


@dataclass(frozen=True)
class LineCoverage:
    request_line_id: UUID
    approved_qty: Decimal
    cancelled_qty: Decimal
    posted_qty: Decimal

    @property
    def remaining_qty(self):
        return self.approved_qty - self.cancelled_qty - self.posted_qty


def _quantity(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO \
            or value >= Decimal("1000000000000000") or value != value.quantize(Decimal("0.001")):
        raise ValueError("closure quantities require finite, nonnegative decimal(18,3) facts")
    return value


def line_coverage(*, approved_by_line, cancelled_by_line, posted_receipt_lines):
    if not approved_by_line or not all(isinstance(key, UUID) for key in approved_by_line) \
            or not set(cancelled_by_line) <= set(approved_by_line):
        raise ValueError("closure requires the exact final-approved line set")
    approved = {key: _quantity(value) for key, value in approved_by_line.items()}
    if not any(value > ZERO for value in approved.values()):
        raise ValueError("a request without approved quantity cannot be fulfilled")
    cancelled = {key: _quantity(cancelled_by_line.get(key, ZERO)) for key in approved}
    posted = dict.fromkeys(approved, ZERO)
    receipt_line_ids = set()
    for fact in posted_receipt_lines:
        if fact.request_line_id not in approved or not isinstance(fact.receipt_line_id, UUID) \
                or not isinstance(fact.inventory_transaction_id, UUID) or fact.receipt_line_id in receipt_line_ids:
            raise ValueError("posted coverage must bind each receipt line exactly once")
        receipt_line_ids.add(fact.receipt_line_id)
        quantity = _quantity(fact.accepted_qty)
        if quantity <= ZERO:
            raise ValueError("only positively accepted, posted receipt lines cover approved demand")
        posted[fact.request_line_id] += quantity
    result = []
    for line_id in sorted(approved, key=str):
        if cancelled[line_id] + posted[line_id] > approved[line_id]:
            raise ValueError("posted and cancelled quantity exceeds the same approved line")
        result.append(LineCoverage(line_id, approved[line_id], cancelled[line_id], posted[line_id]))
    return tuple(result)
