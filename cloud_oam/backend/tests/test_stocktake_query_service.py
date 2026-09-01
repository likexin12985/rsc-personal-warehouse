from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import event, select

import app.formal_services.stocktake_count as count_service
import app.formal_services.stocktake_query as service
import app.formal_services.stocktake_task as task_service
from app.formal_access import load_formal_principal
from app.formal_services.stocktake_count import (
    StocktakePhysicalObservationInput,
    StocktakeSnapshotCountInput,
    SubmitStocktakeInitialScopeCountCommand,
    submit_stocktake_initial_scope_count,
)
from app.formal_services.stocktake_query import (
    StocktakeReadError,
    list_stocktake_tasks,
    stocktake_task_detail,
)
from app.formal_services.stocktake_task import create_personal_stocktake_draft
from app.foundation_models import (
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeCountObservation,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakePosting,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
)
from app.stocktake_read_schemas import (
    StocktakeTaskDetailOut,
    StocktakeTaskPageOut,
)
from app.stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeScopeSelectionIn,
    StocktakeTaskCreateIn,
)
from test_stocktake_task_service import (  # noqa: F401
    NOW,
    SECRET,
    _create_managed,
    _managed_draft,
    _start,
    _termination_draft,
    db,
    world,
)


EVALUATED_AT = NOW + timedelta(minutes=5)
READ_AT = NOW + timedelta(minutes=10)


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(count_service, "_database_now", lambda _db: NOW)


@pytest.fixture
def read_world(world):
    read_permission = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="read",
        field_code="",
        description="formal stocktake read",
    )
    world.db.add(read_permission)
    world.db.flush()
    roles = {
        row.code: row
        for row in world.db.scalars(
            select(Role).where(
                Role.code.in_(("admin", "provincial_manager", "technician"))
            )
        ).all()
    }
    world.db.add_all(
        [
            RolePermission(
                id=uuid.uuid4(),
                role_id=roles[code].id,
                permission_id=read_permission.id,
                effect="allow",
            )
            for code in ("admin", "provincial_manager", "technician")
        ]
    )
    world.db.flush()
    world.principals = {
        name: load_formal_principal(world.db, principal.user_id, now=READ_AT)
        for name, principal in world.principals.items()
    }
    return world


