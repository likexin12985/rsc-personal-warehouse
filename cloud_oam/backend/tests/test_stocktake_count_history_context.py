"""Read-only count receipts after real multi-round approval and release.

All setup is an isolated SQLite business chain, never a production fixture or
an artificial task-status rewrite. PostgreSQL role/lock coverage lives in the
separate disposable release gate.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_access import load_formal_principal
from app.formal_services import (
    stocktake_close, stocktake_count, stocktake_posting, stocktake_review,
    stocktake_recount_count,
)
from app.formal_services import stocktake_count_command_status as service
from app.foundation_models import AuditEvent, FileObject, Permission, Role, RolePermission, StateTransitionEvent
from app.inventory_models import CustodyAssignment
from app.stocktake_models import (
    FormalStocktakeScope, FormalStocktakeTask, InventoryFreeze, StocktakeRound,
    StocktakePostingCompletion,
)
from app.stocktake_task_schemas import StocktakeScopeSelectionIn, StocktakeTaskCreateIn
from test_stocktake_task_service import (  # noqa: F401
    NOW, SECRET, db, world, _create_managed, _start,
)
from test_stocktake_review_recount_service import (  # noqa: F401
    review_world, _fixed_clocks, _command, _region,
)
from test_stocktake_recount_execution_service import (  # noqa: F401
    recount_world, _recount_clocks, _open_recount, _count_recount,
)
from test_stocktake_difference_service import _evaluate
from test_stocktake_safe_posting_service import posting_world, _post  # noqa: F401
from test_stocktake_close_service import (  # noqa: F401
    close_world, _reconcile, _close, _install_sqlite_close_guards,
)
from test_stocktake_count_command_status import _three_round_history, _lookup, _assert_blocked


@pytest.fixture
def history_world(recount_world, close_world, monkeypatch):
    assert recount_world is close_world
    read = close_world.db.scalar(select(Permission).where(
        Permission.resource == "stocktake", Permission.action == "read"))
    manager = close_world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
    close_world.db.add(RolePermission(
        id=uuid.uuid4(), role_id=manager.id, permission_id=read.id, effect="allow"))
    close_world.db.commit()
    close_world.principals["manager_x"] = load_formal_principal(
        close_world.db, close_world.manager_x.user.id, now=NOW)
    monkeypatch.setattr(service, "_database_now", lambda _db: NOW + timedelta(days=1))
    return close_world


def _terminal_history(world, monkeypatch, *, stage):
    targets = _three_round_history(world, monkeypatch, stage="third_evaluated")
    task = world.db.get(FormalStocktakeTask, targets[0].task_id)
    final_round = world.db.get(StocktakeRound, targets[-1].round_id)
    monkeypatch.setattr(stocktake_review, "_database_now", lambda _db: NOW + timedelta(minutes=55))
    _region(world, task, final_round, _command(
        world, task, final_round, decision="approve", item_decision="no_adjustment"),
        key="history-terminal-region")
    monkeypatch.setattr(stocktake_review, "_database_now", lambda _db: NOW + timedelta(minutes=60))
    stocktake_review.submit_stocktake_headquarters_review(
        world.db, actor=world.principals["admin"], command=replace(_command(
            world, task, final_round, decision="approve", item_decision="no_adjustment"),
            effective_scope_ids=(targets[0].scope_id,)),
        idempotency_key="history-terminal-hq", idempotency_hmac_secret=SECRET,
        trace_request_id="trace-history-terminal-hq")
    if stage in {"posted", "closed"}:
        monkeypatch.setattr(stocktake_posting, "_database_now", lambda _db: NOW + timedelta(minutes=65))
        _post(world, task, key="history-terminal-post")
    if stage == "closed":
        _install_sqlite_close_guards(world)
        monkeypatch.setattr(stocktake_close, "_database_now", lambda _db: NOW + timedelta(minutes=70))
        _reconcile(world, task, key="history-terminal-reconcile")
        monkeypatch.setattr(stocktake_close, "_database_now", lambda _db: NOW + timedelta(minutes=75))
        _close(world, task, key="history-terminal-close")
    world.db.commit()
    assert task.status == stage
    assert not world.db.execute(text("PRAGMA foreign_key_check")).all()
    freezes = tuple(world.db.scalars(select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)))
    assert freezes and all(row.status == ("active" if stage == "approved" else "released") for row in freezes)
    return targets


def _durable_rows(db):
    """Full values of every mapped table, including composite primary keys."""
    return tuple((table.name, tuple(db.execute(select(*table.c).order_by(
        *table.primary_key.columns)).all())) for table in sorted(
            Base.metadata.tables.values(), key=lambda row: row.name))


@pytest.mark.parametrize("stage", ["approved", "posted", "closed"])
def test_all_round_receipts_survive_real_terminal_chain_without_writes(history_world, monkeypatch, stage):
    targets = _terminal_history(history_world, monkeypatch, stage=stage)
    before = _durable_rows(history_world.db)
    transaction = history_world.db.get_transaction()
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    engine = history_world.db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for method in ("flush", "commit", "rollback"):
            monkeypatch.setattr(history_world.db, method,
                                lambda *_a, **_k: pytest.fail("historical GET changed caller transaction"))
        for target in targets:
            result = _lookup(history_world, target, operation=target.operation)
            assert result.lookup_status == "confirmed"
            assert result.command.round_no == target.round_no
            assert result.task_id == target.task_id and result.round_id == target.round_id
            assert _lookup(history_world, target, operation=target.operation,
                           trace_request_id="trace-terminal-never-sent").lookup_status == "not_observed"
        assert _durable_rows(history_world.db) == before
        assert history_world.db.get_transaction() is transaction
        assert not history_world.db.new and not history_world.db.dirty and not history_world.db.deleted
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("damage", [
    "no_completion", "release_reason", "release_actor", "release_time",
    "posting_manifest", "missing_event", "event_metadata", "missing_audit", "audit_hash",
])
def test_released_freeze_requires_full_posting_source_for_seen_and_unseen_trace(history_world, monkeypatch, damage):
    world = history_world
    targets = _terminal_history(world, monkeypatch, stage="posted")
    freeze = world.db.scalar(select(InventoryFreeze).where(InventoryFreeze.task_id == targets[0].task_id))
    completion = world.db.scalar(select(StocktakePostingCompletion).where(
        StocktakePostingCompletion.task_id == targets[0].task_id))
    posting_event = world.db.scalar(select(StateTransitionEvent).where(
        StateTransitionEvent.aggregate_id == str(targets[0].task_id),
        StateTransitionEvent.reason == "nonopening_stocktake_difference_posted"))
    audit = world.db.scalar(select(AuditEvent).where(
        AuditEvent.action == "stocktake.nonopening.difference_posted",
        AuditEvent.aggregate_id == str(completion.id)))
    assert freeze is not None and completion is not None and posting_event is not None and audit is not None
    if damage == "no_completion":
        world.db.delete(completion)
    elif damage == "release_reason":
        freeze.release_reason = "unverified manual release"
    elif damage == "release_actor":
        freeze.released_by_user_id = world.manager_x.user.id
    elif damage == "release_time":
        freeze.valid_to = NOW + timedelta(minutes=1)  # before R2/R3 counted.
    elif damage == "posting_manifest":
        completion.posting_manifest_sha256 = "0" * 64
    elif damage == "missing_event":
        world.db.delete(posting_event)
    elif damage == "event_metadata":
        posting_event.metadata_jsonb = {**posting_event.metadata_jsonb, "scope_count": 99}
    elif damage == "missing_audit":
        # Keep the chain-head foreign key intact while removing the exact
        # required action binding; there is no posting audit candidate left.
        audit.action = "synthetic.unrelated"
    else:
        audit.event_hash = "0" * 64
    world.db.commit()
    before = _durable_rows(world.db)
    for target in targets[1:]:
        _assert_blocked(world, target, operation=target.operation, statuses=(503,))
        _assert_blocked(world, target, operation=target.operation,
                        trace_request_id="trace-damaged-release-never-sent", statuses=(503,))
    assert _durable_rows(world.db) == before


def test_active_task_cannot_forge_released_freeze_without_posting(history_world, monkeypatch):
    world = history_world
    targets = _terminal_history(world, monkeypatch, stage="approved")
    freeze = world.db.scalar(select(InventoryFreeze).where(InventoryFreeze.task_id == targets[0].task_id))
    freeze.status = "released"
    freeze.valid_to = NOW + timedelta(minutes=65)
    freeze.released_by_user_id = world.admin.user.id
    freeze.release_reason = stocktake_posting.FREEZE_RELEASE_REASON
    freeze.version += 1
    world.db.commit()
    for target in targets[1:]:
        _assert_blocked(world, target, operation=target.operation, statuses=(503,))
        _assert_blocked(world, target, operation=target.operation,
                        trace_request_id="trace-forged-release-never-sent", statuses=(503,))


@pytest.mark.parametrize("damage", ["count_permission", "authorization_version", "target_custody"])
def test_terminal_history_still_requires_current_target_authorization(history_world, monkeypatch, damage):
    world = history_world
    targets = _terminal_history(world, monkeypatch, stage="closed")
    if damage == "count_permission":
        role = world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
        permission = world.db.scalar(select(Permission).where(
            Permission.resource == "stocktake", Permission.action == "count"))
        mapping = world.db.scalar(select(RolePermission).where(
            RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
        world.db.delete(mapping)
    elif damage == "authorization_version":
        world.manager_x.user.authorization_version += 1
    else:
        prior = world.db.scalar(select(CustodyAssignment).where(
            CustodyAssignment.location_id == world.region_location.id,
            CustodyAssignment.valid_to.is_(None)))
        prior.valid_to = NOW + timedelta(hours=2)
        world.region_location.custodian_person_id = world.technician.person.id
        world.db.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=world.region_location.id,
            custodian_person_id=world.technician.person.id, valid_from=prior.valid_to,
            valid_to=None, handover_case_id=None))
    world.db.commit()
    for target in targets[1:]:
        expected = (403,) if damage == "count_permission" else (412,)
        _assert_blocked(world, target, operation=target.operation, statuses=expected)
        _assert_blocked(world, target, operation=target.operation,
                        trace_request_id="trace-revoked-target-never-sent", statuses=expected)


@pytest.mark.parametrize("source_action", ["stocktake.scope_count.submitted", "stocktake.recount_scope_count.submitted"])
def test_terminal_history_keeps_ancestor_audit_chain_proof(history_world, monkeypatch, source_action):
    world = history_world
    targets = _terminal_history(world, monkeypatch, stage="closed")
    source = world.db.scalar(select(AuditEvent).where(
        AuditEvent.action == source_action).order_by(AuditEvent.stream_version))
    assert source is not None
    source.event_hash = "0" * 64
    world.db.commit()
    _assert_blocked(world, targets[2], operation="recount_count", statuses=(503,))
    _assert_blocked(world, targets[2], operation="recount_count",
                    trace_request_id="trace-broken-ancestor-never-sent", statuses=(503,))


def test_original_recount_source_validation_without_history_still_rejects_release(history_world, monkeypatch):
    world = history_world
    targets = _terminal_history(world, monkeypatch, stage="posted")
    task = world.db.get(FormalStocktakeTask, targets[1].task_id)
    round_row = world.db.get(StocktakeRound, targets[1].round_id)
    scopes = tuple(world.db.scalars(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id)))
    with pytest.raises((stocktake_review.StocktakeReviewError,
                        stocktake_recount_count.StocktakeRecountCountError)):
        stocktake_recount_count._load_and_validate_recount_assignment_graph(
            world.db, task=task, round_row=round_row, scopes=scopes)


def _unrelated_person_history(world):
    """Two true initial scopes; only the manager's discrepant scope is recounted."""
    key = "history-unrelated-person"
    created = _create_managed(world, key=f"{key}-create", draft=StocktakeTaskCreateIn(
        task_type="sample", region_org_id=world.region_x.id, blind_count=True,
        scopes=tuple(StocktakeScopeSelectionIn(
            owner_org_id=world.region_x.id, location_id=location.id,
            assignee_person_id=person.id, scope_mode="filtered",
            material_id=world.material_a.id, condition_code="new", freeze_mode="hard",
        ) for location, person in (
            (world.region_location, world.manager_x.person),
            (world.personal_location, world.technician.person),
        )), deadline=NOW + timedelta(days=2), note="历史非选中范围人员变更回归"))
    started = _start(world, created.task_id, key=f"{key}-start")
    scopes = {row.location_id: row for row in world.db.scalars(select(FormalStocktakeScope).where(
        FormalStocktakeScope.task_id == created.task_id))}
    for name, location, account, quantity in (
        ("manager_x", world.region_location, world.region_new, Decimal("4.000")),
        ("technician", world.personal_location, world.personal_account, Decimal("3.000")),
    ):
        stocktake_count.submit_stocktake_initial_scope_count(
            world.db, actor=world.principals[name],
            command=stocktake_count.SubmitStocktakeInitialScopeCountCommand(
                task_id=created.task_id, round_id=started.initial_round_id,
                scope_id=scopes[location.id].id, count_mode="blind",
                account_counts=(stocktake_count.StocktakeSnapshotCountInput(
                    stock_account_id=account.id, counted_qty=quantity),)),
            idempotency_key=f"{key}-initial-{name}", idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-{key}-initial-{name}")
    task = world.db.get(FormalStocktakeTask, created.task_id)
    initial = world.db.get(StocktakeRound, started.initial_round_id)
    _evaluate(world, task, initial, key=f"{key}-evaluate")
    difference, _opened, round2 = _open_recount(world, task, initial, key=f"{key}-r2")
    _count_recount(world, task, round2, difference.scope_id,
                   key=f"{key}-r2", counted_qty=Decimal("4.000"))
    world.db.commit()
    return SimpleNamespace(task_id=task.id, round_id=round2.id, scope_id=difference.scope_id,
                           trace=f"trace-{key}-r2-count")


@pytest.mark.parametrize("change", ["person_left", "user_disabled", "custody_changed"])
def test_unselected_historical_person_changes_do_not_revoke_current_target(history_world, change):
    world = history_world
    target = _unrelated_person_history(world)
    assert _lookup(world, target, operation="recount_count").lookup_status == "confirmed"
    if change == "person_left":
        world.technician.person.employment_status = "left"
    elif change == "user_disabled":
        world.technician.user.account_status = "disabled"
    else:
        prior = world.db.scalar(select(CustodyAssignment).where(
            CustodyAssignment.location_id == world.personal_location.id,
            CustodyAssignment.valid_to.is_(None)))
        prior.valid_to = NOW + timedelta(hours=2)
        world.personal_location.custodian_person_id = world.manager_x.person.id
        world.db.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=world.personal_location.id,
            custodian_person_id=world.manager_x.person.id, valid_from=prior.valid_to,
            valid_to=None, handover_case_id=None))
    world.db.commit()
    before = _durable_rows(world.db)
    task = world.db.get(FormalStocktakeTask, target.task_id)
    round_row = world.db.get(StocktakeRound, target.round_id)
    scopes = tuple(world.db.scalars(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id)))
    # The special read context must not weaken the writer's default live-source
    # validation after the old person's eligibility or custody has changed.
    with pytest.raises((stocktake_review.StocktakeReviewError,
                        stocktake_recount_count.StocktakeRecountCountError)):
        stocktake_recount_count._load_and_validate_recount_assignment_graph(
            world.db, task=task, round_row=round_row, scopes=scopes)
    assert _lookup(world, target, operation="recount_count").lookup_status == "confirmed"
    assert _lookup(world, target, operation="recount_count",
                   trace_request_id="trace-unrelated-person-never-sent").lookup_status == "not_observed"
    assert _durable_rows(world.db) == before


