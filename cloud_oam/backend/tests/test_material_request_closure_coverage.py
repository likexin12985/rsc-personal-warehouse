from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4
import pytest

from app.formal_services import material_request_closure_coverage as service


def posting(line, quantity):
    return SimpleNamespace(request_line_id=line, receipt_line_id=uuid4(), inventory_transaction_id=uuid4(), accepted_qty=D(quantity))


def test_split_postings_preserve_exact_fractional_coverage():
    line=uuid4()
    rows=service.line_coverage(approved_by_line={line:D("1")},cancelled_by_line={},
        posted_receipt_lines=(posting(line,"0.125"), posting(line,"0.875")))
    assert rows[0].remaining_qty==D("0") and rows[0].posted_qty==D("1.000")


def test_one_posted_line_cannot_cover_a_different_approved_line():
    first,second=uuid4(),uuid4()
    rows=service.line_coverage(approved_by_line={first:D("1"),second:D("1")},cancelled_by_line={},posted_receipt_lines=(posting(first,"1"),))
    assert {row.request_line_id:row.remaining_qty for row in rows}=={first:D("0"),second:D("1")}
    with pytest.raises(ValueError,match="same approved line"):
        service.line_coverage(approved_by_line={first:D("1"),second:D("1")},cancelled_by_line={},posted_receipt_lines=(posting(first,"2"),))


def test_verified_cancellation_and_posted_quantity_cover_their_original_line():
    line=uuid4()
    rows=service.line_coverage(approved_by_line={line:D("2")},cancelled_by_line={line:D("0.5")},posted_receipt_lines=(posting(line,"1.5"),))
    assert rows[0].remaining_qty==D("0")


def test_unposted_acceptance_does_not_count():
    line=uuid4()
    rows=service.line_coverage(approved_by_line={line:D("1")},cancelled_by_line={},posted_receipt_lines=())
    assert rows[0].remaining_qty==D("1")


def test_duplicate_receipt_line_cannot_count_twice_even_with_another_transaction():
    line=uuid4(); first=posting(line,"1"); second=posting(line,"1");second.receipt_line_id=first.receipt_line_id
    with pytest.raises(ValueError,match="exactly once"):
        service.line_coverage(approved_by_line={line:D("2")},cancelled_by_line={},posted_receipt_lines=(first,second))


@pytest.mark.parametrize("quantity",[D("NaN"),D("Infinity"),D("-1"),D("0.0001"),D("1000000000000000"),1.0])
def test_invalid_fact_quantity_never_becomes_a_completed_line(quantity):
    with pytest.raises(ValueError):
        service.line_coverage(approved_by_line={uuid4():quantity},cancelled_by_line={},posted_receipt_lines=())


def test_empty_or_unapproved_request_is_not_vacuously_complete():
    for approved in ({},{uuid4():D("0")}):
        with pytest.raises(ValueError):
            service.line_coverage(approved_by_line=approved,cancelled_by_line={},posted_receipt_lines=())
