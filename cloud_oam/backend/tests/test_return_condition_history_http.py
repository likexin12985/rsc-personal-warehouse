from uuid import uuid4
import pytest
from formal_file_integrity import FormalFileError
from app.routers import formal_return_condition_history as api


@pytest.mark.parametrize('endpoint', ['history', 'receipts', 'inbox'])
@pytest.mark.parametrize('failure', ['database', 'evidence'])
def test_http_admission_redaction_and_no_commit(monkeypatch, failure, endpoint):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import OperationalError
    from app.database import get_db
    from app.dependencies import get_formal_principal
    rolled = []; calls = []; allowed = False
    db = SimpleNamespace(rollback=lambda: rolled.append(True))
    principal = SimpleNamespace(allows=lambda *args: allowed)
    failure_object = (FormalFileError('evidence_invalid', 'service_unavailable', 'PRIVATE-STORAGE')
        if failure == 'evidence' else OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-CREDENTIAL')))
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise failure_object
    monkeypatch.setattr(api.reader, {'history':'read', 'receipts':'receipt_sources', 'inbox':'inbox'}[endpoint], unavailable)
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides = {get_db: lambda: db, get_formal_principal: lambda: principal}
    with TestClient(app, raise_server_exceptions=False) as client:
        path = '/return-condition-corrections/' + endpoint + ('' if endpoint == 'inbox' else '/' + str(uuid4()))
        response = client.get(path)
        assert response.status_code == 403 and not calls and len(rolled) == 1
        allowed = True
        response = client.get(path)
        assert response.status_code == 503 and len(calls) == 1 and len(rolled) == 2
        assert 'PRIVATE' not in response.text and response.headers['Cache-Control'] == 'private, no-store'
        response = client.get('/return-condition-corrections/' + endpoint + ('?limit=not-an-id' if endpoint == 'inbox' else '/not-an-id'))
        assert response.status_code == 422 and 'not-an-id' not in response.text
