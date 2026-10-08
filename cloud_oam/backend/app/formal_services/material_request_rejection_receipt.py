"""Source-custodian acceptance, independent of warehouse stock posting.

Formal 0174 facts; public HTTP mounting is separate. Caller owns the
transaction. Original key/trace recovery is SELECT-only and uses current read
scope, never the sender's current login or today's write permission.
"""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import or_, select

from app.demand_models import MaterialRequest
from app.formal_access import lock_formal_principal_graph
from app.foundation_models import AuditEvent, FileObject
from app.inventory_models import CustodyAssignment, FormalMaterial, InventorySerial, MaterialInventoryPolicy, SerialCurrentPosition, ReceiptException
from app.stock_operation_models import StockOperationReceiptException
from app.material_request_rejection_receipt_schema import receipts, serials, exceptions
from app.material_request_rejection_receipt_schemas import RejectionReceiptIn, RejectionReceiptOut
from . import material_request_rejection_receiving as receiving
from . import material_request_rejection_return as registration
from . import material_request_lifecycle as lifecycle
from . import inventory_posting as posting
from .formal_files import is_available_formal_file_for_purpose, _lock_file
from .postgresql_lock_graph import lock_inventory_reference_graph, lock_inventory_serial_graph
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError
from .material_request_rejection_history import verified_rejection_history

SCHEMA='rsc.material_request_rejection_receipt.v1'
AGGREGATE='material_request_rejection_receipt'
ACTION='material_request.rejection_return.receive'
AMOUNTS=('accepted_qty','rejected_qty','damaged_qty','shortage_qty')


def _fail(code,category,message):
    registration._fail('warehouse_receipt_'+code,category,message)


def _time(value):
    return registration.history.versions._time(value)


def _context(db,actor,return_id,*,write=False):
    if write:
        posting._lock_inventory_ledger_head_for_atomic_batch(db)
        lock_formal_principal_graph(db,(actor.user_id,))
    context=receiving._authorized(db,actor,return_id)
    if write:
        db.scalar(select(MaterialRequest).where(MaterialRequest.id==context[5].id)
            .with_for_update().execution_options(populate_existing=True))
        context=receiving._authorized(db,actor,return_id)
    return context


def _authority(db,context):
    actor,_,_,location,_,_,stamp=context
    grant=next(g for g in actor.assignments if g.assignment_id==stamp[1])
    chosen=replace(actor,assignments=(grant,),entitlements=tuple(e for e in actor.entitlements if e.assignment_id==grant.assignment_id))
    if not all(p.allows(db,'stock_operation','receive_return',target_scope_type='organization',
        target_scope_id=str(location.owner_org_id)) for p in (actor,chosen)):
        _fail('forbidden','forbidden','没有当前来源仓实际验收权限')
    return grant


def _key(actor,return_id,key,secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret),actor.user_id,
        f'/api/v1/rejection-returns/{return_id}/warehouse-receipts',lifecycle._require_idempotency_key(key))


def _digest(actor,return_id,payload):
    return lifecycle._canonical_hash(dict(return_id=str(return_id),actor_user_id=actor.user_id,
        actor_person_id=str(actor.person_id),input=payload.model_dump(mode='json')))


