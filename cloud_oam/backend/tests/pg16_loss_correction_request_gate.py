"""Shared CI/local correction seals and registered HTTP acceptance on PG16.

The entry point owns disposable database admission, provisioning and cleanup.
Authentication identities are synthetic; transaction and database roles are real.
"""
from sqlalchemy import text

from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as opening_and_sources
from test_postgresql16_release_gate import HEAD_REVISION

PREVIOUS = '20261209_0160'
RETENTION_ERROR = '0161 immutable correction request history requires retention'


def snapshot(owner):
    """All public rows plus normalized function/trigger definitions and ACLs.

    Object OIDs may change across a successful empty migration roundtrip; names,
    definitions, ownership, security properties and data may not.
    """
    with owner.connect() as db:
        tables = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).all()
        rows = {}
        for table in tables:
            quoted = db.dialect.identifier_preparer.quote(table)
            rows[table] = db.scalars(text('SELECT to_jsonb(t)::text FROM public.' + quoted + ' t ORDER BY to_jsonb(t)::text')).all()
        functions = db.execute(text("""
            SELECT p.oid::regprocedure::text, pg_get_functiondef(p.oid),
                pg_get_userbyid(p.proowner), p.proacl::text, p.prosecdef, p.provolatile,
                p.proconfig::text, p.proparallel, p.proleakproof, p.proisstrict
            FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
              AND p.prokind IN ('f','p') ORDER BY p.oid::regprocedure::text
        """)).all()
        triggers = db.execute(text("""
            SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled,
                t.tgdeferrable,t.tginitdeferred
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal
            ORDER BY c.relname,t.tgname
        """)).all()
        return dict(rows=rows,functions=[tuple(row) for row in functions],triggers=[tuple(row) for row in triggers])


def assert_current_runtime(owner, api):
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')
    with api.connect() as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION


def release(engines, *, tracking, migrate, provision, scenario):
    if tracking not in ('quantity','serial') or scenario not in ('seals','http_sources'):
        raise ValueError('explicit correction request tracking and scenario required')
    if HEAD_REVISION != '20261212_0163':
        raise ValueError('correction request gate requires review for the new migration head')
    owner,api = (engines[key] for key in ('star_oam_migrator','star_oam_api'))
    if scenario == 'seals':
        from pg16_loss_correction_seal_business import exercise
    else:
        from pg16_loss_correction_http_business import exercise
    migrate('correction-request-upgrade','upgrade','head')
    provision()
    assert_current_runtime(owner,api)
    before = snapshot(owner)
    migrate('empty-correction-request-downgrade','downgrade',PREVIOUS)
    migrate('empty-correction-request-reupgrade','upgrade','head')
    assert snapshot(owner) == before
    assert_current_runtime(owner,api)
    result = opening_and_sources(engines,tracking=tracking,after_preview=exercise)
    assert result['passed'] and result['submission']['passed']
    assert result['migrationHead'] == HEAD_REVISION
    assert_current_runtime(owner,api)
    before = snapshot(owner)
    migrate('retained-correction-request-downgrade','downgrade',PREVIOUS,expected=RETENTION_ERROR)
    assert snapshot(owner) == before
    assert_current_runtime(owner,api)
    result.update(scenario=scenario,formalMigrationApplied=True,
        emptyMigrationRoundtrip=True,retainedRequestHistoryBlocksDowngrade=True,
        failedDowngradeAllPublicRowsFunctionsTriggersUnchanged=True,
        runtimeStartupSecurityBeforeAndAfter=True,syntheticLoginIdentity=True,
        publicHttpAcceptance=scenario=='http_sources',productionAcceptance=False)
    return result
