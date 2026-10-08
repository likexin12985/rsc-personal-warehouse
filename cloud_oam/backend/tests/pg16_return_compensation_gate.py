"""Native formal compensation on real formal opening, shipment and split return.

Synthetic users/files, no production acceptance. Formal 0176 is installed through Alembic; public return HTTP/UI activation
and production acceptance remain separate requirements.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal
from queue import Queue
from pathlib import Path
from uuid import UUID, uuid4
from unittest.mock import patch
import json
import runpy
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import MaterialRequest
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.material_request_rejection_inbound_schema import inbounds
from app.material_request_return_compensation_schema import compensations
from app.material_request_return_compensation_schemas import ReturnCompensationIn, ReturnCompensationOut
from app.formal_services import material_request_return_compensation as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.formal_services.material_request_lifecycle import MaterialRequestLifecycleError
from pg16_material_request_remaining_cancel_gate import _wait_on_blocker
from pg16_stock_scrap_structure_gate import original_columns, facts
from test_material_request_draft_service import SECRET


def run(engines, *, inbound_id, directory, admin_id, mixed=False, unfulfilled=False):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    root = Path(__file__).resolve().parents[1] / 'alembic'
    frozen = runpy.run_path(str(root / 'return_compensation_0176/transition.py'))
    snapshot = frozen['probe'].snapshot
    ddl = frozen['DATA']['statements']
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261227_0178'
        frozen['verify'](db, 'after')
        after_catalog = snapshot(db)
        for signature, change in frozen['DATA']['functions'].items():
            if change['before'] is None:
                assert not db.scalar(text("SELECT has_function_privilege('star_oam_api',:f,'EXECUTE')"), {'f': 'public.' + signature})
        for right in ('UPDATE', 'DELETE', 'TRUNCATE'):
            assert not db.scalar(text("SELECT has_table_privilege('star_oam_api','public.material_request_return_compensations',:r)"), {'r': right})
        columns = original_columns(db)
        stable_columns = {k: v for k, v in columns.items() if k not in (
            'material_request_return_compensations', 'material_request_closures', 'audit_events', 'audit_chain_heads',
            'material_request_remaining_cancellations', 'material_request_remaining_cancellation_lines')}
        unchanged = facts(db, stable_columns)
        before = facts(db, columns)
    (directory / 'return-compensation-catalog-formal.json').write_text(json.dumps(
        dict(revision='20261225_0176', catalog=frozen['DATA'], observed=after_catalog), indent=2) + '\n')
    with Session(api) as db:
        row = db.execute(select(inbounds).where(inbounds.c.id == UUID(inbound_id))).mappings().one()
        request = db.get(MaterialRequest, row['request_id'])
        request_id, actor_id = request.id, request.requester_user_id
        sources = service.verified_posted_returns(db, request=request)
        assert len(sources) == (1 if mixed else 2)
        values = [ReturnCompensationIn(expected_request_version=request.version, inbound_id=s.row['id'],
            inbound_request_hash=s.row['request_hash'], inbound_plan_hash=s.row['plan_hash'],
            cancelled_qty=s.origin['accepted_qty'], reason='已实际退回入账，原申请人独立取消需求') for s in sources.values()]
    token = 'native-compensation-' + uuid4().hex
    def cancel(db, value=values[0], key=token):
        return service.cancel_returned_demand(db, actor=load_formal_principal(db, actor_id), request_id=request_id,
            payload=value, idempotency_key=key, secret=SECRET, trace_request_id=key)
    from app.formal_services.material_request_completion import completion_quantities
    from app.formal_services.material_request_return_quantities import remaining_with_returns
    from app.formal_services import material_request_closure as closure
    def close(db, key=token+'-close'):
        return closure.close_material_request(db, actor=load_formal_principal(db, admin_id), request_id=request_id,
            payload=dict(expected_request_version=db.get(MaterialRequest, request_id).version, reason='正常入账与逐笔退回补偿均已核验'),
            idempotency_key=key, secret=SECRET, trace_request_id=key, _include_returns=True)
    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        partition = remaining_with_returns(db, actor=actor, request_id=request_id)
        assert partition.schema_version == '2.0' and len(partition.lines) == 1
        assert partition.lines[0].posted_qty == ('1.000' if mixed else '0.000')
        assert partition.lines[0].returned_pending_compensation_qty == ('1.000' if mixed else '3.000')
        try: close(db, token+'-premature')
        except MaterialRequestReadError as exc: assert exc.code.endswith('quantity_incomplete')
        else: raise AssertionError('warehouse return alone allowed demand closure')
    denied = []
    for kind in ('missing_audit', 'quantity', 'version', 'actor', 'evidence'):
        with Session(api) as db, patch.object(service, 'result', return_value=None):
            execute = db.execute
            def altered(statement, *args, **kwargs):
                if getattr(statement, 'is_insert', False) and getattr(statement, 'table', None) is compensations:
                    changes = dict(quantity={'cancelled_qty': '999.000'}, version={'request_version': 1},
                        actor={'actor_person_id': uuid4()}, evidence={'evidence_sha256': '0' * 64})
                    if kind in changes:
                        statement = statement.values(**changes[kind])
                return execute(statement, *args, **kwargs)
            with patch.object(db, 'execute', side_effect=altered), (patch.object(service, 'append_audit_event',
                    return_value=None) if kind == 'missing_audit' else nullcontext()):
                try:
                    cancel(db, key=token + '-' + kind)
                    execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
                except DBAPIError as exc:
                    assert exc.orig.sqlstate in ('23514', '42501') and ('0176' in str(exc) or '0171' in str(exc)), (kind, str(exc))
                    denied.append(dict(case=kind, sqlstate=exc.orig.sqlstate)); db.rollback()
                else:
                    raise AssertionError('invalid compensation committed: ' + kind)
        with owner.connect() as db:
            assert facts(db, columns) == before, kind + ' changed business facts'
    waiting = Queue()
    def competitor():
        with Session(api) as db:
            waiting.put(db.scalar(text('SELECT pg_backend_pid()')))
            try:
                cancel(db, key=token + '-competitor')
            except MaterialRequestReadError as exc:
                db.rollback(); return exc.code
            raise AssertionError('same inbound compensated twice')
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as pool:
        competing = []
        def observe_commit(session):
            session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            blocker = session.scalar(text('SELECT pg_backend_pid()'))
            future = pool.submit(competitor); competing.append(future)
            pid = waiting.get(timeout=10)
            _wait_on_blocker(owner, pid, blocker, future)
        event.listen(db, 'before_commit', observe_commit, once=True)
        first = post_compensation_http(db, actor_id=actor_id, request_id=request_id, value=values[0], key=token)
        assert not first.replayed
        assert competing[0].result(timeout=15).endswith('already_compensated')
    if len(values) > 1:
        with Session(api) as db:
            try: close(db, token+'-partially-compensated')
            except MaterialRequestReadError as exc: assert exc.code.endswith('quantity_incomplete')
            else: raise AssertionError('partial return compensation allowed closure')
        with Session(api) as db:
            second = post_compensation_http(db, actor_id=actor_id, request_id=request_id, value=values[1], key=token + '-second')
            assert second.compensation_id != first.compensation_id
    with Session(owner) as db:
        permission = db.scalar(select(Permission.id).where(Permission.resource == 'material_request', Permission.action == 'cancel', Permission.field_code == ''))
        links = tuple(db.scalars(select(RolePermission).where(RolePermission.permission_id == permission)))
        previous = {r.role_id: r.effect for r in links}
        for link in links: link.effect = 'deny'
        db.commit()
    with Session(api) as db:
        try:
            cancel(db, key=token + '-revoked')
        except (MaterialRequestReadError, MaterialRequestLifecycleError) as exc:
            assert exc.category == 'forbidden'
        else: raise AssertionError('revoked requester submitted new compensation')
        db.rollback(); db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        a = service.command_status(db, actor=actor, request_id=request_id, idempotency_key=token, secret=SECRET)
        b = service.command_status(db, actor=actor, request_id=request_id, trace_request_id=token)
        assert a == b and a.compensation_id == first.compensation_id and a.replayed
        totals = service.verified_return_compensated_quantities(db, request=db.get(MaterialRequest, request_id))
        assert sum(totals.values()) == (1 if mixed else 3)
    recovery_http = verify_compensation_recovery_http(api, actor_id=actor_id, request_id=request_id, value=values[0], key=token, expected=first)
    with Session(owner) as db:
        for link in db.scalars(select(RolePermission).where(RolePermission.permission_id == permission)):
            link.effect = previous[link.role_id]
        db.commit()
    for engine in (api, owner):
        for sql in ('UPDATE public.material_request_return_compensations SET reason=reason',
                    'DELETE FROM public.material_request_return_compensations',
                    'TRUNCATE public.material_request_return_compensations'):
            with engine.connect() as db:
                try: db.execute(text(sql)); db.commit()
                except DBAPIError as exc:
                    assert exc.orig.sqlstate == ('42501' if engine is api else '55000')
                    db.rollback()
                else: raise AssertionError('compensation history mutable')
    with owner.connect() as db:
        assert facts(db, stable_columns) == unchanged
        assert snapshot(db) == after_catalog
    before_remaining_http = verify_quantity_http(engines, request_id=request_id, actor_id=actor_id,
        expected_cancelled='1.000' if mixed else '3.000', expected_unfulfilled='0.000')
    remaining_result = verify_remaining(engines, request_id=request_id, actor_id=actor_id, token=token) if unfulfilled else None
    closure_result = verify_closure(engines, request_id=request_id, actor_id=admin_id, token=token, close=close)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, actor_id)
        coverage = completion_quantities(db, actor=actor, request_id=request_id, _include_returns=True)
        assert coverage.quantity_coverage_complete and coverage.pending_inbound_orders == 0
        assert len(coverage.lines) == 1 and coverage.lines[0].posted_qty == ('1.000' if mixed else '0.000')
        assert coverage.lines[0].cancelled_qty == ('2.000' if unfulfilled else '1.000' if mixed else '3.000')
        partition = remaining_with_returns(db, actor=actor, request_id=request_id)
        assert partition.lines[0].returned_pending_compensation_qty == '0.000'
        assert partition.lines[0].rejected_unsettled_qty == '0.000'
        assert partition.lines[0].unfulfilled_cancelled_qty == ('1.000' if unfulfilled else '0.000')
    with owner.connect() as db:
        assert facts(db, stable_columns) == unchanged and snapshot(db) == after_catalog
    final_http = verify_quantity_http(engines, request_id=request_id, actor_id=actor_id,
        expected_cancelled=coverage.lines[0].cancelled_qty,
        expected_unfulfilled='1.000' if unfulfilled else '0.000')
    output = dict(scope='formal 0176 posted-return compensation, versioned remaining cancellation and closure', postedReturns=len(sources),
        compensatedQty='1.000' if mixed else '3.000', originalStockAndDemandUnchanged=True, exactBlockingBackendObserved=True,
        readOnlyRecoveryAfterRevocation=True, rejectedDirectWrites=denied, formalCatalogVerified=True,
        ddlStatementCount=len(ddl), formalMigrationActivated=True, closureActivated=True, publicHttpActivated=True,
        productionAcceptance=False)
    output.update(compensationHttp=recovery_http, quantityHttp=dict(beforeRemaining=before_remaining_http, final=final_http), independentClosure=closure_result, sameApprovedLineMixedInbound=mixed,
        remainingCancellation=remaining_result,
        finalCoverage=coverage.model_dump(mode='json'), finalPartition=partition.model_dump(mode='json'))
    (directory / 'return-compensation.json').write_text(json.dumps(output, indent=2) + '\n')
    return output


def verify_remaining(engines, *, request_id, actor_id, token):
    from app.formal_services import material_request_remaining_cancel as remaining
    from app.formal_services.material_request_return_quantities import remaining_with_returns
    from app.material_request_remaining_cancel_schema import cancellations
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with Session(api) as db:
        view=remaining_with_returns(db,actor=load_formal_principal(db,actor_id),request_id=request_id)
        line=view.lines[0]
        assert (line.approved_qty,line.posted_qty,line.return_compensated_qty,line.unreserved_qty)==('3.000','1.000','1.000','1.000')
        payload=dict(expected_request_version=view.request_version,reason='已释放的剩余未履约数量不再需要',
            lines=[dict(request_line_id=str(line.request_line_id),cancelled_qty='1.000')])
    def cancel(db, key=token+'-remaining', value=payload):
        return remaining.cancel_remaining_demand(db,actor=load_formal_principal(db,actor_id),request_id=request_id,
            payload=value,idempotency_key=key,secret=SECRET,trace_request_id=key,_include_returns=True)
    with owner.connect() as db:
        columns=original_columns(db); before=facts(db,columns)
    failures=[]
    for kind in ('missing_audit','remaining_quantity','compensation_source'):
        with Session(api) as db, patch.object(remaining,'_result',return_value=None):
            execute=db.execute
            def altered(statement,*args,**kwargs):
                if getattr(statement,'is_insert',False) and getattr(statement,'table',None) is cancellations and kind!='missing_audit':
                    data={getattr(k,'key',k):v.value for k,v in statement._values.items()}
                    evidence=json.loads(json.dumps(data['evidence_jsonb']))
                    if kind=='remaining_quantity': evidence['before']['lines'][0]['unreserved_qty']='2.000'
                    else: evidence['settled_fulfillment']['return_compensations'][0]['original_receipt_line_id']=str(uuid4())
                    statement=statement.values(evidence_jsonb=evidence,evidence_sha256=remaining.lifecycle._canonical_hash(evidence))
                return execute(statement,*args,**kwargs)
            with patch.object(db,'execute',side_effect=altered), (patch.object(remaining,'append_audit_event',return_value=None)
                    if kind=='missing_audit' else nullcontext()):
                try:
                    cancel(db,token+'-invalid-remaining-'+kind)
                    execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.commit()
                except DBAPIError as exc:
                    assert exc.orig.sqlstate=='23514' and '0171' in str(exc),(kind,str(exc))
                    failures.append(kind);db.rollback()
                else: raise AssertionError('invalid remaining cancellation committed: '+kind)
        with owner.connect() as db: assert facts(db,columns)==before
    with Session(api) as db:
        original=cancel(db);db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.commit()
        row=db.execute(select(cancellations).where(cancellations.c.id==original.cancellation_id)).mappings().one()
        assert row['evidence_jsonb']['schema']==remaining.RETURN_SCHEMA
        assert original.lines[0].cancelled_qty=='1.000'
        assert cancel(db).cancellation_id==original.cancellation_id
    with Session(owner) as db:
        perm=db.scalar(select(Permission.id).where(Permission.resource=='material_request',Permission.action=='cancel',Permission.field_code==''))
        links=tuple(db.scalars(select(RolePermission).where(RolePermission.permission_id==perm)))
        previous={r.role_id:r.effect for r in links}
        for link in links: link.effect='deny'
        db.commit()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        observed=remaining.remaining_cancellation_command_status(db,actor=load_formal_principal(db,actor_id),request_id=request_id,
            idempotency_key=token+'-remaining',secret=SECRET)
        assert observed.cancellation_id==original.cancellation_id and observed.replayed
        view=remaining_with_returns(db,actor=load_formal_principal(db,actor_id),request_id=request_id)
        assert view.lines[0].unfulfilled_cancelled_qty=='1.000' and view.lines[0].return_compensated_qty=='1.000'
        assert view.lines[0].unreserved_qty=='0.000'
    with Session(owner) as db:
        for link in db.scalars(select(RolePermission).where(RolePermission.permission_id==perm)):
            link.effect=previous[link.role_id]
        db.commit()
    return dict(cancellationId=str(original.cancellation_id),cancelledQty='1.000',evidenceSchema='v2',
        rejectedDirectSql=failures,readOnlyRecoveryAfterRevocation=True)


def verify_closure(engines, *, request_id, actor_id, token, close):
    from app.formal_services import material_request_closure as closure
    from app.material_request_closure_schema import closures
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.connect() as db:
        columns = original_columns(db); before = facts(db, columns)
    rejected = []
    for kind in ('missing_audit', 'omit_source', 'wrong_receipt', 'quantity'):
        with Session(api) as db, patch.object(closure, '_result', return_value=None):
            execute = db.execute
            def altered(statement, *args, **kwargs):
                if getattr(statement, 'is_insert', False) and getattr(statement, 'table', None) is closures and kind != 'missing_audit':
                    data = {key: bind.value for key, bind in statement._values.items()}
                    # Change evidence and rehash it: only the independent SQL
                    # proof can reject this otherwise self-consistent document.
                    data = {getattr(key, 'key', key): value for key, value in data.items()}
                    evidence = json.loads(json.dumps(data['evidence_jsonb']))
                    records = evidence['settled_fulfillment']['return_compensations']
                    if kind == 'omit_source': records.clear()
                    elif kind == 'wrong_receipt': records[0]['original_receipt_line_id'] = str(uuid4())
                    else: evidence['coverage']['lines'][0]['cancelled_qty'] = '999.000'
                    statement = statement.values(evidence_jsonb=evidence,
                        evidence_sha256=closure.lifecycle._canonical_hash(evidence))
                return execute(statement, *args, **kwargs)
            with patch.object(db, 'execute', side_effect=altered), (patch.object(closure, 'append_audit_event',
                    return_value=None) if kind == 'missing_audit' else nullcontext()):
                try:
                    close(db, token+'-close-invalid-'+kind)
                    execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
                except DBAPIError as exc:
                    assert exc.orig.sqlstate == '23514' and '0169' in str(exc), (kind, str(exc))
                    rejected.append(kind); db.rollback()
                else: raise AssertionError('invalid return closure committed: '+kind)
        with owner.connect() as db: assert facts(db, columns) == before
    with Session(api) as db:
        first = close(db); db.execute(text('SET CONSTRAINTS ALL IMMEDIATE')); db.commit()
        row = db.execute(select(closures).where(closures.c.id == first.closure_id)).mappings().one()
        assert row['evidence_jsonb']['schema'] == 'rsc.material_request_closure_evidence.v2'
    with Session(owner) as db:
        permission = db.scalar(select(Permission.id).where(Permission.resource == 'material_request', Permission.action == 'close', Permission.field_code == ''))
        links = tuple(db.scalars(select(RolePermission).where(RolePermission.permission_id == permission)))
        previous = {r.role_id:r.effect for r in links}
        for link in links: link.effect='deny'
        db.commit()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        original = closure.closure_command_status(db, actor=load_formal_principal(db, actor_id), request_id=request_id,
            idempotency_key=token+'-close', secret=SECRET)
        assert original.closure_id == first.closure_id and original.replayed
        assert closure.read_closure(db, actor=load_formal_principal(db, actor_id), request_id=request_id).business_status == 'closed'
    with Session(owner) as db:
        for link in db.scalars(select(RolePermission).where(RolePermission.permission_id == permission)):
            link.effect=previous[link.role_id]
        db.commit()
    return dict(closureId=str(first.closure_id), schema='v2', rejectedDirectSql=rejected,
        readOnlyRecoveryAfterRevocation=True, originalApprovalAndStockUnchanged=True)


def verify_quantity_http(engines, *, request_id, actor_id, expected_cancelled, expected_unfulfilled):
    """Formal routes, actual runtime role and immutable facts, no service mocks."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    api, owner = engines['star_oam_api'], engines['star_oam_migrator']
    with owner.connect() as db:
        columns = original_columns(db); before = facts(db, columns)
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db:
            return load_formal_principal(db, actor_id)
    settings = get_settings().model_copy(update={'material_request_writes_enabled': False})
    base = '/api/v1/material-requests'
    paths = [base + '/' + str(request_id) + suffix for suffix in ('', '/completion-quantities', '/remaining-fulfillment')]
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: principal, get_settings: lambda: settings}), TestClient(app) as client:
        responses = [client.get(path) for path in paths]
        for response in responses:
            assert response.status_code == 200, response.text
            assert 'no-store' in response.headers['cache-control']
        detail, completion, remainder = [r.json() for r in responses]
        assert all(v['lines'][0]['cancelled_qty'] == expected_cancelled for v in (detail, completion, remainder))
        assert remainder['schema_version'] == '2.0'
        assert remainder['lines'][0]['unfulfilled_cancelled_qty'] == expected_unfulfilled
        listing = client.get(base + '?limit=100')
        assert listing.status_code == 200, listing.text
        assert next(r for r in listing.json()['items'] if r['request_id'] == str(request_id))['allowed_actions'] == detail['allowed_actions']
        assert 'cancel' not in detail['allowed_actions']
        assert [client.get(path).json() for path in paths] == [detail, completion, remainder]
    with owner.connect() as db:
        assert facts(db, columns) == before
    return dict(httpStatus=200, readOnly=True, writesDisabled=True, refreshIdentical=True,
        unchangedFacts=True, cancelledQty=expected_cancelled, unfulfilledCancelledQty=expected_unfulfilled)


