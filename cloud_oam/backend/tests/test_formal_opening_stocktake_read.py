from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select, text, update

import app.formal_services.opening_observation_disposition as disposition_service
import app.formal_services.opening_stocktake_count as count_service
import app.formal_services.opening_stocktake_query as query_service
import app.formal_services.opening_stocktake_recount as recount_service
from app.formal_access import Entitlement, load_formal_principal
from app.formal_services.opening_stocktake import (
    OpeningStocktakeScopeInput,
    start_opening_stocktake,
)
from app.formal_services.opening_stocktake_recount import (
    open_opening_stocktake_recount,
)
from app.formal_services.opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.formal_services.opening_stocktake_review import (
    submit_opening_region_review,
)
from app.formal_services.opening_stocktake_query import (
    OpeningStocktakeReadError,
    list_opening_stocktakes,
    opening_stocktake_detail,
)
from app.foundation_models import (
    Organization,
    Permission,
    RoleAssignment,
    RolePermission,
)
from app.inventory_models import (
    CustodyAssignment,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.main import app
from app.opening_stocktake_read_schemas import OpeningStocktakeTaskSummaryOut
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeCountObservation,
    StocktakeObservationDisposition,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeRound,
    StocktakeScopeCountCompletion,
)

from test_opening_stocktake_review_service import (  # noqa: E402
    NOW,
    _fixed_database_times,
    _material,
    _prepare_observation,
    _prepare_submitted,
    _record_observation_disposition,
    _review_command,
    db,
    world as review_world,
)
from test_opening_stocktake_recount_service import (  # noqa: E402
    _command as _open_recount_command,
)
from test_opening_stocktake_finalize_service import (  # noqa: E402
    _approve as _approve_opening,
    _control_matched_observations,
    _post as _post_opening,
)
from test_opening_control_reconciliation_service import (  # noqa: E402
    _approved_reconciliation,
    world as reconciliation_world,
)


def test_existing_task_read_schema_rejects_start_action() -> None:
    with pytest.raises(ValidationError):
        OpeningStocktakeTaskSummaryOut(
            task_id=uuid.uuid4(),
            task_no="OPENING-READ-CONTRACT-001",
            region_org_id=uuid.uuid4(),
            status="draft",
            blind_count=True,
            current_round_no=0,
            current_round_status=None,
            visible_scope_count=0,
            completed_scope_count=0,
            evidence_status="not_started",
            difference_count=None,
            reconciliation_status="not_required",
            reconciliation_run_id=None,
            pending_control_difference_count=0,
            task_version=0,
            deadline=None,
            allowed_actions=["start"],  # type: ignore[list-item]
        )


def test_empty_list_still_requires_initialized_formal_ledger(world, monkeypatch):
    def unavailable_ledger(_db):
        raise OpeningStocktakeReadError(
            code="opening_stocktake_ledger_unavailable",
            status_code=503,
            message="正式库存账本尚未安全初始化",
        )

    monkeypatch.setattr(query_service, "_ledger_snapshot", unavailable_ledger)

    with pytest.raises(OpeningStocktakeReadError) as raised:
        list_opening_stocktakes(
            world.db,
            actor=world.principals["admin"],
            limit=20,
            after_id=uuid.UUID(int=(1 << 128) - 1),
        )

    assert raised.value.code == "opening_stocktake_ledger_unavailable"
    assert raised.value.status_code == 503


@pytest.fixture
def world(review_world):
    read = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="read",
        field_code="",
        description="read formal opening stocktakes",
    )
    review_world.db.add(read)
    review_world.db.flush()
    review_world.db.add_all(
        [
            RolePermission(
                role_id=role.id,
                permission_id=read.id,
                effect="allow",
            )
            for role in review_world.roles.values()
        ]
    )
    review_world.db.commit()
    review_world.permissions["read"] = read
    review_world.principals = {
        name: load_formal_principal(review_world.db, value.user.id, now=NOW)
        for name, value in (
            ("admin", review_world.admin),
            ("manager_x", review_world.manager_x),
            ("manager_y", review_world.manager_y),
            ("technician", review_world.technician),
        )
    }
    return review_world


@pytest.fixture
def closed_read_world(reconciliation_world, monkeypatch):
    ticks = count()

    def database_now(_db):
        return NOW + timedelta(hours=3, microseconds=next(ticks) + 1)

    monkeypatch.setattr(query_service.finalize_service, "_database_now", database_now)
    monkeypatch.setattr(
        query_service.reconciliation_service,
        "_database_now",
        database_now,
    )
    read = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="read",
        field_code="",
        description="read closed formal opening stocktakes",
    )
    reconciliation_world.db.add(read)
    reconciliation_world.db.flush()
    reconciliation_world.db.add(
        RolePermission(
            role_id=reconciliation_world.roles["admin"].id,
            permission_id=read.id,
            effect="allow",
        )
    )
    reconciliation_world.db.commit()
    reconciliation_world.principals["admin"] = load_formal_principal(
        reconciliation_world.db,
        reconciliation_world.admin.user.id,
        now=NOW + timedelta(hours=3),
    )
    return reconciliation_world


def _draft_task(
    world,
    *,
    region: Organization,
    location: StockLocation,
    assignee_user_id: str,
    suffix: str,
    custodian_person_id_snapshot: uuid.UUID | None = None,
    scope_owner: Organization | None = None,
) -> FormalStocktakeTask:
    task = FormalStocktakeTask(
        id=uuid.uuid4(),
        task_no=f"OPEN-READ-{suffix}",
        task_type="opening",
        region_org_id=region.id,
        status="draft",
        blind_count=True,
        current_round_no=0,
        created_by_user_id=world.admin.user.id,
        version=0,
        note="",
        created_at=NOW,
        updated_at=NOW,
    )
    scope = FormalStocktakeScope(
        id=uuid.uuid4(),
        task_id=task.id,
        scope_no=1,
        scope_mode="location_all",
        location_id=location.id,
        owner_org_id=(scope_owner or region).id,
        custodian_person_id_snapshot=custodian_person_id_snapshot,
        assignee_user_id=assignee_user_id,
        material_id=None,
        condition_code=None,
        availability_bucket=None,
        scope_key=f"location:{location.id}",
        scope_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        created_at=NOW,
    )
    world.db.add(task)
    world.db.flush()
    world.db.add(scope)
    world.db.flush()
    return task


