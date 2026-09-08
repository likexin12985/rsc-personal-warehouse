"""Reservation acceptance inside the caller's validated disposable PG16 gate.

All business commands use the restricted API engine and actual inventory
posting. The bootstrap engine only provisions empty reserved accounts beside
the existing, formally established opening stocktake fixtures.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def _assert_reservation_catalog(api_engine, security_engine):
    from app.database_security import (
        DatabaseSecurityBoundaryError, EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS,
        validate_production_database_security,
    )

    def validate():
        validate_production_database_security(
            api_engine, expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )

    def rejected(mutation, restoration):
        with security_engine.begin() as connection:
            connection.execute(text(mutation))
        try:
            with pytest.raises(DatabaseSecurityBoundaryError):
                validate()
        finally:
            with security_engine.begin() as connection:
                connection.execute(text(restoration))
        validate()

    # Real catalogs, with restoration even when a negative unexpectedly passes.
    for name, binding in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items():
        if name.endswith("_0069"):
            rejected(f"ALTER TABLE public.{binding[0]} DISABLE TRIGGER {name}",
                     f"ALTER TABLE public.{binding[0]} ENABLE ALWAYS TRIGGER {name}")
    for name in ("rsc_guard_stock_reservation_0069",
                 "rsc_guard_stock_reservation_serials_binding_0069"):
        signature = f"public.{name}()"
        rejected(f"ALTER FUNCTION {signature} SECURITY INVOKER",
                 f"ALTER FUNCTION {signature} SECURITY DEFINER")
        rejected(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC",
                 f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        with security_engine.connect() as connection:
            original_definition = connection.scalar(text(
                "SELECT pg_get_functiondef(to_regprocedure(:signature))"
            ), {"signature": signature})
        rejected(f"CREATE OR REPLACE FUNCTION {signature} RETURNS trigger LANGUAGE plpgsql "
                 "SECURITY DEFINER SET search_path = pg_catalog, public "
                 "AS $$ BEGIN RETURN NEW; END $$", original_definition)


def _approved_request(api_engine, *, source_request_id, material_id, manager_user_id,
                      admin_user_id, token):
    from app.demand_models import (
        ApprovalExternalRegistration, ApprovalInstance, ApprovalStep,
        MaterialRequest, MaterialRequestFile, MaterialRequestLine,
    )
    from app.formal_services.material_request_draft import (
        create_material_request_draft, derive_material_request_create_id,
        submit_material_request,
    )
    from app.formal_services.material_request_policy import ApprovalLineDecision
    from test_material_request_approval_service import (
        _approve, _evidence, _principal, _register_external, _verify_external,
    )
    from test_material_request_draft_service import SECRET, _draft

    with Session(api_engine) as db:
        source = db.get(MaterialRequest, source_request_id)
        requester_id = source.requester_user_id
        attachment_id = db.scalar(select(MaterialRequestFile.file_id).where(
            MaterialRequestFile.request_id == source.id,
        ).order_by(MaterialRequestFile.id))
        verifier_id = db.scalar(select(ApprovalExternalRegistration.verified_by_user_id)
            .join(ApprovalStep, ApprovalStep.id == ApprovalExternalRegistration.step_id)
            .join(ApprovalInstance, ApprovalInstance.id == ApprovalStep.instance_id)
            .where(ApprovalInstance.request_id == source.id,
                   ApprovalExternalRegistration.status == "accepted"))
        assert verifier_id and verifier_id != admin_user_id
        actor = _principal(db, requester_id)
        key = f"{token}-create"
        request_id = derive_material_request_create_id(
            actor=actor, idempotency_key=key, idempotency_hmac_secret=SECRET,
        )
        world = SimpleNamespace(
            actor_person=SimpleNamespace(id=source.requester_person_id),
            materials=(SimpleNamespace(id=material_id),),
            attachment=SimpleNamespace(id=attachment_id),
        )
        created = create_material_request_draft(
            db, actor=actor, material_request_id=request_id, draft=_draft(world, request_id),
            idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}",
        )
        db.commit()
    with Session(api_engine) as db:
        submitted = submit_material_request(
            db, actor=_principal(db, requester_id), material_request_id=request_id,
            expected_version=created.request_version, idempotency_key=f"{token}-submit",
            idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{token}-submit",
        )
        db.commit()
    version = submitted.version
    for user_id, suffix in ((manager_user_id, "region"), (admin_user_id, "hq")):
        with Session(api_engine) as db:
            line_id = db.scalar(select(MaterialRequestLine.id).where(
                MaterialRequestLine.request_id == request_id,
            ))
            _, approved = _approve(
                db, actor=_principal(db, user_id), request=db.get(MaterialRequest, request_id),
                request_version=version, quantities={line_id: Decimal("2.000")},
                key=f"{token}-{suffix}",
            )
            version = approved.request_version
            db.commit()
    with Session(api_engine) as db:
        evidence = _evidence(db, uploaded_by=admin_user_id, marker=f"{token}-evidence")
        step, registered = _register_external(
            db, actor=_principal(db, admin_user_id), request=db.get(MaterialRequest, request_id),
            request_version=version, evidence=evidence, key=f"{token}-register", action="approve",
            lines=(ApprovalLineDecision(line_id, Decimal("2.000"), "同意"),),
        )
        step_id = step.id
        db.commit()
    with Session(api_engine) as db:
        final = _verify_external(
            db, actor=_principal(db, verifier_id), request=db.get(MaterialRequest, request_id),
            step=db.get(ApprovalStep, step_id), registration_id=registered.registration_id,
            request_version=registered.request_version, step_version=registered.step_version,
            key=f"{token}-verify",
        )
        db.commit()
    return request_id, line_id, final.request_version, requester_id


def assert_reservation_gate(api_engine, *, security_engine, source_request_id,
                            manager_user_id, admin_user_id, inventory_fixture):
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.demand_models import MaterialRequest, MaterialRequestCommand
    from app.dependencies import get_formal_principal
    from app.formal_services.audit_chain import AuditChainError
    from app.formal_services.material_request_allocation import AllocationCreateInput, create_allocation
    from app.formal_services.material_request_reservation import (
        MaterialRequestReservationError, ReservationCreateInput, create_reservation,
    )
    from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
    from app.inventory_models import (
        InventoryLedgerHead, InventoryMovement, InventoryMovementSerial, InventoryTransaction,
        SerialCurrentPosition, StockAccount, StockBalance,
        StockReservation, StockReservationSerial,
    )
    from app.routers import formal_material_requests
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET

    _assert_reservation_catalog(api_engine, security_engine)

    def api_db():
        with Session(api_engine) as db:
            assert db.scalar(text("SELECT current_user")) == "star_oam_api"
            yield db

    def api_principal():
        with Session(api_engine) as db:
            return _principal(db, admin_user_id)

    settings = Settings(
        _env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic", material_request_writes_enabled=True,
        material_request_idempotency_hmac_secret=SECRET.decode(),
        material_request_contact_mobile_hmac_secret="pg16-reservation-contact-mobile-secret",
        material_request_contact_mobile_hash_version=1,
        material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-pg16-reservation-contact",
        auth_idempotency_kms_key_id="kms-pg16-auth-distinct",
    )
    app = FastAPI()
    formal_material_requests.install_formal_material_request_validation_exception_handler(app)
    app.include_router(formal_material_requests.router, prefix="/api")
    app.include_router(formal_material_requests.command_status_router, prefix="/api")
    app.dependency_overrides[get_db] = api_db
    app.dependency_overrides[get_formal_principal] = api_principal
    app.dependency_overrides[get_settings] = lambda: settings

    for serial_mode, source_id, serial_ids in (
        (False, inventory_fixture["account_id"], ()),
        (True, inventory_fixture["serial_replay_account_id"], (
            inventory_fixture["serial_replay_extra_serial_id"],
            inventory_fixture["serial_replay_serial_id"],
        )),
    ):
        token = f"pg16-reservation-{'serial' if serial_mode else 'quantity'}"
        with Session(security_engine) as db:
            db.execute(text("SET LOCAL ROLE star_oam_migrator"))
            source = db.get(StockAccount, source_id)
            material_id = source.material_id
            target_id = uuid.uuid4()
            db.add(StockAccount(
                id=target_id, owner_org_id=source.owner_org_id,
                custodian_person_id=source.custodian_person_id, location_id=source.location_id,
                material_id=material_id, condition_code=source.condition_code,
                availability_bucket="reserved", lot_id=source.lot_id,
            ))
            db.flush()
            db.add(StockBalance(stock_account_id=target_id, quantity=Decimal("0.000"),
                                ledger_cursor=0, version=0))
            db.commit()

        request_id, line_id, version, requester_id = _approved_request(
            api_engine, source_request_id=source_request_id, material_id=material_id,
            manager_user_id=manager_user_id, admin_user_id=admin_user_id, token=token,
        )
        with TestClient(app) as client:
            sources = client.get(f"/api/v1/material-requests/{request_id}/allocation-options",
                                 params={"request_line_id": str(line_id)})
            assert sources.status_code == 200, sources.text
            assert str(source_id) in {item["stock_account_id"] for item in sources.json()["items"]}
        with Session(api_engine) as db:
            source_balance = db.get(StockBalance, source_id)
            source_quantity = source_balance.quantity
            assert source_quantity >= Decimal("2.000")
            inventory_count = db.scalar(select(func.count()).select_from(InventoryTransaction))
            notification_count = db.scalar(select(func.count()).select_from(NotificationEvent))
            allocation = create_allocation(
                db, actor=_principal(db, admin_user_id), material_request_id=request_id,
                expected_request_version=version,
                allocation=AllocationCreateInput(
                    request_line_id=line_id, source_stock_account_id=source_id,
                    allocated_qty=Decimal("2.000"), source_balance_version=source_balance.version,
                    source_ledger_cursor=source_balance.ledger_cursor, serial_ids=serial_ids,
                ),
                idempotency_key=f"{token}-allocate", idempotency_hmac_secret=SECRET,
                trace_request_id=f"trace-{token}-allocate",
            )
            db.commit()
        with Session(api_engine) as db:
            assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == inventory_count
            assert db.get(StockBalance, source_id).quantity == source_quantity
            request = db.get(MaterialRequest, request_id)
            unchanged_axes = {key: getattr(request, "status" if key == "request_status" else key)
                              for key in allocation.state_axes
                              if key != "reservation_status"}
            assert request.reservation_status == "not_reserved"

        with TestClient(app) as client:
            option_response = client.get(f"/api/v1/material-requests/{request_id}/reservation-options",
                                         params={"request_line_id": str(line_id)})
            assert option_response.status_code == 200, option_response.text
            candidates = option_response.json()
            assert len(candidates["items"]) == 1
            candidate = candidates["items"][0]
            assert candidate["allocation_id"] == str(allocation.allocation_id)
            assert candidate["reservable_qty"] == "2.000"
            assert {item["serial_id"] for item in candidate["serial_options"]} == {
                str(value) for value in serial_ids
            }
            payload = {
                "expected_request_version": allocation.request_version,
                "request_line_id": str(line_id), "allocation_id": str(allocation.allocation_id),
                "reserved_qty": "2.000" if serial_mode else "0.500",
                "source_balance_version": candidate["balance_version"],
                "source_ledger_cursor": candidate["ledger_cursor"],
                "serial_ids": [str(value) for value in serial_ids],
            }
            headers = {"Idempotency-Key": f"{token}-http-create",
                       "X-Request-ID": f"trace-{token}-http-create"}
            domain_input = ReservationCreateInput(
                request_line_id=line_id, allocation_id=allocation.allocation_id,
                reserved_qty=Decimal(payload["reserved_qty"]),
                source_balance_version=payload["source_balance_version"],
                source_ledger_cursor=payload["source_ledger_cursor"], serial_ids=serial_ids,
            )

            def create(db, *, key, expected=allocation.request_version,
                       value=domain_input, actor_id=admin_user_id):
                return create_reservation(
                    db, actor=_principal(db, actor_id), material_request_id=request_id,
                    expected_request_version=expected, reservation=value,
                    idempotency_key=key, idempotency_hmac_secret=SECRET,
                    trace_request_id=f"trace-{key}",
                )

            def snapshot():
                with Session(api_engine) as db:
                    counts = tuple(db.scalar(select(func.count()).select_from(model)) for model in (
                        InventoryTransaction, InventoryMovement, InventoryMovementSerial,
                        StockReservation, StockReservationSerial, MaterialRequestCommand,
                        AuditEvent, OutboxEvent, StateTransitionEvent,
                    ))
                    balances = tuple(db.execute(select(StockBalance).where(
                        StockBalance.stock_account_id.in_((source_id, target_id)),
                    ).order_by(StockBalance.stock_account_id)).scalars())
                    return (counts, tuple((b.stock_account_id, b.quantity, b.version, b.ledger_cursor)
                                          for b in balances),
                            db.get(MaterialRequest, request_id).version,
                            tuple(db.scalars(select(InventoryLedgerHead.next_cursor))),
                            tuple(db.execute(select(
                                SerialCurrentPosition.serial_id,
                                SerialCurrentPosition.stock_account_id,
                                SerialCurrentPosition.last_movement_id,
                            ).where(SerialCurrentPosition.serial_id.in_(serial_ids))
                              .order_by(SerialCurrentPosition.serial_id))))

            before = snapshot()
            for actor_id, value, status in (
                (requester_id, domain_input, 403),
                (admin_user_id, replace(domain_input, source_balance_version=999999), 409),
                (admin_user_id, replace(domain_input, reserved_qty=Decimal("3.000")), 412),
            ):
                with Session(api_engine) as db:
                    with pytest.raises(MaterialRequestReservationError) as denied:
                        create(db, key=f"{token}-denied-{status}", value=value, actor_id=actor_id)
                    assert denied.value.http_status_code == status
                    db.rollback()
                assert snapshot() == before

            for resource, action in (("material_request", "read"), ("inventory_transaction", "post")):
                # Edit an entitlement only inside this disposable transaction,
                # then execute the real service as the restricted API role.
                with Session(security_engine) as db:
                    db.execute(text("SET LOCAL ROLE star_oam_migrator"))
                    changed = db.execute(text(
                        "UPDATE public.role_permissions AS binding SET effect = 'deny' "
                        "FROM public.roles AS role, public.permissions AS permission "
                        "WHERE binding.role_id = role.id AND binding.permission_id = permission.id "
                        "AND role.code = 'admin' AND permission.resource = :resource "
                        "AND permission.action = :action AND permission.field_code = '' "
                        "RETURNING binding.role_id"
                    ), {"resource": resource, "action": action}).all()
                    assert len(changed) == 1
                    db.execute(text("SET LOCAL ROLE star_oam_api"))
                    with pytest.raises(MaterialRequestReservationError) as denied:
                        create(db, key=f"{token}-{resource}-explicit-deny")
                    assert denied.value.http_status_code == 403
                    db.rollback()
                assert snapshot() == before

            # Fail after real posting has succeeded. The public route must roll
            # back the ledger, projections, serial positions and all side effects.
            with patch("app.formal_services.material_request_reservation.append_audit_event",
                       side_effect=AuditChainError("isolated reservation audit failure")):
                failed = client.post(f"/api/v1/material-requests/{request_id}/reservations",
                                     json=payload, headers=headers)
            if (failed.status_code != 503 or failed.json().get("detail", {}).get("code")
                    != "material_request_reservation_audit_unavailable"):
                # An unexpected database failure may be hidden by the route's
                # public error contract. Reproduce inside a rollback-only
                # session so the caller's sanitized diagnostic boundary can
                # report its constraint/SQLSTATE without exposing SQL values.
                with Session(api_engine) as db:
                    create(db, key=f"{token}-diagnose-rolled-back-http")
            assert failed.status_code == 503, failed.text
            assert failed.json()["detail"]["code"] == "material_request_reservation_audit_unavailable"
            assert snapshot() == before

            if serial_mode:
                serial_barrier = Barrier(2)

                def reserve_same_serials(index):
                    race_headers = {
                        "Idempotency-Key": f"{token}-http-race-{index}",
                        "X-Request-ID": f"trace-{token}-http-race-{index}",
                    }
                    serial_barrier.wait(timeout=15)
                    return client.post(f"/api/v1/material-requests/{request_id}/reservations",
                                       json=payload, headers=race_headers), race_headers

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(reserve_same_serials, index) for index in range(2)]
                    responses = [future.result(timeout=45) for future in futures]
                assert sorted(response.status_code for response, _ in responses) == [201, 409], [
                    response.text for response, _ in responses
                ]
                created, headers = next(pair for pair in responses if pair[0].status_code == 201)
            else:
                created = client.post(f"/api/v1/material-requests/{request_id}/reservations",
                                      json=payload, headers=headers)
            assert created.status_code == 201, created.text
            first = created.json()
            assert first["serial_ids"] == payload["serial_ids"]
            assert first["state_axes"]["reservation_status"] == ("reserved" if serial_mode else "pending")
            after = snapshot()
            replay = client.post(f"/api/v1/material-requests/{request_id}/reservations",
                                 json=payload, headers=headers)
            assert replay.status_code == 201, replay.text
            assert replay.json()["idempotency_replayed"] is True
            assert replay.json()["reserve_transaction_id"] == first["reserve_transaction_id"]
            assert snapshot() == after

            latest_version = first["request_version"]
            if not serial_mode:
                with Session(api_engine) as db:
                    balance = db.get(StockBalance, source_id)
                    remainder = replace(domain_input, reserved_qty=Decimal("1.500"),
                                        source_balance_version=balance.version,
                                        source_ledger_cursor=balance.ledger_cursor)
                barrier = Barrier(2)

                def concurrent(index):
                    with Session(api_engine) as db:
                        barrier.wait(timeout=15)
                        try:
                            result = create(db, key=f"{token}-race-{index}",
                                            expected=latest_version, value=remainder)
                            db.commit()
                            return result
                        except MaterialRequestReservationError as error:
                            db.rollback()
                            return error

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(concurrent, index) for index in range(2)]
                    outcomes = [future.result(timeout=45) for future in futures]
                winners = [outcome for outcome in outcomes if not isinstance(outcome, MaterialRequestReservationError)]
                losers = [outcome for outcome in outcomes if isinstance(outcome, MaterialRequestReservationError)]
                assert len(winners) == len(losers) == 1
                assert losers[0].http_status_code == 409
                latest_version = winners[0].request_version

            recovered = client.get("/api/v1/material-request-reservation-command-status", headers=headers)
            assert recovered.status_code == 200, recovered.text
            historical = recovered.json()["command"]
            assert recovered.json()["lookup_status"] == "confirmed"
            assert historical["request_version"] == first["request_version"]
            assert historical["current_request_version"] == latest_version
            assert historical["state_axes"] == first["state_axes"]
            assert historical["reserve_transaction_id"] == first["reserve_transaction_id"]
            assert historical["serial_ids"] == payload["serial_ids"]
            exhausted = client.get(f"/api/v1/material-requests/{request_id}/reservation-options",
                                   params={"request_line_id": str(line_id)})
            assert exhausted.status_code == 200, exhausted.text
            assert exhausted.json()["items"] == []

        with Session(api_engine) as db:
            request = db.get(MaterialRequest, request_id)
            assert request.reservation_status == "reserved"
            assert {key: getattr(request, "status" if key == "request_status" else key)
                    for key in unchanged_axes} == unchanged_axes
            assert db.get(StockBalance, source_id).quantity == source_quantity - Decimal("2.000")
            assert db.get(StockBalance, target_id).quantity == Decimal("2.000")
            assert db.scalar(select(func.count()).select_from(NotificationEvent)) == notification_count
            facts = tuple(db.scalars(select(StockReservation).where(StockReservation.request_id == request_id)))
            assert len(facts) == (1 if serial_mode else 2)
            assert sum(fact.reserved_qty for fact in facts) == Decimal("2.000")
            for fact in facts:
                movement = db.scalar(select(InventoryMovement).where(
                    InventoryMovement.transaction_id == fact.reserve_transaction_id,
                ))
                assert (movement.from_account_id, movement.to_account_id, movement.quantity) == (
                    source_id, target_id, fact.reserved_qty,
                )
                assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
                    OutboxEvent.aggregate_type == "inventory_transaction",
                    OutboxEvent.aggregate_id == str(fact.reserve_transaction_id),
                )) == 1
            for serial_id in serial_ids:
                assert db.get(SerialCurrentPosition, serial_id).stock_account_id == target_id

        # The service cannot mutate an old reservation, including on replica
        # sessions with migrator privileges: the triggers are ENABLE ALWAYS.
        for role, expected_error in (("star_oam_api", "permission denied"),
                                     ("star_oam_migrator", "append-only")):
            with Session(security_engine) as db:
                db.execute(text("SET LOCAL session_replication_role = 'replica'"))
                db.execute(text(f"SET LOCAL ROLE {role}"))
                with pytest.raises(DBAPIError, match=expected_error):
                    db.execute(text("UPDATE public.stock_reservations SET released_qty = 1 WHERE request_id = :id"),
                               {"id": request_id})
                db.rollback()
