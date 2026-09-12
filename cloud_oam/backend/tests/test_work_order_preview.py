"""An entire candidate batch must be validated without creating inventory facts."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import get_db
from app.foundation_models import ExternalObject, ExternalObjectVersion, SourceSystem
from app.inventory_models import InventoryLedgerHead, MaterialInventoryPolicy
from app.formal_services import inventory_posting as posting
from app.formal_services import work_order_preview as preview
from app.formal_services import work_order_material as material
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_operation_read import client_request_hash
from app.routers import formal_work_order_query as api
from test_work_order_material_options import db, world, stock


def candidate(stock, kind):
    return stock.line('1', stock.serials[3:4] if kind=='occupy' else stock.serials[1:2],
        identifier=stock.account.id if kind=='occupy' else stock.reserved.id,
        target=stock.account.id if kind=='release' else None)


def run(db, stock, kind='occupy', lines=None):
    return preview.preview_batch(db, actor=stock.world.current_principal, work_order_id=stock.orders[0].id,
        operation_type=kind, lines=lines if lines is not None else (candidate(stock, kind),))


@pytest.mark.parametrize('kind', ['occupy', 'consume', 'release'])
def test_preview_verifies_codes_current_scope_and_digest_with_database_writes_disabled(db, stock, kind):
    db.execute(text('PRAGMA query_only=ON'))
    result=run(db,stock,kind)
    assert result.status=='batch_validated' and result.line_count==1
    assert result.operator_person_id==stock.actor.person_id and result.authorization_version==stock.actor.authorization_version
    assert result.request_hash==client_request_hash(operation_type=kind, work_order_id=stock.orders[0].id,
        operator_person_id=stock.actor.person_id, lines=(candidate(stock,kind),))
    assert not db.new and not db.dirty and not db.deleted
    assert 'qr_code' not in result.model_dump_json()


@pytest.mark.parametrize('kind', ['occupy', 'consume', 'release'])
def test_preview_rejects_excess_whole_batch_without_making_an_operation(db, stock, kind):
    line=replace(candidate(stock,kind),quantity=Decimal('3'),serial_ids=(),serial_verifications=())
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(posting.InventoryPostingError) as exc: run(db,stock,kind,(line,))
    assert exc.value.code=='work_order_material_quantity_insufficient'
    assert not db.new and not db.dirty and not db.deleted


def test_invalid_later_line_cannot_reserve_an_earlier_valid_line(db, stock):
    first=candidate(stock,'occupy')
    later=candidate(stock,'consume')  # A reserved account cannot be occupied again.
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(posting.InventoryPostingError) as exc: run(db,stock,'occupy',(first,later))
    assert exc.value.code=='work_order_material_choice_invalid'
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('damage', ['sku', 'sn', 'qr', 'missing', 'duplicate', 'foreign_reservation'])
@pytest.mark.parametrize('stock', ['serial'], indirect=True)
def test_unverified_or_other_orders_serial_cannot_pass_preflight(db, stock, damage):
    line=candidate(stock,'consume')
    if damage=='missing': line=replace(line,serial_verifications=())
    elif damage=='duplicate': line=replace(line,serial_verifications=line.serial_verifications*2)
    elif damage=='foreign_reservation': line=stock.line('1',stock.serials[2:3],identifier=stock.reserved.id)
    else:
        key={'sku':'sku_code','sn':'serial_no','qr':'qr_code'}[damage]
        line=replace(line,serial_verifications=(replace(line.serial_verifications[0],**{key:'WRONG'}),))
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(posting.InventoryPostingError) as exc: run(db,stock,'consume',(line,))
    assert exc.value.code in {'serial_verification_missing','serial_verification_mismatch','work_order_serial_choice_invalid'}


@pytest.mark.parametrize('change', ['stale', 'disabled', 'closed', 'foreign_person'])
def test_not_operable_current_oam_source_cannot_pass(db, stock, change):
    order=stock.orders[0]
    external=db.get(ExternalObject,order.external_object_id)
    version=db.get(ExternalObjectVersion,external.current_version_id)
    if change=='stale':
        old=datetime.now(timezone.utc)-timedelta(hours=2)
        order.updated_at=old;order.created_at=old;order.source_updated_at=old
        version.source_updated_at=old;version.valid_from=old;version.created_at=old
    elif change=='disabled': db.get(SourceSystem,external.source_system_id).enabled=False
    elif change=='closed':
        from app.formal_services.oam_work_order_projection import _sha256
        order.status='closed';version.payload_jsonb={**version.payload_jsonb,'status':'closed'}
        version.payload_sha256=_sha256(version.payload_jsonb)
    else: order.engineer_person_id=stock.world.headquarters_reviewer_person.id
    db.commit();db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(posting.InventoryPostingError) as exc: run(db,stock)
    assert exc.value.code in {'work_order_not_operable','work_order_not_found'}


def test_release_target_is_exact_and_available_dimension_survives_zero_balance(db, stock):
    remaining='2' if stock.tracked else '2.125'
    material.execute_occupy_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,
        lines=(stock.line(remaining,stock.serials[3:]),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    db.commit(); db.execute(text('PRAGMA query_only=ON'))
    options=preview.material_options(db,actor=stock.actor,work_order_id=stock.orders[0].id)
    assert all(row.availability_bucket=='reserved' for row in options.items)
    assert options.items[0].release_target_stock_account_id==stock.account.id
    assert run(db,stock,'release').status=='batch_validated'
    with pytest.raises(posting.InventoryPostingError) as exc:
        run(db,stock,'release',(replace(candidate(stock,'release'),target_stock_account_id=stock.reserved.id),))
    assert exc.value.code=='release_target_invalid'


@pytest.mark.parametrize('kind',['occupy','consume'])
def test_target_cannot_be_smuggled_into_command_preview(db,stock,kind):
    with pytest.raises(posting.InventoryPostingError) as exc:
        run(db,stock,kind,(replace(candidate(stock,kind),target_stock_account_id=stock.account.id),))
    assert exc.value.code=='work_order_target_invalid'


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
def test_policy_precision_is_shared_with_posting_validator(db,stock):
    policy=db.scalar(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id==stock.world.material.id))
    assert policy.quantity_scale==0 and not policy.allow_fraction
    line=stock.line('0.5',())
    with pytest.raises(posting.InventoryPostingError) as exc: run(db,stock,lines=(line,))
    assert exc.value.code in {'quantity_scale_exceeded','fraction_not_allowed','serial_quantity_mismatch'}


@pytest.mark.parametrize('change',['ledger','authority','source','policy'])
def test_mid_preview_changes_discard_the_whole_response(db,stock,monkeypatch,change):
    original=posting._validate_tracking_rules
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        if change=='ledger': db.scalar(select(InventoryLedgerHead)).next_cursor+=1
        elif change=='source': stock.orders[0].updated_at+=timedelta(seconds=1)
        elif change=='policy':
            db.scalar(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id==stock.world.material.id)).allow_fraction ^= True
        else: stock.world.current_principal=replace(stock.actor,authorization_version=stock.actor.authorization_version+1)
        db.flush()
    monkeypatch.setattr(posting,'_validate_tracking_rules',changed)
    with pytest.raises((posting.InventoryPostingError,InventoryReadError)) as exc: run(db,stock)
    assert exc.value.code=={'ledger':'inventory_projection_changed','source':'work_order_projection_changed',
        'policy':'work_order_policy_changed','authority':'actor_principal_stale'}[change]


def test_http_preview_is_read_only_and_returns_no_scan_payload(db,stock):
    app=FastAPI();app.include_router(api.router,prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal':app.dependency_overrides[dependency.call]=lambda:stock.actor
    line=candidate(stock,'occupy')
    command=material.operation_request_payload(operation_type='occupy',work_order_id=stock.orders[0].id,
        operator_person_id=stock.actor.person_id,lines=(line,))
    db.execute(text('PRAGMA query_only=ON'))
    with TestClient(app) as client:
        path=f'/api/v1/work-orders/{stock.orders[0].id}/material-operations/occupy/preview'
        response=client.post(path,json={'operator_person_id':str(stock.actor.person_id),'lines':command['lines']})
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='private, no-store'
        assert response.json()['status']=='batch_validated' and 'qr_code' not in response.text
        assert client.post(path,json={'operator_person_id':str(uuid4()),'lines':command['lines']}).status_code==403


@pytest.mark.parametrize('kind,bucket', [('occupy','available'),('occupy','reserved'),('consume','reserved'),('release','reserved'),('release','available')])
def test_matching_hard_freeze_blocks_source_and_destination_dimensions(db,stock,kind,bucket):
    from test_inventory_posting import freeze_account_scope
    account=stock.account if bucket=='available' else stock.reserved
    freeze_account_scope(db,stock.world,account,freeze_mode='hard',scope_mode='filtered')
    db.commit();db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(posting.InventoryPostingError) as exc:run(db,stock,kind)
    assert exc.value.code=='inventory_scope_hard_frozen'
    assert not db.new and not db.dirty and not db.deleted


def test_freeze_outside_material_dimensions_does_not_block_preview(db,stock):
    from test_inventory_posting import freeze_account_scope
    freeze_account_scope(db,stock.world,stock.account,freeze_mode='hard',scope_mode='filtered',matches_account=False)
    db.commit();db.execute(text('PRAGMA query_only=ON'))
    assert run(db,stock).status=='batch_validated'


def test_freeze_started_during_preview_discards_the_result(db,stock,monkeypatch):
    from test_inventory_posting import freeze_account_scope
    original=posting._validate_tracking_rules
    def freeze_after_validation(*args,**kwargs):
        original(*args,**kwargs)
        freeze_account_scope(db,stock.world,stock.reserved,freeze_mode='hard',scope_mode='filtered')
        db.flush()
    monkeypatch.setattr(posting,'_validate_tracking_rules',freeze_after_validation)
    with pytest.raises(posting.InventoryPostingError) as exc:run(db,stock)
    assert exc.value.code=='inventory_scope_hard_frozen'