def _new_region(world, index: int) -> tuple[Organization, StockLocation]:
    region = Organization(
        id=uuid.uuid4(),
        code=f"READ-{index}",
        name=f"读取区域 {index}",
        parent_id=world.hq.id,
        org_type="region_company",
        province_code=f"{index:06d}",
        status="active",
    )
    location = StockLocation(
        id=uuid.uuid4(),
        code=f"READ-{index}-WH",
        name=f"读取区域 {index} 仓",
        location_type="region",
        owner_org_id=region.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(region)
    world.db.flush()
    world.db.add(location)
    world.db.flush()
    return region, location


def test_list_filters_admin_region_and_technician_scope_and_paginates(world):
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-X-PERSONAL",
        name="工程师读取个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    world.db.add(personal_location)
    world.db.flush()
    task_x = _draft_task(
        world,
        region=world.region_x,
        location=personal_location,
        assignee_user_id=world.technician.user.id,
        suffix="X",
        custodian_person_id_snapshot=world.technician.person.id,
    )
    region_y_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-Y-WH",
        name="读取区域 Y 仓",
        location_type="region",
        owner_org_id=world.region_y.id,
        status="active",
    )
    world.db.add(region_y_location)
    world.db.flush()
    task_y = _draft_task(
        world,
        region=world.region_y,
        location=region_y_location,
        assignee_user_id=world.manager_y.user.id,
        suffix="Y",
    )

    admin_first = list_opening_stocktakes(
        world.db, actor=world.principals["admin"], limit=1
    )
    assert len(admin_first.items) == 1
    assert admin_first.next_after_id is not None
    admin_second = list_opening_stocktakes(
        world.db,
        actor=world.principals["admin"],
        limit=1,
        after_id=admin_first.next_after_id,
    )
    assert {admin_first.items[0].task_id, admin_second.items[0].task_id} == {
        task_x.id,
        task_y.id,
    }
    manager_x = list_opening_stocktakes(
        world.db, actor=world.principals["manager_x"], limit=20
    )
    manager_y = list_opening_stocktakes(
        world.db, actor=world.principals["manager_y"], limit=20
    )
    technician = list_opening_stocktakes(
        world.db, actor=world.principals["technician"], limit=20
    )
    assert [row.task_id for row in manager_x.items] == [task_x.id]
    assert [row.task_id for row in manager_y.items] == [task_y.id]
    assert [row.task_id for row in technician.items] == [task_x.id]
    assert technician.items[0].visible_scope_count == 1


def test_detail_returns_same_404_for_missing_and_out_of_scope(world):
    task = _draft_task(
        world,
        region=world.region_x,
        location=world.location,
        assignee_user_id=world.manager_x.user.id,
        suffix="404",
    )
    for task_id in (task.id, uuid.uuid4()):
        with pytest.raises(OpeningStocktakeReadError) as caught:
            opening_stocktake_detail(
                world.db,
                actor=world.principals["manager_y"],
                task_id=task_id,
            )
        assert caught.value.status_code == 404
        assert caught.value.code == "opening_stocktake_not_found"


def test_list_and_detail_expose_batch_reconciliation_status(world):
    task = _draft_task(
        world,
        region=world.region_x,
        location=world.location,
        assignee_user_id=world.manager_x.user.id,
        suffix="RECONCILIATION-STATUS",
    )
    summary = list_opening_stocktakes(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
    ).items[0]
    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=task.id,
    )
    for output in (summary, detail):
        assert output.reconciliation_status == "not_required"
        assert output.reconciliation_run_id is None
        assert output.pending_control_difference_count == 0


def test_reconciliation_status_shape_failure_stops_read_closed(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    task = _draft_task(
        world,
        region=world.region_x,
        location=world.location,
        assignee_user_id=world.manager_x.user.id,
        suffix="RECONCILIATION-INVALID",
    )
    monkeypatch.setattr(
        query_service,
        "_roundless_reconciliation_statuses",
        lambda _db, *, task_ids: {},
    )
    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=task.id,
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_closed_without_control_differences_exposes_not_required(
    closed_read_world,
):
    prepared = _approve_opening(
        closed_read_world,
        observations=_control_matched_observations(closed_read_world),
    )
    posted = _post_opening(
        closed_read_world,
        prepared,
        key=f"opening-read-closed-no-run-post-{uuid.uuid4().hex}",
        expected_version=prepared.task.version,
    )
    task = closed_read_world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted"
    assert posted.pending_control_difference_count == 0
    closed = query_service.finalize_service.close_posted_opening_stocktake(
        closed_read_world.db,
        actor=closed_read_world.principals["admin"],
        command=query_service.finalize_service.CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=task.version,
        ),
        idempotency_key=f"opening-read-closed-no-run-close-{uuid.uuid4().hex}",
        request_id="opening-read-closed-no-run-close-request",
    )
    assert closed.resulting_task_status == "closed"
    closed_read_world.db.commit()

    detail = opening_stocktake_detail(
        closed_read_world.db,
        actor=closed_read_world.principals["admin"],
        task_id=task.id,
    )

    assert detail.status == "closed"
    assert detail.reconciliation_status == "not_required"
    assert detail.reconciliation_run_id is None
    assert detail.pending_control_difference_count == 0


