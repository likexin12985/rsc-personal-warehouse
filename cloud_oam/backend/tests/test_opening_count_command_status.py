from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select

from app.formal_access import load_formal_principal
from app.formal_services import opening_count_command_status as service
from app.foundation_models import AuditEvent, Permission, RolePermission
from app.inventory_models import StockLocation
from app.stocktake_models import (
    FormalStocktakeScope, FormalStocktakeTask, StocktakeRound,
    StocktakeScopeCountCompletion,
)
from app.opening_count_command_status_schemas import (
    OpeningCountCommandStatusOut, OpeningCountHistoricalCommandOut,
)
from test_formal_opening_stocktake_read import (
    NOW, _fixed_database_times, _prepare_submitted, db, review_world, world,
    _prepare_observation, _record_observation_disposition, _review_command,
    _open_recount_command, submit_opening_region_review,
)
from test_opening_stocktake_recount_count_service import (
    _open_round_two, _submit_round_two, _fixed_recount_count_time,
    _fixed_recount_time,
)
from test_opening_stocktake_finalize_service import (
    world as finalize_world, _approve, _post, _control_matched_observations,
    _fixed_finalize_times,
)


TRACE = "opening-review-count-request"


@pytest.fixture(autouse=True)
def recovery_clock(monkeypatch):
    monkeypatch.setattr(service, "_database_now", lambda _db: NOW + timedelta(days=1))


@pytest.fixture
def terminal_world(finalize_world):
    read = Permission(id=uuid.uuid4(), resource="stocktake", action="read", field_code="", description="recovery read")
    finalize_world.db.add(read)
    finalize_world.db.flush()
    for role in finalize_world.roles.values():
        finalize_world.db.add(RolePermission(role_id=role.id, permission_id=read.id, effect="allow"))
    finalize_world.db.commit()
    finalize_world.principals["manager_x"] = load_formal_principal(
        finalize_world.db, finalize_world.manager_x.user.id, now=NOW,
    )
    return finalize_world


def _lookup(world, prepared, **overrides):
    actor = overrides.pop("actor", world.principals["manager_x"])
    values = dict(
        actor=actor, task_id=prepared.task.id, round_id=prepared.round.id,
        scope_id=prepared.scope.id, actor_person_id=actor.person_id,
        actor_authorization_version=actor.authorization_version,
        trace_request_id=TRACE,
    )
    values.update(overrides)
    return service.opening_count_command_status(world.db, **values)


def test_sealing_count_is_one_command_with_two_verified_audit_events(world):
    prepared = _prepare_submitted(world)
    result = _lookup(world, prepared)
    assert result.lookup_status == "confirmed"
    assert result.command.scope_completed is True
    assert result.command.caused_round_submission is True
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert result.command.completion_id == completion.id
    encoded = result.model_dump_json()
    assert result.trace_request_id == TRACE
    for forbidden in ("idempotency", "request_jsonb", "sha256", "quantity", "actor_user_id"):
        assert forbidden not in encoded
    assert set(result.command.model_dump()) == {
        "completion_id", "round_no", "completed_at", "scope_completed", "caused_round_submission",
    }


def test_not_observed_is_distinct_from_scope_already_completed(world):
    prepared = _prepare_submitted(world)
    result = _lookup(world, prepared, trace_request_id="a-different-never-observed-trace")
    assert result.lookup_status == "not_observed" and result.command is None
    assert set(result.model_dump()) == {
        "schema_version", "task_id", "round_id", "scope_id", "actor_person_id",
        "actor_authorization_version", "trace_request_id", "lookup_status", "command",
    }


def test_not_observed_before_any_count_is_read_only(world):
    from app.formal_services.opening_stocktake import start_opening_stocktake
    result = start_opening_stocktake(
        world.db, actor=world.principals["manager_x"], command=world.command,
        idempotency_key="recovery-no-count-start-key", request_id="recovery-start-trace",
    )
    world.db.commit()
    prepared = SimpleNamespace(
        task=world.db.get(FormalStocktakeTask, result.task_id),
        round=world.db.get(StocktakeRound, result.initial_round_id),
        scope=world.db.scalar(select(FormalStocktakeScope)),
    )
    assert _lookup(world, prepared).lookup_status == "not_observed"


