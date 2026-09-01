"""Scope-safe read models over the V1.0 inventory ledger projection.

This module never reads ``inventory_balances`` or any other v0.9 table.  It
also never derives personal stock from an OAM mirror.  A caller receives one
ledger cursor and only projection rows that are covered by its formal RBAC
scope; ambiguous hierarchy or projection state fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..formal_access import Entitlement, FormalAccessError, FormalPrincipal
from ..foundation_models import Organization, Person
from ..inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryLot,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
    CustodyAssignment,
)
from ..inventory_schemas import (
    InventoryAccountOut,
    InventoryAccountPageOut,
    InventoryMovementOut,
    InventoryProjectionOut,
    InventoryScopeOut,
    InventorySummaryOut,
    InventoryTransactionOut,
    PersonalWarehouseOut,
)
from ..models import User
from ..stocktake_models import InventoryOpeningEstablishment
from .inventory_posting import InventoryPostingError
from . import opening_stocktake_finalize as finalize_service
from . import opening_control_reconciliation as reconciliation_service
from .audit_chain import AuditChainError, _lock_audit_chain_head_with_proof
from .opening_stocktake_finalize import OpeningStocktakeFinalizeError


_ZERO = Decimal("0.000")
_PROJECTION_BATCH_SIZE = 400


class InventoryReadError(RuntimeError):
    """A stable, non-sensitive formal inventory read failure."""

    def __init__(self, *, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.public_message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.public_message}


@dataclass(frozen=True, slots=True)
class _ProjectionSnapshot:
    ledger_cursor: int
    projected_at: datetime | None


@dataclass(frozen=True, slots=True)
class _AccountRow:
    account: StockAccount
    location: StockLocation
    material: FormalMaterial
    owner_org: Organization
    location_owner_org: Organization
    custodian: Person | None
    lot: InventoryLot | None
    balance: StockBalance | None


@dataclass(frozen=True, slots=True)
class _OpeningEvidence:
    by_scope: dict[tuple[uuid.UUID, uuid.UUID], InventoryOpeningEstablishment]
    complete: bool


def inventory_summary(
    db: Session,
    *,
    actor: FormalPrincipal,
) -> InventorySummaryOut:
    _require_inventory_read(db, actor)
    snapshot = _projection_snapshot(db)
    evidence = _validated_opening_evidence(
        db,
        actor=actor,
        snapshot=snapshot,
        required_pairs=_authorized_account_scope_pairs(db, actor=actor),
        discover_authorized_zero_scopes=True,
    )
    established = evidence.complete

    return InventorySummaryOut(
        **_projection_fields(snapshot, established=established),
        scopes=_inventory_scopes(db, actor),
        # Quantities from unrelated materials/base units remain deliberately
        # non-additive even after every visible opening scope is established.
        quantity_status=(
            "material_filter_required"
            if established
            else "opening_not_established"
        ),
        physical_in_stock_qty=None,
        available_qty=None,
        reserved_qty=None,
        committed_qty=None,
        frozen_qty=None,
        physical_in_transit_qty=None,
    )


def list_inventory_accounts(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
) -> InventoryAccountPageOut:
    _require_inventory_read(db, actor)
    if isinstance(limit, bool) or not 1 <= limit <= 200:
        raise InventoryReadError(
            code="inventory_page_limit_invalid",
            status_code=422,
            message="库存分页大小无效",
        )
    snapshot = _projection_snapshot(db)
    rows = _authorized_account_page(
        db,
        actor=actor,
        limit=limit + 1,
        after_id=after_id,
    )
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    authorized_rows = _authorized_account_rows(db, actor=actor)
    _validate_current_projection_integrity(
        db,
        snapshot=snapshot,
        account_ids={row.account.id for row in authorized_rows},
    )
    # Pagination must never decide whether the caller's complete visible scope
    # is established.  Re-prove every relevant opening task once outside the
    # page window.  The response contract has no partial-opening state, so one
    # unopened pair keeps every row in the page quantity-blind.
    evidence = _validated_opening_evidence(
        db,
        actor=actor,
        snapshot=snapshot,
        required_pairs={
            (row.account.owner_org_id, row.account.location_id)
            for row in authorized_rows
        },
        discover_authorized_zero_scopes=True,
    )
    items = [
        _account_output(
            db,
            row,
            snapshot=snapshot,
            opening_established=evidence.complete,
        )
        for row in visible_rows
    ]
    return InventoryAccountPageOut(
        **_projection_fields(snapshot, established=evidence.complete),
        items=items,
        next_after_id=(visible_rows[-1].account.id if has_more else None),
    )


def personal_warehouse(
    db: Session,
    *,
    actor: FormalPrincipal,
) -> PersonalWarehouseOut:
    _require_inventory_read(db, actor)
    effective_at = datetime.now(timezone.utc)
    locations = list(
        db.scalars(
            select(StockLocation)
            .where(
                StockLocation.location_type == "personal",
                StockLocation.custodian_person_id == actor.person_id,
                StockLocation.status == "active",
            )
            .order_by(StockLocation.id)
        )
    )
    if len(locations) > 1:
        raise InventoryReadError(
            code="personal_location_not_unique",
            status_code=409,
            message="个人仓位置存在冲突，已停止读取",
        )

    snapshot = _projection_snapshot(db)
    if not locations:
        return PersonalWarehouseOut(
            **_projection_fields(snapshot, established=False),
            person_id=actor.person_id,
            location_id=None,
            location_code=None,
            location_name=None,
            location_status=None,
            custody_effective_from=None,
            items=[],
        )

    location = locations[0]
    _validate_personal_location_topology(db, location)
    assignments = list(
        db.scalars(
            select(CustodyAssignment).where(
                CustodyAssignment.location_id == location.id,
                CustodyAssignment.valid_from <= effective_at,
                or_(
                    CustodyAssignment.valid_to.is_(None),
                    CustodyAssignment.valid_to > effective_at,
                ),
            )
        )
    )
    if len(assignments) != 1 or assignments[0].custodian_person_id != actor.person_id:
        raise InventoryReadError(
            code="personal_custody_not_current",
            status_code=409,
            message="个人仓保管责任未建立或存在冲突，已停止读取",
        )

    # Do not let candidate-scope filtering silently omit a corrupt account in
    # the current person's location.  Every account below a personal leaf must
    # carry that exact custodian, regardless of the caller's broader roles.
    if db.scalar(
        select(StockAccount.id)
        .where(
            StockAccount.location_id == location.id,
            or_(
                StockAccount.custodian_person_id.is_(None),
                StockAccount.custodian_person_id != actor.person_id,
            ),
        )
        .limit(1)
    ) is not None:
        raise InventoryReadError(
            code="personal_account_custodian_mismatch",
            status_code=409,
            message="个人仓账户保管责任存在冲突，已停止读取",
        )

    rows = _authorized_account_rows(db, actor=actor, location_id=location.id)
    _validate_current_projection_integrity(
        db,
        snapshot=snapshot,
        account_ids={row.account.id for row in rows},
    )
    required_pairs = {
        (row.account.owner_org_id, row.account.location_id) for row in rows
    }
    evidence = _validated_opening_evidence(
        db,
        actor=actor,
        snapshot=snapshot,
        required_pairs=required_pairs,
        location_id=location.id,
        discover_authorized_zero_scopes=True,
    )
    # The current personal-warehouse response has no partial-establishment
    # status.  Preserve its fail-closed contract: mixed established/unopened
    # owner scopes return custody metadata only until the whole location is
    # established.
    established = evidence.complete
    return PersonalWarehouseOut(
        **_projection_fields(snapshot, established=established),
        person_id=actor.person_id,
        location_id=location.id,
        location_code=location.code,
        location_name=location.name,
        location_status=location.status,
        custody_effective_from=assignments[0].valid_from,
        items=(
            [
                _account_output(
                    db,
                    row,
                    snapshot=snapshot,
                    opening_established=True,
                )
                for row in rows
            ]
            if established
            else []
        ),
    )


def inventory_transaction_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    transaction_id: uuid.UUID,
) -> InventoryTransactionOut:
    _require_inventory_read(db, actor)
    snapshot = _projection_snapshot(db)
    return _inventory_transaction_detail_after_opening(
        db,
        actor=actor,
        transaction_id=transaction_id,
        snapshot=snapshot,
    )


def _validate_personal_location_topology(
    db: Session,
    location: StockLocation,
) -> None:
    """Fail closed unless the personal warehouse is a valid region leaf."""

    if db.scalar(
        select(StockLocation.id)
        .where(StockLocation.parent_id == location.id)
        .limit(1)
    ) is not None:
        raise InventoryReadError(
            code="personal_location_not_leaf",
            status_code=409,
            message="个人仓不是位置树叶子节点，已停止读取",
        )
    parent = db.get(StockLocation, location.parent_id) if location.parent_id else None
    if (
        parent is None
        or parent.status != "active"
        or parent.location_type != "region"
        or parent.owner_org_id != location.owner_org_id
    ):
        raise InventoryReadError(
            code="personal_location_parent_invalid",
            status_code=409,
            message="个人仓区域父位置无效，已停止读取",
        )
    owner = db.get(Organization, location.owner_org_id)
    if owner is None or owner.status != "active" or owner.org_type != "region_company":
        raise InventoryReadError(
            code="personal_location_owner_invalid",
            status_code=409,
            message="个人仓归属区域无效，已停止读取",
        )

    seen: set[uuid.UUID] = set()
    current: StockLocation | None = location
    while current is not None:
        if current.id in seen:
            raise InventoryReadError(
                code="personal_location_cycle",
                status_code=409,
                message="个人仓位置树存在循环，已停止读取",
            )
        seen.add(current.id)
        current = db.get(StockLocation, current.parent_id) if current.parent_id else None


def _inventory_transaction_detail_after_opening(
    db: Session,
    *,
    actor: FormalPrincipal,
    transaction_id: uuid.UUID,
    snapshot: _ProjectionSnapshot,
) -> InventoryTransactionOut:
    transaction = db.get(InventoryTransaction, transaction_id)
    if transaction is None:
        raise InventoryReadError(
            code="inventory_transaction_not_found",
            status_code=404,
            message="库存交易不存在",
        )
    if (
        transaction.status != "posted"
        or not isinstance(transaction.ledger_cursor, int)
        or transaction.ledger_cursor <= 0
        or transaction.ledger_cursor > snapshot.ledger_cursor
    ):
        raise InventoryReadError(
            code="inventory_transaction_projection_invalid",
            status_code=409,
            message="库存交易与当前账本快照不一致，已停止读取",
        )
    movements = list(
        db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.transaction_id == transaction.id)
            .order_by(InventoryMovement.line_no)
        )
    )
    if not movements:
        raise InventoryReadError(
            code="inventory_transaction_incomplete",
            status_code=409,
            message="库存交易缺少流水明细，已停止读取",
        )

    account_ids = sorted(
        {
            account_id
            for movement in movements
            for account_id in (movement.from_account_id, movement.to_account_id)
            if account_id is not None
        },
        key=str,
    )
    account_rows = {
        row.account.id: row
        for row in _account_rows_by_ids(db, account_ids)
    }
    if set(account_rows) != set(account_ids):
        raise InventoryReadError(
            code="inventory_transaction_account_missing",
            status_code=409,
            message="库存交易引用的账户不完整，已停止读取",
        )
    for row in account_rows.values():
        if not _account_allowed(db, actor, row):
            raise InventoryReadError(
                code="inventory_transaction_scope_denied",
                status_code=403,
                message="没有此库存交易的数据权限",
            )

    _validate_current_projection_integrity(
        db,
        snapshot=snapshot,
        account_ids=set(account_ids),
    )

    required_pairs = {
        (row.account.owner_org_id, row.account.location_id)
        for row in account_rows.values()
    }
    evidence = _validated_opening_evidence(
        db,
        actor=actor,
        snapshot=snapshot,
        required_pairs=required_pairs,
        discover_authorized_zero_scopes=False,
    )
    if not evidence.complete or any(
        row.established_ledger_cursor > transaction.ledger_cursor
        for row in evidence.by_scope.values()
    ):
        raise InventoryReadError(
            code="inventory_opening_not_established",
            status_code=409,
            message="交易涉及的库存范围在该账本游标前尚未完成正式期初建立",
        )

    serial_rows = db.execute(
        select(
            InventoryMovementSerial.movement_id,
            InventorySerial.id,
            InventorySerial.serial_no,
        )
        .join(
            InventorySerial,
            InventorySerial.id == InventoryMovementSerial.serial_id,
        )
        .where(InventoryMovementSerial.transaction_id == transaction.id)
        .order_by(InventoryMovementSerial.movement_id, InventorySerial.serial_no)
    ).all()
    serials_by_movement: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for movement_id, serial_id, serial_no in serial_rows:
        serials_by_movement.setdefault(movement_id, []).append(
            (serial_id, serial_no)
        )

    actor_user = db.get(User, transaction.actor_user_id)
    operator = db.get(Person, actor_user.person_id) if actor_user and actor_user.person_id else None
    if operator is None:
        raise InventoryReadError(
            code="inventory_transaction_actor_missing",
            status_code=409,
            message="库存交易操作者证据不完整，已停止读取",
        )
    reversed_by = db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.reversed_transaction_id == transaction.id
        )
    )
    movement_outputs = []
    for movement in movements:
        linked_serials = serials_by_movement.get(movement.id, [])
        movement_outputs.append(
            InventoryMovementOut(
                movement_id=movement.id,
                line_no=movement.line_no,
                from_account_id=movement.from_account_id,
                to_account_id=movement.to_account_id,
                external_boundary_code=movement.external_boundary_code,
                quantity=_format_quantity(movement.quantity),
                serial_ids=[serial_id for serial_id, _ in linked_serials],
                serial_numbers=[serial_no for _, serial_no in linked_serials],
            )
        )
    return InventoryTransactionOut(
        transaction_id=transaction.id,
        transaction_no=transaction.transaction_no,
        ledger_cursor=transaction.ledger_cursor,
        movement_type=transaction.movement_type,
        status="posted",
        source_document_type=transaction.source_document_type,
        source_document_id=transaction.source_document_id,
        effective_at=transaction.effective_at,
        posted_at=transaction.posted_at,
        operator_person_id=operator.id,
        operator_person_name=operator.name,
        idempotency_fingerprint=transaction.idempotency_key_hash[:12],
        reversed_transaction_id=transaction.reversed_transaction_id,
        reversed_by_transaction_id=reversed_by,
        movements=movement_outputs,
    )


def _validated_opening_evidence(
    db: Session,
    *,
    actor: FormalPrincipal,
    snapshot: _ProjectionSnapshot,
    required_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    location_id: uuid.UUID | None = None,
    discover_authorized_zero_scopes: bool,
) -> _OpeningEvidence:
    """Re-prove each relevant opening task once and return exact valid scopes.

    ``inventory_opening_establishments`` is only a pointer index.  No row is
    accepted until the formal finalizer replay validator has recomputed its
    task, scopes, all count/recount seals, two independent reviews, canonical
    terminal State/Outbox/Audit facts, posting, ledger movement, current
    projections and freeze-release evidence.  Missing pointers mean "not
    established"; a present but contradictory pointer is a hard read failure
    so corrupted evidence can never be presented as inventory truth.
    """

    statement = select(InventoryOpeningEstablishment).order_by(
        InventoryOpeningEstablishment.task_id,
        InventoryOpeningEstablishment.owner_org_id,
        InventoryOpeningEstablishment.location_id,
        InventoryOpeningEstablishment.id,
    )
    if location_id is not None:
        statement = statement.where(
            InventoryOpeningEstablishment.location_id == location_id
        )
    elif required_pairs and not discover_authorized_zero_scopes:
        statement = statement.where(
            or_(
                *(
                    and_(
                        InventoryOpeningEstablishment.owner_org_id == owner_org_id,
                        InventoryOpeningEstablishment.location_id == scope_location_id,
                    )
                    for owner_org_id, scope_location_id in sorted(
                        required_pairs,
                        key=lambda pair: (str(pair[0]), str(pair[1])),
                    )
                )
            )
        )

    relevant: list[InventoryOpeningEstablishment] = []
    for row in db.scalars(statement).all():
        pair = (row.owner_org_id, row.location_id)
        if pair in required_pairs or (
            discover_authorized_zero_scopes
            and _opening_scope_allowed(db, actor=actor, row=row)
        ):
            relevant.append(row)

    by_scope = {
        (row.owner_org_id, row.location_id): row for row in relevant
    }
    if len(by_scope) != len(relevant):
        _invalid_opening_read_evidence()

    task_ids = tuple(sorted({row.task_id for row in relevant}, key=str))
    if task_ids:
        try:
            batch_root = finalize_service._lock_opening_terminal_task_batch_root(
                db,
                task_ids=task_ids,
            )
            # The projection snapshot was captured before any business locks.
            # Once the ledger is held, an exact cursor mismatch means the
            # response was assembled across two ledger states and must be
            # regenerated from the beginning.
            if snapshot.ledger_cursor != batch_root.current_ledger_cursor:
                _invalid_opening_read_evidence()
            closed_task_ids = tuple(
                task.id for task in batch_root.tasks if task.status == "closed"
            )
            reconciliation_user_ids = (
                reconciliation_service._opening_control_reconciliation_historical_user_ids(
                    db,
                    task_ids=closed_task_ids,
                )
                if closed_task_ids
                else ()
            )
            batch_graph = finalize_service._lock_opening_terminal_task_batch_graph(
                db,
                root=batch_root,
                supplied_user_ids=(actor.user_id, *reconciliation_user_ids),
            )
            reconciliation_batch = (
                reconciliation_service._lock_opening_control_reconciliation_batch_graph(
                    db,
                    task_ids=closed_task_ids,
                    principal_graph=batch_graph.principal_graph,
                )
                if closed_task_ids
                else None
            )
            _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
                db,
                stream_key="inventory",
            )
            finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
                db,
                proof=batch_graph,
                audit_proof=audit_proof,
                require_closed_task_ids=closed_task_ids,
                expected_ledger_cursor=snapshot.ledger_cursor,
            )
            if reconciliation_batch is not None:
                statuses = reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
                    db,
                    proof=reconciliation_batch,
                    audit_proof=audit_proof,
                )
                if set(statuses) != set(closed_task_ids) or any(
                    statuses[task_id].status not in {"not_required", "approved"}
                    for task_id in closed_task_ids
                ):
                    _invalid_opening_read_evidence()
        except (
            AuditChainError,
            OpeningStocktakeFinalizeError,
            InventoryPostingError,
            reconciliation_service.OpeningControlReconciliationError,
        ) as exc:
            _invalid_opening_read_evidence(exc)

    if any(
        not isinstance(row.established_ledger_cursor, int)
        or row.established_ledger_cursor < 0
        or row.established_ledger_cursor > snapshot.ledger_cursor
        for row in relevant
    ):
        _invalid_opening_read_evidence()

    return _OpeningEvidence(
        by_scope=by_scope,
        complete=bool(by_scope) and required_pairs <= set(by_scope),
    )


def _opening_scope_allowed(
    db: Session,
    *,
    actor: FormalPrincipal,
    row: InventoryOpeningEstablishment,
) -> bool:
    location = db.get(StockLocation, row.location_id)
    owner = db.get(Organization, row.owner_org_id)
    if location is None or owner is None:
        _invalid_opening_read_evidence()
    assert location is not None
    return _inventory_target_allowed(
        db,
        actor=actor,
        location_owner_org_id=location.owner_org_id,
        custodian_person_id=location.custodian_person_id,
    )


def _authorized_account_rows(
    db: Session,
    *,
    actor: FormalPrincipal,
    location_id: uuid.UUID | None = None,
) -> list[_AccountRow]:
    statement = _account_row_statement(db, actor=actor)
    if location_id is not None:
        statement = statement.where(StockAccount.location_id == location_id)
    values = db.execute(statement.order_by(StockAccount.id)).all()
    return [
        row
        for values_row in values
        if _account_allowed(db, actor, row := _account_row(values_row))
    ]


def _authorized_account_scope_pairs(
    db: Session,
    *,
    actor: FormalPrincipal,
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    return {
        (row.account.owner_org_id, row.account.location_id)
        for row in _authorized_account_rows(db, actor=actor)
    }


def _invalid_opening_read_evidence(cause: Exception | None = None) -> None:
    error = InventoryReadError(
        code="inventory_opening_establishment_invalid",
        status_code=503,
        message="库存期初建立证据不完整或相互矛盾，已停止读取",
    )
    if cause is None:
        raise error
    raise error from cause


def _validate_current_projection_integrity(
    db: Session,
    *,
    snapshot: _ProjectionSnapshot,
    account_ids: set[uuid.UUID],
) -> None:
    """Rebuild visible current projections from the immutable ledger.

    Balances and serial positions are caches, never independent stock facts.
    The account set is supplied by the fully authorized read scope rather than
    a page window.  Queries are bounded in portable batches so SQLite's bind
    limit and PostgreSQL parameter counts cannot turn the check into N+1 work.

    A final ledger-head reread distinguishes a concurrent committed posting
    from stable projection drift.  Only the former is retryable; stable drift
    fails closed with one non-sensitive 503 response.
    """

    checked_ids = set(account_ids)
    if not checked_ids:
        return

    expected_quantities = {
        account_id: _ZERO for account_id in checked_ids
    }
    expected_cursors = {account_id: 0 for account_id in checked_ids}
    touched_transactions: dict[uuid.UUID, set[uuid.UUID]] = {
        account_id: set() for account_id in checked_ids
    }
    projection_invalid = False

    for batch in _projection_batches(checked_ids):
        batch_ids = set(batch)
        movement_rows = db.execute(
            select(
                InventoryMovement.from_account_id,
                InventoryMovement.to_account_id,
                InventoryMovement.quantity,
                InventoryTransaction.id,
                InventoryTransaction.ledger_cursor,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryMovement.transaction_id,
            )
            .where(
                InventoryTransaction.status == "posted",
                InventoryTransaction.ledger_cursor <= snapshot.ledger_cursor,
                or_(
                    InventoryMovement.from_account_id.in_(batch),
                    InventoryMovement.to_account_id.in_(batch),
                ),
            )
        ).all()
        for (
            from_account_id,
            to_account_id,
            raw_quantity,
            transaction_id,
            ledger_cursor,
        ) in movement_rows:
            quantity = _projection_decimal(raw_quantity)
            if (
                quantity is None
                or quantity <= _ZERO
                or not isinstance(ledger_cursor, int)
                or ledger_cursor <= 0
            ):
                projection_invalid = True
                continue
            for account_id, sign in (
                (from_account_id, -1),
                (to_account_id, 1),
            ):
                if account_id not in batch_ids:
                    continue
                expected_quantities[account_id] += sign * quantity
                expected_cursors[account_id] = max(
                    expected_cursors[account_id], ledger_cursor
                )
                touched_transactions[account_id].add(transaction_id)

    current_balances: dict[uuid.UUID, tuple[Decimal | None, int, int]] = {}
    for batch in _projection_batches(checked_ids):
        for account_id, quantity, ledger_cursor, version in db.execute(
            select(
                StockBalance.stock_account_id,
                StockBalance.quantity,
                StockBalance.ledger_cursor,
                StockBalance.version,
            ).where(StockBalance.stock_account_id.in_(batch))
        ):
            current_balances[account_id] = (
                _projection_decimal(quantity),
                ledger_cursor,
                version,
            )

    for account_id in checked_ids:
        expected_quantity = expected_quantities[account_id]
        expected_cursor = expected_cursors[account_id]
        expected_version = len(touched_transactions[account_id])
        current = current_balances.get(account_id)
        if current is None:
            # An untouched zero account may legitimately have no lazily-created
            # projection row.  Once ledger history touches it, cursor/version
            # evidence must exist even when its net quantity returns to zero.
            if (
                expected_quantity != _ZERO
                or expected_cursor != 0
                or expected_version != 0
            ):
                projection_invalid = True
            continue
        current_quantity, current_cursor, current_version = current
        if (
            current_quantity is None
            or current_quantity < _ZERO
            or current_quantity != expected_quantity
            or current_cursor != expected_cursor
            or current_version != expected_version
        ):
            projection_invalid = True

    related_serial_ids: set[uuid.UUID] = set()
    for batch in _projection_batches(checked_ids):
        related_serial_ids.update(
            db.scalars(
                select(InventoryMovementSerial.serial_id)
                .join(
                    InventoryMovement,
                    InventoryMovement.id
                    == InventoryMovementSerial.movement_id,
                )
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id
                    == InventoryMovementSerial.transaction_id,
                )
                .where(
                    InventoryTransaction.status == "posted",
                    InventoryTransaction.ledger_cursor
                    <= snapshot.ledger_cursor,
                    or_(
                        InventoryMovement.from_account_id.in_(batch),
                        InventoryMovement.to_account_id.in_(batch),
                    ),
                )
                .distinct()
            )
        )
        # Include a position that claims to be visible even if its immutable
        # movement history never touched this scope; that claim is itself a
        # projection fact which must be disproved rather than silently omitted.
        related_serial_ids.update(
            db.scalars(
                select(SerialCurrentPosition.serial_id).where(
                    SerialCurrentPosition.stock_account_id.in_(batch)
                )
            )
        )

    expected_positions: dict[
        uuid.UUID, tuple[uuid.UUID | None, uuid.UUID]
    ] = {}
    current_positions: dict[
        uuid.UUID, tuple[uuid.UUID | None, uuid.UUID]
    ] = {}
    for batch in _projection_batches(related_serial_ids):
        ranked_history = (
            select(
                InventoryMovementSerial.serial_id.label("serial_id"),
                InventoryMovement.to_account_id.label("stock_account_id"),
                InventoryMovement.id.label("movement_id"),
                func.row_number()
                .over(
                    partition_by=InventoryMovementSerial.serial_id,
                    order_by=(
                        InventoryTransaction.ledger_cursor.desc(),
                        InventoryMovement.line_no.desc(),
                        InventoryMovement.id.desc(),
                    ),
                )
                .label("position_rank"),
            )
            .join(
                InventoryMovement,
                InventoryMovement.id == InventoryMovementSerial.movement_id,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id
                == InventoryMovementSerial.transaction_id,
            )
            .where(
                InventoryMovementSerial.serial_id.in_(batch),
                InventoryTransaction.status == "posted",
                InventoryTransaction.ledger_cursor <= snapshot.ledger_cursor,
            )
            .subquery()
        )
        for serial_id, stock_account_id, movement_id in db.execute(
            select(
                ranked_history.c.serial_id,
                ranked_history.c.stock_account_id,
                ranked_history.c.movement_id,
            ).where(ranked_history.c.position_rank == 1)
        ):
            expected_positions[serial_id] = (stock_account_id, movement_id)

        for serial_id, stock_account_id, last_movement_id in db.execute(
            select(
                SerialCurrentPosition.serial_id,
                SerialCurrentPosition.stock_account_id,
                SerialCurrentPosition.last_movement_id,
            ).where(SerialCurrentPosition.serial_id.in_(batch))
        ):
            current_positions[serial_id] = (
                stock_account_id,
                last_movement_id,
            )

    for serial_id in related_serial_ids:
        # ``stock_account_id is None`` is an exact, valid external-outbound
        # position.  Tuple equality intentionally distinguishes it from a
        # missing projection row.
        if current_positions.get(serial_id) != expected_positions.get(serial_id):
            projection_invalid = True
        if serial_id not in current_positions or serial_id not in expected_positions:
            projection_invalid = True

    _ensure_projection_snapshot_current(db, snapshot)
    if projection_invalid:
        _invalid_current_projection()


def _projection_batches(
    values: set[uuid.UUID],
) -> list[tuple[uuid.UUID, ...]]:
    ordered = sorted(values, key=str)
    return [
        tuple(ordered[offset : offset + _PROJECTION_BATCH_SIZE])
        for offset in range(0, len(ordered), _PROJECTION_BATCH_SIZE)
    ]


def _projection_decimal(value: object) -> Decimal | None:
    try:
        quantity = Decimal(value)  # type: ignore[arg-type]
        if (
            not quantity.is_finite()
            or quantity.quantize(Decimal("0.001")) != quantity
        ):
            return None
    except Exception:
        return None
    return quantity


def _ensure_projection_snapshot_current(
    db: Session,
    snapshot: _ProjectionSnapshot,
) -> None:
    next_cursor = db.scalar(
        select(InventoryLedgerHead.next_cursor).where(
            InventoryLedgerHead.stream_key == "inventory"
        )
    )
    if next_cursor != snapshot.ledger_cursor + 1:
        raise InventoryReadError(
            code="inventory_projection_changed",
            status_code=409,
            message=(
                "库存投影在读取期间发生变化，请重新读取完整快照"
            ),
        )


def _invalid_current_projection() -> None:
    raise InventoryReadError(
        code="inventory_projection_integrity_invalid",
        status_code=503,
        message=(
            "库存余额或序列号投影无法从不可变流水重建，已停止读取"
        ),
    )


def _require_inventory_read(db: Session, actor: FormalPrincipal) -> None:
    operational_allows = _operational_inventory_read_entitlements(db, actor)
    if (
        actor.access_mode != "active"
        or actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.authorization_version <= 0
        or not operational_allows
        or not actor.allows(db, "inventory", "read")
    ):
        raise InventoryReadError(
            code="inventory_read_denied",
            status_code=403,
            message="没有库存读取权限",
        )


def _projection_snapshot(db: Session) -> _ProjectionSnapshot:
    head = db.scalar(
        select(InventoryLedgerHead).where(
            InventoryLedgerHead.stream_key == "inventory"
        )
    )
    if head is None or head.next_cursor <= 0:
        raise InventoryReadError(
            code="inventory_projection_not_provisioned",
            status_code=503,
            message="正式库存投影尚未安全初始化",
        )
    cursor = head.next_cursor - 1
    transaction_count, first_transaction_cursor, latest_transaction_cursor = db.execute(
        select(
            func.count(InventoryTransaction.id),
            func.min(InventoryTransaction.ledger_cursor),
            func.max(InventoryTransaction.ledger_cursor),
        )
    ).one()
    if cursor == 0:
        ledger_is_contiguous = (
            transaction_count == 0
            and first_transaction_cursor is None
            and latest_transaction_cursor is None
        )
    else:
        ledger_is_contiguous = (
            transaction_count == cursor
            and first_transaction_cursor == 1
            and latest_transaction_cursor == cursor
        )
    if not ledger_is_contiguous:
        raise InventoryReadError(
            code="inventory_ledger_cursor_inconsistent",
            status_code=409,
            message="库存账本游标与连续交易事实不一致，已停止读取",
        )
    projected_at = None
    if cursor > 0:
        projected_at = db.scalar(
            select(InventoryTransaction.posted_at).where(
                InventoryTransaction.ledger_cursor == cursor
            )
        )
        if projected_at is None:
            raise InventoryReadError(
                code="inventory_ledger_cursor_inconsistent",
                status_code=409,
                message="库存账本游标缺少对应交易事实，已停止读取",
            )
    return _ProjectionSnapshot(ledger_cursor=cursor, projected_at=projected_at)


def _projection_fields(
    snapshot: _ProjectionSnapshot,
    *,
    established: bool,
) -> dict[str, object]:
    return {
        # Reaching this helper means the formal ledger head exists and the
        # projection can be read consistently.  Opening completeness is an
        # independent business status and must never be inferred from cursor 0
        # or from the presence of a single transaction.
        "projection_status": "ready",
        "opening_balance_status": (
            "established" if established else "not_established"
        ),
        "projected_at": snapshot.projected_at,
        "ledger_cursor": snapshot.ledger_cursor,
    }


def _inventory_scopes(
    db: Session,
    actor: FormalPrincipal,
) -> list[InventoryScopeOut]:
    values = {
        (row.scope_type, row.scope_id)
        for row in _operational_inventory_read_entitlements(db, actor)
    }
    return [
        InventoryScopeOut(scope_type=scope_type, scope_id=scope_id)
        for scope_type, scope_id in sorted(values)
    ]


def _authorized_account_page(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None,
    location_id: uuid.UUID | None = None,
) -> list[_AccountRow]:
    rows: list[_AccountRow] = []
    cursor = after_id
    batch_size = min(max(limit * 2, 100), 500)
    while len(rows) < limit:
        statement = _account_row_statement(db, actor=actor)
        if cursor is not None:
            statement = statement.where(StockAccount.id > cursor)
        if location_id is not None:
            statement = statement.where(StockAccount.location_id == location_id)
        values_batch = db.execute(
            statement.order_by(StockAccount.id).limit(batch_size)
        ).all()
        if not values_batch:
            break
        for values in values_batch:
            row = _account_row(values)
            cursor = row.account.id
            if _account_allowed(db, actor, row):
                rows.append(row)
                if len(rows) >= limit:
                    break
        if len(values_batch) < batch_size:
            break
    return rows


def _account_row_statement(db: Session, *, actor: FormalPrincipal):
    custodian_alias = aliased(Person)
    owner_org_alias = aliased(Organization)
    location_owner_org_alias = aliased(Organization)
    candidate_predicate = _candidate_scope_predicate(
        db,
        actor=actor,
        custodian_alias=custodian_alias,
    )
    return (
        select(
            StockAccount,
            StockLocation,
            FormalMaterial,
            owner_org_alias,
            location_owner_org_alias,
            custodian_alias,
            InventoryLot,
            StockBalance,
        )
        .join(StockLocation, StockLocation.id == StockAccount.location_id)
        .join(FormalMaterial, FormalMaterial.id == StockAccount.material_id)
        .join(owner_org_alias, owner_org_alias.id == StockAccount.owner_org_id)
        .join(
            location_owner_org_alias,
            location_owner_org_alias.id == StockLocation.owner_org_id,
        )
        .outerjoin(custodian_alias, custodian_alias.id == StockAccount.custodian_person_id)
        .outerjoin(InventoryLot, InventoryLot.id == StockAccount.lot_id)
        .outerjoin(StockBalance, StockBalance.stock_account_id == StockAccount.id)
        .where(candidate_predicate)
    )


def _account_rows_by_ids(
    db: Session,
    account_ids: list[uuid.UUID],
) -> list[_AccountRow]:
    if not account_ids:
        return []
    custodian_alias = aliased(Person)
    owner_org_alias = aliased(Organization)
    location_owner_org_alias = aliased(Organization)
    values = db.execute(
        select(
            StockAccount,
            StockLocation,
            FormalMaterial,
            owner_org_alias,
            location_owner_org_alias,
            custodian_alias,
            InventoryLot,
            StockBalance,
        )
        .join(StockLocation, StockLocation.id == StockAccount.location_id)
        .join(FormalMaterial, FormalMaterial.id == StockAccount.material_id)
        .join(owner_org_alias, owner_org_alias.id == StockAccount.owner_org_id)
        .join(
            location_owner_org_alias,
            location_owner_org_alias.id == StockLocation.owner_org_id,
        )
        .outerjoin(custodian_alias, custodian_alias.id == StockAccount.custodian_person_id)
        .outerjoin(InventoryLot, InventoryLot.id == StockAccount.lot_id)
        .outerjoin(StockBalance, StockBalance.stock_account_id == StockAccount.id)
        .where(StockAccount.id.in_(account_ids))
    ).all()
    return [_account_row(row) for row in values]


def _account_row(values) -> _AccountRow:
    return _AccountRow(
        account=values[0],
        location=values[1],
        material=values[2],
        owner_org=values[3],
        location_owner_org=values[4],
        custodian=values[5],
        lot=values[6],
        balance=values[7],
    )


def _candidate_scope_predicate(db: Session, *, actor: FormalPrincipal, custodian_alias):
    allows = _operational_inventory_read_entitlements(db, actor)
    if any(row.scope_type == "national" and row.scope_id == "*" for row in allows):
        return StockAccount.id.is_not(None)

    person_ids: set[uuid.UUID] = set()
    organization_roots: set[uuid.UUID] = set()
    try:
        for row in allows:
            if row.scope_type == "person":
                person_ids.add(uuid.UUID(row.scope_id))
            elif row.scope_type == "organization":
                organization_roots.add(uuid.UUID(row.scope_id))
    except (TypeError, ValueError) as exc:
        raise InventoryReadError(
            code="inventory_scope_invalid",
            status_code=403,
            message="库存授权范围无效",
        ) from exc
    organization_ids = _active_organization_descendants(db, organization_roots)
    predicates = []
    if person_ids:
        predicates.append(StockAccount.custodian_person_id.in_(person_ids))
    if organization_ids:
        predicates.extend(
            [
                StockLocation.owner_org_id.in_(organization_ids),
                custodian_alias.organization_id.in_(organization_ids),
            ]
        )
    if not predicates:
        raise InventoryReadError(
            code="inventory_scope_empty",
            status_code=403,
            message="库存授权范围为空",
        )
    return or_(*predicates)


def _operational_inventory_read_entitlements(
    db: Session,
    actor: FormalPrincipal,
    *,
    effects: frozenset[str] = frozenset({"allow"}),
) -> tuple[Entitlement, ...]:
    """Return only V1 operational inventory grants bound to the same assignment.

    ``FormalPrincipal.allows(target=None)`` deliberately answers whether any
    entitlement exists and therefore cannot by itself distinguish operational
    inventory scopes from an accidentally misconfigured external document
    role.  Inventory reads add this domain boundary so a Star approver can
    never become an inventory reader merely because a permission row was
    attached to that role.
    """

    assignments = {assignment.assignment_id: assignment for assignment in actor.assignments}
    allowed: list[Entitlement] = []
    for entitlement in actor.entitlements:
        if (
            entitlement.resource != "inventory"
            or entitlement.action != "read"
            or entitlement.field_code != ""
            or entitlement.effect not in effects
        ):
            continue
        assignment = assignments.get(entitlement.assignment_id)
        if assignment is None or (
            assignment.role_code != entitlement.role_code
            or assignment.scope_type != entitlement.scope_type
            or assignment.scope_id != entitlement.scope_id
        ):
            continue
        if entitlement.role_code == "admin":
            person = db.get(Person, actor.person_id)
            organization = (
                db.get(Organization, person.organization_id) if person else None
            )
            valid = (
                entitlement.scope_type == "national"
                and entitlement.scope_id == "*"
                and organization is not None
                and organization.status == "active"
                and organization.org_type == "headquarters"
            )
        elif entitlement.role_code == "provincial_manager":
            person = db.get(Person, actor.person_id)
            person_organization = (
                db.get(Organization, person.organization_id) if person else None
            )
            target_organization = (
                db.get(Organization, uuid.UUID(entitlement.scope_id))
                if _is_uuid(entitlement.scope_id)
                else None
            )
            valid = (
                entitlement.scope_type == "organization"
                and person_organization is not None
                and person_organization.status == "active"
                and person_organization.org_type
                in {"headquarters", "region_company", "department"}
                and target_organization is not None
                and target_organization.status == "active"
                and target_organization.org_type == "region_company"
            )
        elif entitlement.role_code == "technician":
            person = db.get(Person, actor.person_id)
            organization = (
                db.get(Organization, person.organization_id) if person else None
            )
            valid = (
                entitlement.scope_type == "person"
                and entitlement.scope_id == str(actor.person_id)
                and person is not None
                and person.employment_status == "active"
                and organization is not None
                and organization.status == "active"
                and organization.org_type
                in {"headquarters", "region_company", "department"}
            )
        else:
            valid = False
        if valid:
            allowed.append(entitlement)
    return tuple(allowed)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _active_organization_descendants(
    db: Session,
    roots: set[uuid.UUID],
) -> set[uuid.UUID]:
    if not roots:
        return set()
    organizations = list(
        db.execute(
            select(Organization.id, Organization.parent_id, Organization.status)
        )
    )
    active_ids = {organization_id for organization_id, _, status in organizations if status == "active"}
    if not roots <= active_ids:
        raise InventoryReadError(
            code="inventory_scope_organization_inactive",
            status_code=403,
            message="库存组织授权范围无效",
        )
    children: dict[uuid.UUID, set[uuid.UUID]] = {}
    for organization_id, parent_id, status in organizations:
        if status == "active" and parent_id is not None:
            children.setdefault(parent_id, set()).add(organization_id)

    result: set[uuid.UUID] = set()
    completed: set[uuid.UUID] = set()
    visiting: set[uuid.UUID] = set()
    for root in sorted(roots, key=str):
        stack: list[tuple[uuid.UUID, bool]] = [(root, False)]
        while stack:
            current, exiting = stack.pop()
            if exiting:
                visiting.remove(current)
                completed.add(current)
                continue
            if current in completed:
                continue
            if current in visiting:
                raise InventoryReadError(
                    code="inventory_scope_organization_cycle",
                    status_code=409,
                    message="组织树存在循环，已停止库存读取",
                )
            visiting.add(current)
            result.add(current)
            stack.append((current, True))
            for child in sorted(children.get(current, ()), key=str, reverse=True):
                if child in visiting:
                    raise InventoryReadError(
                        code="inventory_scope_organization_cycle",
                        status_code=409,
                        message="组织树存在循环，已停止库存读取",
                    )
                if child not in completed:
                    stack.append((child, False))
    return result


def _account_allowed(
    db: Session,
    actor: FormalPrincipal,
    row: _AccountRow,
) -> bool:
    return _inventory_target_allowed(
        db,
        actor=actor,
        location_owner_org_id=row.location.owner_org_id,
        custodian_person_id=row.account.custodian_person_id,
    )


def _inventory_target_allowed(
    db: Session,
    *,
    actor: FormalPrincipal,
    location_owner_org_id: uuid.UUID,
    custodian_person_id: uuid.UUID | None,
) -> bool:
    operational_actor = replace(
        actor,
        entitlements=_operational_inventory_read_entitlements(
            db,
            actor,
            effects=frozenset({"allow", "deny"}),
        ),
    )
    try:
        organization_allowed = operational_actor.allows(
            db,
            "inventory",
            "read",
            target_scope_type="organization",
            target_scope_id=str(location_owner_org_id),
        )
        if custodian_person_id is None:
            return organization_allowed
        person_allowed = operational_actor.allows(
            db,
            "inventory",
            "read",
            target_scope_type="person",
            target_scope_id=str(custodian_person_id),
        )
    except FormalAccessError as exc:
        raise InventoryReadError(
            code="inventory_scope_graph_invalid",
            status_code=409,
            message="库存授权组织树无效，已停止读取",
        ) from exc
    is_self_person_scope = (
        custodian_person_id == actor.person_id
        and any(
            entitlement.resource == "inventory"
            and entitlement.action == "read"
            and entitlement.effect == "allow"
            and entitlement.scope_type == "person"
            and entitlement.scope_id == str(actor.person_id)
            for entitlement in operational_actor.entitlements
        )
    )
    return person_allowed and (organization_allowed or is_self_person_scope)


def _account_output(
    db: Session,
    row: _AccountRow,
    *,
    snapshot: _ProjectionSnapshot,
    opening_established: bool,
) -> InventoryAccountOut:
    if row.balance is not None:
        _ensure_balance_at_snapshot(row.balance, snapshot)
        quantity = row.balance.quantity if opening_established else None
        version = row.balance.version
        cursor = row.balance.ledger_cursor
    else:
        quantity = _ZERO if opening_established else None
        version = 0
        cursor = 0
    policy = _effective_policy(db, row.account.material_id)
    return InventoryAccountOut(
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
        tracking_mode=policy.tracking_mode,
        condition_code=row.account.condition_code,
        availability_bucket=row.account.availability_bucket,
        lot_id=(row.lot.id if row.lot else None),
        lot_no=(row.lot.lot_no if row.lot else None),
        quantity_status=(
            "available" if opening_established else "opening_not_established"
        ),
        quantity=(_format_quantity(quantity) if quantity is not None else None),
        balance_version=version,
        ledger_cursor=cursor,
    )


def _effective_policy(
    db: Session,
    material_id: uuid.UUID,
) -> MaterialInventoryPolicy:
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(MaterialInventoryPolicy).where(
                MaterialInventoryPolicy.material_id == material_id,
                MaterialInventoryPolicy.effective_from <= now,
                or_(
                    MaterialInventoryPolicy.effective_to.is_(None),
                    MaterialInventoryPolicy.effective_to > now,
                ),
            )
        )
    )
    if len(rows) != 1:
        raise InventoryReadError(
            code="inventory_policy_not_unique",
            status_code=409,
            message="物料库存策略缺失或重叠，已停止读取",
        )
    return rows[0]


def _ensure_balance_at_snapshot(
    balance: StockBalance,
    snapshot: _ProjectionSnapshot,
) -> None:
    if balance.ledger_cursor > snapshot.ledger_cursor:
        raise InventoryReadError(
            code="inventory_projection_changed",
            status_code=409,
            message="库存投影在读取期间发生变化，请重新读取完整快照",
        )
    if _as_decimal(balance.quantity) < _ZERO:
        raise InventoryReadError(
            code="inventory_projection_negative",
            status_code=409,
            message="库存投影存在负数异常，已停止读取",
        )


def _as_decimal(value: Decimal | int | str) -> Decimal:
    try:
        return Decimal(value)
    except Exception as exc:
        raise InventoryReadError(
            code="inventory_quantity_invalid",
            status_code=409,
            message="库存数量格式无效，已停止读取",
        ) from exc


def _format_quantity(value: Decimal | int | str) -> str:
    quantity = _as_decimal(value)
    if not quantity.is_finite():
        raise InventoryReadError(
            code="inventory_quantity_non_finite",
            status_code=409,
            message="库存数量不是有限十进制数，已停止读取",
        )
    quantized = quantity.quantize(Decimal("0.001"))
    if quantized != quantity:
        raise InventoryReadError(
            code="inventory_quantity_scale_invalid",
            status_code=409,
            message="库存数量精度超过三位小数，已停止读取",
        )
    return format(quantized, ".3f")
