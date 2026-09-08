"""Read-only source inventory candidates for an approved material request.

This module deliberately stops at a candidate directory.  It never creates or
updates allocation, reservation, outbound, shipment, receipt or notification
facts.  Every request and inventory projection is revalidated before the
response is returned.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import uuid

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..demand_models import MaterialRequest, MaterialRequestLine
from ..formal_access import FormalPrincipal
from ..material_request_allocation_option_schemas import (
    MaterialRequestAllocationOptionOut,
    MaterialRequestAllocationOptionPageOut,
)
from . import inventory_query
from . import material_request_query


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestAllocationOptionError(RuntimeError):
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


def list_allocation_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    request_line_id: uuid.UUID,
) -> MaterialRequestAllocationOptionPageOut:
    """Return visible, positive ``available`` stock accounts for one approved line."""

    _required_uuid(material_request_id, "material_request_id_invalid")
    _required_uuid(request_line_id, "material_request_line_id_invalid")
    try:
        with db.no_autoflush:
            request_view = material_request_query.material_request_detail(
                db, actor=actor, request_id=material_request_id
            )
            line = next(
                (row for row in request_view.lines if row.request_line_id == request_line_id),
                None,
            )
            if line is None:
                _fail("material_request_line_not_found", "not_found", "需求单明细不存在")
            if line.revision_id != request_view.current_revision_id or line.revision_no != request_view.current_revision_no:
                _fail("material_request_line_revision_stale", "conflict", "需求单明细版本已变化，请重新读取")
            if request_view.states.request_status not in {"approved", "partially_approved"}:
                _fail("material_request_not_approved", "precondition_failed", "需求单尚未完成最终审批")
            if line.status not in {"approved", "partially_approved"}:
                _fail("material_request_line_not_approved", "precondition_failed", "需求单明细尚未完成最终审批")
            allocatable = line.final_approved_qty - line.cancelled_qty
            if allocatable <= Decimal("0"):
                _fail("material_request_line_fully_cancelled", "precondition_failed", "需求单明细没有可分配数量")

            # Fulfilment source selection is a headquarters/regional operation;
            # a technician's personal inventory-read grant is not allocation
            # authority, even when the technician can read that account.
            if not set(actor.role_codes).intersection({"admin", "provincial_manager"}):
                _fail("material_request_allocation_options_forbidden", "forbidden", "当前账号没有货源分配目录权限")

            # The inventory reader performs the formal RBAC, hierarchy, ledger
            # continuity, balance rebuild and opening-establishment proof.
            inventory_query._require_inventory_read(db, actor)
            snapshot = inventory_query._projection_snapshot(db)
            rows = [
                row for row in inventory_query._authorized_account_rows(db, actor=actor)
                if row.account.material_id == line.material_id
                and row.account.availability_bucket == "available"
                and row.location.status == "active" and row.material.status == "active"
            ]
            inventory_query._validate_current_projection_integrity(
                db, snapshot=snapshot, account_ids={row.account.id for row in rows}
            )
            evidence = inventory_query._validated_opening_evidence(
                db,
                actor=actor,
                snapshot=snapshot,
                required_pairs={(row.account.owner_org_id, row.account.location_id) for row in rows},
                discover_authorized_zero_scopes=not rows,
            )
            if not evidence.complete:
                # A partially established directory can still expose the
                # exact proven sources. Missing opening pointers never supply
                # quantities and do not invalidate another warehouse's proof.
                rows = [row for row in rows if (
                    row.account.owner_org_id, row.account.location_id
                ) in evidence.by_scope]
                if not rows:
                    _fail("inventory_opening_not_established", "precondition_failed", "库存期初建账尚未完成")

            candidates: list[MaterialRequestAllocationOptionOut] = []
            for row in rows:
                if row.location.status != "active" or row.material.status != "active":
                    continue
                if row.account.material_id != line.material_id:
                    continue
                # Only the free bucket is a source candidate.  Frozen, held,
                # reserved, picking and transit buckets never appear here.
                if row.account.availability_bucket != "available":
                    continue
                if row.balance is None:
                    continue
                inventory_query._ensure_balance_at_snapshot(row.balance, snapshot)
                quantity = _decimal(row.balance.quantity)
                if quantity <= Decimal("0"):
                    continue
                policy = inventory_query._effective_policy(db, row.material.id)
                fixed = _fixed_quantity(quantity, policy.quantity_scale)
                candidates.append(
                    MaterialRequestAllocationOptionOut(
                        stock_account_id=row.account.id,
                        owner_org_id=row.owner_org.id,
                        owner_org_code=row.owner_org.code,
                        owner_org_name=row.owner_org.name,
                        location_owner_org_id=row.location_owner_org.id,
                        location_owner_org_code=row.location_owner_org.code,
                        location_owner_org_name=row.location_owner_org.name,
                        location_id=row.location.id,
                        location_code=row.location.code,
                        location_name=row.location.name,
                        location_type=row.location.location_type,
                        location_parent_id=row.location.parent_id,
                        custodian_person_id=(row.custodian.id if row.custodian else None),
                        custodian_person_name=(row.custodian.name if row.custodian else None),
                        material_id=row.material.id,
                        sku_code=row.material.sku_code,
                        material_name=row.material.name,
                        base_unit=row.material.base_unit,
                        condition_code=row.account.condition_code,
                        availability_bucket="available",
                        lot_id=(row.lot.id if row.lot else None),
                        lot_no=(row.lot.lot_no if row.lot else None),
                        quantity=fixed,
                        quantity_scale=policy.quantity_scale,
                        balance_version=row.balance.version,
                        ledger_cursor=row.balance.ledger_cursor,
                    )
                )

            candidates.sort(key=lambda item: item.stock_account_id)
            # A second request read closes the race between final approval and
            # inventory projection reads.  No stale revision may be presented.
            reread = material_request_query.material_request_detail(
                db, actor=actor, request_id=material_request_id
            )
            reread_line = next(
                (row for row in reread.lines if row.request_line_id == request_line_id),
                None,
            )
            if (
                reread_line is None
                or reread.request_version != request_view.request_version
                or reread.current_revision_id != request_view.current_revision_id
                or reread.current_revision_no != request_view.current_revision_no
                or reread_line.revision_id != line.revision_id
                or reread_line.material_id != line.material_id
                or reread_line.final_approved_qty != line.final_approved_qty
                or reread_line.cancelled_qty != line.cancelled_qty
                or reread_line.status != line.status
            ):
                _fail("material_request_revision_stale", "conflict", "需求单版本已变化，请重新读取")
            return MaterialRequestAllocationOptionPageOut(
                request_id=material_request_id,
                request_line_id=request_line_id,
                request_version=request_view.request_version,
                current_revision_id=request_view.current_revision_id,
                current_revision_no=request_view.current_revision_no,
                material_id=line.material_id,
                final_approved_qty=_fixed_quantity(line.final_approved_qty, 3),
                cancelled_qty=_fixed_quantity(line.cancelled_qty, 3),
                allocatable_qty=_fixed_quantity(allocatable, 3),
                projection_status="ready",
                opening_balance_status="established",
                projected_at=snapshot.projected_at,
                ledger_cursor=snapshot.ledger_cursor,
                items=tuple(candidates),
            )
    except MaterialRequestAllocationOptionError:
        raise
    except material_request_query.MaterialRequestReadError as exc:
        _fail(exc.code, exc.category, exc.message)
    except inventory_query.InventoryReadError as exc:
        # Keep the inventory reader's stable code/status, while exposing no DB detail.
        category = "forbidden" if exc.status_code == 403 else (
            "precondition_failed" if exc.status_code == 412 else
            "conflict" if exc.status_code == 409 else "service_unavailable"
        )
        _fail(exc.code, category, exc.public_message)
    except DBAPIError:
        _fail("material_request_allocation_options_database_unavailable", "service_unavailable", "库存候选暂时不可用")
    except (TypeError, ValueError, InvalidOperation) as exc:
        _fail("material_request_allocation_projection_invalid", "service_unavailable", "库存候选投影无效")
    raise AssertionError("unreachable allocation option boundary")


def _required_uuid(value: uuid.UUID, code: str) -> None:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(code, "invalid_request", "标识无效")


def _decimal(value: object) -> Decimal:
    result = Decimal(value)  # type: ignore[arg-type]
    if not result.is_finite():
        raise InvalidOperation("non-finite quantity")
    return result


def _fixed_quantity(value: object, scale: int) -> str:
    quantity = _decimal(value)
    if not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= 3:
        raise InvalidOperation("invalid quantity scale")
    quantum = Decimal(1).scaleb(-scale)
    quantized = quantity.quantize(quantum)
    if quantized != quantity:
        raise InvalidOperation("quantity exceeds policy scale")
    return format(quantized, f".{scale}f")


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestAllocationOptionError(code, category, message)


__all__ = ["MaterialRequestAllocationOptionError", "list_allocation_options"]
