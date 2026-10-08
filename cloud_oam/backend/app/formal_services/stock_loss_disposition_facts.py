"""Historical proof of an exact approved loss-line disposition.

No current authority is inferred here. Callers must authorize reads/writes; the
original actors and ledger cursor remain the coordinates of historical proof.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from ..foundation_models import AuditEvent, NotificationEvent, NotificationPersonTarget, OutboxEvent, StateTransitionEvent
from ..inventory_models import CustodyAssignment, InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockAccount
from ..stock_operation_models import (StockLossDisposition as Disposition, StockLossHeadquartersDecision as Decision,
    StockLossHeadquartersReview as Review, StockOperationLine as Line, StockOperationOrder as Order, StockOperationSerial as Serial)
from ..stock_loss_schemas import StockLossDispositionPreviewIn
from . import inventory_posting as posting, stock_loss_facts as original, stock_loss_sources as sources
from . import stock_loss_headquarters_reviews as headquarters
from .audit_chain import AuditChainError
from .notification_events import target_manifest_hash
from .serial_ledger import SerialLedgerError, rebuild_serial_states
from .work_order_query import _aware

AGGREGATE = 'stock_loss_disposition'
KIND = 'stock_loss.disposition_posted'


def invalid():
    sources._fail('stock_loss_disposition_evidence_invalid', '报损处置与原批准、冻结份额或流水证据不一致，请保留原请求核验', 503)


def posting_command(row):
    if row.disposition == 'scrap':
        from .stock_scrap.request_facts import posting_command as scrap_command
        return scrap_command(row)
    return posting.InventoryPostingCommand(transaction_no='INV-LOSS-D-'+row.idempotency_key_hash[:20].upper(),
        movement_type=row.plan_jsonb['movement_type'], source_document_type=AGGREGATE, source_document_id=str(row.id),
        posting_key=f'stock-loss:dispose_loss:{row.id}:{row.idempotency_key_hash}', effective_at=_aware(row.created_at),
        movements=(posting.InventoryMovementCommand(from_account_id=row.source_account_id, to_account_id=row.target_account_id,
            quantity=row.quantity, serial_ids=tuple(UUID(s) for s in row.plan_jsonb['serial_ids'])),))


def payload(row):
    if row.disposition == 'scrap':
        from .stock_scrap.request_facts import original_payload
        return original_payload(row)
    if row.disposition == "return_to_region":
        from .stock_loss_return_facts import payload as return_payload
        return return_payload(row)
    return dict(disposition_id=str(row.id), operation_id=str(row.operation_id), line_id=str(row.line_id),
        headquarters_decision_id=str(row.headquarters_decision_id), disposition=row.disposition,
        executor_person_id=str(row.executor_person_id), authorization_version=row.authorization_version,
        posting_transaction_id=str(row.posting_transaction_id), posting_movement_id=str(row.posting_movement_id),
        quantity=format(row.quantity, '.3f'), source_account_id=str(row.source_account_id), target_account_id=str(row.target_account_id),
        request_id=row.request_id, request_hash=row.request_hash, plan_hash=row.plan_hash, status='posted')


def historical_hold_basis(db, source_id, cursor):
    """Use original freezes/releases at this cursor, never today's balances."""
    basis = []
    lines = db.scalars(select(Line).join(Order, Order.id == Line.operation_id)
        .join(InventoryTransaction, InventoryTransaction.id == Order.posting_transaction_id).where(
            Line.operation_type == 'loss_report', Line.reserved_account_id == source_id,
            InventoryTransaction.ledger_cursor <= cursor).order_by(Line.id))
    for line in lines:
        release = db.scalar(select(Disposition).join(InventoryTransaction,
            InventoryTransaction.id == Disposition.posting_transaction_id).where(
                Disposition.line_id == line.id, InventoryTransaction.ledger_cursor <= cursor))
        serials = sorted(str(s) for s in db.scalars(select(Serial.serial_id).where(Serial.line_id == line.id)))
        basis.append(dict(line_id=str(line.id), operation_id=str(line.operation_id), quantity=format(line.quantity, '.3f'),
            serial_ids=serials, disposition_id=str(release.id) if release else None))
    return basis


