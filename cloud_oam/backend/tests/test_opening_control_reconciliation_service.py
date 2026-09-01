from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, update

import app.formal_services.opening_control_reconciliation as reconciliation_service
import app.formal_services.inventory_posting as posting_service
import app.formal_services.opening_stocktake_finalize as finalize_service
from app.formal_access import load_formal_principal
from app.formal_services.audit_chain import (
    AuditChainStateError,
    calculate_audit_event_hash,
)
from app.formal_services.opening_control_reconciliation import (
    ApproveOpeningControlReconciliationCommand,
    ExplainOpeningControlReconciliationCommand,
    OpeningControlExplanationInput,
    OpeningControlReconciliationError,
    StartOpeningControlReconciliationCommand,
    approve_opening_control_reconciliation,
    explain_opening_control_reconciliation,
    list_opening_control_reconciliations,
    opening_control_reconciliation_detail,
    opening_control_reconciliation_statuses,
    start_opening_control_reconciliation,
)
from app.formal_services.opening_stocktake import (
    OpeningStocktakeScopeInput,
    StartOpeningStocktakeCommand,
)
from app.formal_services.opening_stocktake_finalize import (
    CloseOpeningStocktakeCommand,
    OpeningStocktakeFinalizeError,
    close_posted_opening_stocktake,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    ExternalObject,
    ExternalObjectVersion,
    FileObject,
    OpeningControlReconciliationCommandConsumption,
    OpeningControlReconciliationItem,
    OpeningControlReconciliationRun,
    OutboxEvent,
    Permission,
    ReconciliationCommand,
    RolePermission,
    StateTransitionEvent,
    SyncInboxEvent,
)
from app.inventory_models import StockAccount, StockBalance, StockLocation
from app.stocktake_models import (
    FormalStocktakeTask,
    InventoryOpeningEstablishment,
    StocktakeControlSnapshotLine,
)

from test_opening_stocktake_finalize_service import _approve, _post  # noqa: E402
from test_opening_stocktake_review_service import (  # noqa: E402
    NOW,
    _fixed_database_times,
    _user_with_role,
    db,
    world as review_world,
)


@pytest.fixture(autouse=True)
def _fixed_reconciliation_times(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = count()

    def database_now(_db):
        return NOW + timedelta(hours=3, microseconds=next(ticks) + 1)

    monkeypatch.setattr(finalize_service, "_database_now", database_now)
    monkeypatch.setattr(reconciliation_service, "_database_now", database_now)


@pytest.fixture
def world(review_world: SimpleNamespace) -> SimpleNamespace:
    stocktake_post = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="post_opening",
        field_code="",
        description="正式期初过账与关闭",
    )
    inventory_post = Permission(
        id=uuid.uuid4(),
        resource="inventory_transaction",
        action="post",
        field_code="",
        description="正式库存过账",
    )
    reconciliation_permissions = {
        action: Permission(
            id=uuid.uuid4(),
            resource="reconciliation",
            action=action,
            field_code="",
            description=f"正式期初对账 {action}",
        )
        for action in ("create_opening", "explain_opening", "approve_opening", "read")
    }
    review_world.db.add_all(
        [stocktake_post, inventory_post, *reconciliation_permissions.values()]
    )
    review_world.db.flush()
    review_world.db.add_all(
        [
            RolePermission(
                role_id=review_world.roles["admin"].id,
                permission_id=permission.id,
                effect="allow",
            )
            for permission in (
                stocktake_post,
                inventory_post,
                reconciliation_permissions["create_opening"],
                reconciliation_permissions["approve_opening"],
                reconciliation_permissions["read"],
            )
        ]
        + [
            RolePermission(
                role_id=review_world.roles["provincial_manager"].id,
                permission_id=reconciliation_permissions[action].id,
                effect="allow",
            )
            for action in ("explain_opening", "read")
        ]
    )
    admin_two = _user_with_role(
        review_world.db,
        review_world.hq,
        review_world.roles["admin"],
        "national",
        "*",
        "Admin-Two",
    )
    _align_finalize_chronology(review_world)
    review_world.db.commit()
    review_world.permissions.update(
        {
            "post_opening": stocktake_post,
            "inventory_post": inventory_post,
            **{
                f"reconciliation_{action}": permission
                for action, permission in reconciliation_permissions.items()
            },
        }
    )
    review_world.admin_two = admin_two
    review_world.principals = {
        name: load_formal_principal(
            review_world.db,
            value.user.id,
            now=NOW + timedelta(hours=3),
        )
        for name, value in (
            ("admin", review_world.admin),
            ("admin_two", admin_two),
            ("manager_x", review_world.manager_x),
            ("manager_y", review_world.manager_y),
            ("technician", review_world.technician),
        )
    }
    return review_world


def _align_finalize_chronology(context: SimpleNamespace) -> None:
    context.account.created_at = NOW
    context.account.updated_at = NOW
    balance = context.db.get(StockBalance, context.account.id)
    assert balance is not None
    balance.version = 0
    context.control.sync_run.created_at = context.control.sync_run.started_at
    context.control.batch.created_at = context.control.batch.received_at
    for line in context.control.lines:
        version = context.db.get(
            ExternalObjectVersion,
            line.external_object_version_id,
        )
        assert version is not None
        external = context.db.get(ExternalObject, version.external_object_id)
        inbox = context.db.get(SyncInboxEvent, line.sync_inbox_event_id)
        assert external is not None and inbox is not None
        external.created_at = context.control.sync_run.started_at
        version.created_at = version.valid_from
        inbox.created_at = inbox.source_updated_at


def _new_opening_context(
    world: SimpleNamespace,
    *,
    suffix: str,
) -> SimpleNamespace:
    location = StockLocation(
        id=uuid.uuid4(),
        code=f"REG-X-{suffix}-WH",
        name=f"区域 X 对账仓 {suffix}",
        location_type="region",
        owner_org_id=world.region_x.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=None,
        location_id=location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    world.db.add(location)
    world.db.flush()
    world.db.add(account)
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=0,
        )
    )
    command = StartOpeningStocktakeCommand(
        task_no=f"OPEN-RECON-{suffix}",
        region_org_id=world.region_x.id,
        control_source_system_id=world.source.id,
        control_sync_run_id=world.control.sync_run.id,
        control_sync_scope_key=world.control.sync_run.scope_key,
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
        ),
        control_lines=world.control.lines,
        blind_count=True,
        deadline=NOW + timedelta(days=2),
        note=f"正式期初对账测试 {suffix}",
    )
    context = SimpleNamespace(
        **{
            **world.__dict__,
            "location": location,
            "account": account,
            "command": command,
        }
    )
    _align_finalize_chronology(context)
    world.db.commit()
    return context


def _posted_task(context: SimpleNamespace) -> SimpleNamespace:
    prepared = _approve(context)
    posted = _post(
        context,
        prepared,
        key=f"opening-reconciliation-post-{uuid.uuid4().hex}",
        expected_version=prepared.task.version,
    )
    context.db.commit()
    task = context.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "posted"
    return SimpleNamespace(context=context, prepared=prepared, posted=posted, task=task)


def _start_reconciliation(
    world: SimpleNamespace,
    posted: SimpleNamespace,
    *,
    key: str,
):
    return start_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin"],
        command=StartOpeningControlReconciliationCommand(
            task_id=posted.task.id,
            expected_task_version=posted.task.version,
        ),
        idempotency_key=key,
        request_id=f"{key}-request",
    )


def _explain_command(world: SimpleNamespace, run_id: uuid.UUID, version: int):
    detail = opening_control_reconciliation_detail(
        world.db,
        actor=world.principals["manager_x"],
        reconciliation_run_id=run_id,
    )
    return ExplainOpeningControlReconciliationCommand(
        reconciliation_run_id=run_id,
        expected_version=version,
        items=tuple(
            OpeningControlExplanationInput(
                reconciliation_item_id=row.reconciliation_item_id,
                expected_version=row.version,
                explanation="已核对期初实盘与 OAM 截止快照差异",
                evidence_reference=f"opening-evidence:{row.stocktake_difference_id}",
            )
            for row in detail.items
        ),
    )


def _available_evidence_file(
    world: SimpleNamespace,
    *,
    suffix: str,
    sha256: str = "a" * 64,
    size_bytes: int = 128,
    mime_type: str = "application/pdf",
) -> FileObject:
    file = FileObject(
        id=uuid.uuid4(),
        storage_key=f"opening-reconciliation/{suffix}/{uuid.uuid4().hex}",
        sha256=sha256,
        size_bytes=size_bytes,
        mime_type=mime_type,
        original_filename=f"{suffix}.pdf",
        uploaded_by=world.manager_x.user.id,
        status="available",
        metadata_jsonb={"purpose": "opening_control_reconciliation"},
    )
    world.db.add(file)
    world.db.flush()
    return file


