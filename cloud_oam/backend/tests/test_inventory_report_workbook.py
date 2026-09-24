from io import BytesIO
import time
from zipfile import ZipFile

from openpyxl import load_workbook
import pytest

from app.formal_services.inventory_report_workbook import (
    InventoryExportLine,
    InventoryWorkbookError,
    render_inventory_workbook,
)


def _line(**overrides):
    values = dict(owner_org_code="HQ", owner_org_name="总部",
        location_code="A", location_name="库位", location_type="warehouse",
        custodian_name=None, sku_code="SKU1", material_name="备件",
        base_unit="件", condition_code="good", availability_bucket="available",
        lot_no=None, serial_no=None, quantity="2.000", stock_account_id="account-1")
    values.update(overrides)
    return InventoryExportLine(**values)


def test_workbook_keeps_source_cursor_and_text_without_formula_execution():
    data = render_inventory_workbook([_line(material_name=" =HYPERLINK(\"x\")")], ledger_cursor=17)
    book = load_workbook(BytesIO(data), read_only=True, data_only=False)
    try:
        rows = list(book.active.values)
        assert rows[0] == ("数据来源", "RSC正式库存流水投影", "账本游标", 17)
        assert rows[2][0] == 17
        assert rows[2][8] == "' =HYPERLINK(\"x\")"
        assert book.active["I3"].data_type == "s"
        assert rows[2][14] == "2.000"
    finally:
        book.close()


def test_workbook_bytes_remain_identical_across_seconds_for_exact_oss_recovery():
    first = render_inventory_workbook([_line()], ledger_cursor=17)
    time.sleep(2.1)
    second = render_inventory_workbook([_line()], ledger_cursor=17)
    assert second == first
    with ZipFile(BytesIO(first)) as package:
        assert {info.date_time for info in package.infolist()} == {(2000, 1, 1, 0, 0, 0)}


@pytest.mark.parametrize("kwargs, expected", [
    ({"max_rows": 1}, "行数"),
    ({"max_bytes": 1}, "文件大小"),
])
def test_workbook_fails_closed_on_limits(kwargs, expected):
    lines = [_line(), _line(stock_account_id="account-2")]
    with pytest.raises(InventoryWorkbookError, match=expected):
        render_inventory_workbook(lines, ledger_cursor=0, **kwargs)


def test_workbook_rejects_oversize_and_control_cells():
    for name in ("x" * 1025, "bad\x01cell"):
        with pytest.raises(InventoryWorkbookError):
            render_inventory_workbook([_line(material_name=name)], ledger_cursor=0)


@pytest.mark.parametrize("quantity", ["=1+1", "-1", "NaN", "1.0001", "1e999"])
def test_workbook_rejects_invalid_quantity(quantity):
    with pytest.raises(InventoryWorkbookError, match="数量无效"):
        render_inventory_workbook([_line(quantity=quantity)], ledger_cursor=0)
