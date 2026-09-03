from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

import app.formal_services.stocktake_close as close_service
import app.formal_services.stocktake_query as query_service
from app.formal_access import load_formal_principal
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.stocktake_close import (
    CloseReconciledStocktakeCommand,
    ReconcileStocktakeForCloseCommand,
    StocktakeCloseError,
    close_reconciled_stocktake,
    reconcile_posted_stocktake_for_close,
)
from app.foundation_models import (
    AuditEvent,
    OutboxEvent,
    Permission,
    Role,
    RolePermission,
    StateTransitionEvent,
)
from app.inventory_models import (
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
    SerialCurrentPosition,
    StockBalance,
)
from app.stocktake_models import (
    StocktakeCloseCompletion,
    StocktakeCloseReconciliationCompletion,
    StocktakeCloseReconciliationSerial,
    StocktakeCloseTransitionAck,
    StocktakeCountLine,
    StocktakeCountObservation,
)
from app.models import User
from test_stocktake_review_recount_service import (  # noqa: F401
    SECRET,
    _command,
    _fixed_clocks,
    _hq,
    _prepare,
    _region,
    review_world,
)
from test_stocktake_difference_service import db, world  # noqa: F401
from test_stocktake_safe_posting_service import (
    POSTED_AT,
    _approve,
    _post,
    posting_world,
)
from app.formal_services.stocktake_count import StocktakeSnapshotCountInput
from test_stocktake_task_service import _managed_draft


RECONCILED_AT = POSTED_AT + timedelta(minutes=5)
CLOSED_AT = RECONCILED_AT + timedelta(minutes=5)
SECOND_RECONCILED_AT = CLOSED_AT + timedelta(minutes=5)
SECOND_CLOSED_AT = SECOND_RECONCILED_AT + timedelta(minutes=5)


@pytest.fixture
def close_world(posting_world):
    # The shared fixture now seeds every mutable balance from one immutable
    # opening transaction, so closing exercises the same ledger graph as start.
    admin_role = posting_world.db.scalar(select(Role).where(Role.code == "admin"))
    assert admin_role is not None
    permissions = tuple(
        Permission(
            id=uuid.uuid4(),
            resource="stocktake",
            action=action,
            field_code="",
            description=action,
        )
        for action in ("reconcile", "close")
    )
    posting_world.db.add_all(permissions)
    posting_world.db.flush()
    posting_world.db.add_all(
        RolePermission(
            id=uuid.uuid4(),
            role_id=admin_role.id,
            permission_id=permission.id,
            effect="allow",
        )
        for permission in permissions
    )
    posting_world.db.flush()
    posting_world.principals["admin"] = load_formal_principal(
        posting_world.db,
        posting_world.principals["admin"].user_id,
        now=RECONCILED_AT,
    )
    return posting_world


