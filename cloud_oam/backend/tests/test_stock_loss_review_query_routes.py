"""Registered approval inbox routes preserve scope, privacy and read-only facts."""
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from app.formal_services import stock_loss_review_query as query
from test_stock_loss_review_recovery import db, world, stock, allowed, evidence, regional, headquarters, review_world, state
from test_stock_loss_source_routes import client, private, PATH


def test_registered_inbox_and_detail_are_read_only(db, client, review_world):
    w=review_world;before=state(db);db.execute(text('PRAGMA query_only=ON'));prefix=PATH+'/reviews/'+w.stage
    result=client.get(prefix);assert result.status_code==200,result.text;private(result)
    item=result.json()['items'][0];detail=client.get(prefix+'/'+str(w.request.operation_id))
    assert detail.status_code==200,detail.text;private(detail)
    assert detail.json()['report']==item and item['operation_id']==str(w.request.operation_id)
    assert all(x not in detail.text for x in ['idempotency_key','storage_key','qr_code','stock_account_id'])
    assert state(db)==before


@pytest.mark.parametrize('fault,status', [('scope',403),('missing',404),('stage',422),('limit',422),('cursor',422),('view',422),('database',503)])
def test_inbox_errors_fail_closed(db, allowed, client, review_world, monkeypatch, fault, status):
    w=review_world;prefix=PATH+'/reviews/'+w.stage;params={}
    if fault=='scope':allowed.world.current_principal=replace(w.actor,assignments=())
    elif fault=='missing':prefix+='/'+str(uuid4())
    elif fault=='stage':prefix=PATH+'/reviews/external'
    elif fault=='limit':params['limit']=21
    elif fault=='cursor':params['after_id']='invalid'
    elif fault=='view':params['view']='everything'
    else:
        def fail(*args,**kwargs):raise OperationalError('PRIVATE-QUERY',{},Exception('PRIVATE-CONNECTION'))
        monkeypatch.setattr(query,'list_review_reports',fail)
    before=state(db);result=client.get(prefix,params=params)
    assert result.status_code==status,result.text;private(result)
    assert 'PRIVATE-' not in result.text and state(db)==before
