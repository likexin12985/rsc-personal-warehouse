"""Two physical departure lines of one lot share one first receiving account."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount, StockBalance, InventoryMovement, InventoryTransaction
from app.stock_operation_models import StockOperationOutboundLine, StockOperationReturnInboundLine
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundSubmitIn
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.stock_return_receipt_schemas import StockReturnReceiptPreviewIn
from app.formal_services import stock_return_outbound_commands as departures
from app.formal_services.stock_return_outbound_plan import preview_outbound
from pg16_stock_return_account_admission_gate import commands, recovery, inbound, snapshot
from pg16_stock_return_outbound_gate import prepare_departure_worlds
from pg16_stock_return_outbound_concurrency_gate import _prepare_return
from pg16_work_order_material_gate import _checkpoint
import pg16_stock_return_shipment_gate as parcels
import pg16_stock_return_receipt_gate as receipts


def assert_multiline_return_account_gate(api_engine, fixture_engine):
    worlds = prepare_departure_worlds(api_engine, fixture_engine,
        precreate_receiving_accounts=False, with_lots=True)
    pending = _prepare_return(api_engine, fixture_engine, worlds['quantity'])
    outbound_ids = []
    for amount in ('.375', '.625'):
        with Session(api_engine) as db:
            actor = load_formal_principal(db, pending['user_id'])
            args = dict(actor=actor, work_order_id=pending['work_order_id'], operation_id=pending['operation_id'])
            request = StockReturnOutboundPreviewIn(**pending['value'].model_dump(
                exclude={'expected_plan_hash', 'request_id', 'idempotency_key'}))
            request = request.model_copy(update={'outbound_at': datetime.now(timezone.utc),
                'lines': (request.lines[0].model_copy(update={'quantity': Decimal(amount)}),)})
            checked, _ = preview_outbound(db, **args, request=request)
            result = departures.execute_outbound(db, **args, request=StockReturnOutboundSubmitIn(
                **request.model_dump(), expected_plan_hash=checked.plan_hash,
                request_id=uuid4().hex, idempotency_key=uuid4().hex))
            outbound_ids.append(result.outbound_id)
            _checkpoint(db); db.commit()
    with Session(api_engine) as db:
        actor = load_formal_principal(db, pending['user_id'])
        args = dict(actor=actor, work_order_id=pending['work_order_id'], operation_id=pending['operation_id'])
        lines = tuple(db.scalars(select(StockOperationOutboundLine).where(
            StockOperationOutboundLine.outbound_id.in_(outbound_ids)).order_by(StockOperationOutboundLine.id)))
        assert len(lines) == 2 and len({line.transit_stock_account_id for line in lines}) == 1
        request = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id,
            carrier='Synthetic batch carrier', tracking_no='PG16-MULTILINE-' + uuid4().hex,
            shipped_at=datetime.now(timezone.utc), reason='Synthetic two departures in one parcel',
            lines=[dict(outbound_line_id=line.id, quantity=line.quantity) for line in lines])
        checked, _ = parcels.preview_shipment(db, **args, request=request)
        shipped = parcels.commands.execute_shipment(db, **args, request=StockReturnShipmentSubmitIn(
            **request.model_dump(), expected_plan_hash=checked.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex))
        _checkpoint(db); db.commit()
    with Session(api_engine) as db:
        from app.models import User
        from app.inventory_models import Shipment
        shipment = db.get(Shipment, shipped.shipment_id)
        receiver = db.scalars(select(User.id).where(User.person_id == shipment.target_person_id)).one()
        context = receipts._context(db, dict(shipment_id=shipment.id, user_id=receiver))
        request = StockReturnReceiptPreviewIn(operator_person_id=context.actor.person_id,
            received_at=datetime.now(timezone.utc), reason='Synthetic whole parcel accepted separately from inbound',
            lines=[dict(shipment_line_id=line.shipment_line_id, accepted_qty=line.shipped_quantity)
                for line in context.package.lines])
        accepted = receipts._execute(db, context, receipts._command(db, context, request))
        db.commit()
    before = snapshot(api_engine)
    with Session(api_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        args, plan = inbound._args(db, accepted.receipt_id)
        assert len(plan['lines']) == 2
        targets = {UUID(line['target_account_id']) for line in plan['lines']}
        assert len(targets) == 1
        target = targets.pop()
        assert db.get(StockAccount, target) is None
        assert len({line['lot_id'] for line in plan['lines']}) == 1 and plan['lines'][0]['lot_id'] is not None
        db.rollback()
    assert snapshot(api_engine) == before
    result = inbound._commit_competing_same_request(api_engine, args)
    with Session(api_engine) as db:
        account = db.get(StockAccount, target)
        assert str(account.lot_id) == plan['lines'][0]['lot_id']
        balance = db.get(StockBalance, target)
        assert balance.quantity == Decimal(1) and balance.version == 1
        movements = tuple(db.scalars(select(InventoryMovement).where(
            InventoryMovement.transaction_id == UUID(str(result['posting_transaction_id'])))))
        assert len(movements) == 2 and {m.to_account_id for m in movements} == {target}
        assert sum(m.quantity for m in movements) == balance.quantity
        assert db.scalar(select(func.count()).select_from(StockOperationReturnInboundLine).where(
            StockOperationReturnInboundLine.inbound_id == UUID(str(result['inbound_id'])))) == 2
        # Recompute from every posted movement, independent of the balance row.
        ledger_quantity = db.scalar(select(func.sum(InventoryMovement.quantity)).join(
            InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id).where(
                InventoryMovement.to_account_id == target, InventoryTransaction.status == 'posted'))
        assert ledger_quantity == balance.quantity
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.from_account_id == target)) == 0
        args['actor'] = load_formal_principal(db, receiver)
        assert commands.execute_return_inbound(db, **args) == dict(result, replayed=True)
        assert recovery.lookup_return_inbound_request(db, actor=args['actor'], receipt_id=accepted.receipt_id,
            request_id=args['request_id']) == result
        _checkpoint(db); db.commit()
    print('PG16 lot multiline: two genuine departures, one parcel/receipt, one new account, two movements and ledger rebuild PASS', flush=True)
    return dict(tracking='lot', acceptedLines=2, receivingAccountsCreated=1, movements=2,
        quantity='1.000', readOnlyPreview=True, duplicateRace=True, ledgerRebuild=True)
