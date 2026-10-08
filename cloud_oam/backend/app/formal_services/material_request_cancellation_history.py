"""Verify whole-request and compensated remaining cancellations from their facts.

Internal evidence only. The caller must authorize current request access first.
Partial-fulfillment compensation is a different command and is not fabricated
from the original direct-cancellation facts.
"""
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.demand_models import ApprovalAction, MaterialRequestCommand
from app.foundation_models import AuditEvent
from . import material_request_lifecycle as lifecycle
from .audit_chain import AuditChainError, verify_audit_event_in_read_snapshot
from .material_request_query import MaterialRequestReadError


def _invalid():
    raise MaterialRequestReadError('closure_cancellation_history_invalid',
        'service_unavailable', '取消数量缺少完整的原始命令、逐行事实或审计依据')


def verified_cancelled_quantities(db, *, request):
    with db.no_autoflush:
        try:
            return _verify(db, request)
        except (lifecycle.MaterialRequestLifecycleError, AuditChainError,
                KeyError, ValueError, TypeError, AttributeError):
            _invalid()


def _verify(db, request):
    graph = lifecycle._read_graph(db, request.id)
    from .material_request_remaining_cancel import verified_remaining_cancelled_quantities
    remaining = verified_remaining_cancelled_quantities(db, request=request)
    facts = graph.cancellation_facts
    commands = tuple(db.scalars(select(MaterialRequestCommand).where(
        MaterialRequestCommand.request_id == request.id,
        MaterialRequestCommand.operation == 'cancel').execution_options(populate_existing=True)))
    if request.status != 'cancelled':
        if facts or commands or any(line.cancelled_qty != 0 for line in graph.lines):
            _invalid()
        if set(remaining) - {line.id for line in graph.lines}:
            _invalid()
        return {line.id: remaining.get(line.id, Decimal('0.000')) for line in graph.lines}
    if remaining or len(commands) != 1 or not facts:
        _invalid()
    command = commands[0]
    actions = tuple(db.scalars(select(ApprovalAction).where(ApprovalAction.command_id == command.id)))
    if len(actions) != 1:
        _invalid()
    action = actions[0]
    # Historical identity is used only to verify the existing command envelope.
    # It never grants the current reader or a future close command permission.
    historical = SimpleNamespace(principal=SimpleNamespace(user_id=command.actor_user_id,
        authorization_version=command.authorization_version),
        person=SimpleNamespace(id=command.actor_person_id),
        technician_grant=SimpleNamespace(assignment_id=command.actor_role_assignment_id))
    replay = lifecycle._load_replay(db, key_hash=command.idempotency_key_hash,
        request_hash=command.request_hash, operation='cancel', request_id=request.id,
        request_reference=f'/api/v1/material-requests/{request.id}/cancel',
        requester=historical, expected_comment=action.comment)
    if (replay is None or command.target_version > request.version
            or command.actor_user_id != request.requester_user_id
            or command.actor_person_id != request.requester_person_id
            or replay.result.revision_id != graph.revision.id
            or replay.result.revision_no != request.revision_no
            or replay.result.approval_instance_id != graph.latest_instance.id
            or replay.result.approval_attempt_no != graph.latest_instance.attempt_no
            or replay.result.request_no != request.request_no
            or not lifecycle._same_timestamp(request.cancelled_at, command.occurred_at)):
        _invalid()
    requested = tuple(lifecycle.MaterialRequestCancellationLineInput(
        row.request_line_id, row.cancelled_qty, row.reason) for row in facts)
    lifecycle._require_cancellation_facts(graph=graph, command=command, action=action,
        requested=requested, facts=facts, require_projection=True)
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == 'material_request',
        AuditEvent.action == 'material_request.cancel', AuditEvent.aggregate_type == 'material_request',
        AuditEvent.aggregate_id == str(request.id), AuditEvent.actor_user_id == command.actor_user_id,
        AuditEvent.occurred_at == command.occurred_at)))
    if len(audits) != 1:
        _invalid()
    audit = audits[0]
    document = audit.after_jsonb
    facts_by_id = {str(f.request_line_id): f for f in facts}
    order = document['request_line_order']
    if (not isinstance(order, list) or len(order) != len(facts_by_id) or set(order) != set(facts_by_id)
            or document.get('request_payload_schema') != 'cancel_input_line_order_v1'
            or document.get('request_id') != str(request.id)
            or document.get('revision_id') != str(graph.revision.id)
            or document.get('version') != command.target_version
            or document.get('status') != 'cancelled'
            or document.get('approval_instance_id') != str(graph.latest_instance.id)
            or document.get('cancellation_fact_count') != len(facts)
            or document.get('cancellation_fact_manifest_sha256') != lifecycle._cancellation_fact_manifest(facts)
            or document.get('reason_sha256') != lifecycle._text_hash(action.comment)):
        _invalid()
    payload = {'operation': 'cancel', 'request_id': str(request.id),
        'expected_version': command.target_version - 1, 'reason': action.comment,
        'lines': tuple({'request_line_id': key, 'cancelled_qty': format(facts_by_id[key].cancelled_qty, 'f'),
                       'reason': facts_by_id[key].reason} for key in order),
        'actor_user_id': command.actor_user_id, 'actor_person_id': str(command.actor_person_id),
        'authorization_version': command.authorization_version}
    if lifecycle._canonical_hash(payload) != command.request_hash:
        _invalid()
    verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audit.id)
    return {line.id: next((f.cancelled_qty for f in facts if f.request_line_id == line.id), Decimal('0.000'))
            for line in graph.lines}
