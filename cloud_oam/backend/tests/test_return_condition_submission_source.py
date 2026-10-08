"""Regional source preparation uses real authority/history, never an HQ alias."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RoleAssignment, RolePermission
from app.inventory_models import StockAccount, StockBalance, StockLocation
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_services import inventory_posting as posting, stock_return_inbound_recovery as recovery
from app.formal_services.stock_loss_corrections import (
    return_condition_authority as authority, return_condition_submission_source as subject,
    return_condition_source as old_source, return_history,
)
# Imported fixtures must expose their dependencies in every consuming module;
# pytest does not inherit the defining module's fixture namespace.
from test_return_condition_authority import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    derived, ready, parcel, acceptance, prepared, authority_template, regional_opening,
    reader_tables, context, permission, ERRORS,
)
from test_stock_return_inbound import snapshot

pytestmark = pytest.mark.parametrize('stock', ['quantity'], indirect=True)
_REAL_AUTHORITY_TABLES = authority._tables


def allow(db, role, resource, action):
    p = db.scalar(select(Permission).where(Permission.resource == resource,
        Permission.action == action, Permission.field_code == ''))
    if p is None:
        p = Permission(resource=resource, action=action, field_code='', description='Synthetic regional source test')
        db.add(p); db.flush()
    link = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
        RolePermission.permission_id == p.id))
    if link is None:
        link = RolePermission(role_id=role.id, permission_id=p.id, effect='allow'); db.add(link)
    else:
        link.effect = 'allow'
    db.flush()
    return link


@pytest.fixture
def regional_source(db, context, monkeypatch):
    c = context
    # The shared authority fixture seeds isolated partial reader facts. Source
    # and service flows must resolve the full tables used by their real writes.
    monkeypatch.setattr(authority, '_tables', _REAL_AUTHORITY_TABLES)
    # The old receiver happened to have an HQ role. Explicitly revoke it so
    # this test cannot pass by silently retaining or impersonating HQ access.
    now = datetime.now(timezone.utc)
    for grant in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == c.actor.user_id,
            RoleAssignment.role_id == c.hq_role.id)):
        grant.status = 'revoked'; grant.revoked_at = now; grant.revoked_by = c.actor.user_id
    c.read_grant = allow(db, c.regional_role, 'inventory', 'read')
    allow(db, c.regional_role, 'stock_operation', 'read')
    db.commit()
    c.actor = load_formal_principal(db, c.actor.user_id)
    assert c.actor.role_codes == ('provincial_manager',)
    return c


def inspect(db, c, actor=None, identifier=None):
    return subject.inspect_submission_source(db, actor=actor or c.actor, inbound_line_id=identifier or c.line)


def test_region_proves_exact_source_without_hq_history_permission_or_any_write(db, regional_source, monkeypatch):
    c = regional_source
    before = snapshot(db)
    loaded_users = []
    real = posting.load_formal_principal
    def current_only(db, user_id):
        loaded_users.append(user_id)
        assert user_id == c.actor.user_id, 'historical identity must not masquerade as a current actor'
        return real(db, user_id)
    monkeypatch.setattr(posting, 'load_formal_principal', current_only)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = inspect(db, c); doc = result.document
        assert doc['actor_user_id'] == c.actor.user_id
        assert doc['source_account_id'] == str(c.source.id)
        assert doc['selection']['inbound_line_id'] == str(c.line)
        assert doc['historical_damaged_quantity'] == '0.375'
        assert doc['account_balance_quantity'] == '1.000'
        assert doc['source_status'] == 'recorded_stock_retained'
        assert doc['submission_permission_checked'] is True
        assert doc['physical_verification_required'] and not doc['correction_authorized'] and not doc['posting_allowed']
        assert not {'coordinates', 'lines', 'groups', 'requester_id', 'reason', 'command_jsonb'} & doc.keys()
        assert snapshot(db) == before and not db.new and not db.dirty
        assert loaded_users and set(loaded_users) == {c.actor.user_id}
        with pytest.raises(ERRORS):
            return_history.read(db, actor=c.actor, root_disposition_id=UUID(doc['selection']['root_disposition_id']))
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


def test_original_inbound_request_remains_independently_recoverable(db, regional_source):
    c = regional_source
    header = db.get(StockOperationReturnInbound, c.issue.inbound_id)
    before = snapshot(db)
    expected_hash = header.request_hash
    inspect(db, c)
    original = recovery.lookup_return_inbound_request(db, actor=c.actor,
        receipt_id=header.receipt_id, request_id=header.request_id)
    assert original['request_hash'] == expected_hash
    assert snapshot(db) == before


@pytest.mark.parametrize('change', ['submit_deny','inventory_deny','other_custodian','hq_only','unknown_line'])
def test_denied_scope_never_runs_the_internal_history_verifier(db, regional_source, monkeypatch, change):
    c = regional_source
    actor, identifier = c.actor, c.line
    if change == 'submit_deny':
        permission(db, c.regional_role, authority.ACTIONS['submit']).effect = 'deny'
    elif change == 'inventory_deny':
        c.read_grant.effect = 'deny'
    elif change == 'other_custodian':
        actor = load_formal_principal(db, c.reviewer.user_id)
    elif change == 'hq_only':
        actor = load_formal_principal(db, c.hq.user_id)
    else:
        identifier = uuid4()
    db.commit()
    def unexpected(*args, **kwargs):
        raise AssertionError('unauthorized source traversed the full history')
    monkeypatch.setattr(return_history, '_verified_graph', unexpected)
    before = snapshot(db)
    with pytest.raises(ERRORS):
        inspect(db, c, actor, identifier)
    assert snapshot(db) == before


@pytest.mark.parametrize('change', ['permission','inbound','balance','custody'])
def test_change_after_first_stock_proof_discards_the_whole_result(db, regional_source, monkeypatch, change):
    c = regional_source
    real = old_source._current
    count = 0
    def mutate(*args, **kwargs):
        nonlocal count
        result = real(*args, **kwargs); count += 1
        if count == 1:
            if change == 'permission':
                permission(db, c.regional_role, authority.ACTIONS['submit']).effect = 'deny'
            elif change == 'inbound':
                db.get(StockOperationReturnInbound, c.issue.inbound_id).reason = 'tampered synthetic historical reason'
            elif change == 'balance':
                db.get(StockBalance, c.source.id).quantity += Decimal(1)
            else:
                db.get(StockLocation, c.source.location_id).custodian_person_id = c.reviewer.person_id
            db.flush()
        return result
    monkeypatch.setattr(old_source, '_current', mutate)
    with pytest.raises(ERRORS):
        inspect(db, c)
    assert count >= 1


def test_full_history_tampering_is_not_hidden_by_current_scope_and_balance(db, regional_source):
    c = regional_source
    header = db.get(StockOperationReturnInbound, c.issue.inbound_id)
    header.request_hash = 'f'*64; db.commit()
    before = snapshot(db)
    with pytest.raises(ERRORS):
        inspect(db, c)
    assert snapshot(db) == before


def test_real_outgoing_and_returned_stock_does_not_become_proved_original_share(db, regional_source):
    c = regional_source
    allow(db, c.hq_role, 'inventory_transaction', 'post')
    target = StockAccount(id=uuid4(), **{k:getattr(c.source,k) for k in (
        'owner_org_id','location_id','custodian_person_id','material_id','condition_code','lot_id')},
        availability_bucket='reserved')
    db.add(target); db.commit()
    actor = load_formal_principal(db, c.hq.user_id)
    for kind, first, second in [('reserve',c.source.id,target.id), ('release',target.id,c.source.id)]:
        token = uuid4().hex
        command = posting.InventoryPostingCommand(transaction_no='SYNTHETIC-REGIONAL-'+token,
            movement_type=kind, source_document_type='test_case', source_document_id=token,
            posting_key='synthetic-regional:'+token, effective_at=datetime.now(timezone.utc),
            movements=(posting.InventoryMovementCommand(first,second,Decimal('.125'),()),))
        posting.post_inventory_transaction(db, actor=actor, command=command, idempotency_key=token, request_id=token)
        db.commit()
    before = snapshot(db)
    doc = inspect(db,c).document
    assert doc['account_balance_quantity'] == '1.000'
    assert doc['historical_damaged_quantity'] == '0.375'
    assert doc['source_status'] == 'later_activity_requires_reconciliation'
    assert doc['later_outgoing_count'] == 1 and Decimal(doc['later_outgoing_quantity']) == Decimal('.125')
    assert not doc['posting_allowed'] and snapshot(db) == before


@pytest.mark.parametrize('change', ['other_region','custodian_changed'])
def test_original_request_lookup_still_denies_out_of_scope_receivers(db, regional_source, change):
    from test_formal_access import make_organization
    c = regional_source
    header = db.get(StockOperationReturnInbound, c.issue.inbound_id)
    if change == 'other_region':
        other = make_organization(db, name='Different granted region', org_type='region_company')
        grant = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == c.actor.user_id,
            RoleAssignment.role_id == c.regional_role.id, RoleAssignment.status == 'active'))
        grant.scope_id = str(other.id)
    else:
        db.get(StockLocation, c.source.location_id).custodian_person_id = c.reviewer.person_id
    db.commit()
    actor = load_formal_principal(db, c.actor.user_id)
    before = snapshot(db)
    with pytest.raises(ERRORS):
        recovery.lookup_return_inbound_request(db, actor=actor,
            receipt_id=header.receipt_id, request_id=header.request_id)
    assert snapshot(db) == before
