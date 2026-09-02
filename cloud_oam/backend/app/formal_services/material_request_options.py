"""Read-only, requester-scoped picker options for formal material requests.

This service never reads OAM directly. It exposes only already-validated local
projections and applies the same requester and work-order guards used by the
draft command. The command service still revalidates a selected work order
when a draft is created, amended, or submitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..demand_models import OamWorkOrder
from ..formal_access import FormalPrincipal
from ..material_request_option_schemas import (
    MaterialRequestWorkOrderOptionDetailOut,
    MaterialRequestWorkOrderOptionOut,
    MaterialRequestWorkOrderOptionPageOut,
)
from . import material_request_draft as draft_service


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestOptionError(RuntimeError):
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


def list_work_order_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
    query: str | None = None,
    now: datetime | None = None,
) -> MaterialRequestWorkOrderOptionPageOut:
    """Return only the current requester's selectable OAM work orders."""

    checked_limit = _limit(limit)
    checked_after = _cursor(after_id)
    checked_query = _query_text(query)
    try:
        with db.no_autoflush:
            effective_now = (
                _aware(now)
                if now is not None
                else draft_service.material_request_database_now(db)
            )
            requester = _requester_context(db, actor, effective_now)
            eligible = _eligible_rows(
                db,
                requester=requester,
                limit=checked_limit + 1,
                after_id=checked_after,
                query=checked_query,
            )
            page = eligible[:checked_limit]
            evidence = tuple(
                draft_service.require_material_request_work_order_evidence(
                    db,
                    row,
                    now=effective_now,
                )
                for row in eligible
            )
            _validate_unique_page(page)
            current = _requester_context(db, requester.principal, effective_now)
            return MaterialRequestWorkOrderOptionPageOut(
                person_id=current.person.id,
                authorization_version=current.principal.authorization_version,
                items=tuple(
                    _option(row, verified)
                    for row, verified in zip(
                        page,
                        evidence[:checked_limit],
                        strict=True,
                    )
                ),
                next_after_id=(
                    eligible[checked_limit].id
                    if len(eligible) > checked_limit
                    else None
                ),
            )
    except MaterialRequestOptionError:
        raise
    except draft_service.MaterialRequestDraftError as exc:
        _fail(exc.code, exc.category, exc.message)
    except DBAPIError:
        _fail(
            "material_request_work_order_options_database_unavailable",
            "service_unavailable",
            "OAM 工单选项暂时不可用",
        )
    raise AssertionError("unreachable material-request work-order option boundary")


def work_order_option_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    work_order_id: uuid.UUID,
    now: datetime | None = None,
) -> MaterialRequestWorkOrderOptionDetailOut:
    """Resolve one current selectable work order for draft edit restoration."""

    checked_id = _required_uuid(work_order_id)
    try:
        with db.no_autoflush:
            effective_now = (
                _aware(now)
                if now is not None
                else draft_service.material_request_database_now(db)
            )
            requester = _requester_context(db, actor, effective_now)
            try:
                row, evidence = draft_service.resolve_material_request_work_order(
                    db,
                    checked_id,
                    requester,
                    now=effective_now,
                )
            except draft_service.MaterialRequestDraftError as exc:
                if exc.code in {
                    "material_request_work_order_not_found",
                    "material_request_work_order_inactive",
                    "material_request_work_order_forbidden",
                    "material_request_work_order_scope_mismatch",
                    "material_request_work_order_projection_invalid",
                    "material_request_work_order_projection_stale",
                }:
                    _fail(
                        "material_request_work_order_option_not_found",
                        "not_found",
                        "当前可选 OAM 工单不存在",
                    )
                raise
            current = _requester_context(db, requester.principal, effective_now)
            return MaterialRequestWorkOrderOptionDetailOut(
                person_id=current.person.id,
                authorization_version=current.principal.authorization_version,
                item=_option(row, evidence),
            )
    except MaterialRequestOptionError:
        raise
    except draft_service.MaterialRequestDraftError as exc:
        _fail(exc.code, exc.category, exc.message)
    except DBAPIError:
        _fail(
            "material_request_work_order_options_database_unavailable",
            "service_unavailable",
            "OAM 工单选项暂时不可用",
        )
    raise AssertionError("unreachable material-request work-order detail boundary")


