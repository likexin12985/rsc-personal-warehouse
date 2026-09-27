"""Reconstruct the immutable loss submission, including its exact freeze.

This is the original submission result, not the later review/disposition state.
Historical quantities come from its ledger cursor; current balances are never
used as evidence of what was available before the submission.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import select

from ..foundation_models import FileObject, NotificationEvent, NotificationPersonTarget, OutboxEvent, StateTransitionEvent
from ..inventory_models import FormalMaterial, InventorySerial, InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockAccount
from ..stock_operation_models import StockLossFile, StockOperationLine as Line, StockOperationOrder as Order, StockOperationSerial as Serial
from ..stock_loss_schemas import StockLossPreviewIn, StockLossSubmittedOut
from . import inventory_posting as posting, stock_loss_plan as plan, stock_loss_sources as sources
from .audit_chain import AuditChainError
from .formal_files import is_available_formal_file_for_purpose
from .notification_events import target_manifest_hash
from .serial_ledger import SerialLedgerError, rebuild_serial_states
from .stock_return_facts import audit, single
from .work_order_query import _aware

KIND = 'stock_loss_submitted'
AGGREGATE = 'stock_operation_order'


def invalid():
    sources._fail('stock_loss_evidence_invalid', '原报损单、冻结流水或附件证据不完整，请保留原请求核验', 503)


def posting_command(order, *, at, movements):
    key = order.idempotency_key_hash
    return posting.InventoryPostingCommand(transaction_no='INV-LOSS-S-'+key[:20].upper(),
        movement_type='freeze', source_document_type='stock_operation_loss', source_document_id=str(order.id),
        posting_key=f'stock-loss:submit_loss:{order.id}:{key}', effective_at=at, movements=movements)


def payload(order):
    return dict(operation_id=str(order.id), requester_id=str(order.requester_id),
        posting_transaction_id=str(order.posting_transaction_id), request_hash=order.request_hash,
        plan_hash=order.plan_hash)


def _result(db, *, actor, order):
    if order.actor_user_id != actor.user_id or order.requester_id != actor.person_id:
        sources._fail('stock_loss_not_found', '本人原报损单不存在', 404)
    if (order.operation_type != 'loss_report' or order.status != 'submitted'
            or order.authorization_version < 1
            or not re.fullmatch(r'[0-9a-f]{64}', order.idempotency_key_hash)
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', order.request_id)
            or order.operation_no != 'LOSS-'+order.idempotency_key_hash[:24].upper()
            or any(getattr(order, field) is not None for field in
                ('oam_work_order_id','target_location_id','transit_location_id','target_custody_assignment_id'))
            or sources._hash(order.command_jsonb) != order.request_hash
            or sources._hash(order.plan_jsonb) != order.plan_hash
            or order.plan_jsonb['intent'] != order.command_jsonb
            or order.plan_jsonb['authorization_version'] != order.authorization_version
            or order.plan_jsonb['location_id'] != str(order.source_location_id)
            or not re.fullmatch(r'[0-9a-f]{64}',order.plan_jsonb['source_basis_hash'])):
        invalid()
    request = StockLossPreviewIn.model_validate({k:order.command_jsonb[k] for k in StockLossPreviewIn.model_fields})
    if (plan.intent(request) != order.command_jsonb or request.reason != order.reason
            or request.operator_person_id != order.requester_id): invalid()
    tx = db.get(InventoryTransaction, order.posting_transaction_id, populate_existing=True)
    if tx is None or order.plan_jsonb['ledger_cursor'] != tx.ledger_cursor-1: invalid()
    lines = tuple(db.scalars(select(Line).where(Line.operation_id==order.id).order_by(Line.line_no)
        .execution_options(populate_existing=True)))
    planned = order.plan_jsonb['lines']
    selected = sorted(request.lines,key=lambda line:str(line.stock_account_id))
    if not 1<=len(lines)<=100 or len(lines)!=len(planned) or len(lines)!=len(selected): invalid()
    before_cursor=tx.ledger_cursor-1
    movements=[]
    seen_serials=set()
    for number,(line,wanted,view) in enumerate(zip(lines,selected,planned,strict=True),1):
        source=db.get(StockAccount,line.stock_account_id,populate_existing=True)
        held=db.get(StockAccount,line.reserved_account_id,populate_existing=True)
        basis=view['source']
        if (source is None or held is None or line.operation_type!='loss_report'
                or line.source_recovery_line_id is not None or line.line_no!=number
                or line.stock_account_id!=wanted.stock_account_id or line.quantity!=wanted.quantity
                or line.quantity<=0 or line.reason!=order.reason or line.material_id!=source.material_id
                or source.custodian_person_id!=order.requester_id or source.location_id!=order.source_location_id
                or source.availability_bucket!='available' or held.availability_bucket!='frozen'
                or line.target_condition!=source.condition_code
                or source.condition_code not in {'new','used','damaged'}
                or any(getattr(source,k)!=getattr(held,k) for k in
                    ('owner_org_id','custodian_person_id','location_id','material_id','lot_id','condition_code'))
                or view['selected_quantity']!=format(line.quantity,'.3f')): invalid()
        for key in ('stock_account_id','owner_org_id','custodian_person_id','location_id','material_id','lot_id'):
            actual = source.id if key=='stock_account_id' else getattr(source,key)
            if basis[key]!=(str(actual) if actual is not None else None): invalid()
        if basis['condition_code']!=source.condition_code or basis['availability_bucket']!='available': invalid()
        historical=Decimal(0)
        for move in db.scalars(select(InventoryMovement).join(InventoryTransaction,
                InventoryTransaction.id==InventoryMovement.transaction_id).where(
                InventoryTransaction.ledger_cursor<=before_cursor,
                (InventoryMovement.from_account_id==source.id)|(InventoryMovement.to_account_id==source.id))):
            historical += move.quantity if move.to_account_id==source.id else -move.quantity
        if Decimal(basis['quantity'])!=historical or line.quantity>historical: invalid()
        proofs=tuple(db.scalars(select(Serial).where(Serial.line_id==line.id).order_by(Serial.serial_id)
            .execution_options(populate_existing=True)))
        ids=tuple(row.serial_id for row in proofs)
        if (any(not row.sku_verified or not row.qr_verified for row in proofs)
                or ids!=tuple(sorted((p.serial_id for p in wanted.serial_verifications),key=str))
                or set(ids)&seen_serials or view['selected_serials']!=[
                    dict(serial_id=str(p.serial_id),serial_no=p.serial_no)
                    for p in sorted(wanted.serial_verifications,key=lambda p:str(p.serial_id))]): invalid()
        seen_serials.update(ids)
        sku=db.get(FormalMaterial,source.material_id,populate_existing=True)
        if sku is None: invalid()
        for proof in wanted.serial_verifications:
            serial=db.get(InventorySerial,proof.serial_id,populate_existing=True)
            if (serial is None or serial.material_id!=source.material_id or serial.lot_id!=source.lot_id
                    or proof.sku_code!=sku.sku_code or proof.serial_no!=serial.serial_no
                    or proof.qr_code!=serial.qr_code): invalid()
        states=rebuild_serial_states(db,set(ids),through_cursor=before_cursor)
        if any(sn not in states or states[sn].stock_account_id!=source.id
               or states[sn].lifecycle_status!='active' for sn in ids): invalid()
        movements.append(posting.InventoryMovementCommand(from_account_id=source.id,to_account_id=held.id,
            quantity=line.quantity,serial_ids=ids))
    command=posting_command(order,at=_aware(tx.effective_at),movements=tuple(movements))
    if (tx.status!='posted' or tx.actor_user_id!=order.actor_user_id or tx.reversed_transaction_id is not None
            or tx.idempotency_key_hash!=order.idempotency_key_hash
            or any(getattr(tx,k)!=getattr(command,k) for k in
                ('transaction_no','source_document_type','source_document_id','posting_key','movement_type'))
            or tx.request_hash!=posting._posting_request_hash(replace(actor,authorization_version=order.authorization_version),command)
            or _aware(tx.created_at)<_aware(order.created_at)
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id==tx.id).limit(1))): invalid()
    actual=tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id==tx.id)
        .order_by(InventoryMovement.line_no).execution_options(populate_existing=True)))
    if len(actual)!=len(movements): invalid()
    for number,(row,wanted) in enumerate(zip(actual,movements,strict=True),1):
        ids=tuple(db.scalars(select(InventoryMovementSerial.serial_id)
            .where(InventoryMovementSerial.movement_id==row.id).order_by(InventoryMovementSerial.serial_id)))
        if (row.line_no!=number or row.from_account_id!=wanted.from_account_id
                or row.to_account_id!=wanted.to_account_id or row.quantity!=wanted.quantity
                or row.external_boundary_code is not None or ids!=wanted.serial_ids): invalid()
    bindings=tuple(db.scalars(select(StockLossFile).where(StockLossFile.operation_id==order.id)
        .order_by(StockLossFile.file_id).execution_options(populate_existing=True)))
    evidence=order.plan_jsonb['evidence']
    if (not 1<=len(bindings)<=20 or [str(row.file_id) for row in bindings]!=sorted(str(p) for p in request.evidence_file_ids)
            or len(bindings)!=len(evidence)): invalid()
    for binding,expected in zip(bindings,evidence,strict=True):
        file=db.get(FileObject,binding.file_id,populate_existing=True)
        if (binding.operation_type!='loss_report'
                or _aware(binding.created_at)!=_aware(order.created_at)
                or not is_available_formal_file_for_purpose(file,purpose='stock_loss_evidence',uploader_user_id=order.actor_user_id)
                or file.metadata_jsonb.get('provider')!='aliyun_oss_v2'
                or file.metadata_jsonb.get('uploader_person_id')!=str(order.requester_id)
                or file.metadata_jsonb.get('authorization_version')!=order.authorization_version
                or sources._hash(file.metadata_jsonb)!=binding.metadata_sha256
                or not _aware(file.created_at)<=_aware(datetime.fromisoformat(
                    file.metadata_jsonb['completion']['verified_at']))<=_aware(order.created_at)
                or expected!=dict(file_id=str(file.id),original_filename=file.original_filename,
                    sha256=file.sha256,size_bytes=file.size_bytes,mime_type=file.mime_type,
                    metadata_sha256=binding.metadata_sha256)): invalid()
    audit(db,actor=actor,stream='inventory',aggregate_type='inventory_transaction',identifier=tx.id,
        action='inventory.transaction.posted',request_id=posting._request_reference(order.request_id),before=None,
        after=dict(ledger_cursor=tx.ledger_cursor,movement_count=len(lines),movement_type='freeze',
            posting_key=tx.posting_key,reversed_transaction_id=None,status='posted'))
    single(db,StateTransitionEvent,aggregate_type='inventory_transaction',aggregate_id=str(tx.id),from_status=None,
        to_status='posted',reason='inventory_transaction_posted',actor_id=actor.user_id,
        idempotency_key=posting._derived_evidence_key('state',tx.id,'posted'),
        metadata_jsonb=dict(ledger_cursor=tx.ledger_cursor,movement_type='freeze',request_reference=posting._request_reference(order.request_id)))
    single(db,OutboxEvent,aggregate_type='inventory_transaction',aggregate_id=str(tx.id),event_type='inventory.transaction.posted',
        idempotency_key=posting._derived_evidence_key('outbox',tx.id,'posted'),payload_jsonb=dict(transaction_id=str(tx.id),
            transaction_no=tx.transaction_no,movement_type='freeze',ledger_cursor=tx.ledger_cursor,reversed_transaction_id=None))
    body=payload(order)
    audit(db,actor=actor,stream='inventory',aggregate_type=AGGREGATE,identifier=order.id,action=KIND,
        request_id=order.request_id,before={},after=body)
    single(db,OutboxEvent,aggregate_type=AGGREGATE,aggregate_id=str(order.id),event_type=KIND,
        idempotency_key=KIND+':'+str(order.id),payload_jsonb=body)
    single(db,StateTransitionEvent,aggregate_type=AGGREGATE,aggregate_id=str(order.id),from_status=None,
        to_status='submitted',reason=KIND,actor_id=actor.user_id,idempotency_key=KIND+':'+str(order.id),metadata_jsonb=body)
    event=single(db,NotificationEvent,event_type=KIND,business_type=AGGREGATE,business_id=str(order.id),
        dedup_key='stock-loss-notification:'+KIND+':'+str(order.id),payload_jsonb=body)
    targets=tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id==event.id)))
    if targets!=(order.requester_id,) or event.target_manifest_sha256!=target_manifest_hash(targets): invalid()
    return StockLossSubmittedOut(operation_id=order.id,operation_no=order.operation_no,requester_id=order.requester_id,
        source_location_id=order.source_location_id,reason=order.reason,request_id=order.request_id,
        request_hash=order.request_hash,plan_hash=order.plan_hash,posting_transaction_id=tx.id,
        submitted_at=_aware(order.created_at),lines=planned,evidence=evidence)


def order_result(db, *, actor, order):
    try:
        return _result(db,actor=actor,order=order)
    except (KeyError,TypeError,ValueError,AttributeError,InvalidOperation,AuditChainError,SerialLedgerError):
        invalid()


@dataclass(frozen=True)
class _SubmissionIdentity:
    """Historical hash coordinates, deliberately not an authorization principal."""
    user_id: str
    person_id: object
    authorization_version: int


def submission_evidence(db, *, order):
    """Internal historical proof after the caller authorizes the current reader.

    A former employee's current login/grants are not required to verify their
    immutable submission. This DTO cannot grant permission or perform writes.
    """
    return order_result(db, actor=_SubmissionIdentity(
        order.actor_user_id, order.requester_id, order.authorization_version), order=order)
