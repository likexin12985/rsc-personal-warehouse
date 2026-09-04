"""Supply assertions invoked only inside the already-validated disposable PG16 gate.

This module never provisions a database, opens a network connection or reads
credentials. Its caller supplies the gate's restricted API-role engine.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import threading
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def assert_supply_gate(api_engine, *, source_request_id, manager_user_id, admin_user_id):
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.demand_models import (
        ApprovalExternalRegistration, ApprovalInstance, ApprovalStep, MaterialRequest,
        MaterialRequestCommand, MaterialRequestFile, MaterialRequestLine, SupplyTask,
    )
    from app.dependencies import get_formal_principal
    from app.formal_services.material_request_draft import create_material_request_draft, submit_material_request
    from app.formal_services.material_request_lifecycle import (
        MaterialRequestCancelInput, MaterialRequestCancellationLineInput, cancel_material_request,
    )
    from app.formal_services.material_request_policy import ApprovalLineDecision
    from app.formal_services.material_request_supply import (
        MaterialRequestSupplyError, SupplyTaskCreateInput, SupplyTaskUpdateInput,
        create_supply_task, update_supply_task,
    )
    from app.formal_services.material_request_supply_command_status import material_request_supply_command_status
    from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
    from app.inventory_models import InventoryTransaction
    from app.routers import formal_material_requests
    from test_material_request_approval_service import _approve, _evidence, _principal, _register_external, _verify_external
    from test_material_request_draft_service import SECRET, _draft

    request_id = uuid.uuid4()
    with Session(api_engine) as db:
        source = db.get(MaterialRequest, source_request_id)
        requester_id = source.requester_user_id
        line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.request_id == source.id)
                         .order_by(MaterialRequestLine.line_no))
        attachment = db.scalar(select(MaterialRequestFile).where(MaterialRequestFile.request_id == source.id)
                               .order_by(MaterialRequestFile.id))
        verifier_id = db.scalar(select(ApprovalExternalRegistration.verified_by_user_id)
            .join(ApprovalStep, ApprovalStep.id == ApprovalExternalRegistration.step_id)
            .join(ApprovalInstance, ApprovalInstance.id == ApprovalStep.instance_id)
            .where(ApprovalInstance.request_id == source.id, ApprovalExternalRegistration.status == "accepted"))
        assert verifier_id and verifier_id != admin_user_id
        world = SimpleNamespace(actor_person=SimpleNamespace(id=source.requester_person_id),
            materials=(SimpleNamespace(id=line.material_id),), attachment=SimpleNamespace(id=attachment.file_id))
        draft = _draft(world, request_id)
        created = create_material_request_draft(db, actor=_principal(db, requester_id),
            material_request_id=request_id, draft=draft, idempotency_key="pg16-supply-demand-create",
            idempotency_hmac_secret=SECRET, trace_request_id="pg16-supply-demand-create-trace")
        db.commit()
    with Session(api_engine) as db:
        submitted = submit_material_request(db, actor=_principal(db, requester_id), material_request_id=request_id,
            expected_version=created.request_version, idempotency_key="pg16-supply-demand-submit",
            idempotency_hmac_secret=SECRET, trace_request_id="pg16-supply-demand-submit-trace")
        db.commit()
    version = submitted.version
    for user_id, key in ((manager_user_id, "pg16-supply-region-approve"), (admin_user_id, "pg16-supply-hq-approve")):
        with Session(api_engine) as db:
            request = db.get(MaterialRequest, request_id)
            line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.request_id == request_id))
            line_id = line.id
            _, approved = _approve(db, actor=_principal(db, user_id), request=request, request_version=version,
                quantities={line_id: Decimal("2.000")}, key=key)
            version = approved.request_version
            db.commit()
    with Session(api_engine) as db:
        request = db.get(MaterialRequest, request_id)
        evidence = _evidence(db, uploaded_by=admin_user_id, marker="pg16-supply-evidence")
        step, registered = _register_external(db, actor=_principal(db, admin_user_id), request=request,
            request_version=version, evidence=evidence, key="pg16-supply-register", action="approve",
            lines=(ApprovalLineDecision(line_id, Decimal("2.000"), "同意"),))
        step_id = step.id
        db.commit()
    with Session(api_engine) as db:
        final = _verify_external(db, actor=_principal(db, verifier_id), request=db.get(MaterialRequest, request_id),
            step=db.get(ApprovalStep, step_id), registration_id=registered.registration_id,
            request_version=registered.request_version, step_version=registered.step_version, key="pg16-supply-verify")
        version = final.request_version
        db.commit()

    neutral_models = (InventoryTransaction, NotificationEvent, OutboxEvent)
    with Session(api_engine) as db:
        neutral_counts = tuple(db.scalar(select(func.count()).select_from(model)) for model in neutral_models)
        request = db.get(MaterialRequest, request_id)
        before_axes = {key: getattr(request, key) for key in final.state_axes}
    plan = SupplyTaskCreateInput(request_line_id=line_id, supply_type="star_replenishment",
        reference_no=None, expected_qty=Decimal("2.000"), expected_date=None)

    def api_db():
        with Session(api_engine) as db:
            yield db

    def api_principal():
        with Session(api_engine) as db:
            return _principal(db, admin_user_id)

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic",
        material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode("utf-8"),
        material_request_contact_mobile_hmac_secret=(
            "pg16-supply-contact-mobile-hmac-secret-v1"
        ),
        material_request_contact_mobile_hash_version=1,
        material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-pg16-supply-contact",
        auth_idempotency_kms_key_id="kms-pg16-auth-distinct",
    )
    supply_api = FastAPI()
    formal_material_requests.install_formal_material_request_validation_exception_handler(
        supply_api
    )
    supply_api.include_router(formal_material_requests.router, prefix="/api")
    supply_api.include_router(
        formal_material_requests.command_status_router, prefix="/api"
    )
    supply_api.dependency_overrides[get_db] = api_db
    supply_api.dependency_overrides[get_formal_principal] = api_principal
    supply_api.dependency_overrides[get_settings] = lambda: settings

    response_keys = {
        "schema_version", "request_id", "action", "request_version",
        "revision_id", "revision_no", "approval_instance_id",
        "approval_attempt_no", "current_step_id", "states",
        "idempotency_replayed", "supply_task_id", "task_no",
        "task_status", "task_version",
    }
    write_headers = lambda operation: {
        "Idempotency-Key": f"pg16-supply-http-{operation}-idempotency",
        "X-Request-ID": f"pg16-supply-http-{operation}-trace",
    }

    def create(db, *, key, expected, actor_id=admin_user_id):
        return create_supply_task(db, actor=_principal(db, actor_id), material_request_id=request_id,
            expected_request_version=expected, plan=plan, idempotency_key=key,
            idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}")

    with Session(api_engine) as db:
        with pytest.raises(MaterialRequestSupplyError) as denied:
            create(db, key="pg16-supply-requester-denied", expected=version, actor_id=requester_id)
        assert denied.value.http_status_code == 403
        db.rollback()
    with Session(api_engine) as db:
        first = create(db, key="pg16-supply-create-one", expected=version)
        db.commit()
    with Session(api_engine) as db:
        replay = create(db, key="pg16-supply-create-one", expected=version)
        assert replay.replayed and replay.supply_task_id == first.supply_task_id
        db.commit()
    with Session(api_engine) as db:
        with pytest.raises(MaterialRequestSupplyError):
            create(db, key="pg16-supply-over-capacity", expected=first.request_version)
        db.rollback()
    with Session(api_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE public.supply_tasks SET reference_no='FORGED', version=version+1, "
                            "updated_at=clock_timestamp() WHERE id=:id"), {"id": first.supply_task_id})
            db.commit()
        db.rollback()

    def update(db, task, *, key, status, expected_version=None):
        return update_supply_task(db, actor=_principal(db, admin_user_id), material_request_id=request_id,
            supply_task_id=task.supply_task_id, expected_request_version=task.request_version if expected_version is None else expected_version,
            expected_task_version=task.task_version,
            update=SupplyTaskUpdateInput(status=status, reference_no="PG16-SUPPLY-REF", expected_date=None, comment="供给计划处理"),
            idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}")

    with Session(api_engine) as db:
        registered_plan = update(db, first, key="pg16-supply-register-reference", status="reference_registered")
        db.commit()
    with Session(api_engine) as db:
        cancelled = update(db, registered_plan, key="pg16-supply-cancel-plan", status="cancelled")
        db.commit()
    with Session(api_engine) as db:
        with pytest.raises(MaterialRequestSupplyError):
            update(db, cancelled, key="pg16-supply-reopen-denied", status="open")
        db.rollback()
    with Session(api_engine) as db:
        recovered = material_request_supply_command_status(db, actor=_principal(db, admin_user_id),
            trace_request_id="trace-pg16-supply-create-one")
        assert recovered.lookup_status == "confirmed"
        assert recovered.command.supply_task_id == first.supply_task_id
        assert recovered.command.task_status == "open"
        assert db.get(SupplyTask, first.supply_task_id).status == "cancelled"
        db.rollback()

    # Exercise the production HTTP composition boundary with the real service,
    # PG transaction, strict request/response models and trace-based GET.
    with TestClient(supply_api) as client:
        created_response = client.post(
            f"/api/v1/material-requests/{request_id}/supply-tasks",
            json={
                "expected_request_version": cancelled.request_version,
                "request_line_id": str(line_id),
                "supply_type": "star_replenishment",
                "reference_no": None,
                "expected_qty": "2.000",
                "expected_date": None,
                "note": "PG16 HTTP supply gate",
            },
            headers=write_headers("create"),
        )
        assert created_response.status_code == 201, created_response.text
        http_created = created_response.json()
        assert set(http_created) == response_keys
        assert http_created["action"] == "create_supply_task"
        assert http_created["request_version"] == cancelled.request_version + 1
        assert http_created["task_status"] == "open"
        assert http_created["task_version"] == 0
        assert http_created["states"] == {
            "request_status": "approved", **before_axes,
        }
        assert http_created["idempotency_replayed"] is False
        assert "no-store" in created_response.headers["Cache-Control"]

        task_path = (
            f"/api/v1/material-requests/{request_id}/supply-tasks/"
            f"{http_created['supply_task_id']}"
        )
        registered_response = client.post(
            task_path,
            json={
                "expected_request_version": http_created["request_version"],
                "expected_task_version": http_created["task_version"],
                "status": "reference_registered",
                "reference_no": "PG16-HTTP-SUPPLY-REF",
                "expected_date": "2026-09-20",
                "comment": "register exact HTTP reference",
            },
            headers=write_headers("update"),
        )
        assert registered_response.status_code == 200, registered_response.text
        http_registered = registered_response.json()
        assert set(http_registered) == response_keys
        assert http_registered["action"] == "update_supply_task"
        assert http_registered["request_version"] == http_created["request_version"] + 1
        assert http_registered["task_version"] == 1
        assert http_registered["task_status"] == "reference_registered"

        cancelled_response = client.post(
            task_path,
            json={
                "expected_request_version": http_registered["request_version"],
                "expected_task_version": http_registered["task_version"],
                "status": "cancelled",
                "reference_no": "PG16-HTTP-SUPPLY-REF",
                "expected_date": "2026-09-20",
                "comment": "cancel exact HTTP supply plan",
            },
            headers=write_headers("cancel"),
        )
        assert cancelled_response.status_code == 200, cancelled_response.text
        http_cancelled = cancelled_response.json()
        assert set(http_cancelled) == response_keys
        assert http_cancelled["action"] == "cancel_supply_task"
        assert http_cancelled["request_version"] == http_registered["request_version"] + 1
        assert http_cancelled["task_version"] == 2
        assert http_cancelled["task_status"] == "cancelled"

        status_response = client.get(
            "/api/v1/material-request-supply-command-status",
            params={"trace_request_id": "pg16-supply-http-create-trace"},
        )
        assert status_response.status_code == 200, status_response.text
        status_payload = status_response.json()
        assert set(status_payload) == {"schema_version", "lookup_status", "command"}
        assert status_payload["lookup_status"] == "confirmed"
        assert status_payload["command"]["supply_task_id"] == http_created["supply_task_id"]
        assert status_payload["command"]["task_status"] == "open"
        assert status_payload["command"]["task_version"] == 0
        assert "idempotency_replayed" not in status_payload["command"]
        assert "no-store" in status_response.headers["Cache-Control"]

    with Session(api_engine) as db:
        rolled_back = create(db, key="pg16-supply-intentional-rollback", expected=http_cancelled["request_version"])
        db.rollback()
    with Session(api_engine) as db:
        assert db.get(SupplyTask, rolled_back.supply_task_id) is None
        assert db.scalar(select(MaterialRequestCommand.id).where(
            MaterialRequestCommand.request_id == request_id,
            MaterialRequestCommand.target_version == rolled_back.request_version)) is None
        assert db.scalar(select(AuditEvent.id).where(AuditEvent.request_id == "trace-pg16-supply-intentional-rollback")) is None

    barrier = threading.Barrier(2)
    def concurrent(index):
        with Session(api_engine) as db:
            barrier.wait(timeout=15)
            try:
                result = create(db, key=f"pg16-supply-race-create-{index}", expected=http_cancelled["request_version"])
                db.commit()
                return result
            except MaterialRequestSupplyError as error:
                db.rollback()
                return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(concurrent, index) for index in range(2)]
        outcomes = [future.result(timeout=45) for future in futures]
    winners = [value for value in outcomes if not isinstance(value, MaterialRequestSupplyError)]
    losers = [value for value in outcomes if isinstance(value, MaterialRequestSupplyError)]
    assert len(winners) == len(losers) == 1
    with Session(api_engine) as db:
        last = update(db, winners[0], key="pg16-supply-cancel-race-winner", status="cancelled")
        db.commit()
    with Session(api_engine) as db:
        assert tuple(db.scalar(select(func.count()).select_from(model)) for model in neutral_models) == neutral_counts
        request = db.get(MaterialRequest, request_id)
        assert {key: getattr(request, key) for key in before_axes} == before_axes
        assert db.scalar(select(func.count()).select_from(SupplyTask).where(SupplyTask.request_line_id == line_id)) == 3
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "supply_task",
            StateTransitionEvent.aggregate_id.in_([
                str(first.supply_task_id), http_created["supply_task_id"],
                str(winners[0].supply_task_id),
            ]))) == 8
        # Once all plans have been cancelled, the original demand may be safely cancelled independently.
        cancelled_request = cancel_material_request(db, actor=_principal(db, requester_id), material_request_id=request_id,
            expected_version=last.request_version,
            cancellation=MaterialRequestCancelInput(reason="独立取消需求", lines=(MaterialRequestCancellationLineInput(
                request_line_id=line_id, cancelled_qty=Decimal("2.000"), reason="需求取消"),)),
            idempotency_key="pg16-supply-demand-cancel", idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-supply-demand-cancel")
        db.commit()
        assert cancelled_request.request_status == "cancelled"
    with Session(api_engine) as db:
        recovered_after_request_cancel = material_request_supply_command_status(
            db,
            actor=_principal(db, admin_user_id),
            trace_request_id="pg16-supply-http-create-trace",
        )
        assert recovered_after_request_cancel.lookup_status == "confirmed"
        assert recovered_after_request_cancel.command.supply_task_id == uuid.UUID(
            http_created["supply_task_id"]
        )
        assert recovered_after_request_cancel.command.task_status == "open"
        db.rollback()
    with TestClient(supply_api) as client:
        status_after_cancel = client.get(
            "/api/v1/material-request-supply-command-status",
            params={"trace_request_id": "pg16-supply-http-create-trace"},
        )
        assert status_after_cancel.status_code == 200, status_after_cancel.text
        assert status_after_cancel.json()["lookup_status"] == "confirmed"
        assert status_after_cancel.json()["command"]["task_status"] == "open"
    return request_id