def _requester_context(
    db: Session,
    actor: FormalPrincipal,
    now: datetime,
) -> draft_service.MaterialRequestRequesterContext:
    return draft_service.require_material_request_requester_context(
        db,
        actor,
        ("create", "update_draft"),
        now,
    )


def _option(
    row: OamWorkOrder,
    evidence: draft_service.MaterialRequestWorkOrderEvidence,
) -> MaterialRequestWorkOrderOptionOut:
    try:
        return MaterialRequestWorkOrderOptionOut(
            work_order_id=row.id,
            work_order_no=row.work_order_no,
            status=row.status,
            source_system_code=evidence.source_system_code,
            source_external_id=evidence.source_external_id,
            source_version=evidence.source_version,
            source_updated_at=evidence.source_updated_at,
            synced_at=evidence.synced_at,
            freshness_status=evidence.freshness_status,
        )
    except MaterialRequestOptionError:
        raise
    except Exception:
        _fail(
            "material_request_work_order_projection_invalid",
            "service_unavailable",
            "OAM 工单选项投影无效",
        )
    raise AssertionError("unreachable work-order option projection")


def _validate_unique_page(rows: tuple[OamWorkOrder, ...]) -> None:
    if (
        len({row.id for row in rows}) != len(rows)
        or len({row.work_order_no for row in rows}) != len(rows)
    ):
        _fail(
            "material_request_work_order_projection_ambiguous",
            "service_unavailable",
            "OAM 工单选项不唯一",
        )


def _eligible_rows(
    db: Session,
    *,
    requester: draft_service.MaterialRequestRequesterContext,
    limit: int,
    after_id: uuid.UUID | None,
    query: str | None,
) -> tuple[OamWorkOrder, ...]:
    """Scan bounded keyset chunks until one response page is complete.

    Region ancestry is intentionally rechecked in application policy instead
    of trusting an OAM projection row. Chunking prevents a corrupted or stale
    cross-region tail from turning one picker request into an unbounded read.
    """

    rows: list[OamWorkOrder] = []
    scan_after = after_id
    include_scan_after = scan_after is not None
    while len(rows) < limit:
        batch_size = min(200, max(50, (limit - len(rows)) * 2))
        statement = (
            select(OamWorkOrder)
            .where(
                OamWorkOrder.engineer_person_id == requester.person.id,
                OamWorkOrder.status.in_(("pending", "active")),
            )
            .order_by(OamWorkOrder.id)
            .execution_options(populate_existing=True)
        )
        if scan_after is not None:
            statement = statement.where(
                OamWorkOrder.id >= scan_after
                if include_scan_after
                else OamWorkOrder.id > scan_after
            )
        if query is not None:
            statement = statement.where(
                func.lower(OamWorkOrder.work_order_no).contains(
                    query.casefold(),
                    autoescape=True,
                )
            )
        candidates = tuple(db.scalars(statement.limit(batch_size)).all())
        for row in candidates:
            if draft_service.material_request_organization_descends_from(
                db,
                row.organization_id,
                requester.region.id,
            ):
                rows.append(row)
                if len(rows) == limit:
                    break
        if len(rows) == limit or len(candidates) < batch_size:
            break
        scan_after = candidates[-1].id
        include_scan_after = False
    return tuple(rows)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        _fail(
            "material_request_work_order_limit_invalid",
            "invalid_request",
            "工单分页大小无效",
        )
    return value


def _cursor(value: uuid.UUID | None) -> uuid.UUID | None:
    if value is not None and (
        not isinstance(value, uuid.UUID) or value.int == 0
    ):
        _fail(
            "material_request_work_order_cursor_invalid",
            "invalid_request",
            "工单分页游标无效",
        )
    return value


def _required_uuid(value: uuid.UUID) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(
            "material_request_work_order_id_invalid",
            "invalid_request",
            "工单标识无效",
        )
    return value


def _query_text(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 100
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        _fail(
            "material_request_work_order_query_invalid",
            "invalid_request",
            "工单检索词无效",
        )
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _fail(
            "material_request_work_order_projection_invalid",
            "service_unavailable",
            "OAM 工单选项投影无效",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(code: str, category: str, message: str):
    raise MaterialRequestOptionError(code, category, message)


__all__ = [
    "MaterialRequestOptionError",
    "list_work_order_options",
    "work_order_option_detail",
]
