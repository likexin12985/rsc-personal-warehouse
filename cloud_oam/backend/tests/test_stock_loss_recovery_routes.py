"""The public recovery read is private, bounded and cannot replay stock."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.formal_services import stock_loss_recovery as recovery, stock_loss_commands as commands
from test_stock_loss_recovery import db, world, stock, allowed, readable, evidence, submission, lookup, snapshot
from test_stock_loss_source_routes import client, private, PATH


def test_http_reads_missing_and_committed_requests_without_writes(db, readable, evidence, client):
    value=submission(db,readable,evidence);db.commit()
    body=lookup(value).model_dump(mode='json')
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    response=client.post(PATH+'/request-lookup',json=body)
    assert response.status_code==200,response.text
    assert response.json()=={'lookup_status':'not_found','retry_permitted':False}
    private(response);assert snapshot(db)==before
    db.execute(text('PRAGMA query_only=OFF'))
    result=commands.submit_loss(db,actor=readable.actor,request=value);db.commit()
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    response=client.post(PATH+'/request-lookup',json=body)
    assert response.status_code==200,response.text
    assert response.json()['submission']['operation_id']==str(result.operation_id)
    assert response.json()['lookup_status']=='found' and response.json()['retry_permitted'] is False
    assert value.idempotency_key not in response.text and 'storage_key' not in response.text
    private(response);assert snapshot(db)==before


@pytest.mark.parametrize('fault,status',[('permission',403),('schema',422),('database',503)])
def test_lookup_errors_keep_original_request_and_hide_database_details(db,readable,evidence,client,monkeypatch,fault,status):
    value=submission(db,readable,evidence);db.commit();before=snapshot(db)
    body=lookup(value).model_dump(mode='json')
    if fault=='permission':
        readable.world.current_principal=replace(readable.actor,entitlements=())
    elif fault=='schema':body['request_hash']='not-a-digest'
    else:
        def unavailable(*args,**kwargs):
            raise OperationalError('PRIVATE-SQL',{'key':'PRIVATE-PARAMETER'},Exception('PRIVATE-ERROR'))
        monkeypatch.setattr(recovery,'lookup_loss_request',unavailable)
    response=client.post(PATH+'/request-lookup',json=body)
    assert response.status_code==status,response.text
    if fault=='database':assert response.json()['detail']['code']=='stock_loss_lookup_unavailable'
    assert 'PRIVATE-' not in response.text
    private(response);assert snapshot(db)==before


@pytest.mark.parametrize('changed',['request_id','idempotency_key','request_hash','expected_plan_hash'])
def test_http_conflicting_coordinates_cannot_disclose_success(db,readable,evidence,client,changed):
    value=submission(db,readable,evidence)
    result=commands.submit_loss(db,actor=readable.actor,request=value);db.commit()
    body=lookup(value).model_dump(mode='json')
    body[changed]=uuid4().hex if changed in ('request_id','idempotency_key') else 'f'*64
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    response=client.post(PATH+'/request-lookup',json=body)
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='stock_loss_request_conflict'
    assert str(result.operation_id) not in response.text and value.idempotency_key not in response.text
    private(response);assert snapshot(db)==before
