"""Bind each appended shipment/receipt to one immutable request version."""
import hashlib
import json
from datetime import timezone
from uuid import uuid4

from sqlalchemy import select

from ..demand_models import MaterialRequestCommand
from ..foundation_models import AuditEvent
from .audit_chain import append_audit_event
from .material_request_inbound_state import personal_inbound_state
from .material_request_query import MaterialRequestReadError

SCHEMA = "rsc.material_request_fulfillment_command.v1"


def _time(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _document(request_id, version, operation, fact):
    return {"schema": SCHEMA, "operation": operation, "request_id": str(request_id),
            "target_version": version, "fact_id": str(fact.id), "payload_sha256": fact.request_hash}


def record_fulfillment_command(db, *, request, actor, operation, fact, request_reference, permission_action):
    if operation not in {"shipment", "receipt"}:
        raise ValueError("Unsupported fulfillment command")
    assignments = sorted({entry.assignment_id for entry in actor.entitlements
        if entry.resource == "material_request" and entry.action == permission_action and entry.effect == "allow"}, key=str)
    if not assignments:
        raise MaterialRequestReadError("fulfillment_permission_missing", "forbidden", "没有当前履约动作权限")
    target = request.version + 1
    state = personal_inbound_state(db, request)
    result = {"schema_version": "1.0", "operation": operation, "request_id": str(request.id),
              "target_version": target, "fact_id": str(fact.id), "personal_inbound_status": state}
    command = MaterialRequestCommand(id=uuid4(), operation=operation, request_id=request.id,
        target_version=target, idempotency_key_hash=fact.idempotency_key_hash,
        request_reference=request_reference, request_hash=fact.request_hash,
        result_hash=_hash(result), request_jsonb=_document(request.id, target, operation, fact), result_jsonb=result,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id, actor_role_assignment_id=assignments[0],
        authorization_version=actor.authorization_version, occurred_at=fact.created_at, created_at=fact.created_at)
    db.add(command)
    db.flush()
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id,
        action="fulfillment_version_recorded", aggregate_type=operation, aggregate_id=str(fact.id),
        before_jsonb={}, after_jsonb={"command_id": str(command.id), "request_id": str(request.id),
            "request_version": target, "request_hash": command.request_hash,
            "result_hash": command.result_hash, "permission_action": permission_action},
        request_id=f"fulfillment-version-{command.id}", occurred_at=fact.created_at, created_at=fact.created_at)
    request.personal_inbound_status = state
    request.version = target
    request.updated_at = fact.created_at
    db.flush()
    return command


def verify_fulfillment_command(db, *, request, actor, operation, fact, request_reference, expected_version):
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.idempotency_key_hash == fact.idempotency_key_hash))
    if (command is None or not isinstance(command.result_jsonb, dict)
        or command.operation != operation or command.request_id != request.id
        or command.target_version != expected_version or command.actor_user_id != actor.user_id
        or command.actor_person_id != actor.person_id or command.request_reference != request_reference
        or command.request_hash != fact.request_hash
        or _time(command.occurred_at) != _time(fact.created_at)
        or _time(command.created_at) != _time(fact.created_at)
        or command.request_jsonb != _document(request.id, expected_version, operation, fact)
        or command.result_hash != _hash(command.result_jsonb)
        or command.result_jsonb != {"schema_version": "1.0", "operation": operation,
            "request_id": str(request.id), "target_version": expected_version, "fact_id": str(fact.id),
            "personal_inbound_status": command.result_jsonb.get("personal_inbound_status")}
        or command.result_jsonb.get("personal_inbound_status") not in {"pending_acceptance", "partially_accepted", "accepted", "posted"}):
        raise MaterialRequestReadError("fulfillment_command_invalid", "service_unavailable", "履约版本命令证据不完整，保留原请求核验")
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.action == "fulfillment_version_recorded",
        AuditEvent.aggregate_type == operation, AuditEvent.aggregate_id == str(fact.id))).all())
    expected_audit = {"command_id": str(command.id), "request_id": str(request.id),
        "request_version": expected_version, "request_hash": command.request_hash, "result_hash": command.result_hash,
        "permission_action": "receive" if request_reference.endswith("/my-receipts") else "fulfill"}
    if (len(audits) != 1 or audits[0].actor_user_id != actor.user_id or audits[0].after_jsonb != expected_audit
        or _time(audits[0].occurred_at) != _time(fact.created_at)):
        raise MaterialRequestReadError("fulfillment_command_audit_invalid", "service_unavailable", "履约版本审计证据不完整，保留原请求核验")
    return command