def test_closed_difference_run_pending_status_tamper_fails_read_closed(
    closed_read_world,
    monkeypatch: pytest.MonkeyPatch,
):
    graph = _approved_reconciliation(
        closed_read_world,
        suffix=f"read-closed-tamper-{uuid.uuid4().hex}",
    )
    closed = query_service.finalize_service.close_posted_opening_stocktake(
        closed_read_world.db,
        actor=closed_read_world.principals["admin"],
        command=query_service.finalize_service.CloseOpeningStocktakeCommand(
            task_id=graph.posted.task.id,
            expected_version=graph.posted.task.version,
        ),
        idempotency_key=f"opening-read-closed-tamper-close-{uuid.uuid4().hex}",
        request_id="opening-read-closed-tamper-close-request",
    )
    assert closed.resulting_task_status == "closed"
    closed_read_world.db.commit()

    real_statuses = (
        query_service.reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch
    )
    tamper_called = False

    def pending_tamper(db_session, *, proof, audit_proof):
        nonlocal tamper_called
        statuses = real_statuses(
            db_session,
            proof=proof,
            audit_proof=audit_proof,
        )
        original = statuses[graph.posted.task.id]
        assert original.status == "approved"
        assert original.reconciliation_run_id == graph.started.reconciliation_run_id
        tamper_called = True
        return {
            **statuses,
            graph.posted.task.id: query_service.reconciliation_service.OpeningControlReconciliationStatus(
                status="pending",
                reconciliation_run_id=graph.started.reconciliation_run_id,
                pending_control_difference_count=1,
            ),
        }

    monkeypatch.setattr(
        query_service.reconciliation_service,
        "_opening_control_reconciliation_statuses_from_prelocked_batch",
        pending_tamper,
    )
    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            closed_read_world.db,
            actor=closed_read_world.principals["admin"],
            task_id=graph.posted.task.id,
        )

    assert tamper_called is True
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_close_action_uses_independent_reconciliation_status(world):
    admin = world.principals["admin"]
    grant = next(row for row in admin.assignments if row.role_code == "admin")
    actor = replace(
        admin,
        entitlements=admin.entitlements
        + (
            Entitlement(
                assignment_id=grant.assignment_id,
                role_code=grant.role_code,
                scope_type=grant.scope_type,
                scope_id=grant.scope_id,
                resource="stocktake",
                action="post_opening",
                field_code="",
                effect="allow",
            ),
        ),
    )
    task_id = uuid.uuid4()
    graph = SimpleNamespace(
        task=SimpleNamespace(
            id=task_id,
            status="posted",
            region_org_id=world.region_x.id,
        ),
        round_row=None,
        establishments=(object(),),
    )
    pending = query_service.reconciliation_service.OpeningControlReconciliationStatus(
        status="pending",
        reconciliation_run_id=None,
        pending_control_difference_count=1,
    )
    approved = query_service.reconciliation_service.OpeningControlReconciliationStatus(
        status="approved",
        reconciliation_run_id=uuid.uuid4(),
        pending_control_difference_count=0,
    )
    not_required = (
        query_service.reconciliation_service.OpeningControlReconciliationStatus(
            status="not_required",
            reconciliation_run_id=None,
            pending_control_difference_count=0,
        )
    )
    assert "close" not in query_service._allowed_actions(
        world.db,
        actor=actor,
        graph=graph,
        reconciliation=pending,
    )
    assert "close" in query_service._allowed_actions(
        world.db,
        actor=actor,
        graph=graph,
        reconciliation=approved,
    )
    assert "close" in query_service._allowed_actions(
        world.db,
        actor=actor,
        graph=graph,
        reconciliation=not_required,
    )


def test_regional_and_admin_reads_fail_closed_on_cross_region_scope_graph(world):
    foreign_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-CROSS-OWNER",
        name="跨所有权读取边界",
        location_type="region",
        owner_org_id=world.region_y.id,
        status="active",
    )
    world.db.add(foreign_location)
    world.db.flush()
    task = _draft_task(
        world,
        region=world.region_x,
        location=foreign_location,
        assignee_user_id=world.manager_x.user.id,
        suffix="CROSS",
        scope_owner=world.region_y,
    )
    for actor_name in ("manager_x", "admin"):
        for read in (
            lambda actor_name=actor_name: opening_stocktake_detail(
                world.db,
                actor=world.principals[actor_name],
                task_id=task.id,
            ),
            lambda actor_name=actor_name: list_opening_stocktakes(
                world.db,
                actor=world.principals[actor_name],
                limit=20,
            ),
        ):
            with pytest.raises(OpeningStocktakeReadError) as caught:
                read()
            assert caught.value.status_code == 503
            assert caught.value.code == "opening_stocktake_evidence_invalid"