def _reconcile(world, task, *, key: str, version: int | None = None):
    return reconcile_posted_stocktake_for_close(
        world.db,
        actor=world.principals["admin"],
        command=ReconcileStocktakeForCloseCommand(
            task_id=task.id,
            expected_task_version=task.version if version is None else version,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _close(world, task, *, key: str, version: int | None = None):
    return close_reconciled_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseReconciledStocktakeCommand(
            task_id=task.id,
            expected_task_version=task.version if version is None else version,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _append_coherent_scope_gain(world, *, occurred_at) -> int:
    head = world.db.scalar(
        select(InventoryLedgerHead).where(InventoryLedgerHead.stream_key == "inventory")
    )
    assert head is not None
    cursor = head.next_cursor
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"TX-CLOSE-RECON-{cursor}",
        movement_type="stocktake_gain",
        source_document_type="stocktake_close_reconciliation_test",
        source_document_id=str(cursor),
        posting_key=f"stocktake-close-reconciliation-test:{cursor}",
        idempotency_key_hash=f"{cursor + 1000:064x}",
        request_hash=f"{cursor + 2000:064x}",
        status="posted",
        effective_at=occurred_at,
        posted_at=occurred_at,
        ledger_cursor=cursor,
        reversed_transaction_id=None,
        actor_user_id=world.principals["admin"].user_id,
        created_at=occurred_at,
    )
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=None,
        to_account_id=world.region_new.id,
        external_boundary_code="TEST_EXTERNAL",
        quantity=Decimal("1.000"),
        created_at=occurred_at,
    )
    world.db.add(transaction)
    world.db.flush()
    world.db.add(movement)
    balance = world.db.get(StockBalance, world.region_new.id)
    assert balance is not None
    balance.quantity += Decimal("1.000")
    balance.ledger_cursor = cursor
    balance.version += 1
    head.next_cursor = cursor + 1
    world.db.flush()
    return cursor


def _add_verified_observation(
    world,
    *,
    task,
    round_row,
    line: StocktakeCountLine,
    custodian_person_id,
) -> None:
    account = world.region_new
    world.db.add(
        StocktakeCountObservation(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=line.scope_id,
            observation_no=1,
            owner_org_id=account.owner_org_id,
            location_id=account.location_id,
            custodian_person_id_snapshot=custodian_person_id,
            material_id=account.material_id,
            material_identifier_raw=world.material_a.sku_code,
            material_identifier_type="sku_code",
            condition_code=account.condition_code,
            availability_bucket=account.availability_bucket,
            lot_id=account.lot_id,
            lot_no_raw=None,
            serial_id=None,
            serial_no_raw=None,
            serial_identifier_type=None,
            counted_qty=Decimal("5.000"),
            verification_status="verified",
            count_method="manual",
            reason_code="test_evidence",
            remark="local fail-closed evidence",
            counted_by_user_id=world.principals["admin"].user_id,
            counted_at=POSTED_AT,
            dimension_sha256="1" * 64,
            request_sha256="2" * 64,
            idempotency_key_hash=uuid.uuid4().hex * 2,
            created_at=POSTED_AT,
        )
    )
    world.db.flush()


def _direct_plan(world, task):
    posting, approval = close_service._posting_root(world.db, task.id)
    account_ids, serial_ids = close_service._plan_coordinates(
        world.db,
        task=task,
        posting=posting,
        approval=approval,
    )
    head = world.db.scalar(
        select(InventoryLedgerHead).where(InventoryLedgerHead.stream_key == "inventory")
    )
    assert head is not None
    return close_service._build_reconciliation_plan(
        world.db,
        task=task,
        posting=posting,
        approval=approval,
        reconciliation_cursor=head.next_cursor - 1,
        expected_account_ids=account_ids,
        expected_serial_ids=serial_ids,
        require_current_projection=True,
    )


def _install_sqlite_close_guards(world) -> object:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260901_0038_nonopening_stocktake_close_reconciliation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "stocktake_close_migration_0038_service_test", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = world.db.connection()
    for table_name in migration.NEW_TABLES:
        for operation in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER trg_{table_name}_immutable_"
                f"{operation.lower()}_0038 BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, "
                "'non-opening stocktake close facts are immutable'); END"
            )
    for sql in (
        migration._sqlite_ack_insert_sql(),
        migration._sqlite_reconciliation_insert_sql(),
        migration._sqlite_account_insert_sql(),
        migration._sqlite_serial_insert_sql(),
        migration._sqlite_close_insert_sql(),
        migration._sqlite_audit_event_guard_sql(),
        migration._sqlite_state_event_guard_sql(),
        migration._sqlite_task_terminal_sql(),
        migration._sqlite_task_ack_sql(),
    ):
        connection.exec_driver_sql(sql)
    return migration


def test_reconcile_and_close_are_independent_idempotent_terminal_facts(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-basic",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-basic-post")
    posted_version = task.version
    outbox_before = close_world.db.scalar(select(func.count()).select_from(OutboxEvent))
    posted_detail = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=RECONCILED_AT,
    )
    assert posted_detail.state_axes.reconciliation_status == "not_reconciled"
    assert posted_detail.state_axes.closure_status == "open"
    assert posted_detail.close_control.latest_reconciliation is None
    assert posted_detail.allowed_actions == ("reconcile",)

    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    reconciled = _reconcile(close_world, task, key="close-basic-reconcile")
    assert reconciled.resulting_task_status == "posted"
    assert reconciled.task_version == posted_version + 1
    assert reconciled.reconciliation_no == 1
    assert reconciled.book_total_qty == reconciled.physical_total_qty
    assert task.status == "posted" and task.closed_at is None
    assert close_world.db.scalar(select(func.count()).select_from(StocktakeCloseCompletion)) == 0
    assert close_world.db.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_before
    reconciled_detail = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=RECONCILED_AT,
    )
    assert reconciled_detail.state_axes.reconciliation_status == "recorded"
    assert reconciled_detail.allowed_actions == ("reconcile", "close")
    assert reconciled_detail.close_control.latest_reconciliation is not None
    assert (
        reconciled_detail.close_control.latest_reconciliation.completion_id
        == reconciled.completion_id
    )

    replay = _reconcile(
        close_world,
        task,
        key="close-basic-reconcile",
        version=posted_version,
    )
    assert replay.replayed is True
    assert replay.completion_id == reconciled.completion_id

    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    closed = _close(close_world, task, key="close-basic-close")
    assert closed.resulting_task_status == "closed"
    assert closed.task_version == reconciled.task_version + 1
    assert closed.reconciliation_completion_id == reconciled.completion_id
    assert task.status == "closed" and task.closed_at == CLOSED_AT
    assert close_world.db.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_before
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 1
    assert close_world.db.scalar(select(func.count()).select_from(StocktakeCloseCompletion)) == 1
    assert close_world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action.in_(
                (
                    "stocktake.nonopening.close_reconciliation_recorded",
                    "stocktake.nonopening.closed",
                )
            )
        )
    ) == 2
    closed_detail = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=CLOSED_AT,
    )
    assert closed_detail.state_axes.reconciliation_status == "recorded"
    assert closed_detail.state_axes.closure_status == "closed"
    assert closed_detail.allowed_actions == ()
    assert closed_detail.close_control.close_completion is not None
    assert closed_detail.close_control.close_completion.completion_id == closed.completion_id

    close_replay = _close(
        close_world,
        task,
        key="close-basic-close",
        version=reconciled.task_version,
    )
    assert close_replay.replayed is True
    assert close_replay.completion_id == closed.completion_id


