"""Private atomic condition application; the caller owns commit/rollback.

Uses the unified inventory writer. No HTTP route, production grant, migration,
approval or correction execution is installed here. Complete request seals and
native business guards remain required before activation. A repeated key must
be recovered by exact read, never silently replayed through this writer.
"""
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from app.formal_access import lock_formal_principal_graph
from app.foundation_models import FileObject
from app.inventory_models import FormalMaterial, InventoryMovement, StockAccount
from app.return_condition_requests import validate_submit
from app.return_condition_seal_schema import build_schema
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_planning import TrackedQuantity
from app.formal_services.work_order_query import _aware
from . import return_condition_authority as authority, return_condition_submission_source as preparation
from . import return_condition_identity as identities, return_condition_posting_authority as permits
from . import return_condition_business_events as business
from . import return_condition_request_inputs as inputs
from . import return_condition_coordinates as coordinates
from . import return_condition_keys as keys
from .return_condition_evidence import completed_evidence
from .return_condition_event_files import read_event_evidence


@lru_cache(maxsize=1)
def tables():
    return build_schema()[0].tables


def fail(code, message):
    sources._fail('return_condition_'+code,message,409)


def _unused(db, actor, request, key):
    events=tables()['stock_condition_events']
    if db.scalar(select(events.c.id).where(or_(events.c.idempotency_key_hash==key,
            (events.c.actor_user_id==actor.user_id)&(events.c.request_id==request.request_id))).limit(1)):
        fail('request_requires_lookup','原请求已有事实，请只读回查，禁止重复提交')
    registry=inputs.table()
    if db.scalar(select(registry.c.event_id).where(or_(registry.c.idempotency_key_hash==key,
            (registry.c.actor_user_id==actor.user_id)&(registry.c.request_id==request.request_id))).limit(1)):
        inputs.invalid()


def _files(db, actor, request, at):
    links=tables()['stock_condition_files']
    result=[]
    for identifier in sorted(request.evidence_file_ids,key=str):
        row=db.scalar(select(FileObject).where(FileObject.id==identifier).with_for_update(read=True)
            .execution_options(populate_existing=True))
        item=completed_evidence(row,uploader_user_id=actor.user_id,uploader_person_id=actor.person_id,
            authorization_version=actor.authorization_version,provider_code='aliyun_oss_v2',recorded_at=at)
        if db.scalar(select(links.c.event_id).where(links.c.file_id==identifier)) is not None:
            fail('evidence_already_bound','附件已绑定另一纠正事件，请回查原请求或上传本次凭证')
        result.append(item)
    return tuple(result)


def _selection(db, request, doc):
    if doc['source_status'] not in ('recorded_stock_retained', 'verified_condition_history'):
        fail('source_requires_reconciliation','原破损份额已有后续移动，须先核验完整后续历史')
    limit = doc['claimable_quantity'] if doc['source_status'] == 'verified_condition_history' else doc['historical_damaged_quantity']
    if request.quantity>Decimal(limit):
        fail('quantity_exceeds_exception','本次冻结数量不能超过核验后未占用的原破损份额')
    rows=doc['policy_fingerprint']
    if len(rows)!=1 or rows[0][0]!=doc['material_id']:
        fail('policy_changed','原入库物料追踪策略不唯一')
    policy=rows[0]
    selected=tuple(sorted((v.serial_id for v in request.serial_verifications),key=str))
    TrackedQuantity(request.quantity,policy[2],policy[3],policy[4],selected).validate(
        UUID(doc['lot_id']) if doc['lot_id'] else None)
    permitted={UUID(s['serial_id']):s for s in doc['serials']
        if (s.get('claimable_for_correction', False) if doc['source_status'] == 'verified_condition_history'
            else s['retained_at_original_inbound'])}
    material=db.get(FormalMaterial,UUID(doc['material_id']),populate_existing=True)
    if material is None:
        fail('material_changed','原物料不存在')
    for proof in request.serial_verifications:
        original=permitted.get(proof.serial_id)
        if (original is None or proof.sku_code!=material.sku_code or proof.serial_no!=original['serial_no']
                or proof.qr_code!=original['qr_code']):
            fail('serial_mismatch','必须逐件核对原破损验收的物料号、SN和二维码')
    return selected,policy


