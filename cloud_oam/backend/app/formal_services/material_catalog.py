"""Read-only active material catalog for formal request and stocktake clients."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    load_formal_principal,
)
from ..inventory_models import FormalMaterial, MaterialInventoryPolicy
from ..material_catalog_schemas import MaterialCatalogItemOut, MaterialCatalogPageOut


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "service_unavailable": 503,
}


class MaterialCatalogError(RuntimeError):
    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


def list_active_materials(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
    query: str | None = None,
    now: datetime | None = None,
) -> MaterialCatalogPageOut:
    """Return one stable UUID page without mutating or refreshing master data."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        _fail("material_catalog_limit_invalid", "invalid_request", "物料分页大小无效")
    if after_id is not None and not isinstance(after_id, uuid.UUID):
        _fail("material_catalog_cursor_invalid", "invalid_request", "物料分页游标无效")
    checked_query = _query_text(query)
    effective_now = _aware(now or datetime.now(timezone.utc))
    try:
        with db.no_autoflush:
            principal = _require_current_actor(db, actor, now=effective_now)
            if not principal.allows(db, "inventory", "read"):
                _fail("material_catalog_forbidden", "forbidden", "无权读取正式物料目录")

            statement = (
                select(FormalMaterial, MaterialInventoryPolicy)
                .join(
                    MaterialInventoryPolicy,
                    and_(
                        MaterialInventoryPolicy.material_id == FormalMaterial.id,
                        MaterialInventoryPolicy.effective_from <= effective_now,
                        or_(
                            MaterialInventoryPolicy.effective_to.is_(None),
                            MaterialInventoryPolicy.effective_to > effective_now,
                        ),
                    ),
                    isouter=True,
                )
                .where(FormalMaterial.status == "active")
                .order_by(FormalMaterial.id)
                .limit(limit + 1)
                .execution_options(populate_existing=True)
            )
            if after_id is not None:
                statement = statement.where(FormalMaterial.id >= after_id)
            if checked_query is not None:
                normalized = checked_query.casefold()
                statement = statement.where(
                    or_(
                        func.lower(FormalMaterial.sku_code).contains(
                            normalized, autoescape=True
                        ),
                        func.lower(FormalMaterial.name).contains(
                            normalized, autoescape=True
                        ),
                        func.lower(FormalMaterial.specification).contains(
                            normalized, autoescape=True
                        ),
                    )
                )
            rows = tuple(db.execute(statement).all())
            page_rows = rows[:limit]
            _validate_rows(page_rows)
            _require_current_actor(db, principal, now=effective_now)
            return MaterialCatalogPageOut(
                items=tuple(_item(material, policy) for material, policy in page_rows),
                next_after_id=(rows[limit][0].id if len(rows) > limit else None),
            )
    except MaterialCatalogError:
        raise
    except DBAPIError:
        _fail(
            "material_catalog_database_unavailable",
            "service_unavailable",
            "正式物料目录暂时不可用",
        )
    raise AssertionError("unreachable material catalog boundary")


def _validate_rows(
    rows: tuple[tuple[FormalMaterial, MaterialInventoryPolicy | None], ...],
) -> None:
    material_ids = [material.id for material, _policy in rows]
    if len(material_ids) != len(set(material_ids)):
        _fail(
            "material_catalog_policy_ambiguous",
            "service_unavailable",
            "正式物料策略不唯一，禁止选择物料",
        )
    if any(policy is None for _material, policy in rows):
        _fail(
            "material_catalog_policy_missing",
            "service_unavailable",
            "正式物料策略不完整，禁止选择物料",
        )


def _item(
    material: FormalMaterial,
    policy: MaterialInventoryPolicy | None,
) -> MaterialCatalogItemOut:
    if policy is None:  # kept explicit for static type checking and fail closed
        _fail(
            "material_catalog_policy_missing",
            "service_unavailable",
            "正式物料策略不完整，禁止选择物料",
        )
    try:
        return MaterialCatalogItemOut(
            material_id=material.id,
            sku_code=material.sku_code,
            name=material.name,
            specification=material.specification,
            base_unit=material.base_unit,
            tracking_mode=policy.tracking_mode,
            quantity_scale=policy.quantity_scale,
            allow_fraction=policy.allow_fraction,
            source_updated_at=_aware(material.source_updated_at),
        )
    except Exception:
        _fail(
            "material_catalog_projection_invalid",
            "service_unavailable",
            "正式物料目录投影无效",
        )
    raise AssertionError("unreachable material catalog projection")


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    *,
    now: datetime,
) -> FormalPrincipal:
    if not isinstance(supplied, FormalPrincipal) or not supplied.user_id:
        _fail("material_catalog_actor_invalid", "forbidden", "正式操作人上下文无效")
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("material_catalog_actor_not_current", "forbidden", "正式操作人授权已失效")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.access_mode != "active"
    ):
        _fail("material_catalog_actor_stale", "forbidden", "正式操作人权限版本已变化")
    return current


def _query_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 100:
        _fail("material_catalog_query_invalid", "invalid_request", "物料检索词无效")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail("material_catalog_query_invalid", "invalid_request", "物料检索词无效")
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _fail(
            "material_catalog_projection_invalid",
            "service_unavailable",
            "正式物料目录投影无效",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(code: str, category: str, message: str):
    raise MaterialCatalogError(code, category, message)


__all__ = ["MaterialCatalogError", "list_active_materials"]