def _mixed_started(read_world, *, key: str, blind_count: bool = True):
    draft = StocktakeTaskCreateIn(
        task_type="sample",
        region_org_id=read_world.region_x.id,
        blind_count=blind_count,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=read_world.region_x.id,
                location_id=read_world.personal_location.id,
                assignee_person_id=read_world.technician.person.id,
                scope_mode="filtered",
                material_id=read_world.material_a.id,
                condition_code="new",
                freeze_mode="cutoff_replay",
            ),
            StocktakeScopeSelectionIn(
                owner_org_id=read_world.region_x.id,
                location_id=read_world.region_location.id,
                assignee_person_id=read_world.manager_x.person.id,
                scope_mode="filtered",
                material_id=read_world.material_a.id,
                condition_code="new",
                freeze_mode="hard",
            ),
        ),
        deadline=NOW + timedelta(days=2),
        note="查询范围裁剪测试",
    )
    created = _create_managed(read_world, key=f"{key}-create", draft=draft)
    started = _start(read_world, created.task_id, key=f"{key}-start")
    task = read_world.db.get(FormalStocktakeTask, created.task_id)
    round_row = read_world.db.get(StocktakeRound, started.initial_round_id)
    scopes = tuple(
        read_world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == created.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    assert task is not None and round_row is not None and len(scopes) == 2
    by_location = {row.location_id: row for row in scopes}
    return task, round_row, by_location


def _submit_scope(
    read_world,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    actor_name: str,
    account_id: uuid.UUID,
    counted_qty: str,
    key: str,
    blind: bool = True,
    observations: tuple[StocktakePhysicalObservationInput, ...] = (),
):
    return submit_stocktake_initial_scope_count(
        read_world.db,
        actor=read_world.principals[actor_name],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            count_mode="blind" if blind else "open",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=account_id,
                    counted_qty=Decimal(counted_qty),
                    book_qty_confirmation=(
                        None
                        if blind
                        else Decimal("3.000")
                        if account_id == read_world.personal_account.id
                        else Decimal("5.000")
                    ),
                ),
            ),
            physical_observations=observations,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _seal_difference_facts(
    read_world,
    task,
    round_row,
    *,
    specs,
):
    """Create immutable read fixtures without coupling to a concurrent writer branch."""

    submission = read_world.db.scalar(
        select(StocktakeRoundSubmission).where(
            StocktakeRoundSubmission.task_id == task.id,
            StocktakeRoundSubmission.round_id == round_row.id,
        )
    )
    admin_assignment = read_world.db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == read_world.principals["admin"].user_id,
            RoleAssignment.scope_type == "national",
        )
    )
    assert submission is not None and admin_assignment is not None
    rows = []
    pending_count = 0
    for number, spec in enumerate(specs, start=1):
        pending = spec.get("pending", False)
        pending_count += int(pending)
        row = StocktakeDifference(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=spec["scope_id"],
            control_snapshot_line_id=None,
            difference_no=number,
            difference_type="excess" if pending else "missing",
            material_id=spec.get("material_id"),
            expected_account_id=None if pending else spec["account_id"],
            observed_account_id=None,
            observed_line_id=spec.get("observation_id"),
            serial_id=None,
            book_qty=Decimal("0.000") if pending else Decimal("1.000"),
            counted_qty=Decimal("1.000") if pending else Decimal("0.000"),
            difference_qty=Decimal("1.000") if pending else Decimal("-1.000"),
            affected_qty=Decimal("1.000"),
            reason_code=(
                "stocktake_pending_verification" if pending else "quantity_shortage"
            ),
            reason_text=("等待主数据核验" if pending else "截止快照与实盘差异"),
            evidence_required=True,
            created_at=EVALUATED_AT,
        )
        read_world.db.add(row)
        rows.append(row)
    read_world.db.flush()
    completion = StocktakeDifferenceSetCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        round_submission_id=submission.id,
        difference_count=len(rows),
        physical_difference_count=len(rows),
        control_difference_count=0,
        pending_observation_difference_count=pending_count,
        total_affected_qty=Decimal(len(rows)).quantize(Decimal("0.000")),
        difference_manifest_sha256="a" * 64,
        request_sha256="b" * 64,
        idempotency_key_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        completed_by_user_id=read_world.principals["admin"].user_id,
        completed_by_person_id=read_world.principals["admin"].person_id,
        completed_role_assignment_id=admin_assignment.id,
        authorization_version=read_world.principals["admin"].authorization_version,
        role_code="admin",
        scope_type="national",
        scope_id_snapshot="*",
        authorization_sha256="c" * 64,
        completed_at=EVALUATED_AT,
        created_at=EVALUATED_AT,
    )
    read_world.db.add(completion)
    read_world.db.flush()
    return completion, tuple(rows)


