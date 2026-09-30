"""Loss sender HTTP reads over actual PG16 facts and seeded runtime grants."""
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission

BASE = '/api/v1/stock-operations/loss-reports/returns'


def run(world, *, phase, departed, shipped, snapshot, permissions=False):
    from app.main import app

    context = world['context']
    owner, api = world['owner'], world['api']
    identity = {'user_id': context['engineer_id']}
    order_id = str(world['order_id'])
    total = context['request'].lines[0].quantity
    departed, shipped = Decimal(departed), Decimal(shipped)
    calls = []
    statements = []

    def select_only(connection, cursor, statement, parameters, execution, many):
        # Opening evidence deliberately takes row/advisory locks. PostgreSQL
        # READ ONLY rejects SELECT FOR UPDATE, so enforce SELECT-only SQL and
        # unchanged committed facts without removing those consistency locks.
        assert not (execution.isinsert or execution.isupdate or execution.isdelete)
        kind = statement.lstrip().split(None, 1)[0].upper()
        assert kind in ('SELECT', 'SHOW'), ('unexpected query mutation', kind)
        statements.append(kind)

    def database():
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            try:
                yield db
                assert not db.new and not db.dirty and not db.deleted
            finally:
                db.rollback()

    def principal(db=Depends(get_db)):
        # Only authentication is injected for the synthetic identity. Both
        # require_permission and service authorization run against real grants.
        return load_formal_principal(db, identity['user_id'])

    def unchanged():
        result = snapshot(owner)
        with owner.connect() as db:
            for table in ('stock_operation_outbounds', 'stock_operation_outbound_lines',
                          'stock_operation_outbound_serials', 'stock_operation_command_seals',
                          'stock_loss_dispositions', 'receipts', 'receipt_lines', 'inbound_orders'):
                result[table] = tuple(sorted(repr(dict(row)) for row in
                    db.execute(text('SELECT * FROM ' + table)).mappings()))
        return result

    @contextmanager
    def denied(actions):
        with Session(owner) as db:
            grants = tuple(db.scalars(select(RolePermission)
                .join(Role, Role.id == RolePermission.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Role.code == 'technician', Permission.resource == 'stock_operation',
                       Permission.action.in_(actions), Permission.field_code == '')))
            assert len(grants) == len(actions), actions
            saved = [(grant.id, grant.effect) for grant in grants]
            for grant in grants:
                assert grant.effect == 'allow'
                grant.effect = 'deny'
            db.commit()
        try:
            yield
        finally:
            with Session(owner) as db:
                for grant_id, effect in saved:
                    db.get(RolePermission, grant_id).effect = effect
                db.commit()

    with patch.object(app, 'dependency_overrides', {
        get_db: database, get_formal_principal: principal,
    }):
        client = TestClient(app, raise_server_exceptions=False)
        event.listen(api, 'before_cursor_execute', select_only)
        try:
            def get(path, status=200):
                response = client.get(BASE + path)
                assert response.status_code == status, (phase, path, response.status_code, response.text)
                assert 'no-store' in response.headers['cache-control']
                assert response.headers['referrer-policy'] == 'no-referrer'
                assert all(value not in response.text for value in
                           ('qr_code', 'PRIVATE-SCAN', 'source_recovery_line_id', 'work_order_id'))
                calls.append({'path': path, 'status': status})
                return response.json()

            before = unchanged()
            listing = get('/my-sending')
            assert len(listing['items']) == 1 and listing['next_after_id'] is None
            origin = listing['items'][0]['origin']
            assert origin['operation_id'] == order_id
            assert origin['requester_id'] == str(context['person_id'])
            assert origin['submitted_by_user_id'] == str(context['admin_id'])
            assert origin['submitted_by_user_id'] != str(context['engineer_id'])
            detail = get('/' + order_id)
            assert Decimal(detail['line']['return_quantity']) == total
            assert len(detail['line']['selected_serials']) == int(context['tracking'] == 'serial')
            assert all(key not in detail['line'] for key in ('held_quantity', 'selectable_quantity'))
            options = get('/' + order_id + '/outbounds/options')
            assert sum(Decimal(line['selectable_quantity']) for line in options['lines']) == total - departed
            history = get('/' + order_id + '/outbounds')
            assert history['outbound_status'] == ('not_outbound' if departed == 0 else
                'outbound' if departed == total else 'partially_outbound')
            assert history['original']['submitted_by_user_id'] == str(context['admin_id'])
            choices = get('/' + order_id + '/shipments/options')
            assert sum(Decimal(line['selectable_quantity']) for line in choices['lines']) == departed - shipped
            parcels = get('/' + order_id + '/shipments')
            assert parcels['shipment_status'] == ('not_shipped' if shipped == 0 else
                'shipped' if shipped == total else 'partially_shipped')
            assert sum(Decimal(line['selected_quantity']) for parcel in parcels['items']
                       for line in parcel['lines']) == shipped
            get('/' + str(uuid4()), 404)
            get('/my-sending?snapshot_hash=PRIVATE-SCAN', 422)
            get('/PRIVATE-SCAN/outbounds', 422)
            assert unchanged() == before

            if permissions:
                with denied(('outbound_return', 'ship_return')):
                    before = unchanged()
                    for suffix in ('', '/outbounds', '/shipments'):
                        get('/' + order_id + suffix)
                    get('/my-sending')
                    for suffix in ('/outbounds/options', '/shipments/options'):
                        get('/' + order_id + suffix, 403)
                    assert unchanged() == before
                with denied(('read',)):
                    before = unchanged()
                    get('/my-sending', 403)
                    for suffix in ('', '/outbounds', '/shipments'):
                        get('/' + order_id + suffix, 403)
                    assert unchanged() == before
                identity['user_id'] = context['admin_id']
                before = unchanged()
                assert get('/my-sending')['items'] == []
                get('/' + order_id, 404)
                assert unchanged() == before
        finally:
            client.close()
            event.remove(api, 'before_cursor_execute', select_only)
    print('PG16 loss sender ' + context['tracking'] + ': ' + phase + ' real read-only HTTP PASS', flush=True)
    return dict(passed=True, phase=phase, apiRoleSelectOnly=True, queryStatementCount=len(statements), factsUnchanged=True,
                departed=str(departed), shipped=str(shipped), calls=calls,
                realPermissionRevocationChecked=permissions, historicalHqActorPreserved=True)
