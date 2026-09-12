"""Find original own acceptances after restart; never create accounts or post."""
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select

from ..demand_models import MaterialRequest, MaterialRequestCommand
from ..foundation_models import AuditEvent
from ..inventory_models import (FormalMaterial, InboundOrder, InventoryMovement,
    InventoryMovementSerial, InventorySerial, InventoryTransaction, OutboundPosting,
    Receipt, Shipment, ShipmentLine, StockAccount)
from ..material_request_my_inbound_candidates_schemas import (InboundCandidateDetailOut,
    InboundCandidateLineOut, InboundCandidateOut, InboundSerialOut, MyInboundCandidatesOut)
from . import inventory_posting as inventory
from . import material_request_fulfillment_command as versions
from . import material_request_inbound as inbound
from . import material_request_my_receipt as receipts
from . import material_request_query as query


def _invalid():
    raise query.MaterialRequestReadError('my_inbound_history_invalid', 'service_unavailable', '入账证据不完整')


def _posted(db, request, order):
    if order.status != 'pending' or order.posting_transaction_id is not None:
        _invalid()
    projection = inbound._posting_projection(db, order)
    if projection['status'] != 'posted':
        return None
    tx = db.get(InventoryTransaction, projection['posting_transaction_id'])
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.idempotency_key_hash == tx.idempotency_key_hash))
    if (tx.posted_at is None or tx.ledger_cursor <= 0 or command is None
            or command.operation != 'personal_inbound' or command.request_id != request.id
            or command.actor_user_id != tx.actor_user_id or not 1 <= command.target_version <= request.version
            or command.request_hash != tx.request_hash
            or command.request_jsonb != versions._document(request.id, command.target_version, 'personal_inbound', tx)
            or not isinstance(command.result_jsonb, dict)
            or command.result_hash != versions._hash(command.result_jsonb)
            or command.result_jsonb != dict(schema_version='1.0', operation='personal_inbound', request_id=str(request.id),
                target_version=command.target_version, fact_id=str(tx.id), personal_inbound_status=command.result_jsonb.get('personal_inbound_status'))
            or command.result_jsonb.get('personal_inbound_status') not in {'pending_acceptance', 'partially_accepted', 'accepted', 'posted'}
            or versions._time(command.occurred_at) != versions._time(tx.created_at)
            or versions._time(command.created_at) != versions._time(tx.created_at)):
        _invalid()
    reference = command.request_reference
    if reference not in {f'/api/v1/material-requests/{request.id}/my-inbounds',
            f'/api/v1/material-requests/{request.id}/inbound-orders/{order.id}/post'}:
        _invalid()
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.action == 'fulfillment_version_recorded',
        AuditEvent.aggregate_type == 'personal_inbound', AuditEvent.aggregate_id == str(tx.id))))
    expected_audit = dict(command_id=str(command.id), request_id=str(request.id), request_version=command.target_version,
        request_hash=command.request_hash, result_hash=command.result_hash,
        permission_action='receive' if reference.endswith('/my-inbounds') else 'fulfill')
    if (len(audits) != 1 or audits[0].actor_user_id != tx.actor_user_id or audits[0].after_jsonb != expected_audit
            or versions._time(audits[0].occurred_at) != versions._time(tx.created_at)):
        _invalid()
    expected = inventory._validate_posting_command(inbound._order_posting_command(db, order))
    digest = inventory._canonical_hash({'operation': 'post', 'actor': dict(authorization_version=command.authorization_version,
        person_id=str(command.actor_person_id), user_id=command.actor_user_id), 'command': inventory._posting_document(expected)})
    moves = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id).order_by(InventoryMovement.line_no)))
    actual = inventory.InventoryPostingCommand(tx.transaction_no, tx.movement_type, tx.source_document_type,
        tx.source_document_id, tx.posting_key, versions._time(tx.effective_at), tuple(inventory.InventoryMovementCommand(
            row.from_account_id, row.to_account_id, row.quantity, tuple(db.scalars(select(InventoryMovementSerial.serial_id)
                .where(InventoryMovementSerial.movement_id == row.id, InventoryMovementSerial.transaction_id == tx.id)
                .order_by(InventoryMovementSerial.serial_id))), row.external_boundary_code) for row in moves))
    if digest != tx.request_hash or inventory._posting_document(actual) != inventory._posting_document(expected):
        _invalid()
    return tx