@pytest.mark.parametrize("anchor,value", [
    ("task_id", uuid.UUID(int=0)), ("round_id", "not-a-uuid"),
    ("scope_id", uuid.UUID(int=0)), ("actor_person_id", uuid.UUID(int=0)),
    ("actor_authorization_version", True), ("actor_authorization_version", 0),
    ("trace_request_id", "bad trace"), ("trace_request_id", "a" * 161),
])
def test_invalid_anchors_fail_before_database(world, anchor, value, monkeypatch):
    prepared = _prepare_submitted(world)
    monkeypatch.setattr(world.db, "scalar", lambda *_a, **_k: pytest.fail("invalid input reached SQL"))
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, **{anchor: value})
    assert caught.value.http_status_code == 400


@pytest.mark.parametrize("anchor,value", [
    ("actor_person_id", uuid.uuid4()), ("actor_authorization_version", 2),
])
def test_identity_sentinel_mismatch_stays_blocked(world, anchor, value):
    prepared = _prepare_submitted(world)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, **{anchor: value})
    assert caught.value.http_status_code == 412


@pytest.mark.parametrize("actor_name", ["manager_y", "technician"])
def test_unrelated_scope_is_not_an_existence_oracle(world, actor_name):
    prepared = _prepare_submitted(world)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, actor=world.principals[actor_name])
    assert caught.value.http_status_code == 404


def test_admin_read_permission_cannot_recover_somebody_elses_count(world):
    prepared = _prepare_submitted(world)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, actor=world.principals["admin"])
    assert caught.value.http_status_code == 403


@pytest.mark.parametrize("field", ["request_jsonb", "request_resolution_jsonb", "evidence_manifest_sha256"])
def test_incomplete_completion_is_never_not_observed(world, field):
    prepared = _prepare_submitted(world)
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    setattr(completion, field, "0" * 64 if field.endswith("sha256") else None)
    world.db.commit()
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, trace_request_id="never-observed-but-broken-graph")
    assert caught.value.http_status_code == 503


def test_missing_sealing_companion_is_not_a_confirmed_scope(world):
    prepared = _prepare_submitted(world)
    companion = world.db.scalar(select(AuditEvent).where(AuditEvent.action == service._ROUND_ACTION))
    # Keep the head foreign key intact while removing the required semantic
    # companion. The immutable production database separately forbids this.
    companion.action = "unrelated.corrupt.audit"
    world.db.commit()
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared)
    assert caught.value.http_status_code == 503


def test_count_permission_revocation_blocks_even_when_read_remains(world):
    prepared = _prepare_submitted(world)
    permission = world.db.scalar(select(Permission).where(
        Permission.resource == "stocktake", Permission.action == "count",
    ))
    mapping = world.db.scalar(select(RolePermission).where(
        RolePermission.role_id == world.roles["provincial_manager"].id,
        RolePermission.permission_id == permission.id,
    ))
    world.db.delete(mapping)
    world.db.commit()
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared)
    assert caught.value.http_status_code == 403


def test_recovery_never_flushes_commits_rolls_back_or_writes(world, monkeypatch):
    prepared = _prepare_submitted(world)
    # Resolve expired fixture attributes before installing the pending object;
    # the real service receives detached UUID inputs, never ORM attributes.
    prepared = SimpleNamespace(
        task=SimpleNamespace(id=prepared.task.id),
        round=SimpleNamespace(id=prepared.round.id),
        scope=SimpleNamespace(id=prepared.scope.id),
    )
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(world.db.get_bind(), "before_cursor_execute", record)
    world.db.add(Permission(id=uuid.uuid4(), resource="unrelated", action="read", field_code="", description="pending"))
    for name in ("flush", "commit", "rollback"):
        monkeypatch.setattr(world.db, name, lambda *_a, **_k: pytest.fail("GET attempted a transaction mutation"))
    assert _lookup(world, prepared).lookup_status == "confirmed"
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_history_remains_confirmable_after_new_recount_round(world):
    prepared = _open_round_two(world)
    old = SimpleNamespace(task=prepared.task, round=prepared.source_round, scope=prepared.scope)
    result = _lookup(world, old)
    assert result.lookup_status == "confirmed"
    assert result.command.caused_round_submission is True
    assert prepared.task.current_round_no == 2
    assert prepared.round.status == "counting"


