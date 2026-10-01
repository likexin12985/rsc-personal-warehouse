"""Complete first-correction stock plan proof from immutable prior facts."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from app.inventory_models import StockAccount, InventoryTransaction, CustodyAssignment
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware
from .chain_projection import InvalidChain
from .correction_models import StockLossCorrectionExecution as Execution, StockLossCorrectionDecision as Decision
from .correction_models import StockLossDispositionReversal as Inverse
from .history_events import load_event_checked_inventory_history
from .history_inventory import correction_command
from .historical_original import _bound, verify_historical_original
from .historical_inverse import _balance
from .historical_holds import read_hold_snapshot
from .request_contracts import CorrectionPreview


def _need(condition):
    if not condition:
        raise InvalidChain('loss_correction_historical_plan_invalid')


@dataclass(frozen=True)
class HistoricalCorrection:
    correction_execution_id: UUID
    root_disposition_id: UUID
    posting_transaction_id: UUID
    posting_cursor: int
    observed_ledger_cursor: int
    plan_hash: str


def verify_plan(db, *, correction_execution_id, proved_inverse_ids):
    _need(type(correction_execution_id) is UUID and correction_execution_id.int != 0)
    with db.no_autoflush:
        start = _bound(db)
        row = db.get(Execution, correction_execution_id, populate_existing=True)
        _need(row is not None and row.disposition in {'restore_available', 'convert_used', 'convert_damaged'})
        root = db.get(StockLossDisposition, row.root_disposition_id, populate_existing=True)
        inverse = db.get(Inverse, row.reversal_id, populate_existing=True)
        decision = db.get(Decision, row.correction_decision_id, populate_existing=True)
        _need(root is not None and inverse is not None and decision is not None)
        _need(inverse.id in proved_inverse_ids)
        loaded = load_event_checked_inventory_history(db, root_disposition_id=root.id)
        _need(loaded.observed_ledger_cursor == start[0]
            and any(fact.id == row.id for fact in loaded.history.executions))
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True)
        tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
        source = db.get(StockAccount, row.source_account_id, populate_existing=True)
        target = db.get(StockAccount, row.target_account_id, populate_existing=True)
        _need(all(value is not None for value in (order, tx, source, target)))
        at = _aware(row.created_at); cursor = tx.ledger_cursor - 1
        _need(row.source_account_id == root.source_account_id and row.quantity == root.quantity
            and source.availability_bucket == 'frozen' and target.availability_bucket == 'available'
            and source.id != target.id and all(getattr(source, k) == getattr(target, k) for k in
                ('owner_org_id', 'location_id', 'custodian_person_id', 'material_id', 'lot_id'))
            and target.condition_code == {'restore_available': source.condition_code,
                'convert_used': 'used', 'convert_damaged': 'damaged'}[row.disposition]
            and _aware(target.created_at) <= at)
        custody = tuple(db.scalars(select(CustodyAssignment).where(
            CustodyAssignment.location_id == source.location_id, CustodyAssignment.valid_from <= at,
            or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))
            .execution_options(populate_existing=True)))
        _need(len(custody) == 1 and custody[0].id == row.custody_assignment_id
            and custody[0].custodian_person_id == source.custodian_person_id == order.requester_id)
        source_balance, source_version = _balance(db, source.id, cursor)
        target_balance, target_version = _balance(db, target.id, cursor)
        new_target = _aware(target.created_at) == at
        if new_target:
            fields = {k:getattr(target, k) for k in ('owner_org_id', 'location_id', 'custodian_person_id',
                'material_id', 'lot_id', 'condition_code', 'availability_bucket')}
            expected_id = uuid5(NAMESPACE_URL, 'rsc:stock-loss-disposition-account:v1:' + sources._hash(
                {k:str(value) if value is not None else None for k, value in fields.items()}))
            _need(target.id == expected_id and target_balance == 0 and target_version == 0)
        holds = read_hold_snapshot(db, source_account_id=source.id, through_cursor=cursor)
        share = next((line for line in holds.lines if line.line_id == root.line_id), None)
        _need(holds.observed_ledger_cursor == start[0] and share is not None
            and share.pending_reversal_id == inverse.id and share.active_execution_id is None
            and share.frozen_quantity == root.quantity and share.frozen_serial_ids == loaded.history.basis.serial_ids
            and source_balance >= root.quantity)
        for line in holds.lines:
            if line.root_disposition_id is not None and line.root_disposition_id != root.id:
                verify_historical_original(db, root_disposition_id=line.root_disposition_id)
        serial_ids = tuple(sorted(loaded.history.basis.serial_ids, key=str))
        states = rebuild_serial_states(db, serial_ids, through_cursor=cursor)
        _need(all(identifier in states and states[identifier].stock_account_id == source.id
            and states[identifier].last_movement_id == inverse.posting_movement_id
            and states[identifier].lifecycle_status == 'active' for identifier in serial_ids))
        policies, fingerprint = sources._policies(db, {source.material_id}, at)
        posting._validate_tracking_rules(correction_command(row, serial_ids), {source.id: source, target.id: target}, policies)
        recorded = row.plan_jsonb.get('policy_fingerprint')
        _need(type(recorded) is list and len(recorded) == 1 and type(recorded[0]) is list
            and len(recorded[0]) == 7 and recorded[0][:6] == list(fingerprint[0][:6]))
        if recorded[0][6] is not None:
            _need(recorded[0][6] == fingerprint[0][6] and _aware(datetime.fromisoformat(recorded[0][6])) > at)
        intent = CorrectionPreview.model_validate(row.command_jsonb['intent']).model_dump(mode='json')
        expected = dict(schema_version='1.0', stage='correction_execution_plan', intent=intent,
            actor_user_id=row.actor_user_id, actor_person_id=str(row.actor_person_id), authorization_version=row.authorization_version,
            root_disposition_id=str(root.id), reversal_id=str(inverse.id), correction_decision_id=str(decision.id),
            disposition=decision.disposition, source_account_id=str(source.id), target_account_id=str(target.id),
            source_condition=source.condition_code, target_condition=target.condition_code, target_requires_creation=new_target,
            custody_assignment_id=str(custody[0].id), quantity=format(row.quantity, '.3f'), serial_ids=[str(s) for s in serial_ids],
            movement_type='unfreeze' if decision.disposition == 'restore_available' else 'status_change', ledger_cursor=cursor,
            source_balance_quantity=format(source_balance, '.3f'), source_balance_version=source_version,
            target_balance_quantity=format(target_balance, '.3f'), target_balance_version=target_version,
            frozen_holds_before=holds.plan_basis(), policy_fingerprint=recorded)
        _need(sources._hash(expected) == row.plan_hash == sources._hash(row.plan_jsonb))
        _need(_bound(db) == start)
        return HistoricalCorrection(row.id, root.id, tx.id, tx.ledger_cursor, start[0], row.plan_hash)