@pytest.mark.parametrize(
    "drift",
    (
        "inactive_owner",
        "cross_region_owner",
        "cross_region_location_owner",
        "organization_cycle",
        "location_cycle",
    ),
)
def test_descendant_scope_tree_drift_fails_detail_and_list_closed(world, drift):
    descendant_owner = Organization(
        id=uuid.uuid4(),
        code=f"READ-DRIFT-OWNER-{drift}",
        name=f"读取漂移子组织 {drift}",
        parent_id=world.region_x.id,
        org_type="region_company",
        province_code=None,
        status="active",
    )
    organization_cycle_peer = Organization(
        id=uuid.uuid4(),
        code=f"READ-DRIFT-PEER-{drift}",
        name=f"读取漂移循环组织 {drift}",
        parent_id=descendant_owner.id,
        org_type="region_company",
        province_code=None,
        status="active",
    )
    parent_location = StockLocation(
        id=uuid.uuid4(),
        code=f"READ-DRIFT-PARENT-{drift}",
        name=f"读取漂移父库位 {drift}",
        location_type="region",
        owner_org_id=descendant_owner.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    descendant_location = StockLocation(
        id=uuid.uuid4(),
        code=f"READ-DRIFT-LOCATION-{drift}",
        name=f"读取漂移子库位 {drift}",
        location_type="region",
        owner_org_id=descendant_owner.id,
        parent_id=parent_location.id,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(descendant_owner)
    world.db.flush()
    world.db.add_all(
        [organization_cycle_peer, parent_location, descendant_location]
    )
    world.db.flush()
    task = _draft_task(
        world,
        region=world.region_x,
        location=descendant_location,
        assignee_user_id=world.manager_x.user.id,
        suffix=f"DRIFT-{drift}",
        scope_owner=descendant_owner,
    )

    if drift == "inactive_owner":
        descendant_owner.status = "inactive"
    elif drift == "cross_region_owner":
        descendant_owner.parent_id = world.region_y.id
    elif drift == "cross_region_location_owner":
        descendant_location.owner_org_id = world.region_y.id
    elif drift == "organization_cycle":
        descendant_owner.parent_id = organization_cycle_peer.id
    elif drift == "location_cycle":
        parent_location.parent_id = descendant_location.id
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(drift)
    world.db.flush()

    for read in (
        lambda: opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=task.id,
        ),
        lambda: list_opening_stocktakes(
            world.db,
            actor=world.principals["manager_x"],
            limit=20,
        ),
    ):
        with pytest.raises(OpeningStocktakeReadError) as caught:
            read()
        assert caught.value.status_code == 503
        assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_two_regional_assignments_do_not_union_cross_region_scope_for_read(world):
    foreign_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-DUAL-REGION-WH",
        name="双区域授权跨区读取边界",
        location_type="region",
        owner_org_id=world.region_y.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(foreign_location)
    world.db.flush()
    task = _draft_task(
        world,
        region=world.region_x,
        location=foreign_location,
        assignee_user_id=world.manager_x.user.id,
        suffix="DUAL-REGION",
        scope_owner=world.region_y,
    )
    world.db.add(
        RoleAssignment(
            id=uuid.uuid4(),
            user_id=world.manager_x.user.id,
            role_id=world.roles["provincial_manager"].id,
            scope_type="organization",
            scope_id=str(world.region_y.id),
            valid_from=world.manager_x.assignment.valid_from,
            valid_to=None,
            status="active",
            assigned_by=world.admin.user.id,
            revoked_at=None,
            revoked_by=None,
            reason="prove read never unions two regional assignments",
        )
    )
    world.manager_x.user.authorization_version += 1
    world.db.flush()
    dual_region_actor = load_formal_principal(
        world.db,
        world.manager_x.user.id,
        now=NOW + timedelta(hours=1),
    )
    for region in (world.region_x, world.region_y):
        assert dual_region_actor.allows(
            world.db,
            "stocktake",
            "read",
            target_scope_type="organization",
            target_scope_id=str(region.id),
        )

    for read in (
        lambda: opening_stocktake_detail(
            world.db,
            actor=dual_region_actor,
            task_id=task.id,
        ),
        lambda: list_opening_stocktakes(
            world.db,
            actor=dual_region_actor,
            limit=20,
        ),
    ):
        with pytest.raises(OpeningStocktakeReadError) as caught:
            read()
        assert caught.value.status_code == 503
        assert caught.value.code == "opening_stocktake_evidence_invalid"


@pytest.mark.parametrize(
    ("recount_assignee", "technician_can_see"),
    [("manager_x", False), ("technician", True)],
)
def test_technician_visibility_tracks_current_recount_assignment(
    world,
    monkeypatch,
    recount_assignee,
    technician_can_see,
):
    monkeypatch.setattr(
        recount_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=3),
    )
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code=f"READ-RECOUNT-{recount_assignee}",
        name="工程师复盘读取个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    personal_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=world.technician.person.id,
        location_id=personal_location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    world.db.add(personal_location)
    world.db.flush()
    world.db.add_all(
        [
            CustodyAssignment(
                id=uuid.uuid4(),
                location_id=personal_location.id,
                custodian_person_id=world.technician.person.id,
                valid_from=NOW - timedelta(days=30),
                valid_to=None,
                handover_case_id=None,
            ),
            personal_account,
        ]
    )
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=personal_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=1,
        )
    )
    world.db.commit()
    prepared = _prepare_submitted(
        world,
        command=replace(
            world.command,
            task_no=f"OPEN-READ-RECOUNT-{recount_assignee}",
            scopes=(
                OpeningStocktakeScopeInput(
                    owner_org_id=world.region_x.id,
                    location_id=personal_location.id,
                    assignee_user_id=world.technician.user.id,
                    freeze_mode="hard",
                ),
            ),
        ),
        count_actor=world.principals["technician"],
    )
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="读取边界复盘",
        ),
        idempotency_key=f"opening-read-review-{uuid.uuid4().hex}",
        request_id="opening-read-review-request",
    )
    world.db.commit()
    assignee = getattr(world, recount_assignee)
    open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            prepared.task,
            prepared.round,
            prepared.scope,
            assignee.user.id,
        ),
        idempotency_key=f"opening-read-recount-{uuid.uuid4().hex}",
        request_id="opening-read-recount-request",
    )
    world.db.commit()
    technician_page = list_opening_stocktakes(
        world.db,
        actor=world.principals["technician"],
        limit=20,
    )
    assert bool(technician_page.items) is technician_can_see
    if technician_can_see:
        assert technician_page.items[0].task_id == prepared.task.id
        assert technician_page.items[0].current_round_no == 2


def test_blind_counting_detail_never_exposes_book_control_or_quantity(world):
    started = start_opening_stocktake(
        world.db,
        actor=world.principals["manager_x"],
        command=world.command,
        idempotency_key=f"opening-read-start-{uuid.uuid4().hex}",
        request_id="opening-read-start-request",
    )
    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=started.task_id,
    )
    body = detail.model_dump(mode="json")
    assert body["evidence_status"] == "counting_hidden"
    assert body["differences"] == []
    assert body["scopes"][0]["zero_confirmed"] is None
    assert body["scopes"][0]["count_line_count"] is None
    assert body["scopes"][0]["observation_line_count"] is None
    assert body["scopes"][0]["serial_count"] is None
    assert body["scopes"][0]["total_counted_qty"] is None
    rendered = str(body).lower()
    assert "book_qty" not in rendered
    assert "control_qty" not in rendered
    assert "manifest" not in rendered
    assert "sha256" not in rendered
    assert "oam" not in rendered


