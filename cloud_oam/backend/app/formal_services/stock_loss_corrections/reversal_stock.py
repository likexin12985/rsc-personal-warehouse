"""Read-only stock preparation for a dedicated loss reversal.

This is not a posting permit or public preview. Atomic stock writing, deferred
authority/causality guards and downstream compensation remain separate gates.
The original four disposition kinds are supported before fulfillment; later
correction plans and scrap lifecycle require their own complete proofs.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from sqlalchemy import or_, select

from app.inventory_models import (
    CustodyAssignment, InventoryMovement, InventorySerial, InventoryTransaction,
    SerialCurrentPosition, StockAccount, StockBalance, StockLocation,
)
from app.stock_operation_models import StockLossDisposition
from app.formal_services import inventory_posting as posting, inventory_query as inventory
from app.formal_services import stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware
from .chain_projection import project
from .history_events import load_event_checked_inventory_history
from .historical_holds import read_hold_snapshot
from .historical_original import verify_historical_original, _bound
from .request_contracts import ReversalPreview, ReversalExecute, validate
from . import request_authority
from . import return_dependencies, return_history, return_boundary


@dataclass(frozen=True)
class StockPreparation:
    document_json: str
    plan_hash: str
    checked_at: datetime

    @property
    def document(self):
        return json.loads(self.document_json)


def _fail(code, message):
    sources._fail(code, message, 412)


def _return_boundary(db, execution):
    if execution.disposition != 'return_to_region':
        return None
    before = _bound(db)
    stages = return_dependencies.read(db, disposition_id=execution.id)
    # The caller already checks current reverse_loss authority. Prove both
    # ends of the fulfillment graph without borrowing the read permission or
    # interpreting a missing outbound header as an unfulfilled return.
    groups, fingerprint, lines, _ = return_history._verified_graph(db, execution)
    if stages.has_downstream_facts:
        _fail('loss_reversal_requires_return_compensation', '原退回已有后续事实，须按出库、发运、验收和入库逐项办理补偿')
    if (return_history._capture(db, execution)[1] != fingerprint or _bound(db) != before):
        _fail('loss_reversal_read_changed', '预检期间退回履约事实发生变化，请重新核验')
    # This binds the exact child lines and SN to the stock preparation hash.
    # It is still not a stop fact or authority to post a return compensation.
    return return_boundary.document(execution, groups['orders'][0], lines)


def _custody(db, *, account, requester_id, at):
    location = db.get(StockLocation, account.location_id, populate_existing=True)
    if (location is None or location.status != 'active' or location.location_type != 'personal'
            or location.owner_org_id != account.owner_org_id
            or location.custodian_person_id != requester_id or account.custodian_person_id != requester_id):
        _fail('loss_reversal_custody_changed', '原个人仓与当前保管责任不一致，须核验交接')
    rows = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == location.id,
        CustodyAssignment.valid_from <= at, or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
        .execution_options(populate_existing=True)))
    if len(rows) != 1 or rows[0].custodian_person_id != requester_id:
        _fail('loss_reversal_custody_changed', '原个人仓必须有唯一有效的当前保管责任')
    return rows[0]


def prepare(db, *, actor, request):
    request = validate(request)
    if type(request) not in (ReversalPreview, ReversalExecute):
        raise ValueError('an exact loss reversal selection is required')
    selection = ReversalPreview.model_validate({key:getattr(request, key) for key in ReversalPreview.model_fields})
    with db.no_autoflush:
        refs = request_authority.references(db, actor=actor, request=selection)
        current, root, order, execution = refs.actor, refs.root, refs.order, refs.execution
        start = _bound(db)
        loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
        history = loaded.history
        progress = project(history.basis, history.executions, history.reversals, history.decisions)
        if progress.active_execution_id != execution.id or progress.pending_reversal_id is not None:
            _fail('loss_reversal_execution_not_current', '指定原执行已被冲销或不是当前执行，请回查准确原记录')
        from .history_chain import verify_chain
        verify_chain(db, root_disposition_id=root.id)
        boundary = _return_boundary(db, execution)
        at = datetime.now(timezone.utc)
        source = db.get(StockAccount, execution.target_account_id, populate_existing=True)
        target = db.get(StockAccount, root.source_account_id, populate_existing=True)
        if (source is None or target is None or source.id == target.id or target.availability_bucket != 'frozen'
                or any(getattr(source, key) != getattr(target, key) for key in
                    ('owner_org_id', 'location_id', 'custodian_person_id', 'material_id', 'lot_id'))):
            _fail('loss_reversal_accounts_changed', '必须回到原明细的准确冻结账户')
        custody = _custody(db, account=target, requester_id=order.requester_id, at=at)
        accounts = {source.id: source, target.id: target}
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
        snapshot = inventory._projection_snapshot(db)
        if loaded.observed_ledger_cursor != snapshot.ledger_cursor:
            _fail('loss_reversal_read_changed', '预检期间库存已有变化，请重新核验')
        opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
            required_pairs={(target.owner_org_id, target.location_id)}, discover_authorized_zero_scopes=False)
        if not opening.complete:
            _fail('loss_reversal_opening_required', '原库存范围缺少完整期初证据')
        inventory._validate_current_projection_integrity(db, snapshot=snapshot, account_ids=set(accounts))
        balances = {identifier: db.get(StockBalance, identifier, populate_existing=True) for identifier in accounts}
        if any(value is None for value in balances.values()): inventory._invalid_current_projection()
        if balances[source.id].quantity < root.quantity:
            _fail('loss_reversal_stock_insufficient', '准确反向来源库存不足，不能借用其他账户补足')
        holds = read_hold_snapshot(db, source_account_id=target.id, through_cursor=snapshot.ledger_cursor)
        selected = next((line for line in holds.lines if line.line_id == root.line_id), None)
        if (selected is None or selected.active_execution_id != execution.id or selected.frozen_quantity != 0
                or holds.observed_ledger_cursor != snapshot.ledger_cursor):
            _fail('loss_reversal_hold_changed', '原单冻结份额或当前执行已变化')
        for line in holds.lines:
            if line.root_disposition_id is not None and line.root_disposition_id != root.id:
                verify_historical_original(db, root_disposition_id=line.root_disposition_id)
        serial_ids = tuple(sorted(history.basis.serial_ids, key=str))
        move = db.get(InventoryMovement, execution.posting_movement_id, populate_existing=True)
        tx = db.get(InventoryTransaction, execution.posting_transaction_id, populate_existing=True)
        policies, fingerprint = sources._policies(db, {target.material_id}, at)
        movement = posting.InventoryMovementCommand(source.id, target.id, root.quantity, serial_ids, move.external_boundary_code)
        posting._validate_tracking_rules(posting.InventoryPostingCommand(transaction_no='loss-reversal-preparation',
            movement_type='reversal', source_document_type='stock_loss_disposition_reversal', source_document_id=str(root.id),
            posting_key='loss-reversal-preparation', effective_at=at, movements=(movement,)), accounts, policies)
        now_states = rebuild_serial_states(db, serial_ids, through_cursor=snapshot.ledger_cursor)
        before_states = rebuild_serial_states(db, serial_ids, through_cursor=tx.ledger_cursor - 1)
        serial_basis = []
        for identifier in serial_ids:
            now, prior = now_states.get(identifier), before_states.get(identifier)
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if (now is None or prior is None or serial is None or position is None
                    or now.stock_account_id != source.id or now.last_movement_id != move.id):
                _fail('loss_reversal_serial_has_later_activity', '原 SN 已有后续移动，不能直接反向较早处置')
            if (prior.stock_account_id != target.id or prior.lifecycle_status != 'active' or now.lifecycle_status != 'active'
                    or serial.lifecycle_status != now.lifecycle_status or position.stock_account_id != now.stock_account_id
                    or position.last_movement_id != now.last_movement_id):
                inventory._invalid_current_projection()
            serial_basis.append(dict(serial_id=str(identifier), original_movement_id=str(move.id),
                lifecycle_before=now.lifecycle_status, lifecycle_after=prior.lifecycle_status,
                previous_movement_id=str(prior.last_movement_id), previous_ledger_cursor=prior.ledger_cursor))
        document = dict(schema_version='1.0', stage='stock_preparation_only', intent=selection.model_dump(mode='json'),
            actor_user_id=current.user_id, actor_person_id=str(current.person_id), authorization_version=current.authorization_version,
            root_disposition_id=str(root.id), original_execution_id=str(execution.id),
            original_transaction_id=str(tx.id), original_movement_id=str(move.id), original_ledger_cursor=tx.ledger_cursor,
            source_account_id=str(source.id), target_account_id=str(target.id), quantity=format(root.quantity, '.3f'),
            serial_ids=[str(s) for s in serial_ids], serials=serial_basis,
            source_condition=source.condition_code, target_condition=target.condition_code,
            custody_assignment_id=str(custody.id), ledger_cursor=snapshot.ledger_cursor,
            source_balance_quantity=format(balances[source.id].quantity, '.3f'), source_balance_version=balances[source.id].version,
            target_balance_quantity=format(balances[target.id].quantity, '.3f'), target_balance_version=balances[target.id].version,
            frozen_holds_before=holds.plan_basis(), restored_line_id=str(root.line_id),
            restored_frozen_quantity=format(root.quantity, '.3f'), return_boundary=boundary,
            policy_fingerprint=[list(row) for row in fingerprint])
        # Recheck current mutable reference facts, authority and read bounds.
        later = datetime.now(timezone.utc)
        if (_custody(db, account=target, requester_id=order.requester_id, at=later).id != custody.id
                or sources._policies(db, {target.material_id}, later)[1] != fingerprint
                or _return_boundary(db, execution) != boundary or _bound(db) != start):
            _fail('loss_reversal_read_changed', '预检期间业务、责任或策略发生变化，请重新核验')
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=later)
        request_authority.references(db, actor=current, request=selection)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return StockPreparation(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            sources._hash(document), at)
