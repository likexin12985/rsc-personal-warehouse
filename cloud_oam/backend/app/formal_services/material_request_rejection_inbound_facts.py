"""Historical warehouse posting verification without today's stock projections."""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import case, func, or_, select

from app.foundation_models import AuditEvent, OutboxEvent, NotificationEvent, NotificationPersonTarget, StateTransitionEvent
from app.inventory_models import CustodyAssignment, InventoryTransaction, InventoryMovement, InventoryMovementSerial, StockAccount
from app.material_request_rejection_inbound_schema import parts, serials
from app.material_request_rejection_inbound_schemas import RejectionInboundIn, RejectionInboundOut
from . import material_request_rejection_inbound_plan as plans
from .audit_chain import verify_audit_event_in_read_snapshot, AuditChainError
from .notification_events import target_manifest_hash

acceptance = plans.acceptance
posting = acceptance.posting
ACTION = 'material_request.rejection_return.inbound'
AGGREGATE = plans.SOURCE


def digest(actor, return_id, receipt_id, payload):
    return acceptance.lifecycle._canonical_hash(dict(return_id=str(return_id), receipt_id=str(receipt_id),
        actor_user_id=actor.user_id, actor_person_id=str(actor.person_id), input=payload.model_dump(mode='json')))


def body(row):
    return {k: str(row[k]) for k in ('id', 'receipt_id', 'return_id', 'request_id', 'source_account_id',
        'target_location_id', 'custody_assignment_id', 'actor_person_id', 'actor_role_assignment_id', 'posting_transaction_id')} | {
        k: row[k] for k in ('request_version', 'authorization_version', 'request_hash', 'plan_hash', 'idempotency_key_hash')} | {
        k: format(row[k], '.3f') for k in ('accepted_qty', 'damaged_qty')}


def invalid():
    plans.fail('history_invalid', 'service_unavailable', '原仓库入账的验收、份额、库存流水、通知或审计证据不一致')


def single(db, model, **fields):
    values = tuple(db.scalars(select(model).filter_by(**fields).limit(2).execution_options(populate_existing=True)))
    if len(values) != 1:
        invalid()
    return values[0]


def audit(db, *, aggregate, identifier, stream, action, actor_id, trace, before, after):
    row = single(db, AuditEvent, stream_key=stream, aggregate_type=aggregate, aggregate_id=str(identifier))
    if (row.actor_user_id != actor_id or row.action != action or row.request_id != trace
            or row.before_jsonb != before or row.after_jsonb != after):
        invalid()
    verify_audit_event_in_read_snapshot(db, stream_key=stream, event_id=row.id)
    return row


