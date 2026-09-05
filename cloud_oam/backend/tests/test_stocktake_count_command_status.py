"""Historical non-opening count lookup, using real synthetic count facts."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select, text

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.formal_services import stocktake_count_command_status as service
from app.formal_services.audit_chain import append_audit_event
from app.foundation_models import AuditEvent, Permission, Role, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.stocktake_count_command_status_schemas import StocktakeCountCommandStatusOut
from app.stocktake_models import FormalStocktakeScope, FormalStocktakeTask, InventoryFreeze, StocktakeRound, StocktakeScopeCountCompletion
from test_stocktake_task_service import NOW, db, world, _managed_draft  # noqa: F401
from test_stocktake_count_service import _started
from test_stocktake_difference_service import _submitted
from test_stocktake_review_recount_service import review_world, _prepare  # noqa: F401
from test_stocktake_recount_execution_service import (
    recount_world, _recount_clocks, _open_recount, _count_recount,
    _prepare_two_scope_initial,
)  # noqa: F401
from test_stocktake_safe_posting_service import posting_world, _approve, _post  # noqa: F401
from test_stocktake_close_service import (
    close_world, _reconcile, _close, _install_sqlite_close_guards, RECONCILED_AT, CLOSED_AT,
)  # noqa: F401


@pytest.fixture(autouse=True)
def _status_setup(world, monkeypatch, request):
    monkeypatch.setattr(service, "_database_now", lambda _db: NOW + timedelta(days=1))
    if "status_terminal_world" in request.fixturenames:
        return  # posting_world creates the shared read permission itself.
    permission = Permission(id=uuid.uuid4(), resource="stocktake", action="read", field_code="", description="status read")
    world.db.add(permission)
    world.db.flush()
    for role in world.db.scalars(select(Role)).all():
        world.db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"))
    world.db.commit()
    world.principals = {name: load_formal_principal(world.db, actor.user_id, now=NOW) for name, actor in world.principals.items()}


@pytest.fixture
def status_terminal_world(close_world):
    permission = close_world.db.scalar(select(Permission).where(Permission.resource == "stocktake", Permission.action == "read"))
    role = close_world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
    close_world.db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect="allow"))
    close_world.db.commit()
    close_world.principals["manager_x"] = load_formal_principal(close_world.db, close_world.manager_x.user.id, now=NOW)
    return close_world


def _initial(world, key="status-initial"):
    task, round_row = _submitted(world, key=key, counted_qty=Decimal("4.000"))
    scope = world.db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id))
    world.db.commit()
    return SimpleNamespace(task_id=task.id, round_id=round_row.id, scope_id=scope.id, trace=f"trace-{key}-count")


def _lookup(world, target, **changes):
    actor = changes.pop("actor", world.principals["manager_x"])
    values = dict(actor=actor, operation="initial_count", task_id=target.task_id,
                  round_id=target.round_id, scope_id=target.scope_id,
                  actor_person_id=actor.person_id, actor_authorization_version=actor.authorization_version,
                  trace_request_id=target.trace)
    values.update(changes)
    return service.stocktake_count_command_status(world.db, **values)


def _assert_blocked(world, target, statuses=(403, 404, 412, 503), **changes):
    with pytest.raises(service.StocktakeCountCommandStatusError) as caught:
        _lookup(world, target, **changes)
    assert caught.value.http_status_code in statuses


def test_initial_confirmation_is_minimal_historical_fact(world):
    target = _initial(world)
    result = _lookup(world, target)
    assert result.lookup_status == "confirmed"
    assert result.operation == "initial_count"
    assert result.command.scope_completed is True
    assert result.command.caused_round_submission is True
    assert result.command.round_no == 1
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert result.command.completion_id == completion.id
    assert set(result.model_dump()) == {"schema_version", "operation", "task_id", "round_id", "scope_id", "actor_person_id", "actor_authorization_version", "trace_request_id", "lookup_status", "command"}
    assert set(result.command.model_dump()) == {"completion_id", "round_no", "completed_at", "scope_completed", "caused_round_submission"}
    for forbidden in ("request_jsonb", "sha256", "idempotency", "quantity", "serial", "actor_user_id", "task_status", "task_version"):
        assert forbidden not in result.model_dump_json()


def test_unseen_trace_does_not_claim_scope_unexecuted_or_unlock(world):
    target = _initial(world)
    result = _lookup(world, target, trace_request_id="never-observed-trace")
    assert result.lookup_status == "not_observed"
    assert result.command is None
    assert "retryable" not in result.model_dump_json()
    assert "not_executed" not in result.model_dump_json()


def test_not_observed_before_any_count(world):
    created, started, scopes, round_row = _started(world, key="status-empty", draft=_managed_draft(world, material_id=world.material_a.id, condition_code="new"))
    world.db.commit()
    target = SimpleNamespace(task_id=created.task_id, round_id=round_row.id, scope_id=scopes[0].id, trace="never-sent")
    assert _lookup(world, target).lookup_status == "not_observed"


def test_two_scope_history_distinguishes_nonsealing_and_sealing_count(recount_world):
    task, initial_round, scopes = _prepare_two_scope_initial(recount_world, key="status-two")
    recount_world.db.commit()
    for condition, sealed in (("new", False), ("used", True)):
        target = SimpleNamespace(task_id=task.id, round_id=initial_round.id, scope_id=scopes[condition].id, trace=f"trace-status-two-initial-{condition}")
        result = _lookup(recount_world, target)
        assert result.lookup_status == "confirmed"
        assert result.command.caused_round_submission is sealed


def test_same_trace_for_two_real_scope_counts_is_not_a_matching_subset(recount_world, monkeypatch):
    from app.formal_services import stocktake_count
    reference = stocktake_count._request_reference
    monkeypatch.setattr(stocktake_count, "_request_reference", lambda raw: reference(
        "trace-status-shared-initial-new" if raw == "trace-status-shared-initial-used" else raw
    ))
    task, initial_round, scopes = _prepare_two_scope_initial(recount_world, key="status-shared")
    recount_world.db.commit()
    for scope in scopes.values():
        target = SimpleNamespace(task_id=task.id, round_id=initial_round.id, scope_id=scope.id, trace="trace-status-shared-initial-new")
        _assert_blocked(recount_world, target, statuses=(503,))


def test_initial_history_and_recount_history_remain_separate(recount_world):
    task, initial_round = _prepare(recount_world, key="status-recount")
    difference, opened, recount_round = _open_recount(recount_world, task, initial_round, key="status-recount")
    old = SimpleNamespace(task_id=task.id, round_id=initial_round.id, scope_id=difference.scope_id, trace="trace-status-recount-count")
    recount_world.db.commit()
    assert _lookup(recount_world, old).command.round_no == 1
    new = SimpleNamespace(task_id=task.id, round_id=recount_round.id, scope_id=difference.scope_id, trace="trace-status-round-two-count")
    assert _lookup(recount_world, new, operation="recount_count").lookup_status == "not_observed"
    _count_recount(recount_world, task, recount_round, difference.scope_id, key="status-round-two", counted_qty=Decimal("4.000"))
    recount_world.db.commit()
    result = _lookup(recount_world, new, operation="recount_count")
    assert result.lookup_status == "confirmed" and result.command.round_no == 2
    assert result.command.caused_round_submission is True
    assert _lookup(recount_world, old).command.round_no == 1
    _assert_blocked(recount_world, new, operation="initial_count", statuses=(400, 404, 412, 503))


@pytest.mark.parametrize("closed", [False, True])
def test_history_survives_real_post_and_close(status_terminal_world, monkeypatch, closed):
    from app.formal_services import stocktake_close
    world = status_terminal_world
    task, round_row = _approve(world, monkeypatch, key="status-terminal", counted_qty=Decimal("5.000"), decision="no_adjustment")
    scope = world.db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == task.id))
    target = SimpleNamespace(task_id=task.id, round_id=round_row.id, scope_id=scope.id, trace="trace-status-terminal-count")
    _post(world, task, key="status-terminal-post")
    if closed:
        _install_sqlite_close_guards(world)
        monkeypatch.setattr(stocktake_close, "_database_now", lambda _db: RECONCILED_AT)
        _reconcile(world, task, key="status-terminal-reconcile")
        monkeypatch.setattr(stocktake_close, "_database_now", lambda _db: CLOSED_AT)
        _close(world, task, key="status-terminal-close")
        assert not world.db.execute(text("PRAGMA foreign_key_check")).all()
    world.db.commit()
    assert task.status == ("closed" if closed else "posted")
    result = _lookup(world, target)
    assert result.lookup_status == "confirmed"
    assert result.command.round_no == 1
    assert "task_status" not in result.model_dump_json()


@pytest.mark.parametrize("anchor,value", [
    ("task_id", uuid.UUID(int=0)), ("round_id", "not-a-uuid"), ("scope_id", uuid.UUID(int=0)),
    ("actor_person_id", uuid.UUID(int=0)), ("actor_authorization_version", True),
    ("actor_authorization_version", 0), ("trace_request_id", "bad trace"),
    ("trace_request_id", "a" * 161), ("operation", "post"),
])
def test_bad_lookup_anchors_never_reach_sql(world, monkeypatch, anchor, value):
    target = _initial(world)
    monkeypatch.setattr(world.db, "scalar", lambda *_a, **_k: pytest.fail("invalid input reached SQL"))
    monkeypatch.setattr(world.db, "scalars", lambda *_a, **_k: pytest.fail("invalid input reached SQL"))
    _assert_blocked(world, target, statuses=(400,), **{anchor: value})


@pytest.mark.parametrize("change", ["person", "version", "manager_y", "technician", "admin"])
def test_actor_sentinel_and_exact_executor_scope_are_enforced(world, change):
    target = _initial(world)
    if change == "person":
        _assert_blocked(world, target, actor_person_id=uuid.uuid4(), statuses=(412,))
    elif change == "version":
        _assert_blocked(world, target, actor_authorization_version=2, statuses=(412,))
    else:
        _assert_blocked(world, target, actor=world.principals[change], statuses=(403, 404))


def test_count_permission_revoked_while_read_remains_blocks_history(world):
    target = _initial(world)
    role = world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
    permission = world.db.scalar(select(Permission).where(Permission.resource == "stocktake", Permission.action == "count"))
    mapping = world.db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
    world.db.delete(mapping)
    world.db.commit()
    _assert_blocked(world, target, statuses=(403,))


def test_current_scope_custody_transfer_blocks_original_executor(world):
    target = _initial(world)
    scope = world.db.get(FormalStocktakeScope, target.scope_id)
    location = world.db.get(StockLocation, scope.location_id)
    prior = world.db.scalar(select(CustodyAssignment).where(
        CustodyAssignment.location_id == location.id, CustodyAssignment.valid_to.is_(None)))
    transferred_at = NOW + timedelta(hours=1)
    prior.valid_to = transferred_at
    location.custodian_person_id = world.technician.person.id
    world.db.add(CustodyAssignment(id=uuid.uuid4(), location_id=location.id,
                                 custodian_person_id=world.technician.person.id,
                                 valid_from=transferred_at, valid_to=None, handover_case_id=None))
    world.db.commit()
    _assert_blocked(world, target, statuses=(412,))


@pytest.mark.parametrize("change", ["permission", "authorization_version"])
def test_permission_change_after_trace_resolution_blocks_confirmation(world, monkeypatch, change):
    target = _initial(world)
    original = service._resolve_trace
    completed = []
    def resolve_then_revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result is not None
        if change == "authorization_version":
            world.manager_x.user.authorization_version += 1
        else:
            role = world.db.scalar(select(Role).where(Role.code == "provincial_manager"))
            permission = world.db.scalar(select(Permission).where(Permission.resource == "stocktake", Permission.action == "count"))
            mapping = world.db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
            world.db.delete(mapping)
        world.db.flush()
        completed.append(True)
        return result
    monkeypatch.setattr(service, "_resolve_trace", resolve_then_revoke)
    _assert_blocked(world, target, statuses=(403, 412))
    assert completed == [True]


def test_legitimate_audit_storage_time_may_follow_business_occurrence(world):
    target = _initial(world)
    audit = world.db.scalar(select(AuditEvent).where(AuditEvent.action == "stocktake.scope_count.submitted"))
    # Real append_audit_event uses the model's storage timestamp, whereas count
    # supplies its earlier business timestamp. Never rewrite either signed fact.
    assert service.count._as_utc(audit.created_at) > service.count._as_utc(audit.occurred_at)
    result = _lookup(world, target)
    assert result.lookup_status == "confirmed"
    assert service.count._as_utc(result.command.completed_at) == service.count._as_utc(audit.occurred_at)


@pytest.mark.parametrize("corruption", [None, "freeze_at_cutoff", "round_started_at_cutoff", "completion_before_round"])
def test_advancing_start_clock_preserves_cutoff_then_freeze_causality(world, monkeypatch, corruption):
    from app.formal_services import stocktake_count, stocktake_task
    clock = [NOW]
    def advancing_now(_db):
        clock[0] += timedelta(milliseconds=1)
        return clock[0]
    monkeypatch.setattr(stocktake_task, "_database_now", advancing_now)
    monkeypatch.setattr(stocktake_count, "_database_now", lambda _db: NOW + timedelta(minutes=1))
    target = _initial(world, key="status-advancing-clock")
    task = world.db.get(FormalStocktakeTask, target.task_id)
    round_row = world.db.get(StocktakeRound, target.round_id)
    freeze = world.db.scalar(select(InventoryFreeze).where(InventoryFreeze.stocktake_scope_id == target.scope_id))
    assert task.cutoff_at < task.frozen_at
    assert freeze.valid_from == task.frozen_at == round_row.started_at
    if corruption == "freeze_at_cutoff":
        freeze.valid_from = task.cutoff_at
    elif corruption == "round_started_at_cutoff":
        round_row.started_at = task.cutoff_at
    elif corruption == "completion_before_round":
        completion = world.db.scalar(select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == target.round_id,
            StocktakeScopeCountCompletion.scope_id == target.scope_id))
        completion.completed_at = round_row.started_at - timedelta(milliseconds=1)
        completion.created_at = completion.completed_at
    world.db.commit()
    if corruption is None:
        assert _lookup(world, target).lookup_status == "confirmed"
    else:
        _assert_blocked(world, target, statuses=(503,))


@pytest.mark.parametrize("field,value", [("authorization_sha256", "0" * 64), ("evidence_manifest_sha256", "0" * 64), ("count_line_count", 99), ("serial_count", 99)])
def test_broken_completion_is_not_confirmed_or_downgraded_to_not_observed(world, field, value):
    target = _initial(world)
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert getattr(completion, field) != value
    setattr(completion, field, value)
    world.db.commit()
    _assert_blocked(world, target, statuses=(503,))
    _assert_blocked(world, target, trace_request_id="also-never-sent", statuses=(503,))


def test_missing_sealing_audit_companion_is_blocking(world):
    target = _initial(world)
    companion = world.db.scalar(select(AuditEvent).where(AuditEvent.action == "stocktake.initial_round.submitted"))
    companion.action = "unrelated.corrupt.audit"
    world.db.commit()
    _assert_blocked(world, target, statuses=(503,))


@pytest.mark.parametrize("namespace", ["stocktake", "opening", "material_request"])
def test_other_namespace_cannot_impersonate_primary_count_evidence(world, namespace):
    target = _initial(world)
    from app.formal_services.stocktake_count import _request_reference
    from app.formal_services.opening_stocktake_count import _request_reference as opening_reference
    reference = _request_reference(target.trace) if namespace == "stocktake" else opening_reference(target.trace) if namespace == "opening" else target.trace
    primary = world.db.scalar(select(AuditEvent).where(AuditEvent.action == "stocktake.scope_count.submitted"))
    append_audit_event(world.db, stream_key="inventory", actor_user_id=world.principals["manager_x"].user_id,
                       action=primary.action, aggregate_type=primary.aggregate_type, aggregate_id=primary.aggregate_id,
                       before_jsonb=primary.before_jsonb, after_jsonb=primary.after_jsonb, request_id=reference, occurred_at=NOW)
    world.db.commit()
    _assert_blocked(world, target, statuses=(503,))


def test_same_raw_trace_in_recount_namespace_is_not_a_matching_subset(world):
    from app.formal_services.stocktake_recount_count import _request_reference
    target = _initial(world)
    append_audit_event(world.db, stream_key="inventory", actor_user_id=world.principals["manager_x"].user_id,
                       action="stocktake.recount.unrelated.command", aggregate_type="other", aggregate_id=str(uuid.uuid4()),
                       before_jsonb=None, after_jsonb={"status": "recorded"}, request_id=_request_reference(target.trace), occurred_at=NOW)
    world.db.commit()
    _assert_blocked(world, target, statuses=(503,))


def test_get_never_flushes_commits_rolls_back_or_changes_any_fact(world, monkeypatch):
    target = _initial(world)
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(world.db.get_bind(), "before_cursor_execute", capture)
    world.db.add(Permission(id=uuid.uuid4(), resource="unrelated", action="read", field_code="", description="unflushed"))
    for method in ("flush", "commit", "rollback"):
        monkeypatch.setattr(world.db, method, lambda *_a, **_k: pytest.fail("GET attempted a transaction mutation"))
    assert _lookup(world, target).lookup_status == "confirmed"
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


@pytest.mark.parametrize("extra", ["task_status", "task_version", "request_jsonb", "idempotency_key", "retryable"])
def test_response_forbids_current_projection_and_replay_fields(world, extra):
    document = _lookup(world, _initial(world)).model_dump()
    with pytest.raises(ValidationError):
        StocktakeCountCommandStatusOut(**document, **{extra: "forbidden"})
    document["lookup_status"] = "not_observed"
    with pytest.raises(ValidationError):
        StocktakeCountCommandStatusOut(**document)


@pytest.mark.parametrize("value", [1, 1.0, "true", False, None])
def test_scope_completed_is_strict_true(world, value):
    document = _lookup(world, _initial(world)).model_dump()
    document["command"]["scope_completed"] = value
    with pytest.raises(ValidationError):
        StocktakeCountCommandStatusOut(**document)


@pytest.fixture
def api_status(world, monkeypatch):
    from app.routers import formal_stocktakes
    target = _initial(world)
    result = _lookup(world, target)
    stub = Mock(return_value=result)
    monkeypatch.setattr(service, "stocktake_count_command_status", stub)
    api = FastAPI()
    api.include_router(formal_stocktakes.router, prefix="/api")
    fake_db = SimpleNamespace(commit=Mock(), rollback=Mock(), flush=Mock())
    api.dependency_overrides[get_db] = lambda: fake_db
    api.dependency_overrides[get_formal_principal] = lambda: SimpleNamespace(allows=lambda *_a, **_k: True)
    params = {"operation": "initial_count", "actor_person_id": str(world.principals["manager_x"].person_id), "actor_authorization_version": "1", "trace_request_id": target.trace}
    path = f"/api/v1/stocktakes/{target.task_id}/rounds/{target.round_id}/scopes/{target.scope_id}/count-command-status"
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, path=path, params=params, result=result, stub=stub, db=fake_db)


def test_api_no_store_exact_response_and_no_transaction_mutation(api_status):
    state = api_status
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 200
    assert response.json() == state.result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    for call in (state.db.commit, state.db.rollback, state.db.flush):
        call.assert_not_called()


def test_api_evidence_failure_is_private_and_does_not_rollback(api_status):
    state = api_status
    state.stub.side_effect = service.StocktakeCountCommandStatusError(
        "stocktake_count_command_status_evidence_invalid", "service_unavailable", "保持阻塞",
    )
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 503
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    for call in (state.db.commit, state.db.rollback, state.db.flush):
        call.assert_not_called()


@pytest.mark.parametrize("kind", ["body", "key_header", "duplicate", "idempotency_key", "request_jsonb", "request_sha256", "actor_user_id"])
def test_api_rejects_replay_material_and_ambiguous_coordinates(api_status, kind):
    state = api_status
    kwargs = {"params": state.params}
    if kind == "body": kwargs["content"] = b'{"observations":[]}'
    elif kind == "key_header": kwargs["headers"] = {"Idempotency-Key": "not-accepted"}
    elif kind == "duplicate": kwargs["params"] = [*state.params.items(), ("trace_request_id", "other-valid-trace")]
    else: kwargs["params"] = {**state.params, kind: "not-accepted"}
    response = state.client.request("GET", state.path, **kwargs)
    assert response.status_code == 400
    assert "no-store" in response.headers["cache-control"]
    state.stub.assert_not_called()


@pytest.mark.parametrize("failure", [401, 422])
def test_real_app_framework_failures_keep_dynamic_status_path_private(world, monkeypatch, failure):
    from app import main
    target = _initial(world)
    path = f"/api/v1/stocktakes/{target.task_id}/rounds/{target.round_id}/scopes/{target.scope_id}/count-command-status"
    def principal():
        if failure == 401:
            raise HTTPException(status_code=401, detail="synthetic unauthenticated")
        return SimpleNamespace(allows=lambda *_a, **_k: True)
    monkeypatch.setitem(main.app.dependency_overrides, get_formal_principal, principal)
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: world.db)
    stub = Mock(side_effect=AssertionError("framework rejection reached service"))
    monkeypatch.setattr(service, "stocktake_count_command_status", stub)
    # No lifespan context: exercise real routes/middleware without startup DB work.
    response = TestClient(main.app).get(path)
    assert response.status_code == failure
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    stub.assert_not_called()
