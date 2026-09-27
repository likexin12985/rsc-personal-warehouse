"""API-role regression for detached work-order inverses; every case rolls back."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as posting, work_order_material as material
from app.inventory_models import InventoryMovement, InventoryTransaction
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_removed_registration_gate import snapshot


def reversal_command(original, source_type="reversal_case"):
    return posting.InventoryReversalCommand(original_transaction_id=original.id,
        transaction_no="PG16-REV-" + uuid4().hex, source_document_type=source_type,
        source_document_id=str(uuid4()), posting_key="pg16-reversal:" + uuid4().hex,
        effective_at=datetime.now(timezone.utc))


def assert_work_order_reversal_boundary_gate(api_engine, fixture_engine):
    worlds = query_worlds(fixture_engine)
    baseline = snapshot(api_engine)
    for kind, (_, user_id, orders, line) in worlds.items():
        for operation in ("occupy", "consume", "release"):
            with Session(api_engine) as db:
                actor = load_formal_principal(db, user_id)
                fact, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                    lines=(line,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
                _checkpoint(db)
                if operation != "occupy":
                    reserved = db.scalar(select(InventoryMovement.to_account_id).where(
                        InventoryMovement.transaction_id == fact.posting_transaction_id))
                    moved = replace(line, stock_account_id=reserved,
                        target_stock_account_id=line.stock_account_id if operation == "release" else None)
                    fact, _ = getattr(material, "execute_" + operation + "_operation")(db,
                        actor=actor, work_order_id=orders[0], lines=(moved,),
                        idempotency_key=uuid4().hex, request_id=uuid4().hex)
                    _checkpoint(db)
                original = db.get(InventoryTransaction, fact.posting_transaction_id)
                command = reversal_command(original)
                try:
                    posting.reverse_inventory_transaction(db, actor=actor, command=command,
                        idempotency_key=uuid4().hex, request_id=uuid4().hex)
                except posting.InventoryPostingError as exc:
                    assert exc.code == "work_order_reversal_requires_command"
                else:
                    raise AssertionError("Generic work-order inverse passed the application guard")
                assert_direct_inverse_rejected(db, original)
                db.rollback()
            assert snapshot(api_engine) == baseline
        print(f"PG16 {kind} occupancy/consumption/release generic and direct-SQL inverses rejected; full rollback PASS", flush=True)
    from app.demand_models import WorkOrderMaterialOperation
    from app.formal_services import work_order_replacements as paired, work_order_removed_registration as registration
    from pg16_work_order_removed_registration_gate import case, recovered
    for child in ("consume", "recover"):
        with Session(api_engine) as db:
            args, line = case(db, worlds["serial"])
            identity = registration.register_removed_serial(db, **args); _checkpoint(db)
            parent = paired.execute_replacement(db, actor=args["actor"], work_order_id=args["work_order_id"],
                consume_lines=(line,), recover_lines=(recovered(db, args, identity),),
                pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0], identity.serial_id),),
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            fact = db.get(WorkOrderMaterialOperation, getattr(parent, child + "_operation_id"))
            assert_direct_inverse_rejected(db, db.get(InventoryTransaction, fact.posting_transaction_id))
            db.rollback()
        assert snapshot(api_engine) == baseline
    print("PG16 either child of a registered SN paired replacement cannot be reversed alone; full rollback PASS", flush=True)


def assert_generic_inverse_database_boundary(api_engine, fixture_engine, *, rejected):
    """Exercise the unified stock writer with a synthetic own-stock permission.

    The fixture engineer has work-order operate, not generic reverse. Only this
    test reroutes account authorization to that existing personal scope; the DB
    role, ledger writes, audit, projections and deferred constraints stay real.
    """
    _, user_id, orders, line = query_worlds(fixture_engine)["quantity"]
    baseline = snapshot(api_engine)
    with Session(api_engine) as db:
        actor = load_formal_principal(db, user_id)
        fact, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
            lines=(line,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        original = db.get(InventoryTransaction, fact.posting_transaction_id)
        authorize = posting._authorize_account_ids
        def authorize_own(db, actor, identifiers, **kwargs):
            return authorize(db, actor, identifiers, **{**kwargs, "resource": "work_order_material", "action": "operate"})
        from app.formal_services import work_order_reversal_proof
        try:
            with patch.object(work_order_reversal_proof, "require_reversal_posting", lambda *a, **k: {}), \
                    patch.object(posting, "_require_generic_reversal_origin", lambda *a, **k: None), \
                    patch.object(posting, "_authorize_account_ids", authorize_own):
                posting.reverse_inventory_transaction(db, actor=actor, command=reversal_command(original),
                    idempotency_key=uuid4().hex, request_id=uuid4().hex)
                _checkpoint(db)
        except DBAPIError as exc:
            assert rejected and getattr(exc.orig, "sqlstate", None) == "23514"
            assert "0097 work order reversal" in str(exc.orig)
        else:
            assert not rejected, "Database accepted a complete detached inverse"
        db.rollback()
    assert snapshot(api_engine) == baseline
    print("PG16 complete inverse " + ("rejected by database" if rejected else "reproduced before fix") + "; full rollback PASS", flush=True)


def assert_direct_inverse_rejected(db, original):
    command = reversal_command(original)
    # Bypass all application checks and insert the reversal header.
    # The deferred whole-transaction guard must reject its origin
    # even before a caller could attach one-sided movements.
    key = uuid4().hex
    db.add(InventoryTransaction(id=uuid4(), transaction_no=command.transaction_no,
        movement_type="reversal", source_document_type="reversal_case",
        source_document_id=str(uuid4()), posting_key=command.posting_key,
        idempotency_key_hash=key * 2, request_hash=uuid4().hex * 2,
        status="posted", effective_at=datetime.now(timezone.utc),
        posted_at=datetime.now(timezone.utc), ledger_cursor=db.scalar(select(func.max(InventoryTransaction.ledger_cursor))) + 1,
        reversed_transaction_id=original.id, actor_user_id=original.actor_user_id))
    try:
        _checkpoint(db)
    except DBAPIError as exc:
        assert getattr(exc.orig, "sqlstate", None) == "23514"
        assert "0097 work order reversal" in str(exc.orig)
    else:
        raise AssertionError("Database accepted an inverse detached from its original work order")


def assert_legacy_reversal_migration_candidates(fixture_engine):
    """Run the actual 0097 preflight on an isolated, fully migrated 0096 DB.

    Invalid legacy candidates stay uncommitted and are rolled back after the
    exact preflight rejection. Current-head complete inverse checks above are
    independent; this fixture is not a successful stock-posting example.
    """
    from pathlib import Path
    import hashlib
    import runpy
    from types import SimpleNamespace
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from migration_script_cache import cache_migration_compilation
    from app.demand_models import WorkOrderMaterialOperation
    from test_formal_access import make_organization, make_user
    from work_order_fixtures import add_order

    if fixture_engine.dialect.name != 'postgresql':
        raise RuntimeError('legacy reversal probe requires isolated PostgreSQL 0096')
    folder = Path(__file__).parents[1] / 'alembic/versions'
    with cache_migration_compilation(folder):
        migration = runpy.run_path(str(folder / '20261007_0097_work_order_reversal_boundary.py'))
        old, _ = migration['sources']()
    signatures = (migration['SIGNATURE'], 'public.rsc_oam_runtime_binding_ready_0044()')
    with fixture_engine.connect() as connection:
        assert connection.scalar(text('SELECT current_user')) == 'star_oam_migrator'
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['down_revision']
        for signature, expected in zip(signatures, (hashlib.sha256(old.encode()).hexdigest(), migration['OLD_HASH']), strict=True):
            assert connection.scalar(text("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid=to_regprocedure(:signature)"),
                dict(signature=signature)) == expected
        for table in ('users', 'inventory_transactions', 'work_order_material_operations', 'stocktake_tasks'):
            assert connection.scalar(text('SELECT count(*) FROM '+table)) == 0, 'isolated empty fixture required'

    def state():
        tables = ('users','people','organizations','auth_identities','source_systems','external_objects',
            'external_object_versions','oam_work_orders','inventory_transactions','work_order_material_operations',
            'inventory_ledger_heads','stock_balances','audit_events','audit_chain_heads','outbox_events','alembic_version')
        with fixture_engine.connect() as connection:
            facts = {table: tuple(sorted(repr(tuple(row)) for row in connection.execute(text('SELECT * FROM '+table)))) for table in tables}
            catalog = connection.execute(text("""SELECT p.oid,p.prosrc,p.proowner,p.proacl,p.prosecdef,p.proconfig
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY p.oid""")).all()
            triggers = connection.execute(text("SELECT oid,tgrelid,tgfoid,tgenabled,tgdeferrable,tginitdeferred FROM pg_trigger ORDER BY oid")).all()
            return facts, catalog, triggers
    baseline = state()
    for label in ('original_source', 'inverse_source', 'operation_binding'):
        with Session(fixture_engine) as db:
            org = make_organization(db, name='Synthetic historical reversal fixture')
            user, person = make_user(db, org, name='Synthetic historical actor')
            order = add_order(db, SimpleNamespace(person=person, organization=org))
            at = datetime.now(timezone.utc)
            def transaction(kind, source, cursor, original=None):
                row = InventoryTransaction(id=uuid4(), transaction_no='LEGACY-'+uuid4().hex,
                    movement_type=kind, source_document_type=source, source_document_id=str(order.id),
                    posting_key='legacy-probe:'+uuid4().hex, idempotency_key_hash=uuid4().hex*2,
                    request_hash=uuid4().hex*2, status='posted', effective_at=at, posted_at=at,
                    ledger_cursor=cursor, reversed_transaction_id=original, actor_user_id=user.id, created_at=at)
                db.add(row); db.flush(); return row
            original = transaction('reserve', 'work_order_material' if label=='original_source' else 'legacy_stock', 1)
            transaction('reversal', 'work_order_material' if label=='inverse_source' else 'legacy_inverse', 2, original.id)
            if label == 'operation_binding':
                db.add(WorkOrderMaterialOperation(id=uuid4(), operation_no='LEGACY-'+uuid4().hex,
                    oam_work_order_id=order.id, operator_person_id=person.id, operation_type='occupy', status='posted',
                    posting_transaction_id=original.id, idempotency_key_hash=uuid4().hex*2,
                    request_hash=uuid4().hex*2, created_at=at))
                db.flush()
            assert db.scalar(text('SELECT EXISTS ('+migration['forbidden_reversals']('public.')+')'))
            with cache_migration_compilation(folder), Operations.context(MigrationContext.configure(db.connection())):
                try:
                    migration['upgrade']()
                except DBAPIError as exc:
                    assert getattr(exc.orig, 'sqlstate', None) == 'P0001'
                    assert '0097 transition blocked: detached work-order reversals require reviewed compensation' in str(exc.orig)
                else:
                    raise AssertionError('0097 accepted a detached legacy reversal candidate')
            db.rollback()
        assert state() == baseline, label
    print('PG16 isolated 0096: all three 0097 legacy preflight rejections and full rollback PASS', flush=True)
    return dict(rejectedLegacyCandidates=3, exact0097Preflight=True, fixtureAndCatalogRollback=True,
        historicalRevision=migration['down_revision'], committedStockPostingProven=False)


def assert_reversal_migration_rejects_detached_history(api_engine, fixture_engine):
    """Keep current facts intact; historical downgrade tests use another DB."""
    from test_postgresql16_release_gate import (_create_opening_backfill_database, _drop_opening_backfill_database,
        _run_alembic, _legacy_backfill_engine)

    def catalog():
        with fixture_engine.connect() as connection:
            return connection.execute(text("""SELECT oid,prosrc,proowner,proacl,prosecdef,proconfig FROM pg_proc
                WHERE oid IN ('public.rsc_check_work_order_material_transaction_0090(uuid)'::regprocedure,
                    'public.rsc_oam_runtime_binding_ready_0044()'::regprocedure) ORDER BY oid""")).all()
    before = snapshot(api_engine), catalog()
    database = _create_opening_backfill_database()
    isolated = None
    try:
        for action, revision in (('upgrade','20261006_0096'),('upgrade','20261007_0097'),('downgrade','20261006_0096')):
            _run_alembic(action, revision, database_name=database)
        isolated = _legacy_backfill_engine(database)
        assert_legacy_reversal_migration_candidates(isolated)
    finally:
        if isolated is not None:
            isolated.dispose()
        _drop_opening_backfill_database(database)
    assert (snapshot(api_engine), catalog()) == before
    print('PG16 independent 0096/0097 roundtrip and legacy rejection; current-head facts untouched PASS', flush=True)
