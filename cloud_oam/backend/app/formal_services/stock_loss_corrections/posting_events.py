"""Read-only posting evidence for persisted loss execution and inverse facts.

This composes with the ledger-edge proof, not with a client-provided payload.
It does not authorize a write or establish downstream compensation. Posting
events and domain notification intent remain independent evidence bundles.
"""
from types import SimpleNamespace

from sqlalchemy import select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.stock_operation_models import StockLossDisposition
from app.formal_services import inventory_posting as posting, stock_loss_facts as original
from app.formal_services import stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .correction_models import StockLossDispositionReversal, StockLossCorrectionExecution
from .chain_projection import InvalidChain


def _need(condition):
    if not condition:
        raise InvalidChain('loss_posting_event_evidence_invalid')


def verify(db, *, fact):
    """Require one complete, exact posting bundle; never generate missing rows."""
    _need(type(fact) in (StockLossDisposition, StockLossDispositionReversal, StockLossCorrectionExecution))
    with db.no_autoflush:
        fact = db.get(type(fact), fact.id, populate_existing=True)
        _need(fact is not None)
        tx = db.get(InventoryTransaction, fact.posting_transaction_id, populate_existing=True)
        _need(tx is not None and tx.status == 'posted' and tx.actor_user_id == fact.actor_user_id
            and tx.idempotency_key_hash == fact.idempotency_key_hash
            and _aware(tx.effective_at) == _aware(fact.created_at)
            and _aware(tx.created_at) == _aware(tx.posted_at)
            and _aware(fact.created_at) <= _aware(tx.posted_at))
        inverse = type(fact) is StockLossDispositionReversal
        reverse_id = fact.original_transaction_id if inverse else None
        _need(tx.reversed_transaction_id == reverse_id
            and (tx.movement_type == 'reversal') == inverse)
        suffix = 'reversed' if inverse else 'posted'
        kind = 'inventory.transaction.' + suffix
        ref = posting._request_reference(fact.request_id)
        identity = dict(aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
        # Count by identity before matching expected values. Otherwise a good
        # record can hide a second conflicting action, stream or payload.
        audit = original.single(db, AuditEvent, **identity)
        transition = original.single(db, StateTransitionEvent, **identity)
        outbox = original.single(db, OutboxEvent, **identity)
        audit_body=dict(ledger_cursor=tx.ledger_cursor,movement_count=1,movement_type=tx.movement_type,
            posting_key=tx.posting_key,reversed_transaction_id=str(reverse_id) if inverse else None,status='posted')
        state_body=dict(ledger_cursor=tx.ledger_cursor,movement_type=tx.movement_type,request_reference=ref)
        outbox_body=dict(transaction_id=str(tx.id),transaction_no=tx.transaction_no,
            movement_type=tx.movement_type,ledger_cursor=tx.ledger_cursor,
            reversed_transaction_id=str(reverse_id) if inverse else None)
        original.audit(db, actor=SimpleNamespace(user_id=fact.actor_user_id), stream='inventory',
            aggregate_type='inventory_transaction', identifier=tx.id, action=kind,
            request_id=ref, before=None, after=audit_body)
        _need(transition.from_status is None and transition.to_status == 'posted'
            and transition.actor_id == fact.actor_user_id
            and transition.reason == 'inventory_transaction_' + suffix
            and transition.idempotency_key == posting._derived_evidence_key('state', tx.id, suffix)
            and transition.metadata_jsonb == state_body)
        _need(outbox.event_type == kind
            and outbox.idempotency_key == posting._derived_evidence_key('outbox', tx.id, suffix)
            and outbox.payload_jsonb == outbox_body)
        _need(all(sources._hash(actual)==sources._hash(expected) for actual,expected in (
            (audit.after_jsonb,audit_body),(transition.metadata_jsonb,state_body),(outbox.payload_jsonb,outbox_body))))
        # Delivery attempts/status and mutable retry times are intentionally
        # excluded. Original creation/occurrence must agree with the posting.
        _need(all(_aware(event.created_at) == _aware(tx.posted_at)
            for event in (audit, transition, outbox))
            and all(_aware(event.occurred_at) == _aware(tx.posted_at) for event in (audit, transition)))
        movements = tuple(db.scalars(select(InventoryMovement).where(
            InventoryMovement.transaction_id == tx.id).execution_options(populate_existing=True)))
        _need(len(movements) == 1 and movements[0].id == fact.posting_movement_id
            and _aware(movements[0].created_at) == _aware(tx.posted_at))
        serials = tuple(db.scalars(select(InventoryMovementSerial).where(
            InventoryMovementSerial.movement_id == fact.posting_movement_id)
            .execution_options(populate_existing=True)))
        _need(all(s.transaction_id == tx.id and _aware(s.created_at) == _aware(tx.posted_at) for s in serials))
