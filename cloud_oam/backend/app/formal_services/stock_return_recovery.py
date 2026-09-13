"""Recover exact own requests or seal their namespace without replaying stock."""
from datetime import datetime, timezone
import re
from uuid import UUID, uuid4

from sqlalchemy import select

from ..demand_models import OamWorkOrder
from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..inventory_models import InventoryLedgerHead, InventoryTransaction
from ..stock_operation_models import StockOperationOrder as Order, StockOperationCancellation as Cancellation, StockOperationCommandSeal as Seal, StockOperationOutbound as Outbound, StockOperationShipment as ReturnShipment
from ..stock_return_schemas import StockReturnSealOut, StockReturnSealedOut
from . import inventory_posting as posting, inventory_query as inventory, stock_return_facts as facts
from .audit_chain import append_audit_event, AuditChainError
from .postgresql_lock_graph import lock_material_request_work_order
from .stock_return_plan import authorize
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail


def _coordinate(operation_type, request_id, operation_id):
    if (operation_type not in {"submit_return", "cancel_return", "outbound_return", "ship_return"}
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id)
            or (operation_type == "submit_return") != (operation_id is None)):
        _fail("stock_return_lookup_invalid", "退回原请求坐标无效", 422)


def _row(db, actor, request_id):
    return db.scalar(select(Seal).where(Seal.actor_user_id == actor.user_id, Seal.request_id == request_id)
        .execution_options(populate_existing=True))


def require_unsealed(db, *, actor, request_id):
    if _row(db, actor, request_id) is not None:
        _fail("stock_return_request_sealed", "原退回请求已永久封存，请核验原记录后重新准备操作")


def seal_payload(row):
    return {"work_order_id": str(row.oam_work_order_id), "operator_person_id": str(row.operator_person_id),
        "operation_id": str(row.operation_id) if row.operation_id else None, "operation_type": row.operation_type,
        "authorization_version": row.authorization_version, "request_id": row.request_id, "request_hash": row.request_hash}


def verified_seal(db, *, actor, row):
    try:
        return _verified_seal(db, actor=actor, row=row)
    except (AuditChainError, ValueError, TypeError, AttributeError):
        facts.invalid()


def _verified_seal(db, *, actor, row):
    if (row.actor_user_id != actor.user_id or row.operator_person_id != actor.person_id or row.authorization_version < 1
            or row.operation_type not in {"submit_return", "cancel_return", "outbound_return", "ship_return"}
            or (row.operation_type == "submit_return") != (row.operation_id is None)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", row.request_id)
            or not re.fullmatch(r"[0-9a-f]{64}", row.request_hash) or row.request_reference != posting._request_reference(row.request_id)):
        facts.invalid()
    facts.audit(db, actor=actor, stream="material_request", aggregate_type="stock_operation_command_seal", identifier=row.id,
        action="stock_return.command_sealed", request_id="stock-return-seal:" + str(row.id), before={}, after=seal_payload(row))
    event = facts.single(db, AuditEvent, stream_key="material_request", aggregate_type="stock_operation_command_seal", aggregate_id=str(row.id))
    if _aware(event.occurred_at) != _aware(row.created_at) or _aware(event.created_at) != _aware(row.created_at): facts.invalid()
    return StockReturnSealedOut(seal=StockReturnSealOut(seal_id=row.id, operator_person_id=row.operator_person_id,
        work_order_id=row.oam_work_order_id, operation_id=row.operation_id, operation_type=row.operation_type,
        request_id=row.request_id, request_hash=row.request_hash, sealed_at=_aware(row.created_at)))


def _original_order(db, actor, work_order_id, operation_id):
    row = db.get(Order, operation_id, populate_existing=True)
    if (row is None or row.actor_user_id != actor.user_id or row.requester_id != actor.person_id
            or row.oam_work_order_id != work_order_id):
        _fail("stock_return_not_found", "本人原退回单不存在", 404)
    facts.order_result(db, actor=actor, order=row)
    return row


def _require_evidence(db, *, actor, request_id, result, seal):
    expected_domain = set()
    if result is not None:
        if hasattr(result, "shipment_id"): expected_domain.add(("stock_operation_shipment", str(result.shipment_id)))
        elif hasattr(result, "outbound_id"): expected_domain.add(("stock_operation_outbound", str(result.outbound_id)))
        elif hasattr(result, "cancellation_id"): expected_domain.add(("stock_operation_cancellation", str(result.cancellation_id)))
        else: expected_domain.add(("stock_operation_order", str(result.operation_id)))
    domains = set(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == "material_request", AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == request_id, AuditEvent.aggregate_type.in_(("stock_operation_order", "stock_operation_cancellation", "stock_operation_outbound", "stock_operation_shipment")))))
    if domains != expected_domain: facts.invalid()
    reference = posting._request_reference(request_id)
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == "inventory",
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == reference,
        AuditEvent.aggregate_type == "inventory_transaction").limit(101)))
    states = tuple(db.scalars(select(StateTransitionEvent).where(StateTransitionEvent.actor_id == actor.user_id,
        StateTransitionEvent.aggregate_type == "inventory_transaction",
        StateTransitionEvent.metadata_jsonb["request_reference"].as_string() == reference).limit(101)))
    if len(audits) > 100 or len(states) > 100: facts.invalid()
    observed = set()
    for identifier in {event.aggregate_id for event in (*audits, *states)}:
        try: transaction = db.get(InventoryTransaction, UUID(identifier), populate_existing=True)
        except (TypeError, ValueError): facts.invalid()
        if transaction is None or transaction.actor_user_id != actor.user_id: facts.invalid()
        if transaction.source_document_type in {"stock_operation_return", "stock_operation_return_outbound"}: observed.add(transaction.id)
    if result is not None and hasattr(result, "shipment_id") and (audits or states): facts.invalid()
    if observed != ({result.posting_transaction_id} if result is not None and hasattr(result, "posting_transaction_id") else set()): facts.invalid()
    seal_ids = set(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key == "material_request",
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.aggregate_type == "stock_operation_command_seal",
        AuditEvent.after_jsonb["request_id"].as_string() == request_id)))
    if seal_ids != ({str(seal.id)} if seal else set()): facts.invalid()


