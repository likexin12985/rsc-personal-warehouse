from decimal import Decimal
from app.formal_services import material_request_return_quantities as return_service
"""Remaining quantities are disjoint facts, not authority to cancel."""
from decimal import Decimal as D
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select
from app.formal_services import material_request_remainder as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_remainder_schemas import BUCKETS
from app.models import User
from app.inventory_models import StockReservationRelease, ReceiptLine
from test_material_request_approval_service import approval_db
from test_material_request_lifecycle_service import _approved_request, _current_actor
from test_material_request_completion import cancelled
from test_material_request_reservation_release import release_world, _create, _input
from test_material_request_my_inbound import world, receipt_world, receiving_world, outbound_world, post, facts
from test_formal_material_request_api import api_client, REQUEST_ID

pytest_plugins = ('test_material_request_picking',)


def quantities(**changes):
    values = dict(approved='10', cancelled='1', posted='1', reserved='8', released='1',
        picked='6', dispatched='5', shipped='4', accepted='2', rejected='1')
    values.update(changes)
    return dict(line_id=uuid4(), **{k: D(v) for k,v in values.items()})


def test_mixed_stages_conserve_without_double_counting_release():
    row = service.partition(**quantities())
    assert [getattr(row, key) for key in BUCKETS] == ['2.000'] + ['1.000']*6
    assert row.approved_qty == '10.000'
    assert 'can_cancel' not in row.model_dump()


@pytest.mark.parametrize('change', [dict(approved='-1'),dict(approved='NaN'),dict(approved='Infinity'),
    dict(posted='2.001'),dict(released='3'),dict(reserved='11'),dict(cancelled='4'),dict(dispatched='7'),
    dict(shipped='6'),dict(accepted='4'),dict(rejected='3'),dict(posted='0.0001')])
def test_inconsistent_facts_are_never_clamped_to_zero(change):
    with pytest.raises(MaterialRequestReadError): service.partition(**quantities(**change))


def read_select_only(db, actor, request):
    statements=[]
    def observe(_conn,_cursor,sql,*args):
        statements.append(sql)
        assert sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper()
    event.listen(db.bind,'before_cursor_execute',observe)
    try: result=service.remaining_fulfillment(db,actor=actor,request_id=request.id)
    finally: event.remove(db.bind,'before_cursor_execute',observe)
    assert statements
    return result


@pytest.mark.parametrize('cancel', [False, True])
def test_approved_or_cancelled_demand_is_read_only(approval_db,cancel):
    db=approval_db
    if cancel: _,request,line,actor=cancelled(db)
    else:
        context,request,line,_=_approved_request(db,key='remainder-approved')
        actor=_current_actor(db,context)
    row=read_select_only(db,actor,request).lines[0]
    assert D(row.cancelled_qty if cancel else row.unreserved_qty)==line.requested_qty
    assert all(D(getattr(row,k))==0 for k in BUCKETS if cancel or k!='unreserved_qty')


def test_partial_release_uses_history_without_writer_lock(release_world):
    db,actor,request,original,serials,_=release_world
    amount='1.000' if serials else '0.125'
    _create(release_world,_input(db,original,amount,serials[:1]))
    row=read_select_only(db,actor,request).lines[0]
    assert D(row.reserved_unpicked_qty)==original.reserved_qty-D(amount)
    assert D(row.unreserved_qty)==D(row.approved_qty)-original.reserved_qty+D(amount)


def test_release_tampering_blocks_complete_result(release_world):
    db,actor,request,original,serials,_=release_world
    _create(release_world,_input(db,original,'1.000' if serials else '0.125',serials[:1]))
    db.scalar(select(StockReservationRelease)).released_qty += D('.001'); db.flush()
    with pytest.raises(MaterialRequestReadError):
        service.remaining_fulfillment(db,actor=actor,request_id=request.id)


@pytest.mark.parametrize('posted',[False,True])
def test_accepted_and_posted_are_separate(world,posted):
    db,actor,request,_=world
    if posted: post(world)
    before=facts(db)
    result=read_select_only(db,actor,request)
    amount=db.scalar(select(ReceiptLine.accepted_qty))
    assert sum(D(row.posted_qty if posted else row.accepted_unposted_qty) for row in result.lines)==amount
    assert sum(D(row.accepted_unposted_qty if posted else row.posted_qty) for row in result.lines)==0
    assert facts(db)==before


@pytest.mark.parametrize('change',['request','authorization'])
def test_changed_snapshot_does_not_return_old_assessment(approval_db,monkeypatch,change):
    db=approval_db
    context,request,_,_=_approved_request(db,key='remainder-race')
    actor=_current_actor(db,context)
    original=service.completion_quantities
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        if change=='request': request.version+=1
        else: db.get(User,actor.user_id).authorization_version+=1
        db.flush(); return result
    monkeypatch.setattr(service,'completion_quantities',changed)
    with pytest.raises(MaterialRequestReadError):
        service.remaining_fulfillment(db,actor=actor,request_id=request.id)


def test_http_read_available_with_writes_disabled(api_client,monkeypatch):
    client,db,principal,_,settings=api_client
    settings.material_request_writes_enabled=False
    row=return_service.apply_returned(service.partition(**quantities()), returned=Decimal(0), return_compensated=Decimal(0))
    value=return_service.ReturnedRemainderAssessmentOut(request_id=REQUEST_ID,revision_id=uuid4(),request_version=9,
        open_supply_tasks=0,pending_substitutions=0,lines=(row,))
    action=Mock(return_value=value); monkeypatch.setattr(return_service,'remaining_with_returns',action)
    result=client.get(f'/api/v1/material-requests/{REQUEST_ID}/remaining-fulfillment')
    assert result.status_code==200,result.text
    assert 'no-store' in result.headers['cache-control']
    assert result.json()['lines'][0]['reserved_unpicked_qty']=='1.000'
    assert action.call_args.kwargs['actor'] is principal['value']
    db.commit.assert_not_called()


@pytest.mark.parametrize('category,status',[('forbidden',403),('not_found',404),('service_unavailable',503)])
def test_http_failure_does_not_return_partial_quantities(api_client,monkeypatch,category,status):
    client,db,*_=api_client
    monkeypatch.setattr(return_service,'remaining_with_returns',Mock(side_effect=MaterialRequestReadError('remainder_invalid',category,'核验失败')))
    response=client.get(f'/api/v1/material-requests/{REQUEST_ID}/remaining-fulfillment')
    assert response.status_code==status
    assert 'lines' not in response.json()
    assert 'no-store' in response.headers['cache-control']
    db.commit.assert_not_called()


def test_released_capacity_can_be_cancelled_without_erasing_cumulative_reservation():
    row = service.partition(**quantities(reserved='10'))
    assert row.unreserved_qty == '0.000'
    assert row.reserved_unpicked_qty == '3.000'