def _evidence(db,*,parent,request,source,payload,person_id,user_id,authorization_version,recorded_at,new):
    checked=verified_rejection_history(db,request=request,parent=parent)
    if (len(checked.events)!=2 or checked.events[-1].action!='handover'
        or checked.events[-1].event_id!=payload.handover_id
        or checked.events[-1].request_hash!=payload.handover_request_hash
        or parent['request_hash']!=payload.registration_request_hash):
        _fail('handover_changed','precondition_failed','必须绑定本退回的准确承运交接及原登记')
    if (not checked.events[-1].physical_at<=payload.received_at<=_time(recorded_at)
        or _time(recorded_at)<checked.events[-1].recorded_at):
        _fail('time_invalid','precondition_failed','实际验收时间须位于交运之后且不晚于登记时间')
    custody=db.get(CustodyAssignment,payload.custody_assignment_id,populate_existing=True)
    if (custody is None or custody.location_id!=source.location_id or custody.custodian_person_id!=person_id
        or _time(custody.valid_from)>_time(recorded_at)
        or custody.valid_to is not None and _time(custody.valid_to)<=_time(recorded_at)):
        _fail('custody_invalid','precondition_failed','验收登记时点的来源仓保管责任不匹配')
    policies=tuple(db.scalars(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id==source.material_id,
        MaterialInventoryPolicy.effective_from<=recorded_at,
        or_(MaterialInventoryPolicy.effective_to.is_(None),MaterialInventoryPolicy.effective_to>recorded_at)).limit(2)))
    if len(policies)!=1 or policies[0].tracking_mode!=checked.origin['tracking_mode']:
        _fail('policy_changed','precondition_failed','来源追踪规则已改变，请先处理历史策略差异')
    policy=policies[0]; amounts=payload.amounts; tracked=policy.tracking_mode in ('serial','lot_and_serial')
    groups=(tuple(p.serial_id for p in amounts.accepted_serial_verifications),amounts.rejected_serial_ids,
        amounts.damaged_serial_ids,amounts.shortage_serial_ids)
    for quantity,ids in zip((getattr(amounts,n) for n in AMOUNTS),groups):
        if (quantity!=quantity.quantize(Decimal(1).scaleb(-policy.quantity_scale))
            or not policy.allow_fraction and quantity!=quantity.to_integral_value()
            or tracked and quantity!=len(ids) or not tracked and ids
            or not set(ids)<=set(checked.registration.serial_ids)):
            _fail('quantity_invalid','precondition_failed','数量精度或SN与原退回登记不一致')
    material=db.get(FormalMaterial,source.material_id)
    if amounts.accepted_qty and (material is None or payload.observed_sku_code!=material.sku_code):
        _fail('sku_invalid','precondition_failed','实物核对SKU必须与原退回物料一致')
    if checked.origin['condition']=='damaged' and amounts.damaged_qty!=amounts.accepted_qty:
        _fail('damage_invalid','precondition_failed','原破损拒收份额不能自动按完好件验收')
    for proof in amounts.accepted_serial_verifications:
        serial=db.get(InventorySerial,proof.serial_id,populate_existing=True)
        if (serial is None or serial.material_id!=source.material_id or serial.lot_id!=source.lot_id
            or serial.serial_no!=proof.serial_no or serial.qr_code!=proof.qr_code or material.sku_code!=proof.sku_code):
            _fail('scan_invalid','precondition_failed','接受的实物必须匹配原物料、SN及二维码')
        if new:
            position=db.get(SerialCurrentPosition,serial.id,populate_existing=True)
            if serial.lifecycle_status!='active' or position is None or position.stock_account_id!=parent['in_transit_account_id']:
                _fail('position_changed','precondition_failed','接受SN已不在原退回在途账户')
    files=[]
    for identifier in sorted({e.evidence_file_id for e in amounts.exceptions},key=str):
        file=db.get(FileObject,identifier,populate_existing=True)
        if (not is_available_formal_file_for_purpose(file,purpose='receipt_exception_evidence',uploader_user_id=user_id)
            or file.metadata_jsonb.get('uploader_person_id')!=str(person_id)
            or file.metadata_jsonb.get('authorization_version')!=authorization_version):
            _fail('file_invalid','precondition_failed','异常凭证须为本次办理身份已完成上传的验收证据')
        completed=datetime.fromisoformat(file.metadata_jsonb['completion']['verified_at'])
        if not _time(file.created_at)<=_time(completed)<=_time(recorded_at):
            _fail('file_time_invalid','precondition_failed','异常凭证须在本次验收登记前完成上传')
        files.append(dict(id=str(file.id),sha256=file.sha256,size_bytes=file.size_bytes,mime_type=file.mime_type,
            metadata_sha256=lifecycle._canonical_hash(file.metadata_jsonb)))
    return dict(schema=SCHEMA,input=payload.model_dump(mode='json'),origin=dict(
        return_id=str(parent['id']),registration_evidence_sha256=parent['evidence_sha256'],
        handover_id=str(checked.events[-1].event_id),handover_request_hash=checked.events[-1].request_hash,
        request_id=str(request.id),in_transit_account_id=str(parent['in_transit_account_id']),
        return_source_account_id=str(source.id),target_location_id=str(source.location_id),
        material_id=str(source.material_id),lot_id=str(source.lot_id) if source.lot_id else None,
        source_condition=source.condition_code,policy_id=str(policy.id),tracking_mode=policy.tracking_mode,
        quantity_scale=policy.quantity_scale,allow_fraction=policy.allow_fraction),files=files)