def test_terminal_read_models_reject_mixed_axes_actions_versions_and_times(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-output-closure",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-output-closure-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    _reconcile(close_world, task, key="close-output-closure-reconcile")
    posted = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=RECONCILED_AT,
    )
    posted_summary = next(
        row
        for row in query_service.list_stocktake_tasks(
            close_world.db,
            actor=close_world.principals["admin"],
            limit=100,
            now=RECONCILED_AT,
        ).items
        if row.task_id == task.id
    )

    posted_cases = []
    payload = posted.model_dump()
    payload["posted_at"] = None
    posted_cases.append(payload)
    payload = posted.model_dump()
    payload["state_axes"]["posting_status"] = "not_posted"
    posted_cases.append(payload)
    payload = posted.model_dump()
    payload["allowed_actions"] = ("post",)
    posted_cases.append(payload)
    payload = posted.model_dump()
    payload["scopes"][0]["allowed_actions"] = ("submit_initial_count",)
    posted_cases.append(payload)
    payload = posted.model_dump()
    payload["status"] = "submitted"
    payload["posted_at"] = None
    payload["state_axes"]["posting_status"] = "not_posted"
    payload["state_axes"]["reconciliation_status"] = "not_reconciled"
    payload["allowed_actions"] = ()
    posted_cases.append(payload)
    payload = posted.model_dump()
    payload["close_control"]["latest_reconciliation"]["reconciled_at"] = (
        posted.posted_at
    )
    posted_cases.append(payload)
    for payload in posted_cases:
        with pytest.raises(ValidationError):
            type(posted).model_validate(payload)

    for mutation in ("posting", "actions"):
        payload = posted_summary.model_dump()
        if mutation == "posting":
            payload["state_axes"]["posting_status"] = "not_posted"
        else:
            payload["allowed_actions"] = ("post",)
        with pytest.raises(ValidationError):
            type(posted_summary).model_validate(payload)

    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    _close(close_world, task, key="close-output-closure-close")
    closed = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=CLOSED_AT,
    )
    closed_summary = next(
        row
        for row in query_service.list_stocktake_tasks(
            close_world.db,
            actor=close_world.principals["admin"],
            limit=100,
            now=CLOSED_AT,
        ).items
        if row.task_id == task.id
    )

    closed_cases = []
    payload = closed.model_dump()
    payload["state_axes"]["reconciliation_status"] = "stale"
    closed_cases.append(payload)
    payload = closed.model_dump()
    payload["allowed_actions"] = ("reconcile",)
    closed_cases.append(payload)
    payload = closed.model_dump()
    payload["rounds"][0]["allowed_actions"] = ("generate_initial_differences",)
    closed_cases.append(payload)
    payload = closed.model_dump()
    payload["close_control"]["latest_reconciliation"][
        "reconciled_task_version"
    ] = closed.version
    closed_cases.append(payload)
    payload = closed.model_dump()
    payload["close_control"]["close_completion"]["closed_at"] = (
        closed.close_control.latest_reconciliation.reconciled_at
    )
    payload["closed_at"] = (
        closed.close_control.latest_reconciliation.reconciled_at
    )
    closed_cases.append(payload)
    for payload in closed_cases:
        with pytest.raises(ValidationError):
            type(closed).model_validate(payload)

    for mutation in ("posting", "reconciliation", "actions"):
        payload = closed_summary.model_dump()
        if mutation == "posting":
            payload["state_axes"]["posting_status"] = "not_posted"
        elif mutation == "reconciliation":
            payload["state_axes"]["reconciliation_status"] = "stale"
        else:
            payload["allowed_actions"] = ("reconcile",)
        with pytest.raises(ValidationError):
            type(closed_summary).model_validate(payload)