def _item(db, context, request, receipt):
    original = receipts._result(db, context, request, receipt, replayed=True)
    # The receipt verification already checked every package/outbound binding.
    shipment = db.get(Shipment, original.shipment_id)
    rows = {row.id: row for row in db.scalars(select(ShipmentLine).where(ShipmentLine.shipment_id == shipment.id))}
    location = receipts.receiving._location(db, context=context, shipment=shipment, now=datetime.now(timezone.utc))
    lines = []
    for line in original.lines:
        fact = db.get(OutboundPosting, rows[line.shipment_line_id].outbound_posting_id)
        source = db.get(StockAccount, fact.target_stock_account_id)
        if source is None:
            _invalid()
        material = db.get(FormalMaterial, source.material_id)
        if material is None:
            _invalid()
        serials = []
        # One bounded query per receipt line, rather than a query for every SN.
        serial_rows = {row.id: row for row in db.scalars(select(InventorySerial).where(
            InventorySerial.id.in_(line.accepted_serial_ids)))} if line.accepted_serial_ids else {}
        for serial_id in line.accepted_serial_ids:
            serial = serial_rows.get(serial_id)
            if serial is None or serial.material_id != material.id:
                _invalid()
            serials.append(InboundSerialOut(serial_id=serial.id, serial_no=serial.serial_no))
        lines.append(InboundCandidateLineOut(receipt_line_id=line.receipt_line_id, sku_code=material.sku_code,
            material_name=material.name, base_unit=material.base_unit, accepted_qty=format(line.accepted_qty, '.3f'),
            rejected_qty=format(line.rejected_qty, '.3f'), accepted_serials=tuple(serials)))
    order = db.scalar(select(InboundOrder).where(InboundOrder.receipt_id == receipt.id))
    tx = None
    if order is not None:
        inbound._validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
        tx = _posted(db, request, order)
        if tx is not None:
            command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.idempotency_key_hash == tx.idempotency_key_hash))
            if command.request_reference.endswith('/my-inbounds'):
                from .material_request_my_inbound import _result
                _result(db, context, request, order, replayed=True)
    positive = any(line.accepted_qty > 0 for line in original.lines)
    if tx and not positive:
        _invalid()
    status = 'posted' if tx else 'pending' if positive else 'no_accepted'
    return InboundCandidateOut(receipt_id=receipt.id, status=status,
        message={'posted': '本次合格验收已入账', 'pending': '本次合格验收待入账',
                 'no_accepted': '本次无合格数量，保留拒收及异常记录'}[status],
        detail=InboundCandidateDetailOut(receipt_no=original.receipt_no, receipt_request_hash=original.request_hash,
            shipment_id=shipment.id, shipment_no=shipment.shipment_no, target_location_name=location.name,
            received_at=original.received_at, lines=tuple(lines), inbound_no=order.inbound_no if tx else None,
            inventory_transaction_id=tx.id if tx else None, posted_at=versions._time(tx.posted_at) if tx else None))


def list_my_inbound_candidates(db, *, actor, request_id, limit=5, after_id=None):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise query.MaterialRequestReadError('my_inbound_limit_invalid', 'invalid_request', '入账分页参数无效')
    with db.no_autoflush:
        context, request = receipts._context(db, actor, request_id)
        current, version = context.principal, request.version
        linked = select(ShipmentLine.shipment_id).join(OutboundPosting,
            OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request.id)
        statement = select(Receipt).join(Shipment, Shipment.id == Receipt.shipment_id).where(
            Receipt.shipment_id.in_(linked), Shipment.target_person_id == current.person_id,
            Receipt.receiver_person_id == current.person_id)
        if after_id is not None:
            statement = statement.where(Receipt.id > after_id)
        rows = tuple(db.scalars(statement.order_by(Receipt.id).limit(limit + 1)))
        items = []
        for receipt in rows[:limit]:
            try:
                items.append(_item(db, context, request, receipt))
            except (query.MaterialRequestReadError, inventory.InventoryPostingError, ValidationError):
                # Keep the rest of the recipient's receipts usable. No unchecked
                # quantities, source references or damaged evidence leave this row.
                items.append(InboundCandidateOut(receipt_id=receipt.id, status='blocked', detail=None,
                    message='该验收的归属或入账证据未通过核验，请联系管理员处理'))
        latest = query._load_read_context(db, actor=actor, now=None)
        if latest.principal != current or db.scalar(select(MaterialRequest.version).where(MaterialRequest.id == request.id)) != version:
            raise query.MaterialRequestReadError('my_inbound_context_changed', 'precondition_failed', '查询期间权限或需求已变化，请刷新')
        return MyInboundCandidatesOut(request_id=request.id, request_no=request.request_no, request_version=version,
            person_id=current.person_id, can_post=request.status in {'approved', 'partially_approved'} and current.allows(db,
                'material_request', 'receive', target_scope_type='person', target_scope_id=str(current.person_id)),
            items=tuple(items), next_after_id=rows[limit-1].id if len(rows) > limit else None)