def _explain_with_evidence_file(
    world: SimpleNamespace,
    *,
    reconciliation_run_id: uuid.UUID,
    expected_version: int,
    file: FileObject,
    key: str,
    explanation: str = "已核对期初实盘与 OAM 截止快照差异，并附证据文件",
    evidence_reference: str = "opening-evidence:file-snapshot",
):
    command = _explain_command(world, reconciliation_run_id, expected_version)
    command = replace(
        command,
        items=tuple(
            replace(
                row,
                explanation=explanation,
                evidence_reference=evidence_reference,
                evidence_file_id=file.id,
            )
            for row in command.items
        ),
    )
    return explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id=f"{key}-request",
    )


def _assert_evidence_file_snapshot(
    binding: OpeningControlReconciliationItem,
    file: FileObject,
) -> None:
    assert binding.evidence_file_sha256 == file.sha256
    assert binding.evidence_file_size_bytes == file.size_bytes
    assert binding.evidence_file_mime_type == file.mime_type


def test_create_explain_distinct_hq_approve_then_close(world: SimpleNamespace) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-e2e-create-0001",
    )
    assert started.status == "differences"
    assert started.item_count == 1

    explained = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=_explain_command(world, started.reconciliation_run_id, started.version),
        idempotency_key="opening-reconciliation-e2e-explain-0001",
        request_id="opening-reconciliation-e2e-explain-request",
    )
    detail_for_approver = opening_control_reconciliation_detail(
        world.db,
        actor=world.principals["admin_two"],
        reconciliation_run_id=started.reconciliation_run_id,
    )
    assert detail_for_approver.allowed_actions == ["approve"]
    approved = approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=started.reconciliation_run_id,
            expected_version=explained.version,
            comment="总部复核原因和证据完整，同意核销",
        ),
        idempotency_key="opening-reconciliation-e2e-approve-0001",
        request_id="opening-reconciliation-e2e-approve-request",
    )
    assert approved.status == "approved"
    binding = world.db.get(
        OpeningControlReconciliationRun,
        started.reconciliation_run_id,
    )
    assert binding is not None
    assert binding.created_by_user_id == world.admin.user.id
    assert binding.approved_by_user_id == world.admin_two.user.id
    assert binding.approved_by_user_id != binding.created_by_user_id

    status = opening_control_reconciliation_statuses(
        world.db,
        task_ids=(posted.task.id,),
    )[posted.task.id]
    assert status.status == "approved"
    assert status.reconciliation_run_id == started.reconciliation_run_id
    assert status.pending_control_difference_count == 0
    closed = close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=posted.task.id,
            expected_version=posted.task.version,
        ),
        idempotency_key="opening-reconciliation-e2e-close-0001",
        request_id="opening-reconciliation-e2e-close-request",
    )
    assert closed.resulting_task_status == "closed"
    establishments = world.db.scalars(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.task_id == posted.task.id
        )
    ).all()
    assert establishments and all(
        row.has_pending_control_difference for row in establishments
    )
    assert world.db.get(StockBalance, world.account.id).quantity == Decimal("2.000")


def test_command_replays_conflicts_and_versioned_reexplanation(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    start_command = StartOpeningControlReconciliationCommand(
        task_id=posted.task.id,
        expected_task_version=posted.task.version,
    )
    start_key = "opening-reconciliation-idempotent-create-0001"
    started = start_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin"],
        command=start_command,
        idempotency_key=start_key,
        request_id="opening-reconciliation-idempotent-create-request",
    )
    start_replay = start_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin"],
        command=start_command,
        idempotency_key=start_key,
        request_id="opening-reconciliation-idempotent-create-replay",
    )
    assert start_replay.replayed is True
    assert start_replay.reconciliation_run_id == started.reconciliation_run_id
    with pytest.raises(OpeningControlReconciliationError) as caught:
        start_opening_control_reconciliation(
            world.db,
            actor=world.principals["admin"],
            command=replace(
                start_command,
                expected_task_version=start_command.expected_task_version + 1,
            ),
            idempotency_key=start_key,
            request_id="opening-reconciliation-idempotent-create-conflict",
        )
    assert caught.value.code == "opening_reconciliation_idempotency_conflict"

    first_command = _explain_command(world, started.reconciliation_run_id, 0)
    explain_key = "opening-reconciliation-idempotent-explain-0001"
    first = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=first_command,
        idempotency_key=explain_key,
        request_id="opening-reconciliation-idempotent-explain-request",
    )
    replay = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=first_command,
        idempotency_key=explain_key,
        request_id="opening-reconciliation-idempotent-explain-replay",
    )
    assert replay.replayed is True and replay.version == first.version
    with pytest.raises(OpeningControlReconciliationError) as caught:
        explain_opening_control_reconciliation(
            world.db,
            actor=world.principals["manager_x"],
            command=replace(first_command, expected_version=first.version),
            idempotency_key=explain_key,
            request_id="opening-reconciliation-idempotent-explain-conflict",
        )
    assert caught.value.code == "opening_reconciliation_idempotency_conflict"

    second_command = _explain_command(
        world,
        started.reconciliation_run_id,
        first.version,
    )
    second_command = replace(
        second_command,
        items=tuple(
            replace(row, explanation="补充核对签字记录和差异形成原因")
            for row in second_command.items
        ),
    )
    second = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=second_command,
        idempotency_key="opening-reconciliation-second-explain-0001",
        request_id="opening-reconciliation-second-explain-request",
    )
    assert second.version == first.version + 1
    stale_item_command = _explain_command(
        world,
        started.reconciliation_run_id,
        second.version,
    )
    stale_item_command = replace(
        stale_item_command,
        items=tuple(
            replace(row, expected_version=row.expected_version - 1)
            for row in stale_item_command.items
        ),
    )
    with pytest.raises(OpeningControlReconciliationError) as caught:
        explain_opening_control_reconciliation(
            world.db,
            actor=world.principals["manager_x"],
            command=stale_item_command,
            idempotency_key="opening-reconciliation-stale-item-explain-0001",
            request_id="opening-reconciliation-stale-item-explain-request",
        )
    assert caught.value.code == "opening_reconciliation_item_version_conflict"

    approve_command = ApproveOpeningControlReconciliationCommand(
        reconciliation_run_id=started.reconciliation_run_id,
        expected_version=second.version,
        comment="总部复核解释完整并批准核销",
    )
    approve_key = "opening-reconciliation-idempotent-approve-0001"
    approved = approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=approve_command,
        idempotency_key=approve_key,
        request_id="opening-reconciliation-idempotent-approve-request",
    )
    approve_replay = approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=approve_command,
        idempotency_key=approve_key,
        request_id="opening-reconciliation-idempotent-approve-replay",
    )
    assert approve_replay.replayed is True
    assert approve_replay.version == approved.version
    with pytest.raises(OpeningControlReconciliationError) as caught:
        approve_opening_control_reconciliation(
            world.db,
            actor=world.principals["admin_two"],
            command=replace(
                approve_command,
                comment="同一幂等键不同批准意见",
            ),
            idempotency_key=approve_key,
            request_id="opening-reconciliation-idempotent-approve-conflict",
        )
    assert caught.value.code == "opening_reconciliation_idempotency_conflict"


def test_action_permissions_and_region_scope_fail_closed(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-permission-create-0001",
    )
    explain_command = _explain_command(world, started.reconciliation_run_id, 0)
    for actor_name in ("admin", "manager_y"):
        with pytest.raises(OpeningControlReconciliationError) as caught:
            explain_opening_control_reconciliation(
                world.db,
                actor=world.principals[actor_name],
                command=explain_command,
                idempotency_key=f"opening-reconciliation-forbidden-{actor_name}",
                request_id=f"opening-reconciliation-forbidden-{actor_name}-request",
            )
        assert caught.value.code == "opening_reconciliation_forbidden"
    with pytest.raises(OpeningControlReconciliationError) as caught:
        approve_opening_control_reconciliation(
            world.db,
            actor=world.principals["manager_x"],
            command=ApproveOpeningControlReconciliationCommand(
                reconciliation_run_id=started.reconciliation_run_id,
                expected_version=0,
                comment="无权批准该对账运行",
            ),
            idempotency_key="opening-reconciliation-manager-approve-forbidden",
            request_id="opening-reconciliation-manager-approve-forbidden-request",
        )
    assert caught.value.code == "opening_reconciliation_forbidden"
    assert list_opening_control_reconciliations(
        world.db,
        actor=world.principals["manager_y"],
        limit=20,
    ).items == []
    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["manager_y"],
            reconciliation_run_id=started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_not_found"
    with pytest.raises(OpeningControlReconciliationError) as caught:
        list_opening_control_reconciliations(
            world.db,
            actor=world.principals["technician"],
            limit=20,
        )
    assert caught.value.code == "opening_reconciliation_read_forbidden"


