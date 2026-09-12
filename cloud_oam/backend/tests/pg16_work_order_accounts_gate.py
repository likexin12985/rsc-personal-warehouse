"""Create reserved dimensions only through real API-role work-order postings."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.demand_models import OamWorkOrder
from app.foundation_models import ExternalObject, OutboxEvent, SourceSystem
from app.inventory_models import InventorySerial, SerialCurrentPosition, StockAccount, StockBalance, StockLocation
from app.formal_access import load_formal_principal
from app.models import User
from app.stocktake_models import InventoryOpeningEstablishment
from app.formal_services import work_order_material as service
from app.formal_services import inventory_posting as inventory
from pg16_work_order_material_gate import World, _checkpoint, _db_denies, _run, _snapshot


def _target(db, source):
    return db.scalar(select(StockAccount).filter_by(**{key: getattr(source, key) for key in (
        "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")},
        availability_bucket="reserved"))


def prepare_work_order_stock(api_engine, fixture_engine, *, admin_user_id):
    """Supplement the accepted stock with synthetic items via the real ledger.

    The receipt suite deliberately accepts only 0.25 quantity and one SN. Keep
    its rejection facts intact, and supply enough stock for both later gates.
    Fixture credentials create SN identifiers only, never inventory balances.
    """
    with Session(fixture_engine) as db:
        accounts = db.scalars(select(StockAccount).join(StockBalance,
            StockBalance.stock_account_id == StockAccount.id).join(StockLocation,
            StockLocation.id == StockAccount.location_id).join(InventoryOpeningEstablishment,
            (InventoryOpeningEstablishment.owner_org_id == StockAccount.owner_org_id)
            & (InventoryOpeningEstablishment.location_id == StockAccount.location_id)).where(
                StockLocation.location_type == "personal", StockLocation.status == "active",
                StockAccount.availability_bucket == "available", StockBalance.quantity > 0,
            ).order_by(StockAccount.id)).all()
        plans = {}
        for account in accounts:
            serial_count = len(tuple(db.scalars(select(SerialCurrentPosition.serial_id).where(
                SerialCurrentPosition.stock_account_id == account.id))))
            kind = "serial" if serial_count else "quantity"
            if kind in plans or _target(db, account) is not None:
                continue
            amount = max(Decimal(0), Decimal(2 if serial_count else 3)
                         - db.get(StockBalance, account.id).quantity)
            serials = tuple(uuid4() for _ in range(int(amount))) if serial_count else ()
            now = datetime.now(timezone.utc)
            db.add_all(InventorySerial(id=identifier, material_id=account.material_id,
                lot_id=account.lot_id, serial_no="PG16-WO-" + identifier.hex,
                qr_code="PG16-WO-QR-" + identifier.hex, lifecycle_status="active",
                created_at=now, updated_at=now) for identifier in serials)
            plans[kind] = account.id, amount, serials
        assert set(plans) == {"quantity", "serial"}, "receipt gate must establish both accepted stock types"
        db.commit()
    with Session(api_engine) as db:
        for account_id, amount, serials in plans.values():
            if amount == 0:
                continue
            key = "pg16-work-order-fixture-" + uuid4().hex
            inventory.post_inventory_transaction(db, actor=load_formal_principal(db, admin_user_id),
                command=inventory.InventoryPostingCommand(transaction_no=key, movement_type="inbound",
                    source_document_type="pg16_work_order_fixture", source_document_id=key,
                    posting_key=key, effective_at=datetime.now(timezone.utc),
                    movements=(inventory.InventoryMovementCommand(from_account_id=None,
                        to_account_id=account_id, quantity=amount, serial_ids=serials,
                        external_boundary_code="PG16_WORK_ORDER_FIXTURE"),)),
                idempotency_key=key, request_id=key)
        _checkpoint(db)
        db.commit()
    print("PG16 work-order fixture: accepted stock supplemented through the API ledger PASS", flush=True)


def _fresh_worlds(engine):
    worlds = {}
    with Session(engine) as db:
        candidates = db.scalars(select(StockAccount).join(StockBalance,
            StockBalance.stock_account_id == StockAccount.id).join(StockLocation,
            StockLocation.id == StockAccount.location_id).join(InventoryOpeningEstablishment,
            (InventoryOpeningEstablishment.owner_org_id == StockAccount.owner_org_id)
            & (InventoryOpeningEstablishment.location_id == StockAccount.location_id)).where(
                StockLocation.location_type == "personal", StockLocation.status == "active",
                StockAccount.availability_bucket == "available", StockBalance.quantity >= 1,
            ).order_by(StockAccount.id)).all()
        from types import SimpleNamespace
        from work_order_fixtures import add_order, canonical_source
        source_system = canonical_source(db)
        for account in candidates:
            ids = tuple(db.scalars(select(SerialCurrentPosition.serial_id).where(
                SerialCurrentPosition.stock_account_id == account.id).order_by(SerialCurrentPosition.serial_id)))
            kind = "serial" if ids else "quantity"
            if kind in worlds or _target(db, account) is not None:
                continue
            actor = db.scalar(select(User.id).where(User.person_id == account.custodian_person_id))
            if actor is None:
                continue
            order = add_order(db, SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),
                organization=SimpleNamespace(id=account.owner_org_id)), source_system)
            worlds[kind] = World(actor, account.id, None, (order.id,), ids)
        db.commit()
    assert set(worlds) == {"quantity", "serial"}, "gate needs accepted personal stock with absent reserved dimensions"
    return worlds


def assert_work_order_accounts_gate(api_engine, fixture_engine):
    worlds = _fresh_worlds(fixture_engine)
    for kind, world in worlds.items():
        before = _snapshot(api_engine)
        with Session(api_engine) as db:
            source = db.get(StockAccount, world.account_id)
            source_quantity = db.get(StockBalance, source.id).quantity
            key = uuid4().hex
            first, posted = _run(db, world, "occupy", key=key)
            _checkpoint(db)
            target = _target(db, source)
            assert target is not None and target.created_at == target.updated_at
            assert db.get(StockBalance, source.id).quantity == source_quantity - 1
            balance = db.get(StockBalance, target.id)
            assert balance.quantity == 1 and balance.version == 1 and balance.ledger_cursor == posted.ledger_cursor
            again, _ = _run(db, world, "occupy", key=key)
            _checkpoint(db)
            assert again.id == first.id and _target(db, source).id == target.id
            db.rollback()
        assert _snapshot(api_engine) == before
        with Session(api_engine) as db:
            assert _target(db, db.get(StockAccount, world.account_id)) is None

        def orphan_account(db):
            source = db.get(StockAccount, world.account_id)
            now = datetime.now(timezone.utc)
            row = StockAccount(id=uuid4(), **{key: getattr(source, key) for key in (
                "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")},
                availability_bucket="reserved", created_at=now, updated_at=now)
            db.add(row); db.flush()
            db.add(StockBalance(stock_account_id=row.id, quantity=Decimal(0), ledger_cursor=0, version=0))
        _db_denies(orphan_account, api_engine, "opening recount observation")

        def missing_outbox(db):
            _run(db, world, "occupy")
            pending = [row for row in db.new if isinstance(row, OutboxEvent)
                       and row.event_type == "work_order_material_operation_posted"]
            assert len(pending) == 1
            db.expunge(pending[0])
        _db_denies(missing_outbox, api_engine, "0090 work order command audit or outbox mismatch")

        if kind == "quantity":
            post = service.post_inventory_transaction
            def forged_balance(db, **kwargs):
                target_id = kwargs["command"].movements[0].to_account_id
                db.add(StockBalance(stock_account_id=target_id, quantity=Decimal(99), ledger_cursor=0, version=0))
                db.flush()
                return post(db, **kwargs)
            def extra_stock(db):
                with patch.object(service, "post_inventory_transaction", side_effect=forged_balance):
                    _run(db, world, "occupy")
            before_forgery = _snapshot(api_engine)
            with Session(api_engine) as db:
                try:
                    extra_stock(db)
                except service.InventoryPostingError as exc:
                    assert exc.code == "inventory_balance_projection_drift"
                else:
                    raise AssertionError("the ledger accepted a fabricated initial balance")
                finally:
                    db.rollback()
            assert _snapshot(api_engine) == before_forgery
            # Verify the database boundary independently of the service preflight.
            with patch.object(inventory, "_validate_locked_balance_projections", return_value=None):
                _db_denies(extra_stock, api_engine, "opening recount observation")

    for kind, world in worlds.items():
        barrier = Barrier(2)
        key = uuid4().hex
        def worker():
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                barrier.wait(timeout=10)
                operation, posted = _run(db, world, "occupy", key=key)
                target_id = _target(db, db.get(StockAccount, world.account_id)).id
                result = operation.id, posted.transaction_id, target_id
                db.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        assert results[0] == results[1]
        target_id = results[0][2]
        with Session(api_engine) as db:
            target = _target(db, db.get(StockAccount, world.account_id))
            assert target.id == target_id and db.get(StockBalance, target_id).quantity == 1
            if kind == "serial":
                assert db.get(SerialCurrentPosition, world.serial_ids[0]).stock_account_id == target_id
            # The following separate business transaction uses the newly created dimension.
            _run(db, replace(world, reserved_id=target_id), "release" if kind == "serial" else "consume")
            db.commit()
        with Session(api_engine) as db:
            assert db.get(StockBalance, target_id).quantity == 0
    print("PG16 first reserved account, quantity/SN posting, no orphan or extra stock, rollback and concurrent replay PASS", flush=True)
