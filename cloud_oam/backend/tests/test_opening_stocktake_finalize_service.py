from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import inspect
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

import app.formal_services.opening_stocktake_finalize as finalize_service
import app.formal_services.inventory_posting as inventory_posting_service
import app.formal_services.opening_stocktake_count as count_service
import app.formal_services.opening_stocktake_recount as recount_service
import app.formal_services.opening_stocktake_review as review_service
from app.formal_services.audit_chain import AuditChainStateError
from app.formal_access import load_formal_principal
from app.formal_services.inventory_posting import (
    InventoryMovementCommand,
    InventoryPostingCommand,
    InventoryPostingError,
    post_inventory_transaction,
)
from app.formal_services.opening_stocktake import start_opening_stocktake
from app.formal_services.opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.formal_services.opening_stocktake_finalize import (
    CloseOpeningStocktakeCommand,
    OpeningStocktakeFinalizeError,
    PostOpeningStocktakeCommand,
    close_posted_opening_stocktake,
    post_approved_opening_stocktake,
    validate_opening_finalize_evidence_for_replay,
)
from app.formal_services.opening_stocktake_review import (
    submit_opening_headquarters_review,
    submit_opening_region_review,
)
from app.formal_services.opening_stocktake_recount import (
    open_opening_stocktake_recount,
)
from app.foundation_models import (
    AuditEvent,
    ExternalObject,
    ExternalObjectVersion,
    OutboxEvent,
    Permission,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
    SyncInboxEvent,
)
from app.inventory_models import (
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockBalance,
    StockAccount,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeDifference,
    StocktakePosting,
    StocktakePostingItem,
    StocktakeRound,
)

# Reuse the real start/count/two-review integration fixture instead of
# fabricating downstream evidence that the finalize guard is meant to distrust.
from test_opening_stocktake_review_service import (  # noqa: E402
    NOW,
    _fixed_database_times,
    _prepare_submitted,
    _material,
    _review_command,
    _user_with_role,
    db,
    world as review_world,
)
from test_opening_stocktake_recount_service import (  # noqa: E402
    _command as _open_recount_command,
    _prepare_recount_required,
)


@pytest.fixture(autouse=True)
def _fixed_finalize_times(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = count()
    monkeypatch.setattr(
        finalize_service,
        "_database_now",
        lambda _db: NOW
        + timedelta(hours=3, microseconds=next(ticks) + 1),
    )


@pytest.fixture
def world(review_world: SimpleNamespace) -> SimpleNamespace:
    permission = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="post_opening",
        field_code="",
        description="正式期初过账与关闭",
    )
    posting_permission = Permission(
        id=uuid.uuid4(),
        resource="inventory_transaction",
        action="post",
        field_code="",
        description="正式库存过账",
    )
    review_world.db.add_all([permission, posting_permission])
    review_world.db.flush()
    review_world.db.add(
        RolePermission(
            role_id=review_world.roles["admin"].id,
            permission_id=permission.id,
            effect="allow",
        )
    )
    review_world.db.add(
        RolePermission(
            role_id=review_world.roles["admin"].id,
            permission_id=posting_permission.id,
            effect="allow",
        )
    )
    # The shared fixture freezes its business clock at NOW while SQLAlchemy
    # defaults use the real wall clock.  Align source/master creation metadata
    # to the business timeline so the production chronology guard can prove it.
    review_world.account.created_at = NOW
    review_world.account.updated_at = NOW
    opening_balance = review_world.db.get(StockBalance, review_world.account.id)
    assert opening_balance is not None
    # A pre-opening zero projection has no immutable transaction and therefore
    # has projection version zero.  The shared review-only fixture predates the
    # writer's full ledger/projection reproof and initializes this to one.
    opening_balance.version = 0
    review_world.control.sync_run.created_at = review_world.control.sync_run.started_at
    review_world.control.batch.created_at = review_world.control.batch.received_at
    for line in review_world.control.lines:
        version = review_world.db.get(
            ExternalObjectVersion, line.external_object_version_id
        )
        assert version is not None
        external = review_world.db.get(ExternalObject, version.external_object_id)
        inbox = review_world.db.get(SyncInboxEvent, line.sync_inbox_event_id)
        assert external is not None and inbox is not None
        external.created_at = review_world.control.sync_run.started_at
        version.created_at = version.valid_from
        inbox.created_at = inbox.source_updated_at
    review_world.db.commit()
    review_world.permissions["post_opening"] = permission
    review_world.principals["admin"] = load_formal_principal(
        review_world.db,
        review_world.admin.user.id,
        now=NOW + timedelta(hours=3),
    )
    return review_world


def _approve(
    world: SimpleNamespace,
    *,
    observations: tuple[OpeningPhysicalObservationInput, ...] | None = None,
) -> SimpleNamespace:
    prepared = _prepare_submitted(world, observations=observations)
    command = _review_command(world.db, prepared)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=f"opening-finalize-region-{uuid.uuid4().hex}",
        request_id="opening-finalize-region-request",
    )
    submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=f"opening-finalize-hq-{uuid.uuid4().hex}",
        request_id="opening-finalize-hq-request",
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "approved"
    prepared.task = task
    return prepared


def _approve_zero(world: SimpleNamespace) -> SimpleNamespace:
    started = start_opening_stocktake(
        world.db,
        actor=world.principals["manager_x"],
        command=world.command,
        idempotency_key=f"opening-finalize-zero-start-{uuid.uuid4().hex}",
        request_id="opening-finalize-zero-start-request",
    )
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    )
    assert scope is not None
    counted = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            physical_observations=(),
            zero_confirmed=False,
        ),
        idempotency_key=f"opening-finalize-zero-count-{uuid.uuid4().hex}",
        request_id="opening-finalize-zero-count-request",
    )
    assert counted.round_sealed is True
    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None
    prepared = SimpleNamespace(
        started=started,
        scope=scope,
        task=task,
        round=round_row,
    )
    command = _review_command(world.db, prepared)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=f"opening-finalize-zero-region-{uuid.uuid4().hex}",
        request_id="opening-finalize-zero-region-request",
    )
    submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=f"opening-finalize-zero-hq-{uuid.uuid4().hex}",
        request_id="opening-finalize-zero-hq-request",
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, started.task_id)
    assert task is not None and task.status == "approved"
    prepared.task = task
    return prepared


def _control_matched_observations(
    world: SimpleNamespace,
) -> tuple[OpeningPhysicalObservationInput, ...]:
    return (
        OpeningPhysicalObservationInput(
            material_identifier_raw=world.material.sku_code,
            material_identifier_type="sku_code",
            condition_code="new",
            availability_bucket="available",
            counted_qty=Decimal("5.000"),
            count_method="manual",
        ),
    )


