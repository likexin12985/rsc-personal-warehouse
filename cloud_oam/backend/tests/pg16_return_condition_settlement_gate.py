"""Actual API release, fresh freeze, approval and execution in an owned PG16 DB.

Runs only after the complete initial submission/seal gate. Synthetic candidate
grants and FakeStorage are explicit; no live business or production acceptance.
"""
from uuid import UUID, uuid4
from unittest.mock import patch
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Organization, Role, RolePermission, Permission, NotificationEvent
from app.inventory_models import FormalMaterial, InventorySerial, InventoryTransaction, StockAccount, StockBalance, SerialCurrentPosition
from app.return_condition_decision_requests import ConditionDecision
from app.return_condition_requests import ConditionSubmit
from app.return_condition_settlement_requests import ConditionSettlement
from app.formal_services import formal_files, inventory_posting, inventory_query
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_submission as initial
from app.formal_services.stock_loss_corrections import return_condition_submission_source as preparation
from app.formal_services.stock_loss_corrections import return_condition_settlement as settlement
from app.formal_services.stock_loss_corrections import return_condition_settlement_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_history as graph
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_authority as authority
from app.formal_services.stock_loss_corrections import return_condition_evidence as evidence
from formal_file_integrity import FileUploadIntentInput
from pg16_return_condition_authority_gate import owned_role_connection
from pg16_return_condition_submission_gate import snapshot
from test_formal_access import make_user, assign
from test_formal_files_service import FakeStorage, SECRET

ERRORS = (inventory_posting.InventoryPostingError, inventory_query.InventoryReadError)


