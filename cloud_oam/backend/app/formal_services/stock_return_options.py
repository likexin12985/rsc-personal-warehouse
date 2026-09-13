"""Read exact own recovery sources and currently bound regional return routes."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..inventory_models import StockLocation
from ..stock_return_schemas import StockReturnOptionsOut
from .stock_return_plan import authorize, destination
from .work_order_return_sources import return_sources, _fail
from .work_order_evidence_snapshot import material_audit_cursor
from . import inventory_query as inventory


def _destinations(db, actor, sources):
    if sources.location_id is None or sources.blockers:
        return ()
    source = db.get(StockLocation, sources.location_id, populate_existing=True)
    if source is None:
        _fail("stock_return_destination_invalid", "个人仓位置不存在，请核验库位配置")
    rows = tuple(db.scalars(select(StockLocation.id).where(StockLocation.parent_id == source.parent_id,
        StockLocation.owner_org_id == source.owner_org_id, StockLocation.location_type == "transit",
        StockLocation.status == "active").order_by(StockLocation.id).limit(101)))
    if len(rows) > 100:
        _fail("stock_return_destination_limit", "退回在途位置过多，请先核验区域仓配置")
    at = datetime.now(timezone.utc)
    return tuple(destination(db, person_id=actor.person_id, source_location_id=source.id,
        target_location_id=source.parent_id, transit_location_id=identifier, at=at) for identifier in rows)


def return_options(db, *, actor, work_order_id):
    current = authorize(db, actor, "submit_return")
    with db.no_autoflush:
        audit = material_audit_cursor(db)
        sources = return_sources(db, actor=current, work_order_id=work_order_id)
        destinations = _destinations(db, current, sources)
        latest = return_sources(db, actor=current, work_order_id=work_order_id)
        if (sources.model_dump(exclude={"queried_at"}) != latest.model_dump(exclude={"queried_at"})
                or destinations != _destinations(db, current, latest) or material_audit_cursor(db) != audit):
            _fail("stock_return_options_changed", "退回来源、接收仓或保管关系在读取期间变化，请刷新后重新选择")
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(latest.ledger_cursor, None))
        authorize(db, current, "submit_return")
        return StockReturnOptionsOut(sources=latest, destinations=destinations)