def _post(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    key: str,
    expected_version: int | None = None,
):
    return post_approved_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=PostOpeningStocktakeCommand(
            task_id=prepared.task.id,
            expected_version=(
                prepared.task.version
                if expected_version is None
                else expected_version
            ),
        ),
        idempotency_key=key,
        request_id=f"post-request-{uuid.uuid4().hex}",
    )


def _inventory_command(
    *,
    movement_type: str,
    movement: InventoryMovementCommand,
    token: str,
) -> InventoryPostingCommand:
    return InventoryPostingCommand(
        transaction_no=f"TX-{token}",
        movement_type=movement_type,
        source_document_type="opening_finalize_projection_test",
        source_document_id=f"DOC-{token}",
        posting_key=f"inventory:projection-test:{token}",
        effective_at=NOW + timedelta(hours=4),
        movements=(movement,),
    )


class _DialectOnlySession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )

    def get_bind(self) -> object:
        return self._bind


def _postgresql_sql(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_finalize_select_only_facts_use_owner_locks_on_postgresql() -> None:
    statement = select(StockAccount).order_by(StockAccount.id)

    production_statement = finalize_service._select_only_reference_statement(
        _DialectOnlySession("postgresql"),
        statement,
    )
    local_statement = finalize_service._select_only_reference_statement(
        _DialectOnlySession("sqlite"),
        statement,
    )

    assert "FOR UPDATE" not in _postgresql_sql(production_statement)
    assert "FOR UPDATE" in _postgresql_sql(local_statement)


def test_finalize_posting_uses_the_0027_global_lock_order() -> None:
    source = inspect.getsource(finalize_service._post_approved_opening_stocktake)
    ordered_markers = (
        "_lock_opening_terminal_task_root",
        "_lock_opening_task_principal_graph",
        "lock_opening_stocktake_task_evidence",
        "_opening_start_reference_coordinates",
        "lock_opening_stocktake_start_reference",
        "_materialize_observation_accounts",
        "lock_inventory_reference_graph",
        "_reread_inventory_accounts",
        "lock_inventory_serial_graph",
        "_lock_review_disposition_resolutions",
        "_prelock_inventory_graph",
        "_post_approved_opening_transaction",
    )
    offsets = [source.index(marker) for marker in ordered_markers]

    assert offsets == sorted(offsets)
    prelock_source = inspect.getsource(finalize_service._prelock_inventory_graph)
    assert prelock_source.index("_lock_and_validate_serials") < prelock_source.index(
        "_lock_or_create_balances"
    )
    generic_source = inspect.getsource(
        inventory_posting_service._post_new_transaction
    )
    assert generic_source.index("lock_inventory_reference_graph") < generic_source.index(
        "_lock_and_validate_serials"
    )
    assert generic_source.index("_lock_and_validate_serials") < generic_source.index(
        "_lock_or_create_balances"
    )
    start_call = source[
        source.index("lock_opening_stocktake_start_reference") :
        source.index("# This first pass")
    ]
    assert "for owner_org_id, _location_id in reference_plan.scope_pairs" in start_call
    assert "for _owner_org_id, location_id in reference_plan.scope_pairs" in start_call
    resolution_source = inspect.getsource(
        finalize_service._lock_review_disposition_resolutions
    )
    assert "_lock_review_reference_graph" not in resolution_source
    assert "lock_opening_stocktake_start_reference" not in resolution_source
    assert "lock_inventory_reference_graph" not in resolution_source
    assert "lock_inventory_serial_graph" not in resolution_source


def test_finalize_post_calls_each_owner_helper_once_in_canonical_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    events: list[str] = []
    principal_calls: list[tuple[str, ...]] = []
    real_principal = inventory_posting_service.lock_formal_principal_graph
    real_task = finalize_service.lock_opening_stocktake_task_evidence
    real_start = finalize_service.lock_opening_stocktake_start_reference
    real_inventory = finalize_service.lock_inventory_reference_graph
    real_serial = finalize_service.lock_inventory_serial_graph

    def principal_helper(_db, user_ids):
        checked = tuple(sorted(set(user_ids)))
        principal_calls.append(checked)
        events.append("principal")
        return real_principal(_db, user_ids)

    def task_helper(*args, **kwargs):
        events.append("task")
        return real_task(*args, **kwargs)

    def start_helper(*args, **kwargs):
        events.append("start")
        return real_start(*args, **kwargs)

    def inventory_helper(*args, **kwargs):
        events.append("inventory")
        return real_inventory(*args, **kwargs)

    def serial_helper(*args, **kwargs):
        events.append("serial")
        return real_serial(*args, **kwargs)

    monkeypatch.setattr(
        inventory_posting_service,
        "lock_formal_principal_graph",
        principal_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_opening_stocktake_task_evidence",
        task_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_opening_stocktake_start_reference",
        start_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_inventory_reference_graph",
        inventory_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_inventory_serial_graph",
        serial_helper,
    )

    def unexpected_helper(*_args, **_kwargs):
        pytest.fail("low-level posting re-entered an owner helper")

    monkeypatch.setattr(
        inventory_posting_service,
        "lock_opening_stocktake_task_evidence",
        unexpected_helper,
    )
    monkeypatch.setattr(
        inventory_posting_service,
        "lock_inventory_reference_graph",
        unexpected_helper,
    )
    monkeypatch.setattr(
        inventory_posting_service,
        "lock_inventory_serial_graph",
        unexpected_helper,
    )

    _post(
        world,
        prepared,
        key="opening-finalize-owner-helper-order-0001",
        expected_version=prepared.task.version,
    )

    assert events == ["principal", "task", "start", "inventory", "serial"]
    assert len(principal_calls) == 1
    assert {
        world.manager_x.user.id,
        world.admin.user.id,
    }.issubset(principal_calls[0])


def test_finalize_reference_coordinates_keep_exact_scope_pairs(
    world: SimpleNamespace,
) -> None:
    started = start_opening_stocktake(
        world.db,
        actor=world.principals["manager_x"],
        command=world.command,
        idempotency_key=f"opening-finalize-coordinate-{uuid.uuid4().hex}",
        request_id="opening-finalize-coordinate-request",
    )
    task = world.db.get(FormalStocktakeTask, started.task_id)
    assert task is not None

    plan = finalize_service._opening_start_reference_coordinates(
        world.db,
        task=task,
        round_id=started.initial_round_id,
    )

    assert plan.scope_pairs == ((world.region_x.id, world.location.id),)
    assert world.material.id in plan.material_ids
    assert plan.serial_ids == ()
    masters = dict(plan.master_signatures)
    assert tuple(masters) == (
        "organizations",
        "stock_locations",
        "custody_assignments",
        "stock_accounts",
        "materials",
        "material_inventory_policies",
        "inventory_lots",
        "material_qr_codes",
        "inventory_serials",
        "serial_current_positions",
        "serial_qr_codes",
    )
    assert masters["organizations"]
    assert masters["stock_locations"]
    assert masters["stock_accounts"]
    assert masters["materials"]
    signature_source = inspect.getsource(
        finalize_service._capture_opening_reference_master_signatures
    )
    assert "tuple(model.__table__.columns)" in signature_source
    assert "exact multi-task 0028 owner union" in signature_source


def test_finalize_prelocks_disposition_assignment_and_replays_plain_after_audit(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    historical_assignment = world.db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == world.manager_x.user.id)
        .order_by(RoleAssignment.id)
    )
    assert historical_assignment is not None

    principal_calls: list[tuple[str, ...]] = []
    prelocked_assignment_ids: set[uuid.UUID] = set()
    post_audit_assignment_ids: list[uuid.UUID] = []
    audit_phase = False
    real_principal = inventory_posting_service.lock_formal_principal_graph
    real_scalar = Session.scalar
    real_validate = finalize_service._validate_locked_approved_evidence

    def principal_helper(_db: Session, user_ids) -> None:
        checked = tuple(sorted(set(user_ids)))
        principal_calls.append(checked)
        real_principal(_db, checked)
        prelocked_assignment_ids.update(
            _db.scalars(
                select(RoleAssignment.id).where(
                    RoleAssignment.user_id.in_(checked)
                )
            ).all()
        )

    def begin_post_audit_replay(*args, **kwargs):
        nonlocal audit_phase
        audit_phase = True
        return real_validate(*args, **kwargs)

    def tracked_scalar(self, statement, *args, **kwargs):
        result = real_scalar(self, statement, *args, **kwargs)
        entities = {
            description.get("entity")
            for description in getattr(statement, "column_descriptions", ())
        }
        if (
            self is world.db
            and audit_phase
            and RoleAssignment in entities
            and getattr(statement, "_for_update_arg", None) is not None
            and isinstance(result, RoleAssignment)
        ):
            post_audit_assignment_ids.append(result.id)
            assert result.id in prelocked_assignment_ids
        return result

    monkeypatch.setattr(
        inventory_posting_service,
        "lock_formal_principal_graph",
        principal_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "_validate_locked_approved_evidence",
        begin_post_audit_replay,
    )
    monkeypatch.setattr(Session, "scalar", tracked_scalar)

    _post(
        world,
        prepared,
        key="opening-finalize-disposition-principal-lock-0001",
    )
    assert len(principal_calls) == 1
    assert world.manager_x.user.id in principal_calls[0]
    assert historical_assignment.id in prelocked_assignment_ids
    assert post_audit_assignment_ids == []
    from app.formal_services import opening_observation_disposition

    principal_source = inspect.getsource(
        opening_observation_disposition._task_principal_user_ids
    )
    assert "StocktakeObservationDisposition.decided_by_user_id" in principal_source