@pytest.mark.parametrize(
    ("key", "trace_id"),
    (
        ("a" * 15, "b" * 8),
        ("a" * 129, "b" * 8),
        ("a" * 16, "b" * 7),
        ("a" * 16, "b" * 161),
    ),
)
def test_terminal_transport_limits_match_the_fastapi_boundary(key, trace_id):
    with pytest.raises(StocktakeCloseError):
        close_service._validate_transport(key, b"s" * 32, trace_id)
    assert close_service._validate_transport("a" * 16, b"s" * 32, "b" * 8) == (
        "a" * 16,
        b"s" * 32,
        "b" * 8,
    )


def test_close_requires_independent_current_reconciliation(close_world, monkeypatch):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-before-reconcile",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-before-reconcile-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    with pytest.raises(StocktakeCloseError) as exc:
        _close(close_world, task, key="close-before-reconcile-close")
    assert exc.value.code == "stocktake_close_reconciliation_required"
    assert task.status == "posted" and task.closed_at is None


def test_new_ledger_makes_close_stale_and_new_proof_appends_before_close(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-stale-chain",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-stale-chain-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    first = _reconcile(close_world, task, key="close-stale-chain-first")

    later_cursor = _append_coherent_scope_gain(
        close_world,
        occurred_at=RECONCILED_AT + timedelta(minutes=1),
    )
    stale_detail = query_service.stocktake_task_detail(
        close_world.db,
        actor=close_world.principals["admin"],
        task_id=task.id,
        now=CLOSED_AT,
    )
    assert stale_detail.state_axes.reconciliation_status == "stale"
    assert stale_detail.allowed_actions == ("reconcile",)
    with pytest.raises(StocktakeCloseError) as stale:
        _close(close_world, task, key="close-stale-chain-close-stale")
    assert stale.value.code == "stocktake_close_reconciliation_stale"
    assert task.status == "posted" and task.version == first.task_version

    monkeypatch.setattr(
        close_service, "_database_now", lambda _db: SECOND_RECONCILED_AT
    )
    second = _reconcile(close_world, task, key="close-stale-chain-second")
    assert second.reconciliation_no == 2
    assert second.reconciliation_ledger_cursor == later_cursor
    assert second.task_version == first.task_version + 1
    proofs = tuple(
        close_world.db.scalars(
            select(StocktakeCloseReconciliationCompletion)
            .where(StocktakeCloseReconciliationCompletion.task_id == task.id)
            .order_by(StocktakeCloseReconciliationCompletion.reconciliation_no)
        ).all()
    )
    assert len(proofs) == 2
    assert proofs[1].previous_reconciliation_id == proofs[0].id == first.completion_id
    assert proofs[0].reconciliation_manifest_sha256
    assert proofs[1].reconciliation_manifest_sha256

    monkeypatch.setattr(close_service, "_database_now", lambda _db: SECOND_CLOSED_AT)
    closed = _close(close_world, task, key="close-stale-chain-close-current")
    assert closed.reconciliation_completion_id == second.completion_id
    assert task.status == "closed"


def test_reconcile_requires_one_current_hq_national_admin(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-admin-boundary",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-admin-boundary-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)

    with pytest.raises(StocktakeCloseError) as non_hq:
        reconcile_posted_stocktake_for_close(
            close_world.db,
            actor=close_world.principals["manager_x"],
            command=ReconcileStocktakeForCloseCommand(
                task_id=task.id,
                expected_task_version=task.version,
            ),
            idempotency_key="close-admin-boundary-nonhq",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-close-admin-boundary-nonhq",
        )
    assert non_hq.value.code == "stocktake_close_reconcile_forbidden"

    current_admin = close_world.principals["admin"]
    ambiguous_admin = replace(
        current_admin,
        assignments=current_admin.assignments + current_admin.assignments,
    )
    monkeypatch.setattr(
        close_service,
        "load_formal_principal",
        lambda _db, _user_id: ambiguous_admin,
    )
    with pytest.raises(StocktakeCloseError) as ambiguous:
        _reconcile(close_world, task, key="close-admin-boundary-ambiguous")
    assert ambiguous.value.code == "stocktake_close_reconcile_forbidden"
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 0


def test_reconcile_rejects_stale_authorization_version(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-auth-version",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-auth-version-post")
    user = close_world.db.get(User, close_world.principals["admin"].user_id)
    assert user is not None
    user.authorization_version += 1
    close_world.db.flush()
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)

    with pytest.raises(StocktakeCloseError) as stale:
        _reconcile(close_world, task, key="close-auth-version-reconcile")
    assert stale.value.code == "stocktake_close_actor_principal_stale"
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 0


def test_reconcile_idempotency_never_crosses_actor_or_version_and_old_version_loses(
    close_world, monkeypatch
):
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-idempotency-coordinate",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-idempotency-coordinate-post")
    posted_version = task.version
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    first = _reconcile(
        close_world,
        task,
        key="close-idempotency-coordinate-reconcile",
        version=posted_version,
    )

    with pytest.raises(StocktakeCloseError) as changed_version:
        _reconcile(
            close_world,
            task,
            key="close-idempotency-coordinate-reconcile",
            version=first.task_version,
        )
    assert changed_version.value.code == (
        "stocktake_close_reconciliation_idempotency_conflict"
    )

    with pytest.raises(StocktakeCloseError) as optimistic_loser:
        _reconcile(
            close_world,
            task,
            key="close-idempotency-coordinate-second",
            version=posted_version,
        )
    assert optimistic_loser.value.code == (
        "stocktake_close_reconciliation_version_conflict"
    )

    with pytest.raises(StocktakeCloseError) as cross_actor:
        reconcile_posted_stocktake_for_close(
            close_world.db,
            actor=close_world.principals["manager_x"],
            command=ReconcileStocktakeForCloseCommand(
                task_id=task.id,
                expected_task_version=posted_version,
            ),
            idempotency_key="close-idempotency-coordinate-reconcile",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-close-idempotency-coordinate-other",
        )
    assert cross_actor.value.code == "stocktake_close_reconcile_forbidden"
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 1


