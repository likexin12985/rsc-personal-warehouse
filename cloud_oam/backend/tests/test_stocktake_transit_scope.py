"""Established transit locations use the same scoped stocktake freeze lifecycle."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.formal_services import stocktake_task as task, stocktake_options as options
from app.inventory_models import StockLocation, StockBalance, InventoryTransaction
from app.stocktake_models import InventoryFreeze
from app.stocktake_task_schemas import StocktakeTaskCreateIn, StocktakeScopeSelectionIn, StocktakeTaskStartIn
from test_stocktake_task_service import db, world, NOW, SECRET, _fixed_database_time


def transit(world):
    location=StockLocation(id=uuid4(),code='TEST-TRANSIT-'+uuid4().hex,name='区域在途位置',
        location_type='transit',owner_org_id=world.region_x.id,parent_id=world.region_location.id,status='active')
    world.db.add(location);world.db.commit()
    return location


def draft(world, location, *, mode='hard'):
    return StocktakeTaskCreateIn(task_type='sample',region_org_id=world.region_x.id,
        scopes=(StocktakeScopeSelectionIn(owner_org_id=world.region_x.id,location_id=location.id,
            assignee_person_id=world.manager_x.person.id,freeze_mode=mode),),deadline=NOW+timedelta(days=1))


def create(world, location):
    return task.create_stocktake_task_draft(world.db,actor=world.principals['manager_x'],draft=draft(world,location),
        idempotency_key='transit-create-'+uuid4().hex,idempotency_hmac_secret=SECRET,trace_request_id='transit-create-'+uuid4().hex)


def test_transit_draft_start_freezes_without_posting_or_changing_inventory(world):
    location=transit(world);db=world.db
    before=(tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity))),tuple(db.scalars(select(InventoryTransaction.id))))
    made=create(world,location);db.commit()
    args=dict(actor=world.principals['manager_x'],task_id=made.task_id,command=StocktakeTaskStartIn(expected_version=0),
        idempotency_key='transit-start-'+uuid4().hex,idempotency_hmac_secret=SECRET,trace_request_id='transit-start-'+uuid4().hex)
    result=task.start_stocktake_task(db,**args);db.commit()
    assert result.status=='counting' and result.snapshot_line_count==0
    freezes=tuple(db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id==made.task_id)))
    assert len(freezes)==1 and freezes[0].freeze_mode=='hard' and freezes[0].status=='active'
    replay=task.start_stocktake_task(db,**args);db.commit()
    assert replay.replayed and replay.task_id==result.task_id
    assert before==(tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity))),tuple(db.scalars(select(InventoryTransaction.id))))


def test_transit_options_keep_region_identity_and_manager_assignees(world):
    location=transit(world)
    page=options.list_location_options(world.db,actor=world.principals['manager_x'],region_org_id=world.region_x.id,limit=20,now=NOW)
    row=next(item for item in page.items if item.location_id==location.id)
    assert row.location_type=='transit' and row.owner_org_id==world.region_x.id and row.custodian_person_id is None
    assignees=options.list_assignee_options(world.db,actor=world.principals['manager_x'],region_org_id=world.region_x.id,location_id=location.id,limit=20,now=NOW)
    ids={item.person_id for item in assignees.items}
    assert world.manager_x.person.id in ids and world.technician.person.id not in ids


@pytest.mark.parametrize('parent_kind',['missing','personal','foreign_owner','inactive'])
def test_transit_scope_requires_its_active_same_owner_regional_parent(world,parent_kind):
    location=transit(world)
    if parent_kind=='missing':location.parent_id=None
    elif parent_kind=='personal':location.parent_id=world.personal_location.id
    else:
        parent=StockLocation(id=uuid4(),code='TEST-PARENT-'+uuid4().hex,name='测试父级',location_type='region',
            owner_org_id=world.region_y.id if parent_kind=='foreign_owner' else world.region_x.id,
            status='inactive' if parent_kind=='inactive' else 'active')
        world.db.add(parent);world.db.flush();location.parent_id=parent.id
    world.db.commit()
    with pytest.raises(task.StocktakeTaskError) as caught:create(world,location)
    assert caught.value.code=='transit_location_parent_invalid'
    with pytest.raises(options.StocktakeOptionError) as caught:
        options.list_location_options(world.db,actor=world.principals['manager_x'],region_org_id=world.region_x.id,limit=20,now=NOW)
    assert caught.value.code=='stocktake_transit_parent_invalid'


@pytest.mark.parametrize('location_type', ['transit', 'region'])
def test_transit_count_preserves_original_custodian_without_relaxing_regional_scope(world, monkeypatch, location_type):
    from decimal import Decimal
    from app.formal_services import stocktake_count as count
    from app.stocktake_models import StocktakeSnapshotLine

    monkeypatch.setattr(count, '_database_now', lambda _db: NOW)
    location = transit(world)
    location.location_type = location_type
    # Synthetic initial fixture: a transit account retains its original person.
    world.personal_account.location_id = location.id
    world.db.commit()
    made = create(world, location)
    world.db.commit()
    started = task.start_stocktake_task(world.db, actor=world.principals['manager_x'], task_id=made.task_id,
        command=StocktakeTaskStartIn(expected_version=0), idempotency_key='transit-count-start-'+uuid4().hex,
        idempotency_hmac_secret=SECRET, trace_request_id='transit-count-start-'+uuid4().hex)
    world.db.commit()
    snapshot = world.db.scalars(select(StocktakeSnapshotLine).where(StocktakeSnapshotLine.task_id == made.task_id)).one()
    assert snapshot.stock_account_id == world.personal_account.id and snapshot.book_qty == Decimal('3.000')
    before = (tuple(world.db.execute(select(StockBalance.stock_account_id, StockBalance.quantity))),
        tuple(world.db.scalars(select(InventoryTransaction.id))))
    args = dict(actor=world.principals['manager_x'], command=count.SubmitStocktakeInitialScopeCountCommand(
        task_id=made.task_id, round_id=started.initial_round_id, scope_id=snapshot.scope_id, count_mode='blind',
        account_counts=(count.StocktakeSnapshotCountInput(stock_account_id=world.personal_account.id, counted_qty=Decimal('3.000')),)),
        idempotency_key='transit-count-'+uuid4().hex, idempotency_hmac_secret=SECRET, trace_request_id='transit-count-'+uuid4().hex)
    if location_type == 'region':
        with pytest.raises(count.StocktakeCountError) as caught:
            count.submit_stocktake_initial_scope_count(world.db, **args)
        assert caught.value.code == 'stocktake_count_account_outside_scope'
    else:
        result = count.submit_stocktake_initial_scope_count(world.db, **args)
        world.db.commit()
        assert result.task_status == 'submitted' and result.round_submitted
        replay = count.submit_stocktake_initial_scope_count(world.db, **args)
        world.db.commit()
        assert replay.replayed
    assert world.personal_account.custodian_person_id == world.technician.person.id
    assert before == (tuple(world.db.execute(select(StockBalance.stock_account_id, StockBalance.quantity))),
        tuple(world.db.scalars(select(InventoryTransaction.id))))