def test_finalize_idempotency_and_close_reproof_keep_read_only_facts_plain() -> None:
    post_source = inspect.getsource(finalize_service._post_approved_opening_stocktake)
    close_source = inspect.getsource(finalize_service._close_posted_opening_stocktake)
    evidence_source = inspect.getsource(
        finalize_service._lock_and_validate_approved_evidence
    )

    assert "_select_only_reference_statement" in post_source
    assert "_select_only_reference_statement" in close_source
    assert "_select_only_reference_statement" in evidence_source
    assert ".with_for_update(of=InventoryFreeze)" in evidence_source
    assert ".with_for_update(of=StocktakeRound)" in evidence_source
    replay_plan_source = inspect.getsource(
        inventory_posting_service._plan_opening_task_audit_replay
    )
    assert "_plan_opening_count_replay_evidence" in replay_plan_source
    assert (
        "_plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph"
        in replay_plan_source
    )
    assert replay_plan_source.count(
        "_plan_opening_review_evidence_from_prelocked_reference_graph"
    ) >= 2
    assert "validate_opening_finalize_evidence_for_replay" not in close_source
    ordered_close_markers = (
        "_lock_opening_terminal_task_root",
        "_lock_opening_task_principal_graph",
        "_lock_opening_terminal_task_graph_for_task",
        "_lock_opening_control_reconciliation_graph_for_task",
        "_lock_audit_chain_head_with_proof",
        "_validate_opening_terminal_from_prelocked_graph",
        "_require_approved_opening_control_reconciliation_from_prelocked_graph",
        "_validate_close_side_effects",
    )
    close_offsets = [close_source.index(marker) for marker in ordered_close_markers]
    assert close_offsets == sorted(close_offsets)


def test_public_finalize_replay_signature_has_no_reconciliation_bypass(
    world: SimpleNamespace,
) -> None:
    replay_signature = inspect.signature(
        validate_opening_finalize_evidence_for_replay
    )
    assert tuple(replay_signature.parameters) == (
        "db",
        "task_id",
        "require_closed",
    )
    assert replay_signature.parameters["task_id"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    assert replay_signature.parameters["require_closed"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    terminal_signature = inspect.signature(
        finalize_service.validate_opening_terminal_effects_for_replay
    )
    assert tuple(terminal_signature.parameters) == (
        "db",
        "task_id",
        "require_closed",
    )
    terminal_source = inspect.getsource(
        finalize_service.validate_opening_terminal_effects_for_replay
    )
    assert "reprove_reconciliation" not in terminal_source

    with pytest.raises(TypeError, match="reprove_reconciliation"):
        validate_opening_finalize_evidence_for_replay(
            world.db,
            task_id=uuid.uuid4(),
            reprove_reconciliation=False,
        )


def test_closed_finalize_replay_always_reproves_and_rejects_pending_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid.uuid4()
    db = object()
    principal_graph = object()
    reconciliation_batch = object()
    audit_proof = object()
    calls: list[str] = []

    monkeypatch.setattr(
        finalize_service,
        "_lock_opening_terminal_task_batch_root",
        lambda _db, *, task_ids: (
            calls.append("root")
            or SimpleNamespace(
                tasks=(SimpleNamespace(id=task_ids[0], status="closed"),)
            )
        ),
    )

    def historical_users(_db, *, task_ids):
        assert task_ids == (task_id,)
        calls.append("reconciliation-users")
        return ("historical-user",)

    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_opening_control_reconciliation_historical_user_ids",
        historical_users,
    )

    def lock_terminal(_db, *, root, supplied_user_ids):
        assert root.tasks[0].id == task_id
        assert supplied_user_ids == ("historical-user",)
        calls.append("terminal")
        return SimpleNamespace(principal_graph=principal_graph)

    monkeypatch.setattr(
        finalize_service,
        "_lock_opening_terminal_task_batch_graph",
        lock_terminal,
    )

    def lock_reconciliation(_db, *, task_ids, principal_graph: object):
        assert task_ids == (task_id,)
        assert principal_graph is principal_graph_for_assertion
        calls.append("reconciliation")
        return reconciliation_batch

    principal_graph_for_assertion = principal_graph
    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_lock_opening_control_reconciliation_batch_graph",
        lock_reconciliation,
    )
    monkeypatch.setattr(
        finalize_service,
        "_lock_audit_chain_head_with_proof",
        lambda _db, *, stream_key: (
            calls.append("audit") or object(),
            audit_proof,
        ),
    )

    def validate_terminal(
        _db,
        *,
        proof,
        audit_proof: object,
        require_closed_task_ids,
    ) -> None:
        assert proof.principal_graph is principal_graph_for_assertion
        assert audit_proof is audit_proof_for_assertion
        assert require_closed_task_ids == (task_id,)
        calls.append("terminal-pure")

    audit_proof_for_assertion = audit_proof
    monkeypatch.setattr(
        finalize_service,
        "_validate_opening_terminal_batch_from_prelocked_graph",
        validate_terminal,
    )

    def pending_statuses(_db, *, proof, audit_proof):
        assert proof is reconciliation_batch
        assert audit_proof is audit_proof_for_assertion
        calls.append("reconciliation-pure")
        return {task_id: SimpleNamespace(status="pending")}

    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_opening_control_reconciliation_statuses_from_prelocked_batch",
        pending_statuses,
    )

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        validate_opening_finalize_evidence_for_replay(
            db,
            task_id=task_id,
        )
    assert captured.value.code == "opening_close_reconciliation_pending"
    assert captured.value.http_status_code == 412
    assert calls == [
        "root",
        "reconciliation-users",
        "terminal",
        "reconciliation",
        "audit",
        "terminal-pure",
        "reconciliation-pure",
    ]


