"""Independent reviewer discovery on real quantity/SN history; SQLite only."""
import pytest
from sqlalchemy import select, text, update

from app.formal_access import load_formal_principal
from app.foundation_models import RoleAssignment, RolePermission, Permission
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_case_read as subject
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from test_formal_access import make_organization
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready,
    parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source,
)


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),('serial','serial_command')], indirect=['stock'])
def test_independent_reviewer_finds_only_current_region_cases_without_custody(db, regional_source, request, command_name, monkeypatch):
    c = regional_source; command = request.getfixturevalue(command_name)
    result = writer.submit(db, actor=c.actor, request=command); db.commit()
    reviewer = load_formal_principal(db, c.reviewer.user_id)
    assert reviewer.person_id != c.actor.person_id
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    try:
        page = subject.inbox(db, actor=reviewer, view='pending', limit=1)
        assert page.person_id == reviewer.person_id and page.authorization_version == reviewer.authorization_version
        assert page.next_after_id is None and len(page.items) == 1
        assert page.items[0].inbound_line_id == c.line and page.items[0].events[0].fact.model_dump(mode='json') == result
        assert subject.inbox(db, actor=reviewer, view='all', after_id=c.line).items == []
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
    assert snapshot(db) == before
    db.rollback()
    read_grant = db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation', Permission.action == 'read'))
    real_ids = subject._inbox_ids; calls = 0
    def revoke_on_final_selection(*args, **kwargs):
        nonlocal calls
        ids = real_ids(*args, **kwargs); calls += 1
        if calls == 2:
            db.execute(update(RolePermission).where(RolePermission.id == read_grant.id).values(effect='deny'))
        return ids
    with monkeypatch.context() as patch:
        patch.setattr(subject, '_inbox_ids', revoke_on_final_selection)
        with pytest.raises(InventoryReadError) as error:
            subject.inbox(db, actor=reviewer)
        assert error.value.status_code == 403
    db.rollback(); assert snapshot(db) == before
    other = make_organization(db, name='Synthetic other region', org_type='region_company')
    assignment = db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == reviewer.user_id,
        RoleAssignment.role_id == c.regional_role.id, RoleAssignment.status == 'active')).one()
    assignment.scope_id = str(other.id); db.commit()
    moved = load_formal_principal(db, reviewer.user_id)
    assert subject.inbox(db, actor=moved).items == []
