"""Exact return acceptance remains independent of stock and sender login."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.inventory_models import StockBalance, SerialCurrentPosition, Receipt
from app.stock_operation_models import StockOperationReceipt, StockOperationReceiptLine
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptSubmitIn
from app.formal_services import stock_return_receipt_plan as plan, stock_return_receipt_commands as commands
from app.formal_services import stock_return_receipt_facts as facts, stock_return_receiving as receiving
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_stock_return_receiving import db, world, stock, recovered, destination, prepared, parcel, incoming
from test_work_order_removed_registration import inventory


@pytest.fixture
def acceptance(db, stock, incoming):
    shipped, actor = incoming
    actor = replace(actor, entitlements=actor.entitlements + (replace(actor.entitlements[0], action='receive_return'),))
    stock.world.current_principal = actor
    package = receiving.my_return_receiving_detail(db, actor=actor, shipment_id=shipped.shipment_id).package
    value = StockReturnReceiptPreviewIn(operator_person_id=actor.person_id, received_at=datetime.now(timezone.utc),
        reason='逐件验收退回物料，尚未入账', lines=[dict(shipment_line_id=package.lines[0].shipment_line_id,
            accepted_qty='1.000', accepted_serial_verifications=[dict(serial_id=sn.id, serial_no=sn.serial_no,
                sku_code=stock.world.material.sku_code, qr_code=sn.qr_code) for sn in stock.serials[:1]])])
    return SimpleNamespace(actor=actor, package=package, request=value)


def submit(db, context, request=None):
    request = request or context.request
    checked, _ = plan.preview_receipt(db, actor=context.actor, shipment_id=context.package.shipment_id, request=request)
    return StockReturnReceiptSubmitIn(**request.model_dump(), request_id=uuid4().hex,
        idempotency_key=uuid4().hex, expected_plan_hash=checked.plan_hash)


def execute(db, context, request):
    return commands.execute_receipt(db, actor=context.actor, shipment_id=context.package.shipment_id, request=request)


def snapshot(db):
    from test_stock_return_shipment import snapshot as parent
    return parent(db), tuple(tuple(db.execute(text(f'SELECT * FROM {name} ORDER BY id'))) for name in
        ('receipts', 'stock_operation_receipts', 'stock_operation_receipt_lines', 'stock_operation_receipt_serials', 'stock_operation_receipt_exceptions'))


def test_acceptance_and_replay_are_stock_neutral_with_exact_original_proof(db, stock, parcel, acceptance):
    before = inventory(db)
    value = submit(db, acceptance); result = execute(db, acceptance, value); db.commit()
    assert result.status == 'accepted' and result.lines[0].accepted_qty == '1.000'
    assert result.lines[0].unconfirmed_qty == '1.000'
    assert result.operator_person_id == acceptance.actor.person_id
    assert 'qr_code' not in result.model_dump_json() and 'posting_transaction_id' not in result.model_dump()
    assert inventory(db) == before
    assert db.get(StockBalance, parcel.line.transit_stock_account_id).quantity == 1
    if stock.tracked:
        assert db.get(SerialCurrentPosition, stock.serials[0].id).stock_account_id == parcel.line.transit_stock_account_id
    assert execute(db, acceptance, value) == result
    db.commit(); baseline = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    assert facts.receipt_result(db, actor=acceptance.actor, fact=db.get(StockOperationReceipt, result.receipt_id)) == result
    assert snapshot(db) == baseline


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_partial_receipts_have_independent_audit_order_and_total_budget(db, acceptance):
    first = acceptance.request.model_copy(update={'lines': (acceptance.request.lines[0].model_copy(update={'accepted_qty': Decimal('.375')}),)})
    a = execute(db, acceptance, submit(db, acceptance, first)); db.commit()
    rest = first.model_copy(update={'lines': (first.lines[0].model_copy(update={'accepted_qty': Decimal('.625')}),)})
    b = execute(db, acceptance, submit(db, acceptance, rest)); db.commit()
    assert b.lines[0].previously_accepted_qty == '0.375' and b.lines[0].unconfirmed_qty == '0.625'
    assert facts.verified_receipts(db, shipment_id=acceptance.package.shipment_id) == (a, b)
    with pytest.raises(InventoryReadError, match='尚未确认'):
        submit(db, acceptance)


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('change', ['plan', 'operator', 'foreign_line', 'overage', 'future', 'before_shipment'])
def test_invalid_acceptance_never_creates_receipt(db, acceptance, change):
    value = submit(db, acceptance)
    if change == 'plan': value = value.model_copy(update={'expected_plan_hash': '0' * 64})
    elif change == 'operator': value = value.model_copy(update={'operator_person_id': uuid4()})
    elif change == 'future': value = value.model_copy(update={'received_at': datetime.now(timezone.utc) + timedelta(days=1)})
    elif change == 'before_shipment': value = value.model_copy(update={'received_at': acceptance.package.shipped_at - timedelta(seconds=1)})
    else: value = value.model_copy(update={'lines': (value.lines[0].model_copy(update={
        'shipment_line_id' if change == 'foreign_line' else 'accepted_qty': uuid4() if change == 'foreign_line' else Decimal(2)}),)})
    before = snapshot(db)
    with pytest.raises(InventoryReadError): execute(db, acceptance, value)
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_audit_failure_rolls_back_entire_acceptance(db, acceptance, monkeypatch):
    value = submit(db, acceptance); before = snapshot(db)
    def broken(*args): raise RuntimeError('synthetic receipt audit failure')
    monkeypatch.setattr(commands, '_record', broken)
    with pytest.raises(RuntimeError, match='synthetic receipt audit failure'), db.begin_nested():
        execute(db, acceptance, value)
    db.expire_all(); assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('change', ['quantity', 'audit', 'original_parcel'])
def test_tampered_history_is_not_recovered_or_spent_again(db, acceptance, change):
    result = execute(db, acceptance, submit(db, acceptance)); db.commit()
    if change == 'quantity': db.scalar(select(StockOperationReceiptLine)).accepted_qty = Decimal('.500')
    elif change == 'audit':
        from app.foundation_models import AuditEvent
        db.scalar(select(AuditEvent).filter_by(aggregate_type='stock_operation_receipt', aggregate_id=str(result.receipt_id))).after_jsonb = {}
    else:
        from app.inventory_models import Shipment
        db.get(Shipment, result.shipment_id).tracking_no = 'TAMPERED-SYNTHETIC'
    db.commit(); before = snapshot(db)
    with pytest.raises((InventoryReadError, InventoryPostingError)): facts.receipt_result(db, actor=acceptance.actor, fact=db.get(StockOperationReceipt, result.receipt_id))
    with pytest.raises((InventoryReadError, InventoryPostingError)): submit(db, acceptance)
    assert snapshot(db) == before


@pytest.mark.parametrize('quantity', [True, 1.0, 'NaN', 'Infinity', '-1', '0.0001', '1000000000000000'])
def test_invalid_decimal_inputs_are_rejected(quantity):
    with pytest.raises(ValidationError):
        StockReturnReceiptPreviewIn(operator_person_id=uuid4(), received_at='2026-09-13T00:00:00Z', reason='合成输入',
            lines=[dict(shipment_line_id=uuid4(), accepted_qty=quantity)])


def evidence(db, stock, acceptance, monkeypatch):
    from app.formal_services import formal_files
    from test_formal_files_service import FakeStorage
    from app.foundation_models import FileObject
    from test_material_request_draft_service import SECRET
    monkeypatch.setattr(formal_files, 'load_formal_principal', lambda _db, _user_id: stock.world.current_principal)
    storage = FakeStorage(); key = uuid4().hex
    uploaded = formal_files.create_file_upload_intent(db, actor=acceptance.actor,
        command=formal_files.FileUploadIntentInput(purpose='receipt_exception_evidence', original_filename='synthetic-receipt.png',
            size_bytes=10, mime_type='image/png', sha256='a' * 64), idempotency_key=key, idempotency_hmac_secret=SECRET,
        trace_request_id='upload-' + key, storage=storage, upload_ttl_seconds=600)
    file = db.get(FileObject, uploaded.file_id); storage.materialize(file)
    formal_files.complete_file_upload(db, actor=acceptance.actor, file_id=file.id,
        trace_request_id='complete-' + key, storage=storage)
    db.commit()
    return file, storage


def abnormal(context, file_id, kind):
    original = context.request.lines[0]
    serial_ids = tuple(proof.serial_id for proof in original.accepted_serial_verifications)
    changes = {'exceptions': [dict(exception_type=kind, description='合成实物验收异常证据', evidence_file_id=file_id)]}
    if kind == 'damaged': changes.update(damaged_qty='1.000', damaged_serial_ids=serial_ids)
    else:
        field = 'shortage' if kind == 'shortage' else 'rejected'
        changes.update(accepted_qty='0.000', accepted_serial_verifications=(), **{field + '_qty': '1.000', field + '_serial_ids': serial_ids})
    value = context.request.model_dump(); value['lines'] = [{**original.model_dump(), **changes}]
    return StockReturnReceiptPreviewIn.model_validate(value)


@pytest.mark.parametrize('kind', ['shortage', 'damaged', 'rejected', 'wrong_material', 'wrong_serial'])
def test_exception_evidence_and_outcomes_do_not_forge_stock(db, stock, acceptance, monkeypatch, kind):
    file, storage = evidence(db, stock, acceptance, monkeypatch); before = inventory(db)
    value = abnormal(acceptance, file.id, kind)
    result = execute(db, acceptance, submit(db, acceptance, value)); db.commit()
    assert result.status == 'exception' and result.lines[0].exceptions[0].evidence_file_id == file.id
    assert inventory(db) == before
    from app.formal_services import formal_files
    downloaded = formal_files.create_file_download_intent(db, actor=acceptance.actor, file_id=file.id,
        trace_request_id='download-' + uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert downloaded.file_id == file.id
    if kind == 'shortage':
        later = execute(db, acceptance, submit(db, acceptance)); db.commit()
        assert later.lines[0].previously_accepted_qty == later.lines[0].previously_rejected_qty == '0.000'
        assert later.lines[0].accepted_qty == '1.000'
        assert len(facts.verified_receipts(db, shipment_id=result.shipment_id)) == 2
    else:
        with pytest.raises(InventoryReadError, match='尚未确认'): submit(db, acceptance)
    assert inventory(db) == before
    stock.world.current_principal = stock.actor
    with pytest.raises(formal_files.FormalFileError):
        formal_files.create_file_download_intent(db, actor=stock.actor, file_id=file.id,
            trace_request_id='foreign-' + uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert len(storage.download_calls) == 1


def test_response_loss_recovers_original_or_seals_absent_request(db, stock, acceptance):
    from app.formal_services.stock_return_receipt_recovery import lookup_receipt_request, seal_receipt_request
    value = submit(db, acceptance)
    result = execute(db, acceptance, value); db.commit()
    coords = dict(actor=acceptance.actor, shipment_id=result.shipment_id, request_id=value.request_id)
    before = snapshot(db)
    assert lookup_receipt_request(db, **coords) == result
    assert seal_receipt_request(db, **coords, request_hash=result.request_hash) == result
    assert snapshot(db) == before
    absent = value.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    coords['request_id'] = absent.request_id
    assert lookup_receipt_request(db, **coords) is None
    sealed = seal_receipt_request(db, **coords, request_hash=result.request_hash); db.commit()
    assert sealed.seal.operation_type == 'receive_return' and sealed.seal.shipment_id == result.shipment_id
    before = snapshot(db)
    assert lookup_receipt_request(db, **coords) == sealed
    with pytest.raises(InventoryReadError) as error: execute(db, acceptance, absent)
    assert error.value.code == 'stock_return_request_sealed' and snapshot(db) == before


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
@pytest.mark.parametrize('field', ['sku_code', 'serial_no', 'qr_code', 'serial_id'])
def test_acceptance_requires_fresh_exact_three_code_scan(db, acceptance, field):
    value = acceptance.request.model_dump()
    value['lines'][0]['accepted_serial_verifications'][0][field] = uuid4() if field == 'serial_id' else 'FOREIGN-SYNTHETIC'
    before = snapshot(db)
    with pytest.raises(InventoryReadError): submit(db, acceptance, StockReturnReceiptPreviewIn.model_validate(value))
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_receiving_read_permission_does_not_grant_acceptance(db, stock, acceptance):
    from app.formal_services.stock_return_receipt_recovery import lookup_receipt_request
    value = submit(db, acceptance); result = execute(db, acceptance, value); db.commit()
    actor = replace(acceptance.actor, entitlements=tuple(row for row in acceptance.actor.entitlements if row.action != 'receive_return'))
    stock.world.current_principal = actor; acceptance.actor = actor
    before = snapshot(db)
    assert lookup_receipt_request(db, actor=actor, shipment_id=result.shipment_id, request_id=value.request_id) == result
    with pytest.raises(InventoryReadError): execute(db, acceptance, value)
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_file_completion_is_time_bound_single_receipt_and_history_survives_quarantine(db, stock, acceptance, monkeypatch):
    file, storage = evidence(db, stock, acceptance, monkeypatch)
    value = abnormal(acceptance, file.id, 'shortage')
    original = file.metadata_jsonb
    file.metadata_jsonb = {**original, 'completion': {**original['completion'],
        'verified_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}
    db.commit()
    with pytest.raises(InventoryReadError): submit(db, acceptance, value)
    file.metadata_jsonb = original; db.commit()
    result = execute(db, acceptance, submit(db, acceptance, value)); db.commit()
    with pytest.raises(InventoryReadError, match='已绑定'): submit(db, acceptance, value)
    file.status = 'quarantined'; db.commit()
    assert facts.receipt_result(db, actor=acceptance.actor, fact=db.get(StockOperationReceipt, result.receipt_id)) == result
    from app.formal_services import formal_files
    with pytest.raises(formal_files.FormalFileError):
        formal_files.create_file_download_intent(db, actor=acceptance.actor, file_id=file.id,
            trace_request_id='quarantined-' + uuid4().hex, storage=storage, download_ttl_seconds=60)
    assert not storage.download_calls


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('first', ['original_seal', 'receipt'])
def test_request_namespace_is_shared_with_other_return_actions(db, stock, acceptance, first):
    from app.formal_services.stock_return_recovery import seal_return_request, lookup_return_request
    from app.formal_services.stock_return_receipt_recovery import lookup_receipt_request
    actor = replace(acceptance.actor, entitlements=acceptance.actor.entitlements +
        (replace(acceptance.actor.entitlements[0], action='submit_return'),))
    stock.world.current_principal = actor; acceptance.actor = actor
    value = submit(db, acceptance)
    other = dict(actor=actor, work_order_id=stock.orders[2].id, operation_type='submit_return', request_id=value.request_id)
    own = dict(actor=actor, shipment_id=acceptance.package.shipment_id, request_id=value.request_id)
    if first == 'original_seal':
        seal_return_request(db, **other, request_hash=plan._hash(plan.intent(acceptance.package.shipment_id, value))); db.commit()
        before = snapshot(db)
        with pytest.raises(InventoryReadError) as error: execute(db, acceptance, value)
        assert error.value.code == 'stock_return_request_sealed'
        with pytest.raises(InventoryReadError) as error: lookup_receipt_request(db, **own)
    else:
        execute(db, acceptance, value); db.commit(); before = snapshot(db)
        with pytest.raises(InventoryReadError) as error: lookup_return_request(db, **other)
    assert error.value.code == 'stock_return_request_conflict' and snapshot(db) == before


def test_history_preserves_shortage_observation_and_updates_only_confirmed_progress(db, stock, acceptance, monkeypatch):
    from app.formal_services.stock_return_receipt_queries import receipt_history
    coords = dict(actor=acceptance.actor, shipment_id=acceptance.package.shipment_id)
    empty = receipt_history(db, **coords)
    assert not empty.receipts and empty.lines[0].unconfirmed_qty == '1.000'
    file, _ = evidence(db, stock, acceptance, monkeypatch)
    short = execute(db, acceptance, submit(db, acceptance, abnormal(acceptance, file.id, 'shortage'))); db.commit()
    missing = receipt_history(db, **coords)
    assert missing.receipts == (short,) and missing.lines == empty.lines
    received = execute(db, acceptance, submit(db, acceptance)); db.commit()
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    finished = receipt_history(db, **coords)
    assert finished.receipts == (short, received)
    assert finished.lines[0].accepted_qty == '1.000' and finished.lines[0].unconfirmed_qty == '0.000'
    assert not finished.lines[0].unconfirmed_serials
    assert 'posting_transaction_id' not in finished.model_dump_json() and 'qr_code' not in finished.model_dump_json()
    assert snapshot(db) == before
