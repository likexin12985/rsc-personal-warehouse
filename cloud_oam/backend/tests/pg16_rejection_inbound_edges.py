"""Direct database bypass attempts and later split receipt on a real ledger."""
from dataclasses import replace
from decimal import Decimal
from uuid import UUID,uuid4
from sqlalchemy import select,text,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount,StockBalance,InventoryTransaction,InventorySerial,SerialCurrentPosition
from app.material_request_rejection_inbound_schema import inbounds,parts,serials
from app.material_request_rejection_receipt_schema import receipts
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_receipt_schemas import RejectionReceiptIn
from app.material_request_rejection_inbound_schemas import RejectionInboundIn
from app.formal_services import material_request_rejection_inbound as service
from app.formal_services import material_request_rejection_receipt as acceptance
from pg16_stock_scrap_structure_gate import original_columns,facts
from test_material_request_draft_service import SECRET


def before_post(engines,*,actor_id,plan,token):
    api,owner=engines['star_oam_api'],engines['star_oam_migrator']
    with owner.connect() as db:columns=original_columns(db);before=facts(db,columns)
    result=[]
    for case in ('source_without_inbound','posting_key_disguise','orphan_account'):
        with Session(api) as db:
            actor=load_formal_principal(db,actor_id)
            now=db.scalar(text('SELECT CURRENT_TIMESTAMP'))
            try:
                if case=='orphan_account':
                    missing=next(p for p in plan['parts'] if p['target_balance'] is None)
                    values={k:UUID(v) if v is not None and k.endswith('_id') else v for k,v in missing['target_dimensions'].items()}
                    db.add(StockAccount(id=UUID(missing['target_account_id']),**values,created_at=now,updated_at=now));db.flush()
                    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                else:
                    # Real unified inventory service with current receive_return
                    # permission, but deliberately no independent inbound fact.
                    source=db.get(StockAccount,UUID(plan['source_account_id']))
                    parent=db.execute(select(returns).where(returns.c.id==UUID(plan['return_id']))).mappings().one()
                    target=db.get(StockAccount,parent['return_source_account_id'])
                    ids=tuple(UUID(row['serial_id']) for row in plan['serial_positions'][:1])
                    command=service.plans.command(plan,inbound_id=uuid4(),at=now)
                    command=replace(command,movements=(service.plans.InventoryMovementCommand(from_account_id=source.id,
                        to_account_id=target.id,quantity=Decimal(1),serial_ids=ids),))
                    if case=='posting_key_disguise':command=replace(command,source_document_type='unrelated_stock_transfer')
                    service.posting.post_inventory_transaction(db,actor=actor,command=command,idempotency_key=token+'-'+case,
                        request_id=token+'-'+case,permission_resource='stock_operation',permission_action='receive_return')
                    db.execute(text('SET CONSTRAINTS rsc_rejection_inbound_complete_0175 IMMEDIATE'))
                db.commit()
            except DBAPIError as exc:
                assert exc.orig.sqlstate=='23514',(case,str(exc))
                expected='verified opening recount observation graph' if case=='orphan_account' else '0175 inventory posting requires warehouse inbound'
                assert expected in str(exc),(case,str(exc));db.rollback()
                result.append(dict(case=case,sqlstate=exc.orig.sqlstate))
            else:raise AssertionError('database bypass committed: '+case)
        with owner.connect() as db:assert facts(db,columns)==before
    return result


def after_post(engines,*,posting_id,token):
    api,owner=engines['star_oam_api'],engines['star_oam_migrator']
    with owner.connect() as db:columns=original_columns(db);before=facts(db,columns)
    with Session(api) as db:
        tx=db.get(InventoryTransaction,posting_id)
        values={c.name:getattr(tx,c.name) for c in InventoryTransaction.__table__.columns}
        values.update(id=uuid4(),transaction_no='RENAMED-'+uuid4().hex,source_document_type='unrelated_inverse',
            source_document_id=str(uuid4()),posting_key=token+'-renamed-inverse',idempotency_key_hash=uuid4().hex*2,
            movement_type='reversal',reversed_transaction_id=posting_id,
            ledger_cursor=db.scalar(select(func.max(InventoryTransaction.ledger_cursor)))+1)
        try:
            db.execute(InventoryTransaction.__table__.insert().values(**values))
            db.execute(text('SET CONSTRAINTS rsc_rejection_inbound_complete_0175 IMMEDIATE'));db.commit()
        except DBAPIError as exc:
            assert exc.orig.sqlstate=='23514' and '0175 inbound reversal requires independent command' in str(exc),str(exc)
            db.rollback()
        else:raise AssertionError('renaming inverse source bypassed original inbound binding')
    with owner.connect() as db:assert facts(db,columns)==before
    return [dict(case='renamed_inverse',sqlstate='23514')]