def _serial_rows(payload):
    proofs={p.serial_id:p for p in payload.amounts.accepted_serial_verifications}
    result=[]
    for outcome,ids in (('accepted',tuple(proofs)),('rejected',payload.amounts.rejected_serial_ids),('shortage',payload.amounts.shortage_serial_ids)):
        for sid in ids:
            proof=proofs.get(sid)
            result.append(dict(serial_id=sid,result=outcome,damaged=sid in payload.amounts.damaged_serial_ids,
                **{k:getattr(proof,k) if proof else None for k in ('sku_code','serial_no','qr_code')}))
    return sorted(result,key=lambda x:str(x['serial_id']))


def _audit(row):
    return {k:str(row[k]) for k in ('id','return_id','request_id','handover_id','actor_person_id','actor_role_assignment_id',
        'target_location_id','custody_assignment_id')} | {k:row[k] for k in ('request_version','authorization_version',
        'request_hash','idempotency_key_hash','evidence_sha256')} | {k:format(row[k],'.3f') for k in AMOUNTS}


def _verify(db,context,row):
    _,parent,source,_,_,request,_=context
    try:
        payload=RejectionReceiptIn.model_validate(row['evidence_jsonb']['input'])
        subject=registration.receipts._ReceiptSubject(row['actor_user_id'],row['actor_person_id'])
        evidence=_evidence(db,parent=parent,request=request,source=source,payload=payload,person_id=subject.person_id,
            user_id=subject.user_id,authorization_version=row['authorization_version'],recorded_at=row['recorded_at'],new=False)
        if (row['return_id']!=parent['id'] or row['request_id']!=request.id
            or not parent['request_version']<=row['request_version']<=request.version
            or row['request_version']!=payload.expected_request_version or row['target_location_id']!=source.location_id
            or row['custody_assignment_id']!=payload.custody_assignment_id or row['handover_id']!=payload.handover_id
            or row['registration_request_hash']!=payload.registration_request_hash or row['handover_request_hash']!=payload.handover_request_hash
            or _time(row['received_at'])!=payload.received_at or row['reason']!=payload.reason
            or row['observed_sku_code']!=payload.observed_sku_code
            or any(row[k]!=getattr(payload.amounts,k) for k in AMOUNTS)
            or row['request_hash']!=_digest(subject,parent['id'],payload)
            or row['evidence_jsonb']!=evidence or row['evidence_sha256']!=lifecycle._canonical_hash(evidence)):
            raise ValueError('receipt evidence mismatch')
        stored=[{k:r[k] for k in ('serial_id','result','damaged','sku_code','serial_no','qr_code')} for r in
            db.execute(select(serials).where(serials.c.receipt_id==row['id']).order_by(serials.c.serial_id)).mappings()]
        if stored!=_serial_rows(payload): raise ValueError('receipt serial rows mismatch')
        recorded=[dict(r) for r in db.execute(select(exceptions.c.exception_type,exceptions.c.description,exceptions.c.evidence_file_id)
            .where(exceptions.c.receipt_id==row['id']).order_by(exceptions.c.exception_type)).mappings()]
        if recorded!=[e.model_dump() for e in payload.amounts.exceptions]: raise ValueError('receipt exception rows mismatch')
        audits=tuple(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type==AGGREGATE,AuditEvent.aggregate_id==str(row['id']))))
        if (len(audits)!=1 or audits[0].action!=ACTION or audits[0].actor_user_id!=row['actor_user_id']
            or audits[0].request_id!=row['trace_request_id'] or audits[0].before_jsonb!={'warehouse_receipt':'not_registered'}
            or audits[0].after_jsonb!=_audit(row) or _time(audits[0].occurred_at)!=_time(row['recorded_at'])
            or _time(audits[0].created_at)!=_time(row['recorded_at'])):
            raise ValueError('receipt audit mismatch')
        verify_audit_event_in_read_snapshot(db,stream_key='material_request',event_id=audits[0].id)
        return RejectionReceiptOut(receipt_id=row['id'],return_id=parent['id'],request_id=request.id,request_version=row['request_version'],
            receiver_person_id=subject.person_id,target_location_id=row['target_location_id'],custody_assignment_id=row['custody_assignment_id'],
            handover_id=row['handover_id'],received_at=payload.received_at,recorded_at=_time(row['recorded_at']),
            amounts=payload.amounts,reason=payload.reason,observed_sku_code=payload.observed_sku_code,request_hash=row['request_hash'],replayed=True)
    except (ValueError,TypeError,KeyError,AuditChainError,registration.query.MaterialRequestReadError):
        _fail('history_invalid','service_unavailable','原仓库验收、逐件SN或审计证据不一致')