def test_observations_stay_hidden_while_counting_and_never_cross_visible_scope(world):
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-OBS-PERSONAL",
        name="观察读取个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    personal_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=world.technician.person.id,
        location_id=personal_location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    world.db.add(personal_location)
    world.db.flush()
    world.db.add_all(
        [
            CustodyAssignment(
                id=uuid.uuid4(),
                location_id=personal_location.id,
                custodian_person_id=world.technician.person.id,
                valid_from=NOW - timedelta(days=30),
                valid_to=None,
                handover_case_id=None,
            ),
            personal_account,
        ]
    )
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=personal_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=1,
        )
    )
    world.db.commit()
    command = replace(
        world.command,
        task_no=f"OPEN-READ-OBS-SCOPE-{uuid.uuid4().hex[:8]}",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = start_opening_stocktake(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=f"opening-read-observation-start-{uuid.uuid4().hex}",
        request_id="opening-read-observation-start-request",
    )
    scope_rows = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == started.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    scope_by_location = {row.location_id: row for row in scope_rows}
    region_scope = scope_by_location[world.location.id]
    personal_scope = scope_by_location[personal_location.id]

    partial = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=region_scope.id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw="UNKNOWN-READ-REGION",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                ),
            ),
        ),
        idempotency_key=f"opening-read-observation-region-{uuid.uuid4().hex}",
        request_id="opening-read-observation-region-request",
    )
    assert partial.round_sealed is False
    hidden = opening_stocktake_detail(
        world.db,
        actor=world.principals["technician"],
        task_id=started.task_id,
    )
    assert hidden.evidence_status == "counting_hidden"
    assert hidden.observations == []
    assert hidden.differences == []
    assert "review_region" not in hidden.allowed_actions

    sealed = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["technician"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=personal_scope.id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw="UNKNOWN-READ-PERSONAL",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                ),
            ),
        ),
        idempotency_key=f"opening-read-observation-personal-{uuid.uuid4().hex}",
        request_id="opening-read-observation-personal-request",
    )
    assert sealed.round_sealed is True
    world.db.commit()

    technician = opening_stocktake_detail(
        world.db,
        actor=world.principals["technician"],
        task_id=started.task_id,
    )
    assert [row.scope_id for row in technician.observations] == [personal_scope.id]
    assert [row.material_identifier_raw for row in technician.observations] == [
        "UNKNOWN-READ-PERSONAL"
    ]
    assert all(row.scope_id == personal_scope.id for row in technician.differences)
    assert "review_region" not in technician.allowed_actions
    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=started.task_id,
    )
    assert {row.material_identifier_raw for row in manager.observations} == {
        "UNKNOWN-READ-REGION",
        "UNKNOWN-READ-PERSONAL",
    }


def test_submitted_detail_returns_only_reproved_sealed_differences(world):
    prepared = _prepare_submitted(world)
    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert detail.evidence_status == "sealed"
    assert detail.scopes[0].total_counted_qty == "2.000"
    assert detail.scopes[0].count_line_count is not None
    assert detail.differences
    assert all(row.book_qty.endswith(".000") for row in detail.differences)
    assert all(row.counted_qty.endswith(".000") for row in detail.differences)


def test_pending_observation_is_scope_safe_bound_to_difference_and_role_actions(world):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-PENDING",
        material_identifier_type="unknown",
    )

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert len(manager.observations) == 1
    observation = manager.observations[0]
    assert observation.observation_id == prepared.observation.id
    assert observation.difference_id == prepared.observation_difference.id
    assert observation.scope_id == prepared.scope.id
    assert observation.material_identifier_type == "unknown"
    assert observation.material_identifier_raw == "UNKNOWN-READ-PENDING"
    assert observation.counted_qty == "1.000"
    assert observation.verification_status == "pending_verification"
    assert observation.disposition is None
    assert observation.allowed_dispositions == [
        "pending_verification",
        "requires_recount",
    ]
    assert sum(
        row.difference_id == observation.difference_id
        for row in manager.differences
    ) == 1

    admin = opening_stocktake_detail(
        world.db,
        actor=world.principals["admin"],
        task_id=prepared.task.id,
    )
    assert admin.observations[0].allowed_dispositions == [
        "resolved_existing_master",
        "pending_verification",
        "requires_recount",
    ]


def test_review_action_waits_for_every_pending_observation_disposition_in_detail_and_list(
    world,
):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-ACTION-GATE",
        material_identifier_type="unknown",
    )

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert "review_region" not in manager.allowed_actions
    assert manager.observations[0].allowed_dispositions == [
        "pending_verification",
        "requires_recount",
    ]
    page = list_opening_stocktakes(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
    )
    summary = next(row for row in page.items if row.task_id == prepared.task.id)
    assert "review_region" not in summary.allowed_actions

    admin = opening_stocktake_detail(
        world.db,
        actor=world.principals["admin"],
        task_id=prepared.task.id,
    )
    assert "review_region" not in admin.allowed_actions

    _record_observation_disposition(
        world,
        prepared,
        disposition="requires_recount",
    )
    world.db.commit()

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert manager.observations[0].allowed_dispositions == []
    assert manager.allowed_actions == ["review_region"]
    page = list_opening_stocktakes(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
    )
    summary = next(row for row in page.items if row.task_id == prepared.task.id)
    assert summary.allowed_actions == ["review_region"]


def test_query_reuses_page_audit_proof_for_disposition_replay(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-AUDIT-PROOF",
        material_identifier_type="unknown",
    )
    _record_observation_disposition(
        world,
        prepared,
        disposition="requires_recount",
    )
    world.db.commit()
    audit_calls: list[str] = []
    real_audit = query_service._lock_audit_chain_head_with_proof

    def record_page_audit(*args, **kwargs):
        audit_calls.append("audit")
        return real_audit(*args, **kwargs)

    def forbidden_disposition_audit_lock(*_args, **_kwargs):
        pytest.fail(
            "query disposition replay must reuse the page-wide audit proof"
        )

    monkeypatch.setattr(
        query_service,
        "_lock_audit_chain_head_with_proof",
        record_page_audit,
    )
    monkeypatch.setattr(
        disposition_service,
        "_lock_audit_chain_head_with_proof",
        forbidden_disposition_audit_lock,
    )

    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )

    assert detail.observations[0].disposition is not None
    assert audit_calls == ["audit"]
    planner_source = inspect.getsource(
        query_service._plan_detail_disposition_replays
    )
    replay_source = inspect.getsource(query_service._reprove_detail_evidence)
    assert "_lock_audit_chain_head_with_proof" not in planner_source
    assert "_validate_replay(" not in replay_source
    assert (
        "_validate_opening_observation_disposition_replay_from_prelocked_audit_graph"
        in replay_source
    )


