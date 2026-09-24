"""Formal daily archive migration and exact private security catalog."""
from io import StringIO
from pathlib import Path
import hashlib,runpy
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security, oam_sync_scope_security as scope

PATH=Path(__file__).parents[1]/'alembic/versions/20261109_0130_daily_reconciliation_cutoffs.py'

@pytest.fixture(scope='module')
def migration():return runpy.run_path(str(PATH))

def test_complete_private_catalog_and_deferred_guards(migration):
    assert set(migration['TABLES'])<=security.CONTROL_PRIVATE_TABLES
    for coordinate,fact in migration['FUNCTIONS'].items():
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]==hashlib.sha256(fact['body'].encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate]==('f',fact['result'],False)
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]==fact['sha256']
        assert (coordinate in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS)==(fact['result']=='void')
    for name,(table,function,events,kind,deferred) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',kind,deferred,deferred,deferred)
    assert sum(v[-1] for v in migration['TRIGGERS'].values())==2
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0129['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0130['rsc_oam_runtime_binding_ready_0044()'][6]

def test_daily_catalog_selector_retains_unknown_family_members():
    rows=[dict(function_name=name,argument_types=types) for name,types in security._daily_catalog.FUNCTIONS]
    unknown=dict(function_name='rsc_unreviewed_daily_function_0130',argument_types='')
    assert security._select_material_request_approval_functions(rows+[unknown])==rows+[unknown]

def test_sql_preserves_reviewed_document_protocols(migration):
    validator=migration['FUNCTIONS'][('rsc_validate_daily_mapping_0130','jsonb, uuid, uuid')]['body']
    archive=migration['FUNCTIONS'][('rsc_guard_daily_cutoff_0130','')]['body']
    assert "'rsc.daily_comparison_mapping_candidate.v1'" in validator
    assert "'rsc.daily_cutoff_receipt_candidate.v1'" in archive

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_frozen_migration_parses_and_keeps_existing_facts(migration,direction):
    from pglast import parser
    for (name,_),f in migration['FUNCTIONS'].items():
        parser.parse_plpgsql_json(f"CREATE FUNCTION {name}({f['args']}) RETURNS {f['result']} LANGUAGE plpgsql AS $x$"+f['body']+'$x$')
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert 'direct schema owner required' in sql and 'ACCESS EXCLUSIVE MODE' in sql
    assert 'DELETE FROM' not in sql and 'CREATE OR REPLACE' not in sql
    if direction=='upgrade':
        assert sql.count('CREATE CONSTRAINT TRIGGER')==2 and sql.count('ENABLE ALWAYS TRIGGER')==6
    else:
        assert sql.index('daily facts and audit must be retained')<sql.index('DROP TRIGGER')<sql.index('DROP TABLE')

def test_models_and_frozen_sql_schema_match(migration):
    from app.daily_reconciliation.mapping_models import DailyMappingDecision
    from app.daily_reconciliation.cutoff_models import DailyCutoff
    from sqlalchemy.dialects import postgresql,sqlite
    from sqlalchemy.schema import CreateTable,CreateIndex
    # The query index is added in 0131 and tested against that migration.
    models=(DailyMappingDecision.__table__,DailyCutoff.__table__)
    for dialect in [postgresql.dialect(),sqlite.dialect()]:
        compiled=[str(CreateTable(t).compile(dialect=dialect)) for t in models]+[str(CreateIndex(i).compile(dialect=dialect)) for t in models for i in sorted(t.indexes,key=lambda i:i.name) if i.name!='ix_daily_cutoff_region_id']
        assert compiled==migration['DDL'][dialect.name]
