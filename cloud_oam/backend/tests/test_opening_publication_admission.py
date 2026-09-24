from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4,UUID
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from app.formal_services import opening_publication_admission as service
from app.formal_services.opening_stocktake import OpeningStocktakeError
from app.opening_stocktake_schemas import OpeningStocktakeFromPublicationIn

@pytest.fixture
def setup():
    actor=SimpleNamespace(user_id='person',person_id=uuid4(),authorization_version=3)
    region,publication,source,run=(uuid4() for _ in range(4))
    row=dict(sync_inbox_event_id=str(uuid4()),external_object_version_id=str(uuid4()),external_business_key='control:one',
        material_id=str(uuid4()),condition_code='new',control_qty='1.230',mapping_status='resolved',mapping_note='',source_updated_at=None,payload_sha256='a'*64)
    doc=dict(publication_id=str(publication),publication_sha256='b'*64,actor_person_id=str(actor.person_id),authorization_version=3,
        region_org_id=str(region),source_system_id=str(source),sync_run_id=str(run),sync_scope_key='region:'+str(region),
        captured_at='2026-09-20T00:00:00+00:00',valid_until='2026-09-20T00:45:00+00:00',control_lines=[row])
    db=SimpleNamespace(get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),no_autoflush=nullcontext(),scalar=Mock(return_value=doc),commit=Mock())
    return SimpleNamespace(actor=actor,region=region,publication=publication,source=source,run=run,row=row,doc=doc,db=db)

def test_history_selection_preserves_unknown_time_and_decimal(setup):
    s=setup;doc,lines=service.selection(s.db,actor=s.actor,region=s.region,publication=s.publication)
    assert lines[0].source_updated_at is None and str(lines[0].control_qty)=='1.230'
    assert s.db.scalar.call_args.args[1]['current'] is False and not s.db.commit.called

@pytest.mark.parametrize('field,value',[('authorization_version',True),('actor_person_id','foreign'),('region_org_id',str(uuid4())),
    ('publication_sha256','x'),('control_lines',None),('captured_at','2026-09-20T00:00:00')])
def test_invalid_or_foreign_selection_refused(setup,field,value):
    setup.doc[field]=value
    with pytest.raises(OpeningStocktakeError) as error:service.selection(setup.db,actor=setup.actor,region=setup.region,publication=setup.publication)
    assert error.value.code=='control_publication_invalid'

@pytest.mark.parametrize('changes',[{'control_qty':1.23},{'mapping_status':'unresolved'},{'payload_sha256':'bad'},
    {'source_updated_at':'unknown'},{'extra':'secret'}])
def test_invalid_line_set_refused(setup,changes):
    setup.row.update(changes)
    with pytest.raises(OpeningStocktakeError):service.selection(setup.db,actor=setup.actor,region=setup.region)

def test_duplicates_and_cross_publication_refused(setup):
    setup.doc['control_lines'].append(deepcopy(setup.row))
    with pytest.raises(OpeningStocktakeError):service.selection(setup.db,actor=setup.actor,region=setup.region)
    setup.doc['control_lines'].pop()
    with pytest.raises(OpeningStocktakeError):service.selection(setup.db,actor=setup.actor,region=setup.region,publication=uuid4())

@pytest.mark.parametrize('code,expected',[('23514',412),('42501',403),('25001',503),('08006',503),(None,503)])
def test_database_errors_sanitized_without_fallback(setup,code,expected):
    setup.db.scalar.side_effect=OperationalError('SECRET SQL',{},SimpleNamespace(sqlstate=code))
    with pytest.raises(OpeningStocktakeError) as error:service.selection(setup.db,actor=setup.actor,region=setup.region)
    assert error.value.http_status_code==expected and 'SECRET' not in str(error.value.as_detail()) and setup.db.scalar.call_count==1

def test_new_start_checks_complete_set_and_scope(setup):
    s=setup;_,lines=service.selection(s.db,actor=s.actor,region=s.region)
    command=SimpleNamespace(region_org_id=s.region,control_source_system_id=s.source,control_sync_run_id=s.run,
        control_sync_scope_key=s.doc['sync_scope_key'],control_lines=lines)
    service.require_new_opening(s.db,actor=s.actor,command=command)
    assert s.db.scalar.call_args.args[1]['current'] is True
    command.control_lines=()
    with pytest.raises(OpeningStocktakeError,match='完整批次'):service.require_new_opening(s.db,actor=s.actor,command=command)
    s.doc['control_lines']=[];service.require_new_opening(s.db,actor=s.actor,command=command)
    command.control_sync_scope_key='foreign'
    with pytest.raises(OpeningStocktakeError):service.require_new_opening(s.db,actor=s.actor,command=command)

def test_request_rejects_client_control_coordinates_and_quantities():
    payload=dict(publication_id=str(uuid4()),region_org_id=str(uuid4()),task_no='OPEN-1',scopes=[dict(owner_org_id=str(uuid4()),location_id=str(uuid4()),assignee_user_id='actor')])
    assert OpeningStocktakeFromPublicationIn.model_validate(payload)
    for changes in ({'control_lines':[]},{'control_sync_run_id':str(uuid4())},{'source_system_id':str(uuid4())},{'publication_id':str(UUID(int=0))}):
        with pytest.raises(ValidationError):OpeningStocktakeFromPublicationIn.model_validate(payload|changes)

def test_selected_start_has_no_database_fallback(setup):
    setup.db.get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    with pytest.raises(OpeningStocktakeError) as error:service.selection(setup.db,actor=setup.actor,region=setup.region)
    assert error.value.code=='opening_publication_database_required' and not setup.db.scalar.called