def test_verified_observation_does_not_require_disposition_for_review_action(world):
    material_without_cutoff_account = _material(world.db, world.source)
    prepared = _prepare_observation(
        world,
        material_identifier_raw=material_without_cutoff_account.sku_code,
    )
    assert prepared.observation.verification_status == "verified"

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert manager.observations[0].disposition is None
    assert manager.observations[0].allowed_dispositions == []
    assert manager.allowed_actions == ["review_region"]

    admin = opening_stocktake_detail(
        world.db,
        actor=world.principals["admin"],
        task_id=prepared.task.id,
    )
    assert "review_region" not in admin.allowed_actions


def test_descendant_scope_is_visible_and_keeps_task_wide_review_action_safe(world):
    descendant_owner = Organization(
        id=uuid.uuid4(),
        code="READ-X-DESCENDANT-OWNER",
        name="区域 X 树内子资产组织",
        parent_id=world.region_x.id,
        org_type="region_company",
        province_code=None,
        status="active",
    )
    descendant_owner_location = StockLocation(
        id=uuid.uuid4(),
        code="READ-X-DESCENDANT-OWNER-WH",
        name="区域 X 树内子资产组织物理仓",
        location_type="region",
        owner_org_id=descendant_owner.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(descendant_owner)
    world.db.flush()
    world.db.add(descendant_owner_location)
    world.db.flush()
    command = replace(
        world.command,
        task_no=f"OPEN-READ-HIDDEN-PENDING-{uuid.uuid4().hex[:8]}",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=descendant_owner.id,
                location_id=descendant_owner_location.id,
                assignee_user_id=world.admin.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = start_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=f"opening-read-hidden-start-{uuid.uuid4().hex}",
        request_id="opening-read-hidden-start-request",
    )
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == started.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    scope_by_owner = {row.owner_org_id: row for row in scopes}
    visible_scope = scope_by_owner[world.region_x.id]
    descendant_scope = scope_by_owner[descendant_owner.id]

    for scope, actor_name, raw in (
        (visible_scope, "manager_x", "UNKNOWN-READ-VISIBLE"),
        (descendant_scope, "admin", "UNKNOWN-READ-DESCENDANT"),
    ):
        submit_opening_stocktake_scope_count(
            world.db,
            actor=world.principals[actor_name],
            command=SubmitOpeningStocktakeScopeCountCommand(
                task_id=started.task_id,
                round_id=started.initial_round_id,
                scope_id=scope.id,
                physical_observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=raw,
                        material_identifier_type="unknown",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=Decimal("1.000"),
                    ),
                ),
            ),
            idempotency_key=f"opening-read-hidden-count-{uuid.uuid4().hex}",
            request_id=f"opening-read-hidden-{actor_name}-request",
        )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    visible_observation = world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.task_id == started.task_id,
            StocktakeCountObservation.scope_id == visible_scope.id,
        )
    )
    assert task is not None and round_row is not None
    assert visible_observation is not None
    _record_observation_disposition(
        world,
        SimpleNamespace(
            task=task,
            round=round_row,
            observation=visible_observation,
        ),
        disposition="requires_recount",
    )
    world.db.commit()

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=started.task_id,
    )
    assert [row.material_identifier_raw for row in manager.observations] == [
        "UNKNOWN-READ-VISIBLE",
        "UNKNOWN-READ-DESCENDANT",
    ]
    assert len(manager.scopes) == 2
    observations_by_raw = {
        row.material_identifier_raw: row for row in manager.observations
    }
    assert observations_by_raw["UNKNOWN-READ-VISIBLE"].allowed_dispositions == []
    assert observations_by_raw[
        "UNKNOWN-READ-DESCENDANT"
    ].allowed_dispositions == ["pending_verification", "requires_recount"]
    assert "review_region" not in manager.allowed_actions
    page = list_opening_stocktakes(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
    )
    summary = next(row for row in page.items if row.task_id == started.task_id)
    assert summary.visible_scope_count == 2
    assert "review_region" not in summary.allowed_actions

    admin = opening_stocktake_detail(
        world.db,
        actor=world.principals["admin"],
        task_id=started.task_id,
    )
    descendant = next(
        row
        for row in admin.observations
        if row.material_identifier_raw == "UNKNOWN-READ-DESCENDANT"
    )
    assert descendant.allowed_dispositions == [
        "resolved_existing_master",
        "pending_verification",
        "requires_recount",
    ]

    descendant_observation = world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.task_id == started.task_id,
            StocktakeCountObservation.scope_id == descendant_scope.id,
        )
    )
    assert descendant_observation is not None
    _record_observation_disposition(
        world,
        SimpleNamespace(
            task=task,
            round=round_row,
            observation=descendant_observation,
        ),
        disposition="requires_recount",
    )
    world.db.commit()
    manager_after = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=started.task_id,
    )
    assert manager_after.allowed_actions == ["review_region"]
    assert all(not row.allowed_dispositions for row in manager_after.observations)
    page_after = list_opening_stocktakes(
        world.db,
        actor=world.principals["manager_x"],
        limit=20,
    )
    summary_after = next(
        row for row in page_after.items if row.task_id == started.task_id
    )
    assert summary_after.visible_scope_count == 2
    assert summary_after.allowed_actions == ["review_region"]