def _forbidden_keys(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if (
                key in {"authorization_sha256", "permission_keys"}
                or "mobile" in lowered
                or "candidate" in lowered
                or "idempotency" in lowered
            ):
                found.add(key)
            found.update(_forbidden_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def test_formal_read_permission_is_mandatory(world):
    with pytest.raises(StocktakeReadError) as caught:
        list_stocktake_tasks(
            world.db,
            actor=world.principals["manager_x"],
            limit=10,
            now=READ_AT,
        )
    assert caught.value.code == "stocktake_read_forbidden"
    assert caught.value.status_code == 403


def test_public_schema_definitions_contain_no_sensitive_internal_fields():
    assert _forbidden_keys(StocktakeTaskDetailOut.model_json_schema()) == set()
    assert _forbidden_keys(StocktakeTaskPageOut.model_json_schema()) == set()


def test_blind_technician_is_scope_cropped_before_and_after_seal(read_world):
    task, round_row, scopes = _mixed_started(read_world, key="blind-crop")
    personal_scope = scopes[read_world.personal_location.id]
    region_scope = scopes[read_world.region_location.id]

    before = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert [row.scope_id for row in before.scopes] == [personal_scope.id]
    assert before.scopes[0].snapshot_visibility == "hidden"
    assert before.scopes[0].snapshot_accounts == ()
    assert before.scopes[0].allowed_actions == ("submit_initial_count",)
    assert before.rounds[0].visible_count_lines == ()
    assert before.rounds[0].visible_differences == ()
    assert before.rounds[0].differences_visible is False

    first = _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=personal_scope,
        actor_name="technician",
        account_id=read_world.personal_account.id,
        counted_qty="2.000",
        key="blind-crop-personal",
    )
    assert first.round_submitted is False
    partial = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert len(partial.rounds[0].visible_count_lines) == 1
    assert partial.rounds[0].visible_count_lines[0].counted_qty == Decimal("2.000")
    assert partial.rounds[0].visible_count_lines[0].book_qty is None
    assert partial.rounds[0].visible_count_lines[0].expected_serial_ids is None
    assert partial.scopes[0].snapshot_accounts == ()
    assert partial.rounds[0].visible_differences == ()

    second = _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=region_scope,
        actor_name="manager_x",
        account_id=read_world.region_new.id,
        counted_qty="4.000",
        key="blind-crop-region",
    )
    assert second.round_submitted is True
    submitted_hidden = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert submitted_hidden.scopes[0].snapshot_visibility == "hidden"
    assert submitted_hidden.rounds[0].difference_completion is None
    assert submitted_hidden.state_axes.difference_status == "hidden_for_blind_counter"

    _seal_difference_facts(
        read_world,
        task,
        round_row,
        specs=(
            {
                "scope_id": personal_scope.id,
                "account_id": read_world.personal_account.id,
                "material_id": read_world.material_a.id,
            },
            {
                "scope_id": region_scope.id,
                "account_id": read_world.region_new.id,
                "material_id": read_world.material_a.id,
            },
        ),
    )
    sealed = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert [row.scope_id for row in sealed.scopes] == [personal_scope.id]
    assert sealed.scopes[0].snapshot_visibility == "visible"
    assert len(sealed.scopes[0].snapshot_accounts) == 1
    assert sealed.scopes[0].snapshot_accounts[0].book_qty == Decimal("3.000")
    assert len(sealed.rounds[0].visible_differences) == 1
    assert sealed.rounds[0].visible_differences[0].scope_id == personal_scope.id
    assert sealed.rounds[0].difference_completion is not None
    assert sealed.rounds[0].difference_completion.visible_difference_count == 1
    assert sealed.rounds[0].difference_completion.covers_all_task_scopes is False
    assert _forbidden_keys(sealed.model_dump(mode="json")) == set()

    # A task-level zero posting has no item that can be attributed to this
    # technician's scope, so its identifier/count must not cross the crop.
    unscoped_posting = StocktakePosting(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        posting_kind="difference_adjustment",
        inventory_transaction_id=None,
        total_quantity=Decimal("0.000"),
        idempotency_key_hash="0" * 64,
        request_hash="1" * 64,
        posted_by_user_id=read_world.principals["admin"].user_id,
        posted_at=EVALUATED_AT + timedelta(minutes=2),
        created_at=EVALUATED_AT + timedelta(minutes=2),
    )
    read_world.db.add(unscoped_posting)
    read_world.db.flush()
    technician_after_posting = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    manager_after_posting = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    assert technician_after_posting.rounds[0].posting.status == "not_posted"
    assert technician_after_posting.rounds[0].posting.posting_ids == ()
    assert technician_after_posting.state_axes.posting_status == "not_posted"
    assert manager_after_posting.rounds[0].posting.posting_ids == (
        unscoped_posting.id,
    )
    # A legacy per-round posting row cannot advance the independent task-level
    # posting axis without the formal 0035 completion and task transition.
    assert manager_after_posting.state_axes.posting_status == "not_posted"


def test_open_count_shows_only_visible_scope_snapshot_minimum(read_world):
    task, round_row, scopes = _mixed_started(
        read_world,
        key="open-crop",
        blind_count=False,
    )
    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert len(detail.scopes) == 1
    assert detail.scopes[0].scope_id == scopes[read_world.personal_location.id].id
    assert detail.scopes[0].snapshot_visibility == "visible"
    assert [row.stock_account_id for row in detail.scopes[0].snapshot_accounts] == [
        read_world.personal_account.id
    ]
    assert detail.scopes[0].snapshot_accounts[0].book_qty == Decimal("3.000")


