"""Completion observes independent ledger obligations and never closes OAM."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import get_db
from app.demand_models import WorkOrderMaterialOperation, WorkOrderMaterialLine
from app.foundation_models import AuditChainHead, ExternalObject, ExternalObjectVersion, OutboxEvent
from app.formal_services import work_order_completion as completion, work_order_material as material
from app.formal_services import work_order_replacements as paired, oam_work_order_projection as projection
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.inventory_query import InventoryReadError
from app.inventory_models import InventoryLedgerHead, SerialCurrentPosition, StockAccount
from app.routers import formal_work_order_query as router
from test_work_order_material_options import db, world, stock
from test_work_order_removed_registration import create, replacement_input
from work_order_fixtures import add_order, canonical_source


def check(db, stock, order=None):
    return completion.completion_check(db, actor=stock.world.current_principal, work_order_id=(order or stock.orders[0]).id)


def release(db, stock):
    material.execute_release_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(stock.line("1", stock.serials[1:2], identifier=stock.reserved.id, target=stock.account.id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()


def change_status(db, order, status):
    external = db.get(ExternalObject, order.external_object_id)
    version = db.get(ExternalObjectVersion, external.current_version_id)
    version.payload_jsonb = {**version.payload_jsonb, "status": status}
    version.payload_sha256 = projection._sha256(version.payload_jsonb)
    order.status = status
    db.commit()


def test_read_only_check_uses_this_orders_reservation_not_pooled_stock(db, stock):
    db.execute(text("PRAGMA query_only=ON"))
    result = check(db, stock)
    assert result.material_check_status == "blocked"
    reserved = [row for row in result.issues if row.kind == "unreleased_reservation"]
    assert len(reserved) == 1 and reserved[0].quantity == "1.000"
    assert reserved[0].reference_id == stock.reserved.id
    assert {row.serial_id for row in reserved[0].serials} == ({stock.serials[1].id} if stock.tracked else set())
    assert result.issue_count == (2 if stock.tracked else 1)
    assert "qr_code" not in result.model_dump_json() and "OPTIONS-QR" not in result.model_dump_json()
    assert not db.new and not db.dirty and not db.deleted


def test_release_clears_only_original_reservation_and_serial_consumption_needs_assessment(db, stock):
    release(db, stock)
    result = check(db, stock)
    if stock.tracked:
        assert result.blockers == ("unpaired_serial_consumption",)
        assert result.issues[0].serials[0].serial_id == stock.serials[0].id
    else:
        assert result.material_check_status == "clear" and result.issue_count == 0 and not result.blockers
    other = check(db, stock, stock.orders[1])
    assert other.issues[0].quantity == "1.000" and other.blockers == ("unreleased_reservation",)


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_identity_registration_becomes_pending_recovery_then_independent_pending_return(db, stock):
    admitted = create(db, stock); db.commit()
    pending = [row for row in check(db, stock).issues if row.kind == "pending_recovery"]
    assert len(pending) == 1 and pending[0].reference_id == admitted.registration_id
    assert pending[0].serials[0].serial_id == admitted.serial_id
    result = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        **replacement_input(stock, admitted), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    response = check(db, stock)
    assert "pending_recovery" not in response.blockers and "unreleased_reservation" not in response.blockers
    returned = [row for row in response.issues if row.kind == "pending_return"]
    assert len(returned) == 1 and returned[0].operation_id == result.recover_operation_id
    assert returned[0].quantity == "1.000" and returned[0].condition_code == "damaged"
    assert returned[0].serials[0].serial_id == admitted.serial_id
    # A later ordinary consume changes current stock, but is not a signed return
    # document and cannot release the original custody obligation.
    position = db.get(SerialCurrentPosition, admitted.serial_id)
    account = position.stock_account_id
    other = add_order(db, stock.world, canonical_source(db)); db.commit()
    recovered = replacement_input(stock, admitted)["recover_lines"][0]
    line = material.WorkOrderMaterialLineInput(recovered.material_id, account, Decimal(1), recovered.serial_ids,
        "damaged", recovered.serial_verifications)
    occupied, _ = material.execute_occupy_operation(db, actor=stock.actor, work_order_id=other.id, lines=(line,),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    from app.inventory_models import InventoryMovement
    reserved = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == occupied.posting_transaction_id))
    material.execute_consume_operation(db, actor=stock.actor, work_order_id=other.id, lines=(replace(line, stock_account_id=reserved),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    after = check(db, stock)
    assert [row for row in after.issues if row.kind == "pending_return"] == returned


def test_no_history_is_clear_only_with_verified_opening_and_fresh_source(db, stock):
    order = add_order(db, stock.world, canonical_source(db)); db.commit()
    result = check(db, stock, order)
    assert result.material_check_status == "clear" and not result.issues
    order.updated_at -= timedelta(hours=2); order.source_updated_at = order.updated_at
    external = db.get(ExternalObject, order.external_object_id)
    version = db.get(ExternalObjectVersion, external.current_version_id)
    version.source_updated_at = order.updated_at; version.valid_from = order.updated_at; version.created_at = order.updated_at
    db.commit()
    stale = check(db, stock, order)
    assert stale.material_check_status == "blocked" and stale.blockers == ("source_stale",)


def test_disabled_source_and_unestablished_opening_are_explicit_blockers(db, stock, monkeypatch):
    order = add_order(db, stock.world, canonical_source(db)); db.commit()
    source = canonical_source(db); source.enabled = False; db.commit()
    assert check(db, stock, order).blockers == ("source_disabled",)
    source.enabled = True; db.commit()
    original = completion.material_options
    def unestablished(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.model_copy(update={"opening_balance_status": "not_established", "items": ()})
    monkeypatch.setattr(completion, "material_options", unestablished)
    assert check(db, stock, order).blockers == ("opening_not_established",)


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_standalone_quantity_recoveries_keep_each_original_line_pending_return(db, stock):
    target = StockAccount(owner_org_id=stock.account.owner_org_id, custodian_person_id=stock.actor.person_id,
        location_id=stock.account.location_id, material_id=stock.world.material.id, lot_id=None,
        condition_code="used", availability_bucket="available")
    db.add(target); db.commit()
    for amount in ("1.125", "2.250"):
        material.execute_recover_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
            lines=(material.WorkOrderMaterialLineInput(stock.world.material.id, target.id, Decimal(amount),
                condition_before="used", target_stock_account_id=target.id),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
        db.commit()
    db.execute(text("PRAGMA query_only=ON"))
    rows = [row for row in check(db, stock).issues if row.kind == "pending_return"]
    assert sorted(row.quantity for row in rows) == ["1.125", "2.250"]
    assert len({row.reference_id for row in rows}) == 2 and len({row.operation_id for row in rows}) == 2
    assert all(not row.serials for row in rows)


def test_oam_closed_status_does_not_erase_material_obligations(db, stock):
    change_status(db, stock.orders[0], "closed")
    result = check(db, stock)
    assert result.work_order.status == "closed" and result.material_check_status == "blocked"
    assert "unreleased_reservation" in result.blockers


@pytest.mark.parametrize("drift", ["stock", "identity_audit", "source", "permission"])
def test_change_during_check_discards_the_whole_response(db, stock, monkeypatch, drift):
    original = completion._issues
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if drift == "stock": db.scalar(select(InventoryLedgerHead)).next_cursor += 1; db.flush()
        elif drift == "identity_audit":
            db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == "material_request")).version += 1; db.flush()
        elif drift == "source": stock.orders[0].updated_at += timedelta(seconds=1); db.flush()
        else: stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version + 1)
        return result
    monkeypatch.setattr(completion, "_issues", changed)
    with pytest.raises((InventoryReadError, InventoryPostingError)):
        check(db, stock)


def test_missing_original_operation_audit_or_notification_fact_never_becomes_a_clear_check(db, stock):
    operation = db.scalar(select(WorkOrderMaterialOperation).where(WorkOrderMaterialOperation.oam_work_order_id == stock.orders[0].id))
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(operation.id)))
    db.delete(event); db.commit()
    with pytest.raises(InventoryPostingError) as exc: check(db, stock)
    assert exc.value.code == "work_order_recovery_evidence_invalid"


def test_history_limit_fails_instead_of_returning_a_partial_clear_result(db, stock, monkeypatch):
    monkeypatch.setattr(completion, "MAX_HISTORY", 1)
    with pytest.raises(InventoryReadError) as exc: check(db, stock)
    assert exc.value.code == "work_order_completion_history_too_large"


def test_foreign_order_and_mid_history_other_handler_do_not_expose_their_materials(db, stock):
    with pytest.raises(InventoryPostingError) as exc: check(db, stock, stock.orders[2])
    assert exc.value.code == "work_order_not_found"
    # Mutable fixture corruption represents unresolved prior-handler scope;
    # never infer an empty obligation set from the current engineer's rows.
    operation = db.scalar(select(WorkOrderMaterialOperation).where(WorkOrderMaterialOperation.oam_work_order_id == stock.orders[0].id))
    operation.operator_person_id = stock.world.headquarters_reviewer_person.id; db.commit()
    result = check(db, stock)
    assert "history_scope_unresolved" in result.blockers
    assert str(stock.world.headquarters_reviewer_person.id) not in result.model_dump_json()


def test_http_completion_is_read_only_scoped_and_no_store(db, stock):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: stock.actor
    db.execute(text("PRAGMA query_only=ON"))
    path = f"/api/v1/work-orders/{stock.orders[0].id}/material-completion-check"
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["work_order"]["work_order_id"] == str(stock.orders[0].id)
        assert "qr_code" not in response.text
        assert client.post(path).status_code == 405
        assert client.get(f"/api/v1/work-orders/{stock.orders[2].id}/material-completion-check").status_code == 404
