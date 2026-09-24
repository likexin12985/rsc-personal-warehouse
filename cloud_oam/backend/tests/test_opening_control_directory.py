"""Directory authorization, drift, query shape and sanitized failure boundary."""
from dataclasses import replace
from uuid import UUID,uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.formal_services import opening_control_directory as service
from app.formal_services.opening_start_options import OpeningStartOptionError
from test_opening_start_options import db,world,NOW,_client,PREFIX


def read(world,**changes):
    args=dict(actor=world.principals['admin'],region_org_id=world.region_x.id,limit=50,now=NOW)
    args.update(changes)
    return service.list_control_batches(world.db,**args)


def test_empty_is_not_zero_stock_or_permission_to_start_and_does_not_write(world):
    statements=[]
    def record(*args): statements.append(args[2])
    event.listen(world.db.bind,'before_cursor_execute',record)
    try: page=read(world)
    finally: event.remove(world.db.bind,'before_cursor_execute',record)
    assert not page.items and page.next_after_id is None and not page.start_ready
    assert page.admission_status=='not_evaluated'
    assert statements and all(sql.lstrip().startswith('SELECT') for sql in statements)
    assert all('FOR UPDATE' not in sql and 'FOR SHARE' not in sql for sql in statements)


@pytest.mark.parametrize('actor',['manager_y','technician'])
def test_foreign_or_nonmanager_actor_refused_before_private_read(world,monkeypatch,actor):
    def unexpected(*a,**kw): pytest.fail('private directory read before authorization')
    monkeypatch.setattr(service,'_summaries',unexpected)
    with pytest.raises(OpeningStartOptionError) as error: read(world,actor=world.principals[actor])
    assert error.value.http_status_code==403


def test_exact_region_manager_and_stale_version(world):
    assert not read(world,actor=world.principals['manager_x']).items
    with pytest.raises(OpeningStartOptionError) as error:
        read(world,actor=replace(world.principals['admin'],authorization_version=999))
    assert error.value.http_status_code==403


@pytest.mark.parametrize('changes',[{'limit':0},{'limit':101},{'limit':True},{'after_id':UUID(int=0)},{'region_org_id':UUID(int=0)}])
def test_invalid_coordinates_refused(world,changes):
    with pytest.raises(OpeningStartOptionError) as error: read(world,**changes)
    assert error.value.http_status_code==422


def test_no_fallback_on_database_capability_failure(world,monkeypatch):
    def missing(*a,**kw): raise OperationalError('SECRET SQL',{},Exception('SECRET connection'))
    monkeypatch.setattr(service,'_summaries',missing)
    with pytest.raises(OpeningStartOptionError) as error: read(world)
    assert error.value.http_status_code==503 and 'SECRET' not in str(error.value.as_detail())


def test_replacement_during_read_refuses_page(world,monkeypatch):
    from app.opening_control_directory_schemas import OpeningControlBatchOut
    from datetime import timedelta
    row=OpeningControlBatchOut(publication_id=uuid4(),source_system_id=uuid4(),source_name='Synthetic',
        captured_at=NOW,published_at=NOW,valid_until=NOW+timedelta(minutes=1),record_count=0,is_latest=True)
    results=iter([(row,),(row.model_copy(update={'is_latest':False}),)])
    monkeypatch.setattr(service,'_summaries',lambda *a,**kw:next(results))
    with pytest.raises(OpeningStartOptionError) as error: read(world)
    assert error.value.http_status_code==503


def test_authentication_changes_after_summary_read_are_not_hidden_by_identity_map(world,monkeypatch):
    from app.models import User
    original=service._summaries
    def changed(*a,**kw):
        rows=original(*a,**kw)
        world.db.get(User,world.admin.user.id).authorization_version+=1
        world.db.flush()
        return rows
    monkeypatch.setattr(service,'_summaries',changed)
    with pytest.raises(OpeningStartOptionError) as error: read(world)
    assert error.value.http_status_code==403


def test_http_contract_no_cache_strict_query_and_permission(world):
    url=PREFIX+'/control-batches'; query={'region_org_id':str(world.region_x.id)}
    client=_client(world)
    response=client.get(url,params=query)
    assert response.status_code==200,response.text
    assert response.json()['start_ready'] is False
    assert 'no-store' in response.headers['cache-control']
    assert _client(world,'manager_y').get(url,params=query).status_code==403
    for extra in ('&limit=1&limit=2','&actor=admin','&publication_id='+str(uuid4())):
        assert client.get(url+'?region_org_id='+query['region_org_id']+extra).status_code==422


def test_output_rejects_private_fields_wrong_times_and_boolean_counts():
    from app.opening_control_directory_schemas import OpeningControlBatchOut
    from datetime import timedelta
    from pydantic import ValidationError
    row=dict(publication_id=uuid4(),source_system_id=uuid4(),source_name='Synthetic',captured_at=NOW,
        published_at=NOW,valid_until=NOW+timedelta(minutes=1),record_count=0,is_latest=True)
    for changes in ({'storage_key':'private'},{'captured_at':NOW+timedelta(minutes=3)},
                    {'valid_until':NOW},{'record_count':True},{'publication_id':UUID(int=0)}):
        with pytest.raises(ValidationError): OpeningControlBatchOut(**(row|changes))
