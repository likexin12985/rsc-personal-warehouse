from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from test_work_order_material_evidence import db, evidence, world, client_for
from app.formal_services import work_order_material as service
from app.formal_services.work_order_accounts import resolve_work_order_reserved_lines
from app.inventory_models import StockAccount, StockBalance


@pytest.fixture
def source(db, evidence):
    old = evidence.available
    row = StockAccount(id=uuid4(), owner_org_id=old.owner_org_id,
        custodian_person_id=old.custodian_person_id, location_id=old.location_id,
        material_id=old.material_id, condition_code="used", lot_id=old.lot_id,
        availability_bucket="available")
    db.add(row); db.commit()
    return row


def inputs(source, **changes):
    return replace(service.WorkOrderMaterialLineInput(source.material_id, source.id,
        Decimal(1), condition_before=source.condition_code), **changes)


def resolve(db, source, *, create=True, **changes):
    return resolve_work_order_reserved_lines(db, operator_person_id=source.custodian_person_id,
        lines=(inputs(source, **changes),), create=create)[0]


def targets(db, source):
    return tuple(db.scalars(select(StockAccount).where(StockAccount.location_id == source.location_id,
        StockAccount.material_id == source.material_id, StockAccount.condition_code == source.condition_code,
        StockAccount.availability_bucket == "reserved")))


def test_server_created_target_keeps_all_source_dimensions_and_no_initial_balance(db, source):
    balance_count = db.scalar(select(func.count()).select_from(StockBalance))
    line = resolve(db, source)
    target = db.get(StockAccount, line.target_stock_account_id)
    for key in ("owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id"):
        assert getattr(target, key) == getattr(source, key)
    assert target.id != source.id and target.availability_bucket == "reserved"
    assert target.created_at == target.updated_at
    assert db.scalar(select(func.count()).select_from(StockBalance)) == balance_count
    assert resolve(db, source).target_stock_account_id == target.id
    assert resolve(db, source, target_stock_account_id=target.id).target_stock_account_id == target.id
    assert len(targets(db, source)) == 1


def test_replay_never_creates_missing_target(db, source):
    with pytest.raises(service.InventoryPostingError) as exc:
        resolve(db, source, create=False)
    assert exc.value.code == "occupy_target_missing" and targets(db, source) == ()


@pytest.mark.parametrize("change", ["other_person", "wrong_material", "wrong_condition", "unavailable", "inactive_location", "explicit_missing_target", "explicit_other_dimension"])
def test_wrong_source_or_target_cannot_create_an_account(db, source, evidence, change):
    line = inputs(source)
    if change == "other_person":
        source.custodian_person_id = evidence.world.headquarters_reviewer_person.id
    elif change == "wrong_material":
        line = replace(line, material_id=uuid4())
    elif change == "wrong_condition":
        line = replace(line, condition_before="new")
    elif change == "unavailable":
        source.availability_bucket = "frozen"
    elif change == "inactive_location":
        evidence.location.status = "inactive"
    else:
        line = replace(line, target_stock_account_id=uuid4() if change == "explicit_missing_target" else evidence.account.id)
    db.flush()
    with pytest.raises(service.InventoryPostingError):
        resolve_work_order_reserved_lines(db, operator_person_id=evidence.world.person.id, lines=(line,), create=True)
    assert targets(db, source) == ()


def test_same_sku_in_distinct_accounts_preserves_each_condition(db, source, evidence):
    lines = (inputs(evidence.available), inputs(source))
    service.validate_batch(lines)
    values = resolve_work_order_reserved_lines(db, operator_person_id=source.custodian_person_id, lines=lines, create=True)
    assert values[0].target_stock_account_id != values[1].target_stock_account_id
    assert [db.get(StockAccount, row.target_stock_account_id).condition_code for row in values] == ["new", "used"]


def test_http_posting_failure_rolls_back_new_reserved_account(db, source, evidence, monkeypatch):
    seen = []
    def fail_post(db, **kwargs):
        target_id = kwargs["command"].movements[0].to_account_id
        assert db.get(StockAccount, target_id) is not None
        seen.append(target_id)
        raise service.InventoryPostingError("insufficient_stock", "conflict", "库存不足")
    monkeypatch.setattr(service, "post_inventory_transaction", fail_post)
    payload = dict(operator_person_id=str(source.custodian_person_id), idempotency_key="new-reserved-fails",
        request_id="new-reserved-fails-trace", lines=[dict(material_id=str(source.material_id),
            stock_account_id=str(source.id), condition_before="used", quantity="1")])
    with client_for(db, evidence) as client:
        response = client.post(f"/api/v1/work-orders/{evidence.order.id}/material-operations/occupy", json=payload)
    assert response.status_code == 409, response.text
    assert seen and targets(db, source) == () and db.get(StockAccount, source.id) is not None
