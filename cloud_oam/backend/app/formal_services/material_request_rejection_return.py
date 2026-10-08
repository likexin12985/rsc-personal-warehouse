"""Rejection-return registration and original-request recovery.

The public HTTP boundary owns the transaction;
registration never consumes, releases or posts any inventory quantity.
"""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import or_, select
from app.demand_models import MaterialRequest
from app.formal_access import lock_formal_principal_graph
from app.foundation_models import AuditEvent
from app.inventory_models import Receipt, ShipmentLine, OutboundPosting, StockReservation, StockAccount, MaterialInventoryPolicy, InventorySerial
from app.material_request_closure_schema import closures
from app.material_request_remaining_cancel_schema import cancellations
from app.material_request_rejection_return_schema import returns, return_serials
from app.material_request_rejection_return_schemas import RejectionReturnIn, RejectionReturnOut
from . import material_request_query as query
from . import material_request_lifecycle as lifecycle
from . import material_request_my_receipt as receipts
from . import material_request_my_inbound_candidates as history
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError

ACTION='material_request.register_rejection_return'
AGGREGATE='material_request_rejection_return'
SCHEMA='rsc.material_request_rejection_return.v1'


def _fail(code,category,message):
    raise query.MaterialRequestReadError('rejection_return_'+code,category,message)


def _context(db,actor,request_id,*,write=False):
    if write:
        lock_formal_principal_graph(db,(actor.user_id,))
    context,request=receipts._context(db,actor,request_id)
    if write:
        request=db.scalar(select(MaterialRequest).where(MaterialRequest.id==request_id)
            .with_for_update().execution_options(populate_existing=True))
        context,request=receipts._context(db,actor,request_id)
    if (request.requester_person_id!=context.principal.person_id
            or request.requester_user_id!=context.principal.user_id):
        _fail('not_found','not_found','原申请人需求不存在')
    return context,request


def _authority(db,actor,request):
    requester=lifecycle._require_requester_context(db,actor,'read',lifecycle._database_now(db))
    lifecycle._require_owned_request(request,requester)
    selected=replace(actor,assignments=(requester.technician_grant,),entitlements=tuple(
        e for e in actor.entitlements if e.assignment_id==requester.technician_grant.assignment_id))
    for principal in (actor,selected):
        if not principal.allows(db,'stock_operation','submit_return',target_scope_type='person',target_scope_id=str(actor.person_id)):
            _fail('forbidden','forbidden','没有本人退回登记权限')
    return requester.technician_grant


def _key(actor,request_id,key,secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret),actor.user_id,
        f'/api/v1/material-requests/{request_id}/rejection-returns',lifecycle._require_idempotency_key(key))


def _digest(actor,request_id,payload):
    return lifecycle._canonical_hash(dict(request_id=str(request_id),actor_user_id=actor.user_id,
        actor_person_id=str(actor.person_id),input=payload.model_dump(mode='json')))


def _origin(db,context,request,payload):
    receipt=db.get(Receipt,payload.receipt_id,populate_existing=True)
    if receipt is None:
        _fail('receipt_not_found','not_found','本人原拒收记录不存在')
    checked=history._item(db,context,request,receipt)
    if checked.detail.receipt_request_hash!=payload.receipt_request_hash:
        _fail('receipt_changed','conflict','原拒收指纹不一致，请重新核验')
    original=receipts._result(db,context,request,receipt,replayed=True)
    detail=next((x for x in checked.detail.lines if x.receipt_line_id==payload.receipt_line_id),None)
    if detail is None:
        _fail('line_invalid','precondition_failed','请选择原验收中有拒收数量的明细')
    return _origin_facts(db,request,payload,original,detail.tracking_mode)


