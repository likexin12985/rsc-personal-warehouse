"""Export rows preserve serial and account quantities from a proved read."""

from io import BytesIO
from uuid import UUID

from openpyxl import load_workbook
import pytest

from app.formal_services.inventory_report_rows import inventory_export_lines
from app.formal_services.inventory_report_workbook import InventoryWorkbookError, render_inventory_workbook
from app.inventory_schemas import InventoryAccountOut


ACCOUNT = UUID("20000000-0000-4000-8000-000000000001")
MATERIAL = UUID("30000000-0000-4000-8000-000000000001")


def _account(**changes):
    values = dict(
        stock_account_id=ACCOUNT,
        owner_org_id=UUID("10000000-0000-4000-8000-000000000001"),
        owner_org_code="HQ", owner_org_name="总部",
        location_owner_org_id=UUID("10000000-0000-4000-8000-000000000002"),
        location_owner_org_code="JS", location_owner_org_name="江苏区域",
        location_id=UUID("40000000-0000-4000-8000-000000000001"),
        location_code="JS-A", location_name="江苏仓", location_type="region",
        location_parent_id=None, custodian_person_id=None, custodian_person_name=None,
        material_id=MATERIAL, sku_code="SKU-001", material_name="备件", base_unit="件",
        tracking_mode="serial", condition_code="new", availability_bucket="available",
        lot_id=None, lot_no=None, quantity_status="available", quantity="2.000",
        balance_version=1, ledger_cursor=9,
    )
    values.update(changes)
    return InventoryAccountOut(**values)


def test_serial_export_is_one_row_per_sn_and_preserves_total():
    lines = inventory_export_lines(
        [_account()], serials_by_account={ACCOUNT: ("SN-B", "SN-A")}, ledger_cursor=9,
    )
    assert [(row.serial_no, row.quantity) for row in lines] == [
        ("SN-A", "1.000"), ("SN-B", "1.000"),
    ]
    data = render_inventory_workbook(lines, ledger_cursor=9)
    book = load_workbook(BytesIO(data), read_only=True)
    try:
        rows = list(book.active.values)
        assert [row[13] for row in rows[2:]] == ["SN-A", "SN-B"]
        assert [row[14] for row in rows[2:]] == ["1.000", "1.000"]
    finally:
        book.close()


@pytest.mark.parametrize("account,serials,message", [
    (_account(quantity="2.000"), ("SN-A",), "SN 件数"),
    (_account(quantity="1.500"), ("SN-A",), "SN 件数"),
    (_account(quantity_status="opening_not_established", quantity=None), (), "期初"),
    (_account(ledger_cursor=10), ("SN-A", "SN-B"), "快照"),
    (_account(tracking_mode="none"), ("SN-A",), "非 SN"),
])
def test_export_rejects_incomplete_or_unproved_rows(account, serials, message):
    with pytest.raises(InventoryWorkbookError, match=message):
        inventory_export_lines(
            [account], serials_by_account={ACCOUNT: serials}, ledger_cursor=9,
        )


def test_export_rejects_duplicate_sn_and_out_of_scope_positions():
    second = _account(
        stock_account_id=UUID("20000000-0000-4000-8000-000000000002"),
        quantity="1.000",
    )
    with pytest.raises(InventoryWorkbookError, match="SN 重复"):
        inventory_export_lines(
            [_account(), second],
            serials_by_account={ACCOUNT: ("SN-A", "SN-B"), second.stock_account_id: ("SN-A",)},
            ledger_cursor=9,
        )
    with pytest.raises(InventoryWorkbookError, match="范围外"):
        inventory_export_lines(
            [_account()],
            serials_by_account={ACCOUNT: ("SN-A", "SN-B"), second.stock_account_id: ("SN-X",)},
            ledger_cursor=9,
        )
