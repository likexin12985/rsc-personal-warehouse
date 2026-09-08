"""Atomic, append-only carrier handover facts bound to physical outbound."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, hmac, json, uuid
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, DBAPIError
from ..demand_models import MaterialRequest
from ..foundation_models import OutboxEvent
from ..inventory_models import OutboundPosting, OutboundPostingSerial, Shipment, ShipmentLine, ShipmentSerial, StockAccount
from . import material_request_outbound as outbound
from . import material_request_reservation as reserve
from .audit_chain import append_audit_event, AuditChainError

class ShipmentError(Exception):
    def __init__(self, code, category, message): self.code,self.category,self.message=code,category,message

def _fail(code, cat, msg): raise ShipmentError(code,cat,msg)
def _text(q): return format(Decimal(q), '.3f')
def _hash(value): return hashlib.sha256(value.encode()).hexdigest()

def create_shipment(db, *, actor, request_id, expected_version, target_location_id, target_person_id,
                    carrier, tracking_no, shipped_at, lines, idempotency_key, secret, trace_request_id):
    if not isinstance(secret, bytes): secret=secret.encode()
    if len(secret)<32: _fail('secret_invalid','service_unavailable','发运幂等配置不可用')
    if not carrier.strip() or not tracking_no.strip(): _fail('metadata_invalid','invalid_request','承运商和运单号不能为空')
    try: when=datetime.fromisoformat(shipped_at.replace('Z','+00:00'))
    except ValueError: _fail('time_invalid','invalid_request','交运时间无效')
    if when.tzinfo is None: _fail('time_invalid','invalid_request','交运时间必须带时区')
    if len({line.outbound_posting_id for line in lines}) != len(lines): _fail('duplicate_line','invalid_request','同一出库事实不能重复出现在一个包裹')
    path=f'/api/v1/material-requests/{request_id}/shipments'
    key_hash=hmac.new(secret,f'{actor.user_id}:POST:{path}:{idempotency_key}'.encode(),hashlib.sha256).hexdigest()
    payload_hash=_hash(json.dumps({"request_id":str(request_id),"expected_version":expected_version,
        "target_location_id":str(target_location_id),"target_person_id":str(target_person_id) if target_person_id else None,
        "carrier":carrier.strip(),"tracking_no":tracking_no.strip(),"shipped_at":shipped_at,
        "lines":[{"posting_id":str(x.outbound_posting_id),"qty":_text(x.shipped_qty),"serial_ids":sorted(str(s) for s in x.serial_ids)} for x in lines]}, sort_keys=True, separators=(',',':')))
    request=db.scalar(select(MaterialRequest).where(MaterialRequest.id==request_id).with_for_update())
    if request is None: _fail('not_found','not_found','需求单不存在')
    if request.version != expected_version: _fail('version_conflict','conflict','需求版本已变化，请重新读取')
    existing=db.scalar(select(Shipment).where(Shipment.idempotency_key_hash==key_hash))
    if existing is not None:
        if existing.request_hash != payload_hash: _fail('key_reused','conflict','幂等键已绑定其他发运内容')
        return _result(db, existing, request, replayed=True)
    facts=[]; source_location=None; now=datetime.now(timezone.utc)
    serial_claims=set()
    for line in lines:
        fact=db.get(OutboundPosting,line.outbound_posting_id)
        if fact is None or fact.request_id != request_id: _fail('posting_not_found','not_found','出库事实不存在或不属于该需求')
        outbound.verified_outbound_history(db,fact=fact,request=request)
        shipped=Decimal(line.shipped_qty)
        used=Decimal(db.scalar(select(func.coalesce(func.sum(ShipmentLine.shipped_qty),0)).where(ShipmentLine.outbound_posting_id==fact.id)) or 0)
        if used+shipped>fact.outbound_qty: _fail('quantity_exceeded','precondition_failed','累计发运超过实际出库数量')
        bound=set(db.scalars(select(OutboundPostingSerial.serial_id).where(OutboundPostingSerial.posting_id==fact.id)).all())
        given=set(line.serial_ids)
        already=set(db.scalars(select(ShipmentSerial.serial_id).join(ShipmentLine).where(ShipmentLine.outbound_posting_id==fact.id)).all())
        if bound and (len(given)!=int(shipped) or not given <= bound-already): _fail('serial_mismatch','precondition_failed','发运 SN 必须属于该出库事实且不可重复')
        if not bound and given: _fail('serial_mismatch','precondition_failed','非 SN 物料不得提交 SN')
        if given & serial_claims: _fail('serial_duplicate','invalid_request','同一包裹内 SN 重复')
        serial_claims |= given
        source=db.get(StockAccount,fact.source_stock_account_id)
        if source is None: _fail('source_missing','conflict','出库来源账户不存在')
        outbound._authorize_account_ids(db, actor, (source.id,), action='read', resource='inventory', lock_rows=False)
        if source_location is None: source_location=source.location_id
        elif source_location != source.location_id: _fail('source_mismatch','conflict','同一包裹只能绑定一个来源位置')
        facts.append((fact,line,shipped,tuple(line.serial_ids)))
    if source_location==target_location_id: _fail('target_invalid','invalid_request','来源和目标位置不能相同')
    shipment=Shipment(id=uuid.uuid4(),shipment_no=f'SHP-{now:%Y%m%d}-{uuid.uuid4().hex[:12].upper()}',source_location_id=source_location,target_location_id=target_location_id,target_person_id=target_person_id,carrier=carrier.strip(),tracking_no=tracking_no.strip(),status='shipped',shipped_at=when,idempotency_key_hash=key_hash,request_hash=payload_hash,actor_user_id=actor.user_id,actor_person_id=actor.person_id,authorization_version=actor.authorization_version,created_at=now)
    db.add(shipment); db.flush()
    output_lines=[]
    for fact,line,qty,sids in facts:
        sl=ShipmentLine(id=uuid.uuid4(),shipment_id=shipment.id,outbound_posting_id=fact.id,outbound_line_id=fact.outbound_line_id,shipped_qty=qty,created_at=now); db.add(sl); db.flush()
        db.add_all([ShipmentSerial(shipment_line_id=sl.id,serial_id=s,created_at=now) for s in sids])
        output_lines.append({'shipment_line_id':sl.id,'outbound_posting_id':fact.id,'shipped_qty':_text(qty),'serial_ids':sids})
    db.add(OutboxEvent(event_type='shipment_handover_registered',aggregate_type='shipment',aggregate_id=str(shipment.id),payload_jsonb={'request_id':str(request_id),'shipment_no':shipment.shipment_no,'tracking_no':shipment.tracking_no},status='pending',attempts=0,idempotency_key=f'shipment:{shipment.id}',available_at=now))
    append_audit_event(db,stream_key='material_request',actor_user_id=actor.user_id,action='shipment_handover_registered',aggregate_type='shipment',aggregate_id=str(shipment.id),before_jsonb={},after_jsonb={'request_id':str(request_id),'shipment_no':shipment.shipment_no,'lines':[str(x['shipment_line_id']) for x in output_lines]},request_id=trace_request_id,occurred_at=now,created_at=now)
    return _result(db,shipment,request,replayed=False,lines=output_lines)

def _result(db, shipment, request, replayed, lines=None):
    if lines is None:
        lines=[{'shipment_line_id':x.id,'outbound_posting_id':x.outbound_posting_id,'shipped_qty':_text(x.shipped_qty),'serial_ids':tuple(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id==x.id)).all())} for x in db.scalars(select(ShipmentLine).where(ShipmentLine.shipment_id==shipment.id)).all()]
    return {'schema_version':'1.0','shipment_id':shipment.id,'shipment_no':shipment.shipment_no,'request_id':request.id,'status':shipment.status,'carrier':shipment.carrier,'tracking_no':shipment.tracking_no,'shipped_at':shipment.shipped_at.isoformat(),'lines':tuple(lines),'idempotency_replayed':replayed}

def list_shipments(db, *, actor, request_id):
    request = db.get(MaterialRequest, request_id)
    if request is None: _fail('not_found','not_found','需求单不存在')
    rows = tuple(db.scalars(select(Shipment).where(
        Shipment.id.in_(select(ShipmentLine.shipment_id).join(OutboundPosting,
            OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request_id))
    ).order_by(Shipment.created_at, Shipment.id)).all())
    for row in rows:
        if row.actor_user_id != actor.user_id:
            # Authorization is checked against each bound source account before exposing facts.
            bound = db.scalar(select(OutboundPosting.source_stock_account_id).join(ShipmentLine,
                ShipmentLine.outbound_posting_id == OutboundPosting.id).where(ShipmentLine.shipment_id == row.id))
            if bound is not None:
                outbound._authorize_account_ids(db, actor, (bound,), action='read', resource='inventory', lock_rows=False)
    return tuple(_result(db, row, request, replayed=False) for row in rows)