def test_serial_reconciliation_is_positive_and_close_rejects_position_tampering(
    close_world, monkeypatch
):
    _install_sqlite_close_guards(close_world)
    position = close_world.db.get(SerialCurrentPosition, close_world.serial.id)
    assert position is not None
    baseline_movement = close_world.db.get(
        InventoryMovement, position.last_movement_id
    )
    assert baseline_movement is not None
    assert (
        close_world.db.get(
            InventoryMovementSerial,
            (baseline_movement.id, close_world.serial.id),
        )
        is not None
    )
    task, round_row = _prepare(
        close_world,
        key="close-serial-proof",
        draft=_managed_draft(
            close_world,
            material_id=close_world.material_b.id,
            condition_code="new",
        ),
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=close_world.region_serial.id,
                counted_qty=Decimal("1"),
                count_method="scan",
                serial_ids=(close_world.serial.id,),
            ),
        ),
    )
    _region(
        close_world,
        task,
        round_row,
        _command(
            close_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        key="close-serial-proof-region",
    )
    _hq(
        close_world,
        task,
        round_row,
        _command(
            close_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        monkeypatch,
        key="close-serial-proof-hq",
    )
    _post(close_world, task, key="close-serial-proof-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    reconciliation = _reconcile(
        close_world,
        task,
        key="close-serial-proof-reconcile",
    )
    assert reconciliation.serial_count == 1
    proof = close_world.db.scalar(
        select(StocktakeCloseReconciliationSerial).where(
            StocktakeCloseReconciliationSerial.completion_id
            == reconciliation.completion_id
        )
    )
    position = close_world.db.get(SerialCurrentPosition, close_world.serial.id)
    assert proof is not None and position is not None
    assert proof.serial_id == close_world.serial.id
    assert proof.physical_present_at_count is True
    assert proof.physical_account_id_at_count == close_world.region_serial.id
    assert proof.expected_current_account_id == close_world.region_serial.id
    assert proof.current_position_last_movement_id == position.last_movement_id

    unrelated_movement = close_world.db.scalar(
        select(InventoryMovement).where(
            InventoryMovement.id != position.last_movement_id,
            InventoryMovement.to_account_id == close_world.region_new.id,
        )
    )
    assert unrelated_movement is not None
    position.last_movement_id = unrelated_movement.id
    close_world.db.flush()
    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    with pytest.raises(StocktakeCloseError) as tampered:
        _close(close_world, task, key="close-serial-proof-close")
    assert tampered.value.code in {
        "stocktake_close_source_invalid",
        "stocktake_close_evidence_invalid",
        "stocktake_close_reconciliation_serial_projection_mismatch",
    }
    assert task.status == "posted" and task.closed_at is None


def test_sqlite_0038_guards_bind_ack_reject_mutation_and_orphan_commit(
    close_world, monkeypatch
):
    _install_sqlite_close_guards(close_world)
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-sqlite-guard",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-sqlite-guard-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    reconciliation = _reconcile(
        close_world,
        task,
        key="close-sqlite-guard-reconcile",
    )
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseTransitionAck)
    ) == 1

    for statement in (
        "UPDATE stocktake_close_reconciliation_completions "
        "SET reconciliation_no = reconciliation_no",
        "DELETE FROM stocktake_close_reconciliation_accounts",
        "INSERT INTO stocktake_close_transition_acks "
        "(task_id, target_task_version, transition_kind, "
        "reconciliation_completion_id, close_completion_id, occurred_at, created_at) "
        "VALUES ('00000000000000000000000000000001', 1, 'reconciliation', "
        "'00000000000000000000000000000002', NULL, "
        "'2026-09-01 10:00:00', '2026-09-01 10:00:00')",
    ):
        with pytest.raises(DBAPIError):
            with close_world.db.begin_nested():
                close_world.db.execute(text(statement))

    proof = close_world.db.get(
        StocktakeCloseReconciliationCompletion,
        reconciliation.completion_id,
    )
    assert proof is not None
    close_world.db.commit()
    orphan_values = {
        column.name: getattr(proof, column.name)
        for column in proof.__table__.columns
    }
    orphan_values.update(
        id=uuid.uuid4(),
        previous_reconciliation_id=proof.id,
        reconciliation_no=proof.reconciliation_no + 1,
        expected_task_version=proof.reconciled_task_version,
        reconciled_task_version=proof.reconciled_task_version + 1,
        request_sha256="e" * 64,
        idempotency_key_hash="f" * 64,
        reconciled_at=SECOND_RECONCILED_AT,
        created_at=SECOND_RECONCILED_AT,
    )
    close_world.db.add(
        StocktakeCloseReconciliationCompletion(**orphan_values)
    )
    with pytest.raises(DBAPIError, match="FOREIGN KEY"):
        close_world.db.commit()
    close_world.db.rollback()
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 1

    task = close_world.db.get(type(task), task.id, populate_existing=True)
    assert task is not None and task.status == "posted"
    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    closed = _close(close_world, task, key="close-sqlite-guard-close")
    assert closed.resulting_task_status == "closed"
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseTransitionAck)
    ) == 2