def test_positive_opening_posts_once_but_pending_control_blocks_close(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(world)
    approved_version = prepared.task.version
    post_key = "opening-finalize-positive-0001"
    result = _post(
        world,
        prepared,
        key=post_key,
        expected_version=approved_version,
    )

    assert result.resulting_task_status == "posted"
    assert result.total_quantity == Decimal("2.000")
    assert result.inventory_transaction_id is not None
    assert result.established_scope_count == 1
    assert result.pending_control_difference_count == 1
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted" and task.closed_at is None
    posting = world.db.get(StocktakePosting, result.posting_id)
    transaction = world.db.get(InventoryTransaction, result.inventory_transaction_id)
    balance = world.db.get(StockBalance, world.account.id)
    assert posting is not None and transaction is not None and balance is not None
    assert transaction.movement_type == "opening"
    assert balance.quantity == Decimal("2.000")
    assert world.db.scalar(
        select(func.count())
        .select_from(InventoryMovement)
        .where(InventoryMovement.transaction_id == transaction.id)
    ) == 1
    posting_items = world.db.scalars(
        select(StocktakePostingItem).where(
            StocktakePostingItem.posting_id == posting.id
        )
    ).all()
    assert all(row.difference_id is None for row in posting_items)
    establishment = world.db.scalar(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.posting_id == posting.id
        )
    )
    assert establishment is not None
    assert establishment.has_pending_control_difference is True
    control_differences = world.db.scalars(
        select(StocktakeDifference).where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.difference_type == "control_unassigned",
        )
    ).all()
    assert len(control_differences) == 1
    assert world.db.scalar(
        select(func.count())
        .select_from(StocktakePostingItem)
        .where(StocktakePostingItem.posting_id == posting.id)
    ) == 1
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)
    )
    assert freeze is not None and freeze.status == "released"

    posted_version = task.version
    close_key = "opening-finalize-close-positive-0001"
    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=posted_version,
            ),
            idempotency_key=close_key,
            request_id="opening-finalize-close-request-0001",
        )
    assert captured.value.code == "opening_close_reconciliation_pending"
    assert world.db.get(FormalStocktakeTask, task.id).status == "posted"
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("2.000")

    fact_counts = {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in (
            InventoryTransaction,
            InventoryMovement,
            StocktakePosting,
            StocktakePostingItem,
            InventoryOpeningEstablishment,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }
    post_replay = _post(
        world,
        prepared,
        key=post_key,
        expected_version=approved_version,
    )
    assert post_replay.replayed is True
    assert post_replay.resulting_task_status == "posted"
    assert post_replay.task_version == result.task_version
    assert {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in fact_counts
    } == fact_counts


def test_approved_control_reconciliation_allows_close_without_rewriting_history(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    posted = _post(
        world,
        prepared,
        key="opening-finalize-reconciled-post-0001",
        expected_version=prepared.task.version,
    )
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted"
    establishments = tuple(
        world.db.scalars(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.task_id == task.id
            )
        ).all()
    )
    difference_ids = tuple(
        world.db.scalars(
            select(StocktakeDifference.id).where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.difference_type == "control_unassigned",
            )
        ).all()
    )
    assert establishments and all(
        row.has_pending_control_difference for row in establishments
    )
    assert len(difference_ids) == 1

    run_id = uuid.uuid4()
    approved_at = NOW + timedelta(hours=2)
    calls: list[tuple[str, uuid.UUID]] = []
    graph_proof = object()

    def lock_graph(_db, *, task_id, principal_graph):
        assert principal_graph is not None
        calls.append(("lock", task_id))
        return graph_proof

    def require_approved(_db, *, task_id, proof, audit_proof):
        assert proof is graph_proof
        assert audit_proof is not None
        calls.append(("pure", task_id))
        proof_type = (
            finalize_service.reconciliation_service.OpeningControlReconciliationProof
        )
        return proof_type(
            status="approved",
            reconciliation_run_id=run_id,
            task_id=task_id,
            pending_control_difference_count=0,
            approved_at=approved_at,
        )

    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        lock_graph,
    )
    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_require_approved_opening_control_reconciliation_from_prelocked_graph",
        require_approved,
    )
    closed = close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=task.version,
        ),
        idempotency_key="opening-finalize-reconciled-close-0001",
        request_id="opening-finalize-reconciled-close-request",
    )
    assert closed.resulting_task_status == "closed"
    # The reconciliation graph is proved and pinned before the ledger/audit
    # heads.  Final closed-state replay reuses that proof and must not acquire
    # the same read-only reference locks in the inverse order.
    assert calls == [("lock", task.id), ("pure", task.id)]
    closed_task = world.db.get(FormalStocktakeTask, task.id)
    assert closed_task is not None and closed_task.closed_at is not None
    assert finalize_service._as_utc(closed_task.closed_at) >= approved_at
    assert tuple(
        world.db.scalars(
            select(StocktakeDifference.id).where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.difference_type == "control_unassigned",
            )
        ).all()
    ) == difference_ids
    assert all(
        row.has_pending_control_difference
        for row in world.db.scalars(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.task_id == task.id
            )
        ).all()
    )
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("2.000")
    assert closed.inventory_transaction_id == posted.inventory_transaction_id

    def reject_pending(_db, *, proof, audit_proof):
        del proof, audit_proof
        raise finalize_service.reconciliation_service.OpeningControlReconciliationError(
            "opening_reconciliation_pending",
            "precondition_failed",
            "pending reconciliation",
        )

    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_lock_opening_control_reconciliation_batch_graph",
        lambda _db, *, task_ids, principal_graph: graph_proof,
    )
    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_opening_control_reconciliation_statuses_from_prelocked_batch",
        reject_pending,
    )
    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        validate_opening_finalize_evidence_for_replay(
            world.db,
            task_id=task.id,
        )
    assert captured.value.code == "opening_close_reconciliation_pending"
    assert captured.value.http_status_code == 412