def test_current_submitted_recount_observation_keeps_round_aware_actions(
    world,
    monkeypatch,
):
    monkeypatch.setattr(
        recount_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=3),
    )
    prepared = _prepare_submitted(world)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="读取接口复盘观察验证",
        ),
        idempotency_key=f"opening-read-observation-review-{uuid.uuid4().hex}",
        request_id="opening-read-observation-review-request",
    )
    world.db.commit()
    opened = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            prepared.task,
            prepared.round,
            prepared.scope,
            world.manager_x.user.id,
        ),
        idempotency_key=f"opening-read-observation-recount-{uuid.uuid4().hex}",
        request_id="opening-read-observation-recount-request",
    )
    world.db.commit()
    monkeypatch.setattr(
        count_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=4),
    )
    result = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=prepared.task.id,
            round_id=opened.next_round_id,
            scope_id=prepared.scope.id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw="UNKNOWN-READ-RECOUNT",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                ),
            ),
        ),
        idempotency_key=f"opening-read-observation-recount-count-{uuid.uuid4().hex}",
        request_id="opening-read-observation-recount-count-request",
    )
    assert result.round_sealed is True
    world.db.commit()

    manager = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert manager.current_round is not None
    assert manager.current_round.round_no == 2
    assert [row.material_identifier_raw for row in manager.observations] == [
        "UNKNOWN-READ-RECOUNT"
    ]
    assert manager.observations[0].allowed_dispositions == [
        "pending_verification",
        "requires_recount",
    ]
    admin = opening_stocktake_detail(
        world.db,
        actor=world.principals["admin"],
        task_id=prepared.task.id,
    )
    assert admin.observations[0].allowed_dispositions == [
        "resolved_existing_master",
        "pending_verification",
        "requires_recount",
    ]


def test_disposed_observation_returns_only_non_sensitive_reproved_summary(world):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-DISPOSED",
        material_identifier_type="unknown",
    )
    disposition = _record_observation_disposition(
        world,
        prepared,
        disposition="pending_verification",
    )
    world.db.commit()

    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    observation = detail.observations[0]
    assert observation.allowed_dispositions == []
    assert observation.disposition is not None
    assert observation.disposition.disposition_id == disposition.id
    assert observation.disposition.disposition == "pending_verification"
    assert observation.disposition.reason_code == "test_pending_verification"
    body = observation.model_dump(mode="json")
    rendered = str(body).lower()
    assert "sha256" not in rendered
    assert "authorization" not in rendered
    assert "actor" not in rendered
    assert "decided_by" not in rendered
    assert "comment" not in rendered


def test_disposition_manifest_drift_fails_detail_and_list_closed(world):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-DRIFT",
        material_identifier_type="unknown",
    )
    disposition = _record_observation_disposition(
        world,
        prepared,
        disposition="requires_recount",
    )
    world.db.commit()
    disposition = world.db.get(StocktakeObservationDisposition, disposition.id)
    assert disposition is not None
    disposition.disposition_manifest_sha256 = "0" * 64
    world.db.flush()

    for read in (
        lambda: opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=prepared.task.id,
        ),
        lambda: list_opening_stocktakes(
            world.db,
            actor=world.principals["manager_x"],
            limit=20,
        ),
    ):
        with pytest.raises(OpeningStocktakeReadError) as caught:
            read()
        assert caught.value.status_code == 503
        assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_observation_must_bind_exactly_one_difference(world):
    prepared = _prepare_observation(
        world,
        material_identifier_raw="UNKNOWN-READ-DUPLICATE-DIFFERENCE",
        material_identifier_type="unknown",
    )
    unrelated = world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.task_id == prepared.task.id,
            StocktakeDifference.round_id == prepared.round.id,
            StocktakeDifference.observed_line_id.is_(None),
        )
    )
    assert unrelated is not None
    world.db.execute(text("PRAGMA ignore_check_constraints=ON"))
    unrelated.observed_line_id = prepared.observation.id
    world.db.flush()

    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=prepared.task.id,
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_incomplete_sealed_graph_fails_closed_with_503(world):
    prepared = _prepare_submitted(world)
    completion = world.db.scalar(
        select(StocktakeDifferenceSetCompletion).where(
            StocktakeDifferenceSetCompletion.task_id == prepared.task.id,
            StocktakeDifferenceSetCompletion.round_id == prepared.round.id,
        )
    )
    assert completion is not None
    completion.difference_manifest_sha256 = "0" * 64
    world.db.flush()
    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=prepared.task.id,
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_list_reproves_sealed_manifest_and_fails_closed_with_503(world):
    prepared = _prepare_submitted(world)
    completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.task_id == prepared.task.id,
            StocktakeScopeCountCompletion.round_id == prepared.round.id,
        )
    )
    assert completion is not None
    completion.evidence_manifest_sha256 = "f" * 64
    world.db.flush()
    with pytest.raises(OpeningStocktakeReadError) as caught:
        list_opening_stocktakes(
            world.db,
            actor=world.principals["manager_x"],
            limit=20,
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_task_or_ledger_change_during_read_returns_409(world, monkeypatch):
    task = _draft_task(
        world,
        region=world.region_x,
        location=world.location,
        assignee_user_id=world.manager_x.user.id,
        suffix="RACE",
    )
    original = query_service._ensure_snapshot_current

    def change_then_check(db_session, snapshot):
        db_session.execute(
            update(FormalStocktakeTask)
            .where(FormalStocktakeTask.id == task.id)
            .values(version=FormalStocktakeTask.version + 1)
        )
        original(db_session, snapshot)

    monkeypatch.setattr(query_service, "_ensure_snapshot_current", change_then_check)
    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=task.id,
        )
    assert caught.value.status_code == 409
    assert caught.value.code == "opening_stocktake_read_changed"


def test_unknown_persisted_task_status_fails_closed_with_503(world):
    task = _draft_task(
        world,
        region=world.region_x,
        location=world.location,
        assignee_user_id=world.manager_x.user.id,
        suffix="UNKNOWN-STATUS",
    )
    world.db.execute(text("PRAGMA ignore_check_constraints=ON"))
    world.db.execute(
        update(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task.id)
        .values(status="unknown_drift")
    )
    world.db.expire_all()
    with pytest.raises(OpeningStocktakeReadError) as caught:
        opening_stocktake_detail(
            world.db,
            actor=world.principals["manager_x"],
            task_id=task.id,
        )
    assert caught.value.status_code == 503
    assert caught.value.code == "opening_stocktake_evidence_invalid"