def run(owner, api, *, original, source, directory, submitted, start_cancelled=False, seal_settlements=False, formal=False):
    requester = original['receiverUserId']; headquarters = original['administratorUserId']
    with Session(owner) as db:
        org = db.get(Organization, UUID(source['owner_org_id']))
        roles = {role.code: role for role in db.scalars(select(Role))}
        reviewer, _ = make_user(db, org, name='Owned independent settlement reviewer')
        assign(db, reviewer, roles['provincial_manager'], scope_type='organization', scope_id=str(org.id))
        regional = reviewer.id; grants = {}
        for action in sorted(set(authority.ACTIONS.values())):
            role = roles['admin' if action in ('review_return_condition_headquarters', 'cancel_return_condition_approval') else 'provincial_manager']
            permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
                Permission.action == action, Permission.field_code == ''))
            if formal:
                assert permission is not None, 'formal action permission missing'
            if permission is None:
                permission = Permission(resource='stock_operation', action=action, field_code='', description='Owned settlement gate only')
                db.add(permission); db.flush()
            link = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id))
            if formal:
                assert link is not None, 'formal action role grant missing'
            if link is None:
                link = RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'); db.add(link); db.flush()
            assert link.effect == 'allow'; grants[action] = link.id
        db.commit()
    assert len({requester, regional, headquarters}) == 3
    storage = FakeStorage()
    closed_settlements = []
    closure_proofs = []

    def upload(user_id):
        with Session(api) as db:
            actor = load_formal_principal(db, user_id)
            intent = formal_files.create_file_upload_intent(db, actor=actor,
                command=FileUploadIntentInput(purpose=evidence.PURPOSE, original_filename='原生结算核验.jpg',
                    size_bytes=128, mime_type='image/jpeg', sha256='a' * 64),
                idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET, trace_request_id=uuid4().hex,
                storage=storage, upload_ttl_seconds=600)
            row = db.get(FileObject, intent.file_id); storage.materialize(row)
            formal_files.complete_file_upload(db, actor=actor, file_id=row.id, trace_request_id=uuid4().hex, storage=storage)
            identifier = row.id; db.commit(); return identifier

    def decide(previous, kind, user):
        files = (upload(user),) if kind == 'verify_region' else ()
        command = ConditionDecision(action=kind, case_id=UUID(previous['case_id']),
            expected_event_id=UUID(previous['event_id']), expected_event_hash=previous['request_hash'],
            reason='独立审核原冻结份额', evidence_file_ids=files, request_id=uuid4().hex, idempotency_key=uuid4().hex)
        with Session(api) as db:
            result = decisions.decide(db, actor=load_formal_principal(db, user), request=command); db.commit()
        return result

    def scans_for_case(db, case):
        material = db.get(FormalMaterial, UUID(case['source_jsonb']['material_id']))
        table = graph.tables()['stock_condition_serials']
        ids = db.scalars(select(table.c.serial_id).where(table.c.case_id == case['id']).order_by(table.c.serial_id)).all()
        return [dict(serial_id=identifier, sku_code=material.sku_code,
            serial_no=db.get(InventorySerial, identifier).serial_no, qr_code=db.get(InventorySerial, identifier).qr_code) for identifier in ids]

    def settle(previous, action):
        with Session(api) as db:
            table = graph.tables()['stock_condition_cases']
            case = dict(db.execute(select(table).where(table.c.id == UUID(previous['case_id']))).mappings().one())
            command = ConditionSettlement(action=action, case_id=case['id'], expected_event_id=UUID(previous['event_id']),
                expected_event_hash=previous['request_hash'], reason='原生原冻结份额准确结算',
                serial_verifications=scans_for_case(db, case), request_id=uuid4().hex, idempotency_key=uuid4().hex)
        if seal_settlements:
            from pg16_return_condition_decision_seal_gate import run as check_seal
            proof = check_seal(owner, api, directory=directory, actor_id=requester,
                command=command, permission_link_id=grants[authority.ACTIONS[action]])
            closed = proof.pop('closedRequest'); sealed = proof.pop('closedResult')
            closed_settlements.append((closed, sealed))
            closure_proofs.append(dict(action=action, **proof))
            stable = snapshot(owner)
            # A late command with the real preflight still cannot reuse any
            # coordinate of the permanently closed request.
            late = command.model_copy(update={'request_id':closed.request_id,
                'idempotency_key':closed.idempotency_key})
            with Session(api) as db:
                try:
                    settlement.settle(db, actor=load_formal_principal(db, requester), request=late)
                except ERRORS as error:
                    assert error.code == 'return_condition_request_outcome_unknown'; db.rollback()
                else:
                    raise AssertionError('closed settlement coordinates admitted a stock write')
            assert snapshot(owner) == stable
        before = snapshot(owner)
        record = business.record
        def broken(db, **kwargs):
            record(db, **kwargs)
            raise RuntimeError('owned settlement effect failure')
        with Session(api) as db, patch.object(business, 'record', broken):
            try: settlement.settle(db, actor=load_formal_principal(db, requester), request=command)
            except RuntimeError as error:
                assert str(error) == 'owned settlement effect failure'; db.rollback()
            else: raise AssertionError('settlement fault injection not reached')
        assert snapshot(owner) == before
        with owned_role_connection(owner, directory) as connection:
            connection.execute(text('SET ROLE star_oam_api')); connection.commit()
            with Session(bind=connection) as db:
                settlement.settle(db, actor=load_formal_principal(db, requester), request=command)
                db.execute(text('SET LOCAL ROLE star_oam_migrator'))
                db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"), dict(id=grants[authority.ACTIONS[action]]))
                db.execute(text('SET LOCAL ROLE star_oam_api'))
                try: db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514' and 'condition' in str(error.orig); db.rollback()
                else: raise AssertionError('late revoked settlement committed')
        assert snapshot(owner) == before
        with Session(api) as db:
            assert db.scalar(text('SELECT session_user::text')) == 'star_oam_api'
            actor = load_formal_principal(db, requester)
            missing = recovery.lookup(db, actor=actor, request=command)
            assert missing['request_state'] == 'unknown' and not missing['retry_allowed']
            total = db.scalar(select(func.sum(StockBalance.quantity)))
            count = db.scalar(select(func.count()).select_from(InventoryTransaction))
            result = settlement.settle(db, actor=actor, request=command); db.commit()
            assert db.scalar(select(func.sum(StockBalance.quantity))) == total
            assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == count + 1
            events = graph.tables()['stock_condition_events']
            event = db.execute(select(events).where(events.c.id == UUID(result['event_id']))).mappings().one()
            target = db.get(StockAccount, event['to_account_id'])
            assert db.get(StockBalance, event['from_account_id']).quantity == 0
            assert target.condition_code == ('damaged' if action == 'execute' else case['recorded_condition'])
            assert target.availability_bucket == 'available'
            for scan in command.serial_verifications:
                assert db.get(SerialCurrentPosition, scan.serial_id).stock_account_id == target.id
            note = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id == result['event_id']))
            assert note.status == 'pending'
        stable = snapshot(owner)
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            found = recovery.lookup(db, actor=load_formal_principal(db, requester), request=command)
            assert found['request_state'] == 'found' and found['result'] == result
            assert not found['current_stock_verified'] and not found['retry_allowed']; db.rollback()
        with Session(api) as db:
            try: recovery.lookup(db, actor=load_formal_principal(db, requester), request=command.model_copy(update={'reason': '不同原请求'}))
            except ERRORS: db.rollback()
            else: raise AssertionError('changed original settlement recovered')
        with Session(api) as db:
            try: settlement.settle(db, actor=load_formal_principal(db, requester), request=command)
            except ERRORS: db.rollback()
            else: raise AssertionError('settlement replay accepted')
        assert snapshot(owner) == stable
        print('condition settlement actual API COMMIT ' + action + ' PASS', flush=True)
        return result, command

    if start_cancelled:
        assert submitted['status'] == 'cancelled_pending_release'
        assert submitted['action'] == 'cancel_approved'
        result = submitted
    else:
        result = decide(submitted, 'verify_region', regional)
        result = decide(result, 'approve_hq', headquarters)
        result = decide(result, 'cancel_approved', headquarters)
    released, release_command = settle(result, 'release')
    assert released['status'] == 'released_cancelled'
    # A new explicitly generated test request after verified release; never
    # reuse the prior request or infer permission from an unknown outcome.
    file_id = upload(requester)
    with Session(api) as db:
        actor = load_formal_principal(db, requester)
        proof = preparation.inspect_submission_source(db, actor=actor, inbound_line_id=UUID(released['inbound_line_id']))
        document = proof.document; material = db.get(FormalMaterial, UUID(document['material_id']))
        scans = [dict(serial_id=s['serial_id'], sku_code=material.sku_code, serial_no=s['serial_no'], qr_code=s['qr_code']) for s in document['serials']]
        fresh = ConditionSubmit(action='submit_return_condition', inbound_line_id=UUID(released['inbound_line_id']),
            expected_source_hash=proof.evidence_hash, quantity=released['quantity'], serial_verifications=scans,
            evidence_file_ids=(file_id,), reason='前次释放后重新核实的独立申请', request_id=uuid4().hex, idempotency_key=uuid4().hex)
        result = initial.submit(db, actor=actor, request=fresh); db.commit()
    result = decide(result, 'verify_region', regional)
    result = decide(result, 'approve_hq', headquarters)
    executed, _ = settle(result, 'execute')
    assert executed['status'] == 'executed'
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert recovery.lookup(db, actor=load_formal_principal(db, requester), request=release_command)['result'] == released
    final_snapshot = snapshot(owner)
    for closed, sealed in closed_settlements:
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            historical = recovery.lookup(db, actor=load_formal_principal(db, requester), request=closed)
            assert historical['request_state'] == 'sealed'
            assert historical['seal'] == sealed['seal'] and historical['original_input_hash'] == sealed['original_input_hash']
            assert not historical['retry_allowed'] and not historical['current_stock_verified']
    assert snapshot(owner) == final_snapshot
    return dict(passed=True, actualApiCommitActions=['release','execute'], originalReleaseAfterNewCase=True,
        settlementClosureProofs=closure_proofs, oldSettlementSealsAfterNewCase=bool(closed_settlements),
        exactOriginalSqlReadOnlyLookup=True, stockAndSerialDirectionsVerified=True, assetQuantityConserved=True,
        effectFailureWholeTransactionRollback=True, lateRevocationActualCommitRejected=True,
        duplicateWriteRejected=True, changedOriginalInputRejected=True, fakeObjectStorage=True,
        syntheticCandidateGrants=not formal, nativeTargetAccountAdmission=True, formalMigration=formal,formalDefaultPermissions=formal, productionAcceptance=False)
