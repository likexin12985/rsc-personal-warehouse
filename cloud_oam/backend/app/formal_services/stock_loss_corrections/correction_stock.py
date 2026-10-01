"""Current, query-only stock plan for an independently approved correction.

Private development component. Return child lineage/fulfillment and scrap
lifecycle need their dedicated complete execution proofs before activation.
"""
from datetime import datetime, timezone
import json

from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition, InventorySerial
from app.formal_services import inventory_posting as posting, inventory_query as inventory
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_disposition_plan import target_account
from app.formal_services.serial_ledger import rebuild_serial_states
from .chain_projection import project
from .history_events import load_event_checked_inventory_history
from .history_chain import verify_inverse as verify_original_inverse
from .historical_original import _bound, verify_historical_original
from .historical_holds import read_hold_snapshot
from .request_contracts import CorrectionPreview, CorrectionExecute, validate
from .reversal_stock import StockPreparation, _custody
from . import request_authority


def _fail(code, message):
    sources._fail(code, message, 412)


def prepare(db, *, actor, request):
    request = validate(request)
    if type(request) not in (CorrectionPreview, CorrectionExecute):
        raise ValueError('an exact approved correction selection is required')
    selection = CorrectionPreview.model_validate({key:getattr(request, key) for key in CorrectionPreview.model_fields})
    with db.no_autoflush:
        refs = request_authority.references(db, actor=actor, request=selection)
        current, root, order, inverse, decision = refs.actor, refs.root, refs.order, refs.reversal, refs.decision
        if decision.disposition not in {'restore_available', 'convert_used', 'convert_damaged'}:
            _fail('loss_correction_dedicated_flow_required', '退回与报废的批准须经各自完整作业执行')
        before = _bound(db)
        verify_original_inverse(db, reversal_id=inverse.id)
        loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
        h = loaded.history; state = project(h.basis, h.executions, h.reversals, h.decisions)
        if state.pending_reversal_id != inverse.id or state.active_execution_id is not None:
            _fail('loss_correction_inverse_not_pending', '指定冲销已被后续执行消耗，请回查准确记录')
        at = datetime.now(timezone.utc)
        source = db.get(StockAccount, root.source_account_id, populate_existing=True)
        custody = _custody(db, account=source, requester_id=order.requester_id, at=at)
        target = target_account(db, source, decision.disposition)
        accounts = {source.id: source, target.id: target}
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=at)
        snapshot = inventory._projection_snapshot(db)
        if snapshot.ledger_cursor != loaded.observed_ledger_cursor:
            _fail('loss_correction_read_changed', '纠正预检期间库存发生变化')
        opening = inventory._validated_opening_evidence(db, actor=current, snapshot=snapshot,
            required_pairs={(source.owner_org_id, source.location_id)}, discover_authorized_zero_scopes=False)
        if not opening.complete:
            _fail('loss_correction_opening_required', '准确库存范围缺少完整期初证据')
        existing = target in db
        inventory._validate_current_projection_integrity(db, snapshot=snapshot,
            account_ids={source.id, target.id} if existing else {source.id})
        balance = db.get(StockBalance, source.id, populate_existing=True)
        target_balance = db.get(StockBalance, target.id, populate_existing=True) if existing else None
        if balance is None or (existing and target_balance is None): inventory._invalid_current_projection()
        holds = read_hold_snapshot(db, source_account_id=source.id, through_cursor=snapshot.ledger_cursor)
        selected = next((line for line in holds.lines if line.line_id == root.line_id), None)
        if (selected is None or selected.pending_reversal_id != inverse.id or selected.active_execution_id is not None
                or selected.frozen_quantity != root.quantity or selected.frozen_serial_ids != h.basis.serial_ids
                or holds.observed_ledger_cursor != snapshot.ledger_cursor):
            _fail('loss_correction_hold_changed', '原反向的冻结数量或SN份额已变化')
        for line in holds.lines:
            if line.root_disposition_id is not None and line.root_disposition_id != root.id:
                verify_historical_original(db, root_disposition_id=line.root_disposition_id)
        serial_ids = tuple(sorted(h.basis.serial_ids, key=str))
        states = rebuild_serial_states(db, serial_ids, through_cursor=snapshot.ledger_cursor)
        for identifier in serial_ids:
            state = states.get(identifier)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            if (state is None or position is None or serial is None or state.stock_account_id != source.id
                    or state.last_movement_id != inverse.posting_movement_id or state.lifecycle_status != 'active'
                    or position.stock_account_id != state.stock_account_id or position.last_movement_id != state.last_movement_id
                    or serial.lifecycle_status != state.lifecycle_status):
                _fail('loss_correction_serial_changed', '准确冻结SN已有后续移动或投影不一致')
        policies, fingerprint = sources._policies(db, {source.material_id}, at)
        movement_type = 'unfreeze' if decision.disposition == 'restore_available' else 'status_change'
        posting._validate_tracking_rules(posting.InventoryPostingCommand(transaction_no='correction-preparation',
            movement_type=movement_type, source_document_type='stock_loss_correction_execution',
            source_document_id=str(root.id), posting_key='correction-preparation', effective_at=at,
            movements=(posting.InventoryMovementCommand(source.id, target.id, root.quantity, serial_ids),)), accounts, policies)
        document = dict(schema_version='1.0', stage='correction_execution_plan', intent=selection.model_dump(mode='json'),
            actor_user_id=current.user_id, actor_person_id=str(current.person_id), authorization_version=current.authorization_version,
            root_disposition_id=str(root.id), reversal_id=str(inverse.id), correction_decision_id=str(decision.id),
            disposition=decision.disposition, source_account_id=str(source.id), target_account_id=str(target.id),
            source_condition=source.condition_code, target_condition=target.condition_code, target_requires_creation=not existing,
            custody_assignment_id=str(custody.id), quantity=format(root.quantity, '.3f'), serial_ids=[str(s) for s in serial_ids],
            movement_type=movement_type, ledger_cursor=snapshot.ledger_cursor,
            source_balance_quantity=format(balance.quantity, '.3f'), source_balance_version=balance.version,
            target_balance_quantity=format(target_balance.quantity, '.3f') if existing else '0.000',
            target_balance_version=target_balance.version if existing else 0,
            frozen_holds_before=holds.plan_basis(), policy_fingerprint=[list(row) for row in fingerprint])
        later = datetime.now(timezone.utc)
        if (_bound(db) != before or sources._policies(db, {source.material_id}, later)[1] != fingerprint
                or _custody(db, account=source, requester_id=order.requester_id, at=later).id != custody.id):
            _fail('loss_correction_read_changed', '纠正预检期间责任、策略或业务事实发生变化')
        posting._require_active_account_masters(db, accounts)
        posting._require_no_active_hard_freezes(db, accounts, effective_at=later)
        request_authority.references(db, actor=current, request=selection)
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return StockPreparation(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            sources._hash(document), at)
