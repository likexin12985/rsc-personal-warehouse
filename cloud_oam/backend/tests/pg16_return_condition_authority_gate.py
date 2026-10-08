"""Actual API-role submission admission over the old application's PG16 history.

Only the owned local cluster; temporary synthetic grants roll back completely.
No condition business action is inserted or committed. Later action authority
and the eventual COMMIT fence require separate integrated verification.
"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RoleAssignment, RolePermission
from app.inventory_models import StockAccount, StockLocation, CustodyAssignment
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_loss_corrections import return_condition_authority as subject
from pg16_stock_scrap_structure_gate import facts, functions, original_columns


@contextmanager
def owned_role_connection(owner, directory):
    # The migration account intentionally cannot SET ROLE to API. Use only this
    # fresh, owned, socket-only cluster's DBA session for role switching; do not
    # grant membership, alter production roles, or execute checks as DBA.
    directory = Path(directory).resolve()
    root = Path(__file__).resolve().parents[2] / 'artifacts/local-return-condition-pg16'
    if not directory.is_relative_to(root):
        raise ValueError('owned condition run directory required')
    state = json.loads((directory/'cluster-state.json').read_text())
    assert state['status'] == 'running_checks'
    assert owner.url.username == 'star_oam_migrator' and owner.url.database == 'rsc_pg16_release_gate'
    assert owner.url.host is None and owner.url.port is None
    assert dict(owner.url.query) == {'host':state['socketDirectory']}
    engine = create_engine(owner.url.set(username='postgres'), poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT current_user')) == 'postgres'
            assert connection.scalar(text("SELECT current_setting('listen_addresses')")) == ''
            assert Path(connection.scalar(text("SELECT current_setting('data_directory')"))).resolve() == directory/'data'
            assert connection.scalar(text('SELECT system_identifier::text FROM pg_control_system()')) == state['identity']['systemIdentifier']
            assert int((directory/'data/postmaster.pid').read_text().splitlines()[0]) == state['identity']['pid']
            connection.execute(text('SET ROLE star_oam_migrator'))
            connection.commit()
            yield connection
    finally:
        engine.dispose()


def run(owner, *, original, source, directory, regional_source=False, file_candidate=False):
    with owned_role_connection(owner, directory) as connection:
        assert connection.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert connection.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert 160000 <= int(connection.scalar(text('SHOW server_version_num'))) < 170000
        columns = original_columns(connection)
        before = facts(connection, columns)
        before_functions = functions(connection)
        connection.rollback()
        transaction = connection.begin()
        refused = []
        regional = None
        file_checks = None
        try:
            with Session(bind=connection, join_transaction_mode='create_savepoint') as db:
                user = db.get(User, original['receiverUserId'])
                region = UUID(source['owner_org_id'])
                role = db.scalar(select(Role).where(Role.code == 'provincial_manager'))
                now = datetime.now(timezone.utc)
                grant = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id,
                    RoleAssignment.role_id == role.id, RoleAssignment.scope_type == 'organization',
                    RoleAssignment.scope_id == str(region), RoleAssignment.revoked_at.is_(None),
                    RoleAssignment.valid_to.is_(None)))
                if grant is None:
                    grant = RoleAssignment(user_id=user.id, role_id=role.id, scope_type='organization',
                        scope_id=str(region), valid_from=now-timedelta(days=1), status='active',
                        assigned_by=user.id, reason='Owned native condition authority test')
                    db.add(grant)
                assert db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation',
                    Permission.action == subject.ACTIONS['submit'])) is None
                permission = Permission(resource='stock_operation', action=subject.ACTIONS['submit'],
                    field_code='', description='Owned native test only; rolled back')
                db.add(permission); db.flush()
                link = RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')
                db.add(link); db.flush()
                actor = load_formal_principal(db, user.id)
                assert actor.person_id == UUID(source['custodian_person_id'])
                identifiers = dict(user=user.id, grant=grant.id, link=link.id,
                    location=UUID(source['location_id']), custody=UUID(source['custody_assignment_id']),
                    account=UUID(source['source_account_id']))

                def check(supplied=actor):
                    db.flush()
                    db.execute(text('SET LOCAL ROLE star_oam_api'))
                    statements = []
                    def capture(_c, _cur, sql, _p, _ctx, _many):
                        statements.append(sql.lstrip().split()[0].upper())
                    event.listen(connection, 'before_cursor_execute', capture)
                    try:
                        result = subject.authorize_submission(db, actor=supplied,
                            inbound_line_id=UUID(source['selection']['inbound_line_id']))
                        assert not db.new and not db.dirty and not db.deleted
                        return result
                    finally:
                        event.remove(connection, 'before_cursor_execute', capture)
                        db.execute(text('SET LOCAL ROLE star_oam_migrator'))
                        assert statements and set(statements) == {'SELECT'}, statements

                result = check()
                assert result.owner_org_id == region
                assert result.custody_assignment_id == identifiers['custody']
                for change in ('deny', 'expired', 'version', 'disabled', 'custodian', 'custody_expired', 'other_actor'):
                    nested = db.begin_nested()
                    try:
                        if change == 'deny':
                            db.get(RolePermission, identifiers['link']).effect = 'deny'
                        elif change == 'expired':
                            db.get(RoleAssignment, identifiers['grant']).valid_to = now-timedelta(seconds=1)
                        elif change == 'version':
                            db.get(User, identifiers['user']).authorization_version += 1
                        elif change == 'disabled':
                            db.get(User, identifiers['user']).is_active = False
                        elif change == 'custodian':
                            admin = db.get(User, original['administratorUserId'])
                            db.get(StockLocation, identifiers['location']).custodian_person_id = admin.person_id
                        elif change == 'custody_expired':
                            db.get(CustodyAssignment, identifiers['custody']).valid_to = now-timedelta(seconds=1)
                        candidate = load_formal_principal(db, original['administratorUserId']) if change == 'other_actor' else actor
                        try:
                            check(candidate)
                        except (InventoryReadError, InventoryPostingError) as error:
                            refused.append(dict(change=change, code=error.code))
                        else:
                            raise AssertionError('admission did not refuse: '+change)
                    finally:
                        nested.rollback(); db.expire_all()
                    assert check() == result
                if regional_source:
                    from pg16_return_condition_regional_source_gate import run as check_regional_source
                    regional = check_regional_source(db, connection, actor=actor, role=role,
                        submit_link_id=identifiers['link'], original=original, source=source)
                if file_candidate:
                    from pg16_return_condition_file_gate import run as check_files
                    file_checks = check_files(db, connection, actor=actor,
                        submit_link_id=identifiers['link'], directory=directory)
                db.rollback()
        finally:
            transaction.rollback()
        assert original_columns(connection) == columns and facts(connection, columns) == before
        assert functions(connection) == before_functions
    return dict(passed=True, scope='new submission admission only', realApiRole=True,
        oldApplicationHistory=True, readStatementsOnly=True, positive=1,
        refused=refused, temporaryGrantsRolledBack=True, allOriginalFactsUnchanged=True,
        allOriginalFunctionsRestored=True,
        regionalSource=regional, fileCandidate=file_checks, subsequentActionsNative=False,
        commitAuthorityFence=False, productionAcceptance=False)