@pytest.mark.parametrize("change", [
    "seal", "session", "rollback", "task", "round", "scope", "unknown_file", "frozen_fields",
])
def test_history_context_is_internal_immutable_and_transaction_bound(history_world, monkeypatch, change):
    world = history_world
    targets = _three_round_history(world, monkeypatch)
    original = service._make_history_context
    captured = []
    def capture_context(*args, **kwargs):
        context = original(*args, **kwargs)
        captured.append(context)
        return context
    monkeypatch.setattr(service, "_make_history_context", capture_context)
    assert _lookup(world, targets[2], operation="recount_count").lookup_status == "confirmed"
    assert len(captured) == 1
    context = captured[0]
    task = world.db.get(FormalStocktakeTask, targets[2].task_id)
    round_row = world.db.get(StocktakeRound, targets[2].round_id)
    scope = world.db.get(FormalStocktakeScope, targets[2].scope_id)
    context.require(world.db, task, round_row, (scope,))
    assert context.files_for(world.db, task, round_row, ()) == ()
    if change == "frozen_fields":
        with pytest.raises(FrozenInstanceError):
            context.task_id = uuid.uuid4()
        return
    if change == "session":
        with Session(world.db.get_bind()) as another:
            with pytest.raises(ValueError):
                context.require(another, task, round_row, (scope,))
        return
    if change == "rollback":
        world.db.rollback()
        world.db.execute(select(FormalStocktakeTask.id).limit(1))
        assert world.db.get_transaction() is not context._transaction
    elif change == "seal":
        context = replace(context, _seal=object())
    elif change == "task":
        task = SimpleNamespace(id=uuid.uuid4())
    elif change == "round":
        round_row = SimpleNamespace(id=uuid.uuid4(), task_id=task.id)
    elif change == "scope":
        scope = SimpleNamespace(id=uuid.uuid4(), task_id=task.id)
    elif change == "unknown_file":
        with pytest.raises(ValueError):
            context.files_for(world.db, task, round_row, (uuid.uuid4(),))
        return
    with pytest.raises(ValueError):
        context.require(world.db, task, round_row, (scope,))