def test_sqlite_0038_event_guards_reject_pollution_and_duplicate_coordinates(
    close_world, monkeypatch
):
    _install_sqlite_close_guards(close_world)
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-sqlite-event-guard",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-sqlite-event-guard-post")
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    reconciliation = _reconcile(
        close_world,
        task,
        key="close-sqlite-event-guard-reconcile",
    )
    reconciliation_audit = close_world.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action
            == "stocktake.nonopening.close_reconciliation_recorded",
            AuditEvent.aggregate_id == str(reconciliation.completion_id),
        )
    )
    assert reconciliation_audit is not None

    for after_jsonb in (
        {"schema": "polluted"},
        reconciliation_audit.after_jsonb,
    ):
        with pytest.raises(DBAPIError, match="invalid or duplicate"):
            with close_world.db.begin_nested():
                append_audit_event(
                    close_world.db,
                    stream_key="inventory",
                    actor_user_id=reconciliation_audit.actor_user_id,
                    action="stocktake.nonopening.close_reconciliation_recorded",
                    aggregate_type="stocktake_close_reconciliation",
                    aggregate_id=str(reconciliation.completion_id),
                    before_jsonb=reconciliation_audit.before_jsonb,
                    after_jsonb=after_jsonb,
                    request_id=(
                        "stocktake-close-reconcile-request-" + "a" * 64
                    ),
                    occurred_at=RECONCILED_AT,
                )
    with pytest.raises(DBAPIError, match="invalid or duplicate"):
        with close_world.db.begin_nested():
            append_audit_event(
                close_world.db,
                stream_key="inventory",
                actor_user_id=reconciliation_audit.actor_user_id,
                action="stocktake.nonopening.close_reconciliation_recorded",
                aggregate_type="stocktake_close_reconciliation",
                aggregate_id=reconciliation.completion_id.hex,
                before_jsonb=reconciliation_audit.before_jsonb,
                after_jsonb=reconciliation_audit.after_jsonb,
                request_id="stocktake-close-reconcile-request-" + "b" * 64,
                occurred_at=RECONCILED_AT,
            )
    for action, aggregate_type in (
        (
            "stocktake.nonopening.close_reconciliation_recorded",
            "unrelated_aggregate",
        ),
        ("unrelated.action", "stocktake_close_reconciliation"),
    ):
        with pytest.raises(DBAPIError, match="invalid or duplicate"):
            with close_world.db.begin_nested():
                append_audit_event(
                    close_world.db,
                    stream_key="inventory",
                    actor_user_id=reconciliation_audit.actor_user_id,
                    action=action,
                    aggregate_type=aggregate_type,
                    aggregate_id=str(reconciliation.completion_id),
                    before_jsonb=reconciliation_audit.before_jsonb,
                    after_jsonb=reconciliation_audit.after_jsonb,
                    request_id="stocktake-close-reconcile-request-" + "c" * 64,
                    occurred_at=RECONCILED_AT,
                )
    assert close_world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action
            == "stocktake.nonopening.close_reconciliation_recorded",
            AuditEvent.aggregate_id == str(reconciliation.completion_id),
        )
    ) == 1
    append_audit_event(
        close_world.db,
        stream_key="inventory",
        actor_user_id=reconciliation_audit.actor_user_id,
        action="unrelated.audit.observed",
        aggregate_type="unrelated_object",
        aggregate_id="unrelated-coordinate",
        before_jsonb=None,
        after_jsonb={"status": "observed"},
        request_id="unrelated-audit-request",
        occurred_at=RECONCILED_AT + timedelta(seconds=1),
    )
    close_world.db.add(
        StateTransitionEvent(
            aggregate_type="unrelated_object",
            aggregate_id="unrelated-coordinate",
            from_status="ready",
            to_status="observed",
            reason="unrelated_state_change",
            actor_id=reconciliation_audit.actor_user_id,
            idempotency_key="unrelated-state-event-key",
            occurred_at=RECONCILED_AT + timedelta(seconds=1),
            metadata_jsonb={"status": "observed"},
            created_at=RECONCILED_AT + timedelta(seconds=1),
        )
    )
    close_world.db.flush()

    monkeypatch.setattr(close_service, "_database_now", lambda _db: CLOSED_AT)
    closed = _close(
        close_world,
        task,
        key="close-sqlite-event-guard-close",
    )
    close_state = close_world.db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(task.id),
            StateTransitionEvent.reason
            == "nonopening_stocktake_closed_after_internal_reconciliation",
        )
    )
    assert close_state is not None
    for metadata_jsonb in (
        {"schema": "polluted"},
        close_state.metadata_jsonb,
    ):
        with pytest.raises(DBAPIError, match="invalid or duplicate"):
            with close_world.db.begin_nested():
                close_world.db.add(
                    StateTransitionEvent(
                        aggregate_type="stocktake_task",
                        aggregate_id=str(task.id),
                        from_status="posted",
                        to_status="closed",
                        reason=(
                            "nonopening_stocktake_closed_after_internal_"
                            "reconciliation"
                        ),
                        actor_id=close_state.actor_id,
                        idempotency_key=(
                            "stocktake-close-close-state-" + uuid.uuid4().hex * 2
                        ),
                        occurred_at=CLOSED_AT,
                        metadata_jsonb=metadata_jsonb,
                        created_at=CLOSED_AT,
                    )
                )
                close_world.db.flush()
    assert closed.resulting_task_status == "closed"
    assert close_world.db.scalar(
        select(func.count()).select_from(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(task.id),
            StateTransitionEvent.reason
            == "nonopening_stocktake_closed_after_internal_reconciliation",
        )
    ) == 1