def _origin_facts(db,request,payload,original,tracking_mode):
    """Use an already verified original receipt, independent of today's reader."""
    receipt=db.get(Receipt,payload.receipt_id,populate_existing=True)
    if original.receipt_id!=payload.receipt_id or original.request_hash!=payload.receipt_request_hash:
        _fail('receipt_changed','conflict','原拒收指纹不一致，请重新核验')
    line=next((x for x in original.lines if x.receipt_line_id==payload.receipt_line_id),None)
    if line is None or line.rejected_qty<=0:
        _fail('line_invalid','precondition_failed','请选择原验收中有拒收数量的明细')
    shipment_line=db.get(ShipmentLine,line.shipment_line_id)
    outbound=db.get(OutboundPosting,shipment_line.outbound_posting_id)
    reservation=db.get(StockReservation,outbound.reservation_id)
    source=db.get(StockAccount,reservation.source_stock_account_id)
    transit=db.get(StockAccount,outbound.target_stock_account_id)
    if (source is None or transit is None or source.availability_bucket!='available'
            or transit.availability_bucket!='in_transit' or source.material_id!=transit.material_id
            or source.owner_org_id!=transit.owner_org_id):
        _fail('origin_invalid','service_unavailable','原可用来源与在途账户证据不完整')
    tracked=tracking_mode in ('serial','lot_and_serial')
    when=history.versions._time(receipt.received_at)
    policies=tuple(db.scalars(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id==source.material_id,
        MaterialInventoryPolicy.effective_from<=when,
        or_(MaterialInventoryPolicy.effective_to.is_(None),MaterialInventoryPolicy.effective_to>when))))
    if len(policies)!=1: _fail('policy_invalid','service_unavailable','原验收时点的数量规则不唯一')
    policy=policies[0]
    if policy.tracking_mode!=tracking_mode:
        _fail('policy_invalid','service_unavailable','原验收追踪规则不一致')
    for quantity, ids in ((line.accepted_qty,line.accepted_serial_ids),(line.rejected_qty,line.rejected_serial_ids)):
        if (tracked and quantity!=len(ids)) or (not tracked and ids):
            _fail('origin_invalid','service_unavailable','原验收数量与SN策略不一致')
    all_ids=line.accepted_serial_ids+line.rejected_serial_ids
    serial_rows=tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(all_ids)))) if all_ids else ()
    if len(serial_rows)!=len(all_ids) or any(sn.material_id!=source.material_id or sn.lot_id!=source.lot_id for sn in serial_rows):
        _fail('origin_invalid','service_unavailable','原验收SN物料或批次不一致')
    for quantity in (line.accepted_qty,line.rejected_qty,Decimal(payload.quantity)):
        if quantity!=quantity.quantize(Decimal(1).scaleb(-policy.quantity_scale)) or (not policy.allow_fraction and quantity!=quantity.to_integral_value()):
            _fail('precision_invalid','precondition_failed','退回数量必须符合原验收时点的物料精度')
    if (Decimal(payload.quantity)>line.rejected_qty or not set(payload.serial_ids)<=set(line.rejected_serial_ids)
            or (tracked and Decimal(payload.quantity)!=len(payload.serial_ids))
            or (not tracked and payload.serial_ids)):
        _fail('quantity_invalid','precondition_failed','退回数量或SN必须准确属于本次拒收')
    return dict(request_id=str(request.id),revision_id=str(outbound.revision_id),receipt_id=str(receipt.id),
        receipt_line_id=str(line.receipt_line_id),receipt_request_hash=receipt.request_hash,
        shipment_line_id=str(shipment_line.id),outbound_posting_id=str(outbound.id),reservation_id=str(reservation.id),
        in_transit_account_id=str(transit.id),return_source_account_id=str(source.id),
        rejected_qty=format(line.rejected_qty,'.3f'),condition=line.condition,tracking_mode=tracking_mode,
        quantity_scale=policy.quantity_scale,allow_fraction=policy.allow_fraction,
        rejected_serial_ids=sorted(str(s) for s in line.rejected_serial_ids))


def _audit(row):
    return {k:str(row[k]) for k in ('id','request_id','receipt_id','receipt_line_id','actor_person_id',
        'actor_role_assignment_id')} | {k:row[k] for k in ('return_no','request_version','authorization_version',
        'idempotency_key_hash','request_hash','evidence_sha256')} | {'quantity':format(row['quantity'],'.3f')}