def test_recursive_history_reads_one_full_file_owner_union_before_sources(history_world, monkeypatch):
    """SQL-shape proof with real per-round attachments, not a PG race claim."""
    from app.formal_services import formal_files
    import test_stocktake_difference_service as initial_helpers
    import test_stocktake_recount_execution_service as recount_helpers

    world = history_world
    uploader = world.principals["manager_x"]
    files = []
    # Ancestor traversal is opposite the global UUID lock order.
    for number in (3, 2, 1):
        file_id = uuid.UUID(int=number)
        storage_key = f"formal-files/v1/stocktake_evidence/{file_id.hex[:2]}/{file_id.hex}"
        filename = f"history-round-{number}.jpg"
        row = FileObject(
            id=file_id, storage_key=storage_key, sha256="a" * 64,
            size_bytes=128, mime_type="image/jpeg", original_filename=filename,
            uploaded_by=uploader.user_id, status="available", created_at=NOW,
            metadata_jsonb={
                "authorization_version": uploader.authorization_version,
                "file_id": str(file_id), "idempotency_key_hash": f"{number:064x}",
                "provider": "test_formal_storage", "purpose": "stocktake_evidence",
                "request_sha256": formal_files._upload_request_hash(formal_files._PreparedUpload(
                    purpose="stocktake_evidence", original_filename=filename,
                    size_bytes=128, mime_type="image/jpeg", sha256="a" * 64)),
                "schema": "cloud_oam.formal_file_upload_intent.v1", "storage_key": storage_key,
                "uploader_person_id": str(uploader.person_id), "uploader_user_id": uploader.user_id,
                "completion": {"etag_sha256": "d" * 64, "head_manifest_sha256": "e" * 64,
                               "verified_at": NOW.isoformat()},
            })
        world.db.add(row)
        files.append(row)
    world.db.commit()
    initial_submit = initial_helpers.submit_stocktake_initial_scope_count
    recount_submit = recount_helpers.submit_stocktake_recount_scope_count
    def initial_with_file(db, **kwargs):
        return initial_submit(db, **{**kwargs, "command": replace(
            kwargs["command"], evidence_file_ids=(files[0].id,))})
    def recount_with_file(db, **kwargs):
        round_no = db.get(StocktakeRound, kwargs["command"].round_id).round_no
        return recount_submit(db, **{**kwargs, "command": replace(
            kwargs["command"], evidence_file_ids=(files[round_no - 1].id,))})
    monkeypatch.setattr(initial_helpers, "submit_stocktake_initial_scope_count", initial_with_file)
    monkeypatch.setattr(recount_helpers, "submit_stocktake_recount_scope_count", recount_with_file)
    targets = _three_round_history(world, monkeypatch)
    file_locks = []
    def capture(_conn, clause, _multiparams, _params, _execution_options):
        if getattr(clause, "_for_update_arg", None) is None or not hasattr(clause, "get_final_froms"):
            return
        if FileObject.__table__ not in clause.get_final_froms():
            return
        values = tuple(value for parameter in clause.compile().params.values()
                       for value in (parameter if isinstance(parameter, (tuple, list)) else (parameter,)))
        file_locks.append(tuple(sorted((value for value in values if isinstance(value, uuid.UUID)), key=str)))
    engine = world.db.get_bind()
    event.listen(engine, "before_execute", capture)
    try:
        assert _lookup(world, targets[2], operation="recount_count").lookup_status == "confirmed"
        assert file_locks == [tuple(sorted((row.id for row in files), key=str))]
    finally:
        event.remove(engine, "before_execute", capture)
