"""Real original scrap -> recovery -> new decision -> corrected scrap -> recovery."""
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.stock_loss_correction_models import StockLossDispositionReversal
from app.inventory_models import StockBalance
from app.stock_scrap_schemas import ScrapPreview
from app.stock_scrap_recovery_schemas import ScrapRecoveryApply, ScrapRecoveryPreview, ScrapRecoveryExecute
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionApprove
from app.formal_services.stock_loss_corrections import correction_approval
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.stock_scrap.execution import execute as scrap
from test_stock_scrap_execution import execute_command
from test_stock_scrap_plan import upload
from test_stock_scrap_recovery_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, ready_to_restore,
    service, plan, submit, region_request, hq_request, coordinates,
)


def protect_other_report(db, ready_to_restore, allowed, regional, monkeypatch):
    """Add a real pending loss after the first scrap, before either recovery."""
    from test_stock_loss_dispositions import second_report
    report = second_report(db, allowed, regional, monkeypatch)
    w = ready_to_restore
    account_id = UUID(w.checked.document['target_account_id'])
    holds = read_hold_snapshot(db, source_account_id=account_id)
    protected = next(line for line in holds.lines if line.operation_id == report.operation_id)
    assert protected.frozen_quantity == 1 and protected.active_execution_id is None
    serials = rebuild_serial_states(db, protected.frozen_serial_ids)

    def check():
        current = read_hold_snapshot(db, source_account_id=account_id)
        assert next(line for line in current.lines if line.line_id == protected.line_id) == protected
        assert rebuild_serial_states(db, protected.frozen_serial_ids) == serials

    # A new freeze changes the ledger-bound plan, even for an unrelated line.
    w.found.world.current_principal = w.actor
    preview = ScrapRecoveryPreview.model_validate(w.command.model_dump(include=set(ScrapRecoveryPreview.model_fields)))
    refreshed = plan.prepare(db, actor=w.actor, request=preview)
    assert refreshed.plan_hash != w.checked.plan_hash
    w.checked = refreshed
    w.command = w.command.model_copy(update={'expected_plan_hash': refreshed.plan_hash})
    check()
    return check


def exercise_generations(db, ready_to_restore, monkeypatch, *, protected=None, observe=None):
    w = ready_to_restore
    retained = 1 if protected else 0
    first = service.execute(db, actor=w.actor, request=w.command)
    db.commit()
    if protected:
        protected()
    root = db.get(StockLossDisposition, UUID(first['root_disposition_id']))
    original_rows = tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id')))
    first_inverse = db.get(StockLossDispositionReversal, UUID(first['reversal_id']))
    if observe:
        observe('inverse', first_inverse, w.command)
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    for action in ('approve_loss_correction', 'correct_loss'):
        permission = Permission(resource='stock_operation', action=action, field_code='', description='Synthetic successive scrap')
        db.add(permission); db.flush(); db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    actor = load_formal_principal(db, w.actor.user_id)
    w.found.world.current_principal = actor
    order = db.get(StockOperationOrder, root.operation_id)
    approval_command = CorrectionApprove(root_disposition_id=root.id,
        expected_root_request_hash=root.request_hash, expected_submission_plan_hash=order.plan_hash,
        reversal_id=first_inverse.id, expected_reversal_hash=first_inverse.request_hash, disposition='scrap',
        reason='找回后重新核实，独立批准再次报废', **coordinates())
    approval = correction_approval.approve(db, actor=actor, request=approval_command)
    db.commit()
    if observe:
        from app.stock_loss_correction_models import StockLossCorrectionDecision
        observe('approval', db.get(StockLossCorrectionDecision, UUID(approval['correction_decision_id'])), approval_command)
    file = upload(db, actor, monkeypatch)
    preview = ScrapPreview(source=dict(kind='correction', root_disposition_id=root.id,
        expected_root_request_hash=root.request_hash, expected_submission_plan_hash=order.plan_hash,
        reversal_id=first_inverse.id, expected_reversal_hash=first_inverse.request_hash,
        correction_decision_id=approval['correction_decision_id'], expected_correction_decision_hash=approval['request_hash']),
        execution_reason='按新批准再次报废', evidence_file_ids=(file.id,))
    command, prepared = execute_command(db, actor, preview)
    second_scrap = scrap(db, actor=actor, request=command)
    db.commit()
    if observe:
        from app.stock_loss_correction_models import StockLossCorrectionExecution
        observe('correction', db.get(StockLossCorrectionExecution, UUID(second_scrap['correction_execution_id'])), command)
    assert db.get(StockBalance, root.source_account_id).quantity == retained
    if protected:
        protected()
    assert second_scrap['source_kind'] == 'correction'
    w.found.actors['headquarters'] = actor
    applicant = w.found.actors['apply']
    w.found.world.current_principal = applicant
    new_file = upload(db, applicant, monkeypatch)
    w.found.application = ScrapRecoveryApply(action='apply_scrap_recovery', source=dict(
        scrap_line_id=second_scrap['scrap_line_id'], expected_scrap_request_hash=second_scrap['request_hash']),
        evidence_file_ids=(new_file.id,), reason='第二代准确原报废找回', **coordinates())
    applied = submit(db, w.found, 'apply', w.found.application)
    region = submit(db, w.found, 'regional', region_request(w.found, applied))
    final = submit(db, w.found, 'headquarters', hq_request(w.found, applied, region))
    value = ScrapRecoveryPreview(source=w.found.application.source, recovery_request_id=applied['fact_id'],
        expected_request_hash=applied['request_hash'], headquarters_review_id=final['fact_id'],
        expected_headquarters_hash=final['request_hash'], reason='恢复准确第二代报废，保留第一代全部事实')
    checked = plan.prepare(db, actor=actor, request=value)
    command = ScrapRecoveryExecute(**value.model_dump(), action='execute_scrap_recovery',
        expected_plan_hash=checked.plan_hash, **coordinates())
    last = service.execute(db, actor=actor, request=command)
    db.commit()
    if observe:
        observe('inverse', db.get(StockLossDispositionReversal, UUID(last['reversal_id'])), command)
    assert db.get(StockBalance, root.source_account_id).quantity == 1 + retained
    if protected:
        protected()
    assert tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id'))) == original_rows
    proof = verify_chain(db, root_disposition_id=root.id)
    assert len(proof.inverse_proofs) == 2 and len(proof.correction_proofs) == 1
    assert {p.reversal_id for p in proof.inverse_proofs} == {UUID(first['reversal_id']), UUID(last['reversal_id'])}
    states = rebuild_serial_states(db, tuple(UUID(s) for s in prepared.document['serial_ids']))
    assert all(s.lifecycle_status == 'active' and s.stock_account_id == root.source_account_id for s in states.values())


@pytest.mark.parametrize('shared', [False, True], ids=['single', 'shared'])
def test_two_generations_preserve_original_facts_and_restore_only_exact_frozen_share(
        db, ready_to_restore, allowed, regional, monkeypatch, shared):
    protected = protect_other_report(db, ready_to_restore, allowed, regional, monkeypatch) if shared else None
    exercise_generations(db, ready_to_restore, monkeypatch, protected=protected)
