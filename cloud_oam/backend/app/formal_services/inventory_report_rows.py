"""Convert a proved inventory read snapshot into complete Excel detail lines.

The caller supplies rows from the formal inventory reader and serial positions
from the same ledger cursor. This module checks representation conservation;
it does not grant access or infer an opening balance from an OAM mirror.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping, Sequence
from uuid import UUID

from ..inventory_schemas import InventoryAccountOut
from .inventory_report_workbook import (
    InventoryExportLine,
    InventoryWorkbookError,
    MAX_ROWS,
    _safe_quantity,
    _safe_text,
)


_SERIAL_TRACKING = frozenset({"serial", "lot_and_serial"})
_NON_SERIAL_TRACKING = frozenset({"none", "lot"})


def inventory_export_lines(
    accounts: Iterable[InventoryAccountOut],
    *,
    serials_by_account: Mapping[UUID, Sequence[str]],
    ledger_cursor: int,
) -> tuple[InventoryExportLine, ...]:
    """Reject omissions, duplicates and a quantity/SN mismatch before XLSX.

    One tracked SN produces one quantity-1 row. An empty tracked account still
    produces one zero row so its stock dimension remains visible in the report.
    """

    if type(ledger_cursor) is not int or ledger_cursor < 0:
        raise InventoryWorkbookError("库存报表账本游标无效")
    lines: list[InventoryExportLine] = []
    account_ids: set[UUID] = set()
    material_serials: set[tuple[UUID, str]] = set()
    for account in accounts:
        if not isinstance(account, InventoryAccountOut):
            raise InventoryWorkbookError("库存报表账户行类型无效")
        if account.stock_account_id in account_ids:
            raise InventoryWorkbookError("库存报表账户重复")
        account_ids.add(account.stock_account_id)
        if (
            account.quantity_status != "available"
            or account.quantity is None
            or account.ledger_cursor > ledger_cursor
        ):
            raise InventoryWorkbookError("库存报表期初或账本快照无效")
        quantity = Decimal(_safe_quantity(account.quantity))
        if account.tracking_mode not in _SERIAL_TRACKING | _NON_SERIAL_TRACKING:
            raise InventoryWorkbookError("库存报表物料追踪模式无效")
        serials = tuple(serials_by_account.get(account.stock_account_id, ()))
        if account.tracking_mode in _SERIAL_TRACKING:
            if quantity != quantity.to_integral_value() or len(serials) != int(quantity):
                raise InventoryWorkbookError("库存报表 SN 件数与数量不一致")
            for serial_no in serials:
                if not isinstance(serial_no, str) or not serial_no.strip():
                    raise InventoryWorkbookError("库存报表 SN 无效")
                _safe_text(serial_no)
                key = (account.material_id, serial_no)
                if key in material_serials:
                    raise InventoryWorkbookError("库存报表 SN 重复")
                material_serials.add(key)
            details = tuple((serial_no, "1.000") for serial_no in sorted(serials))
            if not details:
                details = ((None, "0.000"),)
        else:
            if serials:
                raise InventoryWorkbookError("库存报表非 SN 物料带有 SN")
            details = ((None, account.quantity),)
        if len(lines) + len(details) > MAX_ROWS:
            raise InventoryWorkbookError("库存报表超过行数上限")
        for serial_no, detail_quantity in details:
            lines.append(InventoryExportLine(
                owner_org_code=account.owner_org_code,
                owner_org_name=account.owner_org_name,
                location_code=account.location_code,
                location_name=account.location_name,
                location_type=account.location_type,
                custodian_name=account.custodian_person_name,
                sku_code=account.sku_code,
                material_name=account.material_name,
                base_unit=account.base_unit,
                condition_code=account.condition_code,
                availability_bucket=account.availability_bucket,
                lot_no=account.lot_no,
                serial_no=serial_no,
                quantity=detail_quantity,
                stock_account_id=str(account.stock_account_id),
            ))
    if set(serials_by_account) - account_ids:
        raise InventoryWorkbookError("库存报表 SN 引用了范围外账户")
    return tuple(lines)
