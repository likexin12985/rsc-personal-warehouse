"""Real SQLite history and registered HTTP reads; native ACL proof is separate."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import (
    read_sources, correction_approval, correction_execution, correction_stock,
)
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution,
)
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionPreview, CorrectionExecute
from app.main import app
from test_stock_loss_correction_approval import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, request as approval_request,
)
from test_stock_loss_correction_execution import snapshot
from test_stock_loss_original_history_recovery import bind_fixture
from test_stock_loss_source_routes import private

pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
PATH = '/api/v1/stock-operations/loss-reports/corrections/sources/'


def read_only(db, w):
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = read_sources.read(db, actor=w.actor, root_disposition_id=w.root.id)
        assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
        return result
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def grant(db, w, action, effect):
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == action, Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action=action, field_code='', description='Synthetic source read test')
        db.add(permission); db.flush()
    rule = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
        RolePermission.permission_id == permission.id))
    if rule is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect=effect))
    else:
        rule.effect = effect
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)


def test_original_reference_is_query_only_and_not_write_authority(db, recoverable):
    w = recoverable
    grant(db, w, 'reverse_loss', 'deny')
    result = read_only(db, w)
    assert result.chain_state == 'active_execution'
    assert result.write_authorization_provided is False and result.stock_effect == 'none'
    assert result.frozen_share_in_verified_history == 0
    assert result.approval_reference is None and not result.approval_choices
    reference = result.inverse_preview_reference
    assert reference.root_disposition_id == w.root.id
    assert reference.expected_root_request_hash == w.root.request_hash
    assert reference.expected_submission_plan_hash == w.order.plan_hash
    assert reference.reversed_correction_id is None
    assert reference.expected_execution_request_hash == w.root.request_hash
    assert [(r.kind, r.fact_id) for r in result.history] == [('original_execution', w.root.id)]


def test_all_independent_decisions_are_choices_not_auto_execution(db, inverse_ready):
    w = inverse_ready
    bind_fixture(db, w.inverse, w.command, 'inverse')
    pending = read_only(db, w)
    assert pending.chain_state == 'awaiting_approval' and not pending.approval_choices
    assert pending.approval_reference.reversal_id == w.inverse.id
    assert pending.inverse_preview_reference is None
    assert pending.frozen_share_in_verified_history == w.root.quantity
    expected = {}
    for kind in ('restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap'):
        command = approval_request(w, kind)
        posted = correction_approval.approve(db, actor=w.actor, request=command)
        db.commit()
        row = db.get(Decision, UUID(posted['correction_decision_id']))
        bind_fixture(db, row, command, 'approval')
        expected[row.id] = row
    result = read_only(db, w)
    assert result.chain_state == 'awaiting_execution'
    assert result.frozen_share_in_verified_history == w.root.quantity
    assert {c.correction_decision_id for c in result.approval_choices} == set(expected)
    assert not any(r.kind == 'correction_execution' for r in result.history)
    for choice in result.approval_choices:
        row = expected[choice.correction_decision_id]
        assert choice.reason == row.reason and choice.disposition == row.disposition
        if row.disposition in ('return_to_region', 'scrap'):
            assert choice.execution_mode == 'dedicated_flow_required' and choice.preview_reference is None
        else:
            assert choice.execution_mode == 'preview_required'
            assert choice.preview_reference.correction_decision_id == row.id
            assert choice.preview_reference.expected_correction_decision_hash == row.request_hash
            assert choice.preview_reference.reversal_id == w.inverse.id


def test_corrected_stock_references_exact_execution_and_keeps_history(db, inverse_ready):
    w = inverse_ready
    bind_fixture(db, w.inverse, w.command, 'inverse')
    grant(db, w, 'correct_loss', 'allow')
    command = approval_request(w, 'convert_used')
    posted = correction_approval.approve(db, actor=w.actor, request=command)
    db.commit()
    decision = db.get(Decision, UUID(posted['correction_decision_id']))
    bind_fixture(db, decision, command, 'approval')
    reference = read_only(db, w).approval_choices[0].preview_reference
    preview = CorrectionPreview(**reference.model_dump(), reason='Synthetic explicit chosen decision')
    plan = correction_stock.prepare(db, actor=w.actor, request=preview)
    command = CorrectionExecute(**preview.model_dump(), expected_plan_hash=plan.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    posted = correction_execution.execute(db, actor=w.actor, request=command)
    db.commit()
    corrected = db.get(Execution, UUID(posted['correction_execution_id']))
    bind_fixture(db, corrected, command, 'correction')
    result = read_only(db, w)
    assert result.chain_state == 'active_execution' and result.frozen_share_in_verified_history == 0
    assert not result.approval_choices and result.approval_reference is None
    assert result.inverse_preview_reference.reversed_correction_id == corrected.id
    assert result.inverse_preview_reference.expected_execution_request_hash == corrected.request_hash
    assert {r.fact_id for r in result.history} == {w.root.id, w.inverse.id, decision.id, corrected.id}
    assert all(r.created_at.tzinfo is not None for r in result.history)


@pytest.mark.parametrize('fault', ['read_denied', 'forged_root', 'changed_cursor'])
def test_unauthorized_unproven_or_mixed_history_never_returns_sources(db, recoverable, monkeypatch, fault):
    w = recoverable
    if fault == 'read_denied':
        grant(db, w, 'read', 'deny')
    elif fault == 'forged_root':
        w.root.request_hash = 'f' * 64
        db.commit()
    else:
        bound = read_sources._bound
        count = 0
        def changing(session):
            nonlocal count
            count += 1
            value = bound(session)
            return (value[0] + 1, *value[1:]) if count > 1 else value
        monkeypatch.setattr(read_sources, '_bound', changing)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        read_only(db, w)
    assert caught.value.status_code == {'read_denied': 403, 'forged_root': 503, 'changed_cursor': 409}[fault]
    assert snapshot(db) == before


def test_registered_get_is_private_and_does_not_expose_request_secrets(db, recoverable, monkeypatch):
    w = recoverable
    monkeypatch.setattr(app, 'dependency_overrides', {
        get_db: lambda: db, get_formal_principal: lambda: w.actor,
    })
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        client = TestClient(app, raise_server_exceptions=False)
        try:
            response = client.get(PATH + str(w.root.id))
            assert response.status_code == 200, response.text
            private(response)
            body = response.json()
            assert body['root_disposition_id'] == str(w.root.id)
            for secret in ('idempotency_key', 'command_jsonb', 'plan_jsonb', 'key_hash', 'intent_hash'):
                assert secret not in response.text
            missing = client.get(PATH + str(uuid4()))
            assert missing.status_code == 404, missing.text
            private(missing)
        finally:
            client.close()
        assert snapshot(db) == before and not db.new and not db.dirty
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