def _verify(db, row):
    if row.disposition == 'scrap':
        if db.scalar(select(InventoryTransaction.id).where(
                InventoryTransaction.reversed_transaction_id == row.posting_transaction_id).limit(1)):
            invalid()
        from .stock_loss_corrections.history_chain import verify_chain
        verify_chain(db, root_disposition_id=row.id)
        return payload(row)
    if row.disposition == "return_to_region":
        from .stock_loss_return_facts import _verify as verify_return
        return verify_return(db, row)
    line = db.get(Line, row.line_id, populate_existing=True)
    order = db.get(Order, row.operation_id, populate_existing=True)
    decision = db.get(Decision, row.headquarters_decision_id, populate_existing=True)
    review = db.get(Review, decision.review_id, populate_existing=True) if decision else None
    if line is None or order is None or decision is None or review is None:
        invalid()
    original.submission_evidence(db, order=order)
    headquarters.verified(db, row=review, order=order)
    source = db.get(StockAccount, row.source_account_id, populate_existing=True)
    target = db.get(StockAccount, row.target_account_id, populate_existing=True)
    custody = db.get(CustodyAssignment, row.custody_assignment_id, populate_existing=True)
    tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
    plan = row.plan_jsonb
    expected_intent = StockLossDispositionPreviewIn(headquarters_decision_id=decision.id,
        expected_headquarters_review_hash=review.request_hash, expected_submission_plan_hash=order.plan_hash).model_dump(mode='json')
    expected_command = dict(intent=expected_intent, request_id=row.request_id, expected_plan_hash=row.plan_hash)
    if (source is None or target is None or custody is None or tx is None
            or row.disposition not in {'restore_available', 'convert_used', 'convert_damaged'}
            or row.disposition != decision.disposition or decision.line_id != line.id
            or line.operation_id != order.id or review.operation_id != order.id
            or line.reserved_account_id != source.id or row.quantity != line.quantity
            or row.quantity <= 0 or row.authorization_version < 1
            or not re.fullmatch(r'[0-9a-f]{64}', row.idempotency_key_hash)
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id)
            or row.command_jsonb != expected_command or sources._hash(expected_command) != row.request_hash
            or sources._hash(plan) != row.plan_hash
            or source.availability_bucket != 'frozen' or target.availability_bucket != 'available'
            or source.id == target.id or any(getattr(source,k) != getattr(target,k) for k in
                ('owner_org_id','custodian_person_id','location_id','material_id','lot_id'))
            or target.condition_code != {'restore_available':source.condition_code,'convert_used':'used','convert_damaged':'damaged'}[row.disposition]
            or custody.location_id != source.location_id or custody.custodian_person_id != source.custodian_person_id
            or not _aware(custody.valid_from) <= _aware(row.created_at)
            or (custody.valid_to is not None and _aware(custody.valid_to) <= _aware(row.created_at))
            or not _aware(review.created_at) <= _aware(row.created_at) <= _aware(tx.created_at)):
        invalid()
    serial_ids = tuple(sorted(db.scalars(select(Serial.serial_id).where(Serial.line_id == line.id)), key=str))
    cursor = tx.ledger_cursor - 1
    expected = dict(schema_version='1.0', intent=expected_intent, operation_id=str(order.id), line_id=str(line.id),
        executor_person_id=str(row.executor_person_id), authorization_version=row.authorization_version,
        disposition=row.disposition, reason=decision.reason, source_account_id=str(source.id), target_account_id=str(target.id),
        owner_org_id=str(source.owner_org_id), custodian_person_id=str(source.custodian_person_id), location_id=str(source.location_id),
        material_id=str(source.material_id), lot_id=str(source.lot_id) if source.lot_id else None,
        source_condition=source.condition_code, target_condition=target.condition_code,
        custody_assignment_id=str(custody.id), quantity=format(row.quantity,'.3f'), serial_ids=[str(s) for s in serial_ids],
        movement_type='unfreeze' if row.disposition=='restore_available' else 'status_change', ledger_cursor=cursor)
    if any(plan.get(k) != v for k,v in expected.items()):
        invalid()
    if set(plan) != set(expected) | {'source_balance_quantity','source_balance_version','policy_fingerprint','holds'}:
        invalid()
    if plan['holds'] != historical_hold_basis(db, source.id, cursor):
        invalid()
    balance = Decimal(0)
    transactions = set()
    for movement in db.scalars(select(InventoryMovement).join(InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id).where(InventoryTransaction.ledger_cursor <= cursor,
            (InventoryMovement.from_account_id == source.id) | (InventoryMovement.to_account_id == source.id))):
        balance += movement.quantity if movement.to_account_id == source.id else -movement.quantity
        transactions.add(movement.transaction_id)
    if plan['source_balance_quantity'] != format(balance,'.3f') or plan['source_balance_version'] != len(transactions):
        invalid()
    held = [item for item in plan['holds'] if item['disposition_id'] is None]
    if sum((Decimal(item['quantity']) for item in held), Decimal(0)) > balance:
        invalid()
    selected = next((item for item in held if item['line_id'] == str(line.id)), None)
    if selected is None or selected['serial_ids'] != plan['serial_ids'] or selected['quantity'] != plan['quantity']:
        invalid()
    held_serials = [UUID(s) for item in held for s in item['serial_ids']]
    if len(set(held_serials)) != len(held_serials):
        invalid()
    states = rebuild_serial_states(db, set(held_serials), through_cursor=cursor)
    if any(s not in states or states[s].stock_account_id != source.id or states[s].lifecycle_status != 'active' for s in held_serials):
        invalid()
    _, fingerprint = sources._policies(db, {source.material_id}, _aware(row.created_at))
    recorded = plan['policy_fingerprint']
    # A later policy may close the original period. Its original end, if any,
    # must remain exact; closing a formerly open period cannot change history.
    if len(recorded) != 1 or len(recorded[0]) != 7 or recorded[0][:6] != list(fingerprint[0][:6]):
        invalid()
    if recorded[0][6] is not None and (recorded[0][6] != fingerprint[0][6]
            or _aware(datetime.fromisoformat(recorded[0][6])) <= _aware(row.created_at)):
        invalid()
    command = posting_command(row)
    actor = SimpleNamespace(user_id=row.actor_user_id, person_id=row.executor_person_id, authorization_version=row.authorization_version)
    if (tx.status != 'posted' or tx.actor_user_id != row.actor_user_id or tx.reversed_transaction_id is not None
            or tx.idempotency_key_hash != row.idempotency_key_hash or _aware(tx.effective_at) != _aware(row.created_at)
            or any(getattr(tx,k) != getattr(command,k) for k in
                ('transaction_no','source_document_type','source_document_id','posting_key','movement_type'))
            or tx.request_hash != posting._posting_request_hash(actor, command)
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx.id).limit(1))):
        invalid()
    moves = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id)))
    if len(moves) != 1:
        invalid()
    move = moves[0]
    actual_serials = tuple(sorted(db.scalars(select(InventoryMovementSerial.serial_id).where(
        InventoryMovementSerial.movement_id == move.id)), key=str))
    if (move.id != row.posting_movement_id or move.line_no != 1 or move.from_account_id != source.id
            or move.to_account_id != target.id or move.quantity != row.quantity or move.external_boundary_code is not None
            or actual_serials != serial_ids):
        invalid()
    original.audit(db, actor=actor, stream='inventory', aggregate_type='inventory_transaction', identifier=tx.id,
        action='inventory.transaction.posted', request_id=posting._request_reference(row.request_id), before=None,
        after=dict(ledger_cursor=tx.ledger_cursor,movement_count=1,movement_type=tx.movement_type,
            posting_key=tx.posting_key,reversed_transaction_id=None,status='posted'))
    original.single(db, StateTransitionEvent, aggregate_type='inventory_transaction',aggregate_id=str(tx.id),from_status=None,
        to_status='posted',reason='inventory_transaction_posted',actor_id=row.actor_user_id,
        idempotency_key=posting._derived_evidence_key('state',tx.id,'posted'),
        metadata_jsonb=dict(ledger_cursor=tx.ledger_cursor,movement_type=tx.movement_type,request_reference=posting._request_reference(row.request_id)))
    original.single(db, OutboxEvent, aggregate_type='inventory_transaction',aggregate_id=str(tx.id),event_type='inventory.transaction.posted',
        idempotency_key=posting._derived_evidence_key('outbox',tx.id,'posted'),payload_jsonb=dict(transaction_id=str(tx.id),
            transaction_no=tx.transaction_no,movement_type=tx.movement_type,ledger_cursor=tx.ledger_cursor,reversed_transaction_id=None))
    # Count by business identity before matching the expected payload, so an
    # extra conflicting event cannot hide behind one correct matching event.
    for model in (OutboxEvent, StateTransitionEvent):
        original.single(db, model, aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
        original.single(db, model, aggregate_type=AGGREGATE, aggregate_id=str(row.id))
    original.single(db, NotificationEvent, business_type=AGGREGATE, business_id=str(row.id))
    body = payload(row)
    original.audit(db, actor=actor, stream='inventory', aggregate_type=AGGREGATE, identifier=row.id,
        action=KIND,request_id=row.request_id,before={},after=body)
    event = original.single(db, AuditEvent, stream_key='inventory',aggregate_type=AGGREGATE,aggregate_id=str(row.id))
    if _aware(event.created_at) != _aware(row.created_at) or _aware(event.occurred_at) != _aware(row.created_at):
        invalid()
    original.single(db, OutboxEvent, event_type=KIND,aggregate_type=AGGREGATE,aggregate_id=str(row.id),
        idempotency_key=KIND+':'+str(row.id),payload_jsonb=body)
    original.single(db, StateTransitionEvent, aggregate_type=AGGREGATE,aggregate_id=str(row.id),
        from_status='pending',to_status='posted',actor_id=row.actor_user_id,reason=KIND,
        idempotency_key=KIND+':'+str(row.id),metadata_jsonb=body)
    notification = original.single(db, NotificationEvent, event_type=KIND,business_type=AGGREGATE,business_id=str(row.id),
        dedup_key=KIND+':'+str(row.id),payload_jsonb=body)
    targets = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == notification.id)))
    if targets != (order.requester_id,) or notification.target_manifest_sha256 != target_manifest_hash(targets):
        invalid()
    return body


def verified(db, *, row, _proof_cache=None):
    # A shared cache is local to one current read/command, never persisted or
    # reused after commit. Iterate the historical graph to avoid Python stack
    # limits when many successive reports share an account.
    cache = {} if _proof_cache is None else _proof_cache
    pending = [row]
    try:
        while pending:
            fact = pending.pop()
            if fact.id in cache:
                continue
            cache[fact.id] = _verify(db, fact)
            if fact.disposition == 'scrap':
                # verify_chain already discovers and proves sibling original
                # roots and every successor. A scrap plan uses the v2 basis.
                continue
            for item in fact.plan_jsonb['holds']:
                if item['disposition_id'] is not None:
                    prior_id = UUID(item['disposition_id'])
                    if prior_id not in cache:
                        prior = db.get(Disposition, prior_id, populate_existing=True)
                        if prior is None:
                            invalid()
                        pending.append(prior)
        return cache[row.id]
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, AuditChainError, SerialLedgerError):
        invalid()
