"""Resolve an exact receiving dimension without mutating a preview.

Creation belongs to the complete inbound transaction. The database account
admission trigger independently proves that exact first receipt and posting.
"""
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..inventory_models import StockAccount, StockLocation
from .work_order_return_sources import _fail, _hash


def dimensions(*, source: StockAccount, location: StockLocation, person_id: UUID, condition_code: str | None = None) -> dict:
    if source.owner_org_id != location.owner_org_id:
        _fail('stock_return_inbound_owner_mismatch', '退回在途账户与接收仓组织不一致')
    condition = source.condition_code if condition_code is None else condition_code
    if condition not in (source.condition_code, 'damaged') or condition not in ('new', 'used', 'damaged'):
        _fail('stock_return_inbound_condition_invalid', '验收入库仅允许保留原成色或转为坏件')
    return dict(owner_org_id=location.owner_org_id, custodian_person_id=person_id,
        location_id=location.id, material_id=source.material_id,
        condition_code=condition, availability_bucket='available', lot_id=source.lot_id)


def resolve_target(db: Session, *, source: StockAccount, location: StockLocation,
                   person_id: UUID, condition_code: str | None = None) -> StockAccount:
    """Reuse the existing exact account, or return a transient proposed account.

    A stable, server-derived ID allows the existing plan hash and recovery
    contract to describe a not-yet-created dimension. Never add/flush it here.
    The location and custody have already been authorized by the caller.
    """
    fields = dimensions(source=source, location=location, person_id=person_id, condition_code=condition_code)
    with db.no_autoflush:
        rows = tuple(db.scalars(select(StockAccount).filter_by(**fields).limit(2)
            .execution_options(populate_existing=True)))
        if len(rows) > 1:
            _fail('stock_return_inbound_target_missing', '接收仓目标库存账户不唯一', 412)
        if rows:
            return rows[0]
        key = _hash({name: str(value) if value is not None else None for name, value in fields.items()})
        identifier = uuid5(NAMESPACE_URL, 'rsc:stock-return-inbound-account:v1:' + key)
        if db.get(StockAccount, identifier, populate_existing=True) is not None:
            _fail('stock_return_inbound_target_conflict', '接收仓库存账户标识与维度冲突', 412)
        return StockAccount(id=identifier, **fields)


def materialize_targets(db: Session, *, plan: dict, created_at: datetime) -> None:
    """Create only recomputed targets after the ledger lock and plan check.

    The caller owns the complete transaction. Deferred account admission at
    COMMIT must independently prove the exact receipt, inbound and first
    movement. An empty account is never an independently committable result.
    """
    location = db.get(StockLocation, plan['target_location_id'], populate_existing=True)
    if location is None:
        _fail('stock_return_inbound_target_invalid', '退回接收仓不存在')
    resolved = {}
    for line in plan['lines']:
        source = db.get(StockAccount, UUID(line['source_account_id']), populate_existing=True)
        if source is None:
            _fail('stock_return_inbound_target_invalid', '退回在途账户不存在')
        target = resolve_target(db, source=source, location=location,
            person_id=plan['operator_person_id'], condition_code=line['condition_code'])
        if str(target.id) != line['target_account_id']:
            _fail('stock_return_inbound_plan_changed', '接收仓目标账户已变化，请重新核验')
        previous = resolved.get(target.id)
        if previous is not None:
            continue
        resolved[target.id] = target
    # Resolve every line before introducing any pending ORM objects. Multiple
    # receipt lines of one dimension share one account and one first balance.
    for target in resolved.values():
        if target not in db:
            target.created_at = created_at
            target.updated_at = created_at
            db.add(target)
    db.flush()
