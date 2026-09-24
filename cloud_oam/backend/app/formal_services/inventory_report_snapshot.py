"""One complete, scope-safe formal inventory snapshot for an Excel job.

OAM control rows never enter this read path. The caller must separately prove
the report.export permission and freeze the resulting account IDs on the job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import Entitlement, FormalAccessError, FormalPrincipal, _scope_covers
from ..inventory_models import InventorySerial, SerialCurrentPosition
from . import inventory_query
from .inventory_report_rows import inventory_export_lines
from .inventory_report_workbook import InventoryExportLine, InventoryWorkbookError, MAX_ROWS


@dataclass(frozen=True, slots=True)
class InventoryReportSnapshot:
    ledger_cursor: int
    account_ids: tuple[UUID, ...]
    assignment_ids: tuple[UUID, ...]
    lines: tuple[InventoryExportLine, ...]


def capture_inventory_report_snapshot(
    db: Session,
    *,
    actor: FormalPrincipal,
    expected_cursor: int | None = None,
    expected_account_ids: Collection[UUID] | None = None,
) -> InventoryReportSnapshot:
    """Prove opening, ledger and serial position before exposing any quantity."""

    inventory_query._require_inventory_read(db, actor)
    report_grants = qualified_report_grants(db, actor)
    try:
        report_allowed = bool(report_grants) and actor.allows(db, "report", "export")
    except FormalAccessError:
        report_allowed = False
    if not report_allowed:
        _fail("inventory_report_export_denied", 403, "没有库存报表导出权限")
    snapshot = inventory_query._projection_snapshot(db)
    if expected_cursor is not None and snapshot.ledger_cursor != expected_cursor:
        _fail("inventory_report_cursor_changed", 409, "库存账本已变化，请重新申请报表")
    rows = inventory_query._authorized_account_rows(db, actor=actor)
    for row in rows:
        try:
            target_id = str(row.location_owner_org.id)
            allowed = any(
                _scope_covers(db, grant.scope_type, grant.scope_id, "organization", target_id)
                for grant in report_grants
            ) and actor.allows(
                db, "report", "export",
                target_scope_type="organization",
                target_scope_id=target_id,
            )
        except FormalAccessError:
            allowed = False
        if not allowed:
            _fail("inventory_report_scope_denied", 403, "库存报表范围超出导出权限")
    if len(rows) > MAX_ROWS:
        _fail("inventory_report_scope_too_large", 409, "库存报表超过明细行数上限")
    account_ids = tuple(sorted((row.account.id for row in rows), key=str))
    if expected_account_ids is not None and account_ids != tuple(sorted(expected_account_ids, key=str)):
        _fail("inventory_report_scope_changed", 409, "库存可见范围已变化，请重新申请报表")
    inventory_query._validate_current_projection_integrity(
        db, snapshot=snapshot, account_ids=set(account_ids),
    )
    evidence = inventory_query._validated_opening_evidence(
        db,
        actor=actor,
        snapshot=snapshot,
        required_pairs={
            (row.account.owner_org_id, row.account.location_id) for row in rows
        },
        discover_authorized_zero_scopes=True,
    )
    if not evidence.complete:
        _fail("inventory_report_opening_not_established", 409, "库存期初尚未完整建立，不能导出")
    accounts = tuple(
        inventory_query._account_output(
            db, row, snapshot=snapshot, opening_established=True,
        )
        for row in rows
    )
    account_by_id = {row.account.id: row.account for row in rows}
    serials_by_account: dict[UUID, list[str]] = {}
    for batch in inventory_query._projection_batches(set(account_ids)):
        for account_id, serial_no, material_id, lot_id, lifecycle in db.execute(
            select(
                SerialCurrentPosition.stock_account_id,
                InventorySerial.serial_no,
                InventorySerial.material_id,
                InventorySerial.lot_id,
                InventorySerial.lifecycle_status,
            )
            .join(InventorySerial, InventorySerial.id == SerialCurrentPosition.serial_id)
            .where(SerialCurrentPosition.stock_account_id.in_(batch))
        ):
            account = account_by_id.get(account_id)
            if (
                account is None
                or material_id != account.material_id
                or lot_id != account.lot_id
                or lifecycle != "active"
            ):
                _fail("inventory_report_serial_dimension_invalid", 503, "库存 SN 维度与流水投影不一致")
            serials_by_account.setdefault(account_id, []).append(serial_no)
    inventory_query._ensure_projection_snapshot_current(db, snapshot)
    try:
        lines = inventory_export_lines(
            accounts,
            serials_by_account=serials_by_account,
            ledger_cursor=snapshot.ledger_cursor,
        )
    except InventoryWorkbookError as exc:
        _fail("inventory_report_projection_invalid", 503, "库存报表明细与投影不一致", exc)
    return InventoryReportSnapshot(
        ledger_cursor=snapshot.ledger_cursor,
        account_ids=account_ids,
        assignment_ids=tuple(sorted((row.assignment_id for row in actor.assignments), key=str)),
        lines=lines,
    )


def qualified_report_grants(db: Session, actor: FormalPrincipal) -> tuple[Entitlement, ...]:
    """Only V1 report grants paired with inventory.read on one assignment."""

    operational_assignment_ids = {
        grant.assignment_id
        for grant in inventory_query._operational_inventory_read_entitlements(db, actor)
    }
    return tuple(
        entitlement for entitlement in actor.entitlements
        if entitlement.resource == "report"
        and entitlement.action == "export"
        and entitlement.field_code == ""
        and entitlement.effect == "allow"
        and entitlement.role_code in {"admin", "provincial_manager"}
        and entitlement.scope_type in {"national", "organization"}
        and entitlement.assignment_id in operational_assignment_ids
    )


def _fail(code: str, status_code: int, message: str, cause: Exception | None = None) -> None:
    error = inventory_query.InventoryReadError(code=code, status_code=status_code, message=message)
    if cause is None:
        raise error
    raise error from cause
