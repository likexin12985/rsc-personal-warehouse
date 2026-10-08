"""Real native posting after formal refusal-return warehouse acceptance.

Synthetic identities/file storage remain explicit. No opening or authority
checks are replaced. Only fault-injection cases suppress application proof so
that the actual deferred database constraint is the observed rejection.
"""
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from uuid import UUID, uuid4
from unittest.mock import patch
from pathlib import Path
import json
import runpy

from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RoleAssignment, RolePermission
from app.inventory_models import StockBalance, StockAccount, InventoryTransaction, InventoryMovement
from app.material_request_rejection_receipt_schema import receipts
from app.material_request_rejection_inbound_schema import inbounds, parts, serials
from app.material_request_rejection_inbound_schemas import RejectionInboundIn
from app.formal_services import material_request_rejection_inbound as service
from app.formal_services.material_request_query import MaterialRequestReadError
from pg16_stock_scrap_structure_gate import original_columns, facts
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker
from test_material_request_draft_service import SECRET
from pg16_rejection_warehouse_http import post as http_post, preview as http_preview, verify_recovery


def run(engines, *, receipt_id, directory, mixed_split=False):
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    root=Path(__file__).resolve().parents[1]/'alembic'
    frozen=runpy.run_path(str(root/'rejection_inbound_0175/transition.py'))
    from app import material_request_rejection_inbound_security as catalog
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261228_0179'
        catalog.verify(db)
        for table in (inbounds,parts,serials):
            for right in ('SELECT','INSERT'):
                assert db.scalar(text("SELECT has_table_privilege('star_oam_api',:t,:r)"),{'t':'public.'+table.name,'r':right})
            for right in ('UPDATE','DELETE','TRUNCATE'):
                assert not db.scalar(text("SELECT has_table_privilege('star_oam_api',:t,:r)"),{'t':'public.'+table.name,'r':right})
        for signature, change in frozen['DATA']['functions'].items():
            if change['before'] is not None: continue
            assert not db.scalar(text("SELECT has_function_privilege('star_oam_api',:s,'EXECUTE')"),{'s':'public.'+signature})
    receipt_id=UUID(receipt_id); token='native-inbound-'+uuid4().hex
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        receipt=db.execute(select(receipts).where(receipts.c.id==receipt_id)).mappings().one()
        return_id=receipt['return_id']; actor_id=receipt['actor_user_id']
        preview=service.plans.preview(db,actor=load_formal_principal(db,actor_id),return_id=return_id,receipt_id=receipt_id)
        plan=preview['plan']
        command=RejectionInboundIn(expected_request_version=plan['request_version'],reason='来源仓已验收实物独立入账',
            receipt_request_hash=plan['receipt_request_hash'],expected_plan_hash=preview['plan_hash'])
        balance_before=db.get(StockBalance,UUID(plan['source_account_id'])).quantity
        targets_existed={p['target_account_id']:db.get(StockAccount,UUID(p['target_account_id'])) is not None for p in plan['parts']}
        http_preview(db, actor_id=actor_id, return_id=return_id, receipt_id=receipt_id,
            expected=preview, directory=directory)
    def post(db,key=token):
        return service.post(db,actor=load_formal_principal(db,actor_id),return_id=return_id,receipt_id=receipt_id,
            payload=command,idempotency_key=key,secret=SECRET,trace_request_id=key)
    with owner.connect() as db:
        columns=original_columns(db)
        # Exact original request/approval/receipt/return/physical progress facts.
        original_columns_only={k:v for k,v in columns.items() if k.startswith(('material_request','approval_','shipment','receipt','outbound','stock_reserv','stock_allocation'))
            and not k.startswith('material_request_rejection_inbound')}
        original=facts(db,original_columns_only)
        all_before=facts(db,columns)
    from pg16_rejection_inbound_edges import before_post, after_post, split_tail
    extra_denied=before_post(engines,actor_id=actor_id,plan=plan,token=token)
    with Session(api) as db:
        provisional=post(db)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        assert not provisional.replayed
        db.rollback()
    with owner.connect() as db:
        assert facts(db,columns)==all_before, 'rolled back posting leaked facts or accounts'
    rejected=list(extra_denied)
    for name,symbol,message in (
        ('missing_notification','record_business_notification','0175 exact inbound notification required'),
        ('missing_business_audit','append_audit_event','0175 exact inbound audit required')):
        with Session(api) as db, patch.object(service,symbol,return_value=None), patch.object(service.facts,'verify',return_value=None):
            try:
                post(db,token+'-'+name)
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                db.commit()
            except DBAPIError as exc:
                assert exc.orig.sqlstate=='23514' and message in str(exc),str(exc)
                rejected.append(dict(case=name,sqlstate=exc.orig.sqlstate)); db.rollback()
            else: raise AssertionError('incomplete posting committed: '+name)
        with owner.connect() as db:
            assert facts(db,columns)==all_before, name+' leaked transactional state'
    # Mutate an actual SQL INSERT after the service has prepared its complete
    # transaction. Only application reread is suppressed; DB guards must deny.
    for case in ('quantity','authority','plan_hash','part_quantity',*(['serial_partition'] if mixed_split and plan['serial_positions'] else [])):
        with Session(api) as db, patch.object(service.facts,'verify',return_value=None):
            original_execute=db.execute
            def inject(statement,*args,**kwargs):
                table=getattr(statement,'table',None)
                if getattr(statement,'is_insert',False) and table is not None:
                    if table.name==inbounds.name:
                        if case=='quantity': statement=statement.values(accepted_qty=receipt['accepted_qty']+1)
                        if case=='authority': statement=statement.values(authorization_version=plan['authorization_version']+1)
                        if case=='plan_hash': statement=statement.values(plan_hash='0'*64)
                    if table.name==parts.name and case=='part_quantity': statement=statement.values(quantity=receipt['accepted_qty']+1)
                    if table.name==serials.name and case=='serial_partition':
                        args=([dict(row,condition_code='new') if row['condition_code']=='damaged' else row for row in args[0]],*args[1:])
                return original_execute(statement,*args,**kwargs)
            with patch.object(db,'execute',side_effect=inject):
                try:
                    post(db,token+'-'+case)
                    original_execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.commit()
                except DBAPIError as exc:
                    assert exc.orig.sqlstate in ('23514','42501'),(case,str(exc))
                    assert '017' in str(exc) or (case=='part_quantity' and 'verified opening recount observation graph' in str(exc)),(case,str(exc))
                    rejected.append(dict(case=case,sqlstate=exc.orig.sqlstate));db.rollback()
                else:raise AssertionError('altered direct INSERT committed: '+case)
        with owner.connect() as db:
            assert facts(db,columns)==all_before,case+' leaked transactional state'
    waiting=Queue()
    def competitor():
        with Session(api) as db:
            waiting.put(db.scalar(text('SELECT pg_backend_pid()')))
            try: post(db,token+'-competing')
            except MaterialRequestReadError as exc:
                db.rollback();return exc.code
            raise AssertionError('second warehouse posting succeeded')
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing_requests = []
        def observe_posting(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(competitor); competing_requests.append(future)
            pid = waiting.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_posting, once=True)
        posted = http_post(db, actor_id=actor_id, return_id=return_id, receipt_id=receipt_id, value=command, key=token)
        assert competing_requests[0].result(timeout=30).endswith('already_posted')
    with Session(api) as db:
        assert post(db).inbound_id==posted.inbound_id;db.commit()
        assert db.scalar(select(inbounds.c.id).where(inbounds.c.receipt_id==receipt_id))==posted.inbound_id
        assert db.get(StockBalance,UUID(plan['source_account_id'])).quantity==balance_before-receipt['accepted_qty']
        tx=db.get(InventoryTransaction,posted.inventory_transaction_id)
        assert tx.status=='posted'
        for p in plan['parts']:
            current=db.get(StockBalance,UUID(p['target_account_id']))
            from decimal import Decimal
            assert current.quantity==Decimal(p['quantity'])+Decimal((p['target_balance'] or {}).get('quantity','0'))
    with Session(owner) as db:
        assignment=db.scalar(select(inbounds.c.actor_role_assignment_id).where(inbounds.c.id==posted.inbound_id))
        role=db.scalar(select(RoleAssignment.role_id).where(RoleAssignment.id==assignment))
        permission=db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',Permission.action=='receive_return',Permission.field_code==''))
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission))
        assert grant.effect=='allow';grant.effect='deny';db.commit()
    with Session(api) as db:
        try:post(db,token+'-revoked')
        except MaterialRequestReadError as exc:assert exc.category=='forbidden'
        else:raise AssertionError('revoked warehouse actor posted')
        db.rollback();db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,actor_id)
        key=service.command_status(db,actor=actor,return_id=return_id,receipt_id=receipt_id,idempotency_key=token,secret=SECRET)
        trace=service.command_status(db,actor=actor,return_id=return_id,receipt_id=receipt_id,trace_request_id=token)
        assert key==trace and key.inbound_id==posted.inbound_id and key.replayed
    http_evidence = verify_recovery(api, actor_id=actor_id, return_id=return_id, receipt_id=receipt_id,
        value=command, key=token, expected=posted, directory=directory)
    with Session(owner) as db:
        db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission)).effect='allow';db.commit()
    for engine in (api,owner):
        for table in (inbounds,parts,serials):
            for operation in ('UPDATE','DELETE','TRUNCATE'):
                sql=(f'UPDATE public.{table.name} SET '+('reason=reason' if table is inbounds else 'inbound_id=inbound_id')) if operation=='UPDATE' else (
                    f'DELETE FROM public.{table.name}' if operation=='DELETE' else f'TRUNCATE public.{table.name} CASCADE')
                with engine.connect() as db:
                    try:db.execute(text(sql));db.commit()
                    except DBAPIError as exc:
                        assert exc.orig.sqlstate==('42501' if engine is api else '55000'),str(exc)
                        rejected.append(dict(case=operation,table=table.name,role=engine.url.username));db.rollback()
                    else:assert engine is owner and table is serials and not plan['serial_positions'] and operation!='TRUNCATE'
    with owner.connect() as db:
        assert facts(db,original_columns_only)==original
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261228_0179'
    rejected.extend(after_post(engines,posting_id=posted.inventory_transaction_id,token=token))
    tail=split_tail(engines,first=posted,actor_id=actor_id,token=token) if mixed_split else None
    with owner.connect() as db:
        retained=facts(db,original_columns_only)
        assert all(set(rows)<=set(retained[name]) for name,rows in original.items()), 'old business facts rewritten by later receipt'
    result=dict(scope='formal 0175 warehouse inbound',runtimeAdmission=True,
        formalMigrationVerified=True,ddlStatementCount=len(frozen['DATA']['statements']),mixedSplitTail=tail,
        receiptId=str(receipt_id),returnId=str(return_id),inboundId=str(posted.inbound_id),
        actualInventoryPostingCommitted=True,noOpeningOrAuthorityBypass=True,targetsExistedBefore=targets_existed,
        exactBlockingBackendObserved=True,readOnlyRecoveryAfterRevocation=True,rejectedDirectWrites=rejected,
        originalBusinessFactsUnchanged=True,publicHttpActivated=True,publicHttp=http_evidence,productionAcceptance=False)
    (directory/'rejection-inbound.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