def split_tail(engines,*,first,actor_id,token):
    api,owner=engines['star_oam_api'],engines['star_oam_migrator']
    with Session(api) as db:
        actor=load_formal_principal(db,actor_id)
        view=acceptance.receiving.rejection_return_receiving_detail(db,actor=actor,return_id=first.return_id)
        first_row=db.execute(select(inbounds).where(inbounds.c.id==first.inbound_id)).mappings().one()
        first_plan=first_row['plan_jsonb']
        received_ids={UUID(s) for p in first_plan['parts'] for s in p['serial_ids']}
        remaining=[s for s in view.serials if s.serial_id not in received_ids]
        assert Decimal(view.quantity)==Decimal(3)
        assert db.get(StockBalance,UUID(first_plan['source_account_id'])).quantity==1
        proofs=[]
        for entry in remaining:
            sn=db.get(InventorySerial,entry.serial_id)
            assert db.get(SerialCurrentPosition,sn.id).stock_account_id==UUID(first_plan['source_account_id'])
            proofs.append(dict(serial_id=sn.id,sku_code=view.sku_code,serial_no=sn.serial_no,qr_code=sn.qr_code))
        if view.serials:assert len(received_ids)==2 and len(proofs)==1
        payload=RejectionReceiptIn(expected_request_version=view.request_version,reason='第三件另一次实物验收',
            registration_request_hash=view.registration_request_hash,handover_id=view.handover_id,
            handover_request_hash=view.handover_request_hash,custody_assignment_id=view.custody_assignment_id,
            received_at=view.handed_over_at,observed_sku_code=view.sku_code,
            amounts=dict(accepted_qty='1.000',accepted_serial_verifications=proofs))
        later=acceptance.record_rejection_receipt(db,actor=actor,return_id=first.return_id,payload=payload,
            idempotency_key=token+'-later-receipt',secret=SECRET,trace_request_id=token+'-later-receipt')
        db.commit()
    with Session(api) as db:
        actor=load_formal_principal(db,actor_id)
        planned=service.plans.preview(db,actor=actor,return_id=first.return_id,receipt_id=later.receipt_id)
        payload=RejectionInboundIn(expected_request_version=planned['plan']['request_version'],reason='第三件验收后独立入账',
            receipt_request_hash=planned['plan']['receipt_request_hash'],expected_plan_hash=planned['plan_hash'])
        second=service.post(db,actor=actor,return_id=first.return_id,receipt_id=later.receipt_id,payload=payload,
            idempotency_key=token+'-later-inbound',secret=SECRET,trace_request_id=token+'-later-inbound')
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.commit()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,actor_id)
        assert service.command_status(db,actor=actor,return_id=first.return_id,receipt_id=first.receipt_id,
            trace_request_id=token).inbound_id==first.inbound_id
        assert db.get(StockBalance,UUID(first_plan['source_account_id'])).quantity==0
        balances={p['condition_code']:db.get(StockBalance,UUID(p['target_account_id'])).quantity for p in first_plan['parts']}
        assert balances=={'new':Decimal(2),'damaged':Decimal(1)}
        for fact_id in (first.inbound_id,second.inbound_id):
            for sn in db.execute(select(serials).where(serials.c.inbound_id==fact_id)).mappings():
                account=db.scalar(select(parts.c.target_account_id).where(parts.c.inbound_id==fact_id,parts.c.condition_code==sn['condition_code']))
                assert db.get(SerialCurrentPosition,sn['serial_id']).stock_account_id==account
        assert db.scalar(select(func.count()).select_from(inbounds).where(inbounds.c.return_id==first.return_id))==2
        assert db.scalar(select(func.sum(inbounds.c.accepted_qty)).where(inbounds.c.return_id==first.return_id))==3
    return dict(secondReceiptId=str(later.receipt_id),secondInboundId=str(second.inbound_id),
        acceptedQuantity='3.000',newQuantity='2.000',damagedQuantity='1.000',remainingTransit='0.000',
        serialCount=len(received_ids)+len(proofs),firstPostingRecoveryAfterLaterPosting=True)
