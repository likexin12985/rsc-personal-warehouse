"""Exact execution seals on acknowledged fresh PG16; no external target setup."""
from sqlalchemy import text
from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as sources
from pg16_loss_execution_seal_fixture import run as exercise
from pg16_loss_execution_seal_boundaries import snapshot
from pg16_stock_loss_derived_return_gate import catalog
from test_postgresql16_release_gate import HEAD_REVISION


def indexes(owner):
    with owner.connect() as db:
        return tuple(db.execute(text("SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname")))


def release(engines, *, tracking, migrate, provision):
    if tracking not in ('quantity','serial'):
        raise ValueError('tracking must be quantity or serial')
    owner,api=(engines[k] for k in ('star_oam_migrator','star_oam_api'))
    def security():
        validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    migrate('initial-upgrade','upgrade','head');provision();security()
    before=catalog(owner);before_indexes=indexes(owner)
    migrate('empty-execution-seal-downgrade','downgrade','20261206_0157')
    migrate('empty-execution-seal-reupgrade','upgrade','head')
    assert catalog(owner)==before and indexes(owner)==before_indexes
    security()
    with owner.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD_REVISION
    result=sources(engines,tracking=tracking,after_preview=exercise)
    assert result['passed'] and result['submission']['passed']
    result['executionSeals']=result.pop('submission')
    before=catalog(owner);before_facts=snapshot(owner);before_indexes=indexes(owner)
    migrate('retained-execution-seal-downgrade','downgrade','20261206_0157','0159 immutable business history requires retention')
    assert catalog(owner)==before and snapshot(owner)==before_facts and indexes(owner)==before_indexes
    with owner.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD_REVISION
    security()
    result.update(emptyRoundtripCatalogAclIndexesExact=True,retainedExecutionSealHistoryBlocksDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,migrationHead=HEAD_REVISION,productionAcceptance=False)
    return result
