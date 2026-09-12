"""Actual API role posts one accepted receipt through the unified ledger."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_inbound_gate(api_engine, security_engine, *, request_id, receipt_id, admin_user_id):
    from app.demand_models import MaterialRequest, MaterialRequestCommand
    from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent
    from app.formal_access import load_formal_principal
    from app.inventory_models import (InboundOrder, InboundPosting, InventoryTransaction, OutboundPosting,
        Receipt, ReceiptLine, ReceiptSerial, SerialCurrentPosition, Shipment, ShipmentLine, StockAccount, StockBalance, InventoryLedgerHead)
    from app.formal_services import material_request_inbound as inbound
    from app.formal_services.inventory_posting import InventoryPostingError
    from app.database_security import _INBOUND_RECEIPT_INDEX_SQL, _assert_inbound_receipt_index

    with Session(api_engine) as db:
        receipt = db.get(Receipt, receipt_id)
        shipment = db.get(Shipment, receipt.shipment_id)
        location_id, person_id = shipment.target_location_id, shipment.target_person_id
        version = db.get(MaterialRequest, request_id).version
        accepted = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt_id, ReceiptLine.accepted_qty > 0)).all())
        _assert_inbound_receipt_index(db.execute(_INBOUND_RECEIPT_INDEX_SQL).mappings().all())

    def create_order():
        with Session(api_engine) as db:
            value = inbound.create_inbound_order(db, actor=load_formal_principal(db, admin_user_id),
                request_id=request_id, expected_version=version, receipt_id=receipt_id,
                target_location_id=location_id, target_person_id=person_id, trace_request_id=f"pg16-inbound-order-{receipt_id}")
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
            return value

    if not accepted:
        with pytest.raises(inbound.InboundError, match='合格数量'):
            create_order()
        with Session(api_engine) as db:
            assert db.scalar(select(InboundOrder.id).where(InboundOrder.receipt_id == receipt_id)) is None
        print('PG16 inbound: rejected-only receipt cannot create a posting order', flush=True)
        return

    targets = {}
    with Session(security_engine) as db:
        db.execute(text('SET LOCAL ROLE star_oam_migrator'))
        for line in accepted:
            shipped = db.get(ShipmentLine, line.shipment_line_id)
            outbound = db.get(OutboundPosting, shipped.outbound_posting_id)
            source = db.get(StockAccount, outbound.target_stock_account_id)
            target = db.scalar(select(StockAccount).where(StockAccount.location_id == location_id,
                StockAccount.custodian_person_id == person_id, StockAccount.owner_org_id == source.owner_org_id,
                StockAccount.material_id == source.material_id, StockAccount.condition_code == source.condition_code,
                StockAccount.lot_id == source.lot_id, StockAccount.availability_bucket == 'available'))
            if target is None:
                target = StockAccount(id=uuid4(), location_id=location_id, custodian_person_id=person_id,
                    owner_org_id=source.owner_org_id, material_id=source.material_id,
                    condition_code=source.condition_code, lot_id=source.lot_id, availability_bucket='available')
                db.add(target); db.flush()
                db.add(StockBalance(stock_account_id=target.id, quantity=Decimal('0.000'), version=0, ledger_cursor=0))
            targets[line.id] = (source.id, target.id, line.accepted_qty)
        db.commit()

    order = create_order(); order_id = order['inbound_order_id']
    def snapshot():
        with Session(api_engine) as db:
            return (db.get(MaterialRequest, request_id).version,
                db.scalar(select(func.count()).select_from(InboundPosting)),
                db.scalar(select(func.count()).select_from(InventoryTransaction)),
                tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).order_by(StockBalance.stock_account_id))),
                tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
                tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id, SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id))),
                tuple(db.execute(select(InventoryLedgerHead.id, InventoryLedgerHead.next_cursor).order_by(InventoryLedgerHead.id))),
                tuple(db.scalar(select(func.count()).select_from(model)) for model in (MaterialRequestCommand, AuditEvent, OutboxEvent, NotificationEvent)))
    def post(key):
        with Session(api_engine) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            value = inbound.post_inbound_order(db, actor=load_formal_principal(db, admin_user_id),
                inbound_order_id=order_id, material_request_id=request_id, idempotency_key=key,
                request_id=f'trace-{key}')
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
            return value

    before = snapshot()
    with patch.object(inbound, 'append_audit_event', side_effect=RuntimeError('injected inbound audit failure')):
        with pytest.raises(RuntimeError, match='injected inbound'):
            post(f'pg16-inbound-rollback-{order_id}')
    assert snapshot() == before
    barrier = Barrier(2)
    def race(key):
        barrier.wait(timeout=30)
        try: return key, post(key)
        except InventoryPostingError as error: return key, error
    keys = [f'pg16-inbound-{order_id}-{n}' for n in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, keys))
    successful = [(key, result) for key, result in results if not isinstance(result, Exception)]
    failed = [result for _, result in results if isinstance(result, Exception)]
    assert len(successful) == len(failed) == 1 and failed[0].category == 'conflict'
    key, result = successful[0]
    after = snapshot()
    assert after[:3] == (before[0] + 1, before[1] + 1, before[2] + 1)
    quantities_before, quantities_after = dict(before[3]), dict(after[3])
    deltas = {}
    for source, target, qty in targets.values():
        deltas[source] = deltas.get(source, Decimal(0)) - qty
        deltas[target] = deltas.get(target, Decimal(0)) + qty
    assert all(quantities_after[account] == qty + deltas.get(account, Decimal(0)) for account, qty in quantities_before.items())
    assert post(key)['inventory_transaction_id'] == result['inventory_transaction_id']
    assert snapshot() == after
    with Session(api_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.get(InboundOrder, order_id).status == 'pending'
        assert db.get(InboundOrder, order_id).posting_transaction_id is None
        projected = inbound.list_inbound_orders(db, actor=load_formal_principal(db, admin_user_id), request_id=request_id)
        row = next(row for row in projected if row['inbound_order_id'] == order_id)
        assert row['status'] == 'posted' and row['posting_transaction_id'] == result['inventory_transaction_id']
        command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.request_id == request_id,
            MaterialRequestCommand.operation == 'personal_inbound'))
        assert command.target_version == after[0]
        for line_id, (_, target, _) in targets.items():
            for serial_id in db.scalars(select(ReceiptSerial.serial_id).where(ReceiptSerial.receipt_line_id == line_id, ReceiptSerial.accepted.is_(True))):
                assert db.get(SerialCurrentPosition, serial_id).stock_account_id == target
    with Session(api_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("""INSERT INTO inbound_orders SELECT
                (jsonb_populate_record(NULL::inbound_orders, to_jsonb(original) || jsonb_build_object(
                    'id', CAST(:id AS text), 'inbound_no', CAST(:number AS text)))).*
                FROM inbound_orders original WHERE original.id=:original"""),
                {'id': str(uuid4()), 'number': f'PG16-DUP-{uuid4().hex}', 'original': order_id})
        db.rollback()
    with Session(security_engine) as db:
        db.execute(text('SET LOCAL session_replication_role = replica'))
        with pytest.raises(DBAPIError, match='append-only'):
            db.execute(text('DELETE FROM inbound_postings WHERE inbound_order_id=:id'), {'id': order_id})
        db.rollback()
    assert snapshot() == after
    print('PG16 inbound: actual ledger, atomic rollback, race, immutable order, version command and READ ONLY projection PASS', flush=True)
