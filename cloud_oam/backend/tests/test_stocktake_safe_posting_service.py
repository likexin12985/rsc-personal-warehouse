from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.inventory_posting as inventory_service
import app.formal_services.stocktake_posting as posting_service
import app.formal_services.stocktake_query as query_service
from app.formal_access import load_formal_principal
from app.formal_services.stocktake_posting import (
    PostApprovedStocktakeDifferencesCommand,
    StocktakeDifferencePostingError,
    post_approved_stocktake_differences,
)
from app.foundation_models import (
    OutboxEvent,
    Permission,
    Role,
    RolePermission,
    StateTransitionEvent,
)
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    StockBalance,
)
from app.stocktake_models import (
    InventoryFreeze,
    StocktakePosting,
    StocktakePostingCompletion,
    StocktakePostingCompletionItem,
)
from test_stocktake_difference_service import db, world  # noqa: F401
from test_stocktake_review_recount_service import (
    HQ_REVIEWED_AT,
    SECRET,
    _command,
    _fixed_clocks,
    _hq,
    _prepare,
    _region,
    review_world,
)


POSTED_AT = HQ_REVIEWED_AT + timedelta(minutes=5)


@pytest.fixture
def posting_world(review_world, monkeypatch: pytest.MonkeyPatch):
    admin_role = review_world.db.scalar(select(Role).where(Role.code == "admin"))
    assert admin_role is not None
    permissions = tuple(
        Permission(
            id=uuid.uuid4(),
            resource="stocktake",
            action=action,
            field_code="",
            description=action,
        )
        for action in ("post_difference", "read")
    )
    review_world.db.add_all(permissions)
    review_world.db.flush()
    review_world.db.add_all(
        RolePermission(
            role_id=admin_role.id,
            permission_id=permission.id,
            effect="allow",
        )
        for permission in permissions
    )
    review_world.db.flush()
    review_world.principals["admin"] = load_formal_principal(
        review_world.db,
        review_world.principals["admin"].user_id,
        now=POSTED_AT,
    )
    monkeypatch.setattr(posting_service, "_database_now", lambda _db: POSTED_AT)

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260901_0035_nonopening_stocktake_safe_posting.py"
    )
    spec = importlib.util.spec_from_file_location(
        "stocktake_safe_posting_migration_0035", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = review_world.db.connection()
    migration._create_sqlite_immutability_triggers = lambda: None
    for sql in (
        migration._sqlite_approval_task_trigger_sql(),
        migration._sqlite_posting_task_trigger_sql(),
        migration._sqlite_posting_item_trigger_sql(),
    ):
        connection.exec_driver_sql(sql)
    return review_world


def _approve(posting_world, monkeypatch, *, key: str, counted_qty, decision: str):
    task, round_row = _prepare(
        posting_world,
        key=key,
        counted_qty=counted_qty,
    )
    _region(
        posting_world,
        task,
        round_row,
        _command(
            posting_world,
            task,
            round_row,
            decision="approve",
            item_decision=decision,
        ),
        key=f"{key}-region",
    )
    _hq(
        posting_world,
        task,
        round_row,
        _command(
            posting_world,
            task,
            round_row,
            decision="approve",
            item_decision=decision,
        ),
        monkeypatch,
        key=f"{key}-hq",
    )
    return task, round_row


def _post(world, task, *, key: str, version: int | None = None):
    return post_approved_stocktake_differences(
        world.db,
        actor=world.principals["admin"],
        command=PostApprovedStocktakeDifferencesCommand(
            task_id=task.id,
            expected_task_version=task.version if version is None else version,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def test_no_adjustment_posts_completion_releases_freeze_without_inventory_or_outbox(
    posting_world, monkeypatch
):
    task, _round = _approve(
        posting_world,
        monkeypatch,
        key="safe-post-no-adjustment",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    expected_version = task.version
    approved_detail = query_service.stocktake_task_detail(
        posting_world.db,
        actor=posting_world.principals["admin"],
        task_id=task.id,
        now=POSTED_AT,
    )
    assert approved_detail.allowed_actions == ("post",)
    assert approved_detail.state_axes.posting_status == "not_posted"
    before = (
        posting_world.db.scalar(select(func.count()).select_from(InventoryTransaction)),
        posting_world.db.scalar(select(func.count()).select_from(InventoryMovement)),
        posting_world.db.scalar(select(func.count()).select_from(OutboxEvent)),
    )
    result = _post(
        posting_world,
        task,
        key="safe-post-no-adjustment-idempotency",
    )

    assert result.resulting_task_status == "posted"
    assert result.task_version == expected_version + 1
    assert result.difference_count == result.no_adjustment_count == 1
    assert result.accepted_difference_count == 0
    assert result.transaction_count == result.movement_count == 0
    assert result.total_quantity == Decimal("0.000")
    assert result.first_ledger_cursor is None
    assert result.last_ledger_cursor is None
    assert task.status == "posted" and task.posted_at == POSTED_AT
    assert task.closed_at is None
    posted_detail = query_service.stocktake_task_detail(
        posting_world.db,
        actor=posting_world.principals["admin"],
        task_id=task.id,
        now=POSTED_AT,
    )
    assert "post" not in posted_detail.allowed_actions
    assert posted_detail.state_axes.posting_status == "recorded"
    assert (
        posting_world.db.scalar(select(func.count()).select_from(InventoryTransaction)),
        posting_world.db.scalar(select(func.count()).select_from(InventoryMovement)),
        posting_world.db.scalar(select(func.count()).select_from(OutboxEvent)),
    ) == before
    assert posting_world.db.scalar(
        select(func.count()).select_from(StocktakePosting)
    ) == 0
    completion = posting_world.db.get(StocktakePostingCompletion, result.completion_id)
    assert completion is not None
    item = posting_world.db.scalar(
        select(StocktakePostingCompletionItem).where(
            StocktakePostingCompletionItem.completion_id == completion.id
        )
    )
    assert item is not None
    assert item.decision == "no_adjustment"
    assert item.inventory_transaction_id is None
    assert item.inventory_movement_id is None
    freezes = tuple(
        posting_world.db.scalars(
            select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)
        ).all()
    )
    assert freezes and all(row.status == "released" for row in freezes)
    assert all(row.release_reason == posting_service.FREEZE_RELEASE_REASON for row in freezes)
    state = posting_world.db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(task.id),
            StateTransitionEvent.reason == "nonopening_stocktake_difference_posted",
        )
    )
    assert state is not None and state.to_status == "posted"

    replay = _post(
        posting_world,
        task,
        key="safe-post-no-adjustment-idempotency",
        version=expected_version,
    )
    assert replay.replayed is True
    assert replay.completion_id == result.completion_id
    assert posting_world.db.scalar(
        select(func.count()).select_from(StocktakePostingCompletion)
    ) == 1


def test_zero_difference_still_posts_immutable_completion_without_movement(
    posting_world, monkeypatch
):
    task, _round = _approve(
        posting_world,
        monkeypatch,
        key="safe-post-zero",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    result = _post(posting_world, task, key="safe-post-zero-idempotency")
    assert result.resulting_task_status == "posted"
    assert result.difference_count == 0
    assert result.accepted_difference_count == 0
    assert result.no_adjustment_count == 0
    assert result.transaction_count == result.movement_count == 0
    assert posting_world.db.scalar(
        select(func.count()).select_from(StocktakePostingCompletionItem)
    ) == 0
    assert task.closed_at is None


def test_posting_requires_exact_version_and_exact_idempotency_request(
    posting_world, monkeypatch
):
    task, _round = _approve(
        posting_world,
        monkeypatch,
        key="safe-post-guards",
        counted_qty=Decimal("4.000"),
        decision="no_adjustment",
    )
    with pytest.raises(StocktakeDifferencePostingError) as stale:
        _post(
            posting_world,
            task,
            key="safe-post-guards-stale",
            version=task.version - 1,
        )
    assert stale.value.code == "stocktake_posting_task_not_postable"

    expected_version = task.version
    _post(posting_world, task, key="safe-post-guards-idempotency")
    with pytest.raises(StocktakeDifferencePostingError) as conflict:
        _post(
            posting_world,
            task,
            key="safe-post-guards-idempotency",
            version=expected_version - 1,
        )
    assert conflict.value.code == "stocktake_posting_idempotency_conflict"


def test_posting_tail_authorization_reproof_fails_closed(posting_world, monkeypatch):
    actor = posting_world.principals["admin"]
    assignment = posting_service._current_admin_assignment(posting_world.db, actor)
    drifted = replace(
        actor,
        authorization_version=actor.authorization_version + 1,
    )
    monkeypatch.setattr(
        inventory_service,
        "_require_current_stocktake_difference_finalizer",
        lambda _db, _actor: drifted,
    )

    with pytest.raises(StocktakeDifferencePostingError) as caught:
        posting_service._reprove_posting_authorization(
            posting_world.db,
            actor=actor,
            assignment=assignment,
        )

    assert caught.value.code == "stocktake_posting_authorization_changed"
    assert caught.value.category == "precondition_failed"
    assert caught.value.http_status_code == 412


def test_accepted_loss_uses_one_union_batch_and_appends_immutable_ledger(
    posting_world, monkeypatch
):
    calls = {"reference": 0, "serial": 0}
    original_reference = inventory_service.lock_inventory_reference_graph
    original_serial = inventory_service.lock_inventory_serial_graph

    def reference_once(db, account_ids, effective_at):
        calls["reference"] += 1
        return original_reference(db, account_ids, effective_at)

    def serial_once(db, serial_ids):
        calls["serial"] += 1
        return original_serial(db, serial_ids)

    def local_established_reference_graph(
        db, *, command, current_actor_user_id
    ):
        del current_actor_user_id
        account_ids = inventory_service._command_account_ids(command)
        serial_ids = inventory_service._command_serial_ids(command)
        reference_once(db, account_ids, command.effective_at)
        serial_once(db, serial_ids)
        proof = inventory_service._issue_prelocked_inventory_graph_proof(
            db,
            command=command,
            account_ids=account_ids,
            serial_ids=serial_ids,
        )
        return (), proof, None

    # The shared unit-test world predates formal opening establishments.  This
    # test bypasses only that historical fixture prerequisite while retaining
    # the real union reference/SN/balance/audit batch and every 0035 guard.
    monkeypatch.setattr(
        inventory_service,
        "_plan_and_lock_terminal_opening_graphs",
        local_established_reference_graph,
    )
    monkeypatch.setattr(
        inventory_service,
        "_require_valid_opening_establishments",
        lambda *args, **kwargs: None,
    )

    task, _round = _approve(
        posting_world,
        monkeypatch,
        key="safe-post-loss",
        counted_qty=Decimal("4.000"),
        decision="accept_for_posting",
    )
    before_outbox = posting_world.db.scalar(
        select(func.count()).select_from(OutboxEvent)
    )
    result = _post(posting_world, task, key="safe-post-loss-idempotency")

    assert result.resulting_task_status == "posted"
    assert result.accepted_difference_count == result.movement_count == 1
    assert result.transaction_count == 1
    assert result.total_quantity == Decimal("1.000")
    assert result.first_ledger_cursor == result.last_ledger_cursor == 2
    assert calls == {"reference": 1, "serial": 1}
    transaction = posting_world.db.scalar(
        select(InventoryTransaction).where(
            InventoryTransaction.source_document_type == "stocktake_difference",
            InventoryTransaction.source_document_id == str(task.id),
        )
    )
    assert transaction is not None
    assert transaction.movement_type == "stocktake_loss"
    movement = posting_world.db.scalar(
        select(InventoryMovement).where(
            InventoryMovement.transaction_id == transaction.id
        )
    )
    assert movement is not None
    assert movement.from_account_id == posting_world.region_new.id
    assert movement.to_account_id is None
    assert movement.quantity == Decimal("1.000")
    balance = posting_world.db.get(StockBalance, posting_world.region_new.id)
    assert balance is not None
    assert balance.quantity == Decimal("4.000")
    assert balance.ledger_cursor == 2 and balance.version == 2
    assert posting_world.db.scalar(
        select(func.count()).select_from(OutboxEvent)
    ) == before_outbox
    assert task.closed_at is None
