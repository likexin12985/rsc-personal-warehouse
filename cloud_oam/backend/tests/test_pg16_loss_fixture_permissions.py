"""Current-head fixtures must expose policy failures, never fix them silently."""
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from pg16_stock_operation_permission_policy import require_formal_grant, uses_migrated_loss_policy
from test_stock_operation_permission_policy import policy, snapshot, store, custom


@pytest.mark.parametrize('role_code,action', [('provincial_manager','review_loss_regional'),
    ('admin','read'), ('provincial_manager','read'), ('technician','read')])
@pytest.mark.parametrize('case', ['allow', 'missing_permission', 'missing_grant',
    'deny', 'field_grant', 'inactive_role', 'external_role'])
def test_current_fixture_reads_exact_policy_without_authorization_writes(store, case, role_code, action):
    engine, tables = store
    with engine.begin() as db:
        policy.install(db)
        permissions, grants, roles = (tables[n] for n in ('permissions', 'role_permissions', 'roles'))
        if action == 'read':
            pid, gid = custom(db, tables, action, role_code, effect='allow')
        else:
            pid = policy.permission_id(action)
            gid = policy.grant_id(role_code, action)
        if case in ('missing_permission', 'missing_grant'):
            db.execute(grants.delete().where(grants.c.id == gid))
        if case == 'missing_permission':
            db.execute(permissions.delete().where(permissions.c.id == pid))
        if case == 'deny':
            db.execute(grants.update().where(grants.c.id == gid).values(effect='deny'))
        if case == 'field_grant':
            db.execute(permissions.update().where(permissions.c.id == pid).values(field_code='quantity'))
        if case in ('inactive_role', 'external_role'):
            db.execute(roles.update().where(roles.c.code == role_code).values(
                **({'status': 'inactive'} if case == 'inactive_role' else {'is_external': True})))
        before = snapshot(db, tables)
    statements = []
    def query_only(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT ')
    event.listen(engine, 'before_cursor_execute', query_only)
    try:
        with Session(engine) as db:
            if case == 'allow':
                grant = require_formal_grant(db, role_code=role_code, action=action)
                assert grant.id == gid and grant.effect == 'allow'
            else:
                with pytest.raises(AssertionError, match='formal loss grant missing or denied'):
                    require_formal_grant(db, role_code=role_code, action=action)
            db.commit()
        assert statements
        with engine.connect() as db:
            assert snapshot(db, tables) == before
    finally:
        event.remove(engine, 'before_cursor_execute', query_only)


@pytest.mark.parametrize('revisions,expected', [
    (['20261213_0164'], False), (['20261214_0165'], True),
    ([], None), (['20261213_0164','20261214_0165'], None),
    (['20261215_0166','20261216_0167'], None),
    (['20261215_0166'], True), (['20261216_0167'], True), (['20261217_0168'], True), (['20261218_0169'], True), (['20261219_0170'], True), (['20261220_0171'], True), (['20261221_0172'], True), (['20261222_0173'], True), (['20261223_0174'], True), (['20261224_0175'], True), (['20261225_0176'], True), (['20261227_0178'], True), (['20261228_0179'], True), (['20261229_0180'], True), pytest.param(['20261230_0181'], True, id='contact-0181'), pytest.param(['20261231_0182'], None, id='unknown-0182'), (['20261208_0159'], None),
])
def test_historical_fixture_admission_is_explicit_and_read_only(store, revisions, expected):
    engine, tables = store
    with engine.begin() as db:
        db.execute(text('CREATE TABLE alembic_version (version_num TEXT)'))
        for revision in revisions:
            db.execute(text('INSERT INTO alembic_version VALUES (:revision)'), {'revision': revision})
        before = snapshot(db, tables)
    statements = []
    def query_only(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT ')
    event.listen(engine, 'before_cursor_execute', query_only)
    try:
        with Session(engine) as db:
            if expected is None:
                with pytest.raises(AssertionError, match='unreviewed loss fixture migration head'):
                    uses_migrated_loss_policy(db)
            else:
                assert uses_migrated_loss_policy(db) is expected
            db.commit()
        assert statements
        with engine.connect() as db:
            assert snapshot(db, tables) == before
            assert db.scalars(text('SELECT version_num FROM alembic_version')).all() == revisions
    finally:
        event.remove(engine, 'before_cursor_execute', query_only)
