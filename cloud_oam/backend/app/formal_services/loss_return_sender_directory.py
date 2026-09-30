"""Read only own loss-derived returns from a stable ledger/audit snapshot."""
from datetime import datetime, timezone
import re
from uuid import UUID
from sqlalchemy import select
from app.stock_operation_models import StockOperationOrder
from app.stock_return_origin_schemas import LossReturnOrigin
from app.stock_return_schemas import StockReturnDestinationOut
from app.loss_return_sender_schemas import LossReturnSenderItemOut, LossReturnSenderDirectoryOut
from app.formal_services import inventory_query as inventory, stock_return_origins as origins
from app.formal_services import stock_return_facts as facts
from app.formal_services.stock_return_plan import authorize
from app.formal_services.work_order_evidence_snapshot import material_audit_cursor
from app.formal_services.work_order_return_sources import _fail, _hash


def list_sender_returns(db, *, actor, limit=25, after_id=None, snapshot_hash=None):
    if (type(limit) is not int or not 1 <= limit <= 50
            or (after_id is not None and not isinstance(after_id, UUID))
            or (snapshot_hash is not None and (not isinstance(snapshot_hash, str)
                or not re.fullmatch('[0-9a-f]{64}', snapshot_hash)))
            or (after_id is not None and snapshot_hash is None)):
        _fail('stock_loss_return_directory_cursor_invalid', 'Return directory cursor is invalid', 422)
    current = authorize(db, actor, 'read')
    with db.no_autoflush:
        snapshot = inventory._projection_snapshot(db)
        audit = material_audit_cursor(db)
        fingerprint = _hash(dict(person_id=str(current.person_id), authorization_version=current.authorization_version,
            ledger_cursor=snapshot.ledger_cursor, audit=[[version, str(event), digest] for version, event, digest in audit]))
        if snapshot_hash is not None and snapshot_hash != fingerprint:
            _fail('stock_loss_return_directory_changed', 'Return directory changed; refresh before continuing', 409)
        query = select(StockOperationOrder).where(StockOperationOrder.operation_type == 'return',
            StockOperationOrder.requester_id == current.person_id,
            StockOperationOrder.loss_headquarters_decision_id.is_not(None))
        if after_id is not None:
            query = query.where(StockOperationOrder.id > after_id)
        rows = tuple(db.scalars(query.order_by(StockOperationOrder.id).limit(limit+1)
            .execution_options(populate_existing=True)))
        items = []
        for order in rows[:limit]:
            proof = origins.verify_return_origin(db, actor=current, order=order)
            if not isinstance(proof, LossReturnOrigin):
                facts.invalid()
            items.append(LossReturnSenderItemOut(operation_no=order.operation_no, origin=proof, reason=order.reason,
                destination=StockReturnDestinationOut.model_validate(order.plan_jsonb['destination'])))
        if material_audit_cursor(db) != audit:
            _fail('stock_loss_return_directory_changed', 'Return directory changed during read', 409)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        refreshed = authorize(db, current, 'read')
        if refreshed.authorization_version != current.authorization_version:
            _fail('stock_loss_return_directory_changed', 'Return reader authority changed', 409)
        return LossReturnSenderDirectoryOut(person_id=current.person_id,
            authorization_version=current.authorization_version, ledger_cursor=snapshot.ledger_cursor,
            snapshot_hash=fingerprint, queried_at=datetime.now(timezone.utc), items=tuple(items),
            next_after_id=rows[limit-1].id if len(rows)>limit else None)