def test_manager_admin_and_technician_ranges_and_uniform_404(read_world):
    task, _round, scopes = _mixed_started(read_world, key="ranges")
    manager = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    admin = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["admin"],
        task_id=task.id,
        now=READ_AT,
    )
    technician = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    expected_scope_ids = {row.id for row in scopes.values()}
    assert {row.scope_id for row in manager.scopes} == expected_scope_ids
    assert {row.scope_id for row in admin.scopes} == expected_scope_ids
    assert {row.scope_id for row in technician.scopes} == {
        scopes[read_world.personal_location.id].id
    }
    assert list_stocktake_tasks(
        read_world.db,
        actor=read_world.principals["manager_y"],
        limit=10,
        now=READ_AT,
    ).items == ()

    errors = []
    for target in (task.id, uuid.uuid4()):
        with pytest.raises(StocktakeReadError) as caught:
            stocktake_task_detail(
                read_world.db,
                actor=read_world.principals["manager_y"],
                task_id=target,
                now=READ_AT,
            )
        errors.append((caught.value.code, caught.value.status_code, caught.value.message))
    assert errors[0] == errors[1] == (
        "stocktake_read_not_found",
        404,
        "盘点任务不存在",
    )


def test_initial_assignment_not_custody_controls_technician_scope_crop(read_world):
    task, _round, scopes = _mixed_started(read_world, key="assignment-crop")
    personal_scope = scopes[read_world.personal_location.id]
    region_scope = scopes[read_world.region_location.id]
    assert region_scope.custodian_person_id_snapshot != read_world.technician.person.id
    region_scope.assignee_user_id = read_world.technician.user.id
    read_world.db.flush()

    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert {row.scope_id for row in detail.scopes} == {
        personal_scope.id,
        region_scope.id,
    }
    assert all(row.assigned_to_me for row in detail.scopes)
    # Read assignment never manufactures a count permission for the regional scope.
    assert detail.scopes[0].allowed_actions + detail.scopes[1].allowed_actions == (
        "submit_initial_count",
    )


def test_all_five_non_opening_task_types_use_the_same_strict_read_contract(
    read_world,
):
    created = [
        _create_managed(
            read_world,
            key=f"type-{task_type}",
            draft=_managed_draft(read_world, task_type=task_type),
        )
        for task_type in ("full", "sample", "ad_hoc")
    ]
    created.append(
        _create_managed(
            read_world,
            key="type-termination",
            draft=_termination_draft(read_world),
        )
    )
    personal = create_personal_stocktake_draft(
        read_world.db,
        actor=read_world.principals["technician"],
        draft=PersonalStocktakeCreateIn(
            blind_count=True,
            freeze_mode="cutoff_replay",
            note="五类查询契约",
        ),
        idempotency_key="type-personal",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-type-personal",
    )
    expected = {
        row.task_id: row.task_type for row in (*created, personal)
    }

    page = list_stocktake_tasks(
        read_world.db,
        actor=read_world.principals["admin"],
        limit=20,
        now=READ_AT,
    )
    assert {row.task_id: row.task_type for row in page.items} == expected
    for task_id, task_type in expected.items():
        detail = stocktake_task_detail(
            read_world.db,
            actor=read_world.principals["admin"],
            task_id=task_id,
            now=READ_AT,
        )
        assert detail.task_type == task_type
        assert detail.schema_version == "1.0"
        assert _forbidden_keys(detail.model_dump(mode="json")) == set()


def test_provincial_manager_read_scope_covers_active_organization_tree(read_world):
    created = _create_managed(
        read_world,
        key="tree-create",
        draft=_managed_draft(read_world),
    )
    child = type(read_world.region_x)(
        id=uuid.uuid4(),
        code="REG-X-CHILD",
        name="区域 X 子区域",
        parent_id=read_world.region_x.id,
        org_type="region_company",
        province_code=None,
        status="active",
    )
    read_world.db.add(child)
    task = read_world.db.get(FormalStocktakeTask, created.task_id)
    assert task is not None
    task.region_org_id = child.id
    task.version += 1
    task.updated_at = NOW + timedelta(seconds=1)
    read_world.db.flush()

    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    assert detail.region_org_id == child.id


