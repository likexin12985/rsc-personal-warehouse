"""Resolve or seal one receiver request, never replay an uncertain acceptance."""
from datetime import datetime, timezone
import re
from uuid import uuid4

from sqlalchemy import select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..stock_operation_models import (StockOperationReceipt, StockOperationOrder, StockOperationCancellation,
    StockOperationOutbound, StockOperationShipment, StockOperationCommandSeal)
from ..stock_return_receipt_schemas import StockReturnReceiptSealOut, StockReturnReceiptSealedOut
from . import inventory_posting as posting, inventory_query as inventory, stock_return_facts as returns
from . import stock_return_receipt_facts as facts, stock_return_receipt_plan as plan
from . import stock_return_recovery as shared
from .audit_chain import append_audit_event, lock_audit_chain_head, AuditChainError
from .postgresql_lock_graph import lock_material_request_work_order
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail


def coordinate(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', request_id):
        _fail('stock_return_receipt_lookup_invalid', '原验收请求坐标无效', 422)


def seal_payload(row):
    return {**shared.seal_payload(row), 'shipment_id': str(row.shipment_id)}


def verified_seal(db, *, actor, row, package):
    try:
        if (row.operation_type != 'receive_return' or row.shipment_id != package.shipment_id
                or row.operation_id != package.operation_id or row.oam_work_order_id != package.work_order_id
                or row.actor_user_id != actor.user_id or row.operator_person_id != actor.person_id
                or row.authorization_version < 1 or row.request_reference != posting._request_reference(row.request_id)
                or not package.recorded_at <= _aware(row.created_at) <= datetime.now(timezone.utc)
                or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id)
                or not re.fullmatch(r'[a-f0-9]{64}', row.request_hash)): facts.invalid()
        returns.audit(db, actor=actor, stream='material_request', aggregate_type='stock_operation_command_seal', identifier=row.id,
            action='stock_return.command_sealed', request_id='stock-return-seal:' + str(row.id), before={}, after=seal_payload(row))
        event = returns.single(db, AuditEvent, stream_key='material_request', aggregate_type='stock_operation_command_seal', aggregate_id=str(row.id))
        if _aware(event.occurred_at) != _aware(row.created_at) or _aware(event.created_at) != _aware(row.created_at): facts.invalid()
        return StockReturnReceiptSealedOut(seal=StockReturnReceiptSealOut(seal_id=row.id, shipment_id=row.shipment_id,
            operation_id=row.operation_id, work_order_id=row.oam_work_order_id, operator_person_id=row.operator_person_id,
            request_id=row.request_id, request_hash=row.request_hash, sealed_at=_aware(row.created_at)))
    except (ValueError, TypeError, AttributeError, AuditChainError): facts.invalid()


def _evidence(db, actor, request_id, fact, seal):
    domain = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'material_request', AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == request_id)))
    if domain != ((('stock_operation_receipt', str(fact.id)),) if fact else ()): facts.invalid()
    seal_ids = tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key == 'material_request',
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.aggregate_type == 'stock_operation_command_seal',
        AuditEvent.after_jsonb['request_id'].as_string() == request_id)))
    if seal_ids != ((str(seal.id),) if seal else ()): facts.invalid()
    reference = posting._request_reference(request_id)
    if (db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == 'inventory',
            AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == reference).limit(1))
            or db.scalar(select(StateTransitionEvent.id).where(StateTransitionEvent.aggregate_type == 'inventory_transaction',
                StateTransitionEvent.actor_id == actor.user_id,
                StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference).limit(1))): facts.invalid()


def lookup_receipt_request(db, *, actor, shipment_id, request_id):
    coordinate(request_id)
    with db.no_autoflush:
        snapshot = inventory._projection_snapshot(db); audit = material_audit_cursor(db)
        current, detail = plan.authorize(db, actor, shipment_id, action='read')
        from ..stock_operation_models import StockOperationReturnInbound, StockOperationReturnInboundSeal
        for model in (StockOperationOrder, StockOperationCancellation, StockOperationOutbound, StockOperationShipment,
                StockOperationReturnInbound, StockOperationReturnInboundSeal):
            if db.scalar(select(model.id).where(model.actor_user_id == current.user_id, model.request_id == request_id).limit(1)):
                _fail('stock_return_request_conflict', '原请求标识已绑定其他退回操作，请核验准确坐标')
        fact = db.scalar(select(StockOperationReceipt).where(StockOperationReceipt.actor_user_id == current.user_id,
            StockOperationReceipt.request_id == request_id).execution_options(populate_existing=True))
        seal = shared._row(db, current, request_id)
        if fact is not None and seal is not None: facts.invalid()
        if (fact is not None and fact.shipment_id != shipment_id or seal is not None and
                (seal.shipment_id != shipment_id or seal.operation_type != 'receive_return')):
            _fail('stock_return_request_conflict', '原请求已绑定其他包裹或操作，请核验准确坐标')
        _evidence(db, current, request_id, fact, seal)
        result = (facts.receipt_result(db, actor=current, fact=fact) if fact else
            verified_seal(db, actor=current, row=seal, package=detail.package) if seal else None)
        final_actor, final_detail = plan.authorize(db, current, shipment_id, action='read')
        if current != final_actor or detail.package != final_detail.package or material_audit_cursor(db) != audit:
            _fail('stock_return_receipt_lookup_changed', '验收、权限或接收责任在回查期间变化，请查询原请求')
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return result


def seal_receipt_request(db, *, actor, shipment_id, request_id, request_hash):
    coordinate(request_id)
    if not isinstance(request_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', request_hash):
        _fail('stock_return_receipt_seal_invalid', '原验收请求内容摘要无效', 422)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current, detail = plan.authorize(db, actor, shipment_id)
    lock_material_request_work_order(db, detail.package.work_order_id)
    lock_audit_chain_head(db, stream_key='material_request')
    previous = lookup_receipt_request(db, actor=current, shipment_id=shipment_id, request_id=request_id)
    if previous is not None:
        digest = previous.seal.request_hash if isinstance(previous, StockReturnReceiptSealedOut) else previous.request_hash
        if digest != request_hash: _fail('stock_return_receipt_seal_conflict', '原请求已绑定其他内容，请保留恢复记录核验')
        return previous
    now = datetime.now(timezone.utc)
    row = StockOperationCommandSeal(id=uuid4(), operation_type='receive_return', shipment_id=shipment_id,
        operation_id=detail.package.operation_id, oam_work_order_id=detail.package.work_order_id,
        actor_user_id=current.user_id, operator_person_id=current.person_id, authorization_version=current.authorization_version,
        request_id=request_id, request_reference=posting._request_reference(request_id), request_hash=request_hash, created_at=now)
    db.add(row)
    append_audit_event(db, stream_key='material_request', actor_user_id=current.user_id, action='stock_return.command_sealed',
        aggregate_type='stock_operation_command_seal', aggregate_id=str(row.id), before_jsonb={}, after_jsonb=seal_payload(row),
        request_id='stock-return-seal:' + str(row.id), occurred_at=now, created_at=now)
    db.flush()
    return lookup_receipt_request(db, actor=current, shipment_id=shipment_id, request_id=request_id)
