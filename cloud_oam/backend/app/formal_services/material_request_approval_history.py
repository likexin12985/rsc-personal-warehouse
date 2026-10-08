"""Reconstruct final quantities for closure after request access is authorized.

Approval projections alone are not evidence. This internal read verifies the
selected causal chain, hashed decision manifests, commands and audit membership.
It does not confer current permission or decide whether a request can close.
"""
from types import SimpleNamespace

from sqlalchemy import select

from app.demand_models import (ApprovalAction, ApprovalExternalRegistration,
    ApprovalExternalRegistrationLine, ApprovalInstance, ApprovalStep,
    ApprovalStepLineDecision, MaterialRequestCommand, MaterialRequestLine,
    MaterialRequestRevision)
from app.foundation_models import AuditEvent, FileObject
from . import material_request_approval as approval
from .audit_chain import AuditChainError, verify_audit_event_in_read_snapshot
from .material_request_policy import MaterialRequestPolicyError, reconstruct_final_approval_quantities
from .material_request_query import MaterialRequestReadError


def _invalid(message='最终批准历史与不可变逐级决定不一致'):
    raise MaterialRequestReadError('closure_approval_history_invalid', 'service_unavailable', message)


def verified_final_approved_quantities(db, *, request):
    with db.no_autoflush:
        try:
            return _verify(db, request)
        except (AuditChainError, approval.MaterialRequestApprovalError, MaterialRequestPolicyError):
            _invalid()


def _rows(db, model, *where, order=()):
    return tuple(db.scalars(select(model).where(*where).order_by(*order)
        .execution_options(populate_existing=True)))


def _verify(db, request):
    if request.status not in {'approved', 'partially_approved', 'cancelled'}:
        _invalid('需求尚未形成最终批准，不能核验关闭数量')
    revisions = _rows(db, MaterialRequestRevision, MaterialRequestRevision.request_id == request.id,
        MaterialRequestRevision.revision_no == request.revision_no)
    instances = _rows(db, ApprovalInstance, ApprovalInstance.request_id == request.id,
        order=(ApprovalInstance.attempt_no,))
    if len(revisions) != 1 or revisions[0].status != 'sealed' or not instances:
        _invalid()
    revision, instance = revisions[0], instances[-1]
    if (instance.status != 'completed' or instance.current_step_id is not None
            or instance.current_step_no is not None or instance.request_revision_id != revision.id
            or instance.revision_no != request.revision_no or instance.completed_at is None
            or request.decided_at is None
            or approval._as_utc(instance.completed_at) != approval._as_utc(request.decided_at)):
        _invalid()
    lines = _rows(db, MaterialRequestLine, MaterialRequestLine.request_id == request.id,
        MaterialRequestLine.revision_id == revision.id, order=(MaterialRequestLine.line_no,))
    steps = _rows(db, ApprovalStep, ApprovalStep.instance_id == instance.id)
    finals = sorted((s for s in steps if s.step_no == 3), key=lambda s: s.attempt_no)
    if not lines or not finals:
        _invalid()
    by_id = {s.id: s for s in steps}
    chain = [finals[-1]]
    for number in (2, 1):
        previous = by_id.get(chain[-1].predecessor_step_id)
        if previous is None or previous.step_no != number:
            _invalid('最终审批前驱链不完整或跨实例')
        chain.append(previous)
    chain.reverse()
    if chain[0].predecessor_step_id is not None:
        _invalid()
    outcomes = []
    previous_version = 0
    for step in chain:
        if step.status not in {'approved', 'partially_approved'} or step.decided_at is None:
            _invalid()
        actions = _rows(db, ApprovalAction, ApprovalAction.step_id == step.id,
            ApprovalAction.action.in_(('approve', 'partial_approve', 'verify_external_accept')))
        if len(actions) != 1 or actions[0].instance_id != instance.id:
            _invalid('审批步骤缺少唯一的终结动作')
        action = actions[0]
        command = db.get(MaterialRequestCommand, action.command_id, populate_existing=True)
        decisions = _rows(db, ApprovalStepLineDecision, ApprovalStepLineDecision.step_id == step.id,
            order=(ApprovalStepLineDecision.request_line_id,))
        if not decisions or command is None:
            _invalid()
        outcome = approval._step_outcomes(db, step)
        terminal, status = approval._terminal_outcome(outcome)
        registration_ids = {row.external_registration_id for row in decisions}
        if len(registration_ids) != 1:
            _invalid()
        registration_id = next(iter(registration_ids))
        locked = SimpleNamespace(request=request, revision=revision, instance=instance, step=step, lines=lines)
        actor = SimpleNamespace(principal=SimpleNamespace(user_id=action.actor_user_id,
            person_id=action.actor_person_id, authorization_version=action.authorization_version),
            grant=SimpleNamespace(assignment_id=action.actor_role_assignment_id))
        expected = approval._decision_manifest(locked=locked, actor=actor, outcomes=outcome,
            terminal_action=terminal, external_registration_id=registration_id,
            occurred_at=approval._as_utc(step.decided_at))
        external = step.source_mode == 'external_registration'
        operation = 'verify_external' if external else ('region_decide' if step.step_no == 1 else 'headquarters_decide')
        reference = (f'/mr/{request.id}/steps/{step.id}/external/{registration_id}/verify' if external
                     else f'/api/v1/material-requests/{request.id}/approval-steps/{step.id}/decision')
        if (status != step.status or step.decision_manifest_sha256 != expected
                or action.action != ('verify_external_accept' if external else terminal)
                or action.source_mode != step.source_mode
                or command.operation != operation or command.request_id != request.id
                or command.request_reference != reference or not isinstance(command.result_jsonb, dict)
                or not previous_version < command.target_version <= request.version
                or command.actor_user_id != action.actor_user_id or command.actor_person_id != action.actor_person_id
                or command.actor_role_assignment_id != action.actor_role_assignment_id
                or command.authorization_version != action.authorization_version
                or command.result_hash != approval._canonical_hash(command.result_jsonb)
                or any(approval._as_utc(value) != approval._as_utc(step.decided_at)
                       for value in (action.occurred_at, command.occurred_at, command.created_at))):
            _invalid()
        expected_result = {'request_id': str(request.id), 'request_no': request.request_no,
            'request_version': command.target_version, 'revision_id': str(revision.id),
            'revision_no': revision.revision_no, 'instance_id': str(instance.id),
            ('step_id' if external else 'decided_step_id'): str(step.id)}
        if any(command.result_jsonb.get(key) != value for key, value in expected_result.items()):
            _invalid()
        if any(row.decided_by_user_id != action.actor_user_id or row.decided_by_person_id != action.actor_person_id
                or row.decided_role_assignment_id != action.actor_role_assignment_id
                or row.authorization_version != action.authorization_version or row.decision_source != step.source_mode
                or approval._as_utc(row.decided_at) != approval._as_utc(step.decided_at)
                or approval._as_utc(row.created_at) != approval._as_utc(step.decided_at) for row in decisions):
            _invalid()
        if external:
            _external(db, locked, command, action, registration_id)
        elif registration_id is not None or command.request_jsonb != {
                **_command_document(locked, command),
                'step_id': str(step.id), 'step_no': step.step_no, 'step_attempt_no': step.attempt_no,
                'action': terminal, 'decision_manifest_sha256': expected, 'sensitive_fields': 'excluded'}:
            _invalid()
        audit_action = 'material_request.external_evidence.verify_accept' if external else f'material_request.approval.{terminal}'
        audits = _rows(db, AuditEvent, AuditEvent.stream_key == 'material_request',
            AuditEvent.aggregate_type == 'material_request', AuditEvent.aggregate_id == str(request.id),
            AuditEvent.action == audit_action, AuditEvent.occurred_at == action.occurred_at,
            AuditEvent.actor_user_id == action.actor_user_id,
            AuditEvent.after_jsonb['step_id'].as_string() == str(step.id))
        if len(audits) != 1 or audits[0].actor_user_id != action.actor_user_id:
            _invalid('审批缺少对应的审计链事实')
        expected_audit = {'request_id': str(request.id), 'revision_id': str(revision.id),
            'instance_id': str(instance.id), 'request_version': command.target_version,
            'step_id': str(step.id), 'step_status': step.status}
        if any(audits[0].after_jsonb.get(key) != value for key, value in expected_audit.items()):
            _invalid()
        if not external and audits[0].after_jsonb.get('decision_manifest_sha256') != expected:
            _invalid()
        verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audits[0].id)
        outcomes.append(outcome)
        previous_version = command.target_version
    if approval._as_utc(chain[-1].decided_at) != approval._as_utc(request.decided_at):
        _invalid()
    result = reconstruct_final_approval_quantities(
        requested_quantities={line.id: line.requested_qty for line in lines}, step_outcome_chain=outcomes)
    expected = {row.request_line_id: row.approved_qty for row in result}
    status = approval.final_request_approval_status_from_chain(
        requested_quantities={line.id: line.requested_qty for line in lines}, step_outcome_chain=outcomes)
    if status == 'rejected' or (request.status != 'cancelled' and request.status != status) \
            or any(line.final_approved_qty != expected[line.id] for line in lines):
        _invalid('最终批准投影与三级审批事实不一致')
    return expected


