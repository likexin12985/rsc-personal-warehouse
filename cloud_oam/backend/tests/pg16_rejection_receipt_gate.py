"""Acceptance after committed refusal, re-registration and physical handover.

Uses formal migration in the runner's newly owned native cluster. Synthetic identities
and object storage are explicit; PostgreSQL roles, locks and facts are real.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from queue import Queue
from uuid import UUID, uuid4
import json

from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import User
from app.foundation_models import Permission, RoleAssignment, RolePermission
from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount, StockLocation, InventorySerial
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_receipt_schema import receipts, serials, exceptions
from app.material_request_rejection_receipt_schemas import RejectionReceiptIn
from app.formal_services import material_request_rejection_receipt as service
from app.formal_services.material_request_query import MaterialRequestReadError
from pg16_stock_scrap_structure_gate import original_columns, facts
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker
from test_material_request_my_receipt import evidence
from test_material_request_draft_service import SECRET
from pg16_rejection_warehouse_http import post as http_post, verify_recovery


def run(engines, *, return_id, directory, mixed_split=False):
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    return_id = UUID(return_id)
    from app import material_request_rejection_receipt_security as catalog
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM public.alembic_version'))=='20261228_0179'
        catalog.verify(db)
    token = 'native-warehouse-' + uuid4().hex
    with Session(api) as db:
        parent = db.execute(select(returns).where(returns.c.id == return_id)).mappings().one()
        source = db.get(StockAccount, parent['return_source_account_id'])
        location = db.get(StockLocation, source.location_id)
        actor_id = db.scalar(select(User.id).where(User.person_id == location.custodian_person_id))
        actor = load_formal_principal(db, actor_id)
        view = service.receiving.rejection_return_receiving_detail(db, actor=actor, return_id=return_id)
        proofs = []
        for sn in view.serials:
            row = db.get(InventorySerial, sn.serial_id)
            proofs.append(dict(serial_id=row.id, sku_code=view.sku_code, serial_no=row.serial_no, qr_code=row.qr_code))
        proofs.sort(key=lambda p:str(p['serial_id']))
        short_file, _ = evidence((db, actor), key=token+'-short-file')
        damage_file, _ = evidence((db, actor), key=token+'-damage-file')
        short_id, damage_id = short_file.id, damage_file.id
        db.commit()
    base = dict(expected_request_version=view.request_version, registration_request_hash=view.registration_request_hash,
        handover_id=view.handover_id, handover_request_hash=view.handover_request_hash,
        custody_assignment_id=view.custody_assignment_id, received_at=view.handed_over_at, reason='来源仓实物验收独立事实')
    command = RejectionReceiptIn(**base, observed_sku_code=view.sku_code,
        amounts=dict(accepted_qty=view.quantity, accepted_serial_verifications=proofs))
    def record(db, value=command, key=token):
        return service.record_rejection_receipt(db, actor=load_formal_principal(db, actor_id), return_id=return_id,
            payload=value, idempotency_key=key, secret=SECRET, trace_request_id=key)
    with owner.connect() as db:
        columns = {k:v for k,v in original_columns(db).items()
            if not k.startswith('audit_') and not k.startswith('material_request_rejection_receipt')}
        original = facts(db, columns)
        for table in (receipts, serials, exceptions):
            assert db.scalar(text("SELECT has_table_privilege('star_oam_api',:t,'SELECT,INSERT')"), {'t':'public.'+table.name})
            for right in ('UPDATE','DELETE','TRUNCATE'):
                assert not db.scalar(text("SELECT has_table_privilege('star_oam_api',:t,:r)"), {'t':'public.'+table.name,'r':right})
        for signature in catalog.DATA['functions']:
            assert not db.scalar(text("SELECT has_function_privilege('star_oam_api',:s,'EXECUTE')"), {'s':'public.'+signature})
    with Session(api) as db:
        provisional = record(db)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        prepared = dict(db.execute(select(receipts).where(receipts.c.id == provisional.receipt_id)).mappings().one())
        db.rollback()
    denied = []
    for case in ('hash','quantity','authority','custody','origin','missing_audit','missing_serials' if proofs else 'sku'):
        with Session(api) as db:
            row = deepcopy(prepared)
            row.update(id=uuid4(),idempotency_key_hash=uuid4().hex*2,trace_request_id=token+'-'+case,
                recorded_at=db.scalar(text('SELECT CURRENT_TIMESTAMP')))
            if case=='hash': row['evidence_sha256']='0'*64
            if case=='quantity': row['accepted_qty']+=1
            if case=='authority': row['authorization_version']+=1
            if case=='custody': row['custody_assignment_id']=uuid4()
            if case=='origin': row['handover_request_hash']='0'*64
            if case=='sku': row['observed_sku_code']='WRONG-SKU'
            try:
                db.execute(receipts.insert().values(**row))
                if proofs and case!='missing_serials':
                    db.execute(serials.insert(), [dict(receipt_id=row['id'],return_id=return_id,**child)
                        for child in service._serial_rows(command)])
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                db.commit()
            except DBAPIError as exc:
                assert exc.orig.sqlstate in ('23514','42501'), (case,str(exc))
                denied.append(dict(case=case,sqlstate=exc.orig.sqlstate)); db.rollback()
            else: raise AssertionError('direct invalid receipt accepted: '+case)
    short = RejectionReceiptIn(**base, amounts=dict(shortage_qty=view.quantity,
        shortage_serial_ids=tuple(sn.serial_id for sn in view.serials), exceptions=[dict(exception_type='shortage',
            description='本次实物未到，短少不核销待收数量',evidence_file_id=short_id)]))
    with Session(api) as db:
        shortage = http_post(db, actor_id=actor_id, return_id=return_id,
            value=short, key=token+'-shortage')
    damaged = RejectionReceiptIn(**base, observed_sku_code=view.sku_code,
        amounts=dict(accepted_qty='2.000' if mixed_split else view.quantity,damaged_qty='1.000' if mixed_split else view.quantity,
            accepted_serial_verifications=proofs[:2] if mixed_split else proofs,
            damaged_serial_ids=tuple(p['serial_id'] for p in (proofs[:1] if mixed_split else proofs)),exceptions=[dict(exception_type='damaged',
                description='实物破损验收，尚未独立坏件入库',evidence_file_id=damage_id)]))
    waiting = Queue()
    def competing_receipt():
        with Session(api) as db:
            waiting.put(db.scalar(text('SELECT pg_backend_pid()')))
            try: record(db,key=token+'-competing')
            except MaterialRequestReadError as exc:
                db.rollback();return exc.code
            raise AssertionError('competing over-receipt accepted')
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing_requests = []
        def observe_acceptance(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(competing_receipt); competing_requests.append(future)
            pid = waiting.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_acceptance, once=True)
        accepted = http_post(db, actor_id=actor_id, return_id=return_id, value=damaged, key=token)
        assert competing_requests[0].result(timeout=30).endswith('quantity_exceeded')
    with Session(owner) as db:
        assignment=db.scalar(select(receipts.c.actor_role_assignment_id).where(receipts.c.id==accepted.receipt_id))
        role=db.scalar(select(RoleAssignment.role_id).where(RoleAssignment.id==assignment))
        permission=db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',Permission.action=='receive_return',Permission.field_code==''))
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission))
        assert grant.effect=='allow';grant.effect='deny';db.commit()
    with Session(api) as db:
        try: record(db,key=token+'-revoked')
        except MaterialRequestReadError as exc: assert exc.category=='forbidden'
        else: raise AssertionError('revoked receiver submitted new command')
        db.rollback()
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,actor_id)
        by_key=service.rejection_receipt_command_status(db,actor=actor,return_id=return_id,idempotency_key=token,secret=SECRET)
        by_trace=service.rejection_receipt_command_status(db,actor=actor,return_id=return_id,trace_request_id=token)
        assert by_key==by_trace and by_key.receipt_id==accepted.receipt_id and by_key.replayed
        assert service.rejection_receipt_command_status(db,actor=actor,return_id=return_id,trace_request_id=token+'-shortage').receipt_id==shortage.receipt_id
    http_evidence = verify_recovery(api, actor_id=actor_id, return_id=return_id,
        value=damaged, key=token, expected=accepted, directory=directory)
    with Session(owner) as db:
        db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission)).effect='allow';db.commit()
    for engine in (api,owner):
        for table in (receipts,serials,exceptions):
            for operation in ('UPDATE','DELETE','TRUNCATE'):
                sql=(f'UPDATE public.{table.name} SET '+('reason=reason' if table is receipts else 'receipt_id=receipt_id')) if operation=='UPDATE' else (
                    f'DELETE FROM public.{table.name}' if operation=='DELETE' else f'TRUNCATE public.{table.name} CASCADE')
                with engine.connect() as db:
                    try:
                        db.execute(text(sql));db.commit()
                    except DBAPIError as exc:
                        assert exc.orig.sqlstate==('42501' if engine is api else '55000'),str(exc)
                        denied.append(dict(case=operation,table=table.name,role=engine.url.username));db.rollback()
                    else:
                        # Quantity mode has no serial rows, so row triggers have
                        # no target. ACL and statement-level TRUNCATE still apply.
                        assert engine is owner and table is serials and not proofs and operation!='TRUNCATE'
    with owner.connect() as db:
        assert facts(db,columns)==original
    result=dict(scope='formal 0174 warehouse acceptance',runtimeAdmission=True,
        returnId=str(return_id),shortageReceiptId=str(shortage.receipt_id),acceptedReceiptId=str(accepted.receipt_id),
        actualAcceptanceCommitted=True,mixedSplit=mixed_split,shortageDoesNotConsumeQuantityOrSerial=True,damageIsAcceptedSubset=True,
        exactBlockingBackendObserved=True,readOnlyRecoveryAfterRevocation=True,rejectedDirectWrites=denied,
        inventoryAndOriginalBusinessUnchanged=True,warehouseInventoryPosted=False,
        publicHttpActivated=True,publicHttp=http_evidence,productionAcceptance=False)
    (directory/'rejection-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