def test_close_maps_damaged_reconciliation_proof_to_service_unavailable(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    _post(
        world,
        prepared,
        key="opening-finalize-damaged-reconciliation-post-0001",
        expected_version=prepared.task.version,
    )
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted"

    def reject_damaged(_db, *, task_id, proof, audit_proof):
        del task_id, proof, audit_proof
        raise finalize_service.reconciliation_service.OpeningControlReconciliationError(
            "opening_reconciliation_evidence_invalid",
            "service_unavailable",
            "damaged reconciliation proof",
        )

    monkeypatch.setattr(
        finalize_service.reconciliation_service,
        "_require_approved_opening_control_reconciliation_from_prelocked_graph",
        reject_damaged,
    )
    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=task.version,
            ),
            idempotency_key="opening-finalize-damaged-reconciliation-close-0001",
            request_id="opening-finalize-damaged-reconciliation-close-request",
        )
    assert captured.value.code == "opening_close_reconciliation_evidence_invalid"
    assert captured.value.http_status_code == 503
    assert world.db.get(FormalStocktakeTask, task.id).status == "posted"


def test_post_aggregate_overflow_fails_before_ledger_and_establishment(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=None,
        location_id=world.location.id,
        material_id=world.material.id,
        condition_code="used",
        availability_bucket="available",
        lot_id=None,
        created_at=NOW,
        updated_at=NOW,
    )
    world.db.add(used_account)
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=used_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=0,
        )
    )
    world.db.commit()

    # Build a committed legacy approval graph whose individual quantities are
    # legal but whose aggregate predates the new Numeric(18,3) guard.  The
    # finalize service must independently distrust and reject that evidence.
    monkeypatch.setattr(
        count_service,
        "_require_aggregate_quantity",
        lambda value, **_kwargs: value,
    )
    prepared = _approve(
        world,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("600000000000000.000"),
                count_method="manual",
            ),
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="used",
                availability_bucket="available",
                counted_qty=Decimal("600000000000000.000"),
                count_method="manual",
            ),
        ),
    )
    approved_version = prepared.task.version
    fact_counts = {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in (
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            InventoryMovementSerial,
            SerialCurrentPosition,
            StocktakePosting,
            StocktakePostingItem,
            InventoryOpeningEstablishment,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }
    ledger_head = world.db.scalar(select(InventoryLedgerHead))
    assert ledger_head is not None
    next_cursor = ledger_head.next_cursor

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        _post(
            world,
            prepared,
            key="opening-finalize-aggregate-overflow-0001",
            expected_version=approved_version,
        )
    assert captured.value.code == "opening_finalize_aggregate_quantity_invalid"
    world.db.rollback()
    world.db.expire_all()

    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None
    assert task.status == "approved"
    assert task.version == approved_version
    assert {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in fact_counts
    } == fact_counts
    ledger_head = world.db.scalar(select(InventoryLedgerHead))
    assert ledger_head is not None and ledger_head.next_cursor == next_cursor
    balances = world.db.scalars(select(StockBalance)).all()
    assert balances
    assert all(
        row.quantity == Decimal("0.000")
        and row.ledger_cursor == 0
        and row.version == 0
        for row in balances
    )


def test_control_matched_opening_closes_and_replays_independently(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(
        world,
        observations=_control_matched_observations(world),
    )
    posted = _post(
        world,
        prepared,
        key="opening-finalize-matched-post-0001",
        expected_version=prepared.task.version,
    )
    assert posted.pending_control_difference_count == 0
    assert posted.total_quantity == Decimal("5.000")
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted"
    posted_version = task.version
    close_key = "opening-finalize-matched-close-0001"
    closed = close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=posted_version,
        ),
        idempotency_key=close_key,
        request_id="opening-finalize-matched-close-request",
    )
    assert closed.resulting_task_status == "closed"
    assert closed.posting_id == posted.posting_id
    assert closed.inventory_transaction_id == posted.inventory_transaction_id
    replay = close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=posted_version,
        ),
        idempotency_key=close_key,
        request_id="opening-finalize-matched-close-replay",
    )
    assert replay.replayed is True
    assert replay.task_version == closed.task_version
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("5.000")