def _command_document(locked, command):
    return {'schema': 'rsc.material_request_approval_command.v1', 'operation': command.operation,
        'request_id': str(locked.request.id), 'revision_id': str(locked.revision.id),
        'revision_no': locked.revision.revision_no, 'instance_id': str(locked.instance.id),
        'target_version': command.target_version, 'payload_sha256': command.request_hash}


def _external(db, locked, command, action, registration_id):
    registration = db.get(ApprovalExternalRegistration, registration_id, populate_existing=True) if registration_id else None
    if registration is None or registration.step_id != locked.step.id or registration.status != 'accepted':
        _invalid('外部审批登记未经独立复核')
    register_command = approval._require_registration_command(db, locked, registration)
    evidence = db.get(FileObject, registration.evidence_file_id, populate_existing=True)
    rows = _rows(db, ApprovalExternalRegistrationLine,
        ApprovalExternalRegistrationLine.registration_id == registration.id,
        order=(ApprovalExternalRegistrationLine.request_line_id,))
    if register_command is None or evidence is None:
        _invalid()
    approval._verify_external_registration_manifest(locked=locked, registration=registration,
        registration_lines=rows, return_instructions=(), register_command=register_command, evidence=evidence)
    if approval._outcome_documents(rows) != approval._outcome_documents(approval._step_outcomes(db, locked.step)):
        _invalid('独立复核的逐行数量不等于原外部审批登记')
    if (registration.verified_by_user_id != action.actor_user_id
            or registration.verified_by_person_id != action.actor_person_id
            or registration.verified_role_assignment_id != action.actor_role_assignment_id
            or registration.verified_authorization_version != action.authorization_version
            or registration.verified_at is None
            or approval._as_utc(registration.verified_at) != approval._as_utc(action.occurred_at)
            or registration.registered_by_user_id == action.actor_user_id
            or registration.registered_by_person_id == action.actor_person_id
            or command.request_jsonb != {**_command_document(locked, command), 'registration_id': str(registration.id),
                'verification_decision': 'accept', 'registration_manifest_sha256': registration.decision_manifest_sha256,
                'sensitive_fields': 'excluded'}):
        _invalid('外部审批登记与独立复核命令不一致')
