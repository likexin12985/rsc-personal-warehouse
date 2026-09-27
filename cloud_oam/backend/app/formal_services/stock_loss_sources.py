"""Read-only source proof for a loss report, independent of any work order.

The eventual submit command must reload this proof under the posting locks and
bind it to its own immutable loss document, attachments, audit and outbox. A
preview never creates an account, freezes inventory or authorizes a later write.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import hashlib
import json

from sqlalchemy import or_, select

from ..inventory_models import FormalMaterial, InventorySerial, MaterialInventoryPolicy, SerialCurrentPosition, StockAccount
from ..stock_loss_schemas import (
    StockLossSelectionIn, StockLossSelectionLineOut, StockLossSelectionOut, StockLossSourcesOut,
)
from ..work_order_query_schemas import WorkOrderSerialOptionOut
from . import inventory_posting as posting, inventory_query as inventory
from .serial_ledger import SerialLedgerError, rebuild_serial_states
from .stock_loss_planning import ContractError, TrackedQuantity
from .work_order_query import _aware


def _fail(code, message, status_code=409):
    raise inventory.InventoryReadError(code=code, message=message, status_code=status_code)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _policies(db, material_ids, checked_at):
    rows = tuple(db.scalars(select(MaterialInventoryPolicy).where(
        MaterialInventoryPolicy.material_id.in_(material_ids),
        MaterialInventoryPolicy.effective_from <= checked_at,
        or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > checked_at),
    ).execution_options(populate_existing=True)))
    policies = {row.material_id: row for row in rows}
    if (len(rows) != len(material_ids) or set(policies) != material_ids
            or any(row.tracking_mode not in posting.TRACKING_MODES for row in rows)):
        _fail('stock_loss_policy_invalid', '报损物料没有唯一有效库存策略，请核验后重新预检')
    fingerprint = tuple(sorted((str(row.material_id), str(row.id), row.tracking_mode,
        row.quantity_scale, row.allow_fraction, _aware(row.effective_from).isoformat(),
        _aware(row.effective_to).isoformat() if row.effective_to else None) for row in rows))
    return policies, fingerprint


def authorize(db, actor):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    if not current.allows(db, 'stock_operation', 'submit_loss',
                         target_scope_type='person', target_scope_id=str(current.person_id)):
        _fail('stock_loss_forbidden', '没有本人个人仓报损权限', 403)
    return current


def _changed():
    _fail('stock_loss_sources_changed', '报损来源、库存策略或保管关系已变化，请重新预检整批明细')


def _warehouse(db, current):
    warehouse = inventory.personal_warehouse(db, actor=current)
    if (warehouse.opening_balance_status != 'established' or warehouse.location_id is None
            or warehouse.custody_effective_from is None):
        _fail('stock_loss_opening_required', '个人仓尚未完成可信期初建账或保管责任确认')
    return warehouse


def loss_sources(db, *, actor):
    current = authorize(db, actor)
    with db.no_autoflush:
        warehouse = _warehouse(db, current)
        items = []
        for row in warehouse.items:
            if row.availability_bucket != 'available' or row.condition_code not in {'new', 'used', 'damaged'}:
                continue
            if row.quantity is None or row.quantity_status != 'available':
                inventory._invalid_current_projection()
            if Decimal(row.quantity) <= 0:
                continue
            item = db.get(FormalMaterial, row.material_id, populate_existing=True)
            if item is None or item.status != 'active':
                _fail('stock_loss_material_invalid', '报损来源包含已停用或未核验的物料')
            items.append(row)
        inventory._ensure_projection_snapshot_current(db, inventory._ProjectionSnapshot(
            warehouse.ledger_cursor, warehouse.projected_at))
        authorize(db, current)
    return StockLossSourcesOut(person_id=current.person_id,
        authorization_version=current.authorization_version, location_id=warehouse.location_id,
        custody_effective_from=_aware(warehouse.custody_effective_from),
        ledger_cursor=warehouse.ledger_cursor,
        projected_at=_aware(warehouse.projected_at) if warehouse.projected_at else None,
        queried_at=datetime.now(timezone.utc), items=tuple(sorted(items, key=lambda row: str(row.stock_account_id))))


def selection_hash(request):
    return _hash({'kind': 'stock_loss_source_selection', 'operator_person_id': str(request.operator_person_id),
        'lines': [{'stock_account_id': str(row.stock_account_id), 'quantity': format(row.quantity, '.3f'),
            'serial_verifications': [proof.model_dump(mode='json') for proof in sorted(
                row.serial_verifications, key=lambda proof: str(proof.serial_id))]}
            for row in sorted(request.lines, key=lambda row: str(row.stock_account_id))]})


def _unfrozen(db, accounts, at):
    posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
    targets = {key: SimpleNamespace(**{name: getattr(account, name) for name in (
        'owner_org_id', 'location_id', 'material_id', 'condition_code')}, availability_bucket='frozen')
        for key, account in accounts.items()}
    posting._require_no_active_hard_freezes(db, targets, effective_at=at)


def preview_selection(db, *, actor, request: StockLossSelectionIn):
    request = StockLossSelectionIn.model_validate(request.model_dump())
    current = authorize(db, actor)
    if request.operator_person_id != current.person_id:
        _fail('operator_mismatch', '操作人必须是当前登录人员', 403)
    with db.no_autoflush:
        sources = loss_sources(db, actor=current)
        snapshot = inventory._ProjectionSnapshot(sources.ledger_cursor, sources.projected_at)
        candidates = {row.stock_account_id: row for row in sources.items}
        checked_at = datetime.now(timezone.utc)
        selected = tuple(candidates.get(row.stock_account_id) for row in request.lines)
        if any(row is None for row in selected):
            _fail('stock_loss_source_invalid', '报损明细必须准确绑定本人已开账个人仓的可用库存')
        policies, policy_fingerprint = _policies(db, {row.material_id for row in selected}, checked_at)
        accounts, serial_ids, reviewed = {}, set(), []
        for row, source in zip(request.lines, selected, strict=True):
            if row.stock_account_id in accounts:
                _fail('stock_loss_source_duplicate', '同一库存账户只能选择一次，请合并报损数量')
            if row.quantity > Decimal(source.quantity):
                _fail('stock_loss_quantity_insufficient', '报损数量超过本人该账户当前可用库存')
            ids = tuple(proof.serial_id for proof in row.serial_verifications)
            if len(set(ids)) != len(ids) or serial_ids.intersection(ids):
                _fail('stock_loss_serial_duplicate', '整批报损中同一 SN 只能选择一次')
            serial_ids.update(ids)
            policy = policies[source.material_id]
            try:
                TrackedQuantity(row.quantity, policy.tracking_mode, policy.quantity_scale,
                                policy.allow_fraction, ids).validate(source.lot_id)
            except ContractError as error:
                _fail('stock_loss_' + error.code, '报损数量、批次或 SN 与当前库存策略不一致')
            accounts[row.stock_account_id] = db.get(StockAccount, row.stock_account_id, populate_existing=True)
            try:
                states = rebuild_serial_states(db, set(ids), through_cursor=sources.ledger_cursor)
            except SerialLedgerError:
                inventory._ensure_projection_snapshot_current(db, snapshot)
                inventory._invalid_current_projection()
            serials = []
            for proof in row.serial_verifications:
                serial = db.get(InventorySerial, proof.serial_id, populate_existing=True)
                position = db.get(SerialCurrentPosition, proof.serial_id, populate_existing=True)
                state = states.get(proof.serial_id)
                if (serial is None or position is None or state is None
                        or serial.lifecycle_status != 'active' or state.lifecycle_status != 'active'
                        or position.stock_account_id != source.stock_account_id
                        or state.stock_account_id != source.stock_account_id
                        or state.last_movement_id != position.last_movement_id
                        or serial.material_id != source.material_id or serial.lot_id != source.lot_id):
                    inventory._ensure_projection_snapshot_current(db, snapshot)
                    _fail('stock_loss_serial_source_invalid', 'SN 不属于本人该库存账户的当前可用实物')
                if (proof.sku_code != source.sku_code or proof.serial_no != serial.serial_no
                        or proof.qr_code != serial.qr_code):
                    _fail('stock_loss_serial_verification_mismatch', '二维码、SKU、SN 校验不一致')
                serials.append(WorkOrderSerialOptionOut(serial_id=serial.id, serial_no=serial.serial_no))
            reviewed.append(StockLossSelectionLineOut(source=source, selected_quantity=format(row.quantity, '.3f'),
                selected_serials=tuple(sorted(serials, key=lambda serial: str(serial.serial_id)))))
        _unfrozen(db, accounts, checked_at)
        latest = loss_sources(db, actor=current)
        if latest.model_dump(exclude={'queried_at'}) != sources.model_dump(exclude={'queried_at'}):
            _changed()
        if _policies(db, set(policies), datetime.now(timezone.utc))[1] != policy_fingerprint:
            _changed()
        _unfrozen(db, accounts, datetime.now(timezone.utc))
        inventory._ensure_projection_snapshot_current(db, snapshot)
        authorize(db, current)
        digest = selection_hash(request)
        return StockLossSelectionOut(operator_person_id=current.person_id,
            authorization_version=current.authorization_version, location_id=sources.location_id,
            ledger_cursor=sources.ledger_cursor, checked_at=checked_at, selection_hash=digest,
            basis_hash=_hash({'selection_hash': digest, 'sources': sources.model_dump(mode='json',
                exclude={'queried_at'}), 'policies': policy_fingerprint}),
            lines=tuple(sorted(reviewed, key=lambda row: str(row.source.stock_account_id))))
