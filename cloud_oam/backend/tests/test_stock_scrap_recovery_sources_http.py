"""HTTP discovery is scoped read, never a request retry or inventory write."""
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy.exc import OperationalError
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_sources
from test_stock_scrap_http_adapter import harness, public


@pytest.mark.parametrize('stage', ['apply','regional','headquarters','execute'])
def test_queue_only_needs_read_and_passes_exact_stage_and_cursor(harness, monkeypatch, stage):
    h = harness; h.permissions.intersection_update({'read'}); cursor = uuid4(); calls = []
    def queue(db, *, actor, stage, after_id, limit):
        calls.append((db, actor, stage, after_id, limit))
        return dict(schema_version='1.0', person_id=h.principal.person_id, authorization_version=1,
            requested_stage=stage, queried_at=datetime.now(timezone.utc), items=(), next_after_id=None)
    monkeypatch.setattr(recovery_sources, 'queue', queue)
    body = public(h.client.get('/scraps/recovery/sources', params=dict(stage=stage, after_id=str(cursor), limit=3)), 200)
    assert body['items'] == [] and body['requested_stage'] == stage
    assert calls == [(h.db, h.principal, stage, cursor, 3)]
    assert h.calls == ['read'] and h.db.commits == h.db.rollbacks == 0


@pytest.mark.parametrize('path', ['/scraps/recovery/sources', '/scraps/recovery/sources/'+str(uuid4())])
def test_read_revocation_prevents_any_source_service_call(harness, monkeypatch, path):
    h = harness; h.permissions.discard('read')
    def forbidden(*args, **kwargs): raise AssertionError('unauthorized source called')
    monkeypatch.setattr(recovery_sources, 'read', forbidden); monkeypatch.setattr(recovery_sources, 'queue', forbidden)
    assert h.client.get(path, params={'stage':'apply'}).status_code == 403
    assert h.db.commits == h.db.rollbacks == 0


@pytest.mark.parametrize('failure', ['database','invalid-output','scope'])
def test_failed_source_never_returns_private_data_or_an_empty_success(harness, monkeypatch, failure):
    def queue(*args, **kwargs):
        if failure == 'database': raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        if failure == 'scope': raise InventoryReadError(code='scope_denied',message='当前范围不可读',status_code=403)
        return {'command_jsonb': 'PRIVATE-ORIGINAL'}
    monkeypatch.setattr(recovery_sources, 'queue', queue)
    reply = harness.client.get('/scraps/recovery/sources',params={'stage':'regional'})
    public(reply, 403 if failure == 'scope' else 503)
    assert 'items' not in reply.json() and harness.db.commits == harness.db.rollbacks == 0


@pytest.mark.parametrize('params', [{'stage':'invented'}, {'stage':'apply','limit':0}, {'stage':'apply','limit':11}, {'stage':'apply','after_id':'wrong'}])
def test_invalid_page_never_reaches_reader(harness, monkeypatch, params):
    def forbidden(*args, **kwargs): raise AssertionError('invalid page called')
    monkeypatch.setattr(recovery_sources, 'queue', forbidden)
    assert harness.client.get('/scraps/recovery/sources',params=params).status_code == 422