def _open_round_two_with_disposed_source(world):
    prepared = _prepare_observation(
        world, material_identifier_raw="RECOVERY-UNKNOWN-SOURCE",
        material_identifier_type="unknown",
    )
    _record_observation_disposition(world, prepared, disposition="requires_recount")
    world.db.commit()
    submit_opening_region_review(
        world.db, actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared, decision="recount"),
        idempotency_key="recovery-disposed-source-region-review",
        request_id="recovery-disposed-source-region-trace",
    )
    world.db.commit()
    opened = service.recount.open_opening_stocktake_recount(
        world.db, actor=world.principals["manager_x"],
        command=_open_recount_command(
            prepared.task, prepared.round, prepared.scope, world.manager_x.user.id,
        ),
        idempotency_key="recovery-disposed-source-open-recount",
        request_id="recovery-disposed-source-open-trace",
    )
    world.db.commit()
    return prepared, opened


def test_disposed_source_history_and_current_empty_recount_are_independent(world):
    prepared, opened = _open_round_two_with_disposed_source(world)
    old_round_id = prepared.round.id
    task_id = prepared.task.id
    result = _lookup(world, prepared)
    assert result.lookup_status == "confirmed"
    assert result.command.round_no == 1
    assert result.command.caused_round_submission is True
    world.db.rollback()
    detail = service.query.opening_stocktake_detail(
        world.db, actor=world.principals["manager_x"], task_id=task_id,
    )
    assert detail.current_round.round_id == opened.next_round_id
    assert detail.current_round.round_id != old_round_id
    assert detail.current_round.round_no == 2
    assert detail.current_round.status == "counting"
    assert detail.evidence_status == "counting_hidden"
    assert all(scope.completion_status == "pending" for scope in detail.scopes)
    assert detail.observations == [] and detail.differences == []
    world.db.rollback()
    unobserved = _lookup(
        world, prepared, round_id=opened.next_round_id,
        trace_request_id="recovery-current-recount-never-sent",
    )
    assert unobserved.lookup_status == "not_observed"


@pytest.mark.parametrize("corruption", [
    "missing_round_map", "duplicate_round_map", "missing_planned_round",
    "duplicate_planned_round", "foreign_planned_round", "missing_observation",
    "foreign_observation", "foreign_candidate_owner", "duplicate_observation",
    "forged_proof",
])
def test_disposed_source_task_resolution_union_fails_closed(world, monkeypatch, corruption):
    prepared, _opened = _open_round_two_with_disposed_source(world)
    query = service.query
    original = query._lock_opening_read_batch_graph

    def corrupt_graph(db, **kwargs):
        proof = original(db, **kwargs)
        if corruption == "forged_proof":
            return replace(proof, seal=object())
        owner_id, plans = proof.round_plans[0]
        source_id, source_plan = plans[0]
        next_id, next_plan = plans[1]
        maps = dict(proof.disposition_resolutions)
        source_map = maps[source_id]
        assert len(source_map) == 1 and not maps[next_id]
        observation_id = next(iter(source_map))
        if corruption == "missing_round_map":
            return replace(proof, disposition_resolutions=((next_id, maps[next_id]),))
        if corruption == "duplicate_round_map":
            return replace(proof, disposition_resolutions=(*proof.disposition_resolutions, (source_id, source_map)))
        if corruption == "missing_planned_round":
            return replace(proof, round_plans=((owner_id, (plans[1],)),))
        if corruption == "duplicate_planned_round":
            return replace(proof, round_plans=((owner_id, (*plans, plans[0])),))
        if corruption == "foreign_planned_round":
            foreign_id = uuid.uuid4()
            return replace(proof, round_plans=((owner_id, ((foreign_id, source_plan), plans[1])),),
                           disposition_resolutions=((foreign_id, source_map), (next_id, maps[next_id])))
        if corruption == "missing_observation":
            maps[source_id] = {}
        elif corruption == "foreign_observation":
            maps[source_id] = {uuid.uuid4(): source_map[observation_id]}
        elif corruption == "foreign_candidate_owner":
            candidate = source_plan.disposition_candidates[0]
            signature = list(candidate.observation_signature)
            signature[1] = uuid.uuid4()
            altered = replace(candidate, observation_signature=tuple(signature))
            source_plan = replace(source_plan, disposition_candidates=(altered,))
            return replace(proof, round_plans=((owner_id, ((source_id, source_plan), plans[1])),))
        elif corruption == "duplicate_observation":
            maps[next_id] = dict(source_map)
            next_plan = replace(next_plan, disposition_candidates=source_plan.disposition_candidates)
            return replace(proof, round_plans=((owner_id, (plans[0], (next_id, next_plan))),),
                           disposition_resolutions=tuple(maps.items()))
        return replace(proof, disposition_resolutions=tuple(maps.items()))

    monkeypatch.setattr(query, "_lock_opening_read_batch_graph", corrupt_graph)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared)
    assert caught.value.http_status_code == 503
    assert caught.value.code == "opening_count_command_status_evidence_invalid"


