"""Bounded Excel rendering for an already authorized formal inventory snapshot.

This module never queries stock or OAM. Its caller must prove authorization,
opening establishment, projection integrity and a stable ledger cursor before
supplying lines. No workbook can silently truncate a line or an oversized cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Iterable
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook


MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_ROWS = 20_000
MAX_BYTES = 20 * 1024 * 1024
MAX_CELL_CHARS = 1_024
MAX_TOTAL_TEXT_CHARS = 2_000_000
HEADERS = (
    "库存账本游标", "资产所有组织编码", "资产所有组织", "库位编码", "库位名称",
    "库位类型", "保管人", "物料编码", "物料名称", "基本单位", "成色",
    "可用状态", "批次号", "序列号", "数量", "库存账户ID",
)
_PACKAGE_TIMESTAMP = (2000, 1, 1, 0, 0, 0)


class InventoryWorkbookError(ValueError):
    """Export data exceeds a safe or complete workbook contract."""


@dataclass(frozen=True, slots=True)
class InventoryExportLine:
    owner_org_code: str
    owner_org_name: str
    location_code: str
    location_name: str
    location_type: str
    custodian_name: str | None
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: str
    availability_bucket: str
    lot_no: str | None
    serial_no: str | None
    quantity: str
    stock_account_id: str


def _safe_text(value: str | None) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > MAX_CELL_CHARS:
        raise InventoryWorkbookError("库存报表单元格类型或长度无效")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        raise InventoryWorkbookError("库存报表单元格包含非法控制字符")
    # Spreadsheet clients may interpret leading whitespace before a formula.
    # Apostrophe forces the value to stay text even when a client reopens it.
    if value.lstrip(" \t\r\n\u00a0").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _safe_quantity(value: str) -> str:
    checked = _safe_text(value)
    try:
        quantity = Decimal(checked)
    except (InvalidOperation, ValueError) as exc:
        raise InventoryWorkbookError("库存报表数量无效") from exc
    try:
        valid = (
            quantity.is_finite()
            and quantity >= 0
            and quantity.quantize(Decimal("0.001")) == quantity
        )
    except InvalidOperation:
        valid = False
    if not valid:
        raise InventoryWorkbookError("库存报表数量无效")
    return checked


def render_inventory_workbook(
    lines: Iterable[InventoryExportLine],
    *,
    ledger_cursor: int,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    if isinstance(ledger_cursor, bool) or not isinstance(ledger_cursor, int) or ledger_cursor < 0:
        raise InventoryWorkbookError("库存报表账本游标无效")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_ROWS:
        raise InventoryWorkbookError("库存报表行数上限无效")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise InventoryWorkbookError("库存报表字节上限无效")

    checked_rows = []
    text_chars = 0
    for line in lines:
        if not isinstance(line, InventoryExportLine):
            raise InventoryWorkbookError("库存报表行类型无效")
        if len(checked_rows) >= max_rows:
            raise InventoryWorkbookError("库存报表超过行数上限")
        cells = (
            ledger_cursor,
            _safe_text(line.owner_org_code), _safe_text(line.owner_org_name),
            _safe_text(line.location_code), _safe_text(line.location_name),
            _safe_text(line.location_type), _safe_text(line.custodian_name),
            _safe_text(line.sku_code), _safe_text(line.material_name),
            _safe_text(line.base_unit), _safe_text(line.condition_code),
            _safe_text(line.availability_bucket), _safe_text(line.lot_no),
            _safe_text(line.serial_no), _safe_quantity(line.quantity),
            _safe_text(line.stock_account_id),
        )
        text_chars += sum(len(value) for value in cells if isinstance(value, str))
        if text_chars > MAX_TOTAL_TEXT_CHARS:
            raise InventoryWorkbookError("库存报表文本总量超过上限")
        checked_rows.append(cells)

    workbook = Workbook(write_only=True)
    try:
        # An uncertain OSS PUT is recovered by regenerating and comparing the
        # exact bytes. Both workbook properties and ZIP entry timestamps must
        # therefore be stable for one unchanged stock snapshot.
        workbook.properties.created = datetime(2000, 1, 1)
        workbook.properties.modified = datetime(2000, 1, 1)
        sheet = workbook.create_sheet("正式库存余额")
        sheet.append(("数据来源", "RSC正式库存流水投影", "账本游标", ledger_cursor))
        sheet.append(HEADERS)
        for cells in checked_rows:
            sheet.append(cells)
        output = BytesIO()
        workbook.save(output)
        data = _canonicalize_package(output.getvalue())
        if len(data) > max_bytes:
            raise InventoryWorkbookError("库存报表超过文件大小上限")
        return data
    finally:
        workbook.close()


def _canonicalize_package(payload: bytes) -> bytes:
    canonical = BytesIO()
    with ZipFile(BytesIO(payload), "r") as source, ZipFile(
        canonical, "w", compression=ZIP_DEFLATED, compresslevel=6
    ) as target:
        for original in source.infolist():
            entry = ZipInfo(original.filename, date_time=_PACKAGE_TIMESTAMP)
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o600 << 16
            content = source.read(original)
            if original.filename == "docProps/core.xml":
                root = ElementTree.fromstring(content)
                for name in ("created", "modified"):
                    timestamp = root.find("{http://purl.org/dc/terms/}" + name)
                    if timestamp is None:
                        raise InventoryWorkbookError("库存报表时间元数据缺失")
                    timestamp.text = "2000-01-01T00:00:00Z"
                content = ElementTree.tostring(root, encoding="utf-8")
            target.writestr(entry, content, compress_type=ZIP_DEFLATED, compresslevel=6)
    return canonical.getvalue()
