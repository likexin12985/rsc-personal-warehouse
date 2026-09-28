"""Historical loss-return proof; no current grant is inferred from history."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.foundation_models import AuditEvent, NotificationEvent, NotificationPersonTarget, OutboxEvent, StateTransitionEvent
from app.inventory_models import CustodyAssignment, InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockAccount
from app.stock_operation_models import (StockLossDisposition as Disposition, StockLossHeadquartersDecision as Decision,
    StockLossHeadquartersReview as Review, StockOperationLine as Line, StockOperationOrder as Order,
    StockOperationSerial as Serial, StockOperationCancellation)
from app.stock_loss_return_schemas import StockLossReturnPreviewIn
from app.stock_return_schemas import StockReturnDestinationOut
from app.formal_services import inventory_posting as posting, stock_loss_facts as original, stock_loss_sources as sources
from app.formal_services import stock_loss_headquarters_reviews as headquarters
from app.formal_services.stock_loss_disposition_facts import historical_hold_basis
from app.formal_services.notification_events import target_manifest_hash
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware

AGGREGATE = 'stock_loss_disposition'
KIND = 'stock_loss.disposition_posted'
CHILD_KIND = 'stock_loss.return_derived'


def invalid():
    sources._fail('stock_loss_return_evidence_invalid', '派生退回与原报损批准、保管责任或流水不一致，请保留原请求核验', 503)


def child_line_id(decision_id, original_line_id):
    return uuid5(NAMESPACE_URL, f'rsc:stock-loss-return-line:v1:{decision_id}:{original_line_id}')


def posting_command(row):
    return posting.InventoryPostingCommand(transaction_no='INV-LOSS-RETURN-'+row.idempotency_key_hash[:20].upper(),
        movement_type='reserve', source_document_type='stock_operation_return', source_document_id=str(row.return_operation_id),
        posting_key=f'stock-loss:return_to_region:{row.return_operation_id}:{row.idempotency_key_hash}',
        effective_at=_aware(row.created_at), movements=(posting.InventoryMovementCommand(
            from_account_id=row.source_account_id, to_account_id=row.target_account_id, quantity=row.quantity,
            serial_ids=tuple(UUID(s) for s in row.plan_jsonb['serial_ids'])),))


def payload(row):
    return dict(disposition_id=str(row.id), operation_id=str(row.operation_id), line_id=str(row.line_id),
        headquarters_decision_id=str(row.headquarters_decision_id), disposition=row.disposition,
        executor_person_id=str(row.executor_person_id), authorization_version=row.authorization_version,
        posting_transaction_id=str(row.posting_transaction_id), posting_movement_id=str(row.posting_movement_id),
        quantity=format(row.quantity, '.3f'), source_account_id=str(row.source_account_id), target_account_id=str(row.target_account_id),
        request_id=row.request_id, request_hash=row.request_hash, plan_hash=row.plan_hash, status='posted',
        return_operation_id=str(row.return_operation_id), origin_kind='loss_report',
        stock_effect='frozen_to_return_pending', return_fulfillment_required=True)


def child_payload(row, child):
    return dict(operation_id=str(child.id), loss_disposition_id=str(row.id), loss_operation_id=str(row.operation_id),
        loss_line_id=str(row.line_id), headquarters_decision_id=str(row.headquarters_decision_id),
        requester_id=str(child.requester_id), executor_person_id=str(row.executor_person_id),
        posting_transaction_id=str(row.posting_transaction_id), request_hash=row.request_hash,
        plan_hash=row.plan_hash, origin_kind='loss_report', stock_effect='frozen_to_return_pending')


def _verify_child(db, *, row, child, order, line, decision, source, target, serial_ids):
    # Identifier allocation stays deterministic in the command. Historical
    # proof binds persisted unique HQ/child/line coordinates; it must not rerun
    # a replaceable identifier allocator to decide whether stock history exists.
    plan = row.plan_jsonb
    if (child is None or child.id != row.return_operation_id
            or child.operation_type != 'return' or child.status != 'submitted' or child.oam_work_order_id is not None
            or child.loss_headquarters_decision_id != decision.id or child.requester_id != order.requester_id
            or child.source_location_id != source.location_id or child.reason != decision.reason
            or child.operation_no != 'LOSS-RET-'+row.idempotency_key_hash[:24].upper()
            or any(getattr(child,k) != getattr(row,k) for k in (
                'actor_user_id','authorization_version','request_id','idempotency_key_hash','request_hash',
                'plan_hash','command_jsonb','plan_jsonb','posting_transaction_id'))
            or _aware(child.created_at) != _aware(row.created_at)
            or db.scalar(select(StockOperationCancellation.id).where(StockOperationCancellation.operation_id == child.id))):
        invalid()
    child_line = original.single(db, Line, operation_id=child.id)
    if (child_line.id == line.id or child_line.operation_id != child.id or child_line.line_no != 1
            or child_line.operation_type != 'return' or child_line.source_recovery_line_id is not None
            or child_line.source_loss_line_id != line.id or child_line.stock_account_id != source.id
            or child_line.reserved_account_id != target.id or child_line.material_id != source.material_id
            or child_line.quantity != row.quantity or child_line.target_condition != source.condition_code
            or child_line.reason != decision.reason or _aware(child_line.created_at) != _aware(row.created_at)):
        invalid()
    child_serials = tuple(db.scalars(select(Serial).where(Serial.line_id == child_line.id).order_by(Serial.serial_id)))
    if (tuple(s.serial_id for s in child_serials) != serial_ids
            or any(not s.sku_verified or not s.qr_verified or _aware(s.created_at) != _aware(row.created_at) for s in child_serials)):
        invalid()
    destination = StockReturnDestinationOut.model_validate(plan['destination'])
    receiver = db.get(CustodyAssignment, child.target_custody_assignment_id, populate_existing=True)
    if (destination.model_dump(mode='json') != plan['destination']
            or destination.source_location_id != source.location_id
            or destination.target_location_id != child.target_location_id
            or destination.transit_location_id != child.transit_location_id
            or destination.region_org_id != source.owner_org_id
            or destination.custody_assignment_id != child.target_custody_assignment_id
            or receiver is None or receiver.location_id != child.target_location_id
            or receiver.custodian_person_id != destination.custodian_person_id
            or _aware(receiver.valid_from) != _aware(destination.custody_effective_from)
            or _aware(receiver.valid_from) > _aware(row.created_at)
            or (receiver.valid_to is not None and _aware(receiver.valid_to) <= _aware(row.created_at))):
        invalid()
    actor = SimpleNamespace(user_id=row.actor_user_id)
    body = child_payload(row, child)
    original.audit(db, actor=actor, stream='material_request', aggregate_type='stock_operation_order',
        identifier=child.id, action=CHILD_KIND, request_id=row.request_id, before={}, after=body)
    audit = original.single(db, AuditEvent, stream_key='material_request', aggregate_type='stock_operation_order',
        aggregate_id=str(child.id))
    if _aware(audit.created_at) != _aware(row.created_at) or _aware(audit.occurred_at) != _aware(row.created_at):
        invalid()
    for model in (OutboxEvent, StateTransitionEvent):
        original.single(db, model, aggregate_type='stock_operation_order', aggregate_id=str(child.id))
    outbox = original.single(db, OutboxEvent, event_type=CHILD_KIND, aggregate_type='stock_operation_order',
        aggregate_id=str(child.id), idempotency_key=CHILD_KIND+':'+str(child.id), payload_jsonb=body)
    state = original.single(db, StateTransitionEvent, aggregate_type='stock_operation_order', aggregate_id=str(child.id),
        from_status=None, to_status='submitted', actor_id=row.actor_user_id, reason=CHILD_KIND,
        idempotency_key=CHILD_KIND+':'+str(child.id), metadata_jsonb=body)
    # Queue scheduling, attempts and delivery status are mutable operational
    # facts. Only immutable creation coordinates belong in the stock proof.
    if (any(_aware(event.created_at) != _aware(row.created_at) for event in (outbox, state))
            or _aware(state.occurred_at) != _aware(row.created_at)):
        invalid()


def _verify(db, row):
    line = db.get(Line, row.line_id, populate_existing=True)
    order = db.get(Order, row.operation_id, populate_existing=True)
    decision = db.get(Decision, row.headquarters_decision_id, populate_existing=True)
    review = db.get(Review, decision.review_id, populate_existing=True) if decision else None
    child = db.get(Order, row.return_operation_id, populate_existing=True) if row.return_operation_id else None
    if line is None or order is None or decision is None or review is None or child is None:
        invalid()
    original.submission_evidence(db, order=order)
    headquarters.verified(db, row=review, order=order)
    source = db.get(StockAccount, row.source_account_id, populate_existing=True)
    target = db.get(StockAccount, row.target_account_id, populate_existing=True)
    custody = db.get(CustodyAssignment, row.custody_assignment_id, populate_existing=True)
    tx = db.get(InventoryTransaction, row.posting_transaction_id, populate_existing=True)
    plan = row.plan_jsonb
    expected_intent = StockLossReturnPreviewIn(headquarters_decision_id=decision.id,
        expected_headquarters_review_hash=review.request_hash, expected_submission_plan_hash=order.plan_hash,
        target_location_id=child.target_location_id, transit_location_id=child.transit_location_id).model_dump(mode='json')
    expected_command = dict(intent=expected_intent, request_id=row.request_id, expected_plan_hash=row.plan_hash)
    if (source is None or target is None or custody is None or tx is None
            or row.disposition != 'return_to_region' or decision.disposition != row.disposition
            or decision.line_id != line.id or line.operation_id != order.id or review.operation_id != order.id
            or line.operation_type != 'loss_report' or line.reserved_account_id != source.id
            or source.custodian_person_id != order.requester_id or source.location_id != order.source_location_id
            or row.quantity != line.quantity or row.quantity <= 0 or row.authorization_version < 1
            or not re.fullmatch(r'[0-9a-f]{64}', row.idempotency_key_hash)
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', row.request_id)
            or row.command_jsonb != expected_command or sources._hash(expected_command) != row.request_hash
            or sources._hash(plan) != row.plan_hash
            or source.availability_bucket != 'frozen' or target.availability_bucket != 'return_pending'
            or source.id == target.id or any(getattr(source,k) != getattr(target,k) for k in
                ('owner_org_id','custodian_person_id','location_id','material_id','lot_id','condition_code'))
            or custody.location_id != source.location_id or custody.custodian_person_id != source.custodian_person_id
            or _aware(custody.valid_from) > _aware(row.created_at)
            or (custody.valid_to is not None and _aware(custody.valid_to) <= _aware(row.created_at))
            or not _aware(review.created_at) <= _aware(row.created_at) <= _aware(tx.created_at)):
        invalid()
    serial_ids = tuple(sorted(db.scalars(select(Serial.serial_id).where(Serial.line_id == line.id)), key=str))
    _verify_child(db, row=row, child=child, order=order, line=line, decision=decision,
        source=source, target=target, serial_ids=serial_ids)
    cursor = tx.ledger_cursor - 1
    expected = dict(schema_version='1.0', origin_kind='loss_report', intent=expected_intent,
        loss_operation_id=str(order.id), loss_line_id=str(line.id), headquarters_decision_id=str(decision.id),
        derived_return_operation_id=str(child.id), executor_person_id=str(row.executor_person_id),
        requester_id=str(order.requester_id), authorization_version=row.authorization_version, reason=decision.reason,
        source_account_id=str(source.id), pending_account_id=str(target.id), owner_org_id=str(source.owner_org_id),
        custodian_person_id=str(source.custodian_person_id), location_id=str(source.location_id),
        material_id=str(source.material_id), lot_id=str(source.lot_id) if source.lot_id else None,
        condition_code=source.condition_code, source_custody_assignment_id=str(custody.id),
        quantity=format(row.quantity,'.3f'), serial_ids=[str(s) for s in serial_ids],
        destination=plan['destination'], movement_type='reserve', source_document_type='stock_operation_return',
        lifecycle_after='active', return_fulfillment_required=True, ledger_cursor=cursor)
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
