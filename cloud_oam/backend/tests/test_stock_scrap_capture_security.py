from copy import deepcopy
import pytest
from app import stock_scrap_security as scrap
from app.daily_reconciliation import capture_security
from app.daily_reconciliation.capture_role_contract import ROLES

ns=scrap.__dict__
verify=scrap.verify

def actual(configured):
    value={family:{name:deepcopy(change['after']) for name,change in scrap.DATA[family].items()} for family in ('tables','functions')}
    if configured:
        for role,(tables,_) in ROLES.items():
            for name in set(tables)&set(value['tables']):
                value['tables'][name]['acl'].append(dict(grantee=role,privilege='SELECT',grantable=False))
                value['tables'][name]['acl'].sort(key=lambda a:(a['grantee'],a['privilege'],a['grantable']))
    return value

def setup(monkeypatch, value, configured):
    monkeypatch.setitem(ns,'verify_uuid',lambda db:None)
    monkeypatch.setitem(ns,'snapshot',lambda db:value)
    monkeypatch.setattr(capture_security,'validate_capture_roles',lambda db,allow_absent:configured)

@pytest.mark.parametrize('configured',[False,True])
def test_complete_optional_contract_accepted_without_changing_frozen_data(monkeypatch,configured):
    before=deepcopy(scrap.DATA)
    value=actual(configured);observed=deepcopy(value)
    setup(monkeypatch,value,configured);verify(object())
    assert scrap.DATA==before and value==observed

@pytest.mark.parametrize('case',['missing_select','extra_update','grant_option','wrong_role','public','wrong_table','missing_api','column','trigger','function','overload','roles_absent_but_acl_present'])
def test_malformed_catalog_refuses(monkeypatch,case):
    value=actual(True);table=value['tables']['audit_events'];acl=table['acl']
    if case=='missing_select':acl[:]=[a for a in acl if a['grantee']!='rsc_control_capture']
    elif case=='extra_update':acl.append(dict(grantee='rsc_control_capture',privilege='UPDATE',grantable=False))
    elif case=='grant_option':next(a for a in acl if a['grantee']=='rsc_control_capture')['grantable']=True
    elif case in ('wrong_role','public'):next(a for a in acl if a['grantee']=='rsc_control_capture')['grantee']='PUBLIC' if case=='public' else 'unexpected_reader'
    elif case=='wrong_table':value['tables']['stock_scrap_request_seals']['acl'].append(dict(grantee='rsc_control_capture',privilege='SELECT',grantable=False))
    elif case=='missing_api':acl[:]=[a for a in acl if a['grantee']!='star_oam_api']
    elif case=='column':table['columns'][0]['not_null']=not table['columns'][0]['not_null']
    elif case=='trigger':table['triggers'][0]['tgenabled']='D'
    elif case=='function':next(iter(value['functions'].values()))['prosrc']+='\n'
    elif case=='overload':
        row=deepcopy(next(iter(value['functions'].values())))
        value['functions'][row['proname']+'(text,text,text,text,text,text)']=row
    setup(monkeypatch,value,case!='roles_absent_but_acl_present')
    with pytest.raises(ValueError):verify(object())


def test_full_capture_validation_failure_not_suppressed(monkeypatch):
    setup(monkeypatch,actual(True),True)
    def reject(db,allow_absent):raise capture_security.CaptureRoleSecurityError('daily_capture_boundary: unsafe')
    monkeypatch.setattr(capture_security,'validate_capture_roles',reject)
    with pytest.raises(capture_security.CaptureRoleSecurityError):verify(object())
