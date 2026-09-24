"""Restore request identity is explicit and secret values never become errors."""
from pathlib import Path
import runpy

import pytest
from pglast import parser

from app.daily_reconciliation.capture_provisioning import checked_passwords
from app.daily_reconciliation.capture_restore import (
    EMPTY_DATABASE_SQL, ROLE_STATE_SQL, restore_coordinates,
)
from app.daily_reconciliation.capture_role_contract import ROLES
from app.daily_reconciliation.capture_security import CaptureRoleSecurityError

REQUEST='37bc9f4e-55a1-4ed9-9585-c681f043ca30'
DIGEST='a'*64

@pytest.mark.parametrize('restore_id',[None,False,[],{},'',REQUEST.upper(),'0'*32,
    '00000000-0000-0000-0000-000000000000','not-a-request'])
def test_restore_request_requires_canonical_nonzero_uuid(restore_id):
    with pytest.raises(CaptureRoleSecurityError,match='restore_id_invalid'):
        restore_coordinates(restore_id,DIGEST)

@pytest.mark.parametrize('digest',[None,0,[],{},'', 'a'*63,'A'*64,'0'*64,'g'*64])
def test_restore_digest_requires_reviewed_sha256(digest):
    with pytest.raises(CaptureRoleSecurityError,match='backup_digest_invalid'):
        restore_coordinates(REQUEST,digest)

def test_valid_coordinates_and_catalog_queries_parse():
    assert restore_coordinates(REQUEST,DIGEST)==(REQUEST,DIGEST)
    parser.parse_sql(str(EMPTY_DATABASE_SQL))
    parser.parse_sql(str(ROLE_STATE_SQL).replace(':roles',"ARRAY['rsc_control_capture']"))

@pytest.mark.parametrize('value',[None,[],{}, {k:[] for k in ROLES}, {k:'same'*10 for k in ROLES}])
def test_password_validation_refuses_invalid_shapes_without_leaking(value):
    with pytest.raises(CaptureRoleSecurityError):checked_passwords(value)

def test_restore_cli_missing_identity_refuses_before_connecting(monkeypatch,capsys):
    cli=runpy.run_path(str(Path(__file__).parents[2]/'scripts/configure_daily_capture_roles.py'))
    monkeypatch.delenv('OAM_DAILY_CAPTURE_BOOTSTRAP_URL',raising=False)
    for mode in ('prepare-restore','activate-restore','check-restore'):
        assert cli['main'](['--database','test','--mode',mode])==2
    assert 'daily_capture_configuration_refused' in capsys.readouterr().err
