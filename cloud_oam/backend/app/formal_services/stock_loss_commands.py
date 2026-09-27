"""Atomic loss submission candidate; public activation requires its PG proof.

The caller owns commit/rollback. This module is deliberately not routed while
the complete database submission and review/disposition boundaries are built.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..foundation_models import OutboxEvent, StateTransitionEvent
from ..inventory_models import StockAccount
from ..stock_operation_models import StockLossFile, StockOperationOrder as Order, StockOperationLine as Line, StockOperationSerial as Serial
from ..stock_loss_schemas import StockLossSubmitIn
from . import inventory_posting as posting, stock_loss_facts as facts, stock_loss_plan as plan, stock_loss_sources as sources
from .audit_chain import append_audit_event
from .notification_events import record_business_notification
from .stock_return_commands import _fresh_request


def _record(db, *, actor, order):
    body=facts.payload(order);at=order.created_at;kind=facts.KIND;aggregate=facts.AGGREGATE
    append_audit_event(db,stream_key='inventory',actor_user_id=actor.user_id,action=kind,
        aggregate_type=aggregate,aggregate_id=str(order.id),before_jsonb={},after_jsonb=body,
        request_id=order.request_id,occurred_at=at,created_at=at)
    db.add(OutboxEvent(event_type=kind,aggregate_type=aggregate,aggregate_id=str(order.id),payload_jsonb=body,
        idempotency_key=kind+':'+str(order.id),available_at=at,created_at=at,updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=aggregate,aggregate_id=str(order.id),from_status=None,
        to_status='submitted',actor_id=actor.user_id,reason=kind,idempotency_key=kind+':'+str(order.id),
        occurred_at=at,metadata_jsonb=body,created_at=at))
    record_business_notification(db,event_type=kind,business_type=aggregate,business_id=order.id,
        dedup_key='stock-loss-notification:'+kind+':'+str(order.id),payload=body,
        recipient_person_id=order.requester_id,occurred_at=at,now=at)
    db.flush()


def submit_loss(db, *, actor, request):
    request=StockLossSubmitIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current=sources.authorize(db,actor)
    if request.operator_person_id!=current.person_id:
        sources._fail('operator_mismatch','操作人必须是当前登录人员',403)
    key=posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    value=plan.intent(request)
    existing=db.scalar(select(Order).where(Order.idempotency_key_hash==key).execution_options(populate_existing=True))
    if existing is not None:
        if (existing.operation_type!='loss_report' or existing.actor_user_id!=current.user_id
                or existing.requester_id!=current.person_id):
            sources._fail('stock_loss_not_found','本人原报损单不存在',404)
        if (existing.command_jsonb!=value or existing.request_id!=request.request_id
                or existing.plan_hash!=request.expected_plan_hash):
            sources._fail('idempotency_conflict','原请求键已绑定其他报损内容，请回读原单')
        return facts.order_result(db,actor=current,order=existing)
    _fresh_request(db,actor=current,key=key,request_id=request.request_id)
    prepared,_=plan.preview_loss(db,actor=current,request=request)
    graph=posting.InventoryPostingCommand(transaction_no='loss-graph',movement_type='freeze',
        source_document_type='stock_operation_loss',source_document_id=str(current.person_id),
        posting_key='loss-graph',effective_at=datetime.now(timezone.utc),movements=tuple(
            posting.InventoryMovementCommand(from_account_id=row.source.stock_account_id,to_account_id=None,
                quantity=next(line.quantity for line in request.lines if line.stock_account_id==row.source.stock_account_id),
                serial_ids=tuple(sn.serial_id for sn in row.selected_serials)) for row in prepared.lines))
    posting._plan_and_lock_terminal_opening_graphs(db,command=graph,current_actor_user_id=current.user_id)
    prepared,document=plan.preview_loss(db,actor=current,request=request)
    if prepared.plan_hash!=request.expected_plan_hash:
        sources._fail('stock_loss_plan_changed','报损方案已变化，请重新预检并确认')
    if db.scalar(select(StockLossFile.id).where(StockLossFile.file_id.in_(request.evidence_file_ids)).limit(1)):
        sources._fail('stock_loss_file_already_bound','报损附件已绑定原单，请上传本次证据或回读原请求')
    at=datetime.now(timezone.utc)
    parent=Order(id=uuid4(),operation_no='LOSS-'+key[:24].upper(),operation_type='loss_report',status='submitted',
        source_location_id=prepared.location_id,requester_id=current.person_id,actor_user_id=current.user_id,
        authorization_version=current.authorization_version,reason=request.reason,request_id=request.request_id,
        idempotency_key_hash=key,request_hash=sources._hash(value),plan_hash=prepared.plan_hash,
        command_jsonb=value,plan_jsonb=document,created_at=at)
    lines=[]
    for number,wanted in enumerate(sorted(request.lines,key=lambda line:str(line.stock_account_id)),1):
        source=db.get(StockAccount,wanted.stock_account_id,populate_existing=True)
        dimensions={k:getattr(source,k) for k in
            ('owner_org_id','custodian_person_id','location_id','material_id','condition_code','lot_id')}
        held=db.scalar(select(StockAccount).filter_by(**dimensions,availability_bucket='frozen'))
        if held is None:
            held=StockAccount(id=uuid4(),**dimensions,availability_bucket='frozen',created_at=at,updated_at=at)
            db.add(held);db.flush()
        line=Line(id=uuid4(),operation_id=parent.id,operation_type='loss_report',line_no=number,
            stock_account_id=source.id,reserved_account_id=held.id,material_id=source.material_id,
            quantity=wanted.quantity,target_condition=source.condition_code,reason=request.reason,created_at=at)
        lines.append((line,wanted))
    moves=tuple(posting.InventoryMovementCommand(from_account_id=line.stock_account_id,to_account_id=line.reserved_account_id,
        quantity=line.quantity,serial_ids=tuple(sorted((proof.serial_id for proof in wanted.serial_verifications),key=str)))
        for line,wanted in lines)
    posted=posting.post_inventory_transaction(db,actor=current,command=facts.posting_command(parent,at=at,movements=moves),
        idempotency_key=request.idempotency_key,request_id=request.request_id,
        permission_resource='stock_operation',permission_action='submit_loss')
    parent.posting_transaction_id=posted.transaction_id
    db.add(parent);db.flush()
    for line,wanted in lines:
        db.add(line);db.flush()
        db.add_all(Serial(line_id=line.id,serial_id=proof.serial_id,sku_verified=True,qr_verified=True,created_at=at)
            for proof in wanted.serial_verifications)
    db.add_all(StockLossFile(operation_id=parent.id,operation_type='loss_report',file_id=item.file_id,
        metadata_sha256=next(row['metadata_sha256'] for row in document['evidence'] if row['file_id']==str(item.file_id)),
        created_at=at) for item in prepared.evidence)
    db.flush();_record(db,actor=current,order=parent)
    return facts.order_result(db,actor=current,order=parent)
