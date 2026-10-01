"""Prepare real opening/loss approvals for candidate seal boundary checks.

All grants, identities, files and locations are disposable fixtures. Inventory
is established by the shared independent stocktake/opening lifecycle and then
moved only by the actual API-role loss and disposition services.
"""
from uuid import UUID,uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Organization,Permission,Role,RolePermission,FileObject
from app.inventory_models import StockLocation,CustodyAssignment
from app.stock_operation_models import StockOperationLine,StockLossHeadquartersDecision
from app.stock_loss_schemas import StockLossSubmitIn,StockLossRegionalReviewIn,StockLossHeadquartersReviewIn,StockLossDispositionExecuteIn
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import formal_files,stock_loss_commands,stock_loss_plan
from app.formal_services import stock_loss_regional_reviews as regional,stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_disposition_plan as disposition_plan,stock_loss_return_plan as return_plan
from test_formal_access import make_user,assign
from test_formal_files_service import FakeStorage,SECRET
from pg16_loss_execution_seal_boundaries import verify
from pg16_loss_execution_auth_isolation import verify as verify_authentication_isolation


def run(context):
    if context['tracking'] not in ('quantity','serial'):raise ValueError('explicit tracking required')
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        source=db.get(StockLocation,context['location_id']);target=db.get(StockLocation,source.parent_id)
        manager,_=make_user(db,db.get(Organization,source.owner_org_id),name='Synthetic execution-seal regional reviewer')
        roles={row.code:row for row in db.scalars(select(Role))}
        assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(source.owner_org_id))
        for action,role in ((regional.ACTION,'provincial_manager'),(headquarters.ACTION,'admin'),('dispose_loss','admin'),('read','admin')):
            permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',
                Permission.action==action,Permission.field_code==''))
            if permission is None:
                permission=Permission(resource='stock_operation',action=action,field_code='',description='Synthetic 0158 gate only')
                db.add(permission);db.flush()
            grant=db.scalar(select(RolePermission).where(RolePermission.role_id==roles[role].id,
                RolePermission.permission_id==permission.id))
            if grant is None:db.add(RolePermission(role_id=roles[role].id,permission_id=permission.id,effect='allow'))
            else:assert grant.effect=='allow'
        custody=db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id==target.id))
        assert target.custodian_person_id is not None and custody is not None
        assert custody.custodian_person_id==target.custodian_person_id
        transit=StockLocation(code='LOSS-SEAL-TRANSIT-'+uuid4().hex,name='Synthetic execution seal return transit',
            location_type='transit',parent_id=target.id,owner_org_id=target.owner_org_id,status='active')
        db.add(transit);db.commit()
        manager_id,target_id,transit_id=manager.id,target.id,transit.id
    authentication_isolation=verify_authentication_isolation(context['engines'], user_id=context['admin_id'])
    print('PG16 authentication session and audit commit while inventory ledger locked PASS',flush=True)
    selection=context['request'];previous=None;proofs=[]
    for kind in ('restore_available','convert_used','convert_damaged','return_to_region'):
        with Session(api) as db:
            engineer=load_formal_principal(db,context['engineer_id'])
            if previous is not None:
                storage=FakeStorage()
                upload=formal_files.create_file_upload_intent(db,actor=engineer,
                    command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='synthetic-seal.jpg',
                        size_bytes=128,mime_type='image/jpeg',sha256='a'*64),
                    idempotency_key=uuid4().hex,idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,
                    storage=storage,upload_ttl_seconds=60)
                storage.materialize(db.get(FileObject,upload.file_id))
                formal_files.complete_file_upload(db,actor=engineer,file_id=upload.file_id,
                    trace_request_id=uuid4().hex,storage=storage)
                db.commit()
                selection=selection.model_copy(update={'evidence_file_ids':(upload.file_id,),
                    'lines':(selection.lines[0].model_copy(update={'stock_account_id':UUID(previous['target_account_id'])}),)})
            prepared,_=stock_loss_plan.preview_loss(db,actor=engineer,request=selection)
            submitted=stock_loss_commands.submit_loss(db,actor=engineer,request=StockLossSubmitIn(
                **selection.model_dump(),expected_plan_hash=prepared.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
        with Session(api) as db:
            review=regional.verify_regional_loss(db,actor=load_formal_principal(db,manager_id),request=StockLossRegionalReviewIn(
                operation_id=submitted.operation_id,expected_submission_plan_hash=prepared.plan_hash,
                comment='Synthetic independent loss verification',request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
        with Session(api) as db:
            line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==submitted.operation_id)).one()
            principal=load_formal_principal(db,context['admin_id'])
            approved=headquarters.approve_headquarters_loss(db,actor=principal,request=StockLossHeadquartersReviewIn(
                operation_id=submitted.operation_id,expected_submission_plan_hash=prepared.plan_hash,
                regional_review_id=review.review_id,expected_regional_review_hash=review.request_hash,
                decisions=(dict(line_id=line.id,disposition=kind,reason='Synthetic reviewed disposition for seal race'),),
                comment='Synthetic independent headquarters approval',request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
            decision=db.scalars(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id==approved.review_id)).one()
            values=dict(headquarters_decision_id=decision.id,expected_headquarters_review_hash=approved.request_hash,
                expected_submission_plan_hash=prepared.plan_hash,expected_plan_hash='0'*64,
                request_id=uuid4().hex,idempotency_key=uuid4().hex)
            if kind=='return_to_region':
                command=StockLossReturnExecuteIn(**values,target_location_id=target_id,transit_location_id=transit_id)
                preview=return_plan.preview_loss_return(db,actor=principal,request=command);flow='return'
            else:
                command=StockLossDispositionExecuteIn(**values)
                preview=disposition_plan.preview_disposition(db,actor=principal,request=command);flow='disposition'
            command=command.model_copy(update={'expected_plan_hash':preview['plan_hash']})
        actual,previous,proof=verify(context,command,flow)
        assert previous['disposition']==kind
        proofs.append(dict(disposition=kind,**proof))
        print('PG16 candidate '+context['tracking']+' '+kind+': execution/seal SQL and concurrency PASS',flush=True)
    return dict(passed=True,tracking=context['tracking'],cases=proofs,
        fixtureStockUsesActualOpening=True,syntheticGrantsOnly=True,
        authenticationIsolation=authentication_isolation,
        formalMigrationInstalled=False,productionAcceptance=False)
