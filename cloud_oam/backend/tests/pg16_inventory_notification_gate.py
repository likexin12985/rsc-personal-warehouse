"""Notification projection under the real API role in the existing disposable gate.

No database provisioning, production connection or provider call lives here.
The parent gate must already have verified a fresh GitHub PostgreSQL 16 cluster
and committed real posting/reversal fixtures through their business services.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import String, cast, func, literal, select, text
from sqlalchemy.orm import Session

from app.formal_access import FormalAccessError, load_formal_principal
from app.foundation_models import AuditEvent, NotificationDelivery, NotificationEvent, NotificationRecipient, OutboxEvent, Role, RoleAssignment
from app.inventory_models import InventoryMovement, InventoryTransaction, StockBalance
from app.stocktake_models import FormalStocktakeTask, StocktakePostingCompletion
from app.formal_services.inventory_notifications import project_inventory_notification
from app.formal_services.notification_expansion import expand_notification_event
from app.formal_services import inventory_notifications as projection
from app.formal_services import inventory_notification_failures as failures
from app.formal_services import inventory_notification_operations as operations


def _stock_and_source_snapshot(engine):
    with Session(engine) as db:
        return (
            tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity,
                                    StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
            tuple(db.execute(select(OutboxEvent.id, OutboxEvent.status, OutboxEvent.attempts,
                                    OutboxEvent.payload_jsonb).order_by(OutboxEvent.id))),
            tuple(db.execute(select(AuditEvent.id, AuditEvent.event_hash).order_by(AuditEvent.id))),
            tuple(db.execute(select(FormalStocktakeTask.id, FormalStocktakeTask.status,
                                    FormalStocktakeTask.version, FormalStocktakeTask.closed_at)
                             .order_by(FormalStocktakeTask.id))),
        )


def assert_inventory_notification_gate(api_engine):
    with Session(api_engine) as db:
        assert db.scalar(text("SELECT current_user")) == "star_oam_api"
        assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 16
        sources = {}
        for kind in ("inventory.transaction.posted", "inventory.transaction.reversed"):
            row = db.execute(select(OutboxEvent.id, InventoryTransaction.id).join(
                InventoryTransaction, cast(InventoryTransaction.id, String) == OutboxEvent.aggregate_id,
            ).where(OutboxEvent.aggregate_type == "inventory_transaction", OutboxEvent.event_type == kind,
                    InventoryTransaction.source_document_type.in_(("work_order_material", "stock_operation_return", "stock_operation_return_outbound")))
                .order_by(InventoryTransaction.ledger_cursor.desc()).limit(1)).one_or_none()
            assert row is not None, f"real committed fixture missing for {kind}"
            sources[kind] = tuple(row)
        # The existing multi-round service chain posts a real +1 gain before
        # reconciling and closing. Require that exact nonzero completion;
        # zero-difference task completion is not an inventory source.
        stocktake = db.execute(select(OutboxEvent.id, InventoryTransaction.id).join(
            InventoryTransaction, cast(InventoryTransaction.id, String) == OutboxEvent.aggregate_id,
        ).join(FormalStocktakeTask, cast(FormalStocktakeTask.id, String) == InventoryTransaction.source_document_id)
            .join(StocktakePostingCompletion, StocktakePostingCompletion.task_id == FormalStocktakeTask.id)
            .where(OutboxEvent.aggregate_type == "inventory_transaction",
                   OutboxEvent.event_type == "inventory.transaction.stocktake_difference_posted",
                   InventoryTransaction.source_document_type == "stocktake_difference",
                   InventoryTransaction.reversed_transaction_id.is_(None),
                   FormalStocktakeTask.current_round_no == 3,
                   FormalStocktakeTask.status == "closed",
                   StocktakePostingCompletion.transaction_count == 1,
                   StocktakePostingCompletion.movement_count == 1,
                   StocktakePostingCompletion.total_quantity == Decimal("1.000"))
            .order_by(InventoryTransaction.ledger_cursor.desc()).limit(1)).one_or_none()
        assert stocktake is not None, "real multi-round nonzero stocktake Outbox missing"
        stocktake_outbox_id, stocktake_tx = stocktake
        movement = db.scalars(select(InventoryMovement).where(
            InventoryMovement.transaction_id == stocktake_tx)).one()
        assert movement.quantity == Decimal("1.000")
        assert movement.from_account_id is None and movement.to_account_id is not None
        sources["inventory.transaction.stocktake_difference_posted"] = tuple(stocktake)
    posted_id, posted_tx = sources["inventory.transaction.posted"]
    reversed_id, reversed_tx = sources["inventory.transaction.reversed"]
    before = _stock_and_source_snapshot(api_engine)

    # Rolling back the consumer cannot roll back its previously committed source.
    for outbox_id, transaction_id in ((posted_id, posted_tx), (stocktake_outbox_id, stocktake_tx)):
        with Session(api_engine) as db:
            first = project_inventory_notification(db, outbox_id=outbox_id)
            assert first is not None and first.created
            db.flush()  # Exercise real insert ACL/guards before the rollback.
            db.rollback()
        with Session(api_engine) as db:
            assert db.scalar(select(func.count()).select_from(NotificationEvent).where(
                NotificationEvent.dedup_key == f"inventory-change:{transaction_id}")) == 0
        assert _stock_and_source_snapshot(api_engine) == before

    def contender(identifier):
        with Session(api_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = project_inventory_notification(db, outbox_id=identifier)
            db.commit()
            return result

    with Session(api_engine) as owner:
        first = project_inventory_notification(owner, outbox_id=posted_id)
        assert first is not None and first.created
        with ThreadPoolExecutor(max_workers=2) as pool:
            same = pool.submit(contender, posted_id)
            different = pool.submit(contender, reversed_id)
            assert same.result(timeout=15) is None  # Do not wait on or duplicate the owner.
            independent = different.result(timeout=15)
            assert independent is not None and independent.created
        owner.commit()

    with Session(api_engine) as db:
        result = project_inventory_notification(db, outbox_id=stocktake_outbox_id)
        assert result is not None and result.created and result.transaction_id == stocktake_tx
        # This fixture is a regional warehouse account without a personal
        # custodian; its task assignee is not a substitute notification target.
        assert result.affected_person_count == result.recipient_count == 0
        db.commit()

    total_deliveries = 0
    for outbox_id, transaction_id in sources.values():
        with Session(api_engine) as db:
            replay = project_inventory_notification(db, outbox_id=outbox_id)
            assert replay is not None and not replay.created and replay.transaction_id == transaction_id
            first_expansion = expand_notification_event(db, event_id=replay.event_id)
            repeated = expand_notification_event(db, event_id=replay.event_id)
            assert repeated.created_delivery_count == 0
            recipient_count = db.scalar(select(func.count()).select_from(NotificationRecipient).where(
                NotificationRecipient.event_id == replay.event_id))
            delivery_count = db.scalar(select(func.count()).select_from(NotificationDelivery).join(
                NotificationRecipient, NotificationRecipient.id == NotificationDelivery.recipient_id,
            ).where(NotificationRecipient.event_id == replay.event_id))
            assert first_expansion.created_delivery_count == recipient_count == delivery_count
            total_deliveries += delivery_count
            db.commit()
    assert total_deliveries > 0, "personal posting/reversal fixtures must exercise delivery INSERT ACL"
    assert _stock_and_source_snapshot(api_engine) == before
    _assert_inventory_source_isolation(api_engine)
    print("PG16 inventory notifications: API role, posting/reversal/nonzero-stocktake sources, same-object ownership, "
          "independent-object progress, consumer rollback, source quarantine and expansion deduplication PASS; no provider call", flush=True)


def _assert_inventory_source_isolation(api_engine):
    """Inject a source-validation fault; all PG writes/locks remain real."""
    now = datetime.now(timezone.utc)
    with Session(api_engine) as db:
        completed = select(NotificationEvent.id).where(
            NotificationEvent.dedup_key == literal("inventory-change:") + OutboxEvent.aggregate_id,
        ).exists()
        candidates = tuple(db.scalars(select(OutboxEvent.id).where(
            OutboxEvent.aggregate_type == "inventory_transaction",
            OutboxEvent.event_type.in_(projection.INVENTORY_EVENT_TYPES),
            OutboxEvent.available_at <= now, ~completed, ~failures.blocked_source_exists(),
        ).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(3)))
        assert len(candidates) == 3, "source isolation needs three real committed inventory facts"
        original_count = db.scalar(select(func.count()).select_from(NotificationEvent))
    bad_id, good_id, next_id = candidates
    before = _stock_and_source_snapshot(api_engine)
    original_project = projection.project_inventory_notification
    original_failure = projection.record_source_failure
    ownership_checks = []

    def inject_source_failure(db, *, outbox_id, now):
        if outbox_id == bad_id:
            raise projection.InventoryNotificationError("isolated source-validation fault")
        return original_project(db, outbox_id=outbox_id, now=now)

    def contender():
        with Session(api_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = failures.try_source_lock(db, bad_id)
            db.commit()
            return result

    def after_savepoint_rollback(db, **arguments):
        # The failed source savepoint has already rolled back. Its outer
        # source ownership must survive until quarantine is committed.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(contender).result(timeout=15) is False
        ownership_checks.append(arguments["outbox_id"])
        return original_failure(db, **arguments)

    with patch.object(projection, "project_inventory_notification", inject_source_failure), \
            patch.object(projection, "record_source_failure", after_savepoint_rollback):
        with Session(api_engine) as db:
            batch = projection.project_pending_inventory_notifications(db, limit=2, now=now)
            assert len(batch.blocked) == len(batch.projected) == 1
            assert batch.blocked[0].outbox_id == bad_id
            db.rollback()
        assert _stock_and_source_snapshot(api_engine) == before
        with Session(api_engine) as db:
            assert db.scalar(select(func.count()).select_from(NotificationEvent)) == original_count
            assert failures.source_failure(db, bad_id) is None
            batch = projection.project_pending_inventory_notifications(db, limit=2, now=now)
            assert len(batch.blocked) == len(batch.projected) == 1
            assert batch.blocked[0].outbox_id == bad_id
            good_source = db.get(OutboxEvent, good_id)
            assert str(batch.projected[0].transaction_id) == good_source.aggregate_id
            db.commit()
    assert ownership_checks == [bad_id, bad_id]
    with Session(api_engine) as restarted:
        assert failures.source_failure(restarted, bad_id) is not None
        # Execute the real PostgreSQL UUID-to-hex anti-join under the API role.
        assert restarted.scalar(select(OutboxEvent.id).where(
            OutboxEvent.id == bad_id, ~failures.blocked_source_exists())) is None
        next_batch = projection.project_pending_inventory_notifications(restarted, limit=1, now=now)
        assert len(next_batch.projected) == 1 and not next_batch.blocked
        assert str(next_batch.projected[0].transaction_id) == restarted.get(OutboxEvent, next_id).aggregate_id
        restarted.commit()
        assert restarted.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == failures.BLOCKED_ACTION,
            AuditEvent.aggregate_id == bad_id.hex)) == 1
    after = _stock_and_source_snapshot(api_engine)
    assert (after[:2], after[3]) == (before[:2], before[3])
    assert set(before[2]) < set(after[2]) and len(after[2]) == len(before[2]) + 1
    _assert_inventory_source_operations(api_engine, bad_id)


def _assert_inventory_source_operations(api_engine, outbox_id):
    """Real API-role read, role-graph lock, result audit, rollback and replay."""
    with Session(api_engine) as db:
        candidates = tuple(db.scalars(select(RoleAssignment.user_id).join(Role, Role.id == RoleAssignment.role_id)
            .where(Role.code == "admin", RoleAssignment.scope_type == "national", RoleAssignment.scope_id == "*")
            .distinct().order_by(RoleAssignment.user_id)))
        actor = None
        for user_id in candidates:
            try:
                principal = load_formal_principal(db, user_id)
                if all(principal.allows(db, "notification_delivery", action) for action in ("read", "retry")):
                    actor = principal
                    break
            except FormalAccessError:
                continue
        assert actor is not None, "real current national notification operator fixture is required"
        page = operations.list_inventory_notification_sources(db, actor=actor)
        record = next(item for item in page.items if item.outbox_id == outbox_id)
        assert record.status == "blocked"
        event_count = db.scalar(select(func.count()).select_from(NotificationEvent))
        delivery_count = db.scalar(select(func.count()).select_from(NotificationDelivery))
        for bad_actor in (replace(actor, entitlements=()), replace(actor, access_mode="restricted_handover")):
            try:
                operations.list_inventory_notification_sources(db, actor=bad_actor)
            except operations.OperationsError as exc:
                assert exc.http_status_code == 403
            else:
                raise AssertionError("source operations leaked across role boundary")
    command = dict(actor=actor, outbox_id=outbox_id, expected_audit_id=record.latest_audit_id,
        expected_source_sha256=record.source_sha256, reason="Disposable PG16 exact-source recheck",
        idempotency_key=f"pg16-source-recheck-{outbox_id.hex}", request_id=f"pg16-source-trace-{outbox_id.hex}")
    before = _stock_and_source_snapshot(api_engine)

    def contender():
        with Session(api_engine) as db:
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            try:
                operations.recheck_inventory_notification_source(db, **command)
            except operations.OperationsError as exc:
                assert exc.code == "inventory_notification_source_busy"
                db.rollback()
            else:
                raise AssertionError("recheck bypassed another source owner")

    with Session(api_engine) as owner:
        assert failures.try_source_lock(owner, outbox_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contender).result(timeout=15)
        owner.rollback()
    with Session(api_engine) as db:
        result = operations.recheck_inventory_notification_source(db, **command)
        assert result.item.status == "projected" and not result.replayed
        db.flush()
        db.rollback()
    assert _stock_and_source_snapshot(api_engine) == before
    with Session(api_engine) as db:
        assert db.scalar(select(func.count()).select_from(NotificationEvent)) == event_count
        first = operations.recheck_inventory_notification_source(db, **command)
        db.commit()
    with Session(api_engine) as db:
        replay = operations.recheck_inventory_notification_source(db, **command)
        assert replay.replayed and replay.item == first.item
        assert db.scalar(select(func.count()).select_from(NotificationEvent)) == event_count + 1
        assert db.scalar(select(func.count()).select_from(NotificationDelivery)) == delivery_count
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == operations.RECHECK_ACTION, AuditEvent.aggregate_id == outbox_id.hex)) == 1
        assert failures.source_failure(db, outbox_id).id == record.failure_audit_id
        db.commit()
    after = _stock_and_source_snapshot(api_engine)
    assert (after[:2], after[3]) == (before[:2], before[3])
    assert set(before[2]) < set(after[2]) and len(after[2]) == len(before[2]) + 1
    print("PG16 source operations: national permissions, exact-source ownership, API role ACL, "
          "caller rollback, immutable failure and idempotent recheck PASS; no delivery or provider call", flush=True)
