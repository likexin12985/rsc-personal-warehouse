"""HTTP seal results preserve old lookup shape and never claim unknown commits."""
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.routers import formal_work_order_material as api
from app.work_order_material_schemas import WorkOrderMaterialSealedLookupOut, WorkOrderMaterialSealOut
from test_formal_work_order_material_routes import http


def path(http):
    return f'/api/v1/work-orders/{http.order_id}/material-operations/occupy/by-request/original-request/seal'


def body(http):
    return {'operator_person_id':str(http.actor.person_id), 'request_hash':'a'*64}


def response(http):
    return WorkOrderMaterialSealedLookupOut(seal=WorkOrderMaterialSealOut(seal_id=uuid4(),
        work_order_id=http.order_id, operator_person_id=http.actor.person_id, operation_type='occupy',
        request_id='original-request', request_hash='a'*64, sealed_at=datetime.now(timezone.utc)))


def test_seal_and_original_get_use_separate_sealed_union_shape(http, monkeypatch):
    value = response(http)
    command = Mock(return_value=value); monkeypatch.setattr(api, 'seal_command', command)
    result = http.client.post(path(http), json=body(http), headers={'X-Request-ID':'original-request'})
    assert result.status_code == 200 and result.json()['lookup_status'] == 'sealed_not_executed'
    assert set(result.json()) == {'schema_version','lookup_status','command','seal'}
    assert result.json()['command'] is None and result.headers['cache-control'] == 'private, no-store'
    http.db.commit.assert_called_once()
    assert command.call_args.kwargs['request_id'] == 'original-request'
    monkeypatch.setattr(api, 'lookup_operation', Mock(return_value=value))
    assert http.client.get(path(http).removesuffix('/seal')).json() == result.json()


@pytest.mark.parametrize('change', ['operator','digest','trace','key','extra'])
def test_bad_seal_coordinates_do_not_dispatch(http, monkeypatch, change):
    command = Mock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(api, 'seal_command', command)
    payload, headers = body(http), {}
    if change == 'operator': payload['operator_person_id'] = str(uuid4())
    elif change == 'digest': payload['request_hash'] = 'bad'
    elif change == 'trace': headers['X-Request-ID'] = 'another-request'
    elif change == 'key': headers['Idempotency-Key'] = 'another-key'
    else: payload['idempotency_key'] = 'unexpected'
    result = http.client.post(path(http), json=payload, headers=headers)
    assert result.status_code in {400,403,422}; command.assert_not_called()
    http.db.commit.assert_not_called()


def test_uncertain_seal_commit_returns_no_completion_and_requires_original_read(http, monkeypatch):
    monkeypatch.setattr(api, 'seal_command', Mock(return_value=response(http)))
    http.db.commit.side_effect = SQLAlchemyError('unknown commit')
    result = http.client.post(path(http), json=body(http))
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'work_order_seal_unavailable'
    assert 'seal' not in result.json()
    http.db.rollback.assert_called_once()
