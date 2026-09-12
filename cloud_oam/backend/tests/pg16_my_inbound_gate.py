"""Actual engineer/API-role receipt posting, rollback, race and recovery."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


def assert_my_inbound_gate(api_engine, security_engine, *, request_id, receipt_id, admin_user_id):
    from app.demand_models import MaterialRequest, MaterialRequestCommand
    from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent
    from app.formal_access import load_formal_principal
    from app.models import User
    from app.inventory_models import (Receipt, ReceiptLine, ReceiptSerial, Shipment, ShipmentLine,
        OutboundPosting, InboundOrder, InboundPosting, InventoryTransaction, StockAccount,
        StockBalance, SerialCurrentPosition, InventoryLedgerHead)
    from app.material_request_my_inbound_schemas import MyInboundIn
    from app.formal_services import material_request_my_inbound as service
    from app.formal_services import material_request_inbound as inbound
    from app.formal_services import inventory_posting as inventory
    from test_material_request_draft_service import SECRET

    with Session(api_engine) as db:
        receipt = db.get(Receipt, receipt_id)
        actor_id = db.scalars(select(User.id).where(User.person_id == receipt.receiver_person_id)).one()
        actor = load_formal_principal(db, actor_id)
        assert actor_id != admin_user_id
        assert not any(p.resource == 'inventory_transaction' and p.action == 'post' for p in actor.entitlements)
        shipment = db.get(Shipment, receipt.shipment_id)
        location_id, person_id = shipment.target_location_id, shipment.target_person_id
        value = MyInboundIn(expected_request_version=db.get(MaterialRequest, request_id).version,
            receipt_id=receipt_id, receipt_request_hash=receipt.request_hash)
        accepted = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt_id,
            ReceiptLine.accepted_qty > 0)).all())
        assert db.scalar(select(InboundOrder.id).where(InboundOrder.receipt_id == receipt_id)) is None
        dimensions = {}
        for line in accepted:
            source = db.get(StockAccount, db.get(OutboundPosting,
                db.get(ShipmentLine, line.shipment_line_id).outbound_posting_id).target_stock_account_id)
            dimensions[line.id] = (source.id, dict(owner_org_id=source.owner_org_id,
                custodian_person_id=person_id, location_id=location_id, material_id=source.material_id,
                condition_code=source.condition_code, lot_id=source.lot_id, availability_bucket='available'), line.accepted_qty)

    def snapshot():
        with Session(api_engine) as db:
            return (db.get(MaterialRequest, request_id).version,
                tuple(db.scalar(select(func.count()).select_from(model)) for model in (
                    InboundOrder, InboundPosting, InventoryTransaction, MaterialRequestCommand,
                    AuditEvent, OutboxEvent, NotificationEvent)),
                tuple(db.scalars(select(StockAccount.id).order_by(StockAccount.id))),
                tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity,
                    StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
                tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id,
                    SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id))),
                tuple(db.execute(select(InventoryLedgerHead.id, InventoryLedgerHead.next_cursor).order_by(InventoryLedgerHead.id))))

    def post(key):
        with Session(api_engine) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            result = service.create_my_inbound(db, actor=load_formal_principal(db, actor_id),
                request_id=request_id, payload=value, idempotency_key=key, secret=SECRET, trace_request_id=f'trace-{key}')
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
            return result

    def candidate_status():
        from app.formal_services.material_request_my_inbound_candidates import list_my_inbound_candidates
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            actor = load_formal_principal(db, actor_id)
            after_id = None
            while True:
                page = list_my_inbound_candidates(db, actor=actor, request_id=request_id, after_id=after_id)
                for item in page.items:
                    if item.receipt_id == receipt_id:
                        return item
                after_id = page.next_after_id
                assert after_id is not None, 'original receipt must remain discoverable'

    before = snapshot()
    assert candidate_status().status == ('pending' if accepted else 'no_accepted')
    assert snapshot() == before
    if not accepted:
        with pytest.raises(service.MaterialRequestReadError, match='合格数量'):
            post(f'my-inbound-rejected-{receipt_id}')
        assert snapshot() == before
        print('PG16 own inbound: rejected-only receipt has no order or posting PASS', flush=True)
        return

    with Session(api_engine) as db:
        order = inbound._new_inbound_order(db, receipt_id=receipt_id,
            target_location_id=location_id, target_person_id=person_id)
        command = inbound._order_posting_command(db, order, create_missing=True)
        with pytest.raises(inventory.InventoryPostingError) as denied:
            inventory.post_inventory_transaction(db, actor=load_formal_principal(db, actor_id), command=command,
                idempotency_key=f'forbidden-own-inbound-{receipt_id}', request_id=f'forbidden-own-trace-{receipt_id}')
        assert denied.value.category == 'forbidden'
        db.rollback()
    assert snapshot() == before

    for target, name in ((inbound, 'append_audit_event'), (service, 'append_audit_event')):
        with patch.object(target, name, side_effect=RuntimeError('injected own inbound failure')):
            with pytest.raises(RuntimeError, match='injected own inbound'):
                post(f'my-inbound-rollback-{receipt_id}-{target.__name__.split(".")[-1]}')
        assert snapshot() == before

    barrier = Barrier(2)
    def race(key):
        barrier.wait(timeout=30)
        try: return key, post(key)
        except (inventory.InventoryPostingError, service.MaterialRequestReadError) as error:
            return key, error
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(race, [f'my-inbound-{receipt_id}-{i}' for i in range(2)]))
    success = [(key, result) for key, result in outcomes if not isinstance(result, Exception)]
    failure = [result for _, result in outcomes if isinstance(result, Exception)]
    assert len(success) == len(failure) == 1 and failure[0].category == 'conflict'
    key, result = success[0]
    after = snapshot()
    assert after[0] == before[0] + 1
    assert after[1][:4] == tuple(count + 1 for count in before[1][:4])
    before_qty, after_qty = {row[0]: row[1] for row in before[3]}, {row[0]: row[1] for row in after[3]}
    deltas = {}
    with Session(api_engine) as db:
        for line_id, (source, columns, qty) in dimensions.items():
            target = db.scalars(select(StockAccount).filter_by(**columns)).one()
            deltas[source] = deltas.get(source, Decimal(0)) - qty
            deltas[target.id] = deltas.get(target.id, Decimal(0)) + qty
            for serial_id in db.scalars(select(ReceiptSerial.serial_id).where(
                    ReceiptSerial.receipt_line_id == line_id, ReceiptSerial.accepted.is_(True))):
                assert db.get(SerialCurrentPosition, serial_id).stock_account_id == target.id
    assert all(after_qty.get(account, Decimal(0)) == before_qty.get(account, Decimal(0)) + deltas.get(account, Decimal(0))
        for account in set(before_qty) | set(after_qty))
    candidate = candidate_status()
    assert candidate.status == 'posted' and candidate.detail.inventory_transaction_id == result.inventory_transaction_id
    assert snapshot() == after
    replay = post(key)
    assert replay.idempotency_replayed and replay.inventory_transaction_id == result.inventory_transaction_id
    assert snapshot() == after
    with Session(api_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        assert service.my_inbound_command_status(db, actor=actor, request_id=request_id,
            idempotency_key=key, secret=SECRET).inventory_transaction_id == result.inventory_transaction_id
        assert service.my_inbound_trace_status(db, actor=actor, request_id=request_id,
            trace_request_id=f'trace-{key}').inventory_transaction_id == result.inventory_transaction_id
        assert db.get(InboundOrder, result.inbound_order_id).status == 'pending'
        assert db.get(InboundOrder, result.inbound_order_id).posting_transaction_id is None
    assert snapshot() == after
    print('PG16 own inbound: actual engineer ledger, rollback, race, accepted SN and READ ONLY recovery PASS', flush=True)
