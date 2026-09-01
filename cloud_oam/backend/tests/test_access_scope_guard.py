from __future__ import annotations

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.dependencies import enforce_legacy_route_scope
from app.models import InventoryBalance, Material, User, Warehouse
from app.routers.inventory import list_inventory


def user(*, role: str, province: str | None = "江苏省") -> User:
    return User(
        mobile=f"formal-{role}"[:20],
        name=role,
        password_hash="disabled",
        role=role,
        province=province,
        is_active=True,
        require_password_change=False,
    )


def test_external_approver_can_only_read_own_identity_before_approval_module_exists():
    approver = user(role="star_headquarters_approver", province=None)

    enforce_legacy_route_scope(approver, "/api/auth/me")
    with pytest.raises(HTTPException) as error:
        enforce_legacy_route_scope(approver, "/api/inventory")

    assert error.value.status_code == 403
    assert "受限审批" in error.value.detail


@pytest.mark.parametrize("role", ["provincial_manager", "technician"])
def test_internal_scoped_roles_without_scope_fail_closed(role: str):
    scoped_user = user(role=role, province=None)

    enforce_legacy_route_scope(scoped_user, "/api/auth/me")
    with pytest.raises(HTTPException) as error:
        enforce_legacy_route_scope(scoped_user, "/api/dashboard")

    assert error.value.status_code == 403
    assert "数据范围" in error.value.detail


def test_engineer_inventory_is_always_limited_to_self_even_without_mine_flag():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        engineer = user(role="technician")
        colleague = User(
            mobile="13800000002",
            name="同区域工程师",
            password_hash="disabled",
            role="technician",
            province="江苏省",
            is_active=True,
            require_password_change=False,
        )
        warehouse = Warehouse(
            code="WH-JS-SCOPE",
            name="江苏个人仓兼容位置",
            province="江苏省",
            warehouse_type="personal_backpack",
            position_scope="employee",
        )
        material = Material(
            code="SKU-SCOPE-001",
            name="权限测试物料",
            unit="个",
        )
        db.add_all([engineer, colleague, warehouse, material])
        db.flush()
        db.add_all(
            [
                InventoryBalance(
                    warehouse_id=warehouse.id,
                    holder_user_id=engineer.id,
                    holder_key=engineer.id,
                    material_id=material.id,
                    quantity_on_hand=1,
                ),
                InventoryBalance(
                    warehouse_id=warehouse.id,
                    holder_user_id=colleague.id,
                    holder_key=colleague.id,
                    material_id=material.id,
                    quantity_on_hand=9,
                ),
            ]
        )
        db.commit()

        rows = list_inventory(
            q="",
            province="",
            warehouse_id="",
            condition="",
            user=engineer,
            db=db,
            mine=False,
        )

        assert len(rows) == 1
        assert rows[0]["holder"]["id"] == engineer.id
        assert rows[0]["onHand"] == 1
