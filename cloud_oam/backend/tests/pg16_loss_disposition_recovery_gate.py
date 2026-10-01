"""Exact HQ execution recovery after real PG16 COMMIT and grant revocation.

The existing quantity/SN disposition and derived-return gates create all
business facts normally. Wrappers only retain the original command/result;
no posting, approval, proof, grant loader or transaction is mocked.
"""
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.stock_operation_models import StockLossDisposition
from app.formal_services import stock_loss_disposition_commands, stock_loss_return_commands
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from app.main import app
from pg16_stock_loss_disposition_gate import snapshot


def release(engines, *, tracking, flow, migrate, provision):
    if tracking not in ('quantity', 'serial') or flow not in ('disposition', 'return'):
        raise ValueError('explicit quantity/serial tracking and disposition/return flow required')
    if flow == 'disposition':
        import pg16_stock_loss_disposition_gate as fixture
        commands, method = stock_loss_disposition_commands, 'execute_disposition'
    else:
        import pg16_stock_loss_derived_return_gate as fixture
        commands, method = stock_loss_return_commands, 'execute_loss_return'
    original_run, execute = fixture.run, getattr(commands, method)
    owner, api = (engines[k] for k in ('star_oam_migrator', 'star_oam_api'))

    def exercise(context):
        captured = {}
        def record(db, *, actor, request):
            answer = execute(db, actor=actor, request=request)
            if isinstance(answer, dict):
                captured[answer['disposition_id']] = (request.model_copy(deep=True), dict(answer))
            return answer
        with patch.object(commands, method, side_effect=record):
            result = original_run(context)
        assert result['passed']
        with Session(owner) as db:
            role = db.scalars(select(Role).where(Role.code == 'admin')).one()
            read = db.scalars(select(Permission).where(Permission.resource == 'stock_operation',
                Permission.action == 'read', Permission.field_code == '')).one()
            grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
                RolePermission.permission_id == read.id))
            if grant is None:
                grant = RolePermission(role_id=role.id, permission_id=read.id, effect='allow')
                db.add(grant)
            write = db.scalars(select(RolePermission).join(Permission).where(
                RolePermission.role_id == role.id, Permission.resource == 'stock_operation',
                Permission.action == 'dispose_loss', Permission.field_code == '')).one()
            write_id, previous_write = write.id, write.effect
            write.effect = 'deny'
            db.commit()
            read_id, previous_read = grant.id, grant.effect
            identifiers = tuple(str(v) for v in db.scalars(select(StockLossDisposition.id)))
        assert identifiers and set(identifiers).issubset(captured)
        before = snapshot(owner)
        statements = []
        http_statuses = []

        def request_db():
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
                yield db

        def request_actor(db: Session = Depends(get_db)):
            # Only login identity is synthetic. Scope and grants are loaded
            # from the real database, including the revocations below.
            return load_formal_principal(db, context['admin_id'])

        def http_read(command, status, expected=None):
            route = 'dispositions' if flow == 'disposition' else 'derived-returns'
            with patch.object(app, 'dependency_overrides', {
                    get_db: request_db, get_formal_principal: request_actor}):
                client = TestClient(app, raise_server_exceptions=False)
                try:
                    response = client.post(
                        '/api/v1/stock-operations/loss-reports/' + route + '/request-lookup',
                        json=command.model_dump(mode='json'))
                finally:
                    client.close()
            assert response.status_code == status, response.text
            assert 'private' in response.headers['cache-control']
            assert 'no-store' in response.headers['cache-control']
            assert response.headers['referrer-policy'] == 'no-referrer'
            assert command.idempotency_key not in response.text
            if status == 200:
                assert response.json() == dict(lookup_status='found', retry_permitted=False,
                    result_scope='original_command', disposition=expected)
            else:
                assert 'disposition' not in response.json()
            http_statuses.append(status)

        def select_only(conn, cursor, statement, parameters, context, executemany):
            normalized = statement.strip().upper()
            assert normalized.startswith('SELECT ') or normalized == 'SET TRANSACTION READ ONLY', 'recovery wrote business data'
            if normalized.startswith('SELECT '):
                statements.append('SELECT')
        event.listen(api, 'before_cursor_execute', select_only)
        try:
            for identifier in identifiers:
                command, expected = captured[identifier]
                # Fresh session is given only the command, never the response ID.
                with Session(api) as db:
                    db.execute(text('SET TRANSACTION READ ONLY'))
                    actor = load_formal_principal(db, context['admin_id'])
                    assert not any(g.resource == 'stock_operation' and g.action == 'dispose_loss'
                        and g.effect == 'allow' for g in actor.entitlements)
                    found = recovery.lookup_disposition_request(db, actor=actor, request=command, flow=flow)
                    assert found == dict(lookup_status='found', retry_permitted=False, disposition=expected)
                    for field, value in (('idempotency_key', uuid4().hex), ('expected_plan_hash', 'f' * 64)):
                        with pytest.raises(InventoryReadError):
                            recovery.lookup_disposition_request(db, actor=actor,
                                request=command.model_copy(update={field:value}), flow=flow)
                    assert not db.new and not db.dirty and not db.deleted
                http_read(command, 200, expected)
                for field, value in (('idempotency_key', uuid4().hex), ('expected_plan_hash', 'f' * 64)):
                    http_read(command.model_copy(update={field:value}), 409)
            assert statements and snapshot(owner) == before
            with Session(owner) as db:
                db.get(RolePermission, read_id).effect = 'deny'
                db.commit()
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                actor = load_formal_principal(db, context['admin_id'])
                with pytest.raises(InventoryReadError) as caught:
                    recovery.lookup_disposition_request(db, actor=actor, request=command, flow=flow)
                assert caught.value.code == 'stock_loss_disposition_read_forbidden'
            http_read(command, 403)
            assert snapshot(owner) == before
        finally:
            event.remove(api, 'before_cursor_execute', select_only)
            with Session(owner) as db:
                db.get(RolePermission, write_id).effect = previous_write
                db.get(RolePermission, read_id).effect = previous_read
                db.commit()
        result['executionRecovery'] = dict(passed=True, flow=flow, tracking=tracking,
            committedFacts=len(identifiers), actualReadWriteGrantSeparation=True,
            freshSessionOriginalCommand=True, selectOnlyStatements=len(statements),
            nativeReadOnlyTransactions=True, registeredHttpStatuses=http_statuses,
            actualHttpDatabaseRole='star_oam_api', syntheticLoginIdentity=True,
            factsUnchanged=True, retryPermitted=False, productionAcceptance=False)
        print('PG16 HQ recovery '+tracking+' '+flow+': committed facts, original commands, revoked writes and SELECT-only reads PASS', flush=True)
        return result

    with patch.object(fixture, 'run', side_effect=exercise):
        return fixture.release(engines, tracking=tracking, migrate=migrate, provision=provision)