def _history(db,context):
    rows=tuple(db.execute(select(receipts).where(receipts.c.return_id==context[1]['id'])
        .order_by(receipts.c.recorded_at,receipts.c.id).limit(1001)).mappings())
    if len(rows)>1000: _fail('history_limit','precondition_failed','退回验收超过完整核验上限')
    results=tuple(_verify(db,context,row) for row in rows)
    used=set(); confirmed=Decimal(0)
    for result in results:
        a=result.amounts; ids={p.serial_id for p in a.accepted_serial_verifications}|set(a.rejected_serial_ids)
        if used & ids: _fail('history_invalid','service_unavailable','退回SN重复验收')
        used.update(ids);confirmed+=a.accepted_qty+a.rejected_qty
    if confirmed>context[1]['quantity']: _fail('history_invalid','service_unavailable','累计仓库验收超过原退回数量')
    return results,confirmed,used


def record_rejection_receipt(db,*,actor,return_id,payload,idempotency_key,secret,trace_request_id):
    payload=RejectionReceiptIn.model_validate(payload.model_dump() if isinstance(payload,RejectionReceiptIn) else payload)
    trace=lifecycle._require_trace_request_id(trace_request_id)
    context=_context(db,actor,return_id,write=True);actor,parent,source,location,custody,request,stamp=context
    key=_key(actor,return_id,idempotency_key,secret);digest=_digest(actor,return_id,payload)
    old=db.execute(select(receipts).where(receipts.c.idempotency_key_hash==key)).mappings().one_or_none()
    if old is not None:
        if old['request_hash']!=digest: _fail('key_reused','conflict','原请求键已经绑定不同仓库验收')
        return _verify(db,context,old)
    grant=_authority(db,context)
    if payload.expected_request_version!=request.version or payload.custody_assignment_id!=custody.id:
        _fail('context_changed','conflict','需求版本或当前接收责任发生变化')
    if request.status not in ('approved','partially_approved') or db.scalar(select(registration.closures.c.id).where(registration.closures.c.request_id==request.id)):
        _fail('closed','precondition_failed','需求已关闭或不能新增仓库验收')
    if db.scalar(select(receipts.c.id).where(receipts.c.actor_user_id==actor.user_id,receipts.c.trace_request_id==trace)):
        _fail('trace_reused','conflict','请求编号已绑定其他仓库验收')
    lock_inventory_reference_graph(db,(parent['in_transit_account_id'],source.id),lifecycle._database_now(db))
    lock_inventory_serial_graph(db,tuple(p.serial_id for p in payload.amounts.accepted_serial_verifications))
    for identifier in sorted({e.evidence_file_id for e in payload.amounts.exceptions},key=str):
        _lock_file(db,identifier)
        if (db.scalar(select(exceptions.c.receipt_id).where(exceptions.c.evidence_file_id==identifier).limit(1))
            or db.scalar(select(ReceiptException.id).where(ReceiptException.evidence_file_id==identifier).limit(1))
            or db.scalar(select(StockOperationReceiptException.id).where(StockOperationReceiptException.evidence_file_id==identifier).limit(1))):
            _fail('file_bound','conflict','异常文件已绑定原仓库验收')
    _,confirmed,used=_history(db,context)
    a=payload.amounts;ids={p.serial_id for p in a.accepted_serial_verifications}|set(a.rejected_serial_ids)|set(a.shortage_serial_ids)
    if confirmed+a.accepted_qty+a.rejected_qty+a.shortage_qty>parent['quantity'] or used & ids:
        _fail('quantity_exceeded','conflict','验收数量或SN超过原退回未确认份额')
    now=lifecycle._database_now(db)
    evidence=_evidence(db,parent=parent,request=request,source=source,payload=payload,person_id=actor.person_id,
        user_id=actor.user_id,authorization_version=actor.authorization_version,recorded_at=now,new=True)
    if receiving._authorized(db,actor,return_id)[-1]!=stamp: _fail('context_changed','conflict','验收期间权限或接收责任变化')
    _authority(db,context)
    row=dict(id=uuid4(),return_id=return_id,request_id=request.id,request_version=request.version,handover_id=payload.handover_id,
        registration_request_hash=payload.registration_request_hash,handover_request_hash=payload.handover_request_hash,
        target_location_id=location.id,custody_assignment_id=custody.id,actor_user_id=actor.user_id,actor_person_id=actor.person_id,
        actor_role_assignment_id=grant.assignment_id,authorization_version=actor.authorization_version,
        **{k:getattr(a,k) for k in AMOUNTS},received_at=payload.received_at,recorded_at=now,reason=payload.reason,
        observed_sku_code=payload.observed_sku_code,idempotency_key_hash=key,trace_request_id=trace,request_hash=digest,evidence_sha256=lifecycle._canonical_hash(evidence),evidence_jsonb=evidence)
    db.execute(receipts.insert().values(**row))
    children=_serial_rows(payload)
    if children: db.execute(serials.insert(),[dict(receipt_id=row['id'],return_id=return_id,**child) for child in children])
    if a.exceptions: db.execute(exceptions.insert(),[dict(receipt_id=row['id'],**e.model_dump()) for e in a.exceptions])
    append_audit_event(db,stream_key='material_request',actor_user_id=actor.user_id,action=ACTION,aggregate_type=AGGREGATE,
        aggregate_id=str(row['id']),before_jsonb={'warehouse_receipt':'not_registered'},after_jsonb=_audit(row),request_id=trace,occurred_at=now,created_at=now)
    db.flush()
    return _verify(db,context,row).model_copy(update={'replayed':False})


def rejection_receipt_command_status(db,*,actor,return_id,idempotency_key=None,secret=None,trace_request_id=None,request_fingerprint=None):
    if (idempotency_key is None)==(trace_request_id is None): _fail('lookup_invalid','invalid_request','请提供唯一原请求查询坐标')
    with db.no_autoflush:
        context=_context(db,actor,return_id);actor=context[0]
        statement=select(receipts).where(receipts.c.return_id==return_id,receipts.c.actor_user_id==actor.user_id)
        statement=statement.where(receipts.c.idempotency_key_hash==_key(actor,return_id,idempotency_key,secret)) if idempotency_key else statement.where(receipts.c.trace_request_id==lifecycle._require_trace_request_id(trace_request_id))
        row=db.execute(statement).mappings().one_or_none();result=_verify(db,context,row) if row else None
        if row is not None and request_fingerprint is not None and request_fingerprint != lifecycle._canonical_hash(row['evidence_jsonb']['input']):
            _fail('fingerprint_mismatch','conflict','原仓库验收请求指纹不一致')
        if receiving._authorized(db,actor,return_id)[-1]!=context[-1]: _fail('context_changed','conflict','读取期间接收责任或需求变化')
        return result
