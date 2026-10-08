"""Use the real principal loader to observe DB revocation/expiry before review."""
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.formal_access import load_formal_principal
from app.foundation_models import RoleAssignment, Permission, RolePermission, Role
from app.formal_services import inventory_posting as posting
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_approval as service, recovery_authority as authority
from test_stock_scrap_recovery_approval import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found,
    submit, region_request, stock_state, all_state,
)


@pytest.mark.parametrize('change', ['expired', 'deny'])
def test_db_authority_changes_block_region_review_without_stock_or_approval(db, found, monkeypatch, change):
    applied = submit(db, found, 'apply', found.application)
    monkeypatch.setattr(posting, 'load_formal_principal', load_formal_principal)
    actor = found.actors['regional']
    if change == 'expired':
        row = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == actor.user_id))
        row.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
            Permission.action == authority.ACTIONS['regional']))
        role = db.scalar(select(Role).where(Role.code == 'provincial_manager'))
        db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id)).effect = 'deny'
    db.commit()
    before = all_state(db), stock_state(db)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)):
        service.submit(db, actor=actor, request=region_request(found, applied))
    db.rollback()
    assert (all_state(db), stock_state(db)) == before
