"""Role drift is rejected even when table SELECT alone looks valid."""
from pathlib import Path
import json,runpy,subprocess,sys
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy import text
from pglast import parser
from app.daily_reconciliation import capture_security as security
from app.daily_reconciliation.capture_role_contract import ROLES,CONTROL_TABLES,LEDGER_TABLES

FIELDS=('present','role_safe','database_safe','schema_safe','table_safe','column_safe',
        'sequence_safe','function_safe','defaults_safe','parameter_safe','policy_safe')
def rows(): return [dict(role_name=role,**dict.fromkeys(FIELDS,True)) for role in ROLES]

@pytest.mark.parametrize('field',FIELDS)
@pytest.mark.parametrize('value',[False,None])
def test_any_unsafe_or_unknown_dimension_refuses(field,value):
    data=rows();data[0][field]=value
    with pytest.raises(security.CaptureRoleSecurityError,match=field):
        security.assert_catalog(data,allow_absent=True)

def test_optional_disabled_installation_requires_both_roles_absent():
    data=rows()
    for row in data: row.update(dict.fromkeys(FIELDS,False))
    assert security.assert_catalog(data,allow_absent=True) is False
    with pytest.raises(security.CaptureRoleSecurityError):security.assert_catalog(data)
    data[0]=rows()[0]
    with pytest.raises(security.CaptureRoleSecurityError):security.assert_catalog(data,allow_absent=True)
    assert security.assert_catalog(rows()) is True

@pytest.mark.parametrize('mutate',[lambda r:r[:-1],lambda r:r+r[:1],lambda r:[dict(r[0],role_name='wrong'),r[1]]])
def test_missing_duplicate_or_unknown_role_refuses(mutate):
    with pytest.raises(security.CaptureRoleSecurityError,match='role_set'):
        security.assert_catalog(mutate(rows()),allow_absent=True)

def test_catalog_and_both_parameter_styles_parse():
    statement=text(security.ROLE_CATALOG_SQL).bindparams(manifest=security.MANIFEST,login_enabled=True)
    compiled=str(statement.compile(dialect=postgresql.dialect(),compile_kwargs={'literal_binds':True}))
    parser.parse_sql(compiled)
    class Cursor:
        def execute(self,query,parameters):
            assert parameters==dict(manifest=security.MANIFEST,login_enabled=True)
            assert "n.nspname !~ '^pg_'" in query and '%(manifest)s' in query
        def fetchall(self): return rows()
    assert security.validate_capture_roles_cursor(Cursor())
    assert len(CONTROL_TABLES)==27 and len(LEDGER_TABLES)==6

def test_cli_head_tracks_alembic_and_fixed_readers_share_contract():
    from app.daily_reconciliation import capture_provisioning as provisioning,control_source,ledger_capture
    from test_alembic_migrations import HEAD_REVISION
    assert provisioning.REQUIRED_HEAD==HEAD_REVISION
    assert control_source.TABLES==CONTROL_TABLES and ledger_capture.TABLES==LEDGER_TABLES

def test_cli_does_not_echo_credentials_from_errors(monkeypatch,capsys):
    script=Path(__file__).parents[2]/'scripts/configure_daily_capture_roles.py'
    cli=runpy.run_path(str(script))
    secret='synthetic-sensitive-argument-that-must-not-be-echoed'
    with pytest.raises(SystemExit):cli['main'](['--database','x','--unexpected',secret])
    assert secret not in capsys.readouterr().err
    monkeypatch.setenv('OAM_DAILY_CAPTURE_BOOTSTRAP_URL',secret)
    assert cli['main'](['--database','x'])==2
    output=capsys.readouterr();assert secret not in output.err+output.out