def test_uuid_pagination_is_stable_and_graph_loading_is_fixed_batch(read_world):
    for index in range(3):
        _create_managed(read_world, key=f"page-{index}", draft=_managed_draft(read_world))

    statement_counts: list[int] = []
    engine = read_world.db.get_bind()
    for limit in (1, 2):
        counter = {"selects": 0}

        def count_selects(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                counter["selects"] += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            list_stocktake_tasks(
                read_world.db,
                actor=read_world.principals["manager_x"],
                limit=limit,
                now=READ_AT,
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        statement_counts.append(counter["selects"])
    assert statement_counts[0] == statement_counts[1]

    first = list_stocktake_tasks(
        read_world.db,
        actor=read_world.principals["manager_x"],
        limit=2,
        now=READ_AT,
    )
    assert len(first.items) == 2 and first.next_after_id is not None
    second = list_stocktake_tasks(
        read_world.db,
        actor=read_world.principals["manager_x"],
        limit=2,
        after_id=first.next_after_id,
        now=READ_AT,
    )
    assert len(second.items) == 1 and second.next_after_id is None
    assert {row.task_id for row in first.items}.isdisjoint(
        {row.task_id for row in second.items}
    )


def test_authorization_and_task_version_are_rechecked_after_graph_read(
    read_world,
    monkeypatch: pytest.MonkeyPatch,
):
    created = _create_managed(read_world, key="recheck", draft=_managed_draft(read_world))
    actor = read_world.principals["manager_x"]
    original_loader = service._load_task_graphs

    def change_authorization(db, tasks):
        result = original_loader(db, tasks)
        user = db.get(User, actor.user_id)
        assert user is not None
        user.authorization_version += 1
        db.flush()
        return result

    from app.models import User

    monkeypatch.setattr(service, "_load_task_graphs", change_authorization)
    with pytest.raises(StocktakeReadError) as caught:
        stocktake_task_detail(
            read_world.db,
            actor=actor,
            task_id=created.task_id,
            now=READ_AT,
        )
    assert caught.value.code == "stocktake_read_authorization_changed"
    assert caught.value.status_code == 412


def test_task_version_change_during_read_is_conflict(
    read_world,
    monkeypatch: pytest.MonkeyPatch,
):
    created = _create_managed(
        read_world,
        key="version-recheck",
        draft=_managed_draft(read_world),
    )
    original_loader = service._load_task_graphs

    def change_task(db, tasks):
        result = original_loader(db, tasks)
        row = db.get(FormalStocktakeTask, created.task_id)
        assert row is not None
        row.version += 1
        row.updated_at = NOW + timedelta(seconds=2)
        db.flush()
        return result

    monkeypatch.setattr(service, "_load_task_graphs", change_task)
    with pytest.raises(StocktakeReadError) as caught:
        stocktake_task_detail(
            read_world.db,
            actor=read_world.principals["manager_x"],
            task_id=created.task_id,
            now=READ_AT,
        )
    assert caught.value.code == "stocktake_read_snapshot_changed"
    assert caught.value.status_code == 409


def test_query_is_select_only_and_never_flushes_or_commits(
    read_world,
    monkeypatch: pytest.MonkeyPatch,
):
    created = _create_managed(read_world, key="select-only", draft=_managed_draft(read_world))
    before = (set(read_world.db.new), set(read_world.db.dirty), set(read_world.db.deleted))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read service attempted a transaction write method")

    monkeypatch.setattr(read_world.db, "flush", forbidden)
    monkeypatch.setattr(read_world.db, "commit", forbidden)
    monkeypatch.setattr(read_world.db, "rollback", forbidden)
    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=created.task_id,
        now=READ_AT,
    )
    assert detail.task_id == created.task_id
    assert (set(read_world.db.new), set(read_world.db.dirty), set(read_world.db.deleted)) == before


def test_count_difference_reviews_and_posting_remain_independent_facts(read_world):
    created = _create_managed(
        read_world,
        key="axes-create",
        draft=_managed_draft(
            read_world,
            material_id=read_world.material_a.id,
            condition_code="new",
        ),
    )
    started = _start(read_world, created.task_id, key="axes-start")
    task = read_world.db.get(FormalStocktakeTask, created.task_id)
    round_row = read_world.db.get(StocktakeRound, started.initial_round_id)
    scope = read_world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id)
    )
    assert task is not None and round_row is not None and scope is not None
    _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=scope,
        actor_name="manager_x",
        account_id=read_world.region_new.id,
        counted_qty="4.000",
        key="axes-count",
    )
    _seal_difference_facts(
        read_world,
        task,
        round_row,
        specs=(
            {
                "scope_id": scope.id,
                "account_id": read_world.region_new.id,
                "material_id": read_world.material_a.id,
            },
        ),
    )
    manager_assignment = read_world.db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == read_world.manager_x.user.id,
            RoleAssignment.scope_type == "organization",
        )
    )
    assert manager_assignment is not None
    review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage="region",
        reviewer_user_id=read_world.manager_x.user.id,
        reviewer_person_id=read_world.manager_x.person.id,
        reviewer_role_assignment_id=manager_assignment.id,
        authorization_version=read_world.principals["manager_x"].authorization_version,
        decision="approve",
        comment="区域复核事实",
        decision_manifest_sha256="a" * 64,
        idempotency_key_hash="b" * 64,
        reviewed_at=EVALUATED_AT + timedelta(minutes=1),
        created_at=EVALUATED_AT + timedelta(minutes=1),
    )
    # The reader deliberately aggregates a sequence of immutable posting facts.
    # Current schema distinguishes these by kind; the read contract does not
    # assume one posting per round or one fixed posting kind.
    postings = (
        StocktakePosting(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            posting_kind="difference_adjustment",
            inventory_transaction_id=None,
            total_quantity=Decimal("0.000"),
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            posted_by_user_id=read_world.principals["admin"].user_id,
            posted_at=EVALUATED_AT + timedelta(minutes=2),
            created_at=EVALUATED_AT + timedelta(minutes=2),
        ),
        StocktakePosting(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            posting_kind="opening",
            inventory_transaction_id=None,
            total_quantity=Decimal("0.000"),
            idempotency_key_hash="g" * 64,
            request_hash="h" * 64,
            posted_by_user_id=read_world.principals["admin"].user_id,
            posted_at=EVALUATED_AT + timedelta(minutes=3),
            created_at=EVALUATED_AT + timedelta(minutes=3),
        ),
    )
    read_world.db.add_all([review, *postings])
    read_world.db.flush()

    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    assert detail.status == "submitted"
    assert detail.state_axes.count_status == "submitted"
    assert detail.state_axes.difference_status == "evaluated"
    assert detail.state_axes.region_review_status == "approve"
    assert detail.state_axes.headquarters_review_status == "pending"
    assert detail.state_axes.recount_status == "not_required"
    # Legacy per-round posting rows are visible as historical evidence only.
    # The task-level posting axis is established exclusively by the immutable
    # 0035 task-wide posting completion and therefore remains not_posted here.
    assert detail.state_axes.posting_status == "not_posted"
    assert detail.rounds[0].region_review is not None
    assert detail.rounds[0].headquarters_review is None
    assert detail.rounds[0].posting.status == "recorded"
    assert detail.rounds[0].posting.posting_fact_count == 2
    assert set(detail.rounds[0].posting.posting_ids) == {
        row.id for row in postings
    }
    assert detail.rounds[0].posting.inventory_transaction_count == 0