@pytest.mark.parametrize("tamper", ["business_key", "control_qty"])
def test_control_source_fact_tamper_fails_closed(
    world: SimpleNamespace,
    tamper: str,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key=f"opening-reconciliation-tamper-create-{tamper}",
    )
    control = world.db.scalar(
        select(StocktakeControlSnapshotLine).where(
            StocktakeControlSnapshotLine.task_id == posted.task.id
        )
    )
    assert control is not None
    values = (
        {"external_business_key": f"{control.external_business_key}-tampered"}
        if tamper == "business_key"
        else {"control_qty": control.control_qty + Decimal("1.000")}
    )
    world.db.execute(
        update(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.id == control.id)
        .values(**values)
    )
    world.db.expire_all()
    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin"],
            reconciliation_run_id=started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_source_evidence_invalid"
    assert caught.value.http_status_code == 412


def test_reconciliation_list_cursor_pagination_does_not_skip_runs(
    world: SimpleNamespace,
) -> None:
    first_posted = _posted_task(world)
    first = _start_reconciliation(
        world,
        first_posted,
        key="opening-reconciliation-page-create-primary",
    )
    first_explained = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=_explain_command(world, first.reconciliation_run_id, first.version),
        idempotency_key="opening-reconciliation-page-explain-primary",
        request_id="opening-reconciliation-page-explain-primary-request",
    )
    approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=first.reconciliation_run_id,
            expected_version=first_explained.version,
            comment="分页测试首个运行批准关闭",
        ),
        idempotency_key="opening-reconciliation-page-approve-primary",
        request_id="opening-reconciliation-page-approve-primary-request",
    )
    close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=first_posted.task.id,
            expected_version=first_posted.task.version,
        ),
        idempotency_key="opening-reconciliation-page-close-primary",
        request_id="opening-reconciliation-page-close-primary-request",
    )
    second_context = _new_opening_context(world, suffix="PAGE-TWO")
    second_posted = _posted_task(second_context)
    second = _start_reconciliation(
        world,
        second_posted,
        key="opening-reconciliation-page-create-second",
    )
    expected_ids = sorted(
        (first.reconciliation_run_id, second.reconciliation_run_id),
        key=str,
    )
    first_page = list_opening_control_reconciliations(
        world.db,
        actor=world.principals["admin"],
        limit=1,
    )
    assert [row.reconciliation_run_id for row in first_page.items] == [
        expected_ids[0]
    ]
    assert first_page.next_after_id == expected_ids[0]
    second_page = list_opening_control_reconciliations(
        world.db,
        actor=world.principals["admin"],
        limit=1,
        after_id=first_page.next_after_id,
    )
    assert [row.reconciliation_run_id for row in second_page.items] == [
        expected_ids[1]
    ]
    assert second_page.next_after_id is None


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("sha256", "b" * 64),
        ("size_bytes", 256),
        ("mime_type", "image/png"),
    ],
)
def test_evidence_file_metadata_drift_blocks_detail_approve_and_close(
    world: SimpleNamespace,
    changed_field: str,
    changed_value: object,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key=f"opening-reconciliation-evidence-drift-create-{changed_field}",
    )
    file = _available_evidence_file(
        world,
        suffix=f"drift-{changed_field}",
    )
    explained = _explain_with_evidence_file(
        world,
        reconciliation_run_id=started.reconciliation_run_id,
        expected_version=started.version,
        file=file,
        key=f"opening-reconciliation-evidence-drift-explain-{changed_field}",
    )
    binding = world.db.scalar(
        select(OpeningControlReconciliationItem).where(
            OpeningControlReconciliationItem.run_id
            == started.reconciliation_run_id
        )
    )
    assert binding is not None
    _assert_evidence_file_snapshot(binding, file)

    setattr(file, changed_field, changed_value)
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as detail_error:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=started.reconciliation_run_id,
        )
    assert detail_error.value.code == "opening_reconciliation_evidence_invalid"
    assert detail_error.value.http_status_code == 503

    with pytest.raises(OpeningControlReconciliationError) as approve_error:
        approve_opening_control_reconciliation(
            world.db,
            actor=world.principals["admin_two"],
            command=ApproveOpeningControlReconciliationCommand(
                reconciliation_run_id=started.reconciliation_run_id,
                expected_version=explained.version,
                comment="附件快照漂移后不得批准",
            ),
            idempotency_key=(
                f"opening-reconciliation-evidence-drift-approve-{changed_field}"
            ),
            request_id=(
                f"opening-reconciliation-evidence-drift-approve-{changed_field}-request"
            ),
        )
    assert approve_error.value.code == "opening_reconciliation_evidence_invalid"
    assert approve_error.value.http_status_code == 503

    with pytest.raises(OpeningStocktakeFinalizeError) as close_error:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=posted.task.id,
                expected_version=posted.task.version,
            ),
            idempotency_key=(
                f"opening-reconciliation-evidence-drift-close-{changed_field}"
            ),
            request_id=(
                f"opening-reconciliation-evidence-drift-close-{changed_field}-request"
            ),
        )
    assert close_error.value.code == "opening_close_reconciliation_evidence_invalid"
    assert close_error.value.http_status_code == 503
    task = world.db.get(FormalStocktakeTask, posted.task.id)
    assert task is not None and task.status == "posted"


def test_evidence_reference_without_file_remains_valid(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-reference-only-create",
    )
    explained = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=_explain_command(
            world,
            started.reconciliation_run_id,
            started.version,
        ),
        idempotency_key="opening-reconciliation-reference-only-explain",
        request_id="opening-reconciliation-reference-only-explain-request",
    )
    detail = opening_control_reconciliation_detail(
        world.db,
        actor=world.principals["admin_two"],
        reconciliation_run_id=started.reconciliation_run_id,
    )
    assert detail.items
    assert all(row.evidence_reference for row in detail.items)
    assert all(row.evidence_file_id is None for row in detail.items)
    bindings = tuple(
        world.db.scalars(
            select(OpeningControlReconciliationItem).where(
                OpeningControlReconciliationItem.run_id
                == started.reconciliation_run_id
            )
        ).all()
    )
    assert bindings
    assert all(row.evidence_file_sha256 is None for row in bindings)
    assert all(row.evidence_file_size_bytes is None for row in bindings)
    assert all(row.evidence_file_mime_type is None for row in bindings)

    approved = approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=started.reconciliation_run_id,
            expected_version=explained.version,
            comment="仅证据引用已核验，可批准核销",
        ),
        idempotency_key="opening-reconciliation-reference-only-approve",
        request_id="opening-reconciliation-reference-only-approve-request",
    )
    assert approved.status == "approved"


def test_reexplanation_replaces_evidence_file_and_advances_snapshot(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-file-replace-create",
    )
    first_file = _available_evidence_file(
        world,
        suffix="file-replace-first",
        sha256="1" * 64,
        size_bytes=101,
        mime_type="application/pdf",
    )
    first = _explain_with_evidence_file(
        world,
        reconciliation_run_id=started.reconciliation_run_id,
        expected_version=started.version,
        file=first_file,
        key="opening-reconciliation-file-replace-first-explain",
        evidence_reference="opening-evidence:file-replace:first",
    )
    binding = world.db.scalar(
        select(OpeningControlReconciliationItem).where(
            OpeningControlReconciliationItem.run_id
            == started.reconciliation_run_id
        )
    )
    assert binding is not None
    first_item_version = binding.version
    _assert_evidence_file_snapshot(binding, first_file)

    second_file = _available_evidence_file(
        world,
        suffix="file-replace-second",
        sha256="2" * 64,
        size_bytes=202,
        mime_type="image/png",
    )
    second = _explain_with_evidence_file(
        world,
        reconciliation_run_id=started.reconciliation_run_id,
        expected_version=first.version,
        file=second_file,
        key="opening-reconciliation-file-replace-second-explain",
        explanation="重新核对后以替换附件作为当前有效证据",
        evidence_reference="opening-evidence:file-replace:second",
    )
    world.db.expire_all()
    binding = world.db.scalar(
        select(OpeningControlReconciliationItem).where(
            OpeningControlReconciliationItem.run_id
            == started.reconciliation_run_id
        )
    )
    assert binding is not None
    assert second.version == first.version + 1
    assert binding.version == first_item_version + 1
    _assert_evidence_file_snapshot(binding, second_file)

    # The superseded file is no longer part of the current projection proof.
    first_file = world.db.get(FileObject, first_file.id)
    assert first_file is not None
    first_file.sha256 = "3" * 64
    first_file.size_bytes = 303
    first_file.mime_type = "text/plain"
    world.db.flush()
    world.db.expire_all()
    detail = opening_control_reconciliation_detail(
        world.db,
        actor=world.principals["admin_two"],
        reconciliation_run_id=started.reconciliation_run_id,
    )
    assert detail.items
    assert all(row.evidence_file_id == second_file.id for row in detail.items)


