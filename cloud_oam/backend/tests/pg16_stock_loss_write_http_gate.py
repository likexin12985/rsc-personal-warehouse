"""HTTP submit/seal recovery against a fresh owned PostgreSQL API-role fixture."""
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import get_db
from app.database_security import validate_production_database_security
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import RolePermission
from app.main import app
from app.stock_loss_schemas import StockLossSubmitIn
from pg16_stock_loss_seal_gate import all_facts, stock_facts
from pg16_stock_loss_sources_gate import run as sources
from pg16_stock_loss_submit_gate import provision_recovery_read

PATH = '/api/v1/stock-operations/loss-reports'


def exercise(context):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    provision_recovery_read(owner)
    fault = {'commit': None}

    class RequestSession(Session):
        def commit(self):
            if fault['commit'] != 'before':
                super().commit()
            if fault['commit'] is not None:
                raise OperationalError('SYNTHETIC-PRIVATE-SQL', {}, Exception('SYNTHETIC-PRIVATE-COMMIT'))

    def database():
        with RequestSession(api) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            yield db

    def principal(db=Depends(get_db)):
        return load_formal_principal(db, context['engineer_id'])

    def post(client, suffix, body, status=200):
        headers = {}
        if 'request_id' in body:
            headers = {'X-Request-ID': body['request_id'], 'Idempotency-Key': body['idempotency_key']}
        response = client.post(PATH + suffix, json=body, headers=headers)
        assert response.status_code == status, (suffix, response.status_code, response.text)
        assert 'no-store' in response.headers['cache-control']
        assert response.headers['referrer-policy'] == 'no-referrer'
        assert 'SYNTHETIC-PRIVATE-' not in response.text
        return response.json()

    with patch.object(app, 'dependency_overrides', {get_db: database, get_formal_principal: principal}):
        # Use the supplied isolated API-role sessions; do not bootstrap the
        # application's default test SQLite database through ASGI lifespan.
        with closing(TestClient(app, raise_server_exceptions=False)) as client:
            preview = post(client, '/preview', context['request'].model_dump(mode='json'))
            value = StockLossSubmitIn(**context['request'].model_dump(), expected_plan_hash=preview['plan_hash'],
                request_id=uuid4().hex, idempotency_key=uuid4().hex).model_dump(mode='json')

            def coordinates(value):
                return {key: value[key] for key in ('operator_person_id', 'request_id', 'idempotency_key', 'expected_plan_hash')} | {
                    'request_hash': preview['request_hash']}

            # A transaction that never committed must leave every fact unchanged.
            before = all_facts(owner)
            fault['commit'] = 'before'
            rejected = post(client, '', value, 503)
            fault['commit'] = None
            assert rejected['detail']['code'] == 'stock_loss_write_unconfirmed'
            assert all_facts(owner) == before
            absent = post(client, '/request-lookup', coordinates(value))
            assert absent == {'lookup_status': 'not_found', 'retry_permitted': False}

            # Seal another unresolved command; lost acknowledgement is recovered
            # from its immutable audit, and its original write cannot arrive late.
            abandoned = value | {'request_id': uuid4().hex, 'idempotency_key': uuid4().hex}
            seal = coordinates(abandoned) | {'source_location_id': str(context['location_id'])}
            before = all_facts(owner)
            fault['commit'] = 'before'
            post(client, '/request-seal', seal, 503)
            fault['commit'] = None
            assert all_facts(owner) == before
            stock_before = stock_facts(owner)
            fault['commit'] = 'after'
            post(client, '/request-seal', seal, 503)
            fault['commit'] = None
            sealed = post(client, '/request-lookup', coordinates(abandoned))
            assert sealed['lookup_status'] == 'sealed' and sealed['retry_permitted'] is False
            assert stock_facts(owner) == stock_before
            assert post(client, '/request-seal', seal) == sealed
            assert post(client, '', abandoned, 409)['detail']['code'] == 'stock_loss_request_sealed'

            # Real API-role COMMIT happens; only delivery of its response fails.
            # HTTP lookup recovers the one freeze instead of replaying a POST.
            fault['commit'] = 'after'
            post(client, '', value, 503)
            fault['commit'] = None
            found = post(client, '/request-lookup', coordinates(value))
            assert found['lookup_status'] == 'found' and found['retry_permitted'] is False
            result = found['submission']
            assert result['status'] == 'submitted' and result['posting_transaction_id']
            committed = all_facts(owner)
            assert post(client, '', value) == result
            assert post(client, '/request-seal', coordinates(value) | {
                'source_location_id': str(context['location_id'])}) == found
            assert all_facts(owner) == committed

            # Read permission survives withdrawal of submit permission; a new
            # write is forbidden, while the original successful result remains readable.
            with Session(owner) as db:
                db.get(RolePermission, context['grant_id']).effect = 'deny'
                db.commit()
            post(client, '', value, 403)
            post(client, '/request-seal', seal, 403)
            assert post(client, '/request-lookup', coordinates(value)) == found
            assert all_facts(owner) == committed
    print('PG16 loss HTTP ' + context['tracking'] + ': submit, seal, unknown COMMIT, recovery and revoked writes PASS', flush=True)
    return dict(passed=True, apiRoleHttp=True, commitResponseLostRecovered=True,
        preCommitFailureRolledBack=True, sealResponseLostRecovered=True,
        lateSubmissionRejected=True, exactReplayNoSecondFreeze=True, revokedWriteStillReadable=True)


def release(engines, *, tracking, migrate, provision):
    migrate('http-initial-upgrade', 'upgrade', 'head')
    provision()
    result = sources(engines, tracking=tracking, after_preview=exercise)
    validate_production_database_security(engines['star_oam_api'],
        expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
    result['http'] = result.pop('submission')
    result['runtimeSecurity'] = True
    assert result['passed'] and result['http']['passed']
    return result
