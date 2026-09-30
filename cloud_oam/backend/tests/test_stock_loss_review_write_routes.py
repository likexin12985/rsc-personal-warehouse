"""Approval and permanent seal HTTP COMMIT boundaries, with exact read recovery."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from test_stock_loss_review_seals import (db, world, stock, allowed, evidence, regional,
    headquarters, state, review_world, candidate, snapshot)
from test_stock_loss_source_routes import client, private, PATH
from test_stock_loss_seals import stock_facts


def post(client, w, value, action='review', **headers):
    suffix = '/'+w.stage+'-reviews'+('/request-seal' if action=='seal' else '')
    return client.post(PATH+suffix,json=value.model_dump(mode='json'),headers={
        'X-Request-ID':value.original.request_id,'Idempotency-Key':value.original.idempotency_key,**headers})


def inventory_facts(db):
    tables = ('stock_accounts', 'stock_balances', 'inventory_transactions', 'inventory_movements',
        'inventory_movement_serials', 'serial_current_positions', 'inventory_serials', 'inventory_ledger_heads')
    return {table: tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
        for table in tables}


def test_http_approval_commits_once_and_seal_recovers_exact_result(db, review_world, candidate, client):
    w=review_world; db.commit(); before=inventory_facts(db)
    notifications = db.scalar(text('SELECT count(*) FROM notification_events WHERE event_type=:kind'),
        {'kind':w.service.KIND})
    response=post(client,w,candidate)
    assert response.status_code==200,response.text
    private(response); assert response.json()['stock_effect']=='none'
    assert inventory_facts(db)==before
    assert db.scalar(text('SELECT count(*) FROM notification_events WHERE event_type=:kind'),
        {'kind':w.service.KIND}) == notifications + 1
    committed=snapshot(db)
    assert post(client,w,candidate).json()==response.json() and snapshot(db)==committed
    found=client.post(w.path,json=w.request.model_dump(mode='json'))
    assert found.status_code==200 and found.json()['review']==response.json()
    assert found.json()['retry_permitted'] is False
    assert post(client,w,candidate,'seal').json()==found.json()
    assert snapshot(db)==committed


def test_http_seal_is_stock_neutral_and_late_review_is_rejected(db, review_world, candidate, client):
    w=review_world;db.commit();before=stock_facts(db)
    response=post(client,w,candidate,'seal')
    assert response.status_code==200,response.text
    assert response.json()['lookup_status']=='sealed' and response.json()['retry_permitted'] is False
    assert stock_facts(db)==before
    snapshot_after=snapshot(db)
    late=post(client,w,candidate)
    assert late.status_code==409 and late.json()['detail']['code']=='stock_loss_review_request_sealed'
    found=client.post(w.path,json=w.request.model_dump(mode='json'))
    assert found.json()==response.json() and snapshot(db)==snapshot_after
    private(response);private(late);private(found)


@pytest.mark.parametrize('action',['review','seal'])
@pytest.mark.parametrize('committed',[False,True])
def test_lost_commit_response_is_recovered_without_replaying(db, review_world, candidate, client, monkeypatch, action, committed):
    w=review_world;db.commit();before=snapshot(db);commit=db.commit
    def failure():
        if committed:commit()
        raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-ERROR'))
    with monkeypatch.context() as p:
        p.setattr(db,'commit',failure)
        response=post(client,w,candidate,action)
    assert response.status_code==503,response.text
    private(response);assert 'PRIVATE-' not in response.text
    after=snapshot(db)
    found=client.post(w.path,json=w.request.model_dump(mode='json'))
    assert found.status_code==200,found.text
    assert found.json()['lookup_status']==(('found' if action=='review' else 'sealed') if committed else 'not_found')
    assert found.json()['retry_permitted'] is False and snapshot(db)==after
    if not committed:assert after==before


@pytest.mark.parametrize('action',['review','seal'])
@pytest.mark.parametrize('fault,status',[('operator',403),('permission',403),('trace',400),('key',400),('hash',422)])
def test_write_coordinates_and_current_permissions_are_rechecked(db, allowed, review_world, candidate, client, action, fault, status):
    w=review_world;headers={}
    if fault=='operator':candidate=candidate.model_copy(update={'operator_person_id':uuid4()})
    elif fault=='permission':allowed.world.current_principal=replace(w.actor,entitlements=tuple(g for g in w.actor.entitlements if g.action!=w.service.ACTION))
    elif fault=='hash':candidate=candidate.model_copy(update={'request_hash':'f'*64})
    else:headers['X-Request-ID' if fault=='trace' else 'Idempotency-Key']=uuid4().hex
    db.commit();before=snapshot(db)
    response=post(client,w,candidate,action,**headers)
    assert response.status_code==status,response.text
    private(response);assert snapshot(db)==before
