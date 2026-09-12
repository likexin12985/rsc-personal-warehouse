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


def assert_reversal_migration_rejects_detached_history(api_engine, fixture_engine):
    """Inject legacy corruption only inside a schema-and-data rollback sandbox."""
    from pathlib import Path
    import runpy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = runpy.run_path(str(Path(__file__).parents[1] /
        "alembic/versions/20261007_0097_work_order_reversal_boundary.py"))
    successors = [runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions" / filename))
        for filename in ("20261010_0100_stock_return_orders.py", "20261009_0099_work_order_reversal_seals.py", "20261008_0098_work_order_reversals.py")]
    def catalog():
        with fixture_engine.connect() as connection:
            return connection.execute(text("""SELECT oid,prosrc,proowner,proacl,prosecdef,proconfig FROM pg_proc
                WHERE oid IN ('public.rsc_check_work_order_material_transaction_0090(uuid)'::regprocedure,
                    'public.rsc_oam_runtime_binding_ready_0044()'::regprocedure) ORDER BY oid""")).all()
    before = snapshot(api_engine), catalog()
    with fixture_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                # Recreate the actual 0097 catalog in this rollback-only
                # transaction before testing its legacy-history rejection.
                # This stage precedes permanent 0098/0099 fixture facts.
                for successor in successors:
                    successor["downgrade"]()
                migration["downgrade"]()
                original = connection.execute(text("""SELECT id,actor_user_id FROM inventory_transactions
                    WHERE source_document_type='work_order_material' ORDER BY ledger_cursor LIMIT 1""")).one_or_none()
                assert original is not None, "Requires existing synthetic work-order history"
                connection.execute(text("""INSERT INTO inventory_transactions
                    (id,transaction_no,movement_type,source_document_type,source_document_id,posting_key,
                     idempotency_key_hash,request_hash,status,effective_at,posted_at,ledger_cursor,
                     reversed_transaction_id,actor_user_id,created_at)
                    VALUES (:id,:number,'reversal','legacy_inverse',:document,:posting,:hash,:hash,'posted',
                        now(),now(),(SELECT max(ledger_cursor)+1 FROM inventory_transactions),:original,:actor,now())"""),
                    dict(id=uuid4(), number=uuid4().hex, document=uuid4().hex, posting=uuid4().hex,
                         hash=uuid4().hex * 2, original=original.id, actor=original.actor_user_id))
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                try:
                    migration["upgrade"]()
                except DBAPIError as exc:
                    assert "0097 transition blocked" in str(exc.orig)
                else:
                    raise AssertionError("Migration silently accepted detached historical work-order reversal")
        finally:
            transaction.rollback()
    assert (snapshot(api_engine), catalog()) == before
    print("PG16 migration rejects detached legacy history; source, ACL, schema and data rollback PASS", flush=True)