def _exercise_recount_two_serial_observations_share_one_account(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    *,
    verify_tamper: bool,
) -> SimpleNamespace:
    task, source_round, scope = _prepare_recount_required(world)
    monkeypatch.setattr(
        recount_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=3),
    )
    opened = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            task,
            source_round,
            scope,
            world.manager_x.user.id,
        ),
        idempotency_key="opening-finalize-observation-recount-open",
        request_id="opening-finalize-observation-recount-open-request",
    )
    round_two = world.db.get(StocktakeRound, opened.next_round_id)
    assert round_two is not None and round_two.round_no == 2

    new_material = _material(world.db, world.source)
    new_material.sku_code = "SKU-ROUND2-NO-CUTOFF-ACCOUNT"
    policy = world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == new_material.id
        )
    )
    assert policy is not None
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    first_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=new_material.id,
        serial_no="SN-ROUND2-NO-ACCOUNT-001",
        qr_code="QR-ROUND2-NO-ACCOUNT-001",
        lot_id=None,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    second_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=new_material.id,
        serial_no="SN-ROUND2-NO-ACCOUNT-002",
        qr_code="QR-ROUND2-NO-ACCOUNT-002",
        lot_id=None,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    tamper_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=new_material.id,
        serial_no="SN-ROUND2-NO-ACCOUNT-TAMPER",
        qr_code="QR-ROUND2-NO-ACCOUNT-TAMPER",
        lot_id=None,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    world.db.add_all([first_serial, second_serial, tamper_serial])
    world.db.flush()
    monkeypatch.setattr(
        count_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=4),
    )
    counted = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=task.id,
            round_id=round_two.id,
            scope_id=scope.id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=new_material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    serial_no_raw=first_serial.serial_no,
                    serial_identifier_type="serial_no",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                    remark="复盘确认的新维度 SN 1",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=new_material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    serial_no_raw=second_serial.serial_no,
                    serial_identifier_type="serial_no",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                    remark="复盘确认的新维度 SN 2",
                ),
            ),
            zero_confirmed=False,
        ),
        idempotency_key="opening-finalize-observation-round2-count",
        request_id="opening-finalize-observation-round2-count-request",
    )
    assert counted.round_sealed is True
    assert world.db.scalar(
        select(func.count()).select_from(StockAccount).where(
            StockAccount.material_id == new_material.id,
            StockAccount.location_id == scope.location_id,
        )
    ) == 0

    task = world.db.get(FormalStocktakeTask, task.id)
    round_two = world.db.get(StocktakeRound, round_two.id)
    assert task is not None and round_two is not None
    prepared = SimpleNamespace(task=task, round=round_two, scope=scope)
    review_ticks = count()
    monkeypatch.setattr(
        review_service,
        "_database_now",
        lambda _db: NOW
        + timedelta(hours=5, microseconds=next(review_ticks)),
    )
    command = _review_command(world.db, prepared, decision="approve")
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-finalize-observation-round2-region",
        request_id="opening-finalize-observation-round2-region-request",
    )
    submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key="opening-finalize-observation-round2-hq",
        request_id="opening-finalize-observation-round2-hq-request",
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, task.id)
    assert task is not None and task.status == "approved"
    assert world.db.scalar(
        select(func.count()).select_from(StockAccount).where(
            StockAccount.material_id == new_material.id,
            StockAccount.location_id == scope.location_id,
        )
    ) == 0

    finalize_ticks = count()
    monkeypatch.setattr(
        finalize_service,
        "_database_now",
        lambda _db: NOW
        + timedelta(hours=6, microseconds=next(finalize_ticks) + 1),
    )
    result = _post(
        world,
        SimpleNamespace(task=task),
        key="opening-finalize-observation-round2-post",
    )
    assert result.total_quantity == Decimal("2.000")
    account = world.db.scalar(
        select(StockAccount).where(
            StockAccount.material_id == new_material.id,
            StockAccount.location_id == scope.location_id,
            StockAccount.condition_code == "new",
            StockAccount.availability_bucket == "available",
        )
    )
    assert account is not None
    assert world.db.scalar(
        select(func.count()).select_from(StockAccount).where(
            StockAccount.material_id == new_material.id,
            StockAccount.location_id == scope.location_id,
            StockAccount.condition_code == "new",
            StockAccount.availability_bucket == "available",
        )
    ) == 1
    balance = world.db.get(StockBalance, account.id)
    assert balance is not None
    assert balance.quantity == Decimal("2.000")
    assert balance.version == 1
    posting_items = world.db.scalars(
        select(StocktakePostingItem).where(
            StocktakePostingItem.posting_id == result.posting_id
        )
    ).all()
    assert len(posting_items) == 2
    assert all(row.count_line_id is None for row in posting_items)
    assert all(row.difference_id is not None for row in posting_items)
    assert len({row.inventory_movement_id for row in posting_items}) == 2
    transaction = world.db.get(
        InventoryTransaction, result.inventory_transaction_id
    )
    assert transaction is not None and transaction.movement_type == "opening"
    positions = {
        serial_id: world.db.get(SerialCurrentPosition, serial_id)
        for serial_id in (first_serial.id, second_serial.id)
    }
    assert all(row is not None for row in positions.values())
    assert {
        row.stock_account_id for row in positions.values() if row is not None
    } == {account.id}
    assert len(
        {row.last_movement_id for row in positions.values() if row is not None}
    ) == 2
    validate_opening_finalize_evidence_for_replay(world.db, task_id=task.id)
    world.db.commit()

    context = SimpleNamespace(
        task_id=task.id,
        posting_id=result.posting_id,
        transaction_id=transaction.id,
        account_id=account.id,
        first_serial_id=first_serial.id,
        second_serial_id=second_serial.id,
        tamper_serial_id=tamper_serial.id,
    )
    if not verify_tamper:
        return context

    first_movement_serial = world.db.scalar(
        select(InventoryMovementSerial).where(
            InventoryMovementSerial.transaction_id == transaction.id,
            InventoryMovementSerial.serial_id == first_serial.id,
        )
    )
    assert first_movement_serial is not None
    first_movement_serial.serial_id = tamper_serial.id
    world.db.flush()
    with pytest.raises(InventoryPostingError) as wrong_serial:
        validate_opening_finalize_evidence_for_replay(world.db, task_id=task.id)
    assert wrong_serial.value.code == "inventory_opening_establishment_invalid"
    world.db.rollback()

    victim_item = world.db.scalar(
        select(StocktakePostingItem)
        .where(StocktakePostingItem.posting_id == result.posting_id)
        .order_by(StocktakePostingItem.inventory_movement_id)
    )
    assert victim_item is not None
    victim_movement = world.db.get(
        InventoryMovement, victim_item.inventory_movement_id
    )
    assert victim_movement is not None
    victim_serial_rows = world.db.scalars(
        select(InventoryMovementSerial).where(
            InventoryMovementSerial.movement_id == victim_movement.id
        )
    ).all()
    victim_positions = world.db.scalars(
        select(SerialCurrentPosition).where(
            SerialCurrentPosition.last_movement_id == victim_movement.id
        )
    ).all()
    for row in [*victim_serial_rows, *victim_positions, victim_item]:
        world.db.delete(row)
    world.db.flush()
    world.db.delete(victim_movement)
    world.db.flush()
    with pytest.raises(InventoryPostingError) as missing_movement:
        validate_opening_finalize_evidence_for_replay(world.db, task_id=task.id)
    assert missing_movement.value.code == "inventory_opening_establishment_invalid"
    world.db.rollback()
    return context