def lookup_return_request(db, *, actor, work_order_id, operation_type, request_id, operation_id=None):
    _coordinate(operation_type, request_id, operation_id)
    current = authorize(db, actor, "read")
    with db.no_autoflush:
        cursor = db.scalar(select(InventoryLedgerHead.next_cursor).where(InventoryLedgerHead.stream_key == "inventory"))
        if cursor is None: facts.invalid()
        audit = material_audit_cursor(db)
        order = _original_order(db, current, work_order_id, operation_id) if operation_id else None
        records = [(kind, row) for kind, model in (("submit_return", Order), ("cancel_return", Cancellation), ("outbound_return", Outbound), ("ship_return", ReturnShipment))
            for row in db.scalars(select(model).where(model.actor_user_id == current.user_id, model.request_id == request_id)
                .limit(2).execution_options(populate_existing=True))]
        if len(records) > 1: facts.invalid()
        result = None
        if records:
            kind, row = records[0]
            if kind != operation_type or (kind == "submit_return" and row.oam_work_order_id != work_order_id) or (kind != "submit_return" and row.operation_id != operation_id):
                _fail("stock_return_request_conflict", "该请求标识已绑定其他退回操作，请核验原请求坐标")
            if kind == "ship_return":
                from .stock_return_shipment_facts import shipment_result
                result = shipment_result(db, actor=current, fact=row)
            elif kind == "outbound_return":
                from .stock_return_outbound_facts import outbound_result
                result = outbound_result(db, actor=current, fact=row)
            else:
                result = (facts.order_result(db, actor=current, order=row) if kind == "submit_return" else
                    facts.cancellation_result(db, actor=current, order=order, cancellation=row))
        row = _row(db, current, request_id)
        if row:
            if result is not None: facts.invalid()
            if row.oam_work_order_id != work_order_id or row.operation_type != operation_type or row.operation_id != operation_id:
                _fail("stock_return_request_conflict", "该请求已按其他退回坐标封存，请核验原记录")
        _require_evidence(db, actor=current, request_id=request_id, result=result, seal=row)
        output = verified_seal(db, actor=current, row=row) if row else result
        if material_audit_cursor(db) != audit:
            _fail("stock_return_lookup_changed", "退回记录在回查期间变化，请重新查询原请求")
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(cursor - 1, None))
        authorize(db, current, "read")
        return output


def seal_return_request(db, *, actor, work_order_id, operation_type, request_id, request_hash, operation_id=None):
    _coordinate(operation_type, request_id, operation_id)
    if not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash):
        _fail("stock_return_seal_invalid", "原请求内容摘要无效", 422)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current = authorize(db, actor, operation_type)
    lock_material_request_work_order(db, work_order_id)
    if db.get(OamWorkOrder, work_order_id, populate_existing=True) is None:
        _fail("work_order_not_found", "原工单不存在", 404)
    original = lookup_return_request(db, actor=current, work_order_id=work_order_id, operation_type=operation_type,
        request_id=request_id, operation_id=operation_id)
    if original is not None:
        digest = original.seal.request_hash if isinstance(original, StockReturnSealedOut) else original.request_hash
        if digest != request_hash: _fail("stock_return_seal_conflict", "原请求已绑定其他内容，请保留恢复记录核验")
        return original
    now = datetime.now(timezone.utc)
    row = Seal(id=uuid4(), actor_user_id=current.user_id, operator_person_id=current.person_id, oam_work_order_id=work_order_id,
        operation_id=operation_id, operation_type=operation_type, authorization_version=current.authorization_version,
        request_id=request_id, request_reference=posting._request_reference(request_id), request_hash=request_hash, created_at=now)
    db.add(row)
    append_audit_event(db, stream_key="material_request", actor_user_id=current.user_id, action="stock_return.command_sealed",
        aggregate_type="stock_operation_command_seal", aggregate_id=str(row.id), before_jsonb={}, after_jsonb=seal_payload(row),
        request_id="stock-return-seal:" + str(row.id), occurred_at=now, created_at=now)
    db.flush()
    authorize(db, current, operation_type)
    return verified_seal(db, actor=current, row=row)