def _result(db,context,request,row,*,replayed):
    try:
        payload=RejectionReturnIn.model_validate(row['evidence_jsonb']['input'])
    except (ValueError,KeyError,TypeError):
        _fail('history_invalid','service_unavailable','原退回输入证据不一致')
    origin=_origin(db,context,request,payload)
    return _registration_result(db,context.principal,request,row,origin,replayed=replayed)


def _registration_result(db,subject,request,row,origin,*,replayed):
    try:
        payload=RejectionReturnIn.model_validate(row['evidence_jsonb']['input'])
        expected=dict(schema=SCHEMA,input=payload.model_dump(mode='json'),origin=origin)
        if (row['actor_user_id']!=subject.user_id or row['actor_person_id']!=subject.person_id
                or row['evidence_jsonb']!=expected or row['evidence_sha256']!=lifecycle._canonical_hash(expected)
                or row['request_hash']!=_digest(subject,request.id,payload)
                or row['request_version']!=payload.expected_request_version or row['reason']!=payload.reason
                or row['quantity']!=Decimal(payload.quantity)
                or row['request_version']>request.version or not lifecycle._same_timestamp(row['created_at'],row['occurred_at'])):
            raise ValueError('original request mismatch')
        for key in ('request_id','revision_id','receipt_id','receipt_line_id','shipment_line_id',
                    'outbound_posting_id','reservation_id','in_transit_account_id','return_source_account_id','receipt_request_hash'):
            if str(row[key])!=origin[key]: raise ValueError('origin mismatch')
        serials=tuple(db.scalars(select(return_serials.c.serial_id).where(return_serials.c.return_id==row['id']).order_by(return_serials.c.serial_id)))
        if serials!=payload.serial_ids: raise ValueError('serial mismatch')
        audits=tuple(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type==AGGREGATE,AuditEvent.aggregate_id==str(row['id']))))
        if (len(audits)!=1 or audits[0].action!=ACTION or audits[0].actor_user_id!=row['actor_user_id']
                or audits[0].request_id!=row['trace_request_id'] or audits[0].after_jsonb!=_audit(row)
                or audits[0].before_jsonb!={'rejection_return':'not_registered'}
                or not lifecycle._same_timestamp(audits[0].occurred_at,row['occurred_at'])):
            raise ValueError('audit mismatch')
        verify_audit_event_in_read_snapshot(db,stream_key='material_request',event_id=audits[0].id)
        return RejectionReturnOut(return_id=row['id'],return_no=row['return_no'],request_id=request.id,
            request_version=row['request_version'],receipt_id=row['receipt_id'],receipt_line_id=row['receipt_line_id'],
            receipt_request_hash=row['receipt_request_hash'],quantity=payload.quantity,serial_ids=serials,
            registered_at=history.versions._time(row['occurred_at']),
            request_hash=row['request_hash'],replayed=replayed)
    except (ValueError,KeyError,TypeError,AuditChainError):
        _fail('history_invalid','service_unavailable','原退回请求、来源、逐件SN或审计证据不一致')


