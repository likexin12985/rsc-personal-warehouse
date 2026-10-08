"""Original rejected receipt to immutable return registration in the owned cluster."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from queue import Queue
from pathlib import Path
from uuid import UUID,uuid4
import json,runpy
from sqlalchemy import select,text,func,event
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.demand_models import MaterialRequest
from app.foundation_models import Permission,Role,RolePermission
from app.formal_access import load_formal_principal
from app.inventory_models import Receipt,ReceiptLine,ShipmentLine,OutboundPosting
from app.material_request_rejection_return_schema import returns,return_serials
from app.material_request_rejection_return_schemas import RejectionReturnIn
from app.formal_services import material_request_rejection_return as service
from app.formal_services.material_request_query import MaterialRequestReadError
from pg16_stock_scrap_structure_gate import original_columns,facts
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker
from test_material_request_draft_service import SECRET


def run(engines,*,request_id,directory):
    api,owner=engines['star_oam_api'],engines['star_oam_migrator'];request_id=UUID(request_id)
    folder=Path(__file__).parents[1]/'alembic'
    from app import material_request_rejection_return_security as catalog
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261229_0180'
        catalog.verify(db)
    with owner.connect() as db:
        columns={k:v for k,v in original_columns(db).items() if not k.startswith('audit_') and not k.startswith('material_request_rejection_return')}
        original=facts(db,columns)
        for table in (returns.name,return_serials.name):
            assert db.scalar(text("SELECT has_table_privilege('star_oam_api',:table,'SELECT,INSERT')"),{'table':'public.'+table})
            for right in ('UPDATE','DELETE','TRUNCATE'):
                assert not db.scalar(text("SELECT has_table_privilege('star_oam_api',:table,:right)"),{'table':'public.'+table,'right':right})
        for signature in catalog.DATA['functions']:
            assert not db.scalar(text("SELECT has_function_privilege('star_oam_api',:sig,'EXECUTE')"),{'sig':'public.'+signature})
    with Session(api) as db:
        request=db.get(MaterialRequest,request_id); actor_id=request.requester_user_id
        principal=load_formal_principal(db,actor_id)
        receipt=db.scalar(select(Receipt).join(ReceiptLine).join(ShipmentLine,ShipmentLine.id==ReceiptLine.shipment_line_id)
            .join(OutboundPosting,OutboundPosting.id==ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id==request_id,
                ReceiptLine.rejected_qty>0))
        verified=service.receipts._result(db,service.receipts._context(db,principal,request_id)[0],request,receipt,replayed=True)
        line=verified.lines[0]
        payload=RejectionReturnIn(expected_request_version=request.version,receipt_id=receipt.id,
            receipt_line_id=line.receipt_line_id,receipt_request_hash=receipt.request_hash,
            quantity=format(line.rejected_qty,'.3f'),serial_ids=line.rejected_serial_ids,reason='真实拒收后登记退回，库存保持在途')
    token='native-rejection-return-'+uuid4().hex
    def register(db,key=token):
        return service.register_rejection_return(db,actor=load_formal_principal(db,actor_id),request_id=request_id,
            payload=payload,idempotency_key=key,secret=SECRET,trace_request_id=key)
    with Session(api) as db:
        planned=register(db)
        prepared=dict(db.execute(select(returns).where(returns.c.id==planned.return_id)).mappings().one())
        db.rollback()
    denied=[]
    for case in ('hash','origin','quantity','reason','authority','missing_completion',*(['foreign_serial','fractional_serial'] if payload.serial_ids else [])):
        with Session(api) as db:
            row=dict(prepared,id=uuid4(),return_no='RJR-'+uuid4().hex.upper(),idempotency_key_hash=uuid4().hex*2,
                trace_request_id=token+'-'+case,occurred_at=db.scalar(text('SELECT CURRENT_TIMESTAMP')))
            row['created_at']=row['occurred_at']
            if case=='hash':row['evidence_sha256']='0'*64
            if case=='origin':row['in_transit_account_id']=row['return_source_account_id']
            if case=='quantity':row['quantity']+=1
            if case=='reason':row['reason']='与原输入不一致'
            if case=='authority':row['authorization_version']+=1
            if case in ('foreign_serial','fractional_serial'):
                row['evidence_jsonb']=deepcopy(row['evidence_jsonb'])
                if case=='foreign_serial':row['evidence_jsonb']['input']['serial_ids']=[str(uuid4())]
                else:
                    from decimal import Decimal
                    row['quantity']=Decimal('.500');row['evidence_jsonb']['input']['quantity']='0.500'
                row['evidence_sha256']=service.lifecycle._canonical_hash(row['evidence_jsonb'])
                row['request_hash']=service.lifecycle._canonical_hash(dict(request_id=str(request_id),actor_user_id=actor_id,
                    actor_person_id=str(row['actor_person_id']),input=row['evidence_jsonb']['input']))
            try:
                db.execute(returns.insert().values(**row));db.commit()
            except DBAPIError as exc:
                assert exc.orig.sqlstate in ('23514','42501'),(case,str(exc))
                if case=='foreign_serial':assert 'selected return serials mismatch' in str(exc)
                if case=='fractional_serial':assert 'original quantity precision required' in str(exc)
                denied.append(dict(case=case,sqlstate=exc.orig.sqlstate));db.rollback()
            else:raise AssertionError('forged direct insert accepted: '+case)
    waiting=Queue()
    def competing():
        with Session(api) as db:
            waiting.put(db.scalar(text('SELECT pg_backend_pid()')))
            try:register(db,token+'-competing');db.commit()
            except MaterialRequestReadError as exc:
                db.rollback();return exc.code
        raise AssertionError('duplicate registration committed')
    from pg16_rejection_http import post as http_post, verify_recovery as http_recovery
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing_requests = []
        def observe_commit(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(competing); competing_requests.append(future)
            pid = waiting.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_commit, once=True)
        first = http_post(db, actor_id=actor_id, request_id=request_id, value=payload, key=token)
        assert competing_requests[0].result(timeout=20) == 'rejection_return_quantity_exceeded'
    with Session(api) as db:
        assert register(db).return_id==first.return_id;db.commit()
    with Session(owner) as db:
        role=db.scalar(select(Role.id).where(Role.code=='technician'))
        perm=db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',Permission.action=='submit_return',Permission.field_code==''))
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==perm))
        assert grant.effect=='allow';grant.effect='deny';db.commit()
    with Session(api) as db:
        try:register(db,token+'-revoked')
        except MaterialRequestReadError as exc:assert exc.category=='forbidden'
        else:raise AssertionError('revoked registration accepted')
        db.rollback()
    with owner.connect() as db:
        all_columns=original_columns(db); before_recovery=facts(db,all_columns)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        principal=load_formal_principal(db,actor_id)
        by_key=service.rejection_return_command_status(db,actor=principal,request_id=request_id,idempotency_key=token,secret=SECRET)
        by_trace=service.rejection_return_command_status(db,actor=principal,request_id=request_id,trace_request_id=token)
        assert by_key==by_trace and by_key.return_id==first.return_id and by_key.replayed
    http_evidence = http_recovery(api, actor_id=actor_id, request_id=request_id, value=payload, key=token, expected=first)
    with owner.connect() as db:assert facts(db,all_columns)==before_recovery
    with Session(owner) as db:
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==perm));grant.effect='allow';db.commit()
    with owner.connect() as db:assert facts(db,columns)==original
    for engine in (api,owner):
        for operation in ('UPDATE','DELETE','TRUNCATE'):
            sql={'UPDATE':f"UPDATE {returns.name} SET reason='tamper'",'DELETE':f'DELETE FROM {returns.name}',
                 'TRUNCATE':f'TRUNCATE {returns.name} CASCADE'}[operation]
            with engine.connect() as db:
                try:db.execute(text(sql));db.commit()
                except DBAPIError as exc:
                    denied.append(dict(case=operation,role=engine.url.username,sqlstate=exc.orig.sqlstate));db.rollback()
                else:raise AssertionError('immutable return changed')
    with owner.connect() as db:
        assert facts(db,columns)==original
        assert db.scalar(select(func.count()).select_from(returns))==1
    result=dict(scope='formal 0176 public rejection registration', publicHttp=http_evidence,
        formalRuntimeAdmission=True,returnId=str(first.return_id),
        receiptId=str(payload.receipt_id),quantity=payload.quantity,serialIds=[str(s) for s in payload.serial_ids],
        realPostgreSQL=True,registered=True,inventoryUnchanged=True,duplicateConcurrencyBlocked=True,
        exactBlockingBackendObserved=True,revokedNewCommandDenied=True,readOnlyRecoveryAfterRevocation=True,
        rejectedDirectWrites=denied,productionAcceptance=False)
    (directory/'rejection-return-registration.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