def _command_effect_rows(
    world: SimpleNamespace,
    *,
    reconciliation_run_id: uuid.UUID,
    operation: str,
    command_id: uuid.UUID | None = None,
) -> tuple[
    ReconciliationCommand,
    tuple[StateTransitionEvent, ...],
    tuple[OutboxEvent, ...],
    tuple[AuditEvent, ...],
]:
    command_statement = select(ReconciliationCommand).where(
        ReconciliationCommand.run_id == reconciliation_run_id,
        ReconciliationCommand.operation == operation,
    )
    if command_id is not None:
        command_statement = command_statement.where(
            ReconciliationCommand.id == command_id
        )
    command = world.db.scalar(
        command_statement.order_by(
            ReconciliationCommand.occurred_at,
            ReconciliationCommand.id,
        )
    )
    assert command is not None
    event_type = f"reconciliation.opening.{operation.removesuffix('_opening')}"
    states = tuple(
        row
        for row in world.db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.reason == event_type
            )
        ).all()
        if isinstance(row.metadata_jsonb, dict)
        and row.metadata_jsonb.get("reconciliation_run_id")
        == str(reconciliation_run_id)
        and row.metadata_jsonb.get("result_hash") == command.result_hash
    )
    outbox = tuple(
        row
        for row in world.db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == event_type,
                OutboxEvent.aggregate_type == "reconciliation_run",
                OutboxEvent.aggregate_id == str(reconciliation_run_id),
            )
        ).all()
        if isinstance(row.payload_jsonb, dict)
        and row.payload_jsonb.get("result_hash") == command.result_hash
    )
    audits = tuple(
        row
        for row in world.db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == event_type,
                AuditEvent.aggregate_type == "reconciliation_run",
                AuditEvent.aggregate_id == str(reconciliation_run_id),
                AuditEvent.request_id == command.request_reference,
            )
        ).all()
        if isinstance(row.after_jsonb, dict)
        and row.after_jsonb.get("result_hash") == command.result_hash
    )
    return command, states, outbox, audits


def _reseal_inventory_audit_stream(world: SimpleNamespace) -> None:
    """Keep the global chain valid after a test-only semantic corruption."""

    rows = list(
        world.db.scalars(
            select(AuditEvent)
            .where(AuditEvent.stream_key == "inventory")
            .order_by(AuditEvent.stream_version)
        ).all()
    )
    # Move every coordinate out of the way before compacting a deleted row;
    # this avoids transient conflicts with the per-stream unique constraint.
    for row in rows:
        row.stream_version += 1_000_000
    world.db.flush()
    previous_hash: str | None = None
    for stream_version, row in enumerate(rows, start=1):
        row.stream_version = stream_version
        row.previous_hash = previous_hash
        row.event_hash = calculate_audit_event_hash(
            stream_key=row.stream_key,
            event_id=row.id,
            actor_user_id=row.actor_user_id,
            action=row.action,
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            before_jsonb=row.before_jsonb,
            after_jsonb=row.after_jsonb,
            request_id=row.request_id,
            previous_hash=previous_hash,
            occurred_at=row.occurred_at,
        )
        previous_hash = row.event_hash
    world.db.flush()
    head = world.db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "inventory")
    )
    assert head is not None
    head.version = len(rows)
    head.last_event_id = rows[-1].id if rows else None
    head.last_hash = rows[-1].event_hash if rows else None
    world.db.flush()


def _corrupt_create_effect(
    world: SimpleNamespace,
    *,
    reconciliation_run_id: uuid.UUID,
    effect_kind: str,
    mutation: str,
) -> None:
    command, states, outbox, audits = _command_effect_rows(
        world,
        reconciliation_run_id=reconciliation_run_id,
        operation="create_opening",
    )
    assert len(states) == len(outbox) == len(audits) == 1
    target: StateTransitionEvent | OutboxEvent | AuditEvent
    if effect_kind == "state":
        target = states[0]
    elif effect_kind == "outbox":
        target = outbox[0]
    else:
        assert effect_kind == "audit"
        target = audits[0]

    if mutation == "delete":
        world.db.delete(target)
        world.db.flush()
    else:
        assert mutation == "tamper"
        if effect_kind == "state":
            target.metadata_jsonb = {
                **target.metadata_jsonb,
                "result_hash": "0" * 64,
            }
        elif effect_kind == "outbox":
            target.payload_jsonb = {
                **target.payload_jsonb,
                "result_hash": "0" * 64,
            }
        else:
            assert isinstance(target.after_jsonb, dict)
            target.after_jsonb = {
                **target.after_jsonb,
                "result_hash": "0" * 64,
            }
        world.db.flush()
    if effect_kind == "audit":
        # Re-hash the remaining/tampered stream so the ordinary global chain
        # verifier still succeeds.  The reconciliation service must therefore
        # prove the command-to-audit semantic binding itself.
        _reseal_inventory_audit_stream(world)
    world.db.expire_all()
    assert command.id is not None


def _explained_reconciliation(
    world: SimpleNamespace,
    *,
    suffix: str,
) -> SimpleNamespace:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key=f"opening-reconciliation-effect-create-{suffix}",
    )
    explained = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=_explain_command(
            world,
            started.reconciliation_run_id,
            started.version,
        ),
        idempotency_key=f"opening-reconciliation-effect-explain-{suffix}",
        request_id=f"opening-reconciliation-effect-explain-{suffix}-request",
    )
    return SimpleNamespace(posted=posted, started=started, explained=explained)


def _approved_reconciliation(
    world: SimpleNamespace,
    *,
    suffix: str,
) -> SimpleNamespace:
    graph = _explained_reconciliation(world, suffix=suffix)
    approved = approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=graph.started.reconciliation_run_id,
            expected_version=graph.explained.version,
            comment="总部已核验对账副作用证据图",
        ),
        idempotency_key=f"opening-reconciliation-effect-approve-{suffix}",
        request_id=f"opening-reconciliation-effect-approve-{suffix}-request",
    )
    graph.approved = approved
    return graph


def test_create_explain_reexplain_approve_effect_graph_is_exact(
    world: SimpleNamespace,
) -> None:
    graph = _explained_reconciliation(world, suffix="exact")
    second_command = _explain_command(
        world,
        graph.started.reconciliation_run_id,
        graph.explained.version,
    )
    second_command = replace(
        second_command,
        items=tuple(
            replace(row, explanation="补充说明，投影状态未再次变化")
            for row in second_command.items
        ),
    )
    second = explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=second_command,
        idempotency_key="opening-reconciliation-effect-reexplain-exact",
        request_id="opening-reconciliation-effect-reexplain-exact-request",
    )
    approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=graph.started.reconciliation_run_id,
            expected_version=second.version,
            comment="总部复核两版解释后批准核销",
        ),
        idempotency_key="opening-reconciliation-effect-approve-exact",
        request_id="opening-reconciliation-effect-approve-exact-request",
    )
    run_id = graph.started.reconciliation_run_id
    commands = tuple(
        world.db.scalars(
            select(ReconciliationCommand)
            .where(ReconciliationCommand.run_id == run_id)
            .order_by(ReconciliationCommand.occurred_at, ReconciliationCommand.id)
        ).all()
    )
    assert [row.operation for row in commands] == [
        "create_opening",
        "explain_opening",
        "explain_opening",
        "approve_opening",
    ]
    effects = [
        _command_effect_rows(
            world,
            reconciliation_run_id=run_id,
            operation=row.operation,
            command_id=row.id,
        )
        for row in commands
    ]
    assert [len(rows[2]) for rows in effects] == [1, 1, 1, 1]
    assert [len(rows[3]) for rows in effects] == [1, 1, 1, 1]
    assert [len(rows[1]) for rows in effects] == [1, 1, 0, 2]
    create_state = effects[0][1][0]
    assert (
        create_state.aggregate_type,
        create_state.aggregate_id,
        create_state.from_status,
        create_state.to_status,
    ) == ("reconciliation_run", str(run_id), None, "differences")
    first_explain_state = effects[1][1][0]
    assert (
        first_explain_state.aggregate_type,
        first_explain_state.from_status,
        first_explain_state.to_status,
    ) == ("reconciliation_item", "difference", "explained")
    assert {
        (row.aggregate_type, row.from_status, row.to_status)
        for row in effects[3][1]
    } == {
        ("reconciliation_item", "explained", "resolved"),
        ("reconciliation_run", "differences", "approved"),
    }


