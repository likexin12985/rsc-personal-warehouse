"""Found-stock approval after an actual original/inverse/corrected scrap chain."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.foundation_models import AuthIdentity, Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.stock_scrap_recovery_schemas import ScrapRecoveryApply
from app.formal_services.stock_scrap import recovery_authority as authority
from app.formal_services.stock_scrap.execution import execute
from test_formal_access import make_role, assign
from test_stock_scrap_recovery_approval import submit, region_request, hq_request, coordinates, stock_state
from test_stock_scrap_correction_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, command, execute_command,
)
from test_stock_scrap_plan import upload

pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
    pytest.mark.parametrize('ready', ['scrap'], indirect=True)]


def test_corrected_scrap_accepts_independent_recovery_approvals_without_restoring_stock(db, ready, allowed, regional, monkeypatch):
    request, _ = execute_command(db, ready.w.actor, command(db, ready, monkeypatch))
    result = execute(db, actor=ready.w.actor, request=request)
    db.commit()
    assert result['source_kind'] == 'correction'
    technician = make_role(db, 'technician')
    assign(db, allowed.world.user, technician, scope_type='person', scope_id=str(allowed.actor.person_id))
    db.add(AuthIdentity(user_id=allowed.actor.user_id, identity_type='mobile', provider_key='found-stock-test',
        identifier_hash=uuid4().hex + uuid4().hex, hash_version=1, verified_at=datetime.now(timezone.utc), status='active'))
    for stage, role_code in [('apply', 'technician'), ('regional', 'provincial_manager'), ('headquarters', 'admin')]:
        role = db.scalar(select(Role).where(Role.code == role_code))
        permission = Permission(resource='stock_operation', action=authority.ACTIONS[stage], field_code='', description='Synthetic recovery')
        db.add(permission); db.flush()
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    actors = {stage: load_formal_principal(db, actor.user_id) for stage, actor in
        [('apply', allowed.actor), ('regional', regional.actor), ('headquarters', ready.w.actor)]}
    allowed.world.current_principal = actors['apply']
    file = upload(db, actors['apply'], monkeypatch)
    application = ScrapRecoveryApply(action='apply_scrap_recovery', source=dict(scrap_line_id=result['scrap_line_id'],
        expected_scrap_request_hash=result['request_hash']), reason='找到纠正报废实物，申请准确恢复',
        evidence_file_ids=(file.id,), **coordinates())
    found = SimpleNamespace(actors=actors, world=allowed.world, application=application)
    before = stock_state(db)
    applied = submit(db, found, 'apply', application)
    region = submit(db, found, 'regional', region_request(found, applied))
    final = submit(db, found, 'headquarters', hq_request(found, applied, region))
    assert final['status'] == 'approved_pending_execution' and final['stock_effect'] == 'none'
    assert stock_state(db) == before
