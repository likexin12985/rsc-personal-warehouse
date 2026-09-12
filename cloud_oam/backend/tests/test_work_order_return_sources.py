"""A return obligation, available stock and an accepted return are separate facts."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from app.database import get_db
from app.demand_models import WorkOrderMaterialLine
from app.foundation_models import AuditChainHead, OutboxEvent, SourceSystem, ExternalObject, ExternalObjectVersion
from app.inventory_models import InventoryLedgerHead, StockAccount, StockBalance, MaterialInventoryPolicy
from app.formal_services import inventory_posting as posting, work_order_material as material
from app.formal_services import work_order_replacements as paired, work_order_return_sources as returns
from app.formal_services.inventory_query import InventoryReadError
from app.work_order_return_schemas import WorkOrderReturnSelectionIn, WorkOrderReturnSelectionLineIn
from app.routers import formal_work_order_query as api
from test_work_order_material_options import db, world, stock
from test_work_order_replacement_preview import inputs
from test_work_order_removed_registration import counts, inventory
from test_work_order_completion import change_status


@pytest.fixture
def recovered(db, stock):
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        **inputs(stock), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
    account = db.get(StockAccount, origin.stock_account_id)
    return SimpleNamespace(parent=parent, origin=origin, account=account)


def sources(db, stock, order=None):
    return returns.return_sources(db, actor=stock.world.current_principal, work_order_id=(order or stock.orders[0]).id)


def request(stock, recovered, **changes):
    proofs = [dict(serial_id=sn.id, sku_code=stock.world.material.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code)
        for sn in stock.serials[:1]]
    row = WorkOrderReturnSelectionLineIn(**{**dict(source_recovery_line_id=recovered.origin.id,
        stock_account_id=recovered.account.id, quantity="1", serial_verifications=proofs), **changes})
    return WorkOrderReturnSelectionIn(operator_person_id=stock.actor.person_id, lines=(row,))


def preview(db, stock, value):
    return returns.preview_selection(db, actor=stock.world.current_principal, work_order_id=stock.orders[0].id, request=value)


def occupy_returned(db, stock, recovered, quantity="1"):
    line = material.WorkOrderMaterialLineInput(stock.world.material.id, recovered.account.id, Decimal(quantity),
        tuple(sn.id for sn in stock.serials[:1]), "used",
        tuple(material.SerialVerificationInput(sn.id, stock.world.material.sku_code, sn.serial_no, sn.qr_code)
            for sn in stock.serials[:1]))
    material.execute_occupy_operation(db, actor=stock.actor, work_order_id=stock.orders[1].id, lines=(line,),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()


def test_sources_and_whole_preview_are_query_only_and_preserve_pending_return(db, stock, recovered):
    baseline = counts(db), inventory(db)
    value = request(stock, recovered)
    db.execute(text("PRAGMA query_only=ON"))
    result = sources(db, stock)
    assert not result.blockers and len(result.items) == 1
    row = result.items[0]
    assert row.source_recovery_line_id == recovered.origin.id
    assert row.recovery_operation_id == recovered.parent.recover_operation_id
    assert row.stock_account_id == recovered.account.id and row.custodian_person_id == stock.actor.person_id
    assert row.owed_quantity == row.available_quantity == row.selectable_quantity == "1.000"
    assert all(sn.selectable for sn in row.serials)
    checked = preview(db, stock, value)
    assert checked.planning_status == "source_selection_only" and len(checked.lines) == 1
    assert checked.selection_hash == returns.selection_hash(work_order_id=stock.orders[0].id, request=value)
    assert checked.basis_hash == preview(db, stock, value).basis_hash
    assert checked.lines[0].source == row and checked.lines[0].selected_quantity == "1.000"
    assert len(checked.lines[0].selected_serials) == (1 if stock.tracked else 0)
    assert "qr_code" not in checked.model_dump_json() and "OPTIONS-QR" not in checked.model_dump_json()
    assert (counts(db), inventory(db)) == baseline and sources(db, stock).items == result.items
    assert not db.new and not db.dirty and not db.deleted


def test_moved_stock_does_not_erase_owed_quantity_or_borrow_other_stock(db, stock, recovered):
    occupy_returned(db, stock, recovered)
    value = request(stock, recovered)
    db.execute(text("PRAGMA query_only=ON"))
    row = sources(db, stock).items[0]
    assert row.owed_quantity == "1.000" and row.available_quantity == row.selectable_quantity == "0.000"
    assert all(not sn.selectable for sn in row.serials)
    with pytest.raises(InventoryReadError) as exc: preview(db, stock, value)
    assert exc.value.code == "work_order_return_quantity_insufficient"


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_distinct_recovery_lines_share_one_stock_budget_and_allow_partial_selection(db, stock, recovered):
    material.execute_recover_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(material.WorkOrderMaterialLineInput(stock.world.material.id, recovered.account.id, Decimal("1.125"),
            condition_before="used", target_stock_account_id=recovered.account.id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    occupy_returned(db, stock, recovered, "1")
    choices = sources(db, stock).items
    assert len(choices) == 2 and all(row.available_quantity == "1.125" for row in choices)
    value = WorkOrderReturnSelectionIn(operator_person_id=stock.actor.person_id,
        lines=tuple(WorkOrderReturnSelectionLineIn(source_recovery_line_id=row.source_recovery_line_id,
            stock_account_id=row.stock_account_id, quantity="1") for row in choices))
    db.execute(text("PRAGMA query_only=ON"))
    for line in value.lines:
        assert preview(db, stock, value.model_copy(update={"lines": (line,)})).planning_status == "source_selection_only"
    with pytest.raises(InventoryReadError) as exc: preview(db, stock, value)
    assert exc.value.code == "work_order_return_batch_stock_insufficient"
    partial = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"quantity": Decimal("0.625")}),
        value.lines[1].model_copy(update={"quantity": Decimal("0.500")}))})
    assert len(preview(db, stock, partial).lines) == 2
    reverse = partial.model_copy(update={"lines": tuple(reversed(partial.lines))})
    assert preview(db, stock, reverse).selection_hash == preview(db, stock, partial).selection_hash


@pytest.mark.parametrize("damage", ["foreign_origin", "other_account", "excess", "duplicate_origin", "other_operator"])
def test_invalid_selection_never_creates_a_partial_return(db, stock, recovered, damage):
    value = request(stock, recovered)
    if damage == "foreign_origin": value = request(stock, recovered, source_recovery_line_id=uuid4())
    elif damage == "other_account": value = request(stock, recovered, stock_account_id=stock.account.id)
    elif damage == "excess": value = request(stock, recovered, quantity="2")
    elif damage == "duplicate_origin": value = value.model_copy(update={"lines": value.lines * 2})
    else: value = value.model_copy(update={"operator_person_id": uuid4()})
    baseline = counts(db), inventory(db)
    db.execute(text("PRAGMA query_only=ON"))
    with pytest.raises(InventoryReadError): preview(db, stock, value)
    assert (counts(db), inventory(db)) == baseline and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
@pytest.mark.parametrize("damage", ["sku", "sn", "qr", "missing", "duplicate", "other_serial"])
def test_exact_original_sn_and_all_three_physical_codes_are_required(db, stock, recovered, damage):
    value = request(stock, recovered); proof = value.lines[0].serial_verifications[0]
    if damage == "missing": proofs = ()
    elif damage == "duplicate": proofs = (proof, proof)
    elif damage == "other_serial": proofs = (proof.model_copy(update={"serial_id": stock.serials[3].id}),)
    else: proofs = (proof.model_copy(update={{"sku": "sku_code", "sn": "serial_no", "qr": "qr_code"}[damage]: "WRONG"}),)
    value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"serial_verifications": proofs}),)})
    db.execute(text("PRAGMA query_only=ON"))
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)): preview(db, stock, value)


def test_closed_order_and_read_permission_can_prepare_independent_return(db, stock, recovered):
    change_status(db, stock.orders[0], "closed")
    stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements
        if not (row.resource == "work_order_material" and row.action == "operate")))
    db.execute(text("PRAGMA query_only=ON"))
    result = preview(db, stock, request(stock, recovered))
    assert result.work_order.status == "closed" and not result.work_order.can_operate


def test_foreign_work_order_and_corrupted_balance_do_not_expose_candidates(db, stock, recovered):
    with pytest.raises(posting.InventoryPostingError) as exc: sources(db, stock, stock.orders[2])
    assert exc.value.code == "work_order_not_found"
    db.get(StockBalance, recovered.account.id).quantity += 1; db.commit()
    with pytest.raises(InventoryReadError) as exc: sources(db, stock)
    assert exc.value.code == "inventory_projection_integrity_invalid"


def test_missing_recovery_proof_is_not_an_empty_return_list(db, stock, recovered):
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(recovered.parent.id)))
    assert event is not None
    db.delete(event); db.commit()
    with pytest.raises(posting.InventoryPostingError): sources(db, stock)


def test_reversed_recovery_is_not_an_active_return_source(db, stock, recovered):
    from app.formal_services.work_order_reversal_write import execute_reversal
    from test_work_order_reversal_write import submission
    value = submission(db, stock, parent=recovered.parent)
    execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    db.execute(text("PRAGMA query_only=ON"))
    assert not sources(db, stock).items
    with pytest.raises(InventoryReadError) as exc: preview(db, stock, request(stock, recovered))
    assert exc.value.code == "work_order_return_source_invalid"


@pytest.mark.parametrize("change", ["ledger", "audit", "source", "authority"])
def test_mid_read_drift_discards_candidates(db, stock, recovered, monkeypatch, change):
    original = returns._source_items
    def drift(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "ledger": db.scalar(select(InventoryLedgerHead)).next_cursor += 1; db.flush()
        elif change == "audit": db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == "material_request")).version += 1; db.flush()
        elif change == "source": stock.orders[0].updated_at += timedelta(seconds=1); db.flush()
        else: stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version + 1)
        return result
    monkeypatch.setattr(returns, "_source_items", drift)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)): sources(db, stock)


@pytest.mark.parametrize("bucket", ["available", "return_pending"])
def test_source_and_future_return_pending_hard_freezes_block_preview(db, stock, recovered, bucket):
    from test_inventory_posting import freeze_account_scope
    account = SimpleNamespace(**{key: getattr(recovered.account, key) for key in (
        "owner_org_id", "custodian_person_id", "location_id", "material_id", "lot_id", "condition_code")}, availability_bucket=bucket)
    freeze_account_scope(db, stock.world, account, freeze_mode="hard", scope_mode="filtered"); db.commit()
    db.execute(text("PRAGMA query_only=ON"))
    with pytest.raises(posting.InventoryPostingError) as exc: preview(db, stock, request(stock, recovered))
    assert exc.value.code == "inventory_scope_hard_frozen"


@pytest.mark.parametrize("change", ["policy", "freeze"])
def test_policy_or_freeze_changed_after_validation_discards_preview(db, stock, recovered, monkeypatch, change):
    from test_inventory_posting import freeze_account_scope
    original = posting._validate_tracking_rules
    def changed(*args, **kwargs):
        original(*args, **kwargs)
        if change == "policy":
            db.scalar(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id == stock.world.material.id)).effective_from -= timedelta(seconds=1)
        else:
            freeze_account_scope(db, stock.world, recovered.account, freeze_mode="hard", scope_mode="filtered")
        db.flush()
    monkeypatch.setattr(posting, "_validate_tracking_rules", changed)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)): preview(db, stock, request(stock, recovered))


@pytest.mark.parametrize("change", ["disabled", "stale"])
def test_source_health_blockers_preserve_obligations_but_prevent_selection(db, stock, recovered, change):
    external = db.get(ExternalObject, stock.orders[0].external_object_id)
    if change == "disabled":
        db.get(SourceSystem, external.source_system_id).enabled = False
    else:
        old = stock.orders[0].updated_at - timedelta(hours=2)
        stock.orders[0].updated_at = old; stock.orders[0].source_updated_at = old
        version = db.get(ExternalObjectVersion, external.current_version_id)
        version.source_updated_at = old; version.valid_from = old; version.created_at = old
    db.commit(); db.execute(text("PRAGMA query_only=ON"))
    result = sources(db, stock)
    assert len(result.items) == 1 and result.blockers == ("source_" + change,)
    with pytest.raises(InventoryReadError) as exc: preview(db, stock, request(stock, recovered))
    assert exc.value.code == "work_order_return_sources_blocked"


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_same_selection_gets_new_basis_when_other_stock_commands_change_availability(db, stock, recovered):
    value = request(stock, recovered, quantity="0.250")
    before = preview(db, stock, value)
    occupy_returned(db, stock, recovered, "0.500")
    after = preview(db, stock, value)
    assert before.selection_hash == after.selection_hash and before.basis_hash != after.basis_hash
    assert after.lines[0].source.owed_quantity == "1.000"
    assert after.lines[0].source.available_quantity == "0.500"


@pytest.mark.parametrize("bad_quantity", ["0", "-1", "NaN", "Infinity", "0.0001", "1000000000000000"])
def test_invalid_quantities_cannot_reach_source_validation(bad_quantity):
    with pytest.raises(ValidationError):
        WorkOrderReturnSelectionLineIn(source_recovery_line_id=uuid4(), stock_account_id=uuid4(), quantity=bad_quantity)


def test_return_source_routes_are_no_store_scoped_and_do_not_submit_returns(db, stock, recovered):
    app = FastAPI(); app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: stock.actor
    db.execute(text("PRAGMA query_only=ON"))
    path = f"/api/v1/work-orders/{stock.orders[0].id}/return-sources"
    with TestClient(app) as client:
        read = client.get(path)
        assert read.status_code == 200 and read.headers["cache-control"] == "private, no-store"
        checked = client.post(path + "/preview", json=request(stock, recovered).model_dump(mode="json"))
        assert checked.status_code == 200, checked.text
        assert checked.headers["cache-control"] == "private, no-store" and "qr_code" not in checked.text
        assert client.post(path).status_code == 405
        denied = client.get(f"/api/v1/work-orders/{stock.orders[2].id}/return-sources")
        assert denied.status_code == 404 and denied.headers["cache-control"] == "private, no-store"
        denied = client.post(path + "/preview", json=request(stock, recovered, stock_account_id=stock.account.id).model_dump(mode="json"))
        assert denied.status_code == 409 and denied.headers["cache-control"] == "private, no-store"