@pytest.mark.parametrize("effect_kind", ["state", "outbox", "audit"])
@pytest.mark.parametrize("mutation", ["delete", "tamper"])
def test_corrupt_create_effect_blocks_detail_and_approve(
    world: SimpleNamespace,
    effect_kind: str,
    mutation: str,
) -> None:
    suffix = f"preapprove-{effect_kind}-{mutation}"
    graph = _explained_reconciliation(world, suffix=suffix)
    _corrupt_create_effect(
        world,
        reconciliation_run_id=graph.started.reconciliation_run_id,
        effect_kind=effect_kind,
        mutation=mutation,
    )
    with pytest.raises(OpeningControlReconciliationError) as detail_error:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert detail_error.value.code == "opening_reconciliation_evidence_invalid"
    assert detail_error.value.http_status_code == 503

    with pytest.raises(OpeningControlReconciliationError) as approve_error:
        approve_opening_control_reconciliation(
            world.db,
            actor=world.principals["admin_two"],
            command=ApproveOpeningControlReconciliationCommand(
                reconciliation_run_id=graph.started.reconciliation_run_id,
                expected_version=graph.explained.version,
                comment="副作用证据损坏后禁止批准",
            ),
            idempotency_key=f"opening-reconciliation-effect-invalid-{suffix}",
            request_id=f"opening-reconciliation-effect-invalid-{suffix}-request",
        )
    assert approve_error.value.code == "opening_reconciliation_evidence_invalid"
    assert approve_error.value.http_status_code == 503


@pytest.mark.parametrize("effect_kind", ["state", "outbox", "audit"])
@pytest.mark.parametrize("mutation", ["delete", "tamper"])
def test_corrupt_create_effect_blocks_detail_and_close(
    world: SimpleNamespace,
    effect_kind: str,
    mutation: str,
) -> None:
    suffix = f"approved-{effect_kind}-{mutation}"
    graph = _approved_reconciliation(world, suffix=suffix)
    _corrupt_create_effect(
        world,
        reconciliation_run_id=graph.started.reconciliation_run_id,
        effect_kind=effect_kind,
        mutation=mutation,
    )
    with pytest.raises(OpeningControlReconciliationError) as detail_error:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert detail_error.value.code == "opening_reconciliation_evidence_invalid"
    assert detail_error.value.http_status_code == 503

    with pytest.raises(OpeningStocktakeFinalizeError) as close_error:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=graph.posted.task.id,
                expected_version=graph.posted.task.version,
            ),
            idempotency_key=f"opening-reconciliation-effect-close-{suffix}",
            request_id=f"opening-reconciliation-effect-close-{suffix}-request",
        )
    assert close_error.value.code == "opening_close_reconciliation_evidence_invalid"
    assert close_error.value.http_status_code == 503
    task = world.db.get(FormalStocktakeTask, graph.posted.task.id)
    assert task is not None and task.status == "posted"


@pytest.mark.parametrize("approved", [False, True])
def test_missing_command_consumption_seal_fails_closed(
    world: SimpleNamespace,
    approved: bool,
) -> None:
    suffix = f"missing-consumption-{'approved' if approved else 'explained'}"
    graph = (
        _approved_reconciliation(world, suffix=suffix)
        if approved
        else _explained_reconciliation(world, suffix=suffix)
    )
    create_seal = world.db.scalar(
        select(OpeningControlReconciliationCommandConsumption).where(
            OpeningControlReconciliationCommandConsumption.run_id
            == graph.started.reconciliation_run_id,
            OpeningControlReconciliationCommandConsumption.target_version == 0,
        )
    )
    assert create_seal is not None
    world.db.delete(create_seal)
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as detail_error:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert detail_error.value.code == "opening_reconciliation_evidence_invalid"
    assert detail_error.value.http_status_code == 503

    if approved:
        with pytest.raises(OpeningStocktakeFinalizeError) as close_error:
            close_posted_opening_stocktake(
                world.db,
                actor=world.principals["admin"],
                command=CloseOpeningStocktakeCommand(
                    task_id=graph.posted.task.id,
                    expected_version=graph.posted.task.version,
                ),
                idempotency_key=f"opening-reconciliation-seal-close-{suffix}",
                request_id=f"opening-reconciliation-seal-close-{suffix}-request",
            )
        assert close_error.value.code == "opening_close_reconciliation_evidence_invalid"
        assert close_error.value.http_status_code == 503


@pytest.mark.parametrize("document_kind", ["request", "result"])
def test_self_consistent_but_semantically_false_command_document_fails_closed(
    world: SimpleNamespace,
    document_kind: str,
) -> None:
    """A matching hash/effect set must not legitimize a false replay document."""

    graph = _explained_reconciliation(
        world,
        suffix=f"false-command-document-{document_kind}",
    )
    command, states, outboxes, audits = _command_effect_rows(
        world,
        reconciliation_run_id=graph.started.reconciliation_run_id,
        operation="create_opening",
    )
    assert len(states) == len(outboxes) == len(audits) == 1
    if document_kind == "request":
        command.request_jsonb = {
            **command.request_jsonb,
            "schema": "cloud_oam.opening_control_reconciliation.create.forged",
        }
        command.request_hash = reconciliation_service._hash_document(
            command.request_jsonb
        )
    else:
        command.result_jsonb = {
            **command.result_jsonb,
            "item_count": int(command.result_jsonb["item_count"]) + 1,
        }
        command.result_hash = reconciliation_service._hash_document(
            command.result_jsonb
        )
        states[0].metadata_jsonb = {
            **states[0].metadata_jsonb,
            "result_hash": command.result_hash,
        }
        outboxes[0].payload_jsonb = {
            **outboxes[0].payload_jsonb,
            "result": command.result_jsonb,
            "result_hash": command.result_hash,
        }
        audits[0].after_jsonb = {
            **audits[0].after_jsonb,
            "result": command.result_jsonb,
            "result_hash": command.result_hash,
        }
        _reseal_inventory_audit_stream(world)
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503


@pytest.mark.parametrize("operation", ["explain_opening", "approve_opening"])
def test_latest_command_request_must_match_surviving_projection(
    world: SimpleNamespace,
    operation: str,
) -> None:
    graph = (
        _approved_reconciliation(world, suffix="false-approve-request")
        if operation == "approve_opening"
        else _explained_reconciliation(world, suffix="false-explain-request")
    )
    command, _, _, _ = _command_effect_rows(
        world,
        reconciliation_run_id=graph.started.reconciliation_run_id,
        operation=operation,
    )
    if operation == "explain_opening":
        request_items = [dict(row) for row in command.request_jsonb["items"]]
        request_items[0]["explanation"] = "伪造但哈希自洽的历史解释"
        command.request_jsonb = {
            **command.request_jsonb,
            "items": request_items,
        }
    else:
        command.request_jsonb = {
            **command.request_jsonb,
            "comment": "伪造但哈希自洽的批准意见",
        }
    command.request_hash = reconciliation_service._hash_document(
        command.request_jsonb
    )
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503


@pytest.mark.parametrize("closed", [False, True])
def test_create_request_expected_task_version_is_reproved(
    world: SimpleNamespace,
    closed: bool,
) -> None:
    graph = (
        _approved_reconciliation(world, suffix="false-create-task-version-closed")
        if closed
        else _explained_reconciliation(world, suffix="false-create-task-version-posted")
    )
    if closed:
        task = world.db.get(FormalStocktakeTask, graph.posted.task.id)
        assert task is not None
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=task.version,
            ),
            idempotency_key="opening-reconciliation-false-create-version-close",
            request_id="opening-reconciliation-false-create-version-close-request",
        )
    command, _, _, _ = _command_effect_rows(
        world,
        reconciliation_run_id=graph.started.reconciliation_run_id,
        operation="create_opening",
    )
    command.request_jsonb = {
        **command.request_jsonb,
        "expected_task_version": int(
            command.request_jsonb["expected_task_version"]
        )
        + 999,
    }
    command.request_hash = reconciliation_service._hash_document(
        command.request_jsonb
    )
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503


