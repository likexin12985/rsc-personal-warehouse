from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import material_request_allocation_options as service
from app.formal_services import inventory_query, material_request_query
from app.material_request_allocation_option_schemas import (
    MaterialRequestAllocationOptionPageOut,
)
from app.routers import formal_material_requests


NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _id(value: int) -> uuid.UUID:
    return uuid.UUID(f"10000000-0000-4000-8000-{value:012d}")


def _row(
    number: int,
    *,
    material_id: uuid.UUID,
    bucket: str = "available",
    quantity: str = "2.500",
    location_status: str = "active",
    material_status: str = "active",
):
    owner = SimpleNamespace(id=_id(100 + number), code=f"ORG-{number}", name=f"组织{number}")
    location_owner = SimpleNamespace(id=_id(200 + number), code=f"LOCORG-{number}", name=f"库位组织{number}")
    location = SimpleNamespace(
        id=_id(300 + number), code=f"LOC-{number}", name=f"库位{number}",
        location_type="headquarters", parent_id=None, owner_org_id=location_owner.id,
        status=location_status,
    )
    material = SimpleNamespace(
        id=material_id, sku_code="MAT-001", name="测试物料", base_unit="件",
        status=material_status,
    )
    account = SimpleNamespace(
        id=_id(400 + number), owner_org_id=owner.id, location_id=location.id,
        material_id=material_id, condition_code="new", availability_bucket=bucket,
    )
    balance = SimpleNamespace(
        stock_account_id=account.id, quantity=Decimal(quantity), ledger_cursor=3, version=7,
    ) if quantity is not None else None
    return SimpleNamespace(
        account=account, location=location, material=material,
        owner_org=owner, location_owner_org=location_owner,
        custodian=None, lot=None, balance=balance,
    )


def _view(request_id: uuid.UUID, line_id: uuid.UUID, *, version: int = 4):
    revision_id = _id(20)
    line = SimpleNamespace(
        request_line_id=line_id, revision_id=revision_id, revision_no=2,
        material_id=_id(1), final_approved_qty=Decimal("5.000"),
        cancelled_qty=Decimal("1.000"), status="approved",
    )
    return SimpleNamespace(
        request_id=request_id, request_version=version,
        current_revision_id=revision_id, current_revision_no=2,
        lines=(line,), states=SimpleNamespace(request_status="approved"),
    )


def test_schema_rejects_extra_fields_and_keeps_fixed_scale_contract():
    with pytest.raises(ValidationError):
        MaterialRequestAllocationOptionPageOut(
            request_id=_id(1), request_line_id=_id(2), request_version=1,
            current_revision_id=_id(3), current_revision_no=1, material_id=_id(4),
            final_approved_qty="1.000", cancelled_qty="0.000", allocatable_qty="1.000",
            projection_status="ready", opening_balance_status="established",
            projected_at=NOW, ledger_cursor=3, items=(), unexpected="x",
        )
    assert service._fixed_quantity(Decimal("2.5"), 3) == "2.500"
    assert service._fixed_quantity(Decimal("2"), 0) == "2"
    with pytest.raises(Exception):
        service._fixed_quantity(Decimal("2.501"), 0)


