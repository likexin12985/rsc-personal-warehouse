from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from app.formal_services import opening_start_seals as seals
from test_opening_start_recovery import db, world, saved, clock
from test_formal_opening_stocktake_write_api import write_api_client

PREFIX = '/api/v1/stocktakes/opening'


def test_completed_original_wins_and_v2_reads_never_lock_or_write(saved):
    world, result = saved
    args = dict(actor=world.principals['manager_x'], region_org_id=world.command.region_org_id,
        publication_id=uuid4(), trace_request_id='opening-request-0001')
    statements = []
    def record(*args): statements.append(args[2])
    event.listen(world.db.bind, 'before_cursor_execute', record)
    try: recovered = seals.recover_start_command(world.db, **args)
    finally: event.remove(world.db.bind, 'before_cursor_execute', record)
    assert recovered['outcome'] == 'found' and recovered['seal'] is None
    assert recovered['result']['task_id'] == result.task_id
    assert statements and all(sql.lstrip().startswith('SELECT') and 'FOR UPDATE' not in sql for sql in statements)
    original = seals.seal_start_command(world.db, **args)
    assert original == recovered and not world.db.new and not world.db.dirty


def coordinates():
    return dict(region_org_id=str(uuid4()), publication_id=str(uuid4()), trace_request_id=uuid4().hex)


def test_api_terminal_mutation_requires_exact_original_headers_permission_and_strict_body(write_api_client, monkeypatch):
    client, db, principal = write_api_client
    probe = Mock(); monkeypatch.setattr(seals, 'seal_start_command', probe)
    args = coordinates(); headers = {'X-Request-ID': args['trace_request_id'], 'Idempotency-Key': 'opening-start-seal:' + args['trace_request_id']}
    assert client.post(PREFIX + '/seal-start-command', json=args).status_code == 400
    assert client.post(PREFIX + '/seal-start-command', json=args, headers=headers | {'X-Request-ID': uuid4().hex}).status_code == 400
    assert client.post(PREFIX + '/seal-start-command', json=args, headers=headers | {'Idempotency-Key': uuid4().hex}).status_code == 400
    assert client.post(PREFIX + '/seal-start-command', json=args | {'actor_person_id': str(uuid4())}, headers=headers).status_code == 422
    principal.allowed_actions.clear()
    assert client.post(PREFIX + '/seal-start-command', json=args, headers=headers).status_code == 403
    assert not probe.called and not db.commit.called


def test_unknown_commit_is_sanitized_and_cannot_be_reported_as_a_seal(write_api_client, monkeypatch):
    client, db, _ = write_api_client
    args = coordinates()
    result = dict(schema_version='rsc.opening_start_recovery.v2', actor_person_id=str(uuid4()), authorization_version=1,
        **{k: v for k, v in args.items() if k != 'trace_request_id'}, outcome='not_observed', automatic_retry_allowed=False, result=None, seal=None)
    monkeypatch.setattr(seals, 'seal_start_command', Mock(return_value=result))
    db.commit.side_effect = SQLAlchemyError('synthetic private SQL')
    response = client.post(PREFIX + '/seal-start-command', json=args,
        headers={'X-Request-ID': args['trace_request_id'], 'Idempotency-Key': 'opening-start-seal:' + args['trace_request_id']})
    assert response.status_code == 503 and 'private SQL' not in response.text
    assert response.headers['cache-control'] == 'no-store' and db.rollback.call_count == 1


def test_v2_lookup_is_strict_and_independent_from_legacy_v1(write_api_client, monkeypatch):
    client, db, _ = write_api_client; args = coordinates()
    result = dict(schema_version='rsc.opening_start_recovery.v2', actor_person_id=str(uuid4()), authorization_version=1,
        **{k: v for k, v in args.items() if k != 'trace_request_id'}, outcome='not_observed', automatic_retry_allowed=False, result=None, seal=None)
    probe = Mock(return_value=result); monkeypatch.setattr(seals, 'recover_start_command', probe)
    response = client.get(PREFIX + '/start-command-result', params=args)
    assert response.status_code == 200 and response.json() == result
    assert client.get(PREFIX + '/start-command-result', params=args | {'automatic_retry_allowed': True}).status_code == 422
    assert client.get(PREFIX + '/start-command-result', params=list(args.items()) + [('trace_request_id', args['trace_request_id'])]).status_code == 422
    assert probe.call_count == 1 and not db.commit.called


def test_v2_wire_cannot_claim_both_execution_and_permanent_nonexecution():
    from app.opening_stocktake_schemas import OpeningStartCommandResultOut
    from pydantic import ValidationError
    args=coordinates();person=str(uuid4())
    proof=dict(seal_id=str(uuid4()),actor_person_id=person,authorization_version=1,**args,
        sealed_at='2026-09-20T00:00:00Z',permanent_nonexecution=True)
    result=dict(schema_version='rsc.opening_start_recovery.v2',actor_person_id=person,authorization_version=1,
        region_org_id=args['region_org_id'],publication_id=args['publication_id'],outcome='sealed',automatic_retry_allowed=False,result=None,seal=proof)
    OpeningStartCommandResultOut.model_validate(result)
    for change in [dict(outcome='not_observed'),dict(seal=None),dict(seal=proof|{'actor_person_id':str(uuid4())}),
            dict(seal=proof|{'authorization_version':2}),dict(seal=proof|{'sealed_at':'2026-09-20T00:00:00'})]:
        with pytest.raises(ValidationError):OpeningStartCommandResultOut.model_validate(result|change)