def post_compensation_http(db, *, actor_id, request_id, value, key):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    actor = load_formal_principal(db, actor_id)
    def sessions():
        yield db
    settings = get_settings().model_copy(update={'material_request_writes_enabled': True,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: lambda: actor, get_settings: lambda: settings}), TestClient(app) as client:
        response = client.post(f'/api/v1/material-requests/{request_id}/return-compensations',
            json=value.model_dump(mode='json'), headers={'Idempotency-Key': key, 'X-Request-ID': key})
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    return ReturnCompensationOut.model_validate(response.json())


def verify_compensation_recovery_http(api, *, actor_id, request_id, value, key, expected):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db:
            return load_formal_principal(db, actor_id)
    settings = get_settings().model_copy(update={'material_request_writes_enabled': False,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    base = f'/api/v1/material-requests/{request_id}/return-compensations'
    fingerprint = service.lifecycle._canonical_hash(value.model_dump(mode='json'))
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: principal, get_settings: lambda: settings}), TestClient(app) as client:
        for header in ('Idempotency-Key', 'X-Original-Request-ID'):
            response = client.get(base + '/command-status', headers={header: key, 'X-Request-Fingerprint': fingerprint})
            assert response.status_code == 200, response.text
            assert 'no-store' in response.headers['cache-control']
            assert response.json()['lookup_status'] == 'confirmed'
            assert response.json()['command']['compensation_id'] == str(expected.compensation_id)
            assert response.json()['command']['replayed']
        mismatch = client.get(base + '/command-status', headers={'Idempotency-Key': key, 'X-Request-Fingerprint': '0' * 64})
        assert mismatch.status_code == 409
        response = client.get(base + '/candidates')
        assert response.status_code == 200, response.text
        row = next(r for r in response.json()['items'] if r['inbound_id'] == str(value.inbound_id))
        assert row['compensation']['compensation_id'] == str(expected.compensation_id) and not row['compensate_permitted']
        assert client.post(base, json=value.model_dump(mode='json'),
            headers={'Idempotency-Key': key, 'X-Request-ID': key}).status_code == 503
    return dict(firstPostStatus=201, keyAndTraceRecovery=True, fingerprintMismatchStatus=409,
        recoveryAfterRevocation=True, readOnly=True, writeSwitchClosed=True, candidatesVerified=True)