def test_candidates_filter_scope_rows_to_positive_available_active_material(monkeypatch):
    request_id, line_id, material_id = _id(500), _id(501), _id(1)
    view = _view(request_id, line_id)
    valid = _row(1, material_id=material_id)
    rows = [
        valid,
        _row(2, material_id=material_id, bucket="reserved"),
        _row(3, material_id=material_id, bucket="frozen"),
        _row(4, material_id=material_id, quantity="0.000"),
        _row(5, material_id=material_id, location_status="inactive"),
        _row(6, material_id=material_id, material_status="inactive"),
        _row(7, material_id=_id(99)),
    ]
    snapshot = SimpleNamespace(ledger_cursor=3, projected_at=NOW)
    monkeypatch.setattr(material_request_query, "material_request_detail", lambda *a, **k: view)
    monkeypatch.setattr(inventory_query, "_require_inventory_read", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_projection_snapshot", lambda *a, **k: snapshot)
    monkeypatch.setattr(inventory_query, "_authorized_account_rows", lambda *a, **k: rows)
    monkeypatch.setattr(inventory_query, "_validate_current_projection_integrity", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_validated_opening_evidence", lambda *a, **k: SimpleNamespace(complete=True))
    monkeypatch.setattr(inventory_query, "_ensure_balance_at_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_effective_policy", lambda *a, **k: SimpleNamespace(quantity_scale=3))

    class DB:
        no_autoflush = nullcontext()

    output = service.list_allocation_options(
        DB(), actor=SimpleNamespace(), material_request_id=request_id, request_line_id=line_id
    )
    assert isinstance(output, MaterialRequestAllocationOptionPageOut)
    assert [item.stock_account_id for item in output.items] == [valid.account.id]
    assert output.items[0].quantity == "2.500"
    assert output.items[0].balance_version == 7
    assert output.items[0].ledger_cursor == 3
    assert output.allocatable_qty == "4.000"


def test_unestablished_inventory_is_hard_blocked(monkeypatch):
    request_id, line_id = _id(600), _id(601)
    view = _view(request_id, line_id)
    monkeypatch.setattr(material_request_query, "material_request_detail", lambda *a, **k: view)
    monkeypatch.setattr(inventory_query, "_require_inventory_read", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_projection_snapshot", lambda *a, **k: SimpleNamespace(ledger_cursor=0, projected_at=None))
    monkeypatch.setattr(inventory_query, "_authorized_account_rows", lambda *a, **k: [])
    monkeypatch.setattr(inventory_query, "_validate_current_projection_integrity", lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, "_validated_opening_evidence", lambda *a, **k: SimpleNamespace(complete=False))

    class DB:
        no_autoflush = nullcontext()

    with pytest.raises(service.MaterialRequestAllocationOptionError) as captured:
        service.list_allocation_options(
            DB(), actor=SimpleNamespace(), material_request_id=request_id, request_line_id=line_id
        )
    assert captured.value.code == "inventory_opening_not_established"
    assert captured.value.http_status_code == 412


def test_router_allocation_options_is_get_only_and_no_store_on_success_and_error(monkeypatch):
    api = FastAPI()
    api.include_router(formal_material_requests.router, prefix="/api")

    class Principal:
        def allows(self, *_args, **_kwargs):
            return True

    class DB:
        pass

    api.dependency_overrides[get_formal_principal] = lambda: Principal()
    api.dependency_overrides[get_db] = lambda: DB()
    output = MaterialRequestAllocationOptionPageOut(
        request_id=_id(700), request_line_id=_id(701), request_version=4,
        current_revision_id=_id(702), current_revision_no=2, material_id=_id(1),
        final_approved_qty="5.000", cancelled_qty="1.000", allocatable_qty="4.000",
        projection_status="ready", opening_balance_status="established",
        projected_at=NOW, ledger_cursor=3, items=(),
    )
    mocked = lambda *args, **kwargs: output
    monkeypatch.setattr(service, "list_allocation_options", mocked)
    with TestClient(api) as client:
        response = client.get(
            f"/api/v1/material-requests/{_id(700)}/allocation-options",
            params={"request_line_id": str(_id(701))},
        )
        assert response.status_code == 200
        assert "no-store" in response.headers["Cache-Control"]

        def fail(*_args, **_kwargs):
            raise service.MaterialRequestAllocationOptionError(
                "inventory_opening_not_established", "precondition_failed", "库存期初建账尚未完成"
            )

        monkeypatch.setattr(service, "list_allocation_options", fail)
        response = client.get(
            f"/api/v1/material-requests/{_id(700)}/allocation-options",
            params={"request_line_id": str(_id(701))},
        )
        assert response.status_code == 412
        assert "no-store" in response.headers["Cache-Control"]
        assert "sql" not in response.text.lower()