def test_sqlite_0038_graph_recomputes_physical_account_evidence(
    close_world, monkeypatch
):
    _install_sqlite_close_guards(close_world)
    task, _round = _approve(
        close_world,
        monkeypatch,
        key="close-sqlite-physical-spoof",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-sqlite-physical-spoof-post")
    original_builder = close_service._build_reconciliation_plan

    def spoofed_builder(*args, **kwargs):
        plan = original_builder(*args, **kwargs)
        rows = list(plan.account_rows)
        target_index = next(
            index
            for index, row in enumerate(rows)
            if row.scope_id is not None
        )
        rows[target_index] = replace(
            rows[target_index],
            physical_qty_at_count=Decimal("0.000"),
            physical_delta_after_count=Decimal("5.000"),
            expected_physical_qty=Decimal("5.000"),
        )
        return replace(plan, account_rows=tuple(rows))

    monkeypatch.setattr(
        close_service,
        "_build_reconciliation_plan",
        spoofed_builder,
    )
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    with pytest.raises(StocktakeCloseError) as rejected:
        _reconcile(
            close_world,
            task,
            key="close-sqlite-physical-spoof-reconcile",
        )
    assert rejected.value.code in {
        "stocktake_close_reconciliation_concurrent_conflict",
        "stocktake_close_reconciliation_database_guard_rejected",
    }
    assert rejected.value.__cause__ is not None
    assert "transition is invalid" in str(rejected.value.__cause__)
    close_world.db.rollback()
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 0


def test_sqlite_0038_graph_recomputes_serial_physical_evidence(
    close_world, monkeypatch
):
    _install_sqlite_close_guards(close_world)
    position = close_world.db.get(SerialCurrentPosition, close_world.serial.id)
    assert position is not None
    baseline_movement = close_world.db.get(
        InventoryMovement, position.last_movement_id
    )
    assert baseline_movement is not None
    assert (
        close_world.db.get(
            InventoryMovementSerial,
            (baseline_movement.id, close_world.serial.id),
        )
        is not None
    )
    task, round_row = _prepare(
        close_world,
        key="close-sqlite-serial-spoof",
        draft=_managed_draft(
            close_world,
            material_id=close_world.material_b.id,
            condition_code="new",
        ),
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=close_world.region_serial.id,
                counted_qty=Decimal("1"),
                count_method="scan",
                serial_ids=(close_world.serial.id,),
            ),
        ),
    )
    _region(
        close_world,
        task,
        round_row,
        _command(
            close_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        key="close-sqlite-serial-spoof-region",
    )
    _hq(
        close_world,
        task,
        round_row,
        _command(
            close_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        monkeypatch,
        key="close-sqlite-serial-spoof-hq",
    )
    _post(close_world, task, key="close-sqlite-serial-spoof-post")
    original_builder = close_service._build_reconciliation_plan

    def spoofed_builder(*args, **kwargs):
        plan = original_builder(*args, **kwargs)
        assert len(plan.serial_rows) == 1
        serial = replace(
            plan.serial_rows[0],
            physical_present_at_count=False,
            physical_account_id_at_count=None,
        )
        return replace(plan, serial_rows=(serial,))

    monkeypatch.setattr(
        close_service,
        "_build_reconciliation_plan",
        spoofed_builder,
    )
    monkeypatch.setattr(close_service, "_database_now", lambda _db: RECONCILED_AT)
    with pytest.raises(StocktakeCloseError) as rejected:
        _reconcile(
            close_world,
            task,
            key="close-sqlite-serial-spoof-reconcile",
        )
    assert rejected.value.code in {
        "stocktake_close_reconciliation_concurrent_conflict",
        "stocktake_close_reconciliation_database_guard_rejected",
    }
    assert rejected.value.__cause__ is not None
    assert "reconciliation serial is invalid" in str(rejected.value.__cause__)
    close_world.db.rollback()
    assert close_world.db.scalar(
        select(func.count()).select_from(StocktakeCloseReconciliationCompletion)
    ) == 0


def test_zero_quantity_count_line_still_blocks_duplicate_observation(
    close_world, monkeypatch
):
    task, round_row = _approve(
        close_world,
        monkeypatch,
        key="close-zero-duplicate",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-zero-duplicate-post")
    line = close_world.db.scalar(
        select(StocktakeCountLine).where(StocktakeCountLine.task_id == task.id)
    )
    assert line is not None
    line.counted_qty = Decimal("0.000")
    close_world.db.flush()
    _add_verified_observation(
        close_world,
        task=task,
        round_row=round_row,
        line=line,
        custodian_person_id=close_world.region_new.custodian_person_id,
    )
    with pytest.raises(StocktakeCloseError) as duplicate:
        _direct_plan(close_world, task)
    assert duplicate.value.code == "stocktake_close_evidence_invalid"
    assert "重复" in duplicate.value.message or "同时存在" in duplicate.value.message


def test_observation_custodian_dimension_must_match_account_exactly(
    close_world, monkeypatch
):
    task, round_row = _approve(
        close_world,
        monkeypatch,
        key="close-custodian-exact",
        counted_qty=Decimal("5.000"),
        decision="accept_for_posting",
    )
    _post(close_world, task, key="close-custodian-exact-post")
    line = close_world.db.scalar(
        select(StocktakeCountLine).where(StocktakeCountLine.task_id == task.id)
    )
    assert line is not None and close_world.region_new.custodian_person_id is None
    close_world.db.delete(line)
    close_world.db.flush()
    _add_verified_observation(
        close_world,
        task=task,
        round_row=round_row,
        line=line,
        custodian_person_id=close_world.technician.person.id,
    )
    with pytest.raises(StocktakeCloseError) as mismatch:
        _direct_plan(close_world, task)
    assert mismatch.value.code == "stocktake_close_evidence_invalid"
    assert "无法唯一映射" in mismatch.value.message