def test_orphan_generic_item_is_not_hidden_by_extension_inner_join(
    world: SimpleNamespace,
) -> None:
    graph = _explained_reconciliation(world, suffix="orphan-generic-item")
    world.db.add(
        reconciliation_service.ReconciliationItem(
            id=uuid.uuid4(),
            run_id=graph.started.reconciliation_run_id,
            business_key="forged-orphan-item",
            external_qty=Decimal("1.000"),
            local_qty=Decimal("0.000"),
            difference=Decimal("1.000"),
            status="difference",
            explanation="",
            evidence_file_id=None,
            created_at=NOW + timedelta(hours=3),
            updated_at=NOW + timedelta(hours=3),
        )
    )
    world.db.flush()
    world.db.expire_all()

    with pytest.raises(OpeningControlReconciliationError) as caught:
        opening_control_reconciliation_detail(
            world.db,
            actor=world.principals["admin_two"],
            reconciliation_run_id=graph.started.reconciliation_run_id,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503


def test_close_cannot_precede_independent_reconciliation_approval(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _approved_reconciliation(world, suffix="close-before-approval")
    task = world.db.get(FormalStocktakeTask, graph.posted.task.id)
    binding = world.db.get(
        OpeningControlReconciliationRun,
        graph.started.reconciliation_run_id,
    )
    assert task is not None and task.posted_at is not None
    assert binding is not None and binding.approved_at is not None
    regressed_now = reconciliation_service._as_utc(
        binding.approved_at
    ) - timedelta(microseconds=1)
    assert regressed_now > reconciliation_service._as_utc(task.posted_at)
    monkeypatch.setattr(finalize_service, "_database_now", lambda _db: regressed_now)

    with pytest.raises(OpeningStocktakeFinalizeError) as caught:
        close_posted_opening_stocktake(
            world.db,
            actor=world.principals["admin"],
            command=CloseOpeningStocktakeCommand(
                task_id=task.id,
                expected_version=task.version,
            ),
            idempotency_key="opening-reconciliation-close-before-approval",
            request_id="opening-reconciliation-close-before-approval-request",
        )
    assert caught.value.code == "opening_close_reconciliation_clock_not_monotonic"
    assert caught.value.http_status_code == 503
    assert task.status == "posted" and task.closed_at is None


def test_create_cannot_precede_independent_opening_posting(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _posted_task(world)
    task = world.db.get(FormalStocktakeTask, posted.task.id)
    assert task is not None and task.posted_at is not None
    regressed_now = reconciliation_service._as_utc(
        task.posted_at
    ) - timedelta(microseconds=1)
    monkeypatch.setattr(
        reconciliation_service,
        "_database_now",
        lambda _db: regressed_now,
    )

    with pytest.raises(OpeningControlReconciliationError) as caught:
        _start_reconciliation(
            world,
            posted,
            key="opening-reconciliation-create-before-posting",
        )
    assert caught.value.code == "opening_reconciliation_create_clock_not_monotonic"
    assert caught.value.http_status_code == 503
    assert world.db.scalar(
        select(OpeningControlReconciliationRun.run_id).where(
            OpeningControlReconciliationRun.task_id == task.id
        )
    ) is None


def test_graph_chronology_rejects_create_time_before_posting(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-read-before-posting",
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )
    graph = reconciliation_service._validate_run_graph(
        world.db,
        started.reconciliation_run_id,
        lock=False,
        audit_proof=audit_proof,
    )
    graph.task.posted_at = reconciliation_service._as_utc(
        graph.binding.created_at
    ) + timedelta(microseconds=1)

    with pytest.raises(OpeningControlReconciliationError) as caught:
        reconciliation_service._validate_graph_chronology(
            run=graph.run,
            binding=graph.binding,
            task=graph.task,
            items=graph.items,
            commands=graph.commands,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503


def test_create_uses_opening_then_reconciliation_then_audit_lock_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _posted_task(world)
    events: list[str] = []
    audit_proofs: list[object] = []
    principal_helper_calls: list[tuple[tuple[uuid.UUID, ...], tuple[str, ...]]] = []
    real_advisory = reconciliation_service._take_advisory_locks
    real_root = reconciliation_service._lock_opening_terminal_root_for_reconciliation
    real_principal = (
        reconciliation_service._lock_opening_principal_graph_for_reconciliation
    )
    real_opening = reconciliation_service._lock_opening_terminal_graph_for_reconciliation
    real_reconciliation = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task
    )
    real_audit = reconciliation_service._lock_audit_chain_head_with_proof
    real_opening_pure = (
        reconciliation_service._validate_prelocked_opening_terminal_for_reconciliation
    )
    real_reconciliation_pure = reconciliation_service._validate_run_graph
    real_union_principal = posting_service._lock_opening_task_principal_graph

    def record_advisory(session, coordinates):
        events.append(f"advisory-{len(coordinates)}")
        return real_advisory(session, coordinates)

    def record_root(session, task_id):
        assert task_id == posted.task.id
        events.append("ledger-task")
        return real_root(session, task_id)

    def record_principal(*args, **kwargs):
        events.append("principal")
        return real_principal(*args, **kwargs)

    def record_union_principal(*args, **kwargs):
        principal_helper_calls.append(
            (tuple(kwargs["task_ids"]), tuple(kwargs["supplied_user_ids"]))
        )
        return real_union_principal(*args, **kwargs)

    def record_opening(*args, **kwargs):
        events.append("opening")
        return real_opening(*args, **kwargs)

    def record_reconciliation(*args, **kwargs):
        events.append("reconciliation")
        return real_reconciliation(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        result = real_audit(*args, **kwargs)
        audit_proofs.append(result[1])
        return result

    def record_opening_pure(*args, **kwargs):
        events.append("opening-pure")
        return real_opening_pure(*args, **kwargs)

    def record_reconciliation_pure(*args, **kwargs):
        assert kwargs["lock"] is False
        assert kwargs["audit_proof"] is audit_proofs[-1]
        events.append("reconciliation-pure")
        return real_reconciliation_pure(*args, **kwargs)

    monkeypatch.setattr(reconciliation_service, "_take_advisory_locks", record_advisory)
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_root_for_reconciliation",
        record_root,
    )
    monkeypatch.setattr(
        posting_service,
        "_lock_opening_task_principal_graph",
        record_union_principal,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_principal_graph_for_reconciliation",
        record_principal,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_graph_for_reconciliation",
        record_opening,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        record_reconciliation,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_prelocked_opening_terminal_for_reconciliation",
        record_opening_pure,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_run_graph",
        record_reconciliation_pure,
    )

    _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-create-lock-order",
    )

    assert events == [
        "advisory-2",
        "ledger-task",
        "principal",
        "opening",
        "reconciliation",
        "audit",
        "opening-pure",
        "reconciliation-pure",
    ]
    assert principal_helper_calls == [
        ((posted.task.id,), (world.principals["admin"].user_id,))
    ]


def test_explain_uses_opening_then_reconciliation_then_audit_lock_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-explain-lock-order-create",
    )
    command = _explain_command(
        world,
        started.reconciliation_run_id,
        started.version,
    )
    events: list[str] = []
    real_advisory = reconciliation_service._take_advisory_locks
    real_root = reconciliation_service._lock_opening_terminal_root_for_reconciliation
    real_principal_lock = (
        reconciliation_service._lock_opening_principal_graph_for_reconciliation
    )
    real_opening = reconciliation_service._lock_opening_terminal_graph_for_reconciliation
    real_reconciliation = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task
    )
    real_audit = reconciliation_service._lock_audit_chain_head_with_proof
    real_opening_pure = (
        reconciliation_service._validate_prelocked_opening_terminal_for_reconciliation
    )
    real_reconciliation_pure = (
        reconciliation_service._validate_run_graph_from_prelocked_reconciliation_graph
    )

    def record_advisory(session, coordinates):
        events.append(f"advisory-{len(coordinates)}")
        return real_advisory(session, coordinates)

    def record_root(session, task_id):
        assert task_id == posted.task.id
        events.append("ledger-task")
        return real_root(session, task_id)

    def record_principal(*args, **kwargs):
        events.append("principal")
        return real_principal_lock(*args, **kwargs)

    def record_opening(*args, **kwargs):
        events.append("opening")
        return real_opening(*args, **kwargs)

    def record_reconciliation(*args, **kwargs):
        events.append("reconciliation")
        return real_reconciliation(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        return real_audit(*args, **kwargs)

    def record_opening_pure(*args, **kwargs):
        events.append("opening-pure")
        return real_opening_pure(*args, **kwargs)

    def record_reconciliation_pure(*args, **kwargs):
        events.append("reconciliation-pure")
        return real_reconciliation_pure(*args, **kwargs)

    monkeypatch.setattr(reconciliation_service, "_take_advisory_locks", record_advisory)
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_root_for_reconciliation",
        record_root,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_principal_graph_for_reconciliation",
        record_principal,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_graph_for_reconciliation",
        record_opening,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        record_reconciliation,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_prelocked_opening_terminal_for_reconciliation",
        record_opening_pure,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_run_graph_from_prelocked_reconciliation_graph",
        record_reconciliation_pure,
    )

    explain_opening_control_reconciliation(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-reconciliation-explain-lock-order",
        request_id="opening-reconciliation-explain-lock-order-request",
    )
    assert events[:9] == [
        "advisory-2",
        "ledger-task",
        "principal",
        "opening",
        "reconciliation",
        "advisory-1",
        "audit",
        "opening-pure",
        "reconciliation-pure",
    ]
    assert not {"ledger-task", "principal", "opening", "reconciliation"}.intersection(
        events[events.index("audit") + 1 :]
    )