def test_legacy_null_count_cursor_is_visible_but_never_actionable(read_world):
    created = _create_managed(
        read_world,
        key="legacy-cursor-create",
        draft=_managed_draft(
            read_world,
            material_id=read_world.material_a.id,
            condition_code="new",
        ),
    )
    started = _start(read_world, created.task_id, key="legacy-cursor-start")
    task = read_world.db.get(FormalStocktakeTask, created.task_id)
    round_row = read_world.db.get(StocktakeRound, started.initial_round_id)
    scope = read_world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id)
    )
    assert task is not None and round_row is not None and scope is not None
    _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=scope,
        actor_name="manager_x",
        account_id=read_world.region_new.id,
        counted_qty="5.000",
        key="legacy-cursor-count",
    )
    completion = read_world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.task_id == task.id,
            StocktakeScopeCountCompletion.round_id == round_row.id,
        )
    )
    assert completion is not None and completion.count_ledger_cursor is not None
    completion.count_ledger_cursor = None
    read_world.db.flush()

    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    assert detail.rounds[0].visible_scope_completions[0].count_ledger_cursor is None
    assert "generate_initial_differences" not in detail.allowed_actions
    assert detail.rounds[0].allowed_actions == ()


def test_pending_observation_is_explicit_blocker_and_cannot_look_posted(read_world):
    created = _create_managed(
        read_world,
        key="pending-create",
        draft=_managed_draft(read_world, condition_code="new"),
    )
    started = _start(read_world, created.task_id, key="pending-start")
    task = read_world.db.get(FormalStocktakeTask, created.task_id)
    round_row = read_world.db.get(StocktakeRound, started.initial_round_id)
    scope = read_world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id)
    )
    assert task is not None and round_row is not None and scope is not None
    submit_stocktake_initial_scope_count(
        read_world.db,
        actor=read_world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=read_world.region_new.id,
                    counted_qty=Decimal("5.000"),
                ),
                StocktakeSnapshotCountInput(
                    stock_account_id=read_world.region_serial.id,
                    # Serial-tracked material is governed by a zero-scale policy.
                    counted_qty=Decimal("1"),
                    serial_ids=(read_world.serial.id,),
                ),
            ),
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=None,
                    material_identifier_raw="UNKNOWN-MATERIAL",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    reason_code="unknown_material",
                    remark="等待主数据核验",
                ),
            ),
        ),
        idempotency_key="pending-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-pending-count",
    )
    observation = read_world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.task_id == task.id
        )
    )
    assert observation is not None
    _seal_difference_facts(
        read_world,
        task,
        round_row,
        specs=(
            {
                "scope_id": scope.id,
                "account_id": None,
                "material_id": None,
                "observation_id": observation.id,
                "pending": True,
            },
        ),
    )
    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["manager_x"],
        task_id=task.id,
        now=READ_AT,
    )
    assert len(detail.rounds[0].visible_observations) == 1
    assert detail.rounds[0].visible_observations[0].requires_verification is True
    assert detail.rounds[0].visible_differences[0].posting_blocked_by_pending_verification
    assert detail.rounds[0].difference_completion is not None
    assert (
        detail.rounds[0].difference_completion.visible_pending_verification_count
        == 1
    )
    assert detail.state_axes.posting_status == "not_posted"
    assert "generate_initial_differences" not in detail.allowed_actions

    read_world.db.add(
        StocktakePosting(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            posting_kind="difference_adjustment",
            inventory_transaction_id=None,
            total_quantity=Decimal("0.000"),
            idempotency_key_hash="e" * 64,
            request_hash="f" * 64,
            posted_by_user_id=read_world.principals["admin"].user_id,
            posted_at=EVALUATED_AT + timedelta(minutes=2),
            created_at=EVALUATED_AT + timedelta(minutes=2),
        )
    )
    read_world.db.flush()
    with pytest.raises(StocktakeReadError) as caught:
        stocktake_task_detail(
            read_world.db,
            actor=read_world.principals["manager_x"],
            task_id=task.id,
            now=READ_AT,
        )
    assert caught.value.code == "stocktake_read_pending_observation_posted"
    assert caught.value.status_code == 503