def inventory(db, row, plan, actor):
    tx = db.get(InventoryTransaction, row['posting_transaction_id'], populate_existing=True)
    command = plans.command(plan, inbound_id=row['id'], at=acceptance._time(row['recorded_at']))
    if (tx is None or tx.status != 'posted' or tx.reversed_transaction_id is not None or tx.actor_user_id != row['actor_user_id']
            or tx.request_hash != posting._posting_request_hash(actor, command)
            or tx.idempotency_key_hash != posting._storage_hash(row['idempotency_key_hash'])
            or acceptance._time(tx.effective_at) != command.effective_at or acceptance._time(tx.posted_at) < command.effective_at
            or any(getattr(tx, key) != getattr(command, key) for key in
                ('transaction_no', 'movement_type', 'source_document_type', 'source_document_id', 'posting_key'))
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx.id).limit(1))):
        invalid()
    actual = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id)
        .order_by(InventoryMovement.line_no).limit(3)))
    if len(actual) != len(command.movements):
        invalid()
    previous_positions = []
    for number, (move, wanted) in enumerate(zip(actual, command.movements), 1):
        sn = tuple(db.scalars(select(InventoryMovementSerial).where(InventoryMovementSerial.movement_id == move.id)
            .order_by(InventoryMovementSerial.serial_id)))
        if (move.line_no != number or move.from_account_id != wanted.from_account_id or move.to_account_id != wanted.to_account_id
                or move.quantity != wanted.quantity or move.external_boundary_code is not None
                or tuple(s.serial_id for s in sn) != wanted.serial_ids or any(s.transaction_id != tx.id for s in sn)):
            invalid()
        for identifier in wanted.serial_ids:
            previous = db.execute(select(InventoryMovement.id, InventoryMovement.to_account_id).join(InventoryTransaction,
                InventoryTransaction.id == InventoryMovement.transaction_id).join(InventoryMovementSerial,
                InventoryMovementSerial.movement_id == InventoryMovement.id).where(InventoryTransaction.status == 'posted',
                InventoryTransaction.ledger_cursor < tx.ledger_cursor, InventoryMovementSerial.serial_id == identifier)
                .order_by(InventoryTransaction.ledger_cursor.desc(), InventoryMovement.line_no.desc()).limit(1)).one_or_none()
            if previous is None or previous.to_account_id != wanted.from_account_id:
                invalid()
            previous_positions.append(dict(serial_id=str(identifier), last_movement_id=str(previous.id)))
    if plan['serial_positions'] != sorted(previous_positions, key=lambda p: p['serial_id']):
        invalid()
    # Prove the original preview against the original ledger cursor. Today's
    # balances or later valid movements must not affect command recovery.
    balances = [(row['source_account_id'], plan['source_balance'])] + [
        (UUID(p['target_account_id']), p['target_balance']) for p in plan['parts']]
    for identifier, expected in balances:
        quantity, cursor, version = db.execute(select(func.coalesce(func.sum(case(
            (InventoryMovement.to_account_id == identifier, InventoryMovement.quantity), else_=-InventoryMovement.quantity)), 0),
            func.coalesce(func.max(InventoryTransaction.ledger_cursor), 0), func.count(func.distinct(InventoryTransaction.id)))
            .join(InventoryTransaction, InventoryTransaction.id == InventoryMovement.transaction_id)
            .where(InventoryTransaction.status == 'posted', InventoryTransaction.ledger_cursor < tx.ledger_cursor,
                or_(InventoryMovement.from_account_id == identifier, InventoryMovement.to_account_id == identifier))).one()
        expected = expected if expected is not None else dict(quantity='0.000', ledger_cursor=0, version=0)
        if expected != dict(quantity=format(quantity, '.3f'), ledger_cursor=cursor, version=version):
            invalid()
        if identifier == row['source_account_id'] and quantity < row['accepted_qty']:
            invalid()
    event = 'inventory.transaction.posted'
    audit(db, aggregate='inventory_transaction', identifier=tx.id, stream='inventory', action=event,
        actor_id=actor.user_id, trace=posting._request_reference(row['trace_request_id']), before=None,
        after=dict(ledger_cursor=tx.ledger_cursor, movement_count=len(command.movements), movement_type='transfer',
            posting_key=tx.posting_key, reversed_transaction_id=None, status='posted'))
    outbox = single(db, OutboxEvent, aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
    expected = dict(transaction_id=str(tx.id), transaction_no=tx.transaction_no, movement_type='transfer',
        ledger_cursor=tx.ledger_cursor, reversed_transaction_id=None)
    if (outbox.event_type != event or outbox.payload_jsonb != expected
            or outbox.idempotency_key != posting._derived_evidence_key('outbox', tx.id, 'posted')):
        invalid()
    state = single(db, StateTransitionEvent, aggregate_type='inventory_transaction', aggregate_id=str(tx.id))
    if (state.from_status is not None or state.to_status != 'posted' or state.actor_id != actor.user_id
            or state.reason != 'inventory_transaction_posted'
            or state.idempotency_key != posting._derived_evidence_key('state', tx.id, 'posted')
            or state.metadata_jsonb != dict(ledger_cursor=tx.ledger_cursor, movement_type='transfer',
                request_reference=posting._request_reference(row['trace_request_id']))):
        invalid()


def verify(db, context, row, *, replayed=True):
    try:
        original, checked = plans.receipt(db, context, row['receipt_id'])
        plan = row['plan_jsonb']
        actor = SimpleNamespace(user_id=row['actor_user_id'], person_id=row['actor_person_id'],
            authorization_version=row['authorization_version'])
        expected_fields = dict(schema=plans.SCHEMA, receipt_id=str(row['receipt_id']), receipt_request_hash=original['request_hash'],
            receipt_evidence_sha256=original['evidence_sha256'], return_id=str(context[1]['id']), request_id=str(context[5].id),
            request_version=row['request_version'], actor_user_id=row['actor_user_id'], actor_person_id=str(row['actor_person_id']),
            actor_role_assignment_id=str(row['actor_role_assignment_id']), authorization_version=row['authorization_version'],
            target_location_id=str(context[3].id), custody_assignment_id=str(row['custody_assignment_id']),
            source_account_id=str(context[1]['in_transit_account_id']))
        if (set(plan) != set(expected_fields) | {'source_dimensions', 'source_balance', 'serial_positions', 'parts', 'notification_person_ids'}
                or any(plan.get(k) != v for k, v in expected_fields.items())
                or row['request_id'] != context[5].id or row['return_id'] != context[1]['id']
                or row['target_location_id'] != context[3].id or row['source_account_id'] != context[1]['in_transit_account_id']
                or not original['request_version'] <= row['request_version'] <= context[5].version
                or acceptance._time(row['recorded_at']) < acceptance._time(original['recorded_at'])
                or row['accepted_qty'] != checked.amounts.accepted_qty or row['damaged_qty'] != checked.amounts.damaged_qty
                or acceptance.lifecycle._canonical_hash(plan) != row['plan_hash']):
            invalid()
        payload = RejectionInboundIn(expected_request_version=row['request_version'], reason=row['reason'],
            receipt_request_hash=original['request_hash'], expected_plan_hash=row['plan_hash'])
        if digest(actor, row['return_id'], row['receipt_id'], payload) != row['request_hash']:
            invalid()
        custody = db.get(CustodyAssignment, row['custody_assignment_id'], populate_existing=True)
        if (custody is None or custody.location_id != row['target_location_id'] or custody.custodian_person_id != actor.person_id
                or acceptance._time(custody.valid_from) > acceptance._time(row['recorded_at'])
                or custody.valid_to is not None and acceptance._time(custody.valid_to) <= acceptance._time(row['recorded_at'])):
            invalid()
        source = db.get(StockAccount, row['source_account_id'], populate_existing=True)
        if source is None or plans.dimensions(source) != plan['source_dimensions']:
            invalid()
        wanted = plans.partitions(original, checked)
        if len(plan['parts']) != len(wanted):
            invalid()
        expected_parts, expected_serials = [], []
        for part, piece in zip(wanted, plan['parts']):
            if (set(piece) != {'condition_code', 'quantity', 'target_account_id', 'target_dimensions', 'target_balance', 'serial_ids'}
                    or piece['condition_code'] != part.condition_code or piece['quantity'] != format(part.quantity, '.3f')
                    or piece['serial_ids'] != [str(s) for s in part.serial_ids]):
                invalid()
            target = db.get(StockAccount, UUID(piece['target_account_id']), populate_existing=True)
            dims = dict(plan['source_dimensions'], location_id=str(row['target_location_id']),
                custodian_person_id=str(actor.person_id), condition_code=part.condition_code, availability_bucket='available')
            if target is None or piece['target_dimensions'] != dims or plans.dimensions(target) != dims:
                invalid()
            expected_parts.append(dict(inbound_id=row['id'], condition_code=part.condition_code,
                target_account_id=target.id, quantity=part.quantity))
            expected_serials.extend(dict(inbound_id=row['id'], serial_id=s, condition_code=part.condition_code) for s in part.serial_ids)
        actual_parts = [dict(r) for r in db.execute(select(parts).where(parts.c.inbound_id == row['id']).order_by(parts.c.condition_code)).mappings()]
        actual_serials = [dict(r) for r in db.execute(select(serials).where(serials.c.inbound_id == row['id']).order_by(serials.c.serial_id)).mappings()]
        if (actual_parts != sorted(expected_parts, key=lambda x: x['condition_code'])
                or actual_serials != sorted(expected_serials, key=lambda x: str(x['serial_id']))):
            invalid()
        inventory(db, row, plan, actor)
        doc = body(row)
        event = audit(db, aggregate=AGGREGATE, identifier=row['id'], stream='material_request', action=ACTION,
            actor_id=actor.user_id, trace=row['trace_request_id'], before={'warehouse_inbound': 'not_posted'}, after=doc)
        if acceptance._time(event.occurred_at) != acceptance._time(row['recorded_at']):
            invalid()
        outbox = single(db, OutboxEvent, aggregate_type=AGGREGATE, aggregate_id=str(row['id']))
        if outbox.event_type != ACTION or outbox.payload_jsonb != doc or outbox.idempotency_key != 'rejection-inbound:' + str(row['id']):
            invalid()
        people = tuple(UUID(s) for s in sorted({str(p) for p in
            (context[5].requester_person_id, source.custodian_person_id, actor.person_id) if p is not None}))
        if plan['notification_person_ids'] != [str(p) for p in people]:
            invalid()
        notification = single(db, NotificationEvent, business_type=AGGREGATE, business_id=str(row['id']))
        targets = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(
            NotificationPersonTarget.event_id == notification.id).order_by(NotificationPersonTarget.person_id)))
        if (notification.event_type != ACTION or notification.payload_jsonb != doc
                or notification.dedup_key != 'rejection-inbound-notification:' + str(row['id'])
                or notification.target_manifest_sha256 != target_manifest_hash(people) or targets != people):
            invalid()
        return RejectionInboundOut(inbound_id=row['id'], receipt_id=row['receipt_id'], return_id=row['return_id'],
            request_id=row['request_id'], inventory_transaction_id=row['posting_transaction_id'],
            target_location_id=row['target_location_id'], request_hash=row['request_hash'], plan_hash=row['plan_hash'], replayed=replayed)
    except (ValueError, TypeError, KeyError, AttributeError, AuditChainError):
        invalid()
