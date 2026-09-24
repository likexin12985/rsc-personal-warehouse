"""Re-prove a return inbound from its acceptance, ledger and durable evidence.

Current balances and notification delivery are independent projections. A later
legitimate movement must not invalidate an earlier inbound receipt.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from types import SimpleNamespace
import uuid

from sqlalchemy import func, select

from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import (CustodyAssignment, InventoryMovement, InventoryMovementSerial,
    InventorySerial, InventoryTransaction, MaterialInventoryPolicy, StockAccount, StockLocation)
from ..stock_operation_models import (StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
    StockOperationOutboundLine, StockOperationShipment, StockOperationShipmentLine, StockOperationReceipt,
    StockOperationReceiptLine, StockOperationReceiptSerial, StockOperationCommandSeal,
    StockOperationReturnInbound, StockOperationReturnInboundLine, StockOperationReturnInboundSerial,
    StockOperationReturnInboundPosting, StockOperationReturnInboundSeal)
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_return_facts as shared, stock_return_receipt_facts as receipts
from .audit_chain import AuditChainError
from .stock_return_inbound_contract import ReturnInboundLine, build_return_inbound_command
from .stock_return_inbound_plan import authorize_receipt
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail, _hash


def invalid():
    _fail('stock_return_inbound_evidence_invalid', '原入账的验收、库存流水、责任或审计证据不一致，请保留原请求核验', 503)


def document(row, *, replayed=False):
    return dict(schema_version='1.0', inbound_id=row.id, inbound_no=row.inbound_no,
        receipt_id=row.receipt_id, shipment_id=row.shipment_id, target_location_id=row.target_location_id,
        target_custody_assignment_id=row.target_custody_assignment_id, status=row.status,
        posting_transaction_id=row.posting_transaction_id, request_id=row.request_id,
        request_hash=row.request_hash, plan_hash=row.plan_hash, replayed=replayed)


def _rows(db, model, *conditions, order=None, limit=1001):
    statement=select(model).where(*conditions)
    if order is not None: statement=statement.order_by(order)
    return tuple(db.scalars(statement.limit(limit).execution_options(populate_existing=True)))


def _namespace(db, fact, tx):
    for model in (StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
            StockOperationShipment, StockOperationReceipt, StockOperationCommandSeal, StockOperationReturnInboundSeal):
        if db.scalar(select(model.id).where(model.actor_user_id==fact.actor_user_id,model.request_id==fact.request_id).limit(1)):invalid()
    domains=tuple(db.execute(select(AuditEvent.aggregate_type,AuditEvent.aggregate_id).where(
        AuditEvent.stream_key=='material_request',AuditEvent.actor_user_id==fact.actor_user_id,
        AuditEvent.request_id==fact.request_id).limit(2)))
    if domains!=(('stock_operation_return_inbound',str(fact.id)),):invalid()
    reference=posting._request_reference(fact.request_id)
    audits=tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key=='inventory',
        AuditEvent.actor_user_id==fact.actor_user_id,AuditEvent.request_id==reference).limit(2)))
    states=tuple(db.scalars(select(StateTransitionEvent.aggregate_id).where(
        StateTransitionEvent.aggregate_type=='inventory_transaction',StateTransitionEvent.actor_id==fact.actor_user_id,
        StateTransitionEvent.metadata_jsonb['request_reference'].as_string()==reference).limit(2)))
    if audits!=(str(tx.id),) or states!=(str(tx.id),):invalid()
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key=='material_request',
            AuditEvent.actor_user_id==fact.actor_user_id,AuditEvent.aggregate_type.in_(
                ('stock_operation_command_seal','stock_operation_return_inbound_seal')),
            AuditEvent.after_jsonb['request_id'].as_string()==fact.request_id).limit(1)):invalid()


def _stock_before(db, command, tx):
    # Reconstruct at the original cursor, never compare with today's balance.
    quantities={}
    for wanted in command.movements:
        quantities[wanted.from_account_id]=quantities.get(wanted.from_account_id,Decimal(0))+wanted.quantity
        for identifier in wanted.serial_ids:
            previous=db.scalar(select(InventoryMovement.to_account_id).join(InventoryTransaction,
                InventoryTransaction.id==InventoryMovement.transaction_id).join(InventoryMovementSerial,
                InventoryMovementSerial.movement_id==InventoryMovement.id).where(
                    InventoryTransaction.status=='posted',InventoryTransaction.ledger_cursor<tx.ledger_cursor,
                    InventoryMovementSerial.serial_id==identifier).order_by(
                        InventoryTransaction.ledger_cursor.desc(),InventoryMovement.line_no.desc()).limit(1))
            if previous!=wanted.from_account_id:invalid()
    for account_id,quantity in quantities.items():
        def total(column):
            return db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity),0)).join(InventoryTransaction,
                InventoryTransaction.id==InventoryMovement.transaction_id).where(InventoryTransaction.status=='posted',
                InventoryTransaction.ledger_cursor<tx.ledger_cursor,column==account_id))
        if total(InventoryMovement.to_account_id)-total(InventoryMovement.from_account_id)<quantity:invalid()


def _evidence(db, fact, tx, command, actor):
    body={key:str(value) if isinstance(value,uuid.UUID) else value for key,value in document(fact).items()}
    aggregate='stock_operation_return_inbound';kind='stock_return_inbound_posted'
    shared.audit(db,actor=actor,stream='material_request',aggregate_type=aggregate,identifier=fact.id,
        action=kind,request_id=fact.request_id,before={},after=body)
    audit=shared.single(db,AuditEvent,stream_key='material_request',aggregate_type=aggregate,aggregate_id=str(fact.id))
    outbox=shared.single(db,OutboxEvent,aggregate_type=aggregate,aggregate_id=str(fact.id))
    state=shared.single(db,StateTransitionEvent,aggregate_type=aggregate,aggregate_id=str(fact.id))
    if (audit.stream_version!=fact.audit_version or audit.stream_version<=db.get(StockOperationReceipt,fact.receipt_id).audit_version
            or outbox.event_type!=kind or outbox.idempotency_key!=f'stock-return-inbound:{fact.id}' or outbox.payload_jsonb!=body
            or state.from_status is not None or state.to_status!='posted' or state.actor_id!=fact.actor_user_id
            or state.reason!=kind or state.idempotency_key!=outbox.idempotency_key or state.metadata_jsonb!=body
            or any(_aware(row.created_at)!=_aware(fact.created_at) for row in (audit,outbox,state))
            or any(_aware(row.occurred_at)!=_aware(fact.created_at) for row in (audit,state))):invalid()
    shared.audit(db,actor=actor,stream='inventory',aggregate_type='inventory_transaction',identifier=tx.id,
        action='inventory.transaction.posted',request_id=posting._request_reference(fact.request_id),before=None,
        after=dict(ledger_cursor=tx.ledger_cursor,movement_count=len(command.movements),movement_type='transfer',
            posting_key=tx.posting_key,reversed_transaction_id=None,status='posted'))
    tx_audit=shared.single(db,AuditEvent,stream_key='inventory',aggregate_type='inventory_transaction',aggregate_id=str(tx.id))
    tx_state=shared.single(db,StateTransitionEvent,aggregate_type='inventory_transaction',aggregate_id=str(tx.id),
        from_status=None,to_status='posted',actor_id=fact.actor_user_id,reason='inventory_transaction_posted',
        idempotency_key=posting._derived_evidence_key('state',tx.id,'posted'),metadata_jsonb=dict(
            ledger_cursor=tx.ledger_cursor,movement_type='transfer',request_reference=posting._request_reference(fact.request_id)))
    tx_outbox=shared.single(db,OutboxEvent,aggregate_type='inventory_transaction',aggregate_id=str(tx.id),
        event_type='inventory.transaction.posted',idempotency_key=posting._derived_evidence_key('outbox',tx.id,'posted'),
        payload_jsonb=dict(transaction_id=str(tx.id),transaction_no=tx.transaction_no,movement_type='transfer',
            ledger_cursor=tx.ledger_cursor,reversed_transaction_id=None))
    for model in (StateTransitionEvent,OutboxEvent):
        if len(_rows(db,model,model.aggregate_type=='inventory_transaction',model.aggregate_id==str(tx.id),limit=2))!=1:invalid()
    if (any(_aware(row.created_at)!=_aware(tx.created_at) for row in (tx_audit,tx_state,tx_outbox))
            or any(_aware(row.occurred_at)!=_aware(tx.posted_at) for row in (tx_audit,tx_state))):invalid()
    _namespace(db,fact,tx)


def _proof(db, fact):
    receipt=db.get(StockOperationReceipt,fact.receipt_id,populate_existing=True)
    tx=db.get(InventoryTransaction,fact.posting_transaction_id,populate_existing=True)
    if receipt is None or tx is None:invalid()
    original=receipts.verified_receipt_history(db,fact=receipt)
    plan=fact.plan_jsonb
    if (set(plan)!={'schema_version','receipt_id','shipment_id','operator_person_id','authorization_version',
                'target_location_id','target_custody_assignment_id','receipt_plan_hash','reason','ledger_cursor','lines'}
            or fact.actor_user_id!=receipt.actor_user_id or fact.operator_person_id!=receipt.operator_person_id
            or fact.shipment_id!=receipt.shipment_id or fact.target_location_id!=original.target_location_id
            or fact.target_custody_assignment_id!=original.target_custody_assignment_id
            or fact.receipt_plan_hash!=receipt.plan_hash or fact.reason!=receipt.reason
            or fact.inbound_no!=f'RET-IN-{fact.id.hex[:20].upper()}' or fact.status!='posted'
            or fact.authorization_version<1 or fact.audit_version<1
            or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}',fact.request_id)
            or any(not re.fullmatch(r'[a-f0-9]{64}',value) for value in (fact.request_hash,fact.plan_hash,fact.idempotency_key_hash))
            or fact.plan_hash!=_hash(plan) or fact.request_hash!=_hash(dict(receipt_id=str(fact.receipt_id),request_id=fact.request_id,plan_hash=fact.plan_hash))
            or type(plan['ledger_cursor']) is not int or plan['ledger_cursor']<receipt.plan_jsonb['ledger_cursor']
            or plan['ledger_cursor']+1!=tx.ledger_cursor or type(plan['authorization_version']) is not int
            or not _aware(receipt.created_at)<=_aware(tx.effective_at)<=_aware(fact.created_at)<=_aware(tx.created_at)<=_aware(tx.posted_at)<=datetime.now(timezone.utc)):
        invalid()
    location=db.get(StockLocation,fact.target_location_id,populate_existing=True)
    custody=db.get(CustodyAssignment,fact.target_custody_assignment_id,populate_existing=True)
    if (location is None or location.location_type!='region' or custody is None or custody.location_id!=location.id
            or custody.custodian_person_id!=fact.operator_person_id or _aware(custody.valid_from)>_aware(tx.effective_at)
            or custody.valid_to is not None and _aware(custody.valid_to)<=_aware(fact.created_at)):invalid()
    accepted=[line for line in _rows(db,StockOperationReceiptLine,StockOperationReceiptLine.receipt_id==receipt.id,
        order=StockOperationReceiptLine.line_no,limit=101) if line.accepted_qty>0]
    lines=_rows(db,StockOperationReturnInboundLine,StockOperationReturnInboundLine.inbound_id==fact.id,order=StockOperationReturnInboundLine.line_no,limit=101)
    if not 1<=len(lines)==len(accepted)<=100:invalid()
    moves=[];planned=[];seen=set()
    for number,(line,origin) in enumerate(zip(lines,accepted),1):
        parcel_line=db.get(StockOperationShipmentLine,origin.shipment_line_id,populate_existing=True)
        departure=db.get(StockOperationOutboundLine,parcel_line.outbound_line_id,populate_existing=True) if parcel_line else None
        source=db.get(StockAccount,line.source_account_id,populate_existing=True)
        target=db.get(StockAccount,line.target_account_id,populate_existing=True)
        if (departure is None or source is None or target is None or line.line_no!=number or line.receipt_line_id!=origin.id
                or line.accepted_qty!=origin.accepted_qty or line.source_account_id!=departure.transit_stock_account_id
                or line.source_account_id==line.target_account_id or source.availability_bucket!='in_transit'
                or target.availability_bucket!='available' or target.location_id!=location.id
                or source.owner_org_id!=location.owner_org_id or target.owner_org_id!=source.owner_org_id
                or target.custodian_person_id!=fact.operator_person_id
                or any(getattr(account,key)!=getattr(line,key) for account in (source,target) for key in ('material_id','condition_code','lot_id'))
                or _aware(line.created_at)!=_aware(fact.created_at)):invalid()
        selected=_rows(db,StockOperationReceiptSerial,StockOperationReceiptSerial.line_id==origin.id,
            StockOperationReceiptSerial.result=='accepted',order=StockOperationReceiptSerial.serial_id)
        serials=_rows(db,StockOperationReturnInboundSerial,StockOperationReturnInboundSerial.line_id==line.id,
            order=StockOperationReturnInboundSerial.serial_id)
        if (len(serials)!=len(selected) or len(serials)>1000 or any(row.inbound_id!=fact.id or row.receipt_serial_id!=proof.id
                or row.serial_id!=proof.serial_id or _aware(row.created_at)!=_aware(fact.created_at) for row,proof in zip(serials,selected))):invalid()
        ids=tuple(row.serial_id for row in serials)
        if seen.intersection(ids):invalid()
        seen.update(ids)
        policies=_rows(db,MaterialInventoryPolicy,MaterialInventoryPolicy.material_id==line.material_id,
            MaterialInventoryPolicy.effective_from<=_aware(tx.effective_at),
            (MaterialInventoryPolicy.effective_to.is_(None))|(MaterialInventoryPolicy.effective_to>_aware(tx.effective_at)),limit=2)
        if len(policies)!=1:invalid()
        policy=policies[0];qty=line.accepted_qty
        if (qty<=0 or qty!=qty.quantize(Decimal(1).scaleb(-policy.quantity_scale))
                or not policy.allow_fraction and qty!=qty.to_integral_value()
                or policy.tracking_mode in ('lot','lot_and_serial') and line.lot_id is None
                or policy.tracking_mode in ('serial','lot_and_serial') and Decimal(len(ids))!=qty
                or policy.tracking_mode not in ('serial','lot_and_serial') and ids):invalid()
        for identifier in ids:
            serial=db.get(InventorySerial,identifier,populate_existing=True)
            if serial is None or serial.material_id!=line.material_id or serial.lot_id!=line.lot_id:invalid()
        moves.append(ReturnInboundLine(origin.id,source.id,target.id,line.material_id,line.condition_code,line.lot_id,qty,ids))
        planned.append(dict(receipt_line_id=str(origin.id),shipment_line_id=str(origin.shipment_line_id),source_account_id=str(source.id),
            target_account_id=str(target.id),material_id=str(line.material_id),condition_code=line.condition_code,
            lot_id=str(line.lot_id) if line.lot_id else None,accepted_qty=format(qty,'.3f'),serial_ids=[str(i) for i in ids]))
    if len(_rows(db,StockOperationReturnInboundSerial,StockOperationReturnInboundSerial.inbound_id==fact.id,limit=100001))!=len(seen):invalid()
    expected_plan=dict(schema_version='1.0',receipt_id=str(receipt.id),shipment_id=str(receipt.shipment_id),
        operator_person_id=str(fact.operator_person_id),authorization_version=fact.authorization_version,
        target_location_id=str(fact.target_location_id),target_custody_assignment_id=str(fact.target_custody_assignment_id),
        receipt_plan_hash=receipt.plan_hash,reason=receipt.reason,ledger_cursor=plan['ledger_cursor'],lines=planned)
    if plan!=expected_plan:invalid()
    command=build_return_inbound_command(receipt_id=receipt.id,inbound_id=fact.id,effective_at=_aware(tx.effective_at),lines=tuple(moves))
    expected_command=dict(source_document_type=command.source_document_type,source_document_id=command.source_document_id,
        posting_key=command.posting_key,movement_type='transfer',effective_at=command.effective_at.isoformat(),movements=[dict(
            from_account_id=str(m.from_account_id),to_account_id=str(m.to_account_id),quantity=format(m.quantity,'.3f'),
            serial_ids=[str(i) for i in m.serial_ids]) for m in command.movements])
    actor=SimpleNamespace(user_id=fact.actor_user_id,person_id=fact.operator_person_id,authorization_version=fact.authorization_version)
    if (fact.command_jsonb!=expected_command or tx.status!='posted' or tx.reversed_transaction_id is not None
            or tx.request_hash!=posting._posting_request_hash(actor,command) or tx.idempotency_key_hash!=fact.idempotency_key_hash
            or any(getattr(tx,key)!=getattr(command,key) for key in ('transaction_no','movement_type','source_document_type','source_document_id','posting_key'))
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id==tx.id).limit(1))):invalid()
    related=_rows(db,InventoryTransaction,
        ((InventoryTransaction.source_document_type=='stock_return_receipt_inbound') &
         (InventoryTransaction.source_document_id==str(fact.id))) |
        (InventoryTransaction.posting_key==command.posting_key) |
        (InventoryTransaction.idempotency_key_hash==fact.idempotency_key_hash),limit=2)
    if len(related)!=1 or related[0].id!=tx.id:invalid()
    actual=_rows(db,InventoryMovement,InventoryMovement.transaction_id==tx.id,order=InventoryMovement.line_no,limit=101)
    if len(actual)!=len(command.movements):invalid()
    for number,(row,wanted) in enumerate(zip(actual,command.movements),1):
        serials=_rows(db,InventoryMovementSerial,InventoryMovementSerial.movement_id==row.id,order=InventoryMovementSerial.serial_id)
        if (row.line_no!=number or row.from_account_id!=wanted.from_account_id or row.to_account_id!=wanted.to_account_id
                or row.quantity!=wanted.quantity or row.external_boundary_code is not None
                or tuple(sn.serial_id for sn in serials)!=wanted.serial_ids
                or any(sn.transaction_id!=tx.id or _aware(sn.created_at)!=_aware(tx.created_at) for sn in serials)
                or _aware(row.created_at)!=_aware(tx.created_at)):invalid()
    link=shared.single(db,StockOperationReturnInboundPosting,inbound_id=fact.id)
    if link.inventory_transaction_id!=tx.id or _aware(link.created_at)!=_aware(fact.created_at):invalid()
    _stock_before(db,command,tx);_evidence(db,fact,tx,command,actor)
    return document(fact)


def inbound_result(db, *, actor, fact, replayed=False):
    with db.no_autoflush:
        snapshot=inventory._projection_snapshot(db);cursor=material_audit_cursor(db)
        current,_=authorize_receipt(db,actor=actor,receipt_id=fact.receipt_id,action='read')
        try:
            fresh=db.get(StockOperationReturnInbound,fact.id,populate_existing=True)
            if fresh is None or fresh.actor_user_id!=current.user_id or fresh.operator_person_id!=current.person_id:invalid()
            result=_proof(db,fresh)
        except (TypeError,ValueError,KeyError,AttributeError,InvalidOperation,AuditChainError):invalid()
        final,_=authorize_receipt(db,actor=current,receipt_id=fact.receipt_id,action='read')
        if final!=current or material_audit_cursor(db)!=cursor:
            _fail('stock_return_inbound_lookup_changed','入账证据或权限在核验期间变化，请重新查询原请求')
        inventory._ensure_projection_snapshot_current(db,snapshot)
        return {**result,'replayed':replayed}
