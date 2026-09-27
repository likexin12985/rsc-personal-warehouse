"""First receiving dimensions through actual services, API role and PG16 guards."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition
from app.formal_services import stock_return_inbound_accounts as accounts
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services import stock_return_inbound_recovery as recovery
from app.formal_services.stock_return_inbound_facts import document
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_outbound_gate import prepare_departure_worlds
from pg16_stock_return_outbound_concurrency_gate import _prepare_return, departures
import pg16_stock_return_shipment_gate as parcels
import pg16_stock_return_receipt_gate as receipts
import pg16_stock_return_inbound_gate as inbound


def snapshot(engine):
    with engine.connect() as db:
        rows = tuple(db.execute(text('SELECT * FROM stock_accounts ORDER BY id')))
    return inbound.inbound_snapshot(engine), rows


def assert_return_account_admission_gate(api_engine, fixture_engine, *, with_lots=False):
    with api_engine.connect() as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
    worlds = prepare_departure_worlds(api_engine, fixture_engine, precreate_receiving_accounts=False, with_lots=with_lots)
    for world in worlds.values():
        pending = _prepare_return(api_engine, fixture_engine, world)
        with Session(api_engine) as db:
            departures.execute_outbound(db, actor=load_formal_principal(db, pending['user_id']),
                work_order_id=pending['work_order_id'], operation_id=pending['operation_id'], request=pending['value'])
            _checkpoint(db); db.commit()
    origins = parcels.parcel_candidates(api_engine, worlds)
    for candidate in origins.values():
        with Session(api_engine) as db:
            command, _ = parcels._command(db, candidate, '1')
            parcels.commands.execute_shipment(db, **parcels._coordinates(db, candidate), request=command)
            _checkpoint(db); db.commit()
    selected = receipts.receipt_candidates(api_engine, tuple(origins.values()))
    accepted = {}
    for kind, candidate in selected.items():
        with Session(api_engine) as db:
            context = receipts._context(db, candidate)
            request = context.request
            if kind == 'quantity':
                request = request.model_copy(update={'lines': (request.lines[0].model_copy(
                    update={'accepted_qty': Decimal('.375')}),)})
            value = receipts._command(db, context, request=request)
            accepted[kind] = receipts._execute(db, context, value).receipt_id
            db.commit()
    results = []
    original_plans = {}
    rejection = 'API stock account insert must terminate a verified opening recount observation graph'
    for kind in ('quantity', 'serial'):
        receipt_id = accepted[kind]
        before = snapshot(api_engine)
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            args, plan = inbound._args(db, receipt_id)
            again = commands.preview_return_inbound(db, actor=args['actor'], receipt_id=receipt_id)
            assert again['plan_hash'] == plan['plan_hash']
            assert all(db.get(StockAccount, UUID(line['target_account_id'])) is None for line in plan['lines'])
            db.rollback()
        assert snapshot(api_engine) == before
        # The same runtime role still cannot commit an empty receiving account.
        with Session(api_engine) as db:
            args, plan = inbound._args(db, receipt_id)
            accounts.materialize_targets(db, plan=plan, created_at=plan['checked_at'])
            try:
                db.execute(text('SET CONSTRAINTS trg_stock_accounts_opening_observation_commit_0023 IMMEDIATE'))
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514' and rejection in str(error.orig)
            else:
                raise AssertionError('unproved empty return account was admitted')
            finally:
                db.rollback()
        assert snapshot(api_engine) == before
        # An otherwise complete inbound cannot claim an older creation time.
        with Session(api_engine) as db:
            args, plan = inbound._args(db, receipt_id)
            def corrupt(session, *_):
                for row in tuple(session.new):
                    if isinstance(row, StockAccount):
                        row.created_at -= timedelta(seconds=1)
                        row.updated_at = row.created_at
            event.listen(db, 'before_flush', corrupt)
            try:
                with patch.object(commands, 'inbound_result', lambda _db, *, actor, fact, **kw: document(fact)):
                    commands.execute_return_inbound(db, **args)
                db.execute(text('SET CONSTRAINTS trg_stock_accounts_opening_observation_commit_0023 IMMEDIATE'))
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514' and rejection in str(error.orig)
            else:
                raise AssertionError('forged first-account timestamp was admitted')
            finally:
                event.remove(db, 'before_flush', corrupt); db.rollback()
        assert snapshot(api_engine) == before
        with Session(api_engine) as db:
            args, plan = inbound._args(db, receipt_id)
        original_plans[kind] = plan
        result = inbound._commit_competing_same_request(api_engine, args)
        with Session(api_engine) as db:
            actor = load_formal_principal(db, args['actor'].user_id)
            assert recovery.lookup_return_inbound_request(db, actor=actor,
                receipt_id=receipt_id, request_id=args['request_id']) == result
            assert commands.execute_return_inbound(db, **dict(args, actor=actor)) == dict(result, replayed=True)
            for line in plan['lines']:
                target = UUID(line['target_account_id'])
                source = db.get(StockAccount, UUID(line['source_account_id']))
                target_account = db.get(StockAccount, target)
                assert target_account.lot_id == source.lot_id
                assert (target_account.lot_id is not None) == with_lots
                balance = db.get(StockBalance, target)
                assert balance.quantity == Decimal(line['accepted_qty']) and balance.version == 1
                for serial in line['serial_ids']:
                    assert db.get(SerialCurrentPosition, UUID(serial)).stock_account_id == target
            _checkpoint(db); db.commit()
        inbound._assert_receipt_state_read_only(api_engine, receipt_id, args['actor'].user_id, result)
        results.append(dict(tracking=('lot' if kind=='quantity' else 'lot_and_serial') if with_lots else kind, readOnlyPreview=True, emptyAccountRejected=True,
            forgedCreationTimeRejected=True, fullRollback=True, concurrentSinglePosting=True, recovery=True))
        print(f'PG16 first return account {kind}: preview, SQL rejection, atomic commit and duplicate race PASS', flush=True)
    # A second partial acceptance must reuse the first exact account.
    with Session(api_engine) as db:
        context = receipts._context(db, selected['quantity'])
        request = context.request.model_copy(update={'lines': (context.request.lines[0].model_copy(
            update={'accepted_qty': Decimal('.625')}),)})
        value = receipts._command(db, context, request=request)
        later = receipts._execute(db, context, value)
        db.commit()
        args, plan = inbound._args(db, later.receipt_id)
        first = original_plans['quantity']['lines'][0]['target_account_id']
        assert plan['lines'][0]['target_account_id'] == first
        count = db.scalar(select(text('count(*)')).select_from(StockAccount))
        commands.execute_return_inbound(db, **args); _checkpoint(db); db.commit()
        assert db.scalar(select(text('count(*)')).select_from(StockAccount)) == count
        balance = db.get(StockBalance, UUID(first))
        assert balance.quantity == Decimal(1) and balance.version == 2
    results.append(dict(tracking='lot' if with_lots else 'quantity', subsequentReceiptReusesExactAccount=True, totalQuantity='1.000'))
    return results