def test_list_query_count_is_constant_not_per_task(world):
    for index in range(1, 7):
        region, location = _new_region(world, index)
        _draft_task(
            world,
            region=region,
            location=location,
            assignee_user_id=world.manager_x.user.id,
            suffix=f"N{index}",
        )
    bind = world.db.get_bind()

    def count_for(limit: int) -> tuple[int, int]:
        statements: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", record)
        try:
            result = list_opening_stocktakes(
                world.db, actor=world.principals["admin"], limit=limit
            )
        finally:
            event.remove(bind, "before_cursor_execute", record)
        return len(result.items), len(statements)

    one_item, one_queries = count_for(1)
    six_items, six_queries = count_for(6)
    assert one_item == 1
    assert six_items == 6
    assert six_queries == one_queries
    # The sealed read now performs a fixed page-wide owner/proof graph rather
    # than the former optimistic <=15-query snapshot.  The important bound is
    # still constant with page size: one and six tasks execute the same graph.
    assert six_queries <= 40


def test_submitted_detail_uses_one_page_wide_prelock_then_only_pure_replay(
    world,
    monkeypatch: pytest.MonkeyPatch,
):
    prepared = _prepare_submitted(world)
    events: list[str] = []
    principal_calls: list[tuple[uuid.UUID, ...]] = []
    serial_calls: list[tuple[uuid.UUID, ...]] = []

    real_root = query_service._lock_opening_read_batch_root
    real_principal = query_service.posting_service._lock_opening_task_principal_graph
    real_evidence = query_service.lock_opening_stocktake_task_evidence
    real_serial = query_service.finalize_service._lock_opening_serial_union_graph
    real_reconciliation = (
        query_service.reconciliation_service._lock_opening_control_reconciliation_batch_graph
    )
    real_audit = query_service._lock_audit_chain_head_with_proof
    real_pure = query_service._validate_opening_read_batch_graph

    def record_root(*args, **kwargs):
        result = real_root(*args, **kwargs)
        events.append("ledger-tasks")
        return result

    def record_principal(*args, **kwargs):
        principal_calls.append(tuple(kwargs["task_ids"]))
        events.append("principal")
        return real_principal(*args, **kwargs)

    def record_evidence(*args, **kwargs):
        events.append("evidence")
        return real_evidence(*args, **kwargs)

    def record_serial(*args, **kwargs):
        serial_calls.append(tuple(kwargs["serial_ids"]))
        events.append("serial")
        return real_serial(*args, **kwargs)

    def record_reconciliation(*args, **kwargs):
        events.append("reconciliation")
        return real_reconciliation(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        return real_audit(*args, **kwargs)

    def record_pure(*args, **kwargs):
        events.append("pure")
        return real_pure(*args, **kwargs)

    def forbidden_public_replay(*_args, **_kwargs):
        raise AssertionError("formal read must not enter a public standalone replay")

    monkeypatch.setattr(query_service, "_lock_opening_read_batch_root", record_root)
    monkeypatch.setattr(
        query_service.posting_service,
        "_lock_opening_task_principal_graph",
        record_principal,
    )
    monkeypatch.setattr(
        query_service,
        "lock_opening_stocktake_task_evidence",
        record_evidence,
    )
    monkeypatch.setattr(
        query_service.finalize_service,
        "_lock_opening_serial_union_graph",
        record_serial,
    )
    monkeypatch.setattr(
        query_service.reconciliation_service,
        "_lock_opening_control_reconciliation_batch_graph",
        record_reconciliation,
    )
    monkeypatch.setattr(
        query_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        query_service,
        "_validate_opening_read_batch_graph",
        record_pure,
    )
    monkeypatch.setattr(
        query_service.finalize_service,
        "validate_opening_finalize_evidence_for_replay",
        forbidden_public_replay,
    )
    monkeypatch.setattr(
        query_service.reconciliation_service,
        "opening_control_reconciliation_statuses",
        forbidden_public_replay,
    )

    detail = opening_stocktake_detail(
        world.db,
        actor=world.principals["manager_x"],
        task_id=prepared.task.id,
    )
    assert detail.task_id == prepared.task.id
    assert events == [
        "ledger-tasks",
        "principal",
        "evidence",
        "serial",
        "reconciliation",
        "audit",
        "pure",
    ]
    assert principal_calls == [(prepared.task.id,)]
    assert len(serial_calls) == 1
    assert serial_calls[0] == tuple(sorted(set(serial_calls[0]), key=str))


def test_read_batch_source_preserves_owner_order_and_forbids_standalone_replay():
    root_source = inspect.getsource(query_service._lock_opening_read_batch_root)
    assert root_source.index("InventoryLedgerHead") < root_source.index(
        "FormalStocktakeTask"
    )
    assert root_source.index("FormalStocktakeTask") < root_source.index(
        "_task_read_signature"
    )

    graph_source = inspect.getsource(query_service._lock_opening_read_batch_graph)
    for earlier, later in (
        ("_lock_opening_task_principal_graph", "_plan_opening_terminal_task_batch_graph"),
        ("_plan_opening_terminal_task_batch_graph", "lock_opening_stocktake_task_evidence"),
        ("lock_opening_stocktake_task_evidence", "_lock_opening_serial_union_graph"),
        ("_lock_opening_serial_union_graph", "_lock_opening_control_reconciliation_batch_graph"),
    ):
        assert graph_source.index(earlier) < graph_source.index(later)
    assert graph_source.count("_lock_opening_serial_union_graph") == 1
    assert "lock_inventory_serial_graph" not in graph_source
    assert "_lock_opening_terminal_task_batch_graph" not in graph_source

    replay_source = inspect.getsource(query_service._reprove_detail_evidence)
    status_source = inspect.getsource(query_service._load_reconciliation_statuses)
    assert "validate_opening_finalize_evidence_for_replay" not in replay_source
    assert "validate_opening_review_evidence_for_replay(" not in replay_source
    assert "opening_control_reconciliation_statuses(" not in status_source
    assert "_opening_control_reconciliation_statuses_from_prelocked_batch" in status_source


def test_openapi_methods_are_unique_and_formal_read_router_is_mounted():
    schema = app.openapi()
    path = schema["paths"]["/api/v1/stocktakes/opening"]
    assert {"get", "post"}.issubset(path)
    detail = schema["paths"]["/api/v1/stocktakes/opening/{task_id}"]
    assert set(detail) == {"get"}
    operation_ids = [
        operation["operationId"]
        for methods in schema["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))
