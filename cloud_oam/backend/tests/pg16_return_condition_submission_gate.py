"""Full private submission on actual predecessor history, owned socket PG16 only.

Candidate DDL and test grants persist only in the disposable owned database.
No guard is disabled, no production catalog is activated, and storage is fake.
Unlike component probes this calls the actual service and commits as API.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import runpy
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Permission, Role, RoleAssignment, RolePermission, NotificationEvent
from app.inventory_models import FormalMaterial, StockAccount, StockBalance, SerialCurrentPosition
from app.models import User
from app.return_condition_guards import statements as guards
from app.return_condition_requests import ConditionSubmit
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_services import formal_files, stock_return_inbound_recovery
from app.formal_services.stock_loss_corrections import return_condition_submission as subject
from app.formal_services.stock_loss_corrections import return_condition_submission_source as preparation
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_evidence as evidence
from app.formal_services.stock_loss_corrections import return_condition_posting_authority as permits
from app.formal_services.stock_loss_corrections import return_condition_request_inputs as inputs
from app.formal_services import inventory_posting as posting
from formal_file_integrity import FileUploadIntentInput
from migration_script_cache import cache_migration_compilation
from pg16_return_condition_authority_gate import owned_role_connection
from pg16_return_condition_schema import compile_structure
from pg16_stock_scrap_structure_gate import facts, original_columns
from test_formal_files_service import FakeStorage, SECRET


def snapshot(owner):
    with owner.connect() as connection:
        return facts(connection, original_columns(connection))


def run(owner, api, *, original, source, directory, receipt_history_only=False, complete_schema=False, formal=False):
    folder = Path(__file__).resolve().parents[1] / 'alembic'
    if formal:
        assert complete_schema and not receipt_history_only
        with owner.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261230_0181'
        from app.database_security import validate_production_database_security
        validate_production_database_security(api, expected_runtime_role='star_oam_api',
            expected_migration_role='star_oam_migrator')
        receipt_candidate = runpy.run_path(str(folder / 'return_condition_candidate/forward_receipt_history.py'))
        statements = []
        compiled = ''
    else:
        metadata, tables, _, statements = compile_structure()
        statements.extend(guards(posting=True, identity=True))
        with cache_migration_compilation(folder / 'versions'):
            file_candidate = runpy.run_path(str(folder / 'return_condition_candidate/files.py'))
            account_candidate = runpy.run_path(str(folder / 'return_condition_candidate/forward_account.py'))
            receipt_candidate = runpy.run_path(str(folder / 'return_condition_candidate/forward_receipt_history.py'))
        statements.extend(file_candidate['statements']())
        dispatch_candidate = runpy.run_path(str(folder / 'return_condition_candidate/forward_dispatch.py'))
        for name in ('evidence', 'authority'):
            statements.extend(runpy.run_path(str(folder / f'return_condition_candidate/{name}.py'))['statements']())
        statements.extend(dispatch_candidate['statements']())
        statements.extend(account_candidate['statements']())
        input_table,input_statements=runpy.run_path(str(folder / 'return_condition_candidate/request_inputs.py'))['statements'](metadata)
        statements.extend(input_statements)
        key_table,key_statements=runpy.run_path(str(folder / 'return_condition_candidate/request_keys.py'))['statements'](metadata)
        statements.extend(key_statements)
        statements.extend(runpy.run_path(str(folder / 'return_condition_candidate/business_effects.py'))['statements']())
        seal_table,seal_statements=runpy.run_path(str(folder / 'return_condition_candidate/seals.py'))['statements'](metadata)
        statements.extend(seal_statements)
        settlement_tables,settlement_statements=runpy.run_path(str(folder / 'return_condition_candidate/settlement_inputs.py'))['statements'](metadata)
        statements.extend(settlement_statements)
        if complete_schema:
            _, decision_statements = runpy.run_path(str(folder / 'return_condition_candidate/decision_seals.py'))['statements'](
                metadata, additional_request_tables=('stock_condition_settlement_requests',))
            statements.extend(decision_statements)
        settlement_account=runpy.run_path(str(folder / 'return_condition_candidate/forward_settlement_account.py'))
        settlement_transition=settlement_account['compile_transition'](folder / 'return_condition_candidate/forward_account.py')
        # Authentication isolation is now supplied by formal 0166. Never install
        # the old 0165 diagnostic patch over the current, catalog-verified body.
        from app.scrap_authentication_fence_security import DATA as auth_fence_catalog
        expected_auth_fence = auth_fence_catalog['patches'][0]['after']
        tables=(*tables,input_table)
        compiled = '\n\n'.join(s.rstrip().rstrip(';')+';'
            for s in (*statements,settlement_transition['definition'], *receipt_candidate['statements']()))+'\n'
        (Path(directory)/'condition-submission-candidate.sql').write_text(compiled)
        # Ownership is independently validated against the live PID/datadir/systemid.
        with owned_role_connection(owner, directory) as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '20261215_0166'
            assert connection.scalar(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
                {'signature': 'public.' + expected_auth_fence['signature']}) == expected_auth_fence['definition']
            for candidate in (file_candidate, dispatch_candidate, account_candidate):
                actual = connection.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:s)'),
                    {'s': candidate['SIGNATURE']})
                assert actual == candidate['EXPECTED_BODY'], dict(signature=candidate['SIGNATURE'],
                    expected=candidate['EXPECTED_SHA256'], actual=hashlib.sha256((actual or '').encode()).hexdigest())
            connection.rollback()
            with connection.begin():
                for statement in statements:
                    connection.execute(text(statement))
                for table in tables:
                    connection.execute(text('GRANT SELECT,INSERT ON public.'+table.name+' TO star_oam_api'))
                assert connection.scalar(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),
                    {'signature': 'public.' + expected_auth_fence['signature']}) == expected_auth_fence['definition']
        # The forward installer requires the actual migrator login, never a DBA SET ROLE session.
        with owner.begin() as connection:
            settlement_account['install'](connection,folder / 'return_condition_candidate/forward_account.py')
    region = UUID(source['owner_org_id'])
    with Session(owner) as db:
        user = db.get(User, original['receiverUserId'])
        role = db.scalar(select(Role).where(Role.code == 'provincial_manager'))
        grant = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == user.id,
            RoleAssignment.role_id == role.id, RoleAssignment.scope_type == 'organization',
            RoleAssignment.scope_id == str(region), RoleAssignment.revoked_at.is_(None),
            RoleAssignment.valid_to.is_(None)))
        if grant is None:
            db.add(RoleAssignment(user_id=user.id, role_id=role.id, scope_type='organization',
                scope_id=str(region), valid_from=datetime.now(timezone.utc)-timedelta(days=1),
                status='active', assigned_by=user.id, reason='Owned condition business gate'))
        if formal:
            permission = db.scalars(select(Permission).where(Permission.resource == 'stock_operation',
                Permission.action == 'submit_return_condition', Permission.field_code == '')).one()
            link = db.scalars(select(RolePermission).where(RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id)).one()
            assert link.effect == 'allow', 'formal submission permission is denied'
        else:
            permission = Permission(resource='stock_operation', action='submit_return_condition',
                field_code='', description='Owned disposable business gate only')
            db.add(permission); db.flush()
            link = RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')
            db.add(link); db.flush()
        link_id = link.id
        db.commit()
    from pg16_return_condition_receipt_history_gate import run as check_receipt_history
    before_receipt_patch = snapshot(owner)
    receipt_history = check_receipt_history(owner, directory=directory, original=original,
        candidate=receipt_candidate, preinstalled=formal)
    assert snapshot(owner) == before_receipt_patch
    (Path(directory)/'condition-receipt-history-checks.json').write_text(json.dumps(receipt_history, indent=2)+'\n')
    print('condition_historical_receipt_compatibility PASS', flush=True)
    if receipt_history_only:
        return dict(receiptHistory=receipt_history, actualApiSealCommit=False,
            fullSubmissionGate=False)
    storage = FakeStorage()
    with Session(api) as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        actor = load_formal_principal(db, original['receiverUserId'])
        assert actor.role_codes == ('provincial_manager',)
        upload = formal_files.create_file_upload_intent(db, actor=actor,
            command=FileUploadIntentInput(purpose=evidence.PURPOSE, original_filename='原生纠正核对.jpg',
                size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
            idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET,
            trace_request_id=uuid4().hex, storage=storage, upload_ttl_seconds=600)
        row = db.get(FileObject, upload.file_id); storage.materialize(row)
        formal_files.complete_file_upload(db, actor=actor, file_id=row.id,
            trace_request_id=uuid4().hex, storage=storage)
        file_id = row.id
        db.commit()
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
        proof = preparation.inspect_submission_source(db, actor=actor,
            inbound_line_id=UUID(source['selection']['inbound_line_id']))
        doc = proof.document
        material = db.get(FormalMaterial, UUID(doc['material_id']))
        serials = [dict(serial_id=s['serial_id'], sku_code=material.sku_code,
            serial_no=s['serial_no'], qr_code=s['qr_code']) for s in doc['serials']]
        quantity = '1' if serials else '0.025'
        assert Decimal(quantity) <= Decimal(original['damagedQuantity'])
        command = ConditionSubmit(action='submit_return_condition',
            inbound_line_id=UUID(doc['selection']['inbound_line_id']), expected_source_hash=proof.evidence_hash,
            quantity=quantity, serial_verifications=serials, evidence_file_ids=(file_id,),
            # A legal client request ID must not be mistaken for the internal
            # audit namespace by case/event/input/key-binding COMMIT fences.
            reason='原生完整事务核验原破损验收成色', request_id='condition-seal:'+uuid4().hex,
            idempotency_key=uuid4().hex)
        source_balance = db.get(StockBalance, UUID(doc['source_account_id'])).quantity
        db.rollback()
    print('condition_formal_migration_admitted PASS' if formal else 'condition_candidate_installed PASS',flush=True)
    from pg16_return_condition_seal_gate import authentication_without_inventory_wait
    authentication_safe=authentication_without_inventory_wait(api,original)
    assert authentication_safe is True
    print('condition_authentication_compatibility PASS',flush=True)
    from pg16_return_condition_seal_gate import register as register_seal
    from pg16_return_condition_key_gate import refused as refuse_seal
    with Session(api) as db:
        probe=command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
        admitted_seal=register_seal(db,load_formal_principal(db,original['receiverUserId']),probe)
        assert admitted_seal['created'] is True
        refuse_seal(db,db.commit,'condition seal requires one audit and no business effects')
    print('condition_seal_source_and_missing_audit PASS',flush=True)
    baseline = snapshot(owner)
    from pg16_return_condition_seal_admission_gate import run as seal_admission_checks
    seal_admission = seal_admission_checks(owner, api, directory=directory, original=original,
        command=command, permission_link_id=link_id)
    assert snapshot(owner) == baseline
    from pg16_return_condition_key_gate import before_commit as key_prechecks, after_commit as key_postchecks
    key_refused=key_prechecks(api,command=command,original=original)
    assert snapshot(owner)==baseline
    from pg16_return_condition_effect_gate import before_commit as effect_prechecks, after_commit as effect_postchecks
    effect_refused=effect_prechecks(api,command=command,original=original)
    assert snapshot(owner)==baseline
    from pg16_return_condition_coordinate_gate import run as coordinate_checks
    coordinate_missing = coordinate_checks(api, command=command, original=original)
    assert snapshot(owner) == baseline
    with Session(api) as db:
        source_row = db.get(StockAccount, UUID(doc['source_account_id']))
        dimensions = {k:getattr(source_row,k) for k in ('owner_org_id','custodian_person_id',
            'location_id','material_id','condition_code','lot_id')}
        db.add(StockAccount(id=uuid4(), **dimensions, availability_bucket='frozen'))
        try:
            db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate == '23514'
            assert 'API stock account insert must terminate' in str(error.orig)
            db.rollback()
        else:
            raise AssertionError('unbound frozen account accepted')
    assert snapshot(owner) == baseline
    input_refused=[]
    for change in ('missing','quantity','request_id','hash','sku'):
        def corrupted_input(db, *, request, case, event):
            if change=='missing': return None
            document=inputs.canonical(request)
            material=db.get(FormalMaterial,UUID(case['source_jsonb']['material_id']))
            if change=='quantity': document['quantity']='999'
            if change=='request_id': document['request_id']=uuid4().hex
            db.execute(inputs.table().insert(),dict(event_id=event['id'],created_at=event['created_at'],
                actor_user_id=event['actor_user_id'],actor_person_id=event['actor_person_id'],
                authorization_version=event['authorization_version'],request_id=event['request_id'],
                idempotency_key_hash=event['idempotency_key_hash'],
                material_sku_code='incorrect-input-sku' if change=='sku' else material.sku_code,
                input_jsonb=document,input_hash='0'*64 if change=='hash' else posting._canonical_hash(document)))
        committing=False
        with Session(api) as db, patch.object(inputs,'record',corrupted_input), patch.object(inputs,'match_original',return_value=None):
            try:
                subject.submit(db,actor=load_formal_principal(db,original['receiverUserId']),request=command)
                committing=True
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514'
                expected=('condition original input current SKU required' if change=='sku' else
                    'condition original input binding required' if change=='missing' else 'condition exact original input required')
                assert expected in str(error.orig),str(error.orig)
                db.rollback()
                input_refused.append(dict(change=change,atActualCommit=committing))
            else:
                raise AssertionError('corrupted original input committed: '+change)
        assert snapshot(owner)==baseline
    rollback_checks = []
    record = business.record
    for after_effects in (False, True):
        def broken(db, **kwargs):
            assert db.scalar(text("SELECT count(*) FROM inventory_transactions WHERE source_document_type='stock_condition_event'")) == 1
            if after_effects:
                record(db, **kwargs)
            raise RuntimeError('owned condition partial-write failure')
        with Session(api) as db, patch.object(business, 'record', broken):
            try:
                subject.submit(db, actor=load_formal_principal(db, original['receiverUserId']), request=command)
            except RuntimeError as error:
                assert str(error) == 'owned condition partial-write failure'
                db.rollback()
            else:
                raise AssertionError('fault injection did not execute')
            assert permits.KEY not in db.info
        assert snapshot(owner) == baseline
        rollback_checks.append('after_business_effects' if after_effects else 'before_business_effects')
    # The owner can mutate test authority after a real service write. COMMIT
    # still runs under API; the SQL event fence must reject and restore all facts.
    with owned_role_connection(owner, directory) as connection:
        connection.execute(text('SET ROLE star_oam_api')); connection.commit()
        with Session(bind=connection) as db:
            subject.submit(db, actor=load_formal_principal(db, original['receiverUserId']), request=command)
            db.execute(text('SET LOCAL ROLE star_oam_migrator'))
            db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"), {'id':link_id})
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            try:
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate == '23514'
                assert 'condition current' in str(error.orig), str(error.orig)
                db.rollback()
            else:
                raise AssertionError('late authority change accepted at actual COMMIT')
    assert snapshot(owner) == baseline
    from pg16_return_condition_key_gate import commit_with_late_header
    result,key_race=commit_with_late_header(api,command=command,original=original)
    with Session(api) as db:
        cases, events = (subject.tables()[n] for n in ('stock_condition_cases','stock_condition_events'))
        case = db.execute(select(cases).where(cases.c.id==UUID(result['case_id']))).mappings().one()
        event = db.execute(select(events).where(events.c.id==UUID(result['event_id']))).mappings().one()
        assert business.verify(db, case=case, event=event, recipient=UUID(doc['custodian_person_id'])) == result
        from app.formal_services.stock_loss_corrections.return_condition_ledger_facts import verify_event_posting
        edge = verify_event_posting(db, event=event,
            serial_ids=tuple(s.serial_id for s in command.serial_verifications))
        assert edge.transaction_id == case['freeze_transaction_id']
        assert edge.movement_id == case['freeze_movement_id']
        retained_input=inputs.verify(db,case=case,event=event)
        assert inputs.match_original(db,request=command,case=case,event=event)==retained_input['input_hash']
        assert db.get(StockBalance, case['source_account_id']).quantity == source_balance-Decimal(quantity)
        assert db.get(StockBalance, case['frozen_account_id']).quantity == Decimal(quantity)
        for serial in command.serial_verifications:
            assert db.get(SerialCurrentPosition, serial.serial_id).stock_account_id == case['frozen_account_id']
        note = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id==result['event_id']))
        assert note.status == 'pending'
        inbound = db.get(StockOperationReturnInbound, UUID(original['inboundId']))
        recovered = stock_return_inbound_recovery.lookup_return_inbound_request(db,
            actor=load_formal_principal(db, original['receiverUserId']), receipt_id=inbound.receipt_id,
            request_id=original['inboundRequestId'])
        assert recovered['request_hash'] == original['inboundRequestHash']
        db.rollback()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        from app.formal_services.stock_loss_corrections.return_condition_history_read import read as read_condition_history
        history = read_condition_history(db, actor=load_formal_principal(db, original['receiverUserId']),
            inbound_line_id=command.inbound_line_id)
        assert history.graph.event_ids == (UUID(result['event_id']),)
        assert history.graph.projection.held_quantity == command.quantity
        assert history.graph.projection.corrected_quantity == 0
        assert history.current_stock_verified is False and history.retry_allowed is False
        from app.formal_services.stock_loss_corrections.return_condition_recovery import lookup as lookup_condition_request
        recovered_condition = lookup_condition_request(db,
            actor=load_formal_principal(db, original['receiverUserId']), request=command)
        assert recovered_condition['request_state'] == 'found' and recovered_condition['result'] == result
        assert recovered_condition['retry_allowed'] is False and recovered_condition['current_stock_verified'] is False
        db.rollback()
    # Old immutable rows are retained byte-for-byte. Only explicitly mutable
    # inventory projections and chain heads may change; new rows may append.
    after = snapshot(owner)
    key_persisted=key_postchecks(owner,api,command=command,result=result)
    assert snapshot(owner)==after
    coordinate_found = coordinate_checks(api,command=command,original=original,found=True)
    assert snapshot(owner) == after
    effect_persisted=effect_postchecks(owner,api,command=command,original=original,result=result)
    expanded_snapshot=snapshot(owner)
    expected={name:[json.loads(row) for row in rows] for name,rows in after.items()}
    actual={name:[json.loads(row) for row in rows] for name,rows in expanded_snapshot.items()}
    for row in expected['notification_events']:
        if row['id']==effect_persisted['notificationEventId']: row['status']='expanded'
    added=set(effect_persisted['createdDeliveryIds'])
    assert len([r for r in actual['notification_deliveries'] if r['id'] in added])==len(added)
    actual['notification_deliveries']=[r for r in actual['notification_deliveries'] if r['id'] not in added]
    canonical=lambda rows: sorted(json.dumps(row,sort_keys=True) for row in rows)
    assert {k:canonical(v) for k,v in expected.items()}=={k:canonical(v) for k,v in actual.items()}
    from pg16_return_condition_seal_gate import run as seal_checks
    print('condition_permanent_seal_checks START',flush=True)
    permanent_seal=seal_checks(owner,api,directory=directory,original=original,command=command,permission_link_id=link_id)
    after=snapshot(owner)
    mutable = {'stock_balances','serial_current_positions','inventory_ledger_heads','audit_chain_heads'}
    for name, rows in baseline.items():
        if name not in mutable:
            assert set(rows) <= set(after[name]), 'old facts changed: '+name
    return dict(passed=True, realApiRole=True, actualBusinessCommit=True,
        oldApplicationHistory=True, fakeObjectStorage=True, completedFileCommit=True,
        rollbackChecks=rollback_checks, lateAuthorityCommitRejected=True,
        unboundFrozenAccountCommitRejected=True,
        originalInputVerified=True,corruptedOriginalInputsRefused=input_refused,
        historicalPostingEvidenceVerified=True,
        scopedConditionHistorySqlReadOnly=True,
        originalConditionRequestSqlReadOnly=True,
        originalInboundRequestRetained=True, immutableOldRowsRetained=True,
        result=result, pendingNotification=False, notificationStatusAfterChecks='expanded',
        statements=0 if formal else len(statements)+len(receipt_candidate['statements']()),
        executedDdlSha256=None if formal else hashlib.sha256(compiled.encode()).hexdigest(),
        exactInitialRequestLookupImplemented=True, completeRequestRecoveryLifecycle=False,
        crossActionRequestEvidence=dict(missing=coordinate_missing,found=coordinate_found,
            actualApiRole=True,syntheticOrphansRolledBack=True,durableRegistry=True,absenceSeals=False),
        durableConditionKeys=dict(refusals=key_refused,persisted=key_persisted,race=key_race),
        durableBusinessEffects=dict(missing=effect_refused,persisted=effect_persisted),
        absenceSealAdmission=seal_admission, permanentInitialRequestSeal=permanent_seal,
        authenticationEvidenceDoesNotWaitForInventory=authentication_safe,
        historicalReceiptCompatibility=receipt_history,
        legalPrefixedClientRequestCommitted=command.request_id.startswith('condition-seal:'),
        reviewExecuteReleaseImplemented=False,
        formalMigration=formal, formalDefaultPermissions=formal, productionAcceptance=False)
