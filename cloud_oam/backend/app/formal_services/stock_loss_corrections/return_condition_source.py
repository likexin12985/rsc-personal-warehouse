"""Current stock evidence for correcting a preserved v1 inbound condition.

This is an internal, non-mutating preparation stage. It supplies no write authority,
reservation or physical verification. Approval, atomic correction and original
request recovery must be installed separately before exposing a write route.
The canonical complete opening proof acquires row locks: use a short ordinary
transaction and roll it back promptly. SQL READ ONLY is not supported here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from pydantic import ConfigDict
from sqlalchemy import func, or_, select

from app.inventory_models import (
    CustodyAssignment, InventoryMovement, InventorySerial, InventoryTransaction,
    SerialCurrentPosition, StockAccount, StockBalance, StockLocation,
)
from app.stock_operation_models import StockOperationReturnInbound, StockOperationReturnInboundLine
from app.work_order_material_schemas import StrictInput
from app.formal_services import inventory_posting as posting, inventory_query as inventory
from app.formal_services import stock_loss_sources as sources
from app.formal_services.serial_ledger import SerialLedgerError, rebuild_serial_states
from app.formal_services.work_order_query import _aware
from . import return_history
from .historical_original import _bound
from .request_contracts import FactId, Digest


class ConditionSourceSelection(StrictInput):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    root_disposition_id: FactId
    inbound_line_id: FactId
    expected_history_fingerprint: Digest


@dataclass(frozen=True)
class ConditionSourceEvidence:
    document_json: str
    evidence_hash: str
    checked_at: datetime

    @property
    def document(self):
        return json.loads(self.document_json)


def _fail(code, message, status=412):
    sources._fail('return_condition_' + code, message, status)


def _authorize(db, actor, source):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    inventory._require_inventory_read(db, current)
    if not current.allows(db, 'inventory', 'read', target_scope_type='organization',
            target_scope_id=str(source.owner_org_id)):
        _fail('read_forbidden', '没有原入库账户的当前库存查看权限', 403)
    return current


def _current(db, *, issue, line, header, snapshot, actor):
    """Rebuild the exact account and affected SN; never substitute another balance."""
    source = db.get(StockAccount, issue.original_target_account_id, populate_existing=True)
    if (source is None or source.id != line.target_account_id
            or source.condition_code != issue.recorded_condition
            or source.availability_bucket != 'available'
            or source.material_id != line.material_id or source.lot_id != line.lot_id
            or source.location_id != header.target_location_id
            or source.custodian_person_id != header.operator_person_id):
        _fail('source_changed', '原入库账户的物料、成色或保管维度不一致')
    current = _authorize(db, actor, source)
    at = datetime.now(timezone.utc)
    location = db.get(StockLocation, source.location_id, populate_existing=True)
    if (location is None or location.status != 'active' or location.location_type != 'region'
            or location.owner_org_id != source.owner_org_id
            or location.custodian_person_id != source.custodian_person_id):
        _fail('custody_changed', '接收区域仓的当前保管责任已变化，须先核验交接')
    assignments = tuple(db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == source.location_id, CustodyAssignment.valid_from <= at,
        or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
        .limit(2).execution_options(populate_existing=True)))
    if len(assignments) != 1 or assignments[0].custodian_person_id != source.custodian_person_id:
        _fail('custody_changed', '原入库账户缺少唯一有效的当前保管责任')
    accounts = {source.id: source}
    posting._require_active_account_masters(db, accounts)
    posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
    opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
        required_pairs={(source.owner_org_id, source.location_id)}, discover_authorized_zero_scopes=False)
    if not opening.complete:
        _fail('opening_required', '原入库区域仓缺少可信期初证明')
    inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids={source.id})
    balance = db.get(StockBalance, source.id, populate_existing=True)
    if balance is None:
        inventory._invalid_current_projection()
    transaction = db.get(InventoryTransaction, header.posting_transaction_id, populate_existing=True)
    movements = tuple(db.scalars(select(InventoryMovement).where(
        InventoryMovement.transaction_id == header.posting_transaction_id,
        InventoryMovement.line_no == line.line_no,
        InventoryMovement.from_account_id == line.source_account_id,
        InventoryMovement.to_account_id == source.id,
        InventoryMovement.quantity == line.accepted_qty).limit(2).execution_options(populate_existing=True)))
    if transaction is None or transaction.status != 'posted' or len(movements) != 1:
        return_history.invalid()
    original = movements[0]
    policies, policy_fingerprint = sources._policies(db, {source.material_id}, at)
    policy = policies[source.material_id]
    tracked = policy.tracking_mode in ('serial', 'lot_and_serial')
    identifiers = issue.affected_serial_ids
    if (tracked and len(identifiers) != issue.affected_quantity) or (not tracked and identifiers):
        _fail('tracking_changed', '当前追踪策略与原破损验收不一致，须核验策略变更')

    # Non-SN stock is fungible. Replenishment cannot prove which physical units
    # remain after an outgoing movement. Do not subtract and claim a FIFO share.
    count, outgoing = db.execute(select(func.count(InventoryMovement.id),
        func.coalesce(func.sum(InventoryMovement.quantity), 0)).join(InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id).where(
        InventoryMovement.from_account_id == source.id, InventoryTransaction.status == 'posted',
        InventoryTransaction.ledger_cursor > transaction.ledger_cursor,
        InventoryTransaction.ledger_cursor <= snapshot.ledger_cursor)).one()
    serials = []
    retained = not count
    if tracked:
        try:
            states = rebuild_serial_states(db, identifiers, through_cursor=snapshot.ledger_cursor)
        except SerialLedgerError:
            inventory._invalid_current_projection()
        retained = True
        for identifier in identifiers:
            state = states.get(identifier)
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if (state is None or serial is None or position is None
                    or serial.material_id != source.material_id or serial.lot_id != source.lot_id
                    or position.stock_account_id != state.stock_account_id
                    or position.last_movement_id != state.last_movement_id
                    or serial.lifecycle_status != state.lifecycle_status):
                inventory._invalid_current_projection()
            unchanged = (state.stock_account_id == source.id and state.last_movement_id == original.id
                and state.lifecycle_status == 'active')
            retained = retained and unchanged
            serials.append(dict(serial_id=str(identifier), serial_no=serial.serial_no,
                qr_code=serial.qr_code, retained_at_original_inbound=unchanged))
    if retained and balance.quantity < issue.affected_quantity:
        inventory._invalid_current_projection()
    assignment = assignments[0]
    return current, dict(source_account_id=str(source.id), owner_org_id=str(source.owner_org_id),
        location_id=str(source.location_id), custodian_person_id=str(source.custodian_person_id),
        material_id=str(source.material_id), lot_id=str(source.lot_id) if source.lot_id else None,
        recorded_condition=source.condition_code, required_condition=issue.required_condition,
        availability_bucket=source.availability_bucket,
        historical_damaged_quantity=format(issue.affected_quantity, '.3f'),
        account_balance_quantity=format(balance.quantity, '.3f'), balance_version=balance.version,
        balance_ledger_cursor=balance.ledger_cursor, observed_ledger_cursor=snapshot.ledger_cursor,
        original_transaction_id=str(transaction.id), original_movement_id=str(original.id),
        original_ledger_cursor=transaction.ledger_cursor,
        custody_assignment_id=str(assignment.id), custody_valid_from=_aware(assignment.valid_from).isoformat(),
        custody_valid_to=_aware(assignment.valid_to).isoformat() if assignment.valid_to else None,
        later_outgoing_count=count, later_outgoing_quantity=format(outgoing, '.3f'),
        serials=serials, policy_fingerprint=[list(row) for row in policy_fingerprint],
        source_status='recorded_stock_retained' if retained else 'later_activity_requires_reconciliation',
        current_projection_verified=True, physical_verification_required=True,
        correction_authorized=False, posting_allowed=False)


def inspect_source(db, *, actor, selection):
    """Bind current evidence to an exact authorized historical exception.

