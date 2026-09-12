"""Resolve a reserved dimension from the engineer's exact available account."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..inventory_models import StockAccount, StockLocation
from .inventory_posting import InventoryPostingError


def resolve_work_order_reserved_lines(db, *, operator_person_id, lines, create):
    """Called under the ledger and current work-order/identity locks.

    New accounts have no opening quantity. The database requires the same
    transaction to supply the original occupy fact and its actual stock posting.
    An optional caller target is only an assertion about an existing dimension.
    """
    resolved = []
    for line in lines:
        source = db.get(StockAccount, line.stock_account_id, populate_existing=True)
        location = db.get(StockLocation, source.location_id, populate_existing=True) if source else None
        if (source is None or source.material_id != line.material_id
                or source.custodian_person_id != operator_person_id
                or source.availability_bucket != "available"
                or source.condition_code != line.condition_before
                or location is None or location.location_type != "personal"
                or location.custodian_person_id != operator_person_id
                or (create and location.status != "active")):
            raise InventoryPostingError("occupy_source_invalid", "conflict", "占用来源必须是本人个人仓的可用物料")
        dimensions = {key: getattr(source, key) for key in (
            "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")}
        dimensions["availability_bucket"] = "reserved"
        query = select(StockAccount).filter_by(**dimensions).limit(2)
        rows = tuple(db.scalars(query))
        if line.target_stock_account_id is not None:
            if len(rows) != 1 or rows[0].id != line.target_stock_account_id:
                raise InventoryPostingError("occupy_target_invalid", "conflict", "预留账户与所选来源库存维度不一致")
        elif not rows and create:
            if db.get_bind().dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            now = datetime.now(timezone.utc)
            db.execute(insert(StockAccount).values(id=uuid4(), **dimensions,
                created_at=now, updated_at=now).on_conflict_do_nothing())
            rows = tuple(db.scalars(query))
        if len(rows) != 1:
            raise InventoryPostingError("occupy_target_missing", "conflict", "原占用目标账户不存在或不唯一，请核验库存记录")
        resolved.append(replace(line, target_stock_account_id=rows[0].id))
    return tuple(resolved)
