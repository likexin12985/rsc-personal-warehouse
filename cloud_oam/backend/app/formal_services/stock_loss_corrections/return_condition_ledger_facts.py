"""Historical condition posting evidence; never a read or replay permission.

The composing reader must establish current scope, load the complete condition
chain and independently prove its original inbound source. This component
proves the supplied event's actual immutable posting and effects. It deliberately
ignores mutable balances and delivery status: neither describes original success.
"""
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.formal_services import inventory_posting as posting
from app.formal_services import stock_return_facts as facts
from app.formal_services.work_order_query import _aware
from .return_condition_contracts import Posting
from .return_condition_identity import inventory_identity


def invalid():
    posting._fail('return_condition_ledger_history_invalid', 'service_unavailable',
                  '纠正事件的原库存流水或审计证据不完整，不能确认结果或重放请求')


def _rows(db, model, predicate, limit):
    return tuple(db.scalars(select(model).where(predicate).limit(limit)
                           .execution_options(populate_existing=True)))


def _effect(db, model, transaction_id, key):
    # Follow both the business reference and unique key. A forged reference
    # must not hide a contradictory effect from the historical reader.
    rows = _rows(db, model, or_(and_(model.aggregate_type == 'inventory_transaction',
        model.aggregate_id == str(transaction_id)), model.idempotency_key == key), 2)
    if len(rows) != 1:
        invalid()
    row = rows[0]
    if (row.aggregate_type != 'inventory_transaction' or row.aggregate_id != str(transaction_id)
            or row.idempotency_key != key):
        invalid()
    return row


def verify_event_posting(db, *, event, serial_ids):
    """Return an exact historical posting, or None for a non-posting action.

    Call with independently loaded event and case serial facts. This does not
    validate case state, approval, account dimensions, source history or scope.
    All queries are non-writing and refresh ORM facts from the database.
    """
    if type(event['id']) is not UUID or not event['id'].int:
        raise ValueError('exact event UUID required')
    serials = tuple(sorted(serial_ids, key=str))
    if len(set(serials)) != len(serials) or any(type(s) is not UUID or not s.int for s in serials):
        invalid()
    with db.no_autoflush:
        owned = and_(InventoryTransaction.source_document_type == 'stock_condition_event',
                     InventoryTransaction.source_document_id == str(event['id']))
        if event['kind'] not in ('submit', 'execute', 'release'):
            from app.return_condition_schema import TRANSITIONS
            if (event['kind'] not in {t[0] for t in TRANSITIONS}
                    or any(event[k] is not None for k in ('posting_transaction_id',
                        'posting_movement_id', 'movement_type', 'from_account_id', 'to_account_id'))
                    or _rows(db, InventoryTransaction, owned, 1)):
                invalid()
            return None
        if any(event[k] is None for k in ('posting_transaction_id', 'posting_movement_id',
                                         'movement_type', 'from_account_id', 'to_account_id')):
            invalid()
        normalized = dict(event, created_at=_aware(event['created_at']))
        command, key_hash, request_hash = inventory_identity(normalized, serials)
        at = normalized['created_at']
        rows = _rows(db, InventoryTransaction, or_(owned,
            InventoryTransaction.id == event['posting_transaction_id'],
            InventoryTransaction.posting_key == command.posting_key,
            InventoryTransaction.idempotency_key_hash == key_hash,
            InventoryTransaction.reversed_transaction_id == event['posting_transaction_id']), 2)
        if len(rows) != 1:
            invalid()
        tx = rows[0]
        expected = dict(id=event['posting_transaction_id'], transaction_no=command.transaction_no,
            movement_type=command.movement_type, source_document_type=command.source_document_type,
            source_document_id=command.source_document_id, posting_key=command.posting_key,
            idempotency_key_hash=key_hash, request_hash=request_hash, status='posted',
            reversed_transaction_id=None, actor_user_id=event['actor_user_id'])
        if (any(getattr(tx, k) != v for k, v in expected.items()) or tx.ledger_cursor <= 0
                or any(_aware(getattr(tx, k)) != at for k in ('effective_at', 'posted_at', 'created_at'))):
            invalid()
        moves = _rows(db, InventoryMovement, or_(InventoryMovement.transaction_id == tx.id,
                            InventoryMovement.id == event['posting_movement_id']), 2)
        if len(moves) != 1:
            invalid()
        move = moves[0]
        expected = dict(id=event['posting_movement_id'], transaction_id=tx.id, line_no=1,
            from_account_id=event['from_account_id'], to_account_id=event['to_account_id'],
            quantity=event['quantity'], external_boundary_code=None)
        if any(getattr(move, k) != v for k, v in expected.items()) or _aware(move.created_at) != at:
            invalid()
        bindings = _rows(db, InventoryMovementSerial, or_(InventoryMovementSerial.transaction_id == tx.id,
            InventoryMovementSerial.movement_id == move.id), len(serials) + 1)
        if (tuple(sorted((b.serial_id for b in bindings), key=str)) != serials
                or any(b.transaction_id != tx.id or b.movement_id != move.id
                       or _aware(b.created_at) != at for b in bindings)):
            invalid()
        reference = posting._request_reference(event['request_id'])
        audit = facts.single(db, AuditEvent, stream_key='inventory',
                            aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
        facts.audit(db, actor=SimpleNamespace(user_id=event['actor_user_id']), stream='inventory',
            aggregate_type='inventory_transaction', identifier=tx.id, action='inventory.transaction.posted',
            request_id=reference, before=None, after=dict(ledger_cursor=tx.ledger_cursor,
                movement_count=1, movement_type=event['movement_type'], posting_key=command.posting_key,
                reversed_transaction_id=None, status='posted'))
        transition = _effect(db, StateTransitionEvent, tx.id, posting._derived_evidence_key('state', tx.id, 'posted'))
        outbox = _effect(db, OutboxEvent, tx.id, posting._derived_evidence_key('outbox', tx.id, 'posted'))
        if (transition.from_status is not None or transition.to_status != 'posted'
                or transition.reason != 'inventory_transaction_posted'
                or transition.actor_id != event['actor_user_id']
                or transition.metadata_jsonb != dict(ledger_cursor=tx.ledger_cursor,
                    movement_type=event['movement_type'], request_reference=reference)
                or outbox.event_type != 'inventory.transaction.posted'
                or outbox.payload_jsonb != dict(transaction_id=str(tx.id), transaction_no=tx.transaction_no,
                    movement_type=event['movement_type'], ledger_cursor=tx.ledger_cursor,
                    reversed_transaction_id=None)
                or any(_aware(row.created_at) != at for row in (audit, transition, outbox))
                or any(_aware(row.occurred_at) != at for row in (audit, transition))):
            invalid()
        return Posting(tx.id, move.id, tx.ledger_cursor, move.from_account_id,
                       move.to_account_id, move.quantity, serials, tx.movement_type)
