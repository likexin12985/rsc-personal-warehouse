"""Real API-role HTTP writes, permanent seals and ambiguous COMMIT recovery.

Only login identity and one post-COMMIT connection error are synthetic.
Inventory services, SQL constraints, current grants and transactions are real.
"""
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Organization, Permission, Role, RolePermission
from app.inventory_models import StockAccount, StockLocation
from app.main import app
from app.stock_operation_models import StockOperationLine, StockLossHeadquartersDecision
from app.stock_loss_schemas import StockLossSubmitIn, StockLossRegionalReviewIn, StockLossHeadquartersReviewIn
from app.formal_services import stock_loss_commands, stock_loss_plan
from app.formal_services import stock_loss_regional_reviews as regional, stock_loss_headquarters_reviews as headquarters
from pg16_stock_loss_disposition_gate import snapshot
from test_formal_access import make_user, assign


def run(context, *, flow):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        source = db.get(StockAccount, context['account_id'])
        manager, _ = make_user(db, db.get(Organization, source.owner_org_id), name='Synthetic execution HTTP reviewer')
        roles = {row.code: row for row in db.scalars(select(Role))}
        assign(db, manager, roles['provincial_manager'], scope_type='organization', scope_id=str(source.owner_org_id))
        grant_ids = {}
        for action, role in ((regional.ACTION, 'provincial_manager'), (headquarters.ACTION, 'admin'),
                ('dispose_loss', 'admin'), ('read', 'admin')):
            permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
                Permission.action == action, Permission.field_code == ''))
            if permission is None:
                permission = Permission(resource='stock_operation', action=action, field_code='',
                    description='Synthetic native execution HTTP fixture')
                db.add(permission); db.flush()
            grant = db.scalar(select(RolePermission).where(RolePermission.role_id == roles[role].id,
                RolePermission.permission_id == permission.id))
            if grant is None:
                grant = RolePermission(role_id=roles[role].id, permission_id=permission.id, effect='allow')
                db.add(grant); db.flush()
            assert grant.effect == 'allow'
            grant_ids[action] = grant.id
        location = db.get(StockLocation, context['location_id'])
        transit = StockLocation(code='LOSS-HTTP-TRANSIT-' + uuid4().hex, name='Synthetic HTTP return transit',
            location_type='transit', parent_id=location.parent_id, owner_org_id=source.owner_org_id, status='active')
        db.add(transit); db.commit()
        manager_id, target_id, transit_id = manager.id, location.parent_id, transit.id
    with Session(api) as db:
        actor = load_formal_principal(db, context['engineer_id'])
        prepared, _ = stock_loss_plan.preview_loss(db, actor=actor, request=context['request'])
        submitted = stock_loss_commands.submit_loss(db, actor=actor, request=StockLossSubmitIn(
            **context['request'].model_dump(), expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        checked = regional.verify_regional_loss(db, actor=load_formal_principal(db, manager_id),
            request=StockLossRegionalReviewIn(operation_id=submitted.operation_id,
                expected_submission_plan_hash=prepared.plan_hash, comment='Independent HTTP fixture review',
                request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        line = db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id == submitted.operation_id))
        final = headquarters.approve_headquarters_loss(db, actor=actor, request=StockLossHeadquartersReviewIn(
            operation_id=submitted.operation_id, expected_submission_plan_hash=prepared.plan_hash,
            regional_review_id=checked.review_id, expected_regional_review_hash=checked.request_hash,
            decisions=(dict(line_id=line.id, disposition='return_to_region' if flow == 'return' else 'convert_used',
                reason='Independent HTTP fixture decision'),), comment='Independent HQ approval',
            request_id=uuid4().hex, idempotency_key=uuid4().hex))
        db.commit()
        decision = db.scalar(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id == final.review_id))
        command = dict(headquarters_decision_id=str(decision.id), expected_headquarters_review_hash=final.request_hash,
            expected_submission_plan_hash=prepared.plan_hash)
        operator_person_id = str(actor.person_id)
    if flow == 'return':
        command.update(target_location_id=str(target_id), transit_location_id=str(transit_id))

    statuses, commits, readonly = [], [], []
    lose_commit_reply = False

    class HttpSession(Session):
        def commit(self):
            nonlocal lose_commit_reply
            assert not self.info.get('preview_only'), 'preview must not commit'
            super().commit()
            commits.append('committed')
            if lose_commit_reply:
                lose_commit_reply = False
                raise OperationalError('synthetic post-COMMIT connection loss', {}, Exception('response unavailable'))

    def request_db(request: Request):
        with HttpSession(api) as db:
            read_only = request.method == 'GET' or request.url.path.endswith('/request-lookup')
            is_preview = request.url.path.endswith('/preview')
            db.info['preview_only'] = is_preview
            if read_only:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SHOW transaction_read_only')) == 'on'
                readonly.append(request.url.path.rsplit('/', 1)[-1])
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            connection = db.connection()
            def preview_select_only(conn, cursor, statement, parameters, context, executemany):
                assert statement.lstrip().upper().startswith('SELECT '), 'preview wrote business data'
            if is_preview:
                event.listen(connection, 'before_cursor_execute', preview_select_only)
            try:
                yield db
                if is_preview:
                    assert not db.new and not db.dirty and not db.deleted
            finally:
                if is_preview:
                    event.remove(connection, 'before_cursor_execute', preview_select_only)

    def request_actor(db: Session = Depends(get_db)):
        return load_formal_principal(db, context['admin_id'])

    base = '/api/v1/stock-operations/loss-reports'
    path = base + ('/derived-returns' if flow == 'return' else '/dispositions')
    with patch.object(app, 'dependency_overrides', {get_db: request_db, get_formal_principal: request_actor}):
        client = TestClient(app, raise_server_exceptions=False)
        try:
            def call(suffix, body, status):
                response = client.post(path + suffix, json=body)
                assert response.status_code == status, response.text
                assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
                assert response.headers['referrer-policy'] == 'no-referrer'
                key = body.get('idempotency_key', body.get('original', {}).get('idempotency_key'))
                assert key is None or key not in response.text
                statuses.append(status)
                return response.json()
            before = snapshot(owner)
            source_path = base + '/execution-sources/' + str(submitted.operation_id)
            source_response = client.get(source_path)
            assert source_response.status_code == 200, source_response.text
            source_line = source_response.json()['decisions'][0]
            assert source_line['headquarters_decision_id'] == command['headquarters_decision_id']
            assert source_line['original_posting'] is None
            preview = call('/preview', command, 200)
            assert preview['stock_effect'] == 'none' and snapshot(owner) == before
            original = dict(command, expected_plan_hash=preview['plan_hash'], request_id=uuid4().hex, idempotency_key=uuid4().hex)
            sealed = call('/request-seal', dict(operator_person_id=operator_person_id, original=original), 200)
            assert sealed['lookup_status'] == 'sealed' and sealed['seal']['stock_effect'] == 'none'
            after_seal = snapshot(owner)
            call('', original, 409)
            assert snapshot(owner) == after_seal
            assert call('/request-lookup', original, 200) == sealed
            preview = call('/preview', command, 200)
            active = dict(command, expected_plan_hash=preview['plan_hash'], request_id=uuid4().hex, idempotency_key=uuid4().hex)
            lose_commit_reply = True
            uncertain = call('', active, 503)
            assert not lose_commit_reply and uncertain['detail']['code'] == 'stock_loss_execution_unconfirmed'
            found = call('/request-lookup', active, 200)
            assert found['lookup_status'] == 'found' and found['retry_permitted'] is False
            posted = snapshot(owner)
            assert posted != after_seal
            assert call('', active, 200) == found['disposition']
            assert snapshot(owner) == posted
            source_response = client.get(source_path)
            assert source_response.status_code == 200, source_response.text
            original_posting = source_response.json()['decisions'][0]['original_posting']
            assert original_posting['disposition_id'] == found['disposition']['disposition_id']
            assert original_posting['result_scope'] == 'original_posting'
            assert snapshot(owner) == posted
            if flow == 'return':
                assert found['disposition']['stock_effect'] == 'frozen_to_return_pending'
                assert found['disposition']['return_fulfillment_required'] is True
            with Session(owner) as db:
                db.get(RolePermission, grant_ids['dispose_loss']).effect = 'deny'; db.commit()
            assert call('/request-lookup', active, 200) == found
            call('', active, 403)
            with Session(owner) as db:
                db.get(RolePermission, grant_ids['read']).effect = 'deny'; db.commit()
            call('/request-lookup', active, 403)
            assert snapshot(owner) == posted
        finally:
            client.close()
    assert len(commits) == 3  # seal, committed-with-lost-reply, idempotent replay
    return dict(passed=True, tracking=context['tracking'], flow=flow, actualHttpDatabaseRole='star_oam_api',
        syntheticLoginIdentity=True, nativeReadOnlyRequests=len(readonly), registeredHttpStatuses=statuses,
        previewSelectOnlyWithOpeningProofLocks=True,
        permanentSealLateWriteRejected=True, committedLostResponseRecovered=True,
        exactReplayNoAdditionalFacts=True, actualReadWriteGrantSeparation=True, publicHttpWritesProved=True,
        exactDecisionSourceAndHistoricalPosting=True,
        productionAcceptance=False)


def release(engines, *, tracking, flow, migrate, provision):
    if tracking not in ('quantity', 'serial') or flow not in ('disposition', 'return'):
        raise ValueError('explicit tracking and flow required')
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_sources_gate import run as sources
    from test_postgresql16_release_gate import HEAD_REVISION
    migrate('initial-upgrade', 'upgrade', 'head'); provision()
    owner, api = (engines[key] for key in ('star_oam_migrator', 'star_oam_api'))
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION
    def security():
        validate_production_database_security(api, expected_runtime_role='star_oam_api',
            expected_migration_role='star_oam_migrator')
    security()
    result = sources(engines, tracking=tracking, after_preview=lambda context: run(context, flow=flow))
    result['executionHttp'] = result.pop('submission')
    assert result['passed'] and result['executionHttp']['passed']
    security()
    result.update(actualAlembicRevision=HEAD_REVISION, runtimeSecurityBeforeAndAfter=True)
    return result