def test_recount_two_serial_observations_share_one_account_and_replay_exactly(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _exercise_recount_two_serial_observations_share_one_account(
        world,
        monkeypatch,
        verify_tamper=True,
    )


def test_zero_opening_establishes_scope_without_inventory_transaction(
    world: SimpleNamespace,
) -> None:
    prepared = _approve_zero(world)
    head = world.db.get(InventoryLedgerHead, finalize_service.posting_service.INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    next_cursor_before = head.next_cursor

    result = _post(
        world,
        prepared,
        key="opening-finalize-zero-post-0001",
        expected_version=prepared.task.version,
    )

    assert result.total_quantity == Decimal("0.000")
    assert result.inventory_transaction_id is None
    assert result.established_scope_count == 1
    posting = world.db.get(StocktakePosting, result.posting_id)
    assert posting is not None and posting.inventory_transaction_id is None
    assert world.db.scalar(
        select(func.count())
        .select_from(InventoryTransaction)
        .where(
            InventoryTransaction.source_document_type == "opening_stocktake",
            InventoryTransaction.source_document_id == str(prepared.task.id),
        )
    ) == 0
    assert world.db.scalar(
        select(func.count())
        .select_from(StocktakePostingItem)
        .where(StocktakePostingItem.posting_id == posting.id)
    ) == 0
    establishment = world.db.scalar(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.posting_id == posting.id
        )
    )
    assert establishment is not None
    assert establishment.established_ledger_cursor == prepared.task.cutoff_ledger_cursor
    assert world.db.get(
        InventoryLedgerHead, finalize_service.posting_service.INVENTORY_LEDGER_HEAD_ID
    ).next_cursor == next_cursor_before
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("0.000")


def test_finalizer_permission_version_and_hq_organization_fail_closed(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(world)
    before_transactions = world.db.scalar(
        select(func.count()).select_from(InventoryTransaction)
    )

    with pytest.raises(OpeningStocktakeFinalizeError) as forbidden:
        post_approved_opening_stocktake(
            world.db,
            actor=world.principals["manager_x"],
            command=PostOpeningStocktakeCommand(
                task_id=prepared.task.id,
                expected_version=prepared.task.version,
            ),
            idempotency_key="opening-finalize-manager-denied-0001",
            request_id="opening-finalize-manager-denied-request",
        )
    assert forbidden.value.code == "opening_finalize_forbidden"

    with pytest.raises(OpeningStocktakeFinalizeError) as stale:
        _post(
            world,
            prepared,
            key="opening-finalize-stale-version-0001",
            expected_version=prepared.task.version + 1,
        )
    assert stale.value.code == "opening_finalize_version_conflict"

    world.hq.org_type = "region_company"
    with pytest.raises(OpeningStocktakeFinalizeError) as not_hq:
        _post(
            world,
            prepared,
            key="opening-finalize-non-hq-admin-0001",
            expected_version=prepared.task.version,
        )
    assert not_hq.value.code in {
        "opening_finalize_actor_not_current",
        "opening_finalize_assignment_not_current",
    }
    world.db.rollback()
    assert world.db.scalar(select(func.count()).select_from(InventoryTransaction)) == before_transactions


def test_finalize_failure_rolls_back_ledger_posting_release_and_task(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    task_id = prepared.task.id
    approved_version = prepared.task.version
    head = world.db.get(InventoryLedgerHead, finalize_service.posting_service.INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    next_cursor_before = head.next_cursor

    def _fail_task_audit(*_args, **_kwargs):
        raise AuditChainStateError("injected final task audit failure")

    monkeypatch.setattr(finalize_service, "append_audit_event", _fail_task_audit)
    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        _post(
            world,
            prepared,
            key="opening-finalize-rollback-0001",
            expected_version=approved_version,
        )
    assert captured.value.code == "opening_finalize_audit_chain_unavailable"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, task_id)
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == task_id)
    )
    assert task is not None and task.status == "approved" and task.version == approved_version
    assert task.posted_at is None
    assert freeze is not None and freeze.status == "active" and freeze.valid_to is None
    assert world.db.get(
        InventoryLedgerHead, finalize_service.posting_service.INVENTORY_LEDGER_HEAD_ID
    ).next_cursor == next_cursor_before
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("0.000")
    assert world.db.scalar(
        select(func.count()).select_from(StocktakePosting).where(
            StocktakePosting.task_id == task_id
        )
    ) == 0
    assert world.db.scalar(
        select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.source_document_id == str(task_id)
        )
    ) == 0


@pytest.mark.parametrize("tamper", ["quantity", "version"])
def test_close_rejects_balance_projection_drift(
    world: SimpleNamespace,
    tamper: str,
) -> None:
    prepared = _approve(world)
    result = _post(
        world,
        prepared,
        key=f"opening-finalize-balance-{tamper}-drift-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    balance = world.db.get(StockBalance, world.account.id)
    assert task is not None and balance is not None
    if tamper == "quantity":
        balance.quantity += Decimal("1.000")
    else:
        balance.version += 1
    world.db.flush()

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=task.version,
            ),
            idempotency_key=f"opening-finalize-balance-{tamper}-drift-close-0001",
            request_id="opening-finalize-balance-drift-close-request",
        )
    assert captured.value.code == "opening_finalize_replay_evidence_invalid"
    world.db.rollback()
    assert world.db.get(FormalStocktakeTask, task.id).status == "posted"
    assert world.db.get(StockBalance, world.account.id).quantity == result.total_quantity


@pytest.mark.parametrize("tamper", ["terminal_outbox", "posting_key", "boundary"])
def test_canonical_terminal_evidence_tamper_fails_closed(
    world: SimpleNamespace,
    tamper: str,
) -> None:
    prepared = _approve(world)
    result = _post(
        world,
        prepared,
        key=f"opening-finalize-canonical-{tamper}-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    transaction = world.db.get(InventoryTransaction, result.inventory_transaction_id)
    assert transaction is not None
    if tamper == "terminal_outbox":
        row = world.db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "stocktake_task",
                OutboxEvent.aggregate_id == str(prepared.task.id),
                OutboxEvent.event_type == "stocktake.opening.posted",
            )
        )
        assert row is not None
        row.payload_jsonb = {**row.payload_jsonb, "ledger_cursor": -1}
    elif tamper == "posting_key":
        transaction.posting_key = f"forged-opening:{prepared.task.id}"
    else:
        movement = world.db.scalar(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id == transaction.id
            )
        )
        assert movement is not None
        movement.external_boundary_code = "forged-boundary"
    world.db.flush()

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        validate_opening_finalize_evidence_for_replay(
            world.db, task_id=prepared.task.id
        )
    assert captured.value.code == "opening_finalize_replay_evidence_invalid"
    world.db.rollback()


