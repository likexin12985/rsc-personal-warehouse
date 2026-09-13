from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.formal_services.stock_return_inbound_contract import (
    ReturnInboundContractError,
    ReturnInboundLine,
    build_return_inbound_command,
)


def ident(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012d}")


def line(value: int = 1, *, serial_ids=(), quantity="1.000"):
    return ReturnInboundLine(
        receipt_line_id=ident(value),
        source_account_id=ident(100 + value),
        target_account_id=ident(200 + value),
        material_id=ident(300),
        condition_code="used",
        lot_id=None,
        accepted_quantity=Decimal(quantity),
        serial_ids=tuple(serial_ids),
    )


def test_builds_transit_to_region_transfer_without_generic_inbound_fields():
    command = build_return_inbound_command(
        receipt_id=ident(9),
        effective_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        lines=(line(),),
    )
    assert command.movement_type == "transfer"
    assert command.source_document_type == "stock_return_receipt_inbound"
    assert command.movements[0].from_account_id == ident(101)
    assert command.movements[0].to_account_id == ident(201)


def test_rejects_duplicate_lines_and_serials():
    with pytest.raises(ReturnInboundContractError, match="只能入账一次"):
        build_return_inbound_command(receipt_id=ident(9), effective_at=datetime.now(timezone.utc), lines=(line(), line()))
    serial = ident(77)
    with pytest.raises(ReturnInboundContractError, match="SN 重复"):
        build_return_inbound_command(receipt_id=ident(9), effective_at=datetime.now(timezone.utc), lines=(line(1, serial_ids=(serial,)), line(2, serial_ids=(serial,))))


def test_rejects_naive_time_and_zero_quantity():
    with pytest.raises(ReturnInboundContractError, match="带时区"):
        build_return_inbound_command(receipt_id=ident(9), effective_at=datetime(2026, 9, 13), lines=(line(),))
    with pytest.raises(ReturnInboundContractError, match="正"):
        build_return_inbound_command(receipt_id=ident(9), effective_at=datetime.now(timezone.utc), lines=(line(quantity="0.000"),))