def test_recount_history_confirms_original_round_not_initial_assignment(world):
    prepared = _open_round_two(world)
    _submit_round_two(world, prepared, actor_name="manager_x", key="recovery-round-two-key")
    world.db.commit()
    audit = world.db.scalar(select(AuditEvent).where(
        AuditEvent.action == service._SCOPE_ACTION,
        AuditEvent.after_jsonb["round_id"].as_string() == str(prepared.round.id),
    ))
    assert audit is not None
    result = _lookup(world, prepared, trace_request_id="opening-recount-count-request")
    assert result.lookup_status == "confirmed"
    assert result.command.round_no == 2


def test_response_schema_forbids_current_projection_and_raw_write_fields(world):
    prepared = _prepare_submitted(world)
    document = _lookup(world, prepared).model_dump()
    for extra in ("idempotency_key", "request_jsonb", "task_status", "task_version"):
        with pytest.raises(ValidationError):
            OpeningCountCommandStatusOut(**document, **{extra: "forbidden"})
    document["lookup_status"] = "not_observed"
    with pytest.raises(ValidationError):
        OpeningCountCommandStatusOut(**document)


@pytest.mark.parametrize("closed", [False, True])
def test_historical_count_survives_real_post_and_close_with_released_freezes(terminal_world, closed):
    world = terminal_world
    prepared = _approve(world, observations=_control_matched_observations(world))
    _post(world, prepared, key="recovery-real-post-key")
    if closed:
        finalize = service.query.finalize_service
        finalize.close_posted_opening_stocktake(
            world.db, actor=world.principals["admin"],
            command=finalize.CloseOpeningStocktakeCommand(
                task_id=prepared.task.id, expected_version=prepared.task.version,
            ),
            idempotency_key="recovery-real-close-key", request_id="recovery-real-close-request",
        )
    world.db.commit()
    result = _lookup(world, prepared)
    assert result.lookup_status == "confirmed"
    assert result.command.caused_round_submission is True
    assert prepared.task.status == ("closed" if closed else "posted")
    assert "task_status" not in result.model_dump_json()


