"""Read-only exact recovery request, approvals, inverse and original-cursor plan."""
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import or_, select
from app.inventory_models import CustodyAssignment, InventoryTransaction
from app.stock_scrap_recovery_schemas import ScrapRecoveryExecute, ScrapRecoveryPreview
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_corrections import business_events, posting_events
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.historical_inverse import HistoricalInverse, _balance
from app.formal_services.work_order_query import _aware
from . import recovery_facts as facts, recovery_plan, recovery_authority as authority, events
from .tables import tables


def request_fact(*, row, root, order, reversed_execution):
    command = ScrapRecoveryExecute.model_validate(dict(row.command_jsonb, idempotency_key='historical-proof-only'))
    target = reversed_execution if row.reversed_correction_id else root
    facts.need(target is not None and row.source_account_id is None and target.disposition == 'scrap'
        and facts.intent(command) == row.command_jsonb and sources._hash(row.command_jsonb) == row.request_hash
        and command.request_id == row.request_id and command.reason == row.reason and command.expected_plan_hash == row.plan_hash
        and command.source.expected_scrap_request_hash == target.request_hash
        and row.root_disposition_id == root.id and root.operation_id == order.id
        and row.original_transaction_id == target.posting_transaction_id and row.original_movement_id == target.posting_movement_id
        and row.target_account_id == target.source_account_id == root.source_account_id and row.quantity == target.quantity == root.quantity
        and row.authorization_version > 0 and UUID(row.actor_user_id).int > 0 and row.actor_person_id.int > 0
        and _aware(row.created_at) > _aware(target.created_at))
    if row.reversed_correction_id:
        facts.need(target.id == row.reversed_correction_id and target.root_disposition_id == root.id)
    return command


def records(db, inverse):
    schema = tables()
    raw = db.execute(select(schema['stock_loss_disposition_reversals']).where(
        schema['stock_loss_disposition_reversals'].c.id == inverse.id)).mappings().one()
    recovery = db.execute(select(schema['stock_scrap_recovery_executions']).where(
        schema['stock_scrap_recovery_executions'].c.reversal_id == inverse.id)).mappings().one()
    return raw, recovery


def child_event(inverse, source, recovery):
    body = dict(recovery_execution_id=str(recovery['id']), recovery_request_id=str(recovery['recovery_request_id']),
        headquarters_review_id=str(recovery['headquarters_review_id']), scrap_line_id=str(recovery['scrap_line_id']),
        reversal_id=str(inverse.id), posting_transaction_id=str(inverse.posting_transaction_id),
        request_id=inverse.request_id, request_hash=inverse.request_hash, plan_hash=inverse.plan_hash,
        status='posted', stock_effect='restores_original_frozen_share')
    order = dict(actor_user_id=inverse.actor_user_id, requester_id=source['order'].requester_id,
        request_id=inverse.request_id, created_at=_aware(inverse.created_at))
    return dict(aggregate='stock_scrap_recovery_execution', identifier=recovery['id'],
        kind='stock_scrap.recovered', body=body, order=order, notify=False)


def record_child(db, inverse, source):
    _, recovery = records(db, inverse)
    events._record(db, **child_event(inverse, source, recovery))


