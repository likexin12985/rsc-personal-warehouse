"""Read-only exact warehouse destination and accepted condition partitions."""
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select

from app.inventory_models import StockAccount, StockBalance, InventorySerial, SerialCurrentPosition
from app.material_request_rejection_receipt_schema import receipts
from app.material_request_rejection_inbound_schema import inbounds
from . import material_request_rejection_receipt as acceptance
from .stock_return_inbound_quality import accepted_parts
from .stock_return_inbound_accounts import resolve_target
from .inventory_posting import InventoryMovementCommand, InventoryPostingCommand

SCHEMA = 'rsc.material_request_rejection_inbound.v1'
SOURCE = 'material_request_rejection_inbound'


def fail(code, category, message):
    acceptance.registration._fail('warehouse_inbound_' + code, category, message)


def receipt(db, context, receipt_id):
    row = db.execute(select(receipts).where(receipts.c.id == receipt_id,
        receipts.c.return_id == context[1]['id'])).mappings().one_or_none()
    if row is None:
        fail('not_found', 'not_found', '当前退回没有此仓库验收')
    checked = acceptance._verify(db, context, row)
    if checked.amounts.accepted_qty <= 0:
        fail('not_accepted', 'precondition_failed', '短少或拒收观察没有可入账实物')
    return row, checked


def partitions(row, checked):
    return accepted_parts(source_condition=row['evidence_jsonb']['origin']['source_condition'],
        accepted_quantity=checked.amounts.accepted_qty, damaged_quantity=checked.amounts.damaged_qty,
        accepted_serial_ids=tuple(p.serial_id for p in checked.amounts.accepted_serial_verifications),
        damaged_serial_ids=checked.amounts.damaged_serial_ids,
        tracked=row['evidence_jsonb']['origin']['tracking_mode'] in ('serial', 'lot_and_serial'))


def balance(db, identifier):
    row = db.get(StockBalance, identifier, populate_existing=True)
    return None if row is None else dict(quantity=format(row.quantity, '.3f'),
        version=row.version, ledger_cursor=row.ledger_cursor)


def dimensions(account):
    return {key: str(value) if value is not None else None for key in
        ('owner_org_id', 'custodian_person_id', 'location_id', 'material_id', 'condition_code', 'availability_bucket', 'lot_id')
        for value in (getattr(account, key),)}


def build(db, context, receipt_id):
    actor, parent, source, location, custody, request, stamp = context
    grant = acceptance._authority(db, context)
    row, checked = receipt(db, context, receipt_id)
    acceptance._history(db, context)
    if db.scalar(select(inbounds.c.id).where(inbounds.c.receipt_id == receipt_id)):
        fail('already_posted', 'conflict', '本次验收已独立入账，请回读原请求')
    if request.status not in ('approved', 'partially_approved') or db.scalar(select(
            acceptance.registration.closures.c.id).where(acceptance.registration.closures.c.request_id == request.id)):
        fail('closed', 'precondition_failed', '需求已关闭或不能新增仓库入账')
    transit = db.get(StockAccount, parent['in_transit_account_id'], populate_existing=True)
    if (transit is None or transit.availability_bucket != 'in_transit'
            or any(getattr(transit, k) != getattr(source, k) for k in ('owner_org_id', 'material_id', 'condition_code', 'lot_id'))):
        fail('source_changed', 'precondition_failed', '原在途账户与拒收来源不匹配')
    source_balance = balance(db, transit.id)
    if source_balance is None or Decimal(source_balance['quantity']) < checked.amounts.accepted_qty:
        fail('insufficient', 'conflict', '原在途库存不足，不能重复入账')
    pieces, positions = [], []
    for part in partitions(row, checked):
        target = resolve_target(db, source=transit, location=location, person_id=actor.person_id, condition_code=part.condition_code)
        pieces.append(dict(condition_code=part.condition_code, quantity=format(part.quantity, '.3f'),
            target_account_id=str(target.id), target_dimensions=dimensions(target),
            target_balance=balance(db, target.id), serial_ids=[str(s) for s in part.serial_ids]))
        for identifier in part.serial_ids:
            serial = db.get(InventorySerial, identifier, populate_existing=True)
            position = db.get(SerialCurrentPosition, identifier, populate_existing=True)
            if serial is None or serial.lifecycle_status != 'active' or position is None or position.stock_account_id != transit.id:
                fail('serial_changed', 'conflict', '已接受SN不在原在途账户')
            positions.append(dict(serial_id=str(identifier), last_movement_id=str(position.last_movement_id)))
    return dict(schema=SCHEMA, receipt_id=str(receipt_id), receipt_request_hash=row['request_hash'],
        receipt_evidence_sha256=row['evidence_sha256'], return_id=str(parent['id']), request_id=str(request.id),
        request_version=request.version, actor_user_id=actor.user_id, actor_person_id=str(actor.person_id),
        actor_role_assignment_id=str(grant.assignment_id), authorization_version=actor.authorization_version,
        target_location_id=str(location.id), custody_assignment_id=str(custody.id),
        source_account_id=str(transit.id), source_dimensions=dimensions(transit), source_balance=source_balance,
        serial_positions=sorted(positions, key=lambda x: x['serial_id']), parts=pieces,
        notification_person_ids=sorted({str(p) for p in (request.requester_person_id, transit.custodian_person_id, actor.person_id) if p is not None}))


def preview(db, *, actor, return_id, receipt_id):
    with db.no_autoflush:
        context = acceptance._context(db, actor, return_id)
        result = build(db, context, receipt_id)
        final = acceptance._context(db, actor, return_id)
        if final[-1] != context[-1] or build(db, final, receipt_id) != result:
            fail('changed', 'conflict', '核验期间权限、验收或库存已变化，请重新查询')
        return dict(plan=result, plan_hash=acceptance.lifecycle._canonical_hash(result))


def command(plan, *, inbound_id, at):
    return InventoryPostingCommand(transaction_no='INV-REJECT-IN-' + inbound_id.hex[:20].upper(),
        movement_type='transfer', source_document_type=SOURCE, source_document_id=str(inbound_id),
        posting_key='rejection-inbound:' + plan['receipt_id'], effective_at=at,
        movements=tuple(InventoryMovementCommand(from_account_id=UUID(plan['source_account_id']),
            to_account_id=UUID(part['target_account_id']), quantity=Decimal(part['quantity']),
            serial_ids=tuple(UUID(s) for s in part['serial_ids'])) for part in plan['parts']))