Only the persisted damaged subset is selected. Callers cannot provide a source
account, target, quantity or SN override. Evidence is valid at this read only;
the eventual approval/write must independently reload and lock its own facts.
"""
    if type(selection) is not ConditionSourceSelection:
        raise ValueError('an exact condition source selection is required')
    selection = ConditionSourceSelection.model_validate(selection.model_dump(mode='python'))
    with db.no_autoflush:
        before = _bound(db)
        history = return_history.read(db, actor=actor, root_disposition_id=selection.root_disposition_id)
        if history.evidence_fingerprint != selection.expected_history_fingerprint:
            _fail('history_changed', '原退回履约记录已变化，请重新选择准确异常', 409)
        issue = next((row for row in history.classification_exceptions
            if row.inbound_line_id == selection.inbound_line_id), None)
        if issue is None:
            _fail('exception_not_found', '所选入库明细不是原退回的历史成色异常', 404)
        line = db.get(StockOperationReturnInboundLine, issue.inbound_line_id, populate_existing=True)
        header = db.get(StockOperationReturnInbound, issue.inbound_id, populate_existing=True)
        if line is None or header is None or line.inbound_id != header.id:
            return_history.invalid()
        snapshot = inventory._projection_snapshot(db)
        if snapshot.ledger_cursor != history.observed_ledger_cursor:
            return_history.changed()
        current, basis = _current(db, issue=issue, line=line, header=header, snapshot=snapshot, actor=actor)
        # Repeat mutable references, policy, custody, identity and ledger proof;
        # no cached authority or an ORM object may bridge two inconsistent reads.
        latest_history = return_history.read(db, actor=current, root_disposition_id=selection.root_disposition_id)
        latest, fresh = _current(db, issue=issue, line=line, header=header, snapshot=snapshot, actor=current)
        if latest_history != history or latest != current or fresh != basis or _bound(db) != before:
            return_history.changed()
        inventory._ensure_projection_snapshot_current(db, snapshot)
        document = dict(schema_version='return_condition_source/1', stage='source_evidence_only',
            selection=selection.model_dump(mode='json'), actor_user_id=current.user_id,
            actor_person_id=str(current.person_id), authorization_version=current.authorization_version,
            inbound_id=str(header.id), receipt_line_id=str(issue.receipt_line_id), **basis)
        return ConditionSourceEvidence(json.dumps(document, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')), sources._hash(document), datetime.now(timezone.utc))