def test_blind_recount_exposes_causality_and_assignment_but_hides_reason_and_difference(
    read_world,
):
    task, round_row, scopes = _mixed_started(read_world, key="recount")
    personal_scope = scopes[read_world.personal_location.id]
    region_scope = scopes[read_world.region_location.id]
    _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=personal_scope,
        actor_name="technician",
        account_id=read_world.personal_account.id,
        counted_qty="2.000",
        key="recount-personal",
    )
    _submit_scope(
        read_world,
        task=task,
        round_row=round_row,
        scope=region_scope,
        actor_name="manager_x",
        account_id=read_world.region_new.id,
        counted_qty="5.000",
        key="recount-region",
    )
    difference_completion, _ = _seal_difference_facts(
        read_world,
        task,
        round_row,
        specs=(
            {
                "scope_id": personal_scope.id,
                "account_id": read_world.personal_account.id,
                "material_id": read_world.material_a.id,
            },
        ),
    )
    submission = read_world.db.scalar(
        select(StocktakeRoundSubmission).where(
            StocktakeRoundSubmission.round_id == round_row.id
        )
    )
    manager_assignment = read_world.db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == read_world.manager_x.user.id,
            RoleAssignment.scope_type == "organization",
        )
    )
    technician_assignment = read_world.db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == read_world.technician.user.id,
            RoleAssignment.scope_type == "person",
        )
    )
    assert all(
        value is not None
        for value in (
            submission,
            difference_completion,
            manager_assignment,
            technician_assignment,
        )
    )
    review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage="region",
        reviewer_user_id=read_world.manager_x.user.id,
        reviewer_person_id=read_world.manager_x.person.id,
        reviewer_role_assignment_id=manager_assignment.id,
        authorization_version=read_world.principals["manager_x"].authorization_version,
        decision="recount",
        comment="差异需要复盘",
        decision_manifest_sha256="1" * 64,
        idempotency_key_hash="2" * 64,
        reviewed_at=EVALUATED_AT + timedelta(minutes=1),
        created_at=EVALUATED_AT + timedelta(minutes=1),
    )
    read_world.db.add(review)
    read_world.db.flush()
    recount_case = StocktakeRecountCase(
        id=uuid.uuid4(),
        task_id=task.id,
        source_round_id=round_row.id,
        source_round_submission_id=submission.id,
        source_difference_completion_id=difference_completion.id,
        trigger_review_id=review.id,
        next_round_no=2,
        scope_count=1,
        scope_manifest_sha256="3" * 64,
        assignment_manifest_sha256="4" * 64,
        recount_manifest_sha256="5" * 64,
        request_sha256="6" * 64,
        idempotency_key_hash="7" * 64,
        reason="个人仓差异需要独立复盘",
        opened_by_user_id=read_world.manager_x.user.id,
        opened_by_person_id=read_world.manager_x.person.id,
        opened_role_assignment_id=manager_assignment.id,
        authorization_version=read_world.principals["manager_x"].authorization_version,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_snapshot=str(read_world.region_x.id),
        authorization_sha256="8" * 64,
        opened_at=EVALUATED_AT + timedelta(minutes=2),
        created_at=EVALUATED_AT + timedelta(minutes=2),
    )
    read_world.db.add(recount_case)
    read_world.db.flush()
    assignment = StocktakeRecountScopeAssignment(
        id=uuid.uuid4(),
        recount_case_id=recount_case.id,
        task_id=task.id,
        source_round_id=round_row.id,
        scope_id=personal_scope.id,
        assignee_user_id=read_world.technician.user.id,
        assignee_person_id=read_world.technician.person.id,
        assignee_role_assignment_id=technician_assignment.id,
        authorization_version=read_world.principals["technician"].authorization_version,
        role_code="technician",
        scope_type="person",
        scope_id_snapshot=str(read_world.technician.person.id),
        authorization_sha256="9" * 64,
        assignment_sha256="a" * 64,
        assigned_at=EVALUATED_AT + timedelta(minutes=2),
        created_at=EVALUATED_AT + timedelta(minutes=2),
    )
    successor = StocktakeRound(
        id=uuid.uuid4(),
        task_id=task.id,
        round_no=2,
        round_type="recount",
        status="counting",
        submitted_by_user_id=None,
        started_at=EVALUATED_AT + timedelta(minutes=2),
        submitted_at=None,
        count_manifest_sha256=None,
        idempotency_key_hash="b" * 64,
        recount_case_id=recount_case.id,
        created_at=EVALUATED_AT + timedelta(minutes=2),
        updated_at=EVALUATED_AT + timedelta(minutes=2),
    )
    read_world.db.add_all([assignment, successor])
    task.status = "counting"
    task.current_round_no = 2
    task.version += 1
    task.updated_at = EVALUATED_AT + timedelta(minutes=2)
    read_world.db.flush()

    detail = stocktake_task_detail(
        read_world.db,
        actor=read_world.principals["technician"],
        task_id=task.id,
        now=READ_AT,
    )
    assert [row.scope_id for row in detail.scopes] == [personal_scope.id]
    assert detail.scopes[0].assigned_to_me is True
    assert detail.scopes[0].allowed_actions == ("submit_recount_count",)
    assert "submit_recount_count" in detail.allowed_actions
    assert detail.scopes[0].snapshot_visibility == "hidden"
    assert detail.state_axes.recount_status == "counting"
    assert detail.state_axes.difference_status == "hidden_for_blind_counter"
    assert detail.rounds[0].visible_differences == ()
    assert detail.rounds[0].region_review is None
    cause = detail.rounds[1].recount_cause
    assert cause is not None
    assert cause.source_round_id == round_row.id
    assert cause.trigger_review_id == review.id
    assert cause.reason_visible is False and cause.reason is None
    assert len(cause.assignments) == 1
    assert cause.assignments[0].scope_id == personal_scope.id
    assert cause.assignments[0].assigned_to_me is True
    assert _forbidden_keys(detail.model_dump(mode="json")) == set()
