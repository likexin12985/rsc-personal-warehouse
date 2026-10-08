"""Live DB authority over real old inbound fixtures; candidate rows are stubs.

The correction tables here carry only reference data, not full committed stock,
audit or request facts. These checks do not claim a native COMMIT fence.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, select, text

from app.formal_access import load_formal_principal
from app.foundation_models import AuthIdentity, Organization, Permission, Person, Role, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockAccount, StockLocation
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections import return_condition_authority as subject
from test_formal_access import make_user, assign, make_organization
from test_return_condition_source import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    derived, ready, parcel, acceptance, prepared,
)
from test_stock_return_inbound import snapshot
import test_return_condition_source as source_fixture

ERRORS = (InventoryReadError, InventoryPostingError)
pytestmark = pytest.mark.parametrize('stock', ['quantity'], indirect=True)


@pytest.fixture(scope='module')
def authority_template():
    value = {}
    yield value
    if 'database' in value:
        value['database'].close()


@pytest.fixture(autouse=True)
def regional_opening(request, authority_template):
    # The legacy helper is autouse; avoid rerunning its entire upstream business
    # pipeline after the complete immutable test database has been snapshotted.
    if not authority_template:
        source_fixture.regional_opening.__wrapped__(request.getfixturevalue('db'),
            request.getfixturevalue('stock'), request.getfixturevalue('acceptance'),
            request.getfixturevalue('parcel'))


@pytest.fixture
def context(db, stock, request, authority_template):
    # Generate the complete old-service history once, then clone the SQLite
    # database for each independent test. Never reuse a Session or authority.
    if authority_template:
        db.rollback(); db.expunge_all()
        authority_template['database'].backup(db.connection().connection.driver_connection)
        values = authority_template['references']
        request.getfixturevalue('monkeypatch').setattr(posting, 'load_formal_principal', load_formal_principal)
        cases, events = subject._tables()
        return SimpleNamespace(
            actor=load_formal_principal(db, values['actor']),
            reviewer=load_formal_principal(db, values['reviewer']),
            hq=load_formal_principal(db, values['hq']),
            source=db.get(StockAccount, values['source']), line=values['line'],
            issue=SimpleNamespace(inbound_id=values['inbound']),
            custody=db.get(CustodyAssignment, values['custody']), cases=cases, events=events,
            regional_role=db.get(Role, values['regional_role']), hq_role=db.get(Role, values['hq_role']))
    acceptance = request.getfixturevalue('acceptance')
    hq_actor, selection, issue, _ = request.getfixturevalue('prepared')
    source = db.get(StockAccount, issue.original_target_account_id)
    owner = db.get(Organization, source.owner_org_id)
    regional_role = db.scalar(select(Role).where(Role.code == 'provincial_manager'))
    hq_role = db.scalar(select(Role).where(Role.code == 'admin'))
    reviewer, _ = make_user(db, owner, name='Synthetic independent condition reviewer')
    assign(db, reviewer, regional_role, scope_type='organization', scope_id=str(owner.id))
    for kind, action in subject.ACTIONS.items():
        role = regional_role if kind in subject.REQUESTER or kind in subject.REGIONAL else hq_role
        permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
            Permission.action == action, Permission.field_code == ''))
        if permission is None:
            permission = Permission(resource='stock_operation', action=action, field_code='', description='Test only')
            db.add(permission); db.flush()
        if db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id)) is None:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')); db.flush()
    assign(db, db.get(User, acceptance.actor.user_id), regional_role,
        scope_type='organization', scope_id=str(owner.id))
    db.commit()
    actor = load_formal_principal(db, acceptance.actor.user_id)
    reviewer = load_formal_principal(db, reviewer.id)
    hq_actor = load_formal_principal(db, hq_actor.user_id)
    # Full real identity/source tables; explicitly minimal correction facts.
    # Copy all column types so production SELECTs run without a mocked loader.
    metadata = MetaData()
    cases, events = subject._tables()
    for original in (cases, events):
        Table(original.name, metadata, *(Column(c.name, c.type, primary_key=c.name == 'id',
            nullable=c.name != 'id') for c in original.columns))
    metadata.create_all(db.get_bind())
    custody = db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == source.location_id,
        CustodyAssignment.custodian_person_id == source.custodian_person_id))
    db.commit()
    copied = sqlite3.connect(':memory:')
    db.connection().connection.driver_connection.backup(copied)
    authority_template.update(database=copied, references=dict(actor=actor.user_id,
        reviewer=reviewer.user_id, hq=hq_actor.user_id, source=source.id,
        line=selection.inbound_line_id, inbound=issue.inbound_id, custody=custody.id,
        regional_role=regional_role.id, hq_role=hq_role.id))
    return SimpleNamespace(actor=actor, reviewer=reviewer, hq=hq_actor, source=source,
        line=selection.inbound_line_id, issue=issue, custody=custody, cases=cases, events=events,
        regional_role=regional_role, hq_role=hq_role)


def seed(db, c, status='awaiting_regional'):
    case_id, submit_id, verify_id, last_id = (uuid4() for _ in range(4))
    db.execute(c.cases.insert().values(id=case_id, operation_type='condition_correction',
        inbound_id=c.issue.inbound_id, inbound_line_id=c.line, source_account_id=c.source.id,
        recorded_condition=c.source.condition_code, custody_assignment_id=c.custody.id, submit_event_id=submit_id))
    rows = [dict(id=submit_id, case_id=case_id, kind='submit', event_sequence=1,
        actor_user_id=c.actor.user_id, actor_person_id=c.actor.person_id,
        to_state='awaiting_regional', request_hash='a'*64)]
    if status in ('awaiting_headquarters', 'approved'):
        rows.append(dict(id=verify_id, case_id=case_id, kind='verify_region', event_sequence=2,
            actor_user_id=c.reviewer.user_id, actor_person_id=c.reviewer.person_id,
            to_state='awaiting_headquarters', request_hash='b'*64))
    if status == 'awaiting_regional':
        last_id = submit_id
    elif status == 'awaiting_headquarters':
        last_id = verify_id
    else:
        rows.append(dict(id=last_id, case_id=case_id, kind='test_state', event_sequence=3,
            actor_user_id=c.hq.user_id, actor_person_id=c.hq.person_id,
            to_state=status, request_hash='c'*64))
    db.execute(c.events.insert(), rows); db.commit()
    return dict(case_id=case_id, expected_event_id=last_id)


def permission(db, role, action):
    return db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == role.id,
        Permission.resource == 'stock_operation', Permission.action == action))


def test_submission_derives_scope_from_original_inbound_and_does_not_write(db, context):
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result = subject.authorize_submission(db, actor=context.actor, inbound_line_id=context.line)
        assert result.source_account_id == context.source.id
        assert result.owner_org_id == context.source.owner_org_id
        assert result.custody_assignment_id == context.custody.id
        assert result.actor == context.actor and result.case_id is None
        assert snapshot(db) == before and not db.new and not db.dirty
    finally:
        db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('kind,status,actor', [
    ('verify_region','awaiting_regional','reviewer'),
    ('return_evidence','awaiting_regional','reviewer'),
    ('reject_region','awaiting_regional','reviewer'),
    ('supplement','needs_evidence','actor'),
    ('withdraw','awaiting_regional','actor'),
    ('return_region','awaiting_headquarters','hq'),
    ('reject_hq','awaiting_headquarters','hq'),
    ('approve_hq','awaiting_headquarters','hq'),
    ('cancel_approved','approved','hq'),
    ('execute','approved','actor'),
    ('release','rejected_pending_release','actor'),
    ('release','cancelled_pending_release','actor'),
])
def test_each_action_requires_its_stage_and_current_real_permission(db, context, kind, status, actor):
    args = seed(db, context, status)
    result = subject.authorize_action(db, actor=getattr(context, actor), kind=kind, **args)
    assert result.action == subject.ACTIONS[kind]
    assert result.previous_event_id == args['expected_event_id']
    assert not db.new and not db.dirty


@pytest.mark.parametrize('change', ['deny','missing','expired','revoked','identity','version',
    'disabled','suspended','departed','role_inactive','other_region','custodian',
    'custody_expired','custody_ambiguous','location_inactive','owner_inactive','ancestor_inactive','org_cycle'])
def test_changed_live_identity_permission_or_custody_refuses_new_submission(db, context, change):
    c = context
    user = db.get(User, c.actor.user_id)
    grant = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id,
        RoleAssignment.role_id == c.regional_role.id))
    now = datetime.now(timezone.utc)
    if change == 'deny':
        permission(db, c.regional_role, subject.ACTIONS['submit']).effect = 'deny'
    elif change == 'missing':
        db.delete(permission(db, c.regional_role, subject.ACTIONS['submit']))
    elif change == 'expired':
        grant.valid_to = now - timedelta(seconds=1)
    elif change == 'revoked':
        grant.revoked_at = now; grant.revoked_by = user.id; grant.status = 'revoked'
    elif change == 'identity':
        for item in db.scalars(select(AuthIdentity).where(AuthIdentity.user_id == user.id)):
            item.revoked_at = now; item.status = 'revoked'
    elif change == 'version':
        user.authorization_version += 1
    elif change == 'disabled':
        user.is_active = False
    elif change == 'suspended':
        user.account_status = 'suspended'
    elif change == 'departed':
        db.get(Person, user.person_id).employment_status = 'left'
    elif change == 'role_inactive':
        c.regional_role.status = 'inactive'
    elif change == 'other_region':
        region = make_organization(db, name='Another region', org_type='region_company')
        grant.scope_id = str(region.id)
    elif change == 'custodian':
        db.get(StockLocation, c.source.location_id).custodian_person_id = c.reviewer.person_id
    elif change == 'custody_expired':
        c.custody.valid_to = now - timedelta(seconds=1)
    elif change == 'custody_ambiguous':
        db.add(CustodyAssignment(id=uuid4(), location_id=c.custody.location_id,
            custodian_person_id=c.custody.custodian_person_id, valid_from=now-timedelta(seconds=1), valid_to=now+timedelta(days=1)))
    elif change == 'location_inactive':
        db.get(StockLocation, c.source.location_id).status = 'inactive'
    elif change == 'owner_inactive':
        db.get(Organization, c.source.owner_org_id).status = 'inactive'
    else:
        owner = db.get(Organization, c.source.owner_org_id)
        if change == 'org_cycle':
            child = make_organization(db, name='Synthetic cyclic child', parent=owner, org_type='department')
            owner.parent_id = child.id
        else:
            ancestor = db.get(Organization, owner.parent_id) if owner.parent_id else make_organization(db, name='Synthetic source parent')
            owner.parent_id = ancestor.id
            ancestor.status = 'inactive'
    db.commit()
    before = snapshot(db)
    with pytest.raises(ERRORS):
        subject.authorize_submission(db, actor=c.actor, inbound_line_id=c.line)
    assert snapshot(db) == before and not db.new and not db.dirty


def test_regional_read_or_hq_role_cannot_impersonate_original_custodian(db, context):
    for actor in (context.reviewer, context.hq):
        with pytest.raises(ERRORS):
            subject.authorize_submission(db, actor=actor, inbound_line_id=context.line)


def test_requester_cannot_review_and_different_user_cannot_execute(db, context):
    args = seed(db, context)
    with pytest.raises(InventoryReadError, match='申请人'):
        subject.authorize_action(db, actor=context.actor, kind='verify_region', **args)
    args = seed(db, context, 'approved')
    with pytest.raises(ERRORS):
        subject.authorize_action(db, actor=context.reviewer, kind='execute', **args)


@pytest.mark.parametrize('match', ['user','person'])
def test_hq_cannot_reuse_regional_verifier_by_either_identity(db, context, match):
    args = seed(db, context, 'awaiting_headquarters')
    changes = {'actor_user_id':context.hq.user_id} if match == 'user' else {'actor_person_id':context.hq.person_id}
    db.execute(context.events.update().where(context.events.c.id == args['expected_event_id']).values(**changes)); db.commit()
    with pytest.raises(InventoryReadError, match='必须独立'):
        subject.authorize_action(db, actor=context.hq, kind='approve_hq', **args)


def test_historical_reviewer_suspension_does_not_revoke_valid_next_hq_action(db, context):
    args = seed(db, context, 'awaiting_headquarters')
    for actor in (context.actor, context.reviewer):
        user = db.get(User, actor.user_id); user.is_active = False; user.account_status = 'suspended'
    db.commit()
    assert subject.authorize_action(db, actor=context.hq, kind='approve_hq', **args).actor == context.hq


@pytest.mark.parametrize('change', ['stale','wrong_state','foreign_case','custody_replaced'])
def test_exact_case_latest_event_and_custody_cannot_be_substituted(db, context, change):
    args = seed(db, context)
    if change == 'stale':
        args['expected_event_id'] = uuid4()
    elif change == 'foreign_case':
        args['case_id'] = uuid4()
    elif change == 'custody_replaced':
        db.execute(context.cases.update().where(context.cases.c.id == args['case_id'])
            .values(custody_assignment_id=uuid4())); db.commit()
    with pytest.raises(ERRORS):
        subject.authorize_action(db, actor=context.reviewer,
            kind='execute' if change == 'wrong_state' else 'verify_region', **args)


def test_actual_db_permission_revocation_between_passes_is_not_cached(db, context, monkeypatch):
    original = subject._once
    calls = 0
    def revoke(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs); calls += 1
        if calls == 1:
            permission(db, context.regional_role, subject.ACTIONS['submit']).effect = 'deny'; db.flush()
        return result
    monkeypatch.setattr(subject, '_once', revoke)
    with pytest.raises(ERRORS):
        subject.authorize_submission(db, actor=context.actor, inbound_line_id=context.line)
    assert calls == 1