def test_serial_projection_drift_fails_close_and_replay(
    world: SimpleNamespace,
) -> None:
    policy = world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    assert policy is not None
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-FINALIZE-001",
        qr_code="QR-FINALIZE-001",
        lot_id=None,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    world.db.add(serial)
    world.db.commit()
    prepared = _approve(
        world,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=serial.serial_no,
                serial_identifier_type="serial_no",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    result = _post(
        world,
        prepared,
        key="opening-finalize-serial-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    position = world.db.get(SerialCurrentPosition, serial.id)
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert position is not None and task is not None
    assert position.stock_account_id == world.account.id
    position.stock_account_id = None
    world.db.flush()

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=task.version,
            ),
            idempotency_key="opening-finalize-serial-close-0001",
            request_id="opening-finalize-serial-close-request",
        )
    assert captured.value.code == "opening_finalize_replay_evidence_invalid"
    world.db.rollback()
    assert world.db.get(SerialCurrentPosition, serial.id).stock_account_id == world.account.id
    assert result.inventory_transaction_id is not None


def test_historical_terminal_reproof_uses_hq_audit_snapshot_not_current_status(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(world)
    _post(
        world,
        prepared,
        key="opening-finalize-historical-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    world.admin.person.employment_status = "left"
    world.hq.status = "inactive"
    world.hq.org_type = "region_company"
    world.db.flush()

    # This is evidence verification, not authorization for a new write.  The
    # immutable audit snapshot and assignment-at-posting remain valid even
    # though the former administrator can no longer execute a command.
    validate_opening_finalize_evidence_for_replay(
        world.db, task_id=prepared.task.id
    )
    world.db.rollback()


def test_another_current_admin_can_close_after_original_admin_left_and_revoked(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(
        world,
        observations=_control_matched_observations(world),
    )
    posted = _post(
        world,
        prepared,
        key="opening-finalize-admin-history-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    posting = world.db.get(StocktakePosting, posted.posting_id)
    assert task is not None and posting is not None

    successor = _user_with_role(
        world.db,
        world.hq,
        world.roles["admin"],
        "national",
        "*",
        "Successor-Admin",
    )
    world.admin.person.employment_status = "left"
    world.admin.person.organization_id = world.region_x.id
    world.admin.user.authorization_version += 1
    world.admin.assignment.status = "revoked"
    world.admin.assignment.revoked_at = posting.posted_at + timedelta(
        microseconds=1
    )
    world.admin.assignment.revoked_by = successor.user.id
    world.db.commit()
    successor_principal = load_formal_principal(
        world.db,
        successor.user.id,
        now=NOW + timedelta(hours=4),
    )

    closed = close_posted_opening_stocktake(
        world.db,
        actor=successor_principal,
        command=CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=task.version,
        ),
        idempotency_key="opening-finalize-successor-close-0001",
        request_id="opening-finalize-successor-close-request",
    )

    assert closed.resulting_task_status == "closed"
    assert closed.inventory_transaction_id == posted.inventory_transaction_id


def test_same_idempotency_key_with_different_version_conflicts(
    world: SimpleNamespace,
) -> None:
    prepared = _approve(world)
    approved_version = prepared.task.version
    key = "opening-finalize-idempotency-conflict-0001"
    _post(
        world,
        prepared,
        key=key,
        expected_version=approved_version,
    )

    with pytest.raises(OpeningStocktakeFinalizeError) as captured:
        _post(
            world,
            prepared,
            key=key,
            expected_version=approved_version + 1,
        )
    assert captured.value.code == "opening_finalize_idempotency_conflict"


def test_inventory_writer_rejects_inflated_balance_before_outbound(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _approve(world)
    _post(
        world,
        prepared,
        key="opening-finalize-writer-balance-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    balance = world.db.get(StockBalance, world.account.id)
    head = world.db.get(
        InventoryLedgerHead,
        inventory_posting_service.INVENTORY_LEDGER_HEAD_ID,
    )
    assert balance is not None and head is not None
    cursor_before = head.next_cursor
    balance.quantity += Decimal("5.000")
    world.db.flush()

    # Isolate the ordinary writer's own invariant from the earlier opening
    # establishment read gate: even if that upstream callback were weakened,
    # the locked balance cannot be consumed until it matches the ledger.
    monkeypatch.setattr(
        finalize_service,
        "_validate_opening_terminal_effects_from_prelocked_generic_graph",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(InventoryPostingError) as captured:
        post_inventory_transaction(
            world.db,
            actor=world.principals["admin"],
            command=_inventory_command(
                movement_type="stocktake_loss",
                movement=InventoryMovementCommand(
                    from_account_id=world.account.id,
                    to_account_id=None,
                    quantity=Decimal("1.000"),
                    external_boundary_code="projection-drift-test",
                ),
                token="balance-drift-outbound",
            ),
            idempotency_key="inventory-balance-drift-outbound-0001",
            request_id="inventory-balance-drift-outbound-request",
        )
    assert captured.value.code == "inventory_balance_projection_drift"
    assert head.next_cursor == cursor_before


def test_inventory_writer_rejects_serial_current_position_drift(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    assert policy is not None
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-WRITER-DRIFT-001",
        qr_code="QR-WRITER-DRIFT-001",
        lot_id=None,
        lifecycle_status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    world.db.add(serial)
    world.db.commit()
    prepared = _approve_zero(world)
    _post(
        world,
        prepared,
        key="opening-finalize-writer-serial-zero-post-0001",
        expected_version=prepared.task.version,
    )
    world.db.commit()
    post_inventory_transaction(
        world.db,
        actor=world.principals["admin"],
        command=_inventory_command(
            movement_type="inbound",
            movement=InventoryMovementCommand(
                from_account_id=None,
                to_account_id=world.account.id,
                quantity=Decimal("1"),
                serial_ids=(serial.id,),
                external_boundary_code="projection-test-inbound",
            ),
            token="serial-inbound",
        ),
        idempotency_key="inventory-serial-inbound-0001",
        request_id="inventory-serial-inbound-request",
    )
    world.db.commit()
    position = world.db.get(SerialCurrentPosition, serial.id)
    head = world.db.get(
        InventoryLedgerHead,
        inventory_posting_service.INVENTORY_LEDGER_HEAD_ID,
    )
    assert position is not None and head is not None
    cursor_before = head.next_cursor
    position.stock_account_id = None
    world.db.flush()

    monkeypatch.setattr(
        finalize_service,
        "_validate_opening_terminal_effects_from_prelocked_generic_graph",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(InventoryPostingError) as captured:
        post_inventory_transaction(
            world.db,
            actor=world.principals["admin"],
            command=_inventory_command(
                movement_type="stocktake_loss",
                movement=InventoryMovementCommand(
                    from_account_id=world.account.id,
                    to_account_id=None,
                    quantity=Decimal("1"),
                    serial_ids=(serial.id,),
                    external_boundary_code="projection-test-outbound",
                ),
                token="serial-drift-outbound",
            ),
            idempotency_key="inventory-serial-drift-outbound-0001",
            request_id="inventory-serial-drift-outbound-request",
        )
    assert captured.value.code == "inventory_serial_projection_drift"
    assert head.next_cursor == cursor_before
