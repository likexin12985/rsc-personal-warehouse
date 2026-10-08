"""Exercise the identity-injecting helper's loopback and route boundary."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pg16_fulfillment_browser import ORIGIN, browser_application, frontend_manifest


@pytest.fixture
def preview(tmp_path):
    for folder in ('frontend/dist-warehouse/assets', 'frontend/public/brand', 'evidence'):
        (tmp_path / folder).mkdir(parents=True)
    build = tmp_path / 'frontend/dist-warehouse'
    (build / 'index.html').write_text('<html><body>warehouse</body></html>')
    (build / 'sw.js').write_text('// synthetic worker')
    (build / 'manifest.webmanifest').write_text('{}')
    request_id = uuid4()
    prefix = '/api/v1/material-requests/' + str(request_id)
    mounted = FastAPI()
    invoked = []

    @mounted.api_route('/api/{path:path}', methods=['GET', 'POST', 'DELETE'])
    def endpoint(path: str):
        invoked.append(path)
        return {'reached': True}

    app, observed = browser_application(mounted, tmp_path, api=None, admin='fixture-user',
        directory=tmp_path / 'evidence', request_id=request_id)
    return app, prefix, invoked, observed, tmp_path, mounted


@pytest.mark.parametrize('method,suffix,headers', [
    ('POST', '/receipts', {}),
    ('POST', '/receipts', {'Origin': 'https://unrelated.example'}),
    ('POST', '/receipts', {'Origin': ORIGIN, 'Host': 'unrelated.example'}),
    ('POST', '/receipts', {'Origin': ORIGIN, 'Sec-Fetch-Site': 'cross-site'}),
    ('POST', '/outbounds', {'Origin': ORIGIN}),
    ('DELETE', '/receipts', {'Origin': ORIGIN}),
    ('GET', '/unlisted-operation', {}),
    ('GET', '/receipts', {'Host': 'unrelated.example'}),
    ('GET', '/receipts', {'Sec-Fetch-Site': 'cross-site'}),
])
def test_refuses_other_origins_and_operations_before_app_route(preview, method, suffix, headers):
    app, prefix, invoked, observed, _, _ = preview
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        response = client.request(method, prefix + suffix, headers=headers)
    assert response.status_code == 403
    assert invoked == observed == []


def test_other_request_and_nonloopback_client_cannot_use_fixture_identity(preview):
    app, _, invoked, observed, _, _ = preview
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        assert client.post(f'/api/v1/material-requests/{uuid4()}/receipts', headers={'Origin': ORIGIN}).status_code == 403
    with TestClient(app, base_url=ORIGIN, client=('192.0.2.5', 12345)) as client:
        assert client.get('/api/auth/me').status_code == 403
    assert invoked == observed == []


def test_warehouse_browser_permits_only_current_return_acceptance_and_posting(preview):
    _, prefix, invoked, _, root, mounted = preview
    return_id, receipt_id = uuid4(), uuid4()
    app, observed = browser_application(mounted, root, api=None, admin='fixture-custodian',
        directory=root/'evidence', request_id=prefix.rsplit('/', 1)[-1], warehouse_return_id=return_id)
    base = f'/api/v1/rejection-returns/{return_id}'
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        for path in (base+'/warehouse-receipts', base+f'/warehouse-receipts/{receipt_id}/inbounds'):
            assert client.post(path, headers={'Origin': ORIGIN}).status_code == 200
        for path in ('/api/auth/me', '/api/access/context', '/api/v1/rejection-returns/my-warehouse', base+'/warehouse',
                     base+'/warehouse-receipts/command-status', base+f'/warehouse-receipts/{receipt_id}/inbounds/preview',
                     base+f'/warehouse-receipts/{receipt_id}/inbounds/command-status'):
            assert client.get(path).status_code == 200
        count = len(invoked)
        for path in (prefix+'/receipts', prefix+'/close', prefix+f'/inbound-orders/{receipt_id}/post',
                     f'/api/v1/rejection-returns/{uuid4()}/warehouse-receipts', '/api/files/upload-intents'):
            assert client.post(path, headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(base+'/warehouse-receipts').status_code == 403
        assert client.get(prefix).status_code == 403
        assert client.get(f'/api/v1/rejection-returns/{uuid4()}/warehouse').status_code == 403
        assert client.get(prefix+f'/my-receiving/{receipt_id}/candidates').status_code == 403
        assert len(invoked) == count
    assert len(observed) == 9 and mounted.dependency_overrides == {}
    with pytest.raises(ValueError, match='one browser write scope'):
        browser_application(mounted, root, api=None, admin='fixture', directory=root/'evidence',
            request_id=uuid4(), personal_only=True, warehouse_return_id=return_id)


def test_permitted_writes_and_reads_are_observed_without_changing_mounted_app(preview):
    app, prefix, invoked, observed, _, mounted = preview
    assert mounted.dependency_overrides == {}
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        for suffix in ('/receipts', '/inbound-orders', f'/inbound-orders/{uuid4()}/post'):
            result = client.post(prefix + suffix, headers={'Origin': ORIGIN})
            assert result.status_code == 200 and result.headers['Cache-Control'] == 'private, no-store, max-age=0'
        assert client.get(prefix + '/receipts').status_code == 200
    assert len(invoked) == len(observed) == 4
    assert mounted.dependency_overrides == {}


def test_browser_assets_keep_mime_types_and_fixture_notice(preview):
    app, _, _, _, root, _ = preview
    original = frontend_manifest(root)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        assert 'application/javascript' in client.get('/xx/sw.js').headers['Content-Type']
        assert 'application/manifest+json' in client.get('/xx/manifest.webmanifest').headers['Content-Type']
        assert '不是正式环境或用户验收' in client.get('/xx/material-requests').text
    (root / 'frontend/dist-warehouse/sw.js').write_text('// changed')
    assert frontend_manifest(root) != original


def test_closure_preview_allows_only_exact_close_and_readback(preview):
    _, prefix, invoked, _, root, mounted = preview
    app, observed = browser_application(mounted, root, api=None, admin='fixture-user',
        directory=root / 'evidence', request_id=prefix.rsplit('/', 1)[1], closure_only=True)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        assert client.post(prefix + '/close', headers={'Origin': ORIGIN}).status_code == 200
        assert client.get(prefix + '/closure').status_code == 200
        assert client.get(prefix + '/close-command-status').status_code == 200
        for suffix in ('/receipts', '/inbound-orders', f'/inbound-orders/{uuid4()}/post'):
            assert client.post(prefix + suffix, headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(prefix + '/close', headers={'Origin': 'https://unrelated.example'}).status_code == 403
    assert len(invoked) == len(observed) == 3


def test_cancellation_preview_limits_request_and_write_scope(preview):
    _, prefix, invoked, _, root, mounted = preview
    app, observed = browser_application(mounted, root, api=None, admin='original-requester',
        directory=root / 'evidence', request_id=prefix.rsplit('/', 1)[1], cancellation_only=True)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        assert client.post(prefix + '/cancel-remaining', headers={'Origin': ORIGIN}).status_code == 200
        for suffix in ('/remaining-cancellation', '/cancel-remaining-command-status'):
            assert client.get(prefix + suffix).status_code == 200
            assert client.get('/api/v1/material-requests/' + str(uuid4()) + suffix).status_code == 403
        for suffix in ('/close', '/receipts', '/inbound-orders', f'/inbound-orders/{uuid4()}/post'):
            assert client.post(prefix + suffix, headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(prefix + '/cancel-remaining').status_code == 403
        assert client.post(prefix + '/cancel-remaining', headers={'Origin': 'https://unrelated.example'}).status_code == 403
        assert client.post('/api/v1/material-requests/' + str(uuid4()) + '/cancel-remaining', headers={'Origin': ORIGIN}).status_code == 403
    assert len(invoked) == len(observed) == 3


def test_other_preview_scopes_read_cancellation_without_write_access(preview):
    app, prefix, invoked, observed, root, mounted = preview
    for scoped in (app, browser_application(mounted, root, api=None, admin='fixture-user',
            directory=root / 'evidence', request_id=prefix.rsplit('/', 1)[1], closure_only=True)[0]):
        with TestClient(scoped, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
            assert client.get(prefix + '/remaining-cancellation').status_code == 200
            assert client.get(prefix + '/cancel-remaining-command-status').status_code == 200
            assert client.post(prefix + '/cancel-remaining', headers={'Origin': ORIGIN}).status_code == 403
    with pytest.raises(ValueError, match='one browser write scope'):
        browser_application(mounted, root, api=None, admin='fixture-user',
            directory=root / 'evidence', request_id=prefix.rsplit('/', 1)[1], closure_only=True, cancellation_only=True)


def test_personal_preview_only_allows_recipient_commands_and_exact_read_paths(preview):
    _, prefix, invoked, _, root, mounted = preview
    app, observed = browser_application(mounted, root, api=None, admin='original-requester',
        directory=root / 'evidence', request_id=prefix.rsplit('/', 1)[1], personal_only=True)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        for suffix in ('/my-receipts', '/my-inbounds'):
            assert client.post(prefix + suffix, headers={'Origin': ORIGIN}).status_code == 200
            assert client.post(prefix + suffix).status_code == 403
            assert client.post('/api/v1/material-requests/' + str(uuid4()) + suffix, headers={'Origin': ORIGIN}).status_code == 403
            for recovery in ('command-status', 'trace-status'):
                assert client.get(prefix + suffix + '/' + recovery).status_code == 200
        for suffix in ('/my-receiving', '/my-inbounds/candidates', f'/my-receiving/{uuid4()}/candidates'):
            assert client.get(prefix + suffix).status_code == 200
        for suffix in ('/cancel-remaining', '/close', '/receipts', '/inbound-orders', f'/inbound-orders/{uuid4()}/post'):
            assert client.post(prefix + suffix, headers={'Origin': ORIGIN}).status_code == 403
    assert len(invoked) == len(observed) == 9


def test_supply_preview_only_mutates_the_exact_existing_plan(preview):
    _, prefix, invoked, _, root, mounted = preview
    task_id = uuid4()
    app, observed = browser_application(mounted, root, api=None, admin='fixture-hq',
        directory=root/'evidence', request_id=prefix.rsplit('/', 1)[1], supply_task_id=task_id)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        path = prefix + '/supply-tasks/' + str(task_id)
        assert client.post(path, headers={'Origin': ORIGIN}).status_code == 200
        assert client.get('/api/v1/material-request-supply-command-status').status_code == 200
        assert client.get(prefix).status_code == 200
        assert '供给计划更新与取消' in client.get('/xx/material-requests').text
        assert client.get(prefix+'/return-compensations/candidates').status_code == 200
        assert client.get('/api/v1/material-requests/'+str(uuid4())+'/return-compensations/candidates').status_code == 403
        count = len(invoked)
        for blocked in (prefix+'/supply-tasks', prefix+'/supply-tasks/'+str(uuid4()),
                '/api/v1/material-requests/'+str(uuid4())+'/supply-tasks/'+str(task_id),
                prefix+'/allocations', prefix+'/receipts', prefix+'/close',
                prefix+'/inbound-orders/'+str(uuid4())+'/post'):
            assert client.post(blocked, headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(path).status_code == 403
        assert client.post(path, headers={'Origin': 'https://unrelated.example'}).status_code == 403
        assert len(invoked) == count
    assert len(observed) == 5 and mounted.dependency_overrides == {}
    for scope in ({'personal_only': True}, {'closure_only': True}, {'cancellation_only': True},
                  {'warehouse_return_id': uuid4()}):
        with pytest.raises(ValueError, match='one browser write scope'):
            browser_application(mounted, root, api=None, admin='fixture', directory=root/'evidence',
                request_id=uuid4(), supply_task_id=task_id, **scope)


def test_remaining_supply_browser_only_creates_for_current_request(preview):
    _, prefix, invoked, _, root, mounted = preview
    app, observed = browser_application(mounted, root, api=None, admin='fixture-hq',
        directory=root/'evidence', request_id=prefix.rsplit('/', 1)[1], supply_create_only=True)
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345)) as client:
        assert client.get(prefix+'/supply-planning-capacity').status_code == 200
        assert client.post(prefix+'/supply-tasks', headers={'Origin': ORIGIN}).status_code == 200
        assert '剩余供给计划创建' in client.get('/xx/material-requests').text
        count = len(invoked)
        for suffix in ('/receipts', '/close', '/allocations', '/supply-tasks/'+str(uuid4()), '/inbound-orders/'+str(uuid4())+'/post'):
            assert client.post(prefix+suffix, headers={'Origin': ORIGIN}).status_code == 403
        other = '/api/v1/material-requests/'+str(uuid4())
        assert client.get(other+'/supply-planning-capacity').status_code == 403
        assert client.post(other+'/supply-tasks', headers={'Origin': ORIGIN}).status_code == 403
        assert client.post(prefix+'/supply-tasks').status_code == 403
        assert len(invoked) == count
    for scope in ({'supply_task_id':uuid4()}, {'closure_only':True}, {'personal_only':True},
                  {'cancellation_only':True}, {'warehouse_return_id':uuid4()}):
        with pytest.raises(ValueError, match='one browser write scope'):
            browser_application(mounted, root, api=None, admin='fixture-hq', directory=root/'evidence',
                request_id=uuid4(), supply_create_only=True, **scope)
