"""Synthetic native API-role damaged acceptance and condition-aware inbound.

Call only from an owned-cluster fixture after real opening and loss approval.
This file is prepared for the next native scenarios; it is not a pass receipt.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from copy import deepcopy
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.formal_access import load_formal_principal
from app.models import User
from app.foundation_models import FileObject
from app.inventory_models import StockLocation, StockAccount, StockBalance, InventorySerial, SerialCurrentPosition
from app.stock_operation_models import StockOperationOutboundLine, StockOperationReturnInboundLine
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptSubmitIn
from app.formal_services import formal_files
from app.formal_services import stock_return_shipment_plan as ship_plan, stock_return_shipment_commands as shipping
from app.formal_services import stock_return_receipt_plan as receipt_plan, stock_return_receipt_commands as receiving
from app.formal_services import stock_return_inbound_commands as inbound, stock_return_inbound_recovery as recovery
from app.formal_services import stock_return_inbound_plan as inbound_plan
from app.formal_services.stock_return_inbound_accounts import resolve_target
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_return_inbound_facts import document
from app.formal_services.work_order_return_sources import _hash
from test_formal_files_service import FakeStorage, SECRET
from pg16_stock_loss_return_shipment_gate import prepare_departures, inventory_snapshot


def facts_snapshot(owner):
    """Full committed public facts, not only balances, in this synthetic DB."""
    with owner.connect() as db:
        tables = tuple(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
        quote = db.dialect.identifier_preparer.quote
        return {name:tuple(sorted(repr(dict(row)) for row in db.execute(
            text('SELECT * FROM public.'+quote(name))).mappings())) for name in tables}


def reject_forged_parts(owner, api, *, receiver, receipt_id, mixed, tracked=False):
    cases = ['legacy_version', 'wrong_condition'] + ([('swapped_serials' if tracked else 'shifted_damage'), 'omitted_part'] if mixed else [])
    outcomes = []
    for damage in cases:
        before = facts_snapshot(owner)
        committed_attempt = False
        with Session(api) as db:
            actor = load_formal_principal(db,receiver)
            forged = deepcopy(inbound_plan.plan_return_inbound(db,actor=actor,receipt_id=receipt_id))
            if damage == 'legacy_version':
                forged['schema_version'] = '1.0'
            elif damage == 'wrong_condition':
                # Preserve total accepted quantity while pretending every item
                # retained its original condition. Both plan and posting hashes
                # are recomputed by the real command; only native proof stops it.
                first = dict(forged['lines'][0])
                source = db.get(StockAccount,UUID(first['source_account_id']))
                target = resolve_target(db,source=source,
                    location=db.get(StockLocation,forged['target_location_id']),
                    person_id=actor.person_id,condition_code=source.condition_code)
                first.update(condition_code=source.condition_code,target_account_id=str(target.id),
                    accepted_qty=format(sum(Decimal(p['accepted_qty']) for p in forged['lines']),'.3f'),
                    serial_ids=[s for p in forged['lines'] for s in p['serial_ids']])
                forged['lines'] = (first,)
            elif damage == 'swapped_serials':
                parts = [dict(p) for p in forged['lines']]
                assert len(parts) == 2 and all(len(p['serial_ids']) == 1 for p in parts)
                parts[0]['serial_ids'], parts[1]['serial_ids'] = parts[1]['serial_ids'], parts[0]['serial_ids']
                forged['lines'] = tuple(parts)
            elif damage == 'shifted_damage':
                parts = [dict(p) for p in forged['lines']]
                delta = Decimal(parts[1]['accepted_qty'])/2
                parts[0]['accepted_qty'] = format(Decimal(parts[0]['accepted_qty'])+delta,'.3f')
                parts[1]['accepted_qty'] = format(Decimal(parts[1]['accepted_qty'])-delta,'.3f')
                forged['lines'] = tuple(parts)
            else:
                forged['lines'] = (forged['lines'][1],)
            digest = _hash(inbound_plan.plan_document(forged))
            with patch.object(inbound,'plan_return_inbound',return_value=forged), \
                    patch.object(inbound,'inbound_result',side_effect=lambda db,**kw: document(kw['fact'])):
                with pytest.raises(DBAPIError) as error:
                    inbound.execute_return_inbound(db,actor=actor,receipt_id=receipt_id,
                        expected_plan_hash=digest,request_id=uuid4().hex,idempotency_key=uuid4().hex)
                    committed_attempt = True
                    db.commit()
                assert error.value.orig.sqlstate == '23514'
                db.rollback()
        assert facts_snapshot(owner) == before
        outcomes.append(dict(case=damage,sqlstate='23514',rejectedAtCommit=committed_attempt,allFactsUnchanged=True))
    return outcomes


def exercise(context, *, mixed=False):
    world = prepare_departures(context)
    owner, api = world['owner'], world['api']
    departure = world['first']
    with Session(api) as db:
        actor = load_formal_principal(db, context['engineer_id'])
        line = db.scalars(select(StockOperationOutboundLine).where(
            StockOperationOutboundLine.outbound_id == departure.outbound_id)).one()
        value = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,
            carrier='Synthetic carrier', tracking_no='SYNTHETIC-QUALITY-'+uuid4().hex,
            shipped_at=datetime.now(timezone.utc), reason='Synthetic quality acceptance parcel',
            lines=(dict(outbound_line_id=line.id,quantity=departure.lines[0].selected_quantity,
                serial_ids=tuple(s.serial_id for s in departure.lines[0].selected_serials)),))
        checked,_ = ship_plan.preview_shipment(db, actor=actor, work_order_id=None,
            operation_id=world['order_id'], request=value)
        shipped = shipping.execute_shipment(db, actor=actor, work_order_id=None,
            operation_id=world['order_id'], request=StockReturnShipmentSubmitIn(
                **value.model_dump(),expected_plan_hash=checked.plan_hash,
                request_id=uuid4().hex,idempotency_key=uuid4().hex))
        db.commit()
    with Session(owner) as db:
        location = db.get(StockLocation, shipped.destination.target_location_id)
        receiver = db.scalars(select(User.id).where(User.person_id == location.custodian_person_id)).one()
    with Session(api) as db:
        actor = load_formal_principal(db, receiver)
        storage = FakeStorage()
        upload = formal_files.create_file_upload_intent(db, actor=actor,
            command=formal_files.FileUploadIntentInput(purpose='receipt_exception_evidence',
                original_filename='synthetic-damaged.png',size_bytes=128,mime_type='image/png',sha256='a'*64),
            idempotency_key=uuid4().hex,idempotency_hmac_secret=SECRET,
            trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=600)
        file = db.get(FileObject, upload.file_id); storage.materialize(file)
        formal_files.complete_file_upload(db, actor=actor, file_id=file.id,
            trace_request_id=uuid4().hex, storage=storage)
        file_id = file.id
        db.commit()
    before = inventory_snapshot(owner)
    with Session(api) as db:
        actor = load_formal_principal(db, receiver)
        _, detail = receipt_plan.authorize(db, actor, shipped.shipment_id)
        original = detail.package.lines[0]
        serials = [db.get(InventorySerial, s.serial_id) for s in original.serials]
        serial_ids = tuple(s.id for s in serials)
        quantity = Decimal(original.shipped_quantity)
        if mixed and context['tracking'] == 'serial':
            assert len(serials) == quantity == 2
        damaged = quantity/2 if mixed else quantity
        damaged_ids = serial_ids[:1] if mixed else serial_ids
        value = StockReturnReceiptPreviewIn(operator_person_id=actor.person_id,
            received_at=datetime.now(timezone.utc),reason='Synthetic real damaged return acceptance',
            lines=(dict(shipment_line_id=original.shipment_line_id,accepted_qty=quantity,
                damaged_qty=damaged,damaged_serial_ids=damaged_ids,
                accepted_serial_verifications=tuple(dict(serial_id=s.id,serial_no=s.serial_no,
                    sku_code=original.sku_code,qr_code=s.qr_code) for s in serials),
                exceptions=(dict(exception_type='damaged',description='Synthetic physical damage',evidence_file_id=file_id),)),))
        preview,_ = receipt_plan.preview_receipt(db, actor=actor, shipment_id=shipped.shipment_id, request=value)
        accepted = receiving.execute_receipt(db, actor=actor, shipment_id=shipped.shipment_id,
            request=StockReturnReceiptSubmitIn(**value.model_dump(),expected_plan_hash=preview.plan_hash,
                request_id=uuid4().hex,idempotency_key=uuid4().hex))
        receipt_id = accepted.receipt_id
        db.commit()
    assert inventory_snapshot(owner) == before
    before_preview = facts_snapshot(owner)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, receiver)
        view = inbound.preview_return_inbound(db, actor=actor, receipt_id=receipt_id)
        assert view['schema_version'] == '2.0'
        assert [(l['condition_code'],Decimal(l['accepted_qty'])) for l in view['lines']] == (
            [('new',quantity-damaged),('damaged',damaged)] if mixed else [('damaged',quantity)])
        balances_before = {}
        for part in view['lines']:
            balance = db.get(StockBalance,UUID(part['target_account_id']))
            balances_before[part['target_account_id']] = balance.quantity if balance else Decimal(0)
        db.rollback()
    assert facts_snapshot(owner) == before_preview
    rejected = reject_forged_parts(owner,api,receiver=receiver,receipt_id=receipt_id,mixed=mixed,tracked=context['tracking']=='serial')
    # Competing devices must create exactly one complete pair of targets and
    # one posting. No client trusts an interrupted response as completion.
    requests = [dict(receipt_id=receipt_id,expected_plan_hash=view['plan_hash'],
        request_id=uuid4().hex,idempotency_key=uuid4().hex) for _ in range(2)]
    barrier = Barrier(2)
    def compete(index):
        with Session(api) as db:
            actor = load_formal_principal(db,receiver)
            barrier.wait(timeout=30)
            try:
                result = inbound.execute_return_inbound(db,actor=actor,**requests[index])
                db.commit()
                return index,result,None
            except InventoryReadError as error:
                db.rollback()
                return index,None,error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(compete,range(2)))
    winners = [row for row in attempts if row[1] is not None]
    assert len(winners) == 1 and len([row for row in attempts if row[2] == 'stock_return_inbound_receipt_already_posted']) == 1
    index,posted,_ = winners[0]
    command = requests[index]
    full_after = facts_snapshot(owner)
    after = inventory_snapshot(owner)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, receiver)
        result = recovery.lookup_return_inbound_request(db, actor=actor,
            receipt_id=receipt_id,request_id=command['request_id'])
        assert result['inbound_id'] == posted['inbound_id']
        rows = tuple(db.scalars(select(StockOperationReturnInboundLine).where(
            StockOperationReturnInboundLine.inbound_id == result['inbound_id']).order_by(StockOperationReturnInboundLine.line_no)))
        assert len(rows) == (2 if mixed else 1)
        assert len({r.receipt_line_id for r in rows}) == 1
        assert sum(r.accepted_qty for r in rows) == quantity
        for row in rows:
            target = db.get(StockAccount,row.target_account_id)
            assert target.condition_code == row.condition_code
            assert db.get(StockBalance,target.id).quantity == balances_before[str(target.id)]+row.accepted_qty
        assert db.get(StockBalance,rows[0].source_account_id).quantity == 0
        for identifier in serial_ids:
            position = db.get(SerialCurrentPosition,identifier)
            assert db.get(StockAccount,position.stock_account_id).condition_code == ('damaged' if identifier in damaged_ids else 'new')
        db.rollback()
    assert inventory_snapshot(owner) == after
    assert facts_snapshot(owner) == full_after
    with Session(api) as db:
        actor = load_formal_principal(db,receiver)
        assert inbound.execute_return_inbound(db,actor=actor,**command)['replayed'] is True
        db.commit()
    assert inventory_snapshot(owner) == after
    assert facts_snapshot(owner) == full_after
    return dict(concurrentInboundSingleWinner=True,recoveryAndReplayAllFactsUnchanged=True,
        passed=True,tracking=context['tracking'],mixed=mixed,actualApiRoleCommit=True,
        twoSerialOpeningAndApprovedReturn=mixed and context['tracking']=='serial',exactIndividualSerialConditions=True,
        stockNeutralAcceptance=True,queryOnlyPreview=True,exactConditionPartitions=True,
        readOnlyOriginalRecovery=True,replayDoesNotPostAgain=True,forgedPartsRejected=rejected,
        productionAcceptance=False)
