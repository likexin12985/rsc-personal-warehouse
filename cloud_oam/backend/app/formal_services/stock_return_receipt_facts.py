"""Verify return acceptance at its original audit/ledger coordinates."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID

from sqlalchemy import select, or_

from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, FileObject
from ..inventory_models import (Receipt, ReceiptLine, ReceiptException, InboundOrder, Shipment,
    InventoryTransaction, InventoryMovement, InventoryMovementSerial, InventorySerial,
    MaterialInventoryPolicy, CustodyAssignment)
from ..stock_operation_models import (StockOperationReceipt, StockOperationReceiptLine,
    StockOperationReceiptSerial, StockOperationReceiptException, StockOperationShipment,
    StockOperationShipmentLine, StockOperationOutboundLine, StockOperationOrder,
    StockOperationCancellation, StockOperationOutbound, StockOperationCommandSeal)
from ..stock_return_receipt_schemas import StockReturnReceiptPreviewIn, StockReturnReceiptLineOut, StockReturnReceiptOut
from ..stock_return_receiving_schemas import StockReturnReceivingPackageOut
from . import stock_return_receipt_plan as planning, stock_return_receiving as receiving
from . import stock_return_shipment_facts as parcels, stock_return_facts as returns, inventory_posting as posting
from .audit_chain import AuditChainError
from .formal_files import _validate_intent_metadata, FormalFileError
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail


def invalid():
    _fail('stock_return_receipt_evidence_invalid', '原包裹验收数量、SN、异常或审计证据不一致，请保留原请求核验', 503)


def payload(fact, header, package):
    return dict(receipt_id=str(fact.id), shipment_id=str(fact.shipment_id), operation_id=str(package.operation_id),
        work_order_id=str(package.work_order_id), operator_person_id=str(fact.operator_person_id),
        status=header.status, request_hash=header.request_hash)


def _namespace(db, fact, header):
    for model in (StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
            StockOperationShipment, StockOperationCommandSeal):
        if db.scalar(select(model.id).where(model.actor_user_id == fact.actor_user_id,
                model.request_id == fact.request_id).limit(1)): invalid()
    for model in (ReceiptLine, ReceiptException, InboundOrder):
        if db.scalar(select(model.id).where(model.receipt_id == fact.id).limit(1)): invalid()
    if db.scalar(select(Shipment.id).where(Shipment.idempotency_key_hash == header.idempotency_key_hash).limit(1)): invalid()
    if db.scalar(select(InventoryTransaction.id).where(or_(
            InventoryTransaction.idempotency_key_hash == header.idempotency_key_hash,
            (InventoryTransaction.source_document_type == 'stock_operation_return_receipt') &
            (InventoryTransaction.source_document_id == str(fact.id)))).limit(1)): invalid()
    domains = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'material_request', AuditEvent.actor_user_id == fact.actor_user_id,
        AuditEvent.request_id == fact.request_id)))
    if domains != (('stock_operation_receipt', str(fact.id)),): invalid()
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == 'inventory',
            AuditEvent.actor_user_id == fact.actor_user_id,
            AuditEvent.request_id == posting._request_reference(fact.request_id)).limit(1)): invalid()
    if db.scalar(select(StateTransitionEvent.id).where(StateTransitionEvent.aggregate_type == 'inventory_transaction',
            StateTransitionEvent.actor_id == fact.actor_user_id,
            StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == posting._request_reference(fact.request_id)).limit(1)): invalid()


def _policies(db, package, plan, at):
    result = {}
    for fingerprint in plan['policies']:
        if len(fingerprint) != 7: invalid()
        material_id, policy_id, tracking, scale, fraction, start, end = fingerprint
        row = db.get(MaterialInventoryPolicy, UUID(policy_id), populate_existing=True)
        if (row is None or str(row.material_id) != material_id or row.tracking_mode != tracking
                or type(scale) is not int or type(fraction) is not bool or row.quantity_scale != scale
                or row.allow_fraction != fraction or _aware(row.effective_from).isoformat() != start
                or _aware(row.effective_from) > at or row.effective_to is not None and _aware(row.effective_to) <= at
                or end is not None and (datetime.fromisoformat(end) <= at or end != _aware(row.effective_to).isoformat())
                or row.material_id in result): invalid()
        result[row.material_id] = row
    if set(result) != {row.material_id for row in package.lines}: invalid()
    if list(plan['policies']) != sorted(plan['policies'], key=lambda item: item[0]): invalid()
    return result


def _files(db, fact, request, plan):
    ids = sorted({item.evidence_file_id for line in request.lines for item in line.exceptions}, key=str)
    fingerprints = []
    for identifier in ids:
        row = db.get(FileObject, identifier, populate_existing=True)
        if row is None: invalid()
        # Quarantining a file later must not erase the already recorded receipt.
        metadata = _validate_intent_metadata(row, allow_completed=True)
        if (metadata['purpose'] != 'receipt_exception_evidence' or 'completion' not in metadata
                or metadata['uploader_user_id'] != fact.actor_user_id or row.uploaded_by != fact.actor_user_id
                or metadata['uploader_person_id'] != str(fact.operator_person_id)
                or metadata['authorization_version'] != fact.authorization_version
                or not _aware(row.created_at) <= datetime.fromisoformat(metadata['completion']['verified_at']) <= _aware(fact.created_at)):
            invalid()
        fingerprints.append(list(planning.file_fingerprint(row)))
        if (db.scalar(select(StockOperationReceiptException.id).where(
                StockOperationReceiptException.evidence_file_id == identifier,
                StockOperationReceiptException.receipt_id != fact.id).limit(1))
                or db.scalar(select(ReceiptException.id).where(ReceiptException.evidence_file_id == identifier).limit(1))): invalid()
    if fingerprints != [list(item) for item in plan['evidence']]: invalid()


def _line(db, fact, row, chosen, original, policy, accepted, rejected, used):
    at = _aware(fact.created_at)
    if row.shipment_line_id != chosen.shipment_line_id or row.shipment_line_id != original.shipment_line_id:
        invalid()
    quantities = ('accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty')
    remaining = Decimal(original.shipped_quantity) - accepted[row.shipment_line_id] - rejected[row.shipment_line_id]
    if row.accepted_qty + row.rejected_qty + row.shortage_qty > remaining: invalid()
    for key in quantities:
        value = getattr(row, key)
        if (value != getattr(chosen, key) or value != value.quantize(Decimal(1).scaleb(-policy.quantity_scale))
                or not policy.allow_fraction and value != value.to_integral_value()): invalid()
    names = {sn.serial_id: sn.serial_no for sn in original.serials}
    tracked = policy.tracking_mode in {'serial', 'lot_and_serial'}
    if bool(names) != tracked or (policy.tracking_mode in {'lot', 'lot_and_serial'}) != (original.lot_id is not None): invalid()
    groups = (tuple(proof.serial_id for proof in chosen.accepted_serial_verifications), chosen.rejected_serial_ids,
        chosen.shortage_serial_ids, chosen.damaged_serial_ids)
    for ids, amount in zip(groups, (row.accepted_qty, row.rejected_qty, row.shortage_qty, row.damaged_qty)):
        if ((tracked and Decimal(len(ids)) != amount) or (not tracked and ids)
                or not set(ids) <= names.keys() - used[row.shipment_line_id]): invalid()
    serials = tuple(db.scalars(select(StockOperationReceiptSerial).where(StockOperationReceiptSerial.line_id == row.id)
        .order_by(StockOperationReceiptSerial.serial_id).limit(1001).execution_options(populate_existing=True)))
    expected = sorted([(identifier, result, identifier in chosen.damaged_serial_ids, result == 'accepted', result == 'accepted')
        for ids, result in zip(groups[:3], ('accepted', 'rejected', 'shortage')) for identifier in ids], key=lambda item: str(item[0]))
    if (len(serials) > 1000 or [(sn.serial_id, sn.result, sn.damaged, sn.sku_verified, sn.qr_verified) for sn in serials] != expected
            or any(sn.shipment_line_id != row.shipment_line_id or _aware(sn.created_at) != at for sn in serials)): invalid()
    shipment_line = db.get(StockOperationShipmentLine, row.shipment_line_id, populate_existing=True)
    outbound_line = db.get(StockOperationOutboundLine, shipment_line.outbound_line_id, populate_existing=True)
    for proof in chosen.accepted_serial_verifications:
        serial = db.get(InventorySerial, proof.serial_id, populate_existing=True)
        position = db.scalar(select(InventoryMovement.to_account_id).join(InventoryMovementSerial,
            InventoryMovementSerial.movement_id == InventoryMovement.id).join(InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id).where(
                InventoryMovementSerial.serial_id == proof.serial_id,
                InventoryTransaction.ledger_cursor <= fact.plan_jsonb['ledger_cursor'])
            .order_by(InventoryTransaction.ledger_cursor.desc(), InventoryMovement.line_no.desc()).limit(1))
        if (serial is None or serial.material_id != original.material_id or serial.lot_id != original.lot_id
                or proof.serial_no != serial.serial_no or proof.serial_no != names[proof.serial_id]
                or proof.sku_code != original.sku_code or proof.qr_code != serial.qr_code
                or position != outbound_line.transit_stock_account_id): invalid()
    exceptions = tuple(db.scalars(select(StockOperationReceiptException).where(StockOperationReceiptException.line_id == row.id)
        .order_by(StockOperationReceiptException.exception_type).limit(6).execution_options(populate_existing=True)))
    selected = tuple(sorted(chosen.exceptions, key=lambda item: item.exception_type))
    if (len(exceptions) != len(selected) or any(item.receipt_id != fact.id or _aware(item.created_at) != at for item in exceptions)
            or [(item.exception_type, item.description, item.evidence_file_id) for item in exceptions]
                != [(item.exception_type, item.description, item.evidence_file_id) for item in selected]): invalid()
    def projected(ids):
        return tuple(dict(serial_id=identifier, serial_no=names[identifier]) for identifier in sorted(ids, key=str))
    view = StockReturnReceiptLineOut(**{key: getattr(original, key) for key in
        ('shipment_line_id', 'material_id', 'sku_code', 'material_name', 'base_unit', 'condition_code', 'lot_id', 'lot_no')},
        shipped_qty=original.shipped_quantity, previously_accepted_qty=format(accepted[row.shipment_line_id], '.3f'),
        previously_rejected_qty=format(rejected[row.shipment_line_id], '.3f'), unconfirmed_qty=format(remaining, '.3f'),
        **{key: format(getattr(row, key), '.3f') for key in quantities}, accepted_serials=projected(groups[0]),
        rejected_serials=projected(groups[1]), shortage_serials=projected(groups[2]),
        damaged_serial_ids=tuple(sorted(groups[3], key=str)), exceptions=selected)
    accepted[row.shipment_line_id] += row.accepted_qty
    rejected[row.shipment_line_id] += row.rejected_qty
    used[row.shipment_line_id].update((*groups[0], *groups[1]))
    return view


def _result(db, fact, parcel, checked, accepted, rejected, used):
    header = db.get(Receipt, fact.id, populate_existing=True)
    request = StockReturnReceiptPreviewIn.model_validate({key: fact.command_jsonb[key] for key in StockReturnReceiptPreviewIn.model_fields})
    plan = fact.plan_jsonb
    if (set(plan) != {'intent', 'authorization_version', 'ledger_cursor', 'audit_cursor', 'package', 'policies', 'evidence', 'lines'}
            or header is None or header.shipment_id != fact.shipment_id or fact.shipment_id != parcel.id
            or type(plan['ledger_cursor']) is not int or plan['ledger_cursor'] < parcel.plan_jsonb['ledger_cursor']
            or type(plan['audit_cursor']) is not int or plan['audit_cursor'] + 1 != fact.audit_version
            or fact.audit_version <= parcel.audit_version or fact.authorization_version < 1
            or type(plan['authorization_version']) is not int or plan['authorization_version'] != fact.authorization_version or plan['intent'] != fact.command_jsonb
            or planning.intent(fact.shipment_id, request) != fact.command_jsonb
            or header.request_hash != _hash(fact.command_jsonb) or fact.plan_hash != _hash(plan)
            or not re.fullmatch(r'[a-f0-9]{64}', header.idempotency_key_hash)
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', fact.request_id)
            or header.receipt_no != 'RET-RCV-' + header.idempotency_key_hash[:24].upper()
            or request.operator_person_id != fact.operator_person_id or header.receiver_person_id != fact.operator_person_id
            or request.reason != fact.reason or request.received_at != _aware(header.received_at)
            or _aware(fact.created_at) != _aware(header.created_at) or request.received_at > _aware(fact.created_at)
            or _aware(fact.created_at) > datetime.now(timezone.utc)
            or request.received_at < checked.shipped_at or _aware(fact.created_at) < checked.recorded_at): invalid()
    package = StockReturnReceivingPackageOut.model_validate(plan['package'])
    if (not package.target_location_name.strip() or package.model_dump(mode='json') != plan['package']
            or receiving.project_package(db, parcel, checked, target_location_name=package.target_location_name) != package
            or fact.operator_person_id != package.receiver_person_id
            or fact.target_custody_assignment_id != package.custody_assignment_id): invalid()
    assignment = db.get(CustodyAssignment, fact.target_custody_assignment_id, populate_existing=True)
    if (assignment is None or assignment.location_id != package.target_location_id
            or assignment.custodian_person_id != fact.operator_person_id or _aware(assignment.valid_from) > request.received_at
            or assignment.valid_to is not None and _aware(assignment.valid_to) <= _aware(fact.created_at)): invalid()
    policies = _policies(db, package, plan, _aware(fact.created_at)); _files(db, fact, request, plan)
    rows = tuple(db.scalars(select(StockOperationReceiptLine).where(StockOperationReceiptLine.receipt_id == fact.id)
        .order_by(StockOperationReceiptLine.line_no).limit(101).execution_options(populate_existing=True)))
    chosen = sorted(request.lines, key=lambda item: str(item.shipment_line_id))
    if not 1 <= len(rows) == len(chosen) <= 100: invalid()
    originals = {row.shipment_line_id: row for row in package.lines}; views = []
    for number, (row, selected) in enumerate(zip(rows, chosen), 1):
        if row.line_no != number or row.shipment_line_id not in originals or _aware(row.created_at) != _aware(fact.created_at): invalid()
        original = originals[row.shipment_line_id]
        views.append(_line(db, fact, row, selected, original, policies[original.material_id], accepted, rejected, used))
    status = 'exception' if any(line.exceptions for line in request.lines) else 'accepted'
    if header.status != status or [row.model_dump(mode='json') for row in views] != plan['lines']: invalid()
    actor = parcels._RecordedOperator(fact.actor_user_id, fact.operator_person_id, fact.authorization_version)
    body = payload(fact, header, package); kind = 'stock_return_received'; aggregate = 'stock_operation_receipt'
    returns.audit(db, actor=actor, stream='material_request', aggregate_type=aggregate, identifier=fact.id,
        action=kind, request_id=fact.request_id, before={}, after=body)
    event = returns.single(db, AuditEvent, stream_key='material_request', aggregate_type=aggregate, aggregate_id=str(fact.id))
    if (event.stream_version != fact.audit_version or _aware(event.occurred_at) != _aware(fact.created_at)
            or _aware(event.created_at) != _aware(fact.created_at)): invalid()
    returns.single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), event_type=kind,
        idempotency_key=kind + ':' + str(fact.id), payload_jsonb=body)
    returns.single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status=status, actor_id=actor.user_id, reason=kind, idempotency_key=kind + ':' + str(fact.id), metadata_jsonb=body)
    _namespace(db, fact, header)
    return StockReturnReceiptOut(receipt_id=fact.id, receipt_no=header.receipt_no, shipment_id=fact.shipment_id,
        operation_id=package.operation_id, work_order_id=package.work_order_id, operator_person_id=fact.operator_person_id,
        status=status, received_at=request.received_at, recorded_at=_aware(fact.created_at), reason=fact.reason,
        request_id=fact.request_id, request_hash=header.request_hash, plan_hash=fact.plan_hash,
        target_location_id=package.target_location_id, target_custody_assignment_id=fact.target_custody_assignment_id, lines=views)


def verified_receipts(db, *, shipment_id, through_audit_version=None):
    """Prove ordered history once; shortages never consume confirmed budgets."""
    try:
        parcel = db.get(StockOperationShipment, shipment_id, populate_existing=True)
        if parcel is None: invalid()
        checked = parcels.verified_shipment_history(db, fact=parcel)
        statement = select(StockOperationReceipt).where(StockOperationReceipt.shipment_id == shipment_id)
        if through_audit_version is not None: statement = statement.where(StockOperationReceipt.audit_version <= through_audit_version)
        rows = tuple(db.scalars(statement.order_by(StockOperationReceipt.audit_version).limit(1001)
            .execution_options(populate_existing=True)))
        if len(rows) > 1000: invalid()
        accepted = defaultdict(Decimal); rejected = defaultdict(Decimal); used = defaultdict(set)
        return tuple(_result(db, fact, parcel, checked, accepted, rejected, used) for fact in rows)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, AuditChainError, FormalFileError): invalid()


def verified_receipt_history(db, *, fact):
    if fact is None: invalid()
    history = verified_receipts(db, shipment_id=fact.shipment_id, through_audit_version=fact.audit_version)
    if not history or history[-1].receipt_id != fact.id: invalid()
    return history[-1]


def receipt_result(db, *, actor, fact):
    if fact is None: _fail('stock_return_receipt_not_found', '本人原验收记录不存在', 404)
    current, _ = planning.authorize(db, actor, fact.shipment_id, action='read')
    if fact.actor_user_id != current.user_id or fact.operator_person_id != current.person_id:
        _fail('stock_return_receipt_not_found', '本人原验收记录不存在', 404)
    return verified_receipt_history(db, fact=fact)
