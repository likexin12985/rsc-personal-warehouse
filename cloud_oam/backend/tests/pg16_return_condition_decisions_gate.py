"""Subsequent condition decisions on a caller-owned, fully guarded PG16 fixture.

The caller first commits the real initial submission. Dedicated action grants
are synthetic candidate-only setup; object storage remains FakeStorage.
"""
from uuid import UUID, uuid4
from unittest.mock import patch
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Organization, Role, RolePermission, Permission
from app.return_condition_decision_requests import ConditionDecision
from app.formal_services import formal_files
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_decision_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from app.formal_services.stock_loss_corrections import return_condition_evidence as evidence
from formal_file_integrity import FileUploadIntentInput
from pg16_return_condition_authority_gate import owned_role_connection
from pg16_return_condition_submission_gate import snapshot
from test_formal_access import make_user, assign
from test_formal_files_service import FakeStorage, SECRET

STOCK_TABLES=('stock_accounts','stock_balances','inventory_transactions','inventory_movements',
    'inventory_movement_serials','serial_current_positions','inventory_ledger_heads',
    'stock_condition_cases','stock_condition_serials','stock_operation_orders','stock_operation_lines')


def stock(owner):
    values=snapshot(owner)
    return {name:values[name] for name in STOCK_TABLES}


def run(owner, api, *, original, source, directory, submitted):
    with Session(owner) as db:
        org=db.get(Organization,UUID(source['owner_org_id']))
        assert org is not None
        roles={r.code:r for r in db.scalars(select(Role))}
        reviewer,_=make_user(db,org,name='Synthetic independent condition reviewer')
        assign(db,reviewer,roles['provincial_manager'],scope_type='organization',scope_id=str(org.id))
        reviewer_id=reviewer.id
        grants={}
        for action in sorted(set(authority.ACTIONS.values())):
            role=roles['admin' if action in ('review_return_condition_headquarters','cancel_return_condition_approval') else 'provincial_manager']
            permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',Permission.action==action,Permission.field_code==''))
            if permission is None:
                permission=Permission(resource='stock_operation',action=action,field_code='',description='Owned condition decision gate only')
                db.add(permission);db.flush()
            link=db.scalar(select(RolePermission).where(RolePermission.role_id==role.id,RolePermission.permission_id==permission.id))
            if link is None:
                link=RolePermission(role_id=role.id,permission_id=permission.id,effect='allow');db.add(link);db.flush()
            assert link.effect=='allow'
            grants[action]=link.id
        db.commit()
    users={'requester':original['receiverUserId'],'regional':reviewer_id,'headquarters':original['administratorUserId']}
    assert len(set(users.values()))==3
    unchanged=stock(owner)
    storage=FakeStorage()

    def request(previous, kind, user_id):
        files=()
        if kind in ('supplement','verify_region'):
            with Session(api) as db:
                actor=load_formal_principal(db,user_id)
                upload=formal_files.create_file_upload_intent(db,actor=actor,
                    command=FileUploadIntentInput(purpose=evidence.PURPOSE,original_filename='原生审核凭证.jpg',
                        size_bytes=128,mime_type='image/jpeg',sha256='a'*64),
                    idempotency_key=uuid4().hex,idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,
                    storage=storage,upload_ttl_seconds=600)
                row=db.get(FileObject,upload.file_id);storage.materialize(row)
                formal_files.complete_file_upload(db,actor=actor,file_id=row.id,trace_request_id=uuid4().hex,storage=storage)
                files=(row.id,);db.commit()
        return ConditionDecision(action=kind,case_id=UUID(previous['case_id']),expected_event_id=UUID(previous['event_id']),
            expected_event_hash=previous['request_hash'],reason='实际PG16独立审核与请求回查',evidence_file_ids=files,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)

    probe=request(submitted,'return_evidence',users['regional'])
    before=snapshot(owner)
    record=business.record
    def broken(db,**kwargs):
        record(db,**kwargs)
        raise RuntimeError('owned condition decision injected failure')
    with Session(api) as db,patch.object(business,'record',broken):
        try:decisions.decide(db,actor=load_formal_principal(db,users['regional']),request=probe)
        except RuntimeError as error:
            assert str(error)=='owned condition decision injected failure';db.rollback()
        else:raise AssertionError('decision fault injection not reached')
    assert snapshot(owner)==before
    with owned_role_connection(owner,directory) as connection:
        connection.execute(text('SET ROLE star_oam_api'));connection.commit()
        with Session(bind=connection) as db:
            decisions.decide(db,actor=load_formal_principal(db,users['regional']),request=probe)
            db.execute(text('SET LOCAL ROLE star_oam_migrator'))
            db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),dict(id=grants['review_return_condition_regional']))
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            try:db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514' and 'condition' in str(error.orig);db.rollback()
            else:raise AssertionError('late revoked decision committed')
    assert snapshot(owner)==before
    retained=[];result=submitted
    path=(('return_evidence','regional'),('supplement','requester'),('verify_region','regional'),
        ('return_region','headquarters'),('verify_region','regional'),('approve_hq','headquarters'),
        ('cancel_approved','headquarters'))
    for kind,identity in path:
        user_id=users[identity];command=request(result,kind,user_id)
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            actor=load_formal_principal(db,user_id)
            missing=recovery.lookup(db,actor=actor,request=command)
            assert missing['request_state']=='unknown' and not missing['retry_allowed']
            result=decisions.decide(db,actor=actor,request=command);db.commit()
        assert result['action']==kind and result['stock_effect']=='none'
        assert result['posting_transaction_id'] is None and result['posting_movement_id'] is None
        assert stock(owner)==unchanged
        retained.append((user_id,command,result))
        print('condition decision actual COMMIT '+kind+' PASS',flush=True)
    assert result['status']=='cancelled_pending_release'
    before=snapshot(owner)
    for user_id,command,outcome in retained:
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            found=recovery.lookup(db,actor=load_formal_principal(db,user_id),request=command)
            assert found['request_state']=='found' and found['result']==outcome
            assert found['current_case_status']=='cancelled_pending_release'
            assert not found['retry_allowed'] and not found['current_stock_verified']
            db.rollback()
    assert snapshot(owner)==before and stock(owner)==unchanged
    return dict(passed=True,actualApiCommitActions=[k for k,_ in path],stockFactsUnchanged=True,
        exactOriginalSqlReadOnlyLookup=True,effectFailureWholeTransactionRollback=True,
        lateRevocationActualCommitRejected=True,syntheticCandidateGrants=True,fakeObjectStorage=True,
        releaseExecuted=False,conditionExecuted=False,formalMigration=False,productionAcceptance=False)
