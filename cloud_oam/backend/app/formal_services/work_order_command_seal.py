"""Resolve or permanently seal one own ordinary command without replaying it."""
from datetime import datetime, timezone
import re
from uuid import uuid4

from sqlalchemy import select

from ..demand_models import WorkOrderCommandSeal, OamWorkOrder
from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent
from ..work_order_material_schemas import WorkOrderMaterialSealOut, WorkOrderMaterialSealedLookupOut
from . import work_order_material as material
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError
from .inventory_posting import _request_reference, _require_current_actor, _lock_inventory_ledger_head_for_atomic_batch
from .postgresql_lock_graph import lock_material_request_work_order
from .work_order_operation_read import lookup_operation


def _fail(code, message, category="conflict"):
    raise material.WorkOrderMaterialPreflightError(code, message, category)


def _row(db, *, actor, work_order_id, operation_type, request_id):
    with db.no_autoflush:
        return db.scalar(select(WorkOrderCommandSeal).where(
            WorkOrderCommandSeal.actor_user_id == actor.user_id,
            WorkOrderCommandSeal.oam_work_order_id == work_order_id,
            WorkOrderCommandSeal.operation_type == operation_type,
            WorkOrderCommandSeal.request_id == request_id,
        ).execution_options(populate_existing=True))


def require_unsealed_request(db, *, actor, work_order_id, operation_type, request_id):
    # Caller holds ledger -> current actor -> exact OAM work-order locks. Any
    # matching tombstone vetoes a late write, even with a different posting key.
    if _row(db, actor=actor, work_order_id=work_order_id, operation_type=operation_type, request_id=request_id):
        _fail("work_order_request_sealed", "原请求已永久封存，请核验原结果后重新准备操作", "precondition_failed")


def _payload(row):
    return {"work_order_id": str(row.oam_work_order_id), "operator_person_id": str(row.operator_person_id),
            "operation_type": row.operation_type, "request_id": row.request_id,
            "request_hash": row.request_hash, "authorization_version": row.authorization_version}


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _verified_seal(db, *, actor, row):
    invalid = (row.operator_person_id != actor.person_id or row.actor_user_id != actor.user_id
        or row.request_reference != _request_reference(row.request_id)
        or not re.fullmatch(r"[0-9a-f]{64}", row.request_hash)
        or row.operation_type not in {"occupy", "consume", "release"}
        or row.authorization_version < 1 or _utc(row.created_at) != _utc(row.sealed_at))
    with db.no_autoflush:
        events = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == "material_request",
            AuditEvent.aggregate_type == "work_order_command_seal", AuditEvent.aggregate_id == str(row.id)).limit(2)))
    if invalid or len(events) != 1:
        _fail("work_order_seal_evidence_invalid", "原请求封存证据不一致，请保留记录核验", "service_unavailable")
    event = events[0]
    if (event.action != "work_order_material.command_sealed" or event.actor_user_id != actor.user_id
            or event.request_id != "work-order-seal:" + str(row.id) or event.before_jsonb != {}
            or event.after_jsonb != _payload(row) or _utc(event.occurred_at) != _utc(row.sealed_at)):
        _fail("work_order_seal_evidence_invalid", "原请求封存证据不一致，请保留记录核验", "service_unavailable")
    try:
        verify_audit_event_in_read_snapshot(db, stream_key="material_request", event_id=event.id)
    except AuditChainError:
        _fail("work_order_seal_evidence_invalid", "原请求封存审计未通过核验", "service_unavailable")
    return WorkOrderMaterialSealedLookupOut(seal=WorkOrderMaterialSealOut(
        seal_id=row.id, work_order_id=row.oam_work_order_id, operator_person_id=row.operator_person_id,
        operation_type=row.operation_type, request_id=row.request_id, request_hash=row.request_hash,
        sealed_at=row.sealed_at if row.sealed_at.tzinfo else row.sealed_at.replace(tzinfo=timezone.utc)))


def lookup_command_result(db, *, actor, work_order_id, operation_type, request_id):
    original = lookup_operation(db, actor=actor, work_order_id=work_order_id,
                                operation_type=operation_type, request_id=request_id)
    current = _require_current_actor(db, actor)
    row = _row(db, actor=current, work_order_id=work_order_id, operation_type=operation_type, request_id=request_id)
    if row is None:
        return original
    if original.lookup_status == "confirmed":
        _fail("work_order_seal_evidence_invalid", "原请求同时存在过账和封存记录，禁止继续操作", "service_unavailable")
    result = _verified_seal(db, actor=current, row=row)
    _require_current_actor(db, current)
    return result


def seal_command(db, *, actor, work_order_id, operation_type, request_id, request_hash):
    if (operation_type not in {"occupy", "consume", "release"}
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id)
            or not isinstance(request_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", request_hash)):
        _fail("work_order_seal_input_invalid", "原请求封存坐标无效", "invalid_request")
    _lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current = _require_current_actor(db, actor)
    if not current.allows(db, "work_order_material", "operate", target_scope_type="person", target_scope_id=str(current.person_id)):
        _fail("work_order_forbidden", "没有本人工单操作权限", "forbidden")
    lock_material_request_work_order(db, work_order_id)
    # Assignment may have changed. This seals only the current user's original
    # namespace; it cannot operate the new assignee's inventory or requests.
    if db.get(OamWorkOrder, work_order_id, populate_existing=True) is None:
        _fail("work_order_not_found", "原工单不存在", "not_found")
    original = lookup_command_result(db, actor=current, work_order_id=work_order_id,
                                     operation_type=operation_type, request_id=request_id)
    if original.lookup_status != "not_observed":
        digest = original.command.request_hash if original.lookup_status == "confirmed" else original.seal.request_hash
        if digest != request_hash:
            _fail("work_order_seal_request_conflict", "原请求已绑定其他内容，请保留恢复记录核验")
        return original
    now = datetime.now(timezone.utc)
    row = WorkOrderCommandSeal(id=uuid4(), oam_work_order_id=work_order_id,
        actor_user_id=current.user_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, operation_type=operation_type,
        request_id=request_id, request_reference=_request_reference(request_id), request_hash=request_hash,
        sealed_at=now, created_at=now)
    db.add(row)
    append_audit_event(db, stream_key="material_request", actor_user_id=current.user_id,
        action="work_order_material.command_sealed", aggregate_type="work_order_command_seal", aggregate_id=str(row.id),
        before_jsonb={}, after_jsonb=_payload(row), request_id="work-order-seal:" + str(row.id), occurred_at=now, created_at=now)
    db.flush()
    _require_current_actor(db, current)
    return _verified_seal(db, actor=current, row=row)
