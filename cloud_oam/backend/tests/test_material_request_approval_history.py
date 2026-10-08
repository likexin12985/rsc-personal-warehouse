"""Real three-level approval facts; no closing command is implied."""
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.demand_models import ApprovalInstance, ApprovalStep, ApprovalStepLineDecision, MaterialRequestCommand
from app.formal_services.material_request_approval_history import verified_final_approved_quantities
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_lifecycle_service import _approved_request, _cancel_input, _current_actor
from test_material_request_approval_service import (approval_db, _submitted, _principal, _approve,
    _evidence, _register_external, _verify_external)
from test_material_request_draft_service import SECRET
from app.formal_services.material_request_lifecycle import cancel_material_request
from app.formal_services.material_request_policy import ApprovalLineDecision


def test_reconstructs_approval_quantities_with_selects_only(approval_db):
    db = approval_db
    _, request, line, _ = _approved_request(db, key='closure-approval-history')
    statements = []
    def observe(_connection, _cursor, statement, *args):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in statement.upper()
    event.listen(db.bind, 'before_cursor_execute', observe)
    try:
        assert verified_final_approved_quantities(db, request=request) == {line.id: line.requested_qty}
    finally:
        event.remove(db.bind, 'before_cursor_execute', observe)
    assert statements


@pytest.mark.parametrize('change', ['projection', 'manifest', 'decision_reason', 'command_hash',
                                  'unfinished_instance', 'reference', 'predecessor'])
def test_corrupted_approval_does_not_authorize_closure(approval_db, change):
    db = approval_db
    _, request, line, _ = _approved_request(db, key='closure-approval-tamper')
    final = db.scalar(select(ApprovalStep).where(ApprovalStep.step_no == 3))
    if change == 'projection':
        line.final_approved_qty -= Decimal('1.000')
    elif change == 'manifest':
        final.decision_manifest_sha256 = 'f'*64
    elif change == 'decision_reason':
        row = db.scalar(select(ApprovalStepLineDecision).where(ApprovalStepLineDecision.step_id == final.id))
        row.reason = 'changed'
    elif change == 'command_hash':
        row = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == 'verify_external'))
        row.result_hash = 'f'*64
    elif change == 'unfinished_instance':
        row = db.scalar(select(ApprovalInstance))
        row.status = 'rejected'
    elif change == 'reference':
        row = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.operation == 'verify_external'))
        row.request_reference = '/mr/another-request/verify'
    else:
        final.predecessor_step_id = db.scalar(select(ApprovalStep.id).where(ApprovalStep.step_no == 1))
    db.flush()
    with pytest.raises(MaterialRequestReadError):
        verified_final_approved_quantities(db, request=request)


def test_earlier_rejected_line_remains_zero_after_partial_later_approvals(approval_db):
    db = approval_db
    world, request, lines, version = _submitted(db, key='closure-partial-chain', two_lines=True)
    manager = _principal(db, world.manager_users[0].id)
    registrant = _principal(db, world.admin_users[0].id)
    verifier = _principal(db, world.admin_users[1].id)
    _, regional = _approve(db, actor=manager, request=request, request_version=version,
        quantities={lines[0].id: Decimal('0.000'), lines[1].id: Decimal('3.000')}, key='closure-partial-region')
    _, headquarters = _approve(db, actor=registrant, request=request, request_version=regional.request_version,
        quantities={lines[1].id: Decimal('2.000')}, key='closure-partial-hq')
    evidence = _evidence(db, uploaded_by=registrant.user_id, marker='closure-partial-evidence')
    step, registration = _register_external(db, actor=registrant, request=request,
        request_version=headquarters.request_version, evidence=evidence, key='closure-partial-external',
        action='approve', lines=(ApprovalLineDecision(lines[1].id, Decimal('1.500'), '批准剩余可供应数量'),))
    _verify_external(db, actor=verifier, request=request, step=step,
        registration_id=registration.registration_id, request_version=registration.request_version,
        step_version=registration.step_version, key='closure-partial-verify')
    assert request.status == 'partially_approved'
    assert verified_final_approved_quantities(db, request=request) == {
        lines[0].id: Decimal('0.000'), lines[1].id: Decimal('1.500')}


def test_whole_cancellation_preserves_original_approved_quantity(approval_db):
    db = approval_db
    world, request, line, version = _approved_request(db, key='closure-cancelled-history')
    cancel_material_request(db, actor=_current_actor(db, world), material_request_id=request.id,
        expected_version=version, cancellation=_cancel_input(line),
        idempotency_key='closure-whole-cancel-key', idempotency_hmac_secret=SECRET,
        trace_request_id='closure-whole-cancel-trace')
    assert request.status == 'cancelled'
    assert verified_final_approved_quantities(db, request=request) == {line.id: line.requested_qty}
