"""Read or seal an own reversal request without replaying a stock command."""
import re
from uuid import UUID

from sqlalchemy import select

from ..demand_models import WorkOrderReversal
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..inventory_models import InventoryTransaction
from . import work_order_command_seal as seals
from .inventory_posting import _request_reference, _require_current_actor
from .work_order_reversal_read import reversal_result, _invalid


def _require_request(request_id):
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id):
        seals._fail("work_order_reversal_lookup_invalid", "原冲销请求标识无效", "invalid_request")


def _require_request_evidence(db, *, actor, work_order_id, request_id, result, seal):
    """Orphaned evidence is an error, never proof that a request did not run."""
    parent_ids = set(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == "material_request", AuditEvent.aggregate_type == "work_order_material_reversal",
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == request_id)))
    if parent_ids != ({str(result.reversal_id)} if result else set()):
        _invalid()
    reference = _request_reference(request_id)
    events = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.stream_key == "inventory",
        AuditEvent.aggregate_type == "inventory_transaction", AuditEvent.action == "inventory.transaction.reversed",
        AuditEvent.request_id == reference).limit(101)))
    states = tuple(db.scalars(select(StateTransitionEvent).where(
        StateTransitionEvent.actor_id == actor.user_id, StateTransitionEvent.aggregate_type == "inventory_transaction",
        StateTransitionEvent.reason == "inventory_transaction_reversed",
        StateTransitionEvent.metadata_jsonb["request_reference"].as_string() == reference).limit(101)))
    if len(events) > 100 or len(states) > 100:
        _invalid()
    observed = set()
    for identifier in {row.aggregate_id for row in (*events, *states)}:
        try:
            transaction = db.get(InventoryTransaction, UUID(identifier), populate_existing=True)
        except (ValueError, TypeError):
            _invalid()
        if transaction is None or transaction.actor_user_id != actor.user_id:
            _invalid()
        if transaction.source_document_type == "work_order_material" and transaction.source_document_id == str(work_order_id):
            observed.add(transaction.id)
    if observed != ({item.inverse_transaction_id for item in result.items} if result else set()):
        _invalid()
    seal_ids = set(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == "material_request", AuditEvent.aggregate_type == "work_order_command_seal",
        AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.after_jsonb["work_order_id"].as_string() == str(work_order_id),
        AuditEvent.after_jsonb["operation_type"].as_string() == "reverse",
        AuditEvent.after_jsonb["request_id"].as_string() == request_id)))
    if seal_ids != ({str(seal.id)} if seal else set()):
        _invalid()


def lookup_reversal(db, *, actor, work_order_id, request_id):
    """Own history survives work-order reassignment/closure; absence is provisional."""
    _require_request(request_id)
    current = _require_current_actor(db, actor)
    if not current.allows(db, "work_order_material", "read", target_scope_type="person", target_scope_id=str(current.person_id)):
        seals._fail("work_order_forbidden", "没有本人工单操作读取权限", "forbidden")
    with db.no_autoflush:
        parents = tuple(db.scalars(select(WorkOrderReversal).where(
            WorkOrderReversal.actor_user_id == current.user_id, WorkOrderReversal.request_id == request_id
        ).limit(2).execution_options(populate_existing=True)))
        if len(parents) > 1:
            _invalid()
        if parents and parents[0].oam_work_order_id != work_order_id:
            seals._fail("request_id_conflict", "该请求标识已绑定其他工单冲销，请核验原请求坐标")
        result = reversal_result(db, actor=current, parent=parents[0]) if parents else None
        row = seals._row(db, actor=current, work_order_id=work_order_id, operation_type="reverse", request_id=request_id)
        if row is not None and result is not None:
            _invalid()
        _require_request_evidence(db, actor=current, work_order_id=work_order_id, request_id=request_id, result=result, seal=row)
        output = seals._verified_seal(db, actor=current, row=row) if row else result
        _require_current_actor(db, current)
        return output


def seal_reversal(db, *, actor, work_order_id, request_id, request_hash):
    _require_request(request_id)
    if not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash):
        seals._fail("work_order_seal_input_invalid", "原冲销内容摘要无效", "invalid_request")
    current = seals._lock_request(db, actor=actor, work_order_id=work_order_id)
    original = lookup_reversal(db, actor=current, work_order_id=work_order_id, request_id=request_id)
    if original is not None:
        digest = original.seal.request_hash if hasattr(original, "seal") else original.request_hash
        if digest != request_hash:
            seals._fail("work_order_seal_request_conflict", "原冲销已绑定其他内容，请保留恢复记录核验")
        return original
    return seals._write_seal(db, current=current, work_order_id=work_order_id,
        operation_type="reverse", request_id=request_id, request_hash=request_hash)
