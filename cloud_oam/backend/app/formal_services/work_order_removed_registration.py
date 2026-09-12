"""Audited removed-SN identity admission, separate from stock and consumption."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import re
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import or_, select

from ..demand_models import WorkOrderRemovedSerialRegistration as Registration
from ..foundation_models import AuditEvent
from ..inventory_models import FormalMaterial, InventoryLot, InventorySerial, QrCode, StockAccount
from ..work_order_material_schemas import WorkOrderRemovedRegistrationOut, WorkOrderRemovedRegistrationPreviewOut, WorkOrderRemovedScanIn
from . import inventory_posting as posting, inventory_query as inventory
from . import work_order_material as material, work_order_replacements as replacements
from . import work_order_command_seal as seals
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError
from .work_order_material_options import material_options
from .work_order_preview import _policies
from .work_order_query import get_my_work_order, _own_actor
from .postgresql_lock_graph import lock_opening_stocktake_start_reference


def fail(code, message, category="precondition_failed"):
    raise material.WorkOrderMaterialPreflightError(code, message, category)


def command_payload(work_order_id, scan):
    scan = WorkOrderRemovedScanIn.model_validate(scan.model_dump() if hasattr(scan, "model_dump") else scan)
    for value, limit in ((scan.sku_code,80),(scan.serial_no,200),(scan.qr_code,250),(scan.lot_no,160)):
        if value is None and limit == 160:continue
        if not isinstance(value,str) or not value.strip() or len(value)>limit or re.search(r"[\x00-\x1f\x7f]",value):
            fail("removed_registration_codes_invalid","登记拆回 SN 须提供完整有效的 SKU、SN 和二维码", "invalid_request")
        try:value.encode("utf-8")
        except UnicodeEncodeError:fail("removed_registration_codes_invalid","三码格式无效", "invalid_request")
    return {"work_order_id":str(work_order_id),**scan.model_dump(mode="json")}


def _available_identity(db, material_id, command):
    if db.scalar(select(InventorySerial.id).where(or_(
            (InventorySerial.material_id==material_id)&(InventorySerial.serial_no==command["serial_no"]),
            InventorySerial.qr_code==command["qr_code"])).limit(1)) is not None or db.scalar(
            select(QrCode.id).where(QrCode.code==command["qr_code"]).limit(1)) is not None:
        fail("removed_registration_identity_exists","SN 或二维码已登记，请核验原标识后重新扫描，不得创建第二条身份")


def preview_registration(db, *, actor, work_order_id, scan):
    current=posting._require_current_actor(db,actor)
    command=command_payload(work_order_id,scan)
    if command["operator_person_id"]!=str(current.person_id):fail("operator_mismatch","操作人必须为当前登录人员","forbidden")
    with db.no_autoflush:
        options=material_options(db,actor=current,work_order_id=work_order_id)
        basis=next((row for row in options.items if str(row.stock_account_id)==command["basis_stock_account_id"]),None)
        if (not options.work_order.can_operate or options.opening_balance_status!="established"
                or basis is None or "replace" not in basis.allowed_actions):
            fail("removed_registration_basis_invalid","请先选择本人工单尚有剩余占用的投入明细")
        snapshot=inventory._ProjectionSnapshot(options.ledger_cursor,options.projected_at)
        now=datetime.now(timezone.utc)
        sku=db.scalar(select(FormalMaterial).where(FormalMaterial.sku_code==command["sku_code"]).execution_options(populate_existing=True))
        if sku is None or sku.status!="active":fail("removed_material_not_found","拆回 SKU 不存在或已停用")
        policies,fingerprint=_policies(db,{sku.id},now)
        policy=policies[sku.id]
        if policy.tracking_mode not in {"serial","lot_and_serial"}:fail("removed_registration_policy_invalid","该物料不按 SN 管理，不能创建序列号")
        lot=None
        if policy.tracking_mode=="lot_and_serial":
            lot=db.scalar(select(InventoryLot).where(InventoryLot.material_id==sku.id,InventoryLot.lot_no==command["lot_no"])) if command["lot_no"] else None
            if lot is None:fail("recover_lot_invalid","新登记的批次 SN 必须提供该物料已登记的准确批次")
        elif command["lot_no"] is not None:fail("lot_not_allowed","纯 SN 物料不能绑定批次")
        line=replacements.RecoveryLineInput(basis.stock_account_id,sku.id,Decimal(1),command["condition_before"],lot_id=lot.id if lot else None)
        dimensions,_=replacements.recovery_target(db,operator_person_id=current.person_id,line=line,require_active=True)
        def check():
            _available_identity(db,sku.id,command)
            posting._require_no_active_hard_freezes(db,{basis.stock_account_id:SimpleNamespace(**dimensions)},effective_at=datetime.now(timezone.utc))
            posting._require_no_active_hard_freezes(db,{basis.stock_account_id:db.get(StockAccount,basis.stock_account_id)},effective_at=datetime.now(timezone.utc))
        check()
        from .work_order_replacement_preview import _catalog_fingerprint
        catalog=_catalog_fingerprint(db,sku.id,line.lot_id)
        output=WorkOrderRemovedRegistrationPreviewOut(work_order_id=work_order_id,operator_person_id=current.person_id,
            authorization_version=current.authorization_version,source_version=options.work_order.source_version,
            ledger_cursor=options.ledger_cursor,checked_at=now,basis_stock_account_id=basis.stock_account_id,
            material_id=sku.id,sku_code=sku.sku_code,material_name=sku.name,base_unit=sku.base_unit,
            condition_before=command["condition_before"],tracking_mode=policy.tracking_mode,
            quantity_scale=policy.quantity_scale,allow_fraction=policy.allow_fraction,
            lot_id=line.lot_id,lot_no=lot.lot_no if lot else None,serial_id=None,serial_no=command["serial_no"],
            request_hash=replacements._hash(command))
        if get_my_work_order(db,actor=current,work_order_id=work_order_id)!=options.work_order or _policies(db,{sku.id},datetime.now(timezone.utc))[1]!=fingerprint:
            fail("removed_registration_context_changed","工单或库存策略发生变化，请重新核验")
        if _catalog_fingerprint(db,sku.id,line.lot_id)!=catalog:fail("removed_registration_context_changed","物料或批次发生变化，请重新核验")
        if replacements.recovery_target(db,operator_person_id=current.person_id,line=line,require_active=True)[0]!=dimensions:
            fail("removed_registration_context_changed","回收保管维度发生变化，请重新核验")
        check();inventory._ensure_projection_snapshot_current(db,snapshot);posting._require_current_actor(db,current)
        return output


def _audit_payload(row):
    return {"work_order_id":str(row.oam_work_order_id),"operator_person_id":str(row.operator_person_id),
        "serial_id":str(row.serial_id),"authorization_version":row.authorization_version,"source_version":row.source_version,
        "request_id":row.request_id,"request_hash":row.request_hash,"command":row.command_jsonb}


def verified_registration(db, *, actor, row):
    sn=db.get(InventorySerial,row.serial_id,populate_existing=True)
    qr=db.get(QrCode,row.serial_id,populate_existing=True)
    command=row.command_jsonb
    if (row.actor_user_id!=actor.user_id or row.operator_person_id!=actor.person_id or sn is None or qr is None
            or row.request_hash!=replacements._hash(command) or row.registration_no!="WORS-"+row.idempotency_key_hash[:24].upper()
            or sn.material_id!=row.material_id or sn.lot_id!=row.lot_id or sn.serial_no!=command.get("serial_no") or sn.qr_code!=command.get("qr_code")
            or qr.object_type!="serial" or qr.object_id!=sn.id or qr.code!=sn.qr_code or qr.status!="active"
            or seals._utc(sn.created_at)!=seals._utc(row.registered_at) or seals._utc(row.created_at)!=seals._utc(row.registered_at)
            or command.get("work_order_id")!=str(row.oam_work_order_id) or command.get("operator_person_id")!=str(row.operator_person_id)
            or command.get("basis_stock_account_id")!=str(row.basis_stock_account_id)):
        fail("removed_registration_evidence_invalid","原登记身份或内容证明不一致，请保留请求核验","service_unavailable")
    events=tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key=="material_request",
        AuditEvent.aggregate_type=="work_order_removed_serial_registration",AuditEvent.aggregate_id==str(row.id)).limit(2)))
    if len(events)!=1:fail("removed_registration_evidence_invalid","原登记审计缺失或重复","service_unavailable")
    event=events[0]
    if (event.action!="work_order_material.register_removed_serial" or event.actor_user_id!=row.actor_user_id
            or event.request_id!="work-order-serial-registration:"+str(row.id) or event.before_jsonb!={}
            or event.after_jsonb!=_audit_payload(row) or seals._utc(event.occurred_at)!=seals._utc(row.registered_at)):
        fail("removed_registration_evidence_invalid","原登记审计内容不一致","service_unavailable")
    try:verify_audit_event_in_read_snapshot(db,stream_key="material_request",event_id=event.id)
    except AuditChainError:fail("removed_registration_evidence_invalid","原登记审计链未通过核验","service_unavailable")
    return WorkOrderRemovedRegistrationOut(registration_id=row.id,registration_no=row.registration_no,
        work_order_id=row.oam_work_order_id,operator_person_id=row.operator_person_id,serial_id=row.serial_id,
        material_id=row.material_id,lot_id=row.lot_id,basis_stock_account_id=row.basis_stock_account_id,
        request_id=row.request_id,request_hash=row.request_hash,registered_at=seals._utc(row.registered_at))


def lookup_registration(db, *, actor, work_order_id, request_id):
    current=_own_actor(db,actor)
    with db.no_autoflush:
        row=db.scalar(select(Registration).where(Registration.actor_user_id==current.user_id,
            Registration.operator_person_id==current.person_id,Registration.oam_work_order_id==work_order_id,
            Registration.request_id==request_id).execution_options(populate_existing=True))
        sealed=seals._row(db,actor=current,work_order_id=work_order_id,operation_type="register_removed",request_id=request_id)
        if row and sealed:fail("removed_registration_evidence_invalid","原请求同时存在登记和封存证明","service_unavailable")
        result=verified_registration(db,actor=current,row=row) if row else seals._verified_seal(db,actor=current,row=sealed) if sealed else None
        posting._require_current_actor(db,current)
        return result


def register_removed_serial(db, *, actor, work_order_id, scan, idempotency_key, request_id):
    command=command_payload(work_order_id,scan)
    if (not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>200 or idempotency_key!=idempotency_key.strip()
            or not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}",request_id)):
        fail("removed_registration_request_invalid","登记请求标识无效","invalid_request")
    current=seals._lock_request(db,actor=actor,work_order_id=work_order_id)
    if command["operator_person_id"]!=str(current.person_id):fail("operator_mismatch","操作人必须是当前登录人员","forbidden")
    digest=replacements._hash(command);key_hash=hashlib.sha256(idempotency_key.encode()).hexdigest()
    existing=db.scalar(select(Registration).where(Registration.idempotency_key_hash==key_hash))
    if existing:
        if existing.actor_user_id!=current.user_id or existing.oam_work_order_id!=work_order_id:fail("removed_registration_not_found","原登记不存在","not_found")
        if existing.request_id!=request_id or existing.request_hash!=digest or existing.command_jsonb!=command:
            fail("removed_registration_conflict","原请求键已绑定其他登记内容","conflict")
        result=verified_registration(db,actor=current,row=existing);posting._require_current_actor(db,current);return result
    original=lookup_registration(db,actor=current,work_order_id=work_order_id,request_id=request_id)
    if original is not None:
        if getattr(original,"lookup_status",None)=="sealed_not_executed":fail("work_order_request_sealed","原登记请求已永久关闭，请读取原结果")
        fail("removed_registration_conflict","原登记请求已执行，请按原请求读取","conflict")
    prepared=preview_registration(db,actor=current,work_order_id=work_order_id,scan=scan)
    basis=db.get(StockAccount,prepared.basis_stock_account_id,populate_existing=True)
    # Reuse the bounded owner graph: accounts, custody, SKU, policy and lot.
    # No target account or stock fact is created by this identity command.
    lock_opening_stocktake_start_reference(db,basis.owner_org_id,(basis.owner_org_id,),
        (basis.location_id,),(prepared.material_id,),datetime.now(timezone.utc))
    prepared=preview_registration(db,actor=current,work_order_id=work_order_id,scan=scan)
    now=datetime.now(timezone.utc);identifier=uuid4()
    row=Registration(id=uuid4(),registration_no="WORS-"+key_hash[:24].upper(),serial_id=identifier,
        oam_work_order_id=work_order_id,actor_user_id=current.user_id,operator_person_id=current.person_id,
        authorization_version=current.authorization_version,source_version=prepared.source_version,
        basis_stock_account_id=prepared.basis_stock_account_id,material_id=prepared.material_id,lot_id=prepared.lot_id,
        request_id=request_id,request_hash=digest,idempotency_key_hash=key_hash,command_jsonb=command,registered_at=now,created_at=now)
    if db.get_bind().dialect.name=="sqlite":
        # Test dialect only. Production API has no INSERT on these masters;
        # its reviewed BEFORE INSERT registration trigger creates exactly both.
        db.add(InventorySerial(id=identifier,material_id=row.material_id,lot_id=row.lot_id,serial_no=command["serial_no"],
            qr_code=command["qr_code"],lifecycle_status="active",created_at=now,updated_at=now))
        db.add(QrCode(id=identifier,code=command["qr_code"],object_type="serial",object_id=identifier,status="active",created_at=now,updated_at=now))
        db.flush()
    db.add(row)
    append_audit_event(db,stream_key="material_request",actor_user_id=current.user_id,action="work_order_material.register_removed_serial",
        aggregate_type="work_order_removed_serial_registration",aggregate_id=str(row.id),before_jsonb={},after_jsonb=_audit_payload(row),
        request_id="work-order-serial-registration:"+str(row.id),occurred_at=now,created_at=now)
    db.flush();posting._require_current_actor(db,current)
    return verified_registration(db,actor=current,row=row)


def seal_registration(db, *, actor, work_order_id, request_id, request_hash):
    if not isinstance(request_hash,str) or not re.fullmatch(r"[a-f0-9]{64}",request_hash) or not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}",request_id):
        fail("removed_registration_request_invalid","原登记请求标识无效","invalid_request")
    current=seals._lock_request(db,actor=actor,work_order_id=work_order_id)
    original=lookup_registration(db,actor=current,work_order_id=work_order_id,request_id=request_id)
    if original:
        digest=original.seal.request_hash if getattr(original,"lookup_status",None)=="sealed_not_executed" else original.request_hash
        if digest!=request_hash:fail("removed_registration_conflict","原请求已绑定其他内容","conflict")
        return original
    return seals._write_seal(db,current=current,work_order_id=work_order_id,operation_type="register_removed",request_id=request_id,request_hash=request_hash)