def register_rejection_return(db,*,actor,request_id,payload,idempotency_key,secret,trace_request_id):
    payload=RejectionReturnIn.model_validate(payload)
    trace=lifecycle._require_trace_request_id(trace_request_id)
    context,request=_context(db,actor,request_id,write=True)
    actor=context.principal; key=_key(actor,request_id,idempotency_key,secret); digest=_digest(actor,request_id,payload)
    prior=db.execute(select(returns).where(returns.c.idempotency_key_hash==key)).mappings().one_or_none()
    if prior is not None:
        if prior['request_hash']!=digest: _fail('key_reused','conflict','原键已绑定不同退回内容，请核验原请求')
        return _result(db,context,request,prior,replayed=True)
    grant=_authority(db,actor,request)
    if request.version!=payload.expected_request_version:
        _fail('version_changed','conflict','需求版本已变化，请重新核对')
    if (request.status not in ('approved','partially_approved')
            or db.scalar(select(closures.c.id).where(closures.c.request_id==request.id))
            or db.scalar(select(cancellations.c.id).where(cancellations.c.request_id==request.id))):
        _fail('closed','precondition_failed','当前需求不能新增拒收退回')
    if db.scalar(select(returns.c.id).where(returns.c.actor_user_id==actor.user_id,returns.c.trace_request_id==trace)):
        _fail('trace_reused','conflict','请求编号已绑定其他退回，请核验原结果')
    origin=_origin(db,context,request,payload)
    previous=tuple(db.execute(select(returns).where(returns.c.receipt_line_id==payload.receipt_line_id).order_by(returns.c.id).limit(1001)).mappings())
    if len(previous)>1000: _fail('history_limit','precondition_failed','该拒收行的退回记录超过完整核验上限')
    for row in previous: _result(db,context,request,row,replayed=True)
    from .material_request_rejection_progress import _checked_chain
    active = []
    for row in previous:
        chain = _checked_chain(db, context, request, row)
        if not chain or chain[0].action != 'cancel_registration':
            active.append(row)
    if sum((row['quantity'] for row in active),Decimal(0))+Decimal(payload.quantity)>Decimal(origin['rejected_qty']):
        _fail('quantity_exceeded','conflict','累计退回登记超过原拒收数量')
    used=set(db.scalars(select(return_serials.c.serial_id).where(return_serials.c.return_id.in_([row['id'] for row in active]))))
    if used.intersection(payload.serial_ids): _fail('serial_used','conflict','所选拒收SN已经登记退回')
    now=lifecycle._database_now(db)
    from uuid import UUID
    evidence=dict(schema=SCHEMA,input=payload.model_dump(mode='json'),origin=origin)
    row=dict(id=uuid4(),return_no='RJR-'+uuid4().hex.upper(),request_version=request.version,
        actor_user_id=actor.user_id,actor_person_id=actor.person_id,actor_role_assignment_id=grant.assignment_id,
        authorization_version=actor.authorization_version,quantity=Decimal(payload.quantity),
        idempotency_key_hash=key,request_hash=digest,evidence_sha256=lifecycle._canonical_hash(evidence),
        trace_request_id=trace,reason=payload.reason,evidence_jsonb=evidence,occurred_at=now,created_at=now,
        receipt_request_hash=payload.receipt_request_hash,
        **{k:UUID(origin[k]) for k in ('request_id','revision_id','receipt_id','receipt_line_id','shipment_line_id',
            'outbound_posting_id','reservation_id','in_transit_account_id','return_source_account_id')})
    _authority(db,actor,request)
    db.execute(returns.insert().values(**row))
    if payload.serial_ids:
        db.execute(return_serials.insert(),[dict(return_id=row['id'],receipt_line_id=payload.receipt_line_id,serial_id=sid) for sid in payload.serial_ids])
    append_audit_event(db,stream_key='material_request',actor_user_id=actor.user_id,action=ACTION,aggregate_type=AGGREGATE,
        aggregate_id=str(row['id']),before_jsonb={'rejection_return':'not_registered'},after_jsonb=_audit(row),
        request_id=trace,occurred_at=now,created_at=now)
    db.flush()
    return _result(db,context,request,row,replayed=False)


def rejection_return_command_status(db,*,actor,request_id,idempotency_key=None,secret=None,trace_request_id=None,request_fingerprint=None):
    if (idempotency_key is None)==(trace_request_id is None):
        _fail('lookup_invalid','invalid_request','请提供唯一的原请求查询坐标')
    with db.no_autoflush:
        context,request=_context(db,actor,request_id)
        observed_version=request.version
        statement=select(returns).where(returns.c.request_id==request_id,returns.c.actor_user_id==context.principal.user_id)
        statement=statement.where(returns.c.idempotency_key_hash==_key(context.principal,request_id,idempotency_key,secret)) if idempotency_key else statement.where(returns.c.trace_request_id==lifecycle._require_trace_request_id(trace_request_id))
        row=db.execute(statement).mappings().one_or_none()
        result=_result(db,context,request,row,replayed=True) if row else None
        if row is not None and request_fingerprint is not None and request_fingerprint != lifecycle._canonical_hash(row['evidence_jsonb']['input']):
            _fail('fingerprint_mismatch','conflict','原退回请求指纹不一致')
        latest,current=_context(db,actor,request_id)
        if latest.principal!=context.principal or current.version!=observed_version:
            _fail('context_changed','precondition_failed','读取期间身份或需求发生变化')
        return result
