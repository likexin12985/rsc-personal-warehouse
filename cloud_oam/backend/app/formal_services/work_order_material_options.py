"""Own stock choices, with per-work-order occupancy and one ledger snapshot.

This response is an observation, not a reservation or proof of physical scan.
Commands must still revalidate the whole batch and all three supplied codes.
"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from ..inventory_models import InventorySerial, SerialCurrentPosition
from ..work_order_query_schemas import (
    WorkOrderMaterialOptionOut, WorkOrderMaterialOptionsOut, WorkOrderSerialOptionOut,
)
from . import inventory_query as inventory
from .inventory_posting import InventoryPostingError, _require_current_actor
from .serial_ledger import SerialLedgerError, rebuild_serial_states
from .work_order_query import get_my_work_order
from .work_order_reservations import read_work_order_reservations


def material_options(db, *, actor, work_order_id):
    current = _require_current_actor(db, actor)
    with db.no_autoflush:
        order = get_my_work_order(db, actor=current, work_order_id=work_order_id)
        warehouse = inventory.personal_warehouse(db, actor=current)
        snapshot = inventory._ProjectionSnapshot(warehouse.ledger_cursor, warehouse.projected_at)
        try:
            items = _material_items(db, order=order, warehouse=warehouse)
        except (InventoryPostingError, inventory.InventoryReadError, SerialLedgerError) as exc:
            # A concurrent movement must remain a retryable snapshot change,
            # not be misreported as broken history or a missing serial.
            inventory._ensure_projection_snapshot_current(db, snapshot)
            if isinstance(exc, SerialLedgerError):
                inventory._invalid_current_projection()
            raise
        inventory._ensure_projection_snapshot_current(db, snapshot)
        # Identity or source changes during the stock read discard all choices.
        latest = get_my_work_order(db, actor=current, work_order_id=work_order_id)
        if latest != order:
            raise inventory.InventoryReadError(code="work_order_projection_changed", status_code=409,
                message="工单来源在读取期间发生变化，请刷新工单和物料")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        _require_current_actor(db, current)
    return WorkOrderMaterialOptionsOut(
        work_order=order, person_id=current.person_id, authorization_version=current.authorization_version,
        projection_status=warehouse.projection_status, opening_balance_status=warehouse.opening_balance_status,
        projected_at=warehouse.projected_at, ledger_cursor=warehouse.ledger_cursor,
        location_id=warehouse.location_id, location_code=warehouse.location_code,
        location_name=warehouse.location_name, location_status=warehouse.location_status,
        custody_effective_from=warehouse.custody_effective_from, items=items,
    )


def _material_items(db, *, order, warehouse):
    if warehouse.opening_balance_status != "established":
        return ()
    accounts = {row.stock_account_id: row for row in warehouse.items
                if row.availability_bucket in {"available", "reserved"}
                and row.condition_code in {"new", "used", "damaged"}}
    reserved = read_work_order_reservations(db, work_order_id=order.work_order_id,
        account_ids={key for key, row in accounts.items() if row.availability_bucket == "reserved"},
        before_cursor=warehouse.ledger_cursor + 1)
    positions = tuple(db.execute(select(InventorySerial, SerialCurrentPosition).join(
        SerialCurrentPosition, SerialCurrentPosition.serial_id == InventorySerial.id
    ).where(SerialCurrentPosition.stock_account_id.in_(accounts))))
    expected_serials = {sn for row in reserved.values() for sn in row.serial_ids}
    states = rebuild_serial_states(db, {row.id for row, _ in positions} | expected_serials,
                                   through_cursor=warehouse.ledger_cursor)
    by_account = defaultdict(dict)
    for serial, position in positions:
        account = accounts[position.stock_account_id]
        state = states[serial.id]
        if (serial.lifecycle_status != "active" or state.lifecycle_status != "active"
                or state.stock_account_id != account.stock_account_id
                or state.last_movement_id != position.last_movement_id
                or serial.material_id != account.material_id or serial.lot_id != account.lot_id):
            inventory._invalid_current_projection()
        by_account[account.stock_account_id][serial.id] = serial
    items = []
    for identifier, account in sorted(accounts.items(), key=lambda item: (item[1].sku_code, str(item[0]))):
        if account.quantity_status != "available" or account.quantity is None:
            inventory._invalid_current_projection()
        quantity = Decimal(account.quantity)
        serials = by_account[identifier]
        if account.availability_bucket == "reserved":
            remaining = reserved[identifier]
            if remaining.quantity > quantity or not remaining.serial_ids <= serials.keys():
                inventory._invalid_current_projection()
            quantity = remaining.quantity
            serials = {key: serials[key] for key in remaining.serial_ids}
        if ((account.tracking_mode in {"serial", "lot_and_serial"} and quantity != len(serials))
                or (account.tracking_mode not in {"serial", "lot_and_serial"} and serials)):
            inventory._invalid_current_projection()
        if quantity <= 0:
            continue
        actions = (("occupy",) if account.availability_bucket == "available"
                   else ("consume", "release", "replace")) if order.can_operate else ()
        items.append(WorkOrderMaterialOptionOut(**account.model_dump(),
            selectable_quantity=format(quantity, ".3f"), allowed_actions=actions,
            serials=tuple(WorkOrderSerialOptionOut(serial_id=row.id, serial_no=row.serial_no)
                          for row in sorted(serials.values(), key=lambda row: (row.serial_no, str(row.id))))))
    return tuple(items)
