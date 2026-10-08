"""New progress facts after a real rejected receipt in a caller-owned PG16."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from queue import Queue
from uuid import UUID, uuid4
import json
import runpy

from sqlalchemy import create_engine, select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import MaterialRequest
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_progress_schemas import RejectionProgressIn
from app.formal_services import material_request_rejection_progress as service
from app.formal_services.material_request_query import MaterialRequestReadError
from pg16_stock_scrap_structure_gate import original_columns, facts
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker
from test_material_request_draft_service import SECRET


def run(engines, *, request_id, directory):
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    request_id = UUID(request_id)
    from app import material_request_rejection_progress_security as catalog
    with owner.connect() as db: catalog.verify(db)
    with owner.connect() as db:
        columns = {k: v for k, v in original_columns(db).items()
            if not k.startswith('audit_') and k != progress.name
            and not k.startswith('material_request_rejection_return')}
        original_facts = facts(db, columns)
        retained_registration = facts(db, {k:v for k,v in original_columns(db).items() if k.startswith('material_request_rejection_return')})
    with Session(api) as db:
        request = db.get(MaterialRequest, request_id)
        actor_id, version = request.requester_user_id, request.version
        parent = db.execute(select(returns).where(returns.c.request_id == request_id)).mappings().one()
        return_id = parent['id']
        when = service._time(parent['occurred_at'])
    token = 'native-progress-' + uuid4().hex
    def payload(action, previous=None):
        return RejectionProgressIn(expected_request_version=version, action=action,
            registration_request_hash=parent['request_hash'], reason='原拒收物资退运独立事实',
            previous_event_id=previous.event_id if previous else None,
            previous_request_hash=previous.request_hash if previous else None,
            physical_at=None if action == 'cancel_registration' else when,
            carrier='合成承运商' if action == 'handover' else None,
            tracking_no='RETURN-' + token if action == 'handover' else None)
    def record(db, command, key):
        return service.record_rejection_progress(db, actor=load_formal_principal(db, actor_id),
            request_id=request_id, return_id=return_id, payload=command, idempotency_key=key,
            secret=SECRET, trace_request_id=key)
    def state(db):
        return service.rejection_progress_state(db, actor=load_formal_principal(db, actor_id),
            request_id=request_id, return_id=return_id)
    registration_payload = service.registration.RejectionReturnIn.model_validate(parent['evidence_jsonb']['input'])
    def register_again(db, key):
        return service.registration.register_rejection_return(db, actor=load_formal_principal(db, actor_id),
            request_id=request_id, payload=registration_payload, idempotency_key=key, secret=SECRET, trace_request_id=key)
    waiting_replacement = Queue()
    def competing_replacement():
        with Session(api) as db:
            waiting_replacement.put(db.scalar(text('SELECT pg_backend_pid()')))
            replacement = register_again(db, token + '-replacement')
            db.commit()
            return replacement
    from pg16_rejection_http import post as http_post, verify_recovery as http_recovery
    original_return_id = return_id
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing_requests = []
        def observe_cancellation(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            assert state(session).status == 'cancelled'
            try: record(session, payload('depart'), token + '-after-cancel')
            except MaterialRequestReadError as exc: assert exc.code.endswith('already_progressed')
            else: raise AssertionError('cancelled return departed')
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(competing_replacement); competing_requests.append(future)
            pid = waiting_replacement.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_cancellation, once=True)
        cancelled = http_post(db, actor_id=actor_id, request_id=request_id, return_id=return_id,
            value=payload('cancel_registration'), key=token + '-cancel')
        replacement = competing_requests[0].result(timeout=30)
    with Session(api) as db:
        assert register_again(db, token + '-replacement').return_id == replacement.return_id
        try: register_again(db, token + '-extra')
        except MaterialRequestReadError as exc: assert exc.code == 'rejection_return_quantity_exceeded'
        else: raise AssertionError('active registration quota exceeded')
        db.rollback()
        original = service.registration.rejection_return_command_status(db, actor=load_formal_principal(db, actor_id),
            request_id=request_id, trace_request_id=parent['trace_request_id'])
        assert original.return_id == original_return_id and original.status == 'registered'
        assert replacement.serial_ids == original.serial_ids and replacement.quantity == original.quantity
        parent = db.execute(select(returns).where(returns.c.id == replacement.return_id)).mappings().one()
        return_id = replacement.return_id; when = service._time(parent['occurred_at'])
    with Session(api) as db:
        planned = record(db, payload('depart'), token)
        prepared = dict(db.execute(select(progress).where(progress.c.id == planned.event_id)).mappings().one())
        db.rollback()
    denied = []
    for case in ('hash', 'origin', 'authority', 'reason', 'missing_audit', 'skip_departure'):
        with Session(api) as db:
            row = deepcopy(prepared)
            row.update(id=uuid4(), idempotency_key_hash=uuid4().hex * 2, trace_request_id=token + '-' + case,
                recorded_at=db.scalar(text('SELECT CURRENT_TIMESTAMP')))
            if case == 'hash': row['evidence_sha256'] = '0' * 64
            if case == 'origin': row['registration_request_hash'] = '0' * 64
            if case == 'authority': row['authorization_version'] += 1
            if case == 'reason': row['reason'] = '未绑定输入'
            if case == 'skip_departure':
                row.update(action='handover', previous_event_id=uuid4(), previous_request_hash='0'*64,
                    carrier='承运', tracking_no='invalid')
            try:
                db.execute(progress.insert().values(**row)); db.commit()
            except DBAPIError as exc:
                assert exc.orig.sqlstate in ('23514', '42501'), (case, str(exc))
                if case == 'missing_audit': assert 'exact progress audit required' in str(exc)
                if case == 'skip_departure': assert 'one departure required' in str(exc)
                denied.append(dict(case=case, sqlstate=exc.orig.sqlstate)); db.rollback()
            else: raise AssertionError('forged progress committed: ' + case)
    waiting = Queue()
    def cancel_competing():
        with Session(api) as db:
            waiting.put(db.scalar(text('SELECT pg_backend_pid()')))
            try: record(db, payload('cancel_registration'), token + '-competing'); db.commit()
            except MaterialRequestReadError as exc:
                db.rollback(); return exc.code
        raise AssertionError('cancel committed after departure')
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing_requests = []
        def observe_departure(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(cancel_competing); competing_requests.append(future)
            pid = waiting.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_departure, once=True)
        departure = http_post(db, actor_id=actor_id, request_id=request_id, return_id=return_id,
            value=payload('depart'), key=token)
        assert competing_requests[0].result(timeout=20).endswith('already_progressed')
    with Session(api) as db:
        handed = http_post(db, actor_id=actor_id, request_id=request_id, return_id=return_id,
            value=payload('handover', departure), key=token + '-handover')
        assert state(db).status == 'handed_over'
    with Session(owner) as db:
        role = db.scalar(select(Role.id).where(Role.code == 'technician'))
        perm = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'ship_return', Permission.field_code == ''))
        grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == perm))
        assert grant.effect == 'allow'
        grant.effect = 'deny'; db.commit()
    with Session(api) as db:
        try: record(db, payload('handover', departure), token + '-revoked')
        except MaterialRequestReadError as exc: assert exc.category == 'forbidden'
        else: raise AssertionError('revoked handover accepted')
        db.rollback()
    with owner.connect() as db:
        all_columns = original_columns(db); before_read = facts(db, all_columns)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        principal = load_formal_principal(db, actor_id)
        by_key = service.rejection_progress_command_status(db, actor=principal, request_id=request_id,
            return_id=return_id, idempotency_key=token + '-handover', secret=SECRET)
        by_trace = service.rejection_progress_command_status(db, actor=principal, request_id=request_id,
            return_id=return_id, trace_request_id=token + '-handover')
        assert by_key == by_trace and by_key.event_id == handed.event_id and by_key.replayed
        assert state(db).status == 'handed_over'
    http_evidence = http_recovery(api, actor_id=actor_id, request_id=request_id, return_id=return_id,
        value=payload('handover', departure), key=token + '-handover', expected=handed)
    with owner.connect() as db: assert facts(db, all_columns) == before_read
    with Session(owner) as db:
        grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == perm))
        grant.effect = 'allow'; db.commit()
    for engine in (api, owner):
        for operation, sql in (
            ('UPDATE', "UPDATE public.material_request_rejection_progress SET reason='tamper'"),
            ('DELETE', 'DELETE FROM public.material_request_rejection_progress'),
            ('TRUNCATE', 'TRUNCATE public.material_request_rejection_progress CASCADE')):
            with engine.connect() as db:
                try: db.execute(text(sql)); db.commit()
                except DBAPIError as exc:
                    expected = '42501' if engine is api else '55000'
                    assert exc.orig.sqlstate == expected, (operation, engine.url.username, str(exc))
                    if engine is owner:
                        assert 'append-only' in str(exc)
                    denied.append(dict(case=operation, role=engine.url.username, sqlstate=exc.orig.sqlstate)); db.rollback()
                else: raise AssertionError('immutable progress modified')
    with owner.connect() as db:
        assert facts(db, columns) == original_facts
        current_registrations = facts(db, {k:v for k,v in original_columns(db).items() if k.startswith('material_request_rejection_return')})
        assert all(set(rows).issubset(current_registrations[table]) for table,rows in retained_registration.items())
        for signature in catalog.DATA['functions']:
            assert not db.scalar(text("SELECT has_function_privilege('star_oam_api',:s,'EXECUTE')"), {'s': 'public.' + signature})
    # Current receiving scope is independent of the original sender's login.
    from app.models import User
    from app.inventory_models import StockAccount, StockLocation
    from app.formal_services.material_request_rejection_receiving import rejection_return_receiving_detail
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        source = db.get(StockAccount, parent['return_source_account_id'])
        location = db.get(StockLocation, source.location_id)
        receiver_id = db.scalar(select(User.id).where(User.person_id == location.custodian_person_id))
        assert receiver_id and receiver_id != actor_id
        def read_receiving(db):
            return rejection_return_receiving_detail(db, actor=load_formal_principal(db, receiver_id), return_id=return_id)
        receiving_view = read_receiving(db)
        assert read_receiving(db) == receiving_view
        assert receiving_view.handover_id == handed.event_id
        assert receiving_view.quantity == replacement.quantity
        assert tuple(sn.serial_id for sn in receiving_view.serials) == replacement.serial_ids
        db.rollback()
    # Use only this native cluster's bootstrap identity. Migrator is intentionally
    # not a member of API and cannot SET ROLE; do not widen its privileges.
    state_file = json.loads((directory / 'cluster-state.json').read_text())
    assert owner.url.host is None and owner.url.query['host'] == state_file['socketDirectory']
    bootstrap = create_engine(owner.url.set(username='postgres'))
    try:
        with bootstrap.connect() as connection:
            observed = connection.execute(text("SELECT current_database(), session_user, "
                "current_setting('data_directory'), current_setting('listen_addresses'), "
                "(SELECT system_identifier::text FROM pg_control_system())")).one()
            assert observed[0] == owner.url.database and observed[1] == 'postgres'
            assert Path(observed[2]).resolve() == (directory / 'data').resolve()
            assert observed[3] == '' and observed[4] == state_file['identity']['systemIdentifier']
            connection.rollback()
            transaction = connection.begin()
            try:
                connection.execute(text("UPDATE public.users SET account_status='disabled' WHERE id=:id"), {'id': actor_id})
                connection.execute(text('SET LOCAL ROLE star_oam_api'))
                with Session(bind=connection, join_transaction_mode='create_savepoint') as db:
                    assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
                    assert read_receiving(db) == receiving_view
            finally:
                transaction.rollback()
    finally:
        bootstrap.dispose()
    with owner.connect() as db:
        assert facts(db, columns) == original_facts
    result = dict(scope='formal 0176 public rejection progress and active claims', runtimeAdmission=True, publicHttp=http_evidence,
        originalRequestId=str(request_id), returnId=str(return_id), cancelledReturnId=str(original_return_id),
        cancellationId=str(cancelled.event_id), departureId=str(departure.event_id), handoverId=str(handed.event_id),
        physicalFactsCommitted=True, cancellationCommitted=True, cancelledQuantityOrSerialReregistration=True,
        originalRegistrationAndSerialRowsRetained=True, cancellationReregistrationExactBlockerObserved=True,
        inventoryAndOriginalBusinessUnchanged=True,
        exactBlockingBackendObserved=True, readOnlyRecoveryAfterRevocation=True, rejectedDirectWrites=denied,
        warehouseReadOnlyRecovery=True, disabledSenderWarehouseRead=True,
        warehouseAcceptanceCommitted=False, warehouseInventoryPosted=False,
        publicHttpActivated=False, productionAcceptance=False)
    (directory / 'rejection-progress.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
