"""One real quantity or SN lifecycle in the caller-owned isolated PG16 cluster.

Synthetic master identities and external evidence only. No balance seeding,
business SQL shortcuts, real channel calls, or production acceptance claim.
"""
from dataclasses import replace, asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
import json
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.demand_models import MaterialRequest, MaterialRequestLine, ApprovalStep, MaterialRequestCancellationLineFact
from app.inventory_models import StockAccount, StockBalance, StockLocation, CustodyAssignment, InboundOrder, Receipt
from app.formal_access import load_formal_principal
from app.formal_services import material_request_draft as drafting
from app.formal_services import material_request_allocation as allocation
from app.formal_services import material_request_reservation as reservation
from app.formal_services import material_request_picking as picking
from app.formal_services import material_request_outbound as outbound
from app.formal_services.material_request_policy import ApprovalLineDecision
from app.material_request_my_receipt_schemas import MyReceiptIn
from app.material_request_my_inbound_schemas import MyInboundIn
from test_material_request_draft_service import SECRET, _draft
from test_material_request_approval_service import _approve, _evidence, _register_external, _verify_external
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_personal_stock_fixture import establish_personal_stock


def run(engines, *, requester, manager, admin, verifier, directory, tracking, browser_tail=None, partial_release=False, rejected_receipt=False, rejection_split=False, mixed_return=False, unfulfilled_return=False, supply_allocation_recovery=False, supply_upgrade_check=None, supply_browser=None, supply_create_browser=None):
    api=engines['star_oam_api']; owner=engines['star_oam_migrator']
    assert tracking in ('quantity','serial')
    assert not rejection_split or rejected_receipt
    assert not rejected_receipt or (browser_tail is None and not partial_release)
    fixture=prepare_stocktake_inventory(owner,engines['edge_inbox'],actor_user_id=admin,assignee_user_id=manager,control_material=tracking)
    assert not mixed_return or (rejected_receipt and not rejection_split)
    assert not unfulfilled_return or mixed_return
    total = Decimal(3) if rejection_split or unfulfilled_return else Decimal(2) if partial_release or mixed_return or supply_create_browser is not None else Decimal(1)
    if supply_allocation_recovery:
        total = Decimal(4)
    fixture = dict(fixture, physical_count=total)
    serial_ids=()
    if tracking=='serial':
        fixture=dict(fixture,material_id=fixture['concurrency_material_id'],material_sku_code=fixture['concurrency_material_sku_code'],selected_serial_no=fixture['concurrency_serial_no'])
        serial_ids=(fixture['concurrency_serial_id'],)
        # Declare only an empty stock-account dimension before the first
        # physical count. The count and independent reviews create all stock.
        with Session(owner) as db:
            original=db.get(StockAccount,fixture['difference_peer_account_id'])
            declared=StockAccount(id=uuid4(),material_id=fixture['material_id'],**{
                name:getattr(original,name) for name in ('owner_org_id','custodian_person_id','location_id',
                    'condition_code','availability_bucket','lot_id')})
            db.add(declared);db.flush();fixture['difference_peer_account_id']=declared.id;db.commit()
    if tracking=='serial' and total>1:
        from app.inventory_models import InventorySerial
        with Session(owner) as db:
            original=db.get(InventorySerial,serial_ids[0])
            extras=[InventorySerial(id=uuid4(),material_id=original.material_id,lot_id=original.lot_id,
                serial_no='SPLIT-'+uuid4().hex,qr_code='SPLIT-QR-'+uuid4().hex,lifecycle_status='active')
                for _ in range(int(total)-1)]
            db.add_all(extras);db.flush()
            fixture['selected_serial_nos']=(original.serial_no,*(row.serial_no for row in extras))
            serial_ids=(*serial_ids,*(row.id for row in extras))
            db.commit()
    # The shared helper accepts a counter identity; this fixture is a regional
    # source location, so its existing custodian/manager performs the count.
    establish_personal_stock(api,fixture,admin=admin,manager=manager,engineer=manager,reviewer=verifier)
    with Session(api) as db:
        source_id=db.scalars(select(StockAccount.id).join(StockBalance).where(
            StockAccount.location_id==fixture['difference_peer_location_id'],
            StockAccount.material_id==fixture['material_id'],StockAccount.availability_bucket=='available',
            StockAccount.condition_code=='new',StockBalance.quantity==total)).one()
    token='visible-flow-'+uuid4().hex
    with Session(owner) as db:
        person=load_formal_principal(db,requester).person_id
        source=db.get(StockAccount,source_id)
        assert db.get(StockBalance,source_id).quantity==total
        dimensions={name:getattr(source,name) for name in ('owner_org_id','custodian_person_id','location_id','material_id','condition_code','lot_id')}
        accounts={}
        for bucket in ('reserved','picking','in_transit'):
            row=StockAccount(id=uuid4(),availability_bucket=bucket,**dimensions)
            db.add(row); db.flush(); accounts[bucket]=row.id
            db.add(StockBalance(stock_account_id=row.id,quantity=Decimal(0),version=0,ledger_cursor=0))
        target=StockLocation(id=uuid4(),code='VISIBLE-PERSONAL-'+uuid4().hex,name='合成工程师个人仓',
            location_type='personal',owner_org_id=fixture['region_org_id'],parent_id=fixture['difference_peer_location_id'],
            custodian_person_id=person,status='active')
        db.add(target);db.flush(); target_id=target.id
        db.add(CustodyAssignment(id=uuid4(),location_id=target_id,custodian_person_id=person,valid_from=datetime.now(timezone.utc)-timedelta(days=1)))
        db.commit()
    from test_postgresql16_release_gate import _establish_multiround_stocktake_location
    target_opening=_establish_multiround_stocktake_location(api,fixture={**fixture,'recount_location_id':target_id},
        actor_user_id=admin,assignee_user_id=manager,count_user_id=requester,expected_snapshot_line_count=0)
    print('Visible fulfillment target zero opening independently reviewed and closed',flush=True)
    def actor(db,identifier):return load_formal_principal(db,identifier)
    http_commands=[]
    def post(route,body,label,identifier,expected_status=201):
        from fastapi.encoders import jsonable_encoder
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_formal_principal
        from app.config import get_settings
        settings=get_settings().model_copy(update={'material_request_writes_enabled':True,
            'material_request_idempotency_hmac_secret':SECRET.decode()})
        def sessions():
            with Session(api) as db:yield db
        def principal():
            with Session(api) as db:return actor(db,identifier)
        with patch.object(app,'dependency_overrides',{**app.dependency_overrides,get_db:sessions,
            get_formal_principal:principal,get_settings:lambda:settings}),TestClient(app) as client:
            response=client.post('/api/v1/material-requests/'+str(request_id)+'/'+route,
                json=jsonable_encoder(body,custom_encoder={Decimal:str}),
                headers={'Idempotency-Key':token+'-'+label,'X-Request-ID':token+'-'+label})
        http_commands.append(dict(route=route,status=response.status_code))
        (directory/'fulfillment-http.json').write_text(json.dumps(http_commands,indent=2)+'\n')
        assert response.status_code==expected_status,(route,response.status_code,response.text)
        assert 'no-store' in response.headers['cache-control']
        return response.json()
    with Session(api) as db:
        key=token+'-create'
        request_id=drafting.derive_material_request_create_id(actor=actor(db,requester),idempotency_key=key,idempotency_hmac_secret=SECRET)
        world=SimpleNamespace(actor_person=SimpleNamespace(id=person),materials=(SimpleNamespace(id=fixture['material_id']),),attachment=SimpleNamespace(id=None))
        draft=_draft(world,request_id)
        draft=replace(draft,attachment_file_ids=(),lines=(replace(draft.lines[0],requested_qty=total),),purpose='隔离测试完整履约闭环')
        created=drafting.create_material_request_draft(db,actor=actor(db,requester),material_request_id=request_id,draft=draft,
            idempotency_key=key,idempotency_hmac_secret=SECRET,trace_request_id=key)
        db.commit()
    stages=[]
    remaining_stages=[]
    def checkpoint(label):
        with Session(api) as db:
            row=db.get(MaterialRequest,request_id)
            stages.append(dict(stage=label,request_version=row.version,**{name:getattr(row,name) for name in (
                'status','allocation_status','reservation_status','outbound_status','shipment_status','logistics_signature_status',
                'oam_receipt_status','personal_inbound_status','notification_status','reconciliation_status')}))
        expected_bucket = {'external-approved':'unreserved_qty','allocated':'unreserved_qty',
            'reserved':'reserved_unpicked_qty','picked':'picked_unoutbound_qty',
            'outbound':'outbound_unshipped_qty','shipped':'shipped_unreceived_qty',
            'received':'accepted_unposted_qty','rejected':'rejected_unsettled_qty','posted':'posted_qty',
            'browser-received-and-posted':'posted_qty'}.get(label)
        if expected_bucket and not partial_release:
            from app.formal_services.material_request_remainder import remaining_fulfillment
            from app.material_request_remainder_schemas import BUCKETS
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assessment=remaining_fulfillment(db,actor=actor(db,requester),request_id=request_id)
                assert len(assessment.lines)==1
                line=assessment.lines[0]
                expected = {key:'0.000' for key in (*BUCKETS,'posted_qty','cancelled_qty')}
                expected[expected_bucket] = format(total, '.3f')
                if supply_create_browser is not None and label not in ('external-approved', 'allocated'):
                    expected[expected_bucket] = '1.000'
                    expected['unreserved_qty'] = '1.000'
                if mixed_return and label == 'rejected':
                    expected.update(posted_qty='1.000', rejected_unsettled_qty='1.000')
                    if unfulfilled_return: expected.update(reserved_unpicked_qty='1.000')
                if unfulfilled_return and label in ('picked', 'outbound', 'shipped'):
                    expected[expected_bucket]='2.000'
                    expected['reserved_unpicked_qty']='1.000'
                assert line.approved_qty==format(total,'.3f')
                assert all(getattr(line,key)==quantity for key,quantity in expected.items())
                remaining_stages.append(dict(stage=label,**assessment.model_dump(mode='json')))
            (directory/'remaining-fulfillment-stages.json').write_text(json.dumps(remaining_stages,indent=2)+'\n')
        (directory/'fulfillment-stages.json').write_text(json.dumps(stages,indent=2)+'\n')
        print('Visible fulfillment '+label+' committed',flush=True)
    checkpoint('draft')
    with Session(api) as db:
        drafting.submit_material_request(db,actor=actor(db,requester),material_request_id=request_id,expected_version=created.request_version,
            idempotency_key=token+'-submit',idempotency_hmac_secret=SECRET,trace_request_id=token+'-submit');db.commit()
    checkpoint('submitted')
    for identifier,label in ((manager,'region'),(admin,'hq')):
        with Session(api) as db:
            request=db.get(MaterialRequest,request_id)
            line_id=db.scalar(select(MaterialRequestLine.id).where(MaterialRequestLine.request_id==request_id))
            _approve(db,actor=actor(db,identifier),request=request,request_version=request.version,quantities={line_id:total},key=token+'-'+label);db.commit()
        checkpoint(label+'-approved')
    with Session(api) as db:
        request=db.get(MaterialRequest,request_id)
        evidence=_evidence(db,uploaded_by=admin,marker=token+'-evidence')
        step,registered=_register_external(db,actor=actor(db,admin),request=request,request_version=request.version,evidence=evidence,
            key=token+'-external',action='approve',lines=(ApprovalLineDecision(line_id,total,'合成证据同意'),))
        step_id=step.id;db.commit()
    with Session(api) as db:
        _verify_external(db,actor=actor(db,verifier),request=db.get(MaterialRequest,request_id),step=db.get(ApprovalStep,step_id),
            registration_id=registered.registration_id,request_version=registered.request_version,step_version=registered.step_version,key=token+'-verify');db.commit()
    checkpoint('external-approved')
    if supply_allocation_recovery:
        from pg16_supply_allocation_recovery_gate import run as supply_recovery_gate
        result = supply_recovery_gate(engines, request_id=request_id, line_id=line_id,
            source_id=source_id, serial_ids=serial_ids, admin=admin, allocator=verifier,
            requester=requester, token=token, post=post, upgrade_check=supply_upgrade_check)
        checkpoint('allocated')
        return dict(requestId=str(request_id), httpCommands=http_commands, stages=stages,
                    supplyAllocationRecovery=result)
    def command(value,label):
        with Session(api) as db:
            body=dict(expected_request_version=db.get(MaterialRequest,request_id).version,**asdict(value(db)))
        path={'allocated':'allocations','reserved':'reservations','picked':'reservation-picks','outbound':'outbounds'}[label]
        result=post(path,body,label,admin)
        checkpoint(label)
        if label in ('allocated','reserved'):
            return SimpleNamespace(**{k:UUID(v) if k.endswith('_id') and isinstance(v,str) else v for k,v in result.items()})
        return result
    supply_result = None
    if supply_browser is not None:
        with Session(api) as db:
            version = db.get(MaterialRequest, request_id).version
        plan = post('supply-tasks', dict(expected_request_version=version,
            request_line_id=str(line_id), supply_type='headquarters_replenishment',
            expected_qty=format(total, '.3f'), note='浏览器联验计划，不表示实际到货'), 'supply', admin)
    allocated_quantity = Decimal(1) if supply_create_browser is not None else total
    allocated_serials = serial_ids[:int(allocated_quantity)]
    allocated=command(lambda db:allocation.AllocationCreateInput(line_id,source_id,allocated_quantity,
        db.get(StockBalance,source_id).version,db.get(StockBalance,source_id).ledger_cursor,allocated_serials),'allocated')
    if supply_browser is not None:
        supply_result = supply_browser(engines=engines, admin=admin, directory=directory,
            request_id=request_id, supply_task_id=UUID(plan['supply_task_id']))
    if supply_create_browser is not None:
        supply_result = supply_create_browser(engines=engines, admin=admin, directory=directory, request_id=request_id)
        post('supply-tasks/' + supply_result['supplyTaskId'], dict(
            expected_request_version=supply_result['requestVersion'], expected_task_version=supply_result['taskVersion'],
            status='cancelled', reference_no=None, expected_date=None,
            comment='剩余需求不再补货，独立取消预计计划'), 'created-plan-cancel', admin, expected_status=200)
    reserved=command(lambda db:reservation.ReservationCreateInput(line_id,allocated.allocation_id,allocated_quantity,
        db.get(StockBalance,source_id).version,db.get(StockBalance,source_id).ledger_cursor,allocated_serials),'reserved')
    fulfillment_quantity=Decimal(1) if partial_release or supply_create_browser is not None else Decimal(2) if unfulfilled_return else total
    fulfillment_serials=serial_ids[:int(fulfillment_quantity)]
    picked=command(lambda db:picking.ReservationPickInput(reserved.reservation_id,fulfillment_quantity,'实物拣货',
        db.get(StockBalance,accounts['reserved']).version,db.get(StockBalance,accounts['reserved']).ledger_cursor,fulfillment_serials),'picked')
    shipped_out=command(lambda db:outbound.OutboundInput(UUID(picked['pick_id']),accounts['in_transit'],fulfillment_quantity,'实物出库',
        db.get(StockBalance,accounts['picking']).version,db.get(StockBalance,accounts['picking']).ledger_cursor,fulfillment_serials),'outbound')
    with Session(api) as db:
        version=db.get(MaterialRequest,request_id).version
    shipping=dict(expected_request_version=version,target_location_id=target_id,target_person_id=person,carrier='合成手工承运',tracking_no=token,
        shipped_at=datetime.now(timezone.utc).isoformat(),lines=[dict(outbound_posting_id=UUID(shipped_out['posting_id']),shipped_qty=format(fulfillment_quantity,'.3f'),serial_ids=fulfillment_serials)])
    sent=post('shipments',shipping,'ship',admin)
    checkpoint('shipped')
    if browser_tail is None:
        if mixed_return:
            with Session(api) as db:
                normal = MyReceiptIn(expected_request_version=db.get(MaterialRequest,request_id).version,
                    shipment_id=sent['shipment_id'], received_at=datetime.now(timezone.utc).isoformat(),
                    lines=[dict(shipment_line_id=sent['lines'][0]['shipment_line_id'],
                        accepted_qty='1.000', rejected_qty='0.000', condition='normal', accepted_serial_ids=fulfillment_serials[:1])])
            normal_received = post('my-receipts', normal.model_dump(mode='json'), 'normal-receive', requester)
            with Session(api) as db:
                normal_inbound = MyInboundIn(expected_request_version=db.get(MaterialRequest,request_id).version,
                    receipt_id=normal_received['receipt_id'], receipt_request_hash=normal_received['request_hash'])
            post('my-inbounds', normal_inbound.model_dump(mode='json'), 'normal-inbound', requester)
            checkpoint('normal-part-posted')
        receipt_quantity = Decimal(1) if mixed_return else fulfillment_quantity
        receipt_serials = fulfillment_serials[1:] if mixed_return else fulfillment_serials
        with Session(api) as db:
            evidence_id = None
            if rejected_receipt:
                from test_material_request_my_receipt import evidence as receipt_evidence
                file, _ = receipt_evidence((db, actor(db, requester)), key=token+'-rejection-evidence')
                evidence_id = file.id
                db.commit()
            receipt_payload=MyReceiptIn(
                expected_request_version=db.get(MaterialRequest,request_id).version,shipment_id=sent['shipment_id'],received_at=datetime.now(timezone.utc).isoformat(),
                lines=[dict(shipment_line_id=sent['lines'][0]['shipment_line_id'],
                    accepted_qty='0.000' if rejected_receipt else format(receipt_quantity,'.3f'), rejected_qty=format(receipt_quantity,'.3f') if rejected_receipt else '0.000',
                    condition='rejected' if rejected_receipt else 'normal',
                    accepted_serial_ids=() if rejected_receipt else receipt_serials,
                    rejected_serial_ids=receipt_serials if rejected_receipt else (), exception_evidence_file_id=evidence_id)])
        from app.material_request_my_receipt_schemas import MyReceiptOut
        received=MyReceiptOut.model_validate(post('my-receipts',receipt_payload.model_dump(mode='json'),'receive',requester))
        history = verify_recipient_history(engines, request_id=request_id, requester=requester,
            receipt=received, tracking=tracking, rejected=rejected_receipt)
        (directory/'recipient-history.json').write_text(json.dumps(history,indent=2)+'\n')
        if rejected_receipt:
            checkpoint('rejected')
            if unfulfilled_return:
                with Session(api) as db:
                    balance=db.get(StockBalance,accounts['reserved'])
                    release_body=dict(expected_request_version=db.get(MaterialRequest,request_id).version,
                        reservation_id=str(reserved.reservation_id), released_qty='1.000', reason='正常入账和拒收后的剩余占用释放',
                        source_balance_version=balance.version, source_ledger_cursor=balance.ledger_cursor,
                        serial_ids=[str(s) for s in serial_ids[2:]])
                released = post('reservation-releases',release_body,'mixed-late-release',admin)
                from app.formal_services.material_request_reservation_release import release_command_status
                with Session(api) as db:
                    db.execute(text('SET TRANSACTION READ ONLY'))
                    recovered=release_command_status(db,actor=actor(db,admin),trace_request_id=token+'-mixed-late-release')
                    assert recovered['release_id']==released['release_id']
                    assert db.get(StockBalance,accounts['reserved']).quantity == 0
                    assert db.get(StockBalance,source_id).quantity == 1
            from app.inventory_models import SerialCurrentPosition
            from app.formal_services.material_request_closure import read_closure
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.get(StockBalance,accounts['in_transit']).quantity == receipt_quantity
                targets = tuple(db.scalars(select(StockAccount).where(StockAccount.location_id == target_id)))
                personal_qty = sum((db.get(StockBalance, target.id).quantity for target in targets
                    if db.get(StockBalance, target.id) is not None), Decimal(0))
                assert personal_qty == (Decimal(1) if mixed_return else Decimal(0))
                assert not db.scalar(select(InboundOrder.id).where(InboundOrder.receipt_id == received.receipt_id))
                for sid in receipt_serials:
                    assert db.get(SerialCurrentPosition,sid).stock_account_id == accounts['in_transit']
                # This flag describes authority only; quantity eligibility is
                # independently enforced by the actual close command.
                state=read_closure(db,actor=actor(db,admin),request_id=request_id)
                assert state.business_status == 'open' and state.close_permitted
                version=db.get(MaterialRequest,request_id).version
            denied=post('close',dict(expected_request_version=version,reason='拒收未处理禁止关闭'),
                'reject-close',admin,expected_status=412)
            assert 'material_request_closure_quantity_incomplete' in json.dumps(denied)
            result = dict(requestId=str(request_id),tracking=tracking,httpCommands=http_commands,
                rejectedHistory=history,personalQuantity=format(personal_qty,'.3f'),inTransitQuantity=format(receipt_quantity,'.3f'),
                closureBlocked=True,rejectedUnsettledQuantity=format(receipt_quantity,'.3f'),passed=True,syntheticData=True,
                realPostgreSQL=True,productionAcceptance=False,unfulfilledRelease=unfulfilled_return)
            (directory/'fulfillment-result.json').write_text(json.dumps(result,indent=2)+'\n')
            return result
        checkpoint('received')
        with Session(api) as db:
            inbound_payload=MyInboundIn(expected_request_version=db.get(MaterialRequest,request_id).version,receipt_id=received.receipt_id,receipt_request_hash=received.request_hash)
        post('my-inbounds',inbound_payload.model_dump(mode='json'),'inbound',requester)
        checkpoint('posted')
    else:
        browser_result = browser_tail(api=api, admin=admin, directory=directory,
            request_id=request_id, shipment=sent, serial_ids=serial_ids)
        http_commands.extend(browser_result['httpCommands'])
        checkpoint('browser-received-and-posted')
    if partial_release:
        from app.formal_services import material_request_reservation_release as release
        from app.formal_services.material_request_remainder import remaining_fulfillment
        from app.inventory_models import StockReservation, SerialCurrentPosition
        with Session(api) as db:
            request=db.get(MaterialRequest,request_id)
            original=db.get(StockReservation,reserved.reservation_id)
            balance=db.get(StockBalance,accounts['reserved'])
            release_body=dict(expected_request_version=request.version,reservation_id=str(original.id),
                released_qty='1.000',reason='部分发运入账后的剩余占用释放',source_balance_version=balance.version,
                source_ledger_cursor=balance.ledger_cursor,serial_ids=[str(s) for s in serial_ids[1:]])
        output=post('reservation-releases',release_body,'late-release',admin)
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            current=remaining_fulfillment(db,actor=actor(db,requester),request_id=request_id)
            row=current.lines[0]
            assert (row.approved_qty,row.posted_qty,row.unreserved_qty,row.reserved_unpicked_qty)==('2.000','1.000','1.000','0.000')
            assert db.get(StockBalance,source_id).quantity==Decimal(1)
            assert db.get(StockBalance,accounts['reserved']).quantity==Decimal(0)
            assert db.get(StockBalance,accounts['picking']).quantity==Decimal(0)
            assert db.get(StockBalance,accounts['in_transit']).quantity==Decimal(0)
            if serial_ids:
                assert db.get(SerialCurrentPosition,serial_ids[1]).stock_account_id==source_id
                first=db.get(SerialCurrentPosition,serial_ids[0])
                assert db.get(StockAccount,first.stock_account_id).location_id==target_id
            recovered=release.release_command_status(db,actor=actor(db,admin),trace_request_id=token+'-late-release')
            assert recovered['release_id']==output['release_id']
        evidence=dict(requestId=str(request_id),release=output,remaining=current.model_dump(mode='json'),
            httpCommands=http_commands,lateReleaseCommitted=True,readOnlyRecovery=True)
        (directory/'partial-release-readback.json').write_text(json.dumps(evidence,indent=2)+'\n')
        return evidence
    if supply_create_browser is not None:
        from app.formal_services.material_request_remainder import remaining_fulfillment
        from app.inventory_models import SerialCurrentPosition
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            remaining = remaining_fulfillment(db, actor=actor(db, requester), request_id=request_id)
            assert len(remaining.lines) == 1
            row = remaining.lines[0]
            assert (row.approved_qty, row.posted_qty, row.unreserved_qty) == ('2.000', '1.000', '1.000')
            assert db.get(StockBalance, source_id).quantity == 1
            assert all(db.get(StockBalance, account_id).quantity == 0 for account_id in accounts.values())
            targets = list(db.execute(select(StockAccount.id, StockBalance.quantity).join(StockBalance).where(
                StockAccount.location_id == target_id, StockAccount.material_id == fixture['material_id'])))
            assert len(targets) == 1 and targets[0].quantity == 1
            if serial_ids:
                assert db.get(SerialCurrentPosition, serial_ids[0]).stock_account_id == targets[0].id
                assert db.get(SerialCurrentPosition, serial_ids[1]).stock_account_id == source_id
        result = dict(requestId=str(request_id), tracking=tracking, personalQuantity='1.000',
            unreservedQuantity='1.000', httpCommands=http_commands, supplyBrowser=supply_result,
            remaining=remaining.model_dump(mode='json'), stages=stages, passed=True, productionAcceptance=False)
        (directory/'fulfillment-result.json').write_text(json.dumps(result, indent=2)+'\n')
        return result
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        targets=list(db.execute(select(StockAccount.id,StockBalance.quantity).join(StockBalance).where(StockAccount.location_id==target_id,StockAccount.material_id==fixture['material_id'])))
        assert len(targets)==1 and targets[0].quantity==Decimal(1)
        assert all(db.get(StockBalance,sid).quantity==0 for sid in (source_id,*accounts.values()))
        assert stages[-1]['personal_inbound_status']=='posted'
        assert stages[-1]['shipment_status']=='shipped'
        if serial_ids:
            from app.inventory_models import SerialCurrentPosition
            assert db.get(SerialCurrentPosition,serial_ids[0]).stock_account_id==targets[0].id
        assert stages[-1]['oam_receipt_status']=='not_occurred'
        assert stages[-1]['logistics_signature_status']=='not_signed'
        # Prove exact inbound coverage from real committed ledger facts. This
        # fixture has no cancellations; it does not exercise a close command.
        from app.formal_services.material_request_inbound_history import verified_posted_receipt_lines
        from app.formal_services.material_request_closure_coverage import line_coverage
        from app.formal_services.material_request_approval_history import verified_final_approved_quantities
        request = db.get(MaterialRequest, request_id)
        lines = tuple(db.scalars(select(MaterialRequestLine).where(
            MaterialRequestLine.request_id == request_id,
            MaterialRequestLine.revision_no == request.revision_no)))
        assert all(line.cancelled_qty == 0 for line in lines)
        assert not tuple(db.scalars(select(MaterialRequestCancellationLineFact.id).where(
            MaterialRequestCancellationLineFact.request_id == request_id)))
        orders = tuple(db.scalars(select(InboundOrder).join(Receipt).where(
            Receipt.shipment_id == UUID(sent['shipment_id']))))
        assert len(orders) == 1
        coverage = line_coverage(approved_by_line=verified_final_approved_quantities(db, request=request),
            cancelled_by_line={}, posted_receipt_lines=verified_posted_receipt_lines(db, request=request, order=orders[0]))
        assert all(line.remaining_qty == 0 for line in coverage)
        assert sum((line.posted_qty for line in coverage), Decimal(0)) == Decimal(1)
    readback = verify_readback_and_guards(engines, request_id=request_id, actor_id=admin, key=token+'-ship', directory=directory)
    result=dict(readback=readback, requestId=str(request_id),personId=str(person),targetLocationId=str(target_id),materialId=str(fixture['material_id']),
        tracking=tracking,sourceOpeningPosted=True,targetZeroOpeningId=str(target_opening),personalQuantity='1.000',passed=True,syntheticData=True,realPostgreSQL=True,
        scope='formal API-role transactions; synthetic identities and external evidence; not UAT',
        fulfillmentHttp=True,httpCommands=http_commands,browserCommands=browser_tail is not None,stages=stages,
        inboundCoverageVerified=True,finalApprovalHistoryVerified=True,businessClosed=False,
        supplyBrowser=supply_result)
    (directory/'fulfillment-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def verify_recipient_history(engines, *, request_id, requester, receipt, tracking, rejected):
    """Actual recipient HTTP read in a read-only transaction, twice as on refresh."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from pg16_stock_scrap_structure_gate import original_columns, facts
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    with owner.connect() as db:
        columns=original_columns(db); before=facts(db,columns)
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db: return load_formal_principal(db,requester)
    with patch.object(app,'dependency_overrides',{**app.dependency_overrides,
            get_db:sessions,get_formal_principal:principal}),TestClient(app) as client:
        responses=[client.get(f'/api/v1/material-requests/{request_id}/my-inbounds/candidates') for _ in range(2)]
    assert all(r.status_code == 200 and 'no-store' in r.headers['cache-control'] for r in responses)
    assert responses[0].json() == responses[1].json()
    row=next(row for row in responses[0].json()['items'] if row['receipt_id'] == str(receipt.receipt_id))
    line=row['detail']['lines'][0]
    assert row['receipt_id'] == str(receipt.receipt_id)
    assert row['status'] == ('no_accepted' if rejected else 'pending')
    assert line['condition'] == ('rejected' if rejected else 'normal')
    assert (line['tracking_mode'] in {'serial','lot_and_serial'}) == (tracking == 'serial')
    expected=receipt.lines[0]
    assert {s['serial_id'] for s in line['accepted_serials']} == {str(s) for s in expected.accepted_serial_ids}
    assert {s['serial_id'] for s in line['rejected_serials']} == {str(s) for s in expected.rejected_serial_ids}
    assert all(s['serial_no'] for s in line['accepted_serials'] + line['rejected_serials'])
    for field in ('stock_account_id','owner_org_id','source_location_id','idempotency_key'):
        assert field not in responses[0].text
    with owner.connect() as db: assert facts(db,columns) == before
    return dict(httpStatus=200,refreshIdentical=True,readOnly=True,unchangedFacts=True,item=row)


def verify_readback_and_guards(engines, *, request_id, actor_id, key, directory):
    """Recovery is read-only; current HTTP keeps receipt and notification axes separate."""
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import DBAPIError
    from app.main import app
    from app.config import get_settings
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.formal_services.material_request_shipment import shipment_command_status
    from pg16_stock_scrap_structure_gate import original_columns, facts

    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        assert db.scalar(text("SELECT has_column_privilege('star_oam_api','public.material_requests','shipment_status','UPDATE')"))
        assert not db.scalar(text("SELECT has_table_privilege('star_oam_api','public.material_requests','UPDATE')"))
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        recovered = shipment_command_status(db, actor=load_formal_principal(db, actor_id),
            request_id=request_id, idempotency_key=key, secret=SECRET)
        assert recovered['command']['shipment_id']
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db:
            return load_formal_principal(db, actor_id)
    settings = get_settings().model_copy(update={'material_request_idempotency_hmac_secret':SECRET.decode()})
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides,
            get_db:sessions, get_formal_principal:principal, get_settings:lambda:settings}), TestClient(app) as client:
        base = '/api/v1/material-requests/'+str(request_id)
        response = client.get(base)
        recovery = client.get(base+'/shipment-command-status', headers={'Idempotency-Key':key})
        assert recovery.status_code == 200, recovery.text
        assert 'no-store' in recovery.headers['cache-control']
        assert recovery.json()['lookup_status'] == 'confirmed'
        assert recovery.json()['command']['shipment_id'] == str(recovered['command']['shipment_id'])
        options = client.get(base+'/shipment-options')
        assert options.status_code == 200, options.text
        assert 'no-store' in options.headers['cache-control']
        assert options.json()['items'] == []
        remainder = client.get(base+'/remaining-fulfillment')
        assert remainder.status_code == 200, remainder.text
        assert 'no-store' in remainder.headers['cache-control']
        assert sum(Decimal(row['posted_qty']) for row in remainder.json()['lines']) == Decimal(1)
        assert all(Decimal(row[key]) == 0 for row in remainder.json()['lines'] for key in (
            'unreserved_qty','reserved_unpicked_qty','picked_unoutbound_qty','outbound_unshipped_qty',
            'shipped_unreceived_qty','accepted_unposted_qty','rejected_unsettled_qty'))
        (directory/'remaining-fulfillment-http-readback.json').write_text(json.dumps(remainder.json(),indent=2)+'\n')
        completion = client.get(base+'/completion-quantities')
        assert completion.status_code == 200, completion.text
        assert 'no-store' in completion.headers['cache-control']
        assert completion.json()['quantity_coverage_complete'] is True
        assert completion.json()['pending_inbound_orders'] == 0
        assert sum(Decimal(row['posted_qty']) for row in completion.json()['lines']) == Decimal(1)
        assert all(row['remaining_qty'] == '0.000' for row in completion.json()['lines'])
        assert 'business_closed' not in completion.json() and 'can_close' not in completion.json()
        (directory/'completion-http-readback.json').write_text(json.dumps(completion.json(), indent=2)+'\n')
        (directory/'recovery-http-readback.json').write_text(json.dumps(recovery.json(), indent=2)+'\n')
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['cache-control']
    body = response.json()
    assert body['states']['shipment_status'] == 'shipped'
    assert body['states']['personal_inbound_status'] == 'posted'
    assert body['states']['logistics_signature_status'] == 'not_signed'
    assert body['states']['oam_receipt_status'] == 'not_occurred'
    (directory/'current-http-readback.json').write_text(json.dumps(body, indent=2)+'\n')
    # These attempts must fail at the database boundary, including a forged
    # version/time increment that bypasses the service entirely.
    for column, value, suffix in (
            ('shipment_status','not_started',''),
            ('shipment_status','not_started',', version=version+1, updated_at=clock_timestamp()'),
            ('logistics_signature_status','signed','')):
        try:
            with api.begin() as db:
                db.execute(text('UPDATE material_requests SET '+column+'=:value'+suffix+' WHERE id=:id'),
                    {'value':value, 'id':request_id})
                db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        except DBAPIError as error:
            assert error.orig.sqlstate in ('23514','42501'), error.orig.sqlstate
        else:
            raise AssertionError('forged independent axis admitted')
    with owner.connect() as db:
        assert facts(db, columns) == before, 'recovery, HTTP read or denied forgery changed facts'
    return dict(originalCommandRecovered=True, recoveryHttpReadOnly=True, shipmentOptionsHttpReadOnly=True, currentHttpReadback=True,
        forgedProjectionDenied=True, fullReadOnlySnapshotUnchanged=True)