def _two_scope_counts(world, *, same_trace=False, seal=True):
    from app.formal_services.opening_stocktake import OpeningStocktakeScopeInput, start_opening_stocktake
    from app.formal_services.opening_stocktake_count import (
        OpeningPhysicalObservationInput, SubmitOpeningStocktakeScopeCountCommand,
        submit_opening_stocktake_scope_count,
    )
    second_location = StockLocation(
        id=uuid.uuid4(), code="RECOVERY-EMPTY-SECOND", name="第二个空仓范围",
        location_type="region", owner_org_id=world.region_x.id, parent_id=None,
        custodian_person_id=None, status="active",
    )
    world.db.add(second_location)
    world.db.commit()
    command = replace(world.command, scopes=(
        *world.command.scopes,
        OpeningStocktakeScopeInput(
            owner_org_id=world.region_x.id, location_id=second_location.id,
            assignee_user_id=world.manager_x.user.id, freeze_mode="hard",
        ),
    ))
    started = start_opening_stocktake(
        world.db, actor=world.principals["manager_x"], command=command,
        idempotency_key="recovery-two-start-key", request_id="recovery-two-start-trace",
    )
    scopes = {row.location_id: row for row in world.db.scalars(select(FormalStocktakeScope)).all()}
    original = scopes[world.location.id]
    first = submit_opening_stocktake_scope_count(
        world.db, actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id, round_id=started.initial_round_id, scope_id=original.id,
            physical_observations=(OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code", condition_code="new",
                availability_bucket="available", counted_qty=Decimal("2.000"),
            ),),
        ), idempotency_key="recovery-first-count-key", request_id=TRACE,
    )
    assert first.round_sealed is False
    if seal:
        submit_opening_stocktake_scope_count(
            world.db, actor=world.principals["manager_x"],
            command=SubmitOpeningStocktakeScopeCountCommand(
                task_id=started.task_id, round_id=started.initial_round_id,
                scope_id=scopes[second_location.id].id, zero_confirmed=True,
            ), idempotency_key="recovery-second-count-key",
            request_id=TRACE if same_trace else "recovery-distinct-second-trace",
        )
    world.db.commit()
    return SimpleNamespace(
        task=world.db.get(FormalStocktakeTask, started.task_id),
        round=world.db.get(StocktakeRound, started.initial_round_id), scope=original,
    )


@pytest.mark.parametrize("seal", [False, True])
def test_first_scope_historical_summary_never_claims_it_sealed_later_round(world, seal):
    prepared = _two_scope_counts(world, seal=seal)
    result = _lookup(world, prepared)
    assert result.lookup_status == "confirmed"
    assert result.command.caused_round_submission is False
    assert prepared.round.status == ("submitted" if seal else "counting")


def test_same_actor_trace_reused_for_two_valid_commands_is_ambiguous(world):
    prepared = _two_scope_counts(world, same_trace=True)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared)
    assert caught.value.http_status_code == 503


def test_uses_one_canonical_owner_sequence_and_no_standalone_replay(world, monkeypatch):
    prepared = _prepare_submitted(world)
    events = []
    def wrap(obj, name, label):
        real = getattr(obj, name)
        def record(*args, **kwargs):
            events.append(label)
            return real(*args, **kwargs)
        monkeypatch.setattr(obj, name, record)
    q = service.query
    wrap(q, "_lock_opening_read_batch_root", "ledger-task")
    wrap(q.posting_service, "_lock_opening_task_principal_graph", "principal")
    wrap(q, "lock_opening_stocktake_task_evidence", "evidence")
    wrap(q.finalize_service, "_lock_opening_serial_union_graph", "serial")
    wrap(q.reconciliation_service, "_lock_opening_control_reconciliation_batch_graph", "reconciliation")
    wrap(q, "_lock_audit_chain_head_with_proof", "audit")
    def forbidden(*_args, **_kwargs):
        pytest.fail("standalone replay or nested audit owner entered")
    monkeypatch.setattr(service.count, "_lock_audit_chain_head_with_proof", forbidden)
    monkeypatch.setattr(q.finalize_service, "validate_opening_finalize_evidence_for_replay", forbidden)
    assert _lookup(world, prepared).lookup_status == "confirmed"
    assert events == ["ledger-task", "principal", "evidence", "serial", "reconciliation", "audit"]


def test_forged_audit_proof_cannot_confirm(world, monkeypatch):
    prepared = _prepare_submitted(world)
    real = service.query._lock_audit_chain_head_with_proof
    def forged(*args, **kwargs):
        head, _proof = real(*args, **kwargs)
        return head, object()
    monkeypatch.setattr(service.query, "_lock_audit_chain_head_with_proof", forged)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared)
    assert caught.value.http_status_code == 503


