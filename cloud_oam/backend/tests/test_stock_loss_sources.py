"""Loss-source previews use established stock and do not depend on a work order.

SQLite service tests use the existing opening/ledger fixtures. They are not a
production identity, PostgreSQL concurrency or submitted loss-document proof.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.formal_services import stock_loss_sources as loss
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from app.stock_loss_schemas import StockLossSelectionIn, StockLossSelectionLineIn
from app.inventory_models import MaterialInventoryPolicy, InventoryLedgerHead
from test_work_order_material_options import db, world, stock
from test_work_order_removed_registration import counts, inventory


@pytest.fixture
def allowed(stock):
    current = stock.actor
    stock.world.current_principal = replace(current, entitlements=current.entitlements + (
        replace(current.entitlements[0], resource='stock_operation', action='submit_loss'),))
    stock.actor = stock.world.current_principal
    return stock


def request(stock, **changes):
    proofs = tuple(dict(serial_id=sn.id, sku_code=stock.world.material.sku_code,
                       serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in stock.serials[3:4])
    row = StockLossSelectionLineIn(**(dict(stock_account_id=stock.account.id,
                                         quantity='1', serial_verifications=proofs) | changes))
    return StockLossSelectionIn(operator_person_id=stock.actor.person_id, lines=(row,))


def preview(db, stock, value=None):
    return loss.preview_selection(db, actor=stock.actor, request=value or request(stock))


def test_exact_own_available_stock_is_query_only_without_a_work_order(db, allowed):
    before = counts(db), inventory(db)
    db.execute(text('PRAGMA query_only=ON'))
    choices = loss.loss_sources(db, actor=allowed.actor)
    assert len(choices.items) == 1
    assert choices.items[0].stock_account_id == allowed.account.id
    assert choices.items[0].quantity == ('2.000' if allowed.tracked else '2.125')
    result = preview(db, allowed)
    assert result.planning_status == 'source_selection_only'
    assert result.lines[0].selected_quantity == '1.000'
    assert result.lines[0].source.custodian_person_id == allowed.actor.person_id
    assert len(result.lines[0].selected_serials) == int(allowed.tracked)
    assert result.basis_hash == preview(db, allowed).basis_hash
    assert 'qr_code' not in result.model_dump_json() and 'work_order' not in result.model_dump_json()
    assert (counts(db), inventory(db)) == before
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('bad', ['unknown_account', 'reserved_account', 'other_person', 'excess', 'duplicate'])
def test_invalid_batch_is_refused_without_partial_inventory_changes(db, allowed, bad):
    value = request(allowed)
    if bad == 'unknown_account':
        value = request(allowed, stock_account_id=uuid4())
    elif bad == 'reserved_account':
        value = request(allowed, stock_account_id=allowed.reserved.id)
    elif bad == 'other_person':
        value = value.model_copy(update={'operator_person_id': uuid4()})
    elif bad == 'excess':
        value = request(allowed, quantity='3')
    else:
        value = value.model_copy(update={'lines': value.lines * 2})
    before = counts(db), inventory(db)
    with pytest.raises(InventoryReadError):
        preview(db, allowed, value)
    assert (counts(db), inventory(db)) == before and not db.new and not db.dirty


def test_read_permission_alone_cannot_be_used_to_prepare_a_loss(db, stock):
    with pytest.raises(InventoryReadError) as error:
        loss.loss_sources(db, actor=stock.actor)
    assert error.value.code == 'stock_loss_forbidden'


@pytest.mark.parametrize('change', ['version', 'deny', 'withdrawn'])
def test_current_authorization_is_reloaded(db, allowed, change):
    current = allowed.world.current_principal
    if change == 'version':
        allowed.world.current_principal = replace(current, authorization_version=current.authorization_version + 1)
    elif change == 'deny':
        allowed.world.current_principal = replace(current, entitlements=current.entitlements + (
            replace(current.entitlements[-1], effect='deny'),))
    else:
        allowed.world.current_principal = replace(current, entitlements=current.entitlements[:-1])
    with pytest.raises((InventoryReadError, InventoryPostingError)):
        preview(db, allowed)


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
@pytest.mark.parametrize('bad', ['reserved_serial', 'sku', 'qr', 'sn', 'missing', 'duplicate'])
def test_serial_codes_and_current_unreserved_position_are_required(db, allowed, bad):
    value = request(allowed)
    proof = value.lines[0].serial_verifications[0].model_dump()
    if bad == 'reserved_serial':
        sn = allowed.serials[1]
        proof.update(serial_id=sn.id, serial_no=sn.serial_no, qr_code=sn.qr_code)
    elif bad == 'sku':
        proof['sku_code'] = 'OTHER'
    elif bad == 'qr':
        proof['qr_code'] = 'OTHER'
    elif bad == 'sn':
        proof['serial_no'] = 'OTHER'
    proofs = () if bad == 'missing' else (proof, proof) if bad == 'duplicate' else (proof,)
    with pytest.raises(InventoryReadError):
        preview(db, allowed, request(allowed, serial_verifications=proofs))


@pytest.mark.parametrize('value', [True, False, 1.1, float('nan'), 'NaN', 'Infinity', '0', '-1', '0.0001'])
def test_wire_quantities_reject_floats_nonfinite_and_invalid_precision(value):
    with pytest.raises(ValidationError):
        StockLossSelectionLineIn(stock_account_id=uuid4(), quantity=value)


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_fractional_source_uses_the_current_material_policy(db, allowed):
    result = preview(db, allowed, request(allowed, quantity='1.125'))
    assert result.lines[0].selected_quantity == '1.125'


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
def test_serial_selection_order_does_not_change_intent_or_basis(db, allowed):
    proofs = tuple(dict(serial_id=sn.id, sku_code=allowed.world.material.sku_code,
                       serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in allowed.serials[3:5])
    forward = preview(db, allowed, request(allowed, quantity='2', serial_verifications=proofs))
    backward = preview(db, allowed, request(allowed, quantity='2', serial_verifications=tuple(reversed(proofs))))
    assert forward.selection_hash == backward.selection_hash and forward.basis_hash == backward.basis_hash
    assert forward.lines == backward.lines


@pytest.mark.parametrize('bucket', ['available', 'frozen'])
def test_existing_stocktake_freeze_blocks_loss_source_or_destination(db, allowed, bucket):
    from test_inventory_posting import freeze_account_scope
    account = SimpleNamespace(**{key: getattr(allowed.account, key) for key in (
        'owner_org_id', 'custodian_person_id', 'location_id', 'material_id', 'lot_id', 'condition_code')},
        availability_bucket=bucket)
    freeze_account_scope(db, allowed.world, account, freeze_mode='hard', scope_mode='filtered')
    db.commit()
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryPostingError) as error:
        preview(db, allowed)
    assert error.value.code == 'inventory_scope_hard_frozen'


@pytest.mark.parametrize('change', ['policy', 'ledger', 'authority', 'freeze'])
def test_changes_during_preview_discard_the_whole_result(db, allowed, monkeypatch, change):
    from test_inventory_posting import freeze_account_scope
    original = loss._unfrozen
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        original(*args, **kwargs)
        calls += 1
        if calls != 1:
            return
        if change == 'policy':
            db.scalar(select(MaterialInventoryPolicy).where(
                MaterialInventoryPolicy.material_id == allowed.world.material.id)).effective_from -= timedelta(seconds=1)
        elif change == 'ledger':
            db.scalar(select(InventoryLedgerHead)).next_cursor += 1
        elif change == 'authority':
            allowed.world.current_principal = replace(allowed.actor, entitlements=allowed.actor.entitlements[:-1])
        else:
            freeze_account_scope(db, allowed.world, allowed.account, freeze_mode='hard', scope_mode='filtered')
        db.flush()
    monkeypatch.setattr(loss, '_unfrozen', changed)
    with pytest.raises((InventoryReadError, InventoryPostingError)):
        preview(db, allowed)
