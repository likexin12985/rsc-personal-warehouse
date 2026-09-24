"""Formal review catalog, safe empty rollback, and changed-permission retention."""
from io import StringIO
from pathlib import Path
from unittest.mock import Mock
import hashlib,runpy
import pytest
from sqlalchemy import create_engine,text,inspect
from alembic.operations import Operations
from alembic.migration import MigrationContext
from app import database_security as security,oam_sync_scope_security as scope
from app.daily_reconciliation import review_security as catalog

PATH=Path(__file__).parents[1]/'alembic/versions/20261111_0132_daily_reconciliation_review.py'
@pytest.fixture(scope='module')
def migration():return runpy.run_path(str(PATH))

def test_frozen_catalog_and_narrow_permissions(migration):
    assert catalog.TABLES<=security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert not catalog.TABLES & security.CONTROL_PRIVATE_TABLES
    assert not catalog.TABLES & security.RUNTIME_UPDATE_TABLES
    assert security.RUNTIME_UPDATE_COLUMNS['daily_review_bindings']==frozenset({'version','last_event_id','state_jsonb','updated_at'})
    assert set(security.FORMAL_FILE_INTERNAL_FUNCTIONS)==set(security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES)==set(security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256)
    assert len(catalog.FUNCTIONS)==12 and sum(x['api'] for x in catalog.FUNCTIONS.values())==1
    for coord,f in catalog.FUNCTIONS.items():
        src=migration['FUNCTIONS'][coord[0]]
        assert f['sha256']==hashlib.sha256(src[2].encode()).hexdigest()
        hashes=security.RUNTIME_FUNCTION_BODY_SHA256 if f['api'] else security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256
        from app.daily_reconciliation.evidence_security import FUNCTION_HASHES
        assert hashes[coord]==FUNCTION_HASHES.get(coord,f['sha256'])
    assert len(catalog.TRIGGERS)==12 and sum(v[-1] for v in catalog.TRIGGERS.values())==3
    for n,f in catalog.TRIGGERS.items():assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[n]==f
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0132['rsc_oam_runtime_binding_ready_0044()'][6]

def test_original_opening_body_retained(migration):
    for name,(old,new) in migration['GUARD_SOURCES'].items():
        start=new.index('BEGIN')+5;end=new.index('\n    END IF;')+len('\n    END IF;\n')
        assert new[:start]+new[end:]==old
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[(name,'')]==hashlib.sha256(new.encode()).hexdigest()

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_pg_sql_parses_and_preflight_precedes_destructive_work(migration,direction):
    from pglast import parser
    for name,(args,result,body,_) in migration['FUNCTIONS'].items():
        parser.parse_plpgsql_json(f'CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $x$'+body+'$x$')
    out=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':out})):migration[direction]()
    sql=out.getvalue();parser.parse_sql(sql)
    assert 'direct schema owner required' in sql
    if direction=='upgrade':assert sql.count('CREATE CONSTRAINT TRIGGER')==3 and sql.count('ENABLE ALWAYS TRIGGER')==12
    else:assert sql.index('populated downgrade refused')<sql.index('permission seed drift')<sql.index('DROP TRIGGER')

def test_frozen_pg_models_match(migration):
    from app.daily_reconciliation.review_models import DailyReviewEvent,DailyReviewBinding,DailyReviewConsumption
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    tables=[m.__table__ for m in (DailyReviewEvent,DailyReviewBinding,DailyReviewConsumption)]
    assert migration['DDL']['postgresql'][:3]==[str(CreateTable(t).compile(dialect=postgresql.dialect())) for t in tables]

@pytest.fixture
def database(migration):
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        for sql in ['CREATE TABLE permissions (id TEXT PRIMARY KEY,resource TEXT,action TEXT,field_code TEXT,description TEXT,created_at DATETIME,updated_at DATETIME)',
                    'CREATE TABLE role_permissions (id TEXT PRIMARY KEY,role_id TEXT,permission_id TEXT,effect TEXT,created_at DATETIME)',
                    'CREATE TABLE audit_events (aggregate_type TEXT)','CREATE TABLE state_transition_events (aggregate_type TEXT)']:
            c.execute(text(sql))
        with Operations.context(MigrationContext.configure(c)):migration['upgrade']()
    yield engine
    engine.dispose()

def test_sqlite_exact_seeds_and_empty_roundtrip(database,migration):
    with database.begin() as c:
        assert {a for a, in c.execute(text('SELECT action FROM permissions'))}=={'create_daily','explain_daily','approve_daily'}
        assert c.execute(text('SELECT count(*) FROM role_permissions')).scalar_one()==4
        assert len(inspect(c).get_foreign_keys('daily_review_events'))==6
        with Operations.context(MigrationContext.configure(c)):migration['downgrade']()
        assert c.execute(text('SELECT count(*) FROM permissions')).scalar_one()==0
        assert not catalog.TABLES & set(inspect(c).get_table_names())
        with Operations.context(MigrationContext.configure(c)):migration['upgrade']()

@pytest.mark.parametrize('change',['description','effect','extra_role','missing_grant','history'])
def test_downgrade_retains_changed_permissions_or_history(database,migration,change):
    with database.begin() as c:
        if change=='description':c.execute(text("UPDATE permissions SET description='Administrator customization'"))
        elif change=='effect':c.execute(text("UPDATE role_permissions SET effect='deny'"))
        elif change=='extra_role':c.execute(text("INSERT INTO role_permissions SELECT 'custom-grant',role_id,permission_id,effect,created_at FROM role_permissions LIMIT 1"))
        elif change=='missing_grant':c.execute(text('DELETE FROM role_permissions WHERE id=(SELECT id FROM role_permissions LIMIT 1)'))
        else:c.execute(text("INSERT INTO audit_events VALUES ('daily_reconciliation_run')"))
    with database.connect() as c:
        before=list(c.execute(text('SELECT * FROM permissions')))
        with pytest.raises(Exception,match='0132'):
            with Operations.context(MigrationContext.configure(c)):migration['downgrade']()
        c.rollback()
        assert list(c.execute(text('SELECT * FROM permissions')))==before
        assert catalog.TABLES<=set(inspect(c).get_table_names())

@pytest.mark.parametrize('change',['missing','wrong_target','not_deferred','trigger_disabled','duplicate'])
def test_startup_requires_enforced_cyclic_seals(change):
    rows=[dict(zip(('conname','source_table','target_table','definition'),r),flags_safe=True,triggers_safe=True) for r in catalog.SEALS]
    if change=='missing':rows.pop()
    elif change=='wrong_target':rows[0]['target_table']='users'
    elif change=='not_deferred':rows[0]['flags_safe']=False
    elif change=='trigger_disabled':rows[0]['triggers_safe']=False
    else:rows.append(rows[0])
    db=Mock();db.execute.return_value.mappings.return_value.all.return_value=rows
    with pytest.raises(catalog.DailyReviewSecurityError):catalog.validate_review_catalog(db)
