"""Trace recovery transport and permanent-fence migration contracts."""
from io import StringIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import hashlib,runpy
import pytest
from pydantic import ValidationError
from alembic.operations import Operations
from alembic.migration import MigrationContext
from sqlalchemy import create_engine,text,inspect
from app.daily_reconciliation.recovery_http import Reference,RecoveryOutput
from app.daily_reconciliation import recovery_security as catalog,review_entry
from app.routers import formal_daily_reconciliation as routes
from app.main import app
from app import database_security as security,oam_sync_scope_security as scope
from test_daily_review_http import http,private

PATH=Path(__file__).parents[1]/'alembic/versions/20261113_0134_daily_review_request_seals.py'
CONFIRM='permanently_prevent_original_daily_review_request'

def reference(cutoff=None):
    return dict(cutoff_id=cutoff or str(uuid4()),actor_person_id=str(uuid4()),original_authorization_version=1,
        original_review_version=0,operation='open',trace_request_id=uuid4().hex)

def missing(ref):
    return RecoveryOutput(reference=Reference.model_validate(ref),current_authorization_version=1,outcome='not_observed').model_dump(mode='json')

@pytest.fixture(scope='module')
def migration():return runpy.run_path(str(PATH))

def test_current_catalog_matches_frozen_functions_and_private_grants(migration):
    assert catalog.TABLES<=security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert not catalog.TABLES & security.RUNTIME_UPDATE_TABLES
    for coordinate,fact in catalog.FUNCTIONS.items():
        assert fact['sha256']==hashlib.sha256(migration['FUNCTIONS'][coordinate[0]][2].encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]==fact['sha256']
        assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
    for name,fact in catalog.TRIGGERS.items():assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==fact
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0134['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0133['rsc_oam_runtime_binding_ready_0044()'][6]

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_frozen_sql_parses_and_refuses_destructive_nonempty_downgrade(migration,direction):
    from pglast import parser
    for name,(args,result,body,_) in migration['FUNCTIONS'].items():
        parser.parse_plpgsql_json(f'CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $x$'+body+'$x$')
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    if direction=='upgrade':assert sql.count('ENABLE ALWAYS TRIGGER')==7 and sql.count('CREATE CONSTRAINT TRIGGER')==2
    else:assert sql.index('populated downgrade refused')<sql.index('DROP TRIGGER')<sql.index('DROP TABLE')

def test_sqlite_empty_upgrade_roundtrip_and_partial_audit_downgrade_refusal(migration):
    engine=create_engine('sqlite://')
    try:
        with engine.begin() as c:
            c.execute(text('CREATE TABLE audit_events (aggregate_type TEXT,action TEXT)'))
            with Operations.context(MigrationContext.configure(c)):migration['upgrade']()
            assert 'daily_review_request_seals' in inspect(c).get_table_names()
            with Operations.context(MigrationContext.configure(c)):migration['downgrade']();migration['upgrade']()
            c.execute(text("INSERT INTO audit_events VALUES ('daily_review_request_seal','daily_reconciliation.request_sealed')"))
        with engine.connect() as c:
            with pytest.raises(Exception,match='0134'):
                with Operations.context(MigrationContext.configure(c)):migration['downgrade']()
            c.rollback();assert 'daily_review_request_seals' in inspect(c).get_table_names()
    finally:engine.dispose()

@pytest.mark.parametrize('value',[False,None,1,'true'])
def test_ambiguous_or_missing_unique_index_is_refused(value):
    db=Mock();db.scalar.return_value=value
    with pytest.raises(Exception,match='unique_index_drift'):catalog.validate_recovery_catalog(db)

@pytest.mark.parametrize('field,value',[
 ('original_authorization_version',True),('original_authorization_version',0),('original_review_version',-1),
 ('original_review_version',True),('actor_person_id','00000000-0000-0000-0000-000000000000'),
 ('trace_request_id','bad request'),('operation','write_stock'),('access_token','secret'),
 ('idempotency_key','secret-key'),('explanation','private body'),
])
def test_reference_accepts_only_safe_coordinates(field,value):
    with pytest.raises(ValidationError):Reference.model_validate({**reference(),field:value})

@pytest.mark.parametrize('change',[{'outcome':'found'},{'outcome':'sealed'},{'automatic_retry_allowed':True},{'current_authorization_version':0}])
def test_response_cannot_turn_missing_into_success_or_retry_permission(change):
    with pytest.raises(ValidationError):RecoveryOutput.model_validate({**missing(reference()),**change})

@pytest.mark.parametrize('suffix,seal',[('/request-recovery',False),('/request-seal',True)])
def test_cookie_reference_uses_fixed_worker_and_bound_result(http,suffix,seal):
    ref=reference(http.command['cutoff_id']);executor=Mock(return_value={'outcome':'observed','recovery':missing(ref)})
    app.dependency_overrides[routes.get_review_recovery_executor]=lambda:executor
    headers={k:v for k,v in http.headers.items() if k!='Authorization'};headers.update(Cookie='access_token=synthetic',Origin='https://rscwz.cn')
    body=dict(expected_authorization_version=1,reference=ref)
    if seal:body['confirmation']=CONFIRM
    r=http.client.post(http.url+suffix,json=body,headers=headers)
    assert r.status_code==200 and r.json()['outcome']=='not_observed' and not r.json()['automatic_retry_allowed'];private(r)
    assert executor.call_args.kwargs['seal'] is seal and executor.call_args.kwargs['reference']==ref

@pytest.mark.parametrize('fault',['missing_confirmation','bad_confirmation','wrong_path','foreign_origin','duplicate_request','private_body','client_dsn'])
def test_recovery_transport_refuses_unbound_or_unauthorized_requests(http,fault):
    ref=reference(http.command['cutoff_id']);executor=Mock()
    app.dependency_overrides[routes.get_review_recovery_executor]=lambda:executor
    headers={k:v for k,v in http.headers.items() if k!='Authorization'};headers.update(Cookie='access_token=synthetic',Origin='https://rscwz.cn')
    body=dict(expected_authorization_version=1,reference=ref,confirmation=CONFIRM)
    if fault=='missing_confirmation':body.pop('confirmation')
    if fault=='bad_confirmation':body['confirmation']='yes'
    if fault=='wrong_path':ref['cutoff_id']=str(uuid4())
    if fault=='foreign_origin':headers['Origin']='https://other.invalid'
    if fault=='duplicate_request':headers=list(headers.items())+[('X-Request-Id','other')]
    if fault=='private_body':ref['command']={'explanation':'private'}
    if fault=='client_dsn':body['database_url']='postgresql://untrusted'
    r=http.client.post(http.url+'/request-seal',json=body,headers=headers)
    assert r.status_code==(403 if fault=='foreign_origin' else 422);private(r);assert not executor.called

@pytest.mark.parametrize('fault',['unknown','wrong_reference','wrong_authorization','invalid','exception'])
def test_uncertain_or_unbound_worker_output_preserves_unknown_result(http,fault):
    ref=reference(http.command['cutoff_id']);value=missing(ref)
    if fault=='wrong_reference':value['reference']['trace_request_id']=uuid4().hex
    if fault=='wrong_authorization':value['current_authorization_version']=2
    if fault=='invalid':value['outcome']='sealed'
    executor=Mock(return_value={'outcome':'unknown'} if fault=='unknown' else {'outcome':'observed','recovery':value})
    if fault=='exception':executor.side_effect=review_entry.ProcessOutcomeUnknown('secret')
    app.dependency_overrides[routes.get_review_recovery_executor]=lambda:executor
    r=http.client.post(http.url+'/request-recovery',json=dict(expected_authorization_version=1,reference=ref),headers=http.headers)
    assert r.status_code==503 and 'secret' not in r.text and executor.call_count==1;private(r)