def test_literal_superseded_state_remains_forbidden_by_canonical_recount_chain(world):
    prepared = _open_round_two(world)
    prepared.source_round.status = "superseded"
    world.db.commit()
    old = SimpleNamespace(task=prepared.task, round=prepared.source_round, scope=prepared.scope)
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, old)
    assert caught.value.http_status_code == 503


def test_new_identity_version_cannot_silently_upgrade_old_completion(world):
    prepared = _prepare_submitted(world)
    world.manager_x.user.authorization_version += 1
    world.db.commit()
    current = load_formal_principal(world.db, world.manager_x.user.id, now=NOW + timedelta(days=1))
    with pytest.raises(service.OpeningCountCommandStatusError) as caught:
        _lookup(world, prepared, actor=current)
    assert caught.value.http_status_code == 412


@pytest.fixture
def api_recovery(world, monkeypatch):
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.routers.formal_opening_stocktake_read import router
    prepared = _prepare_submitted(world)
    result = _lookup(world, prepared)
    stub = Mock(return_value=result)
    monkeypatch.setattr(service, "opening_count_command_status", stub)
    api = FastAPI()
    api.include_router(router, prefix="/api")
    fake_db = SimpleNamespace(commit=Mock(), rollback=Mock(), flush=Mock())
    principal = SimpleNamespace(allows=lambda *_a, **_k: True)
    api.dependency_overrides[get_db] = lambda: fake_db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    path = f"/api/v1/stocktakes/opening/{prepared.task.id}/rounds/{prepared.round.id}/scopes/{prepared.scope.id}/count-command-status"
    params = {
        "actor_person_id": str(world.manager_x.person.id),
        "actor_authorization_version": "1", "trace_request_id": TRACE,
    }
    with TestClient(api) as client:
        yield SimpleNamespace(client=client, path=path, params=params, stub=stub, db=fake_db, result=result)


def test_api_contract_no_store_and_no_transaction_mutations(api_recovery):
    state = api_recovery
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 200
    assert response.json() == state.result.model_dump(mode="json")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "actor_user_id" not in state.stub.call_args.kwargs
    for call in (state.db.commit, state.db.rollback, state.db.flush):
        call.assert_not_called()


@pytest.mark.parametrize("extra", ["idempotency_key", "request_jsonb", "request_sha256", "actor_user_id"])
def test_api_rejects_raw_write_inputs_and_extra_identity(api_recovery, extra):
    state = api_recovery
    response = state.client.get(state.path, params={**state.params, extra: "not-accepted"})
    assert response.status_code == 400
    assert "no-store" in response.headers["cache-control"]
    state.stub.assert_not_called()


@pytest.mark.parametrize("kind", ["body", "key_header", "duplicate"])
def test_api_rejects_post_replay_material_and_ambiguous_query(api_recovery, kind):
    state = api_recovery
    kwargs = {"params": state.params}
    if kind == "body":
        kwargs["content"] = b'{"observations":[]}'
    elif kind == "key_header":
        kwargs["headers"] = {"Idempotency-Key": "not-accepted"}
    else:
        kwargs["params"] = [*state.params.items(), ("trace_request_id", "another-trace")]
    response = state.client.request("GET", state.path, **kwargs)
    assert response.status_code == 400
    state.stub.assert_not_called()


def test_api_domain_failure_preserves_blocking_status_and_cache_policy(api_recovery):
    state = api_recovery
    state.stub.side_effect = service.OpeningCountCommandStatusError(
        "opening_count_command_status_evidence_invalid", "service_unavailable", "保持阻塞",
    )
    response = state.client.get(state.path, params=state.params)
    assert response.status_code == 503
    assert "no-store" in response.headers["cache-control"]
    state.db.rollback.assert_not_called()


@pytest.mark.parametrize("value", [1, 1.0, "true", "1", False, 0, None])
def test_historical_scope_completed_accepts_only_literal_boolean_true(value):
    document = dict(
        completion_id=uuid.uuid4(), round_no=1, completed_at=NOW,
        scope_completed=value, caused_round_submission=False,
    )
    with pytest.raises(ValidationError):
        OpeningCountHistoricalCommandOut(**document)
    document["scope_completed"] = True
    assert OpeningCountHistoricalCommandOut(**document).scope_completed is True