def test_approve_uses_opening_then_reconciliation_then_audit_lock_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _explained_reconciliation(world, suffix="approve-lock-order")
    events: list[str] = []
    real_advisory = reconciliation_service._take_advisory_locks
    real_root = reconciliation_service._lock_opening_terminal_root_for_reconciliation
    real_principal_lock = (
        reconciliation_service._lock_opening_principal_graph_for_reconciliation
    )
    real_opening = reconciliation_service._lock_opening_terminal_graph_for_reconciliation
    real_reconciliation = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task
    )
    real_audit = reconciliation_service._lock_audit_chain_head_with_proof
    real_opening_pure = (
        reconciliation_service._validate_prelocked_opening_terminal_for_reconciliation
    )
    real_reconciliation_pure = (
        reconciliation_service._validate_run_graph_from_prelocked_reconciliation_graph
    )

    def record_advisory(session, coordinates):
        events.append(f"advisory-{len(coordinates)}")
        return real_advisory(session, coordinates)

    def record_root(session, task_id):
        assert task_id == graph.posted.task.id
        events.append("ledger-task")
        return real_root(session, task_id)

    def record_principal(*args, **kwargs):
        events.append("principal")
        return real_principal_lock(*args, **kwargs)

    def record_opening(*args, **kwargs):
        events.append("opening")
        return real_opening(*args, **kwargs)

    def record_reconciliation(*args, **kwargs):
        events.append("reconciliation")
        return real_reconciliation(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        return real_audit(*args, **kwargs)

    def record_opening_pure(*args, **kwargs):
        events.append("opening-pure")
        return real_opening_pure(*args, **kwargs)

    def record_reconciliation_pure(*args, **kwargs):
        events.append("reconciliation-pure")
        return real_reconciliation_pure(*args, **kwargs)

    monkeypatch.setattr(reconciliation_service, "_take_advisory_locks", record_advisory)
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_root_for_reconciliation",
        record_root,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_principal_graph_for_reconciliation",
        record_principal,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_terminal_graph_for_reconciliation",
        record_opening,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        record_reconciliation,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_prelocked_opening_terminal_for_reconciliation",
        record_opening_pure,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_run_graph_from_prelocked_reconciliation_graph",
        record_reconciliation_pure,
    )

    approve_opening_control_reconciliation(
        world.db,
        actor=world.principals["admin_two"],
        command=ApproveOpeningControlReconciliationCommand(
            reconciliation_run_id=graph.started.reconciliation_run_id,
            expected_version=graph.explained.version,
            comment="锁序复核通过",
        ),
        idempotency_key="opening-reconciliation-approve-lock-order",
        request_id="opening-reconciliation-approve-lock-order-request",
    )
    assert events[:9] == [
        "advisory-2",
        "ledger-task",
        "principal",
        "opening",
        "reconciliation",
        "advisory-1",
        "audit",
        "opening-pure",
        "reconciliation-pure",
    ]
    assert not {"ledger-task", "principal", "opening", "reconciliation"}.intersection(
        events[events.index("audit") + 1 :]
    )


def test_public_reads_use_opening_then_reconciliation_then_audit_pure_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _posted_task(world)
    started = _start_reconciliation(
        world,
        posted,
        key="opening-reconciliation-read-snapshot-create",
    )
    events: list[str] = []
    real_root = finalize_service._lock_opening_terminal_task_batch_root
    real_principal = posting_service._lock_opening_task_principal_graph
    real_opening = finalize_service._lock_opening_terminal_task_batch_graph
    real_reconciliation = (
        reconciliation_service._lock_opening_control_reconciliation_batch_graph
    )
    real_audit = reconciliation_service._lock_audit_chain_head_with_proof
    real_opening_pure = (
        finalize_service._validate_opening_terminal_batch_from_prelocked_graph
    )
    real_reconciliation_pure = (
        reconciliation_service._validate_opening_control_reconciliation_batch_from_prelocked_graph
    )

    def record(label, function):
        def wrapped(*args, **kwargs):
            events.append(label)
            return function(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(
        finalize_service,
        "_lock_opening_terminal_task_batch_root",
        record("ledger-task", real_root),
    )
    monkeypatch.setattr(
        posting_service,
        "_lock_opening_task_principal_graph",
        record("principal", real_principal),
    )
    monkeypatch.setattr(
        finalize_service,
        "_lock_opening_terminal_task_batch_graph",
        record("opening", real_opening),
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_batch_graph",
        record("reconciliation", real_reconciliation),
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        record("audit", real_audit),
    )
    monkeypatch.setattr(
        finalize_service,
        "_validate_opening_terminal_batch_from_prelocked_graph",
        record("opening-pure", real_opening_pure),
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_validate_opening_control_reconciliation_batch_from_prelocked_graph",
        record("reconciliation-pure", real_reconciliation_pure),
    )

    expected = [
        "ledger-task",
        "principal",
        "opening",
        "reconciliation",
        "audit",
        "opening-pure",
        "reconciliation-pure",
    ]

    opening_control_reconciliation_detail(
        world.db,
        actor=world.principals["admin_two"],
        reconciliation_run_id=started.reconciliation_run_id,
    )
    assert events == expected

    events.clear()
    list_opening_control_reconciliations(
        world.db,
        actor=world.principals["admin_two"],
        limit=20,
    )
    assert events == expected

    events.clear()
    opening_control_reconciliation_statuses(
        world.db,
        task_ids=(posted.task.id,),
    )
    assert events == expected


def test_private_reconciliation_prelock_stops_before_audit_and_orders_owner_graph(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _approved_reconciliation(world, suffix="private-prelock-order")
    calls: list[str] = []
    real_advisory = reconciliation_service._take_advisory_locks
    real_source = reconciliation_service._lock_readonly_source
    real_run = reconciliation_service._lock_readonly_run_graph
    real_files = reconciliation_service._lock_readonly_files

    def record_advisory(*args, **kwargs):
        calls.append("run-advisory")
        return real_advisory(*args, **kwargs)

    def record_source(*args, **kwargs):
        calls.append("source")
        return real_source(*args, **kwargs)

    def record_run(*args, **kwargs):
        calls.append("run")
        return real_run(*args, **kwargs)

    def record_files(*args, **kwargs):
        calls.append("files")
        return real_files(*args, **kwargs)

    def unexpected_audit(*_args, **_kwargs):
        pytest.fail("reconciliation lock-only planner must stop before audit")

    monkeypatch.setattr(
        reconciliation_service,
        "_take_advisory_locks",
        record_advisory,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_source",
        record_source,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_run_graph",
        record_run,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_files",
        record_files,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        unexpected_audit,
    )

    proof = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task(
            world.db,
            task_id=graph.posted.task.id,
            expected_run_id=graph.started.reconciliation_run_id,
        )
    )

    assert proof.run_id == graph.started.reconciliation_run_id
    assert calls == ["run-advisory", "source", "run", "files"]


def test_private_reconciliation_replay_is_pure_after_audit_head(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _approved_reconciliation(world, suffix="private-pure-replay")
    proof = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task(
            world.db,
            task_id=graph.posted.task.id,
            expected_run_id=graph.started.reconciliation_run_id,
        )
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )

    def unexpected_lock(*_args, **_kwargs):
        pytest.fail("prelocked reconciliation replay must not reacquire locks")

    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_source",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_run_graph",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_files",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        unexpected_lock,
    )

    approved = (
        reconciliation_service._require_approved_opening_control_reconciliation_from_prelocked_graph(
            world.db,
            task_id=graph.posted.task.id,
            proof=proof,
            audit_proof=audit_proof,
        )
    )

    assert approved.status == "approved"
    assert approved.reconciliation_run_id == graph.started.reconciliation_run_id


def test_reconciliation_replay_has_no_boolean_or_plain_audit_fallback() -> None:
    source = inspect.getsource(reconciliation_service)
    assert "audit_head_prelocked" not in source
    assert "_verify_audit_event_in_prelocked_stream" not in source
    assert "verify_audit_event_in_stream" not in source
    assert "_require_prelocked_audit_stream_proof" in source
    assert "_verify_audit_event_with_prelocked_proof" in source

    assert tuple(
        inspect.signature(
            reconciliation_service.require_approved_opening_control_reconciliation
        ).parameters
    ) == ("db", "task_id")
    assert "audit_proof" in inspect.signature(
        reconciliation_service._validate_run_graph_from_prelocked_reconciliation_graph
    ).parameters
    assert "audit_proof" in inspect.signature(
        reconciliation_service._validate_opening_control_reconciliation_batch_from_prelocked_graph
    ).parameters
    assert "audit_proof" in inspect.signature(
        reconciliation_service._require_approved_opening_control_reconciliation_from_prelocked_graph
    ).parameters


def test_reconciliation_batch_rejects_audit_proof_from_prior_transaction(
    world: SimpleNamespace,
) -> None:
    graph = _approved_reconciliation(world, suffix="stale-audit-proof")
    task_id = graph.posted.task.id
    historical = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=(task_id,),
        supplied_user_ids=historical,
    )
    batch_proof = (
        reconciliation_service._lock_opening_control_reconciliation_batch_graph(
            world.db,
            task_ids=(task_id,),
            principal_graph=principal_graph,
        )
    )
    _audit_head, stale_audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )
    world.db.commit()
    assert world.db.scalar(select(AuditChainHead).limit(1)) is not None

    with pytest.raises(OpeningControlReconciliationError) as caught:
        reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
            world.db,
            proof=batch_proof,
            audit_proof=stale_audit_proof,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"
    assert caught.value.http_status_code == 503
    assert isinstance(caught.value.__cause__, AuditChainStateError)