def verify_execution(db, *, inverse, source):
    root, fact, child = source['root'], source['fact'], source['line']
    command = request_fact(row=inverse, root=root, order=source['order'], reversed_execution=fact if inverse.reversed_correction_id else None)
    raw, recovery = records(db, inverse)
    _, _, final = recovery_plan.approval(db, command, source)
    facts.need(command.source.scrap_line_id == child['id'] == raw['scrap_line_id'] == recovery['scrap_line_id']
        and raw['scrap_source_kind'] == child['source_kind'] and inverse.reversed_correction_id == child['correction_execution_id']
        and raw['scrap_recovery_execution_id'] == recovery['id'] and recovery['reversal_id'] == inverse.id
        and recovery['recovery_request_id'] == command.recovery_request_id
        and recovery['headquarters_review_id'] == final['id'] == command.headquarters_review_id
        and recovery['expected_headquarters_hash'] == command.expected_headquarters_hash == final['request_hash']
        and recovery['headquarters_decision'] == 'approve' and _aware(inverse.created_at) > _aware(final['created_at']))
    for key in ('actor_user_id', 'actor_person_id', 'authorization_version', 'request_id', 'request_hash',
                'idempotency_key_hash', 'reason', 'command_jsonb', 'plan_hash', 'plan_jsonb'):
        facts.need(recovery[key] == raw[key] == getattr(inverse, key))
    facts.need(_aware(recovery['created_at']) == _aware(raw['created_at']) == _aware(inverse.created_at)
        and sources._hash(inverse.plan_jsonb) == inverse.plan_hash)
    posting_events.verify(db, fact=inverse)
    business_events.verify(db, row=inverse, root=root, order=source['order'])
    events._verify(db, **child_event(inverse, source, recovery))
    return command


def verify_plan(db, *, inverse, proved_execution_ids):
    with db.no_autoflush:
        start = _bound(db)
        selection = ScrapRecoveryPreview.model_validate({k:inverse.command_jsonb[k] for k in ScrapRecoveryPreview.model_fields})
        source = authority.load_source(db, selection.source)
        facts.need(source['fact'].id in proved_execution_ids)
        verify_execution(db, inverse=inverse, source=source)
        tx = db.get(InventoryTransaction, inverse.posting_transaction_id, populate_existing=True)
        facts.need(tx is not None and tx.ledger_cursor <= start[0])
        cursor, at = tx.ledger_cursor-1, _aware(inverse.created_at)
        target = source['account']
        assignments = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == target.location_id,
            CustodyAssignment.valid_from <= at, or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))))
        facts.need(len(assignments) == 1 and assignments[0].id == inverse.custody_assignment_id
            and assignments[0].custodian_person_id == source['order'].requester_id == target.custodian_person_id)
        holds = read_hold_snapshot(db, source_account_id=target.id, through_cursor=cursor)
        held = next((r for r in holds.lines if r.line_id == source['root'].line_id), None)
        facts.need(holds.observed_ledger_cursor == start[0] and held is not None and held.active_execution_id == source['fact'].id
            and held.pending_reversal_id is None and held.frozen_quantity == 0)
        amount, version = _balance(db, target.id, cursor)
        facts.need(amount == holds.balance_quantity and version == holds.balance_version)
        original_tx, serials, _ = recovery_plan.serial_basis(db, source, cursor=cursor)
        policies, fingerprint = sources._policies(db, {target.material_id}, at)
        recovery_plan.validate_tracking(source, serials, policies, at)
        recorded = inverse.plan_jsonb.get('policy_fingerprint')
        facts.need(type(recorded) is list and len(recorded) == 1 and type(recorded[0]) is list
            and len(recorded[0]) == 7 and recorded[0][:6] == list(fingerprint[0][:6]))
        if recorded[0][6] is not None:
            facts.need(recorded[0][6] == fingerprint[0][6] and _aware(datetime.fromisoformat(recorded[0][6])) > at)
        expected = recovery_plan.document(actor=SimpleNamespace(user_id=inverse.actor_user_id, person_id=inverse.actor_person_id,
            authorization_version=inverse.authorization_version), selection=selection, source=source, custody=assignments[0],
            cursor=cursor, balance_quantity=amount, balance_version=version, holds=holds, original_tx=original_tx,
            serials=serials, fingerprint=recorded)
        facts.need(sources._hash(expected) == inverse.plan_hash == sources._hash(inverse.plan_jsonb) and _bound(db) == start)
        return HistoricalInverse(inverse.id, source['root'].id, tx.id, tx.ledger_cursor, start[0], inverse.plan_hash)
