"""Release the unpicked part, preserving all picked/outbound evidence."""
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import event, select
from app.inventory_models import StockReservationPick, StockReservationRelease, StockBalance
from app.formal_services import material_request_reservation_release as release
from app.formal_services import material_request_remainder as remainder
from test_material_request_picking import pick_world, _create as pick, _input as pick_input
from test_material_request_reservation_release import release_world, _create, _input
from test_material_request_approval_service import approval_db


def test_release_unpicked_quantity_after_partial_pick(pick_world):
    db,actor,request,original,serials,calls=pick_world
    selected=pick_input(pick_world)
    picked=pick(pick_world,selected)
    axes=release.reserve._state_axes(request)
    quantity=original.reserved_qty-selected.picked_qty
    value=_input(db,original,str(quantity),serials[1:])
    version=request.version
    output=_create(pick_world,value)
    assert output['released_qty']==format(quantity,'.3f')
    assert output['state_axes']['outbound_status']==axes['outbound_status']=='pending_pick'
    assert original.status=='reserved' and original.released_qty==0
    assert db.get(StockBalance,original.stock_account_id).quantity==0
    assert db.get(StockReservationPick,UUID(picked['pick_id'])).picked_qty==selected.picked_qty
    assert len(calls)==2
    assert _create(pick_world,value,version=version)['release_id']==output['release_id']
    assert len(calls)==2
    row=remainder.remaining_fulfillment(db,actor=actor,request_id=request.id).lines[0]
    assert row.reserved_unpicked_qty=='0.000'
    assert Decimal(row.picked_unoutbound_qty)==selected.picked_qty


def test_cannot_release_picked_quantity_even_if_account_contains_more(pick_world):
    db,actor,request,original,serials,calls=pick_world
    pick(pick_world)
    with pytest.raises(release.MaterialRequestReservationReleaseError,match='尚未拣货'):
        _create(pick_world,_input(db,original,str(original.reserved_qty),serials))
    assert len(calls)==1 and not db.scalars(select(StockReservationRelease)).all()


def test_picked_sn_cannot_be_released(pick_world):
    db,_,_,original,serials,calls=pick_world
    if not serials: pytest.skip('serial-specific boundary')
    pick(pick_world)
    with pytest.raises(release.MaterialRequestReservationReleaseError,match='SN'):
        _create(pick_world,_input(db,original,'1.000',serials[:1]))
    assert len(calls)==1


def test_tampered_pick_is_not_available_for_release(pick_world):
    db,_,_,original,serials,calls=pick_world
    picked=pick(pick_world)
    db.get(StockReservationPick,UUID(picked['pick_id'])).reason='rewritten'; db.flush()
    with pytest.raises(release.MaterialRequestReservationReleaseError):
        _create(pick_world,_input(db,original,'1.000' if serials else '0.125',serials[1:2]))
    assert len(calls)==1


def test_partial_release_recovery_does_not_take_write_locks(pick_world):
    db,actor,request,original,serials,calls=pick_world
    selected=pick_input(pick_world)
    pick(pick_world,selected)
    output=_create(pick_world,_input(db,original,str(original.reserved_qty-selected.picked_qty),serials[1:]))
    def read_only_statement(execution):
        assert execution.is_select
        assert getattr(execution.statement,'_for_update_arg',None) is None
    event.listen(db,'do_orm_execute',read_only_statement)
    try:
        recovered=release.release_command_status(db,actor=actor,trace_request_id='trace-release-test-command-0001')
        assert recovered['release_id']==output['release_id']
        assert len(calls)==2
    finally:
        event.remove(db,'do_orm_execute',read_only_statement)
