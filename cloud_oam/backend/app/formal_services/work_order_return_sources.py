"""Verify explicit recovery origins and a whole return-source selection.

No stock-operation head, reservation, shipment, receipt or custody release is
created here. A future return command must bind its own destination and recheck
the selection under posting locks. A source preview is never write authority.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

from sqlalchemy import select

from ..foundation_models import SourceSystem
from ..inventory_models import FormalMaterial, StockAccount
from ..work_order_return_schemas import (
    WorkOrderReturnSelectionIn, WorkOrderReturnSelectionLineOut, WorkOrderReturnSelectionOut,
    WorkOrderReturnSerialOut, WorkOrderReturnSourceOut, WorkOrderReturnSourcesOut,
)
from ..work_order_query_schemas import WorkOrderSerialOptionOut
from . import inventory_posting as posting, inventory_query as inventory, work_order_material as material
from .work_order_completion import completion_check
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_material_options import material_options
from .work_order_preview import _policies as _read_policies
from .work_order_query import _aware

SOURCE_BLOCKERS = frozenset({"opening_not_established", "source_stale", "source_disabled",
    "history_scope_unresolved", "reversal_review_required"})


def _fail(code, message, status_code=409):
    raise inventory.InventoryReadError(code=code, message=message, status_code=status_code)


def _changed():
    _fail("work_order_return_sources_changed", "退回来源、保管关系或库存已变化，请重新核验整批明细")


def _invalid():
    _fail("work_order_return_source_evidence_invalid", "原回收明细与个人保管库存证据不一致，请先核验", 503)


def _source_enabled(db):
    return db.scalar(select(SourceSystem.enabled).where(SourceSystem.code == "starcharge_oam"))


def _source_items(db, *, actor, options, obligations):
    from .stock_return_facts import commitments
    held = commitments(db, actor=actor, recovery_line_ids={row.reference_id for row in obligations.issues if row.kind == "pending_return"})
    available = {row.stock_account_id: row for row in options.items if row.availability_bucket == "available"}
    items = []
    for issue in obligations.issues:
        if issue.kind != "pending_return":
            continue
        account = db.get(StockAccount, issue.stock_account_id, populate_existing=True)
        if (account is None or account.custodian_person_id != actor.person_id
                or account.material_id != issue.material_id or account.condition_code != issue.condition_code
                or account.lot_id != issue.lot_id or account.availability_bucket != "available"
                or issue.condition_code not in {"used", "damaged"}
                or issue.operation_id is None or issue.operation_no is None):
            _invalid()
        candidate = available.get(account.id)
        if candidate is not None and any(getattr(candidate, name) != getattr(account, name) for name in (
                "owner_org_id", "custodian_person_id", "location_id", "material_id", "lot_id", "condition_code")):
            _invalid()
        # If stock has moved, keep the full original obligation visible. Never
        # substitute another account/condition/lot merely because the SKU matches.
        quantity = Decimal(candidate.selectable_quantity) if candidate is not None else Decimal(0)
        current_serials = {row.serial_id for row in candidate.serials} if candidate is not None else set()
        committed, committed_serials = held[issue.reference_id]
        if committed > Decimal(issue.quantity) or not committed_serials <= {row.serial_id for row in issue.serials}:
            _invalid()
        serials = tuple(WorkOrderReturnSerialOut(**row.model_dump(), selectable=row.serial_id in current_serials and row.serial_id not in committed_serials)
            for row in issue.serials)
        selectable = min(Decimal(issue.quantity) - committed, quantity)
        if serials:
            selectable = min(selectable, Decimal(sum(row.selectable for row in serials)))
        items.append(WorkOrderReturnSourceOut(source_recovery_line_id=issue.reference_id,
            recovery_operation_id=issue.operation_id, recovery_operation_no=issue.operation_no,
            stock_account_id=account.id, owner_org_id=account.owner_org_id,
            custodian_person_id=account.custodian_person_id, location_id=account.location_id,
            material_id=issue.material_id, sku_code=issue.sku_code, material_name=issue.material_name,
            base_unit=issue.base_unit, condition_code=issue.condition_code, lot_id=issue.lot_id, lot_no=issue.lot_no,
            owed_quantity=issue.quantity, committed_quantity=format(committed, ".3f"), available_quantity=format(quantity, ".3f"),
            selectable_quantity=format(selectable, ".3f"), serials=serials))
    return tuple(items)


def return_sources(db, *, actor, work_order_id):
    current = posting._require_current_actor(db, actor)
    with db.no_autoflush:
        audit = material_audit_cursor(db)
        enabled = _source_enabled(db)
        obligations = completion_check(db, actor=current, work_order_id=work_order_id)
        options = material_options(db, actor=current, work_order_id=work_order_id)
        if (obligations.ledger_cursor != options.ledger_cursor or obligations.work_order != options.work_order
                or obligations.authorization_version != options.authorization_version):
            _changed()
        snapshot = inventory._ProjectionSnapshot(options.ledger_cursor, options.projected_at)
        try:
            items = _source_items(db, actor=current, options=options, obligations=obligations)
        except inventory.InventoryReadError:
            inventory._ensure_projection_snapshot_current(db, snapshot)
            if material_audit_cursor(db) != audit: _changed()
            raise
        if (material_options(db, actor=current, work_order_id=work_order_id) != options
                or material_audit_cursor(db) != audit or _source_enabled(db) != enabled):
            _changed()
        inventory._ensure_projection_snapshot_current(db, snapshot)
        posting._require_current_actor(db, current)
        return WorkOrderReturnSourcesOut(person_id=current.person_id,
            authorization_version=current.authorization_version, work_order=options.work_order,
            location_id=options.location_id,
            custody_effective_from=_aware(options.custody_effective_from) if options.custody_effective_from else None,
            ledger_cursor=options.ledger_cursor, projected_at=_aware(options.projected_at) if options.projected_at else None,
            queried_at=datetime.now(timezone.utc), blockers=tuple(sorted(set(obligations.blockers) & SOURCE_BLOCKERS)),
            items=items)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _policies(db, material_ids, checked_at):
    policies, _ = _read_policies(db, material_ids, checked_at)
    # Include each policy's material binding and normalize instants so a
    # connection timezone cannot change the same source-basis digest.
    fingerprint = tuple(sorted((str(row.material_id), str(row.id), row.tracking_mode, row.quantity_scale,
        row.allow_fraction, _aware(row.effective_from).isoformat(),
        _aware(row.effective_to).isoformat() if row.effective_to else None) for row in policies.values()))
    return policies, fingerprint


def selection_hash(*, work_order_id, request):
    # This digest covers source selection only, not a future return command.
    return _hash({"kind": "work_order_return_source_selection", "work_order_id": str(work_order_id),
        "operator_person_id": str(request.operator_person_id), "lines": [{
            "source_recovery_line_id": str(row.source_recovery_line_id), "stock_account_id": str(row.stock_account_id),
            "quantity": format(row.quantity, ".3f"),
            "serial_verifications": [proof.model_dump(mode="json") for proof in sorted(
                row.serial_verifications, key=lambda proof: str(proof.serial_id))],
        } for row in sorted(request.lines, key=lambda row: str(row.source_recovery_line_id))]})


def _unfrozen(db, accounts, at):
    posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
    # A future reserve also needs the exact return_pending dimension. It may
    # not exist yet; checking a value object must not create an empty account.
    targets = {key: SimpleNamespace(**{name: getattr(account, name) for name in (
        "owner_org_id", "location_id", "material_id", "condition_code")}, availability_bucket="return_pending")
        for key, account in accounts.items()}
    posting._require_no_active_hard_freezes(db, targets, effective_at=at)


def preview_selection(db, *, actor, work_order_id, request: WorkOrderReturnSelectionIn):
    # Revalidate the public schema for internal callers too; no unchecked batch
    # can bypass size, decimal, UUID or unexpected-field constraints.
    request = WorkOrderReturnSelectionIn.model_validate(request.model_dump())
    current = posting._require_current_actor(db, actor)
    if request.operator_person_id != current.person_id:
        _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    with db.no_autoflush:
        sources = return_sources(db, actor=current, work_order_id=work_order_id)
        if sources.blockers:
            _fail("work_order_return_sources_blocked", "退回来源存在待核验事项，请先刷新工单来源、期初或责任记录")
        candidates = {row.source_recovery_line_id: row for row in sources.items}
        selected_origins = set(); selected_serials = set(); totals = defaultdict(Decimal)
        accounts = {}; lines = []; reviewed = []
        for row in request.lines:
            if row.source_recovery_line_id in selected_origins:
                _fail("work_order_return_source_duplicate", "同一原回收明细只能选择一次")
            selected_origins.add(row.source_recovery_line_id)
            source = candidates.get(row.source_recovery_line_id)
            if source is None or source.stock_account_id != row.stock_account_id or source.location_id != sources.location_id:
                _fail("work_order_return_source_invalid", "退回明细必须准确绑定本人工单的原回收明细与个人仓账户")
            if row.quantity > Decimal(source.selectable_quantity):
                _fail("work_order_return_quantity_insufficient", "退回数量超过原回收待退数量或当前可用库存")
            ids = tuple(proof.serial_id for proof in row.serial_verifications)
            if len(set(ids)) != len(ids) or selected_serials.intersection(ids):
                _fail("work_order_return_serial_duplicate", "整批退回中同一 SN 只能选择一次")
            selected_serials.update(ids)
            if not set(ids) <= {sn.serial_id for sn in source.serials if sn.selectable}:
                _fail("work_order_return_serial_invalid", "SN 不属于该原回收明细当前仍可退回的实物")
            totals[row.stock_account_id] += row.quantity
            if totals[row.stock_account_id] > Decimal(source.available_quantity):
                _fail("work_order_return_batch_stock_insufficient", "多条退回明细合计超过同一账户可用库存")
            account = db.get(StockAccount, row.stock_account_id, populate_existing=True)
            sku = db.get(FormalMaterial, source.material_id, populate_existing=True)
            if sku is None or sku.status != "active":
                _fail("material_invalid", "退回物料不存在或已停用")
            accounts[row.stock_account_id] = account
            line = material.WorkOrderMaterialLineInput(material_id=source.material_id, stock_account_id=row.stock_account_id,
                quantity=row.quantity, serial_ids=ids, condition_before=source.condition_code,
                serial_verifications=tuple(material.SerialVerificationInput(**proof.model_dump()) for proof in row.serial_verifications))
            material.verify_serial_proofs(db, line=line)
            lines.append(line)
            reviewed.append(WorkOrderReturnSelectionLineOut(source=source, selected_quantity=format(row.quantity, ".3f"),
                selected_serials=tuple(WorkOrderSerialOptionOut(serial_id=sn.serial_id, serial_no=sn.serial_no)
                    for sn in source.serials if sn.serial_id in ids)))
        checked_at = datetime.now(timezone.utc)
        policies, fingerprint = _policies(db, {line.material_id for line in lines}, checked_at)
        command = posting.InventoryPostingCommand(transaction_no="preview", movement_type="reserve",
            source_document_type="work_order_return_source_selection", source_document_id=str(work_order_id),
            posting_key="preview", effective_at=checked_at,
            movements=tuple(posting.InventoryMovementCommand(from_account_id=line.stock_account_id, to_account_id=None,
                quantity=line.quantity, serial_ids=line.serial_ids) for line in lines))
        posting._validate_tracking_rules(command, accounts, policies)
        _unfrozen(db, accounts, checked_at)
        # No permission to start new work-order material operations is required:
        # a closed OAM work order still has an independent return obligation.
        latest = return_sources(db, actor=current, work_order_id=work_order_id)
        if latest.model_dump(exclude={"queried_at"}) != sources.model_dump(exclude={"queried_at"}):
            _changed()
        if _policies(db, set(policies), datetime.now(timezone.utc))[1] != fingerprint:
            _changed()
        _unfrozen(db, accounts, datetime.now(timezone.utc))
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(sources.ledger_cursor, sources.projected_at))
        posting._require_current_actor(db, current)
        digest = selection_hash(work_order_id=work_order_id, request=request)
        return WorkOrderReturnSelectionOut(operator_person_id=current.person_id,
            authorization_version=current.authorization_version, work_order=sources.work_order,
            location_id=sources.location_id, ledger_cursor=sources.ledger_cursor, checked_at=checked_at,
            selection_hash=digest, basis_hash=_hash({"selection_hash": digest,
                "sources": sources.model_dump(mode="json", exclude={"queried_at"}), "policies": fingerprint}),
            lines=tuple(sorted(reviewed, key=lambda row: str(row.source.source_recovery_line_id))))