def test_reconciliation_principal_union_covers_every_persisted_actor_source(
    world: SimpleNamespace,
) -> None:
    graph = _approved_reconciliation(world, suffix="principal-actor-union")
    task_id = graph.posted.task.id
    reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )

    assert {
        world.principals["admin"].user_id,
        world.principals["manager_x"].user_id,
        world.principals["admin_two"].user_id,
    }.issubset(reconciliation_user_ids)

    reconciliation_service._lock_opening_terminal_root_for_reconciliation(
        world.db,
        task_id,
    )
    proof = (
        reconciliation_service._lock_opening_principal_graph_for_reconciliation(
            world.db,
            task_id,
            supplied_user_ids=(world.principals["manager_y"].user_id,),
        )
    )
    assert proof.historical_reconciliation_user_ids == reconciliation_user_ids
    assert set(reconciliation_user_ids).issubset(
        proof.opening_principal_graph.user_ids
    )
    assert world.principals["manager_y"].user_id in (
        proof.allowed_reconciliation_user_ids
    )


def test_reconciliation_batch_statuses_lock_before_audit_then_reprove_purely(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _approved_reconciliation(
        world,
        suffix="batch-approved",
    )
    close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=approved.posted.task.id,
            expected_version=approved.posted.task.version,
        ),
        idempotency_key="opening-reconciliation-batch-approved-close",
        request_id="opening-reconciliation-batch-approved-close-request",
    )
    world.db.commit()
    pending_context = _new_opening_context(world, suffix="batch-pending-run")
    pending_with_run = _posted_task(pending_context)
    pending_started = _start_reconciliation(
        pending_context,
        pending_with_run,
        key="opening-reconciliation-batch-pending-run",
    )
    task_ids = tuple(
        sorted(
            (
                pending_with_run.task.id,
                approved.posted.task.id,
            ),
            key=str,
        )
    )
    reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=task_ids,
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=task_ids,
        supplied_user_ids=reconciliation_user_ids,
    )
    planned_task_ids: list[uuid.UUID] = []
    real_single_planner = (
        reconciliation_service._lock_opening_control_reconciliation_graph_for_task
    )
    real_audit_lock = reconciliation_service._lock_audit_chain_head_with_proof

    def record_single_planner(*args, **kwargs):
        planned_task_ids.append(kwargs["task_id"])
        return real_single_planner(*args, **kwargs)

    def unexpected_audit(*_args, **_kwargs):
        pytest.fail("reconciliation batch planner must stop before audit")

    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        record_single_planner,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        unexpected_audit,
    )
    proof = reconciliation_service._lock_opening_control_reconciliation_batch_graph(
        world.db,
        task_ids=reversed(task_ids),
        principal_graph=principal_graph,
    )
    assert proof.task_ids == task_ids
    assert planned_task_ids == list(task_ids)
    superset_principal_subset = (
        reconciliation_service._lock_opening_control_reconciliation_batch_graph(
            world.db,
            task_ids=(approved.posted.task.id,),
            principal_graph=principal_graph,
        )
    )
    assert superset_principal_subset.task_ids == (approved.posted.task.id,)

    monkeypatch.setattr(
        reconciliation_service,
        "_lock_opening_control_reconciliation_graph_for_task",
        real_single_planner,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        real_audit_lock,
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )

    def unexpected_lock(*_args, **_kwargs):
        pytest.fail("post-audit reconciliation batch proof must stay plain")

    monkeypatch.setattr(
        reconciliation_service,
        "_take_advisory_locks",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_source",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_run_graph",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_readonly_files",
        unexpected_lock,
    )
    monkeypatch.setattr(
        reconciliation_service,
        "_lock_audit_chain_head_with_proof",
        unexpected_lock,
    )
    statuses = (
        reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
            world.db,
            proof=proof,
            audit_proof=audit_proof,
        )
    )

    assert statuses[pending_with_run.task.id].status == "pending"
    assert statuses[pending_with_run.task.id].reconciliation_run_id == (
        pending_started.reconciliation_run_id
    )
    assert statuses[approved.posted.task.id].status == "approved"
    assert statuses[approved.posted.task.id].reconciliation_run_id == (
        approved.started.reconciliation_run_id
    )


def test_reconciliation_batch_statuses_pending_without_binding(
    world: SimpleNamespace,
) -> None:
    posted = _posted_task(world)
    task_id = posted.task.id
    historical = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=(task_id,),
        supplied_user_ids=historical,
    )
    proof = reconciliation_service._lock_opening_control_reconciliation_batch_graph(
        world.db,
        task_ids=(task_id,),
        principal_graph=principal_graph,
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )
    status = (
        reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
            world.db,
            proof=proof,
            audit_proof=audit_proof,
        )[task_id]
    )
    assert status.status == "pending"
    assert status.reconciliation_run_id is None
    assert status.pending_control_difference_count > 0


def test_closed_reconciliation_batch_rejects_pending_status_at_central_pure_gate(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = _approved_reconciliation(
        world,
        suffix="closed-central-pending",
    )
    close_posted_opening_stocktake(
        world.db,
        actor=world.principals["admin"],
        command=CloseOpeningStocktakeCommand(
            task_id=approved.posted.task.id,
            expected_version=approved.posted.task.version,
        ),
        idempotency_key="opening-reconciliation-closed-central-pending-close",
        request_id="opening-reconciliation-closed-central-pending-close-request",
    )
    task_id = approved.posted.task.id
    historical = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=(task_id,),
        supplied_user_ids=historical,
    )
    proof = reconciliation_service._lock_opening_control_reconciliation_batch_graph(
        world.db,
        task_ids=(task_id,),
        principal_graph=principal_graph,
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )
    real_validate = (
        reconciliation_service._validate_run_graph_from_prelocked_reconciliation_graph
    )

    def return_pending_graph(*args, **kwargs):
        graph = real_validate(*args, **kwargs)
        graph.run.status = "differences"
        return graph

    monkeypatch.setattr(
        reconciliation_service,
        "_validate_run_graph_from_prelocked_reconciliation_graph",
        return_pending_graph,
    )
    with pytest.raises(OpeningControlReconciliationError) as caught:
        reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
            world.db,
            proof=proof,
            audit_proof=audit_proof,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"


@pytest.mark.parametrize("mutation", ["shrink", "phantom"])
def test_reconciliation_batch_actor_seal_rejects_set_drift(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    graph = _approved_reconciliation(world, suffix=f"actor-{mutation}")
    task_id = graph.posted.task.id
    historical = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=(task_id,),
        supplied_user_ids=historical,
    )
    proof = reconciliation_service._lock_opening_control_reconciliation_batch_graph(
        world.db,
        task_ids=(task_id,),
        principal_graph=principal_graph,
    )
    _audit_head, audit_proof = (
        reconciliation_service._lock_audit_chain_head_with_proof(
            world.db,
            stream_key=reconciliation_service.INVENTORY_STREAM_KEY,
        )
    )
    real_actor_map = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids_by_task
    )

    def drifted_actor_map(*args, **kwargs):
        values = real_actor_map(*args, **kwargs)
        if mutation == "shrink":
            values[task_id] = values[task_id][1:]
        else:
            values[task_id] = tuple(
                sorted((*values[task_id], "phantom-reconciliation-actor"))
            )
        return values

    monkeypatch.setattr(
        reconciliation_service,
        "_opening_control_reconciliation_historical_user_ids_by_task",
        drifted_actor_map,
    )
    with pytest.raises(OpeningControlReconciliationError) as caught:
        reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
            world.db,
            proof=proof,
            audit_proof=audit_proof,
        )
    assert caught.value.code == "opening_reconciliation_evidence_invalid"


def test_reconciliation_batch_rejects_incomplete_principal_union(
    world: SimpleNamespace,
) -> None:
    graph = _approved_reconciliation(world, suffix="incomplete-principal")
    task_id = graph.posted.task.id
    historical = set(
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            world.db,
            task_ids=(task_id,),
        )
    )
    incomplete = posting_service._lock_opening_task_principal_graph(
        world.db,
        task_ids=(task_id,),
    )
    assert not historical.issubset(incomplete.user_ids)

    with pytest.raises(OpeningControlReconciliationError) as caught:
        reconciliation_service._lock_opening_control_reconciliation_batch_graph(
            world.db,
            task_ids=(task_id,),
            principal_graph=incomplete,
        )
    assert caught.value.code == (
        "opening_reconciliation_prelocked_principal_graph_incomplete"
    )