def _persisted(db, rows):
    for name, entries in rows.items():
        table=tables()[name]
        for expected in entries:
            actual=db.execute(select(table).where(*(col==expected[col.name] for col in table.primary_key))).mappings().one()
            for key,value in expected.items():
                if (_aware(actual[key])!=_aware(value) if key=='created_at' else actual[key]!=value):
                    fail('persisted_changed','纠正事实与本次准确命令不一致')


def submit(db, *, actor, request):
    request=validate_submit(request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db,(actor.user_id,))
    admission=authority.authorize_submission(db,actor=actor,inbound_line_id=request.inbound_line_id)
    actor=admission.actor
    key=posting._storage_hash('stock-condition:'+request.idempotency_key)
    _unused(db,actor,request,key)
    coordinates.require_unused(db,actor=actor,request=request)
    checked=preparation.inspect_submission_source(db,actor=actor,inbound_line_id=request.inbound_line_id)
    if checked.evidence_hash!=request.expected_source_hash:
        fail('source_changed','原入库、库存或权限已变化，请重新核验')
    doc=checked.document
    selected,policy=_selection(db,request,doc)
    # Lock the existing opening/reference graph before creating the frozen
    # account. This is the same preplanning order used by original loss writes.
    graph=posting.InventoryPostingCommand(transaction_no='condition-graph',movement_type='freeze',
        source_document_type='stock_condition_event',source_document_id=str(request.inbound_line_id),
        posting_key='condition-graph',effective_at=datetime.now(timezone.utc),movements=(
            posting.InventoryMovementCommand(admission.source_account_id,None,request.quantity,selected),))
    posting._plan_and_lock_terminal_opening_graphs(db,command=graph,current_actor_user_id=actor.user_id)
    fresh=preparation.inspect_submission_source(db,actor=actor,inbound_line_id=request.inbound_line_id)
    if fresh.document!=doc or fresh.evidence_hash!=checked.evidence_hash:
        fail('source_changed','锁定过程中来源发生变化，请重新读取')
    # SKU text is intentionally not an inventory dimension in the source JSON.
    # Revalidate the actual scan after reference locks, including that live text.
    if _selection(db,request,fresh.document)!=(selected,policy):
        fail('selection_changed','锁定过程中所选物料或序列号发生变化')
    source=db.get(StockAccount,admission.source_account_id,populate_existing=True)
    at=datetime.now(timezone.utc)
    files=_files(db,actor,request,at)
    dimensions={k:getattr(source,k) for k in (
        'owner_org_id','custodian_person_id','location_id','material_id','condition_code','lot_id')}
    frozen=db.scalar(select(StockAccount).filter_by(**dimensions,availability_bucket='frozen'))
    if frozen is None:
        frozen=StockAccount(id=uuid4(),**dimensions,availability_bucket='frozen',created_at=at,updated_at=at)
        db.add(frozen);db.flush()
    case_id,event_id,line_id=uuid4(),uuid4(),uuid4()
    events=tables()['stock_condition_events']
    sequence=db.scalar(select(func.coalesce(func.max(events.c.event_sequence),0)).where(
        events.c.inbound_line_id==request.inbound_line_id))+1
    case=dict(id=case_id,operation_type='condition_correction',line_id=line_id,
        root_disposition_id=UUID(doc['selection']['root_disposition_id']),inbound_id=UUID(doc['inbound_id']),
        inbound_line_id=request.inbound_line_id,original_transaction_id=UUID(doc['original_transaction_id']),
        original_movement_id=UUID(doc['original_movement_id']),original_ledger_cursor=doc['original_ledger_cursor'],
        source_account_id=source.id,frozen_account_id=frozen.id,custody_assignment_id=admission.custody_assignment_id,
        recorded_condition=source.condition_code,target_condition='damaged',quantity=request.quantity,
        affected_quantity=Decimal(doc['historical_damaged_quantity']),tracking_mode=policy[2],quantity_scale=policy[3],
        allow_fraction=policy[4],history_hash=doc['selection']['expected_history_fingerprint'],
        source_hash=checked.evidence_hash,source_jsonb=doc,submit_event_id=event_id,submit_kind='submit')
    event=dict(id=event_id,created_at=at,actor_user_id=actor.user_id,actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version,request_id=request.request_id,idempotency_key_hash=key,
        reason=request.reason,case_id=case_id,submit_event_id=event_id,inbound_line_id=request.inbound_line_id,
        source_account_id=source.id,frozen_account_id=frozen.id,quantity=request.quantity,event_sequence=sequence,
        kind='submit',from_state='draft',to_state='awaiting_regional',previous_event_id=None,previous_sequence=None,
        decision_event_id=None,decision_kind=None,posting_transaction_id=None,posting_movement_id=None,
        movement_type='freeze',from_account_id=source.id,to_account_id=frozen.id)
    file_manifest=[dict(file_id=f.file_id,metadata_sha256=f.metadata_sha256) for f in files]
    binding=identities.identity(case,event,serial_ids=selected,evidence=file_manifest,previous_request_hash=None)
    event.update(plan_jsonb=binding.plan,plan_hash=binding.plan_hash,command_jsonb=binding.command,request_hash=binding.request_hash)
    command,posting_key,posting_hash=identities.inventory_identity(event,selected)
    command=posting._validate_posting_command(command)
    permit=permits.issue(db,admission=admission,command=command,event=event,
        source_cursor=doc['observed_ledger_cursor'],key_hash=posting_key,request_hash=posting_hash)
    try:
        posting._require_unused_business_keys(db,command)
        committed=posting._post_new_transaction(db,actor=actor,command=command,idempotency_key_hash=posting_key,
            request_hash=posting_hash,request_reference=posting._request_reference(request.request_id),
            permission_resource='stock_operation',permission_action=authority.ACTIONS['submit'],
            reversed_transaction_id=None,event_suffix='posted',occurred_at=at,condition_authority=permit)
        movement=db.scalars(select(InventoryMovement.id).where(
            InventoryMovement.transaction_id==committed.result.transaction_id)).one()
        event.update(posting_transaction_id=committed.result.transaction_id,posting_movement_id=movement)
        case.update(freeze_transaction_id=committed.result.transaction_id,freeze_movement_id=movement)
        common={k:event[k] for k in ('created_at','actor_user_id','authorization_version','reason','request_id',
            'idempotency_key_hash','request_hash','plan_hash','command_jsonb','plan_jsonb','posting_transaction_id')}
        rows={
            'stock_operation_orders':[dict(common,id=case_id,operation_no='COND-'+case_id.hex.upper(),
                operation_type='condition_correction',status='submitted',source_location_id=source.location_id,
                requester_id=actor.person_id,condition_case_id=case_id)],
            'stock_operation_lines':[dict(id=line_id,operation_id=case_id,operation_type='condition_correction',
                condition_case_id=case_id,line_no=1,stock_account_id=source.id,reserved_account_id=frozen.id,
                material_id=source.material_id,quantity=request.quantity,target_condition='damaged',reason=request.reason,created_at=at)],
            'stock_condition_cases':[case], 'stock_condition_events':[event],
            'stock_operation_serials':[dict(id=uuid4(),line_id=line_id,serial_id=s,sku_verified=True,qr_verified=True,created_at=at) for s in selected],
            'stock_condition_serials':[dict(case_id=case_id,serial_id=s,inbound_line_id=request.inbound_line_id) for s in selected],
            'stock_condition_files':[dict(event_id=event_id,created_at=at,**f) for f in file_manifest],
        }
        for name,entries in rows.items():
            if entries: db.execute(tables()[name].insert(),entries)
        db.flush()
        inputs.record(db,request=request,case=case,event=event)
        business.record(db,case=case,event=event,recipient=actor.person_id)
        keys.record(db,event=event,request=request)
        _persisted(db,rows)
        if read_event_evidence(db,event_id=event_id)!=files:
            fail('evidence_changed','纠正附件持久证明发生变化')
        authority.authorize_submission(db,actor=actor,inbound_line_id=request.inbound_line_id)
        inputs.match_original(db,request=request,case=case,event=event)
        return business.verify(db,case=case,event=event,recipient=actor.person_id)
    finally:
        permits.discard(db,permit)
