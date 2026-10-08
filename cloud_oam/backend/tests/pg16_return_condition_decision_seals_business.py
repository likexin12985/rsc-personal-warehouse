"""All nine absent decision closures and subsequent decisions on owned PG16.

The caller first commits the real initial submission. Dedicated action grants
are synthetic candidate-only setup; object storage remains FakeStorage.
"""
from uuid import UUID, uuid4
from pathlib import Path
import runpy
from app.return_condition_settlement_input_schema import build_schema
from app.formal_services.stock_loss_corrections import return_condition_decision_request_seals as seal_writer
from app.formal_services.stock_loss_corrections import return_condition_decision_sealed_recovery as sealed_recovery
from pg16_return_condition_decision_seal_gate import run as check_seal_lifecycle
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


def run(owner, api, *, original, source, directory, submitted, schema_preinstalled=False, settlement_check=None, formal=False):
    if formal:
        assert schema_preinstalled, 'formal schema must come from Alembic'
    if not schema_preinstalled:
        folder=Path(__file__).resolve().parents[1]/'alembic/return_condition_candidate'
        _,ddl=runpy.run_path(str(folder/'decision_seals.py'))['statements'](
            build_schema()[0], additional_request_tables=('stock_condition_settlement_requests',))
        (directory/'decision-seal-candidate.sql').write_text('\n\n'.join(x.rstrip().rstrip(';')+';' for x in ddl)+'\n')
        with owner.begin() as connection:
            for statement in ddl: connection.execute(text(statement))
        print('condition decision seal installed PASS',flush=True)
    else:
        with owner.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('public.stock_condition_decision_seals')")) is not None
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
            if formal:
                assert permission is not None, 'formal action permission missing'
            if permission is None:
                permission=Permission(resource='stock_operation',action=action,field_code='',description='Owned condition decision gate only')
                db.add(permission);db.flush()
            link=db.scalar(select(RolePermission).where(RolePermission.role_id==role.id,RolePermission.permission_id==permission.id))
            if formal:
                assert link is not None, 'formal action role grant missing'
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
    retained=[];closed=[];closure_lifecycle=None;result=submitted
    path=(('return_evidence','regional'),('supplement','requester'),('verify_region','regional'),
        ('return_region','headquarters'),('verify_region','regional'),('approve_hq','headquarters'),
        ('cancel_approved','headquarters'))
    for kind,identity in path:
        # Close every supported absent action at its real historical predecessor.
        # Actual business progression below uses fresh keys and proves that old
        # closure references remain readable after newer events are committed.
        closures=()
        if kind=='return_evidence':
            closures=(('return_evidence','regional'),('withdraw','requester'),
                ('verify_region','regional'),('reject_region','regional'))
        elif kind=='supplement': closures=(('supplement','requester'),)
        elif kind=='return_region':
            closures=(('return_region','headquarters'),('reject_hq','headquarters'),('approve_hq','headquarters'))
        elif kind=='cancel_approved': closures=(('cancel_approved','headquarters'),)
        for absent_kind,absent_identity in closures:
            absent_user=users[absent_identity];absent=request(result,absent_kind,absent_user)
            if closure_lifecycle is None:
                closure_lifecycle=check_seal_lifecycle(owner,api,directory=directory,actor_id=absent_user,
                    command=absent,permission_link_id=grants[authority.ACTIONS[absent_kind]])
                absent=closure_lifecycle.pop('closedRequest');closure=closure_lifecycle.pop('closedResult')
            else:
                with Session(api) as db:
                    actor=load_formal_principal(db,absent_user)
                    missing=sealed_recovery.lookup(db,actor=actor,request=absent)
                    assert missing['request_state']=='unknown' and not missing['retry_allowed']
                    closure=seal_writer.seal(db,actor=actor,request=absent)
                    db.commit()
            assert closure['request_state']=='sealed' and closure['stock_effect']=='none'
            assert closure['absence_sealed'] and not closure['retry_allowed'] and not closure['original_preflight_verified']
            assert stock(owner)==unchanged
            closed.append((absent_user,absent,closure))
            print('condition decision seal actual COMMIT '+absent_kind+' PASS',flush=True)
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
    assert len(closed)==9 and {r.action for _,r,_ in closed}=={
        'supplement','withdraw','verify_region','return_evidence','reject_region',
        'return_region','reject_hq','approve_hq','cancel_approved'}
    for user_id,command,outcome in closed:
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            found=sealed_recovery.lookup(db,actor=load_formal_principal(db,user_id),request=command)
            assert found==outcome and found['request_state']=='sealed'
            assert not found['retry_allowed'] and not found['current_stock_verified']
            db.rollback()
    assert snapshot(owner)==before and stock(owner)==unchanged
    settlement_result = None
    if settlement_check is not None:
        settlement_result = settlement_check(result)
        assert settlement_result['passed']
        after_settlement = snapshot(owner)
        for user_id, command, outcome in closed:
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                found = sealed_recovery.lookup(db, actor=load_formal_principal(db, user_id), request=command)
                # The immutable closure survives newer postings. Observation
                # freshness advances with the committed ledger, independently.
                cursor = db.scalar(text('SELECT max(ledger_cursor) FROM inventory_transactions'))
                assert cursor > outcome['observed_ledger_cursor']
                assert found == {**outcome, 'observed_ledger_cursor': cursor}, (found, outcome, cursor)
        assert snapshot(owner) == after_settlement
    return dict(businessSettlement=settlement_result,
        oldSealsSqlReadOnlyAfterSettlement=settlement_result is not None,
        decisionSealLifecycle=closure_lifecycle,actualApiSealCommitActions=[r.action for _,r,_ in closed],
        oldSealSqlReadOnlyAfterNewerDecisions=True,passed=True,actualApiCommitActions=[k for k,_ in path],stockFactsUnchanged=settlement_result is None,decisionStockFactsUnchanged=True,
        exactOriginalSqlReadOnlyLookup=True,effectFailureWholeTransactionRollback=True,
        lateRevocationActualCommitRejected=True,syntheticCandidateGrants=not formal,fakeObjectStorage=True,
        releaseExecuted=settlement_result is not None,conditionExecuted=settlement_result is not None,formalMigration=formal,formalDefaultPermissions=formal,productionAcceptance=False)
