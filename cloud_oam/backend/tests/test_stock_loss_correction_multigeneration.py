"""Three actual correction rounds with exact historical request recovery.

SQLite service proof only; explicit binding fixtures do not prove native
PostgreSQL admission, migrations, API role permissions or concurrency.
"""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.inventory_models import InventoryTransaction, StockBalance, SerialCurrentPosition
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_disposition_recovery as original_recovery
from app.formal_services.stock_loss_corrections import (
    history_chain as history,
    reversal_stock as reversal_stock,
    inverse_posting as inverse_posting,
    correction_approval as approval,
    correction_stock as correction_stock,
    correction_execution as correction_execution,
    inverse_recovery as inverse_recovery,
    correction_recovery as correction_recovery,
)
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossDispositionReversal as Inverse,
    StockLossCorrectionDecision as Decision,
    StockLossCorrectionExecution as Correction,
)
from app.formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove, CorrectionPreview, CorrectionExecute,
)
from test_stock_loss_correction_inverse_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable,
)
from test_stock_loss_correction_execution import snapshot
from test_stock_loss_original_history_recovery import bind_fixture

pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)


@pytest.fixture
def active(db, recoverable):
    w = recoverable
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    for action in ('approve_loss_correction', 'correct_loss'):
        permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
            Permission.action == action, Permission.field_code == ''))
        if permission is None:
            permission = Permission(resource='stock_operation', action=action, field_code='', description='Synthetic next-generation test')
            db.add(permission); db.flush()
        grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id))
        if grant is None:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
        else:
            grant.effect = 'allow'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    return w


def test_three_rounds_reverse_exact_predecessor_and_recover_all_original_requests(db, active, execution):
    w = active
    original_rows = tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id')))
    original_payload = original_recovery.facts.payload(w.root)
    selected, requests, inverses, corrections = w.root, [], [], []
    root_binding = dict(root_disposition_id=w.root.id, expected_root_request_hash=w.root.request_hash,
        expected_submission_plan_hash=w.order.plan_hash, reason='复核真实前序处置后独立纠正')
    for generation, kind in enumerate(('convert_used', 'convert_damaged', 'restore_available')):
        preview = ReversalPreview(**root_binding,
            reversed_correction_id=selected.id if generation else None,
            expected_execution_request_hash=selected.request_hash)
        checked = reversal_stock.prepare(db, actor=w.actor, request=preview)
        command = ReversalExecute(**preview.model_dump(), expected_plan_hash=checked.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        old_source = db.get(StockBalance, selected.target_account_id).quantity
        frozen = db.get(StockBalance, w.root.source_account_id).quantity
        posted = inverse_posting.execute_account_inverse(db, actor=w.actor, request=command)
        db.commit()
        inverse = db.get(Inverse, UUID(posted['reversal_id']))
        bind_fixture(db, inverse, command, 'inverse')
        tx = db.get(InventoryTransaction, inverse.posting_transaction_id)
        assert tx.reversed_transaction_id == selected.posting_transaction_id
        assert inverse.original_transaction_id == selected.posting_transaction_id
        assert inverse.original_movement_id == selected.posting_movement_id
        assert inverse.reversed_correction_id == (selected.id if generation else None)
        assert db.get(StockBalance, selected.target_account_id).quantity == old_source - w.root.quantity
        assert db.get(StockBalance, w.root.source_account_id).quantity == frozen + w.root.quantity
        inverses.append(inverse); requests.append(('inverse', command, posted))
        before = snapshot(db)
        db.execute(text('PRAGMA query_only=ON'))
        found = inverse_recovery.lookup_original_inverse(db, actor=w.actor, request=command)
        assert found['request_state'] == 'found' and found['retry_allowed'] is False and found['result'] == posted
        root_result = original_recovery.lookup_disposition_request(db, actor=w.actor,
            request=execution.command, flow=execution.flow)
        assert root_result == dict(lookup_status='found', retry_permitted=False, disposition=original_payload)
        assert snapshot(db) == before
        db.execute(text('PRAGMA query_only=OFF'))

        approve_command = CorrectionApprove(**root_binding, reversal_id=inverse.id,
            expected_reversal_hash=inverse.request_hash, disposition=kind,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        approved = approval.approve(db, actor=w.actor, request=approve_command)
        db.commit()
        decision = db.get(Decision, UUID(approved['correction_decision_id']))
        bind_fixture(db, decision, approve_command, 'approval')
        requests.append(('approval', approve_command, approved))
        assert db.get(StockBalance, w.root.source_account_id).quantity == frozen + w.root.quantity
        preview = CorrectionPreview(**root_binding, reversal_id=inverse.id,
            expected_reversal_hash=inverse.request_hash, correction_decision_id=decision.id,
            expected_correction_decision_hash=decision.request_hash)
        checked = correction_stock.prepare(db, actor=w.actor, request=preview)
        correction_command = CorrectionExecute(**preview.model_dump(), expected_plan_hash=checked.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        corrected = correction_execution.execute(db, actor=w.actor, request=correction_command)
        db.commit()
        selected = db.get(Correction, UUID(corrected['correction_execution_id']))
        bind_fixture(db, selected, correction_command, 'correction')
        assert db.get(StockBalance, w.root.source_account_id).quantity == frozen
        assert selected.reversal_id == inverse.id and selected.correction_decision_id == decision.id
        for identifier in checked.document['serial_ids']:
            position = db.get(SerialCurrentPosition, UUID(identifier))
            assert position.stock_account_id == selected.target_account_id
            assert position.last_movement_id == selected.posting_movement_id
        corrections.append(selected); requests.append(('correction', correction_command, corrected))
        assert tuple(db.execute(text('SELECT * FROM stock_loss_dispositions ORDER BY id'))) == original_rows

    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    proof = history.verify_chain(db, root_disposition_id=w.root.id)
    assert {value.reversal_id for value in proof.inverse_proofs} == {row.id for row in inverses}
    assert {value.correction_execution_id for value in proof.correction_proofs} == {row.id for row in corrections}
    for kind, command, expected in requests:
        lookup = inverse_recovery.lookup_original_inverse if kind == 'inverse' else correction_recovery.lookup
        found = lookup(db, actor=w.actor, request=command)
        assert found['request_state'] == 'found' and found['retry_allowed'] is False and found['result'] == expected
        with pytest.raises(InventoryReadError):
            lookup(db, actor=w.actor, request=command.model_copy(update={'reason': 'different original request'}))
    assert original_recovery.lookup_disposition_request(db, actor=w.actor,
        request=execution.command, flow=execution.flow)['disposition'] == original_payload
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
