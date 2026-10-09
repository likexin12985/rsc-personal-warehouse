"""Focused admission checks; mocks do not substitute for the PG16 gate."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_registration_advances_contact_guards_and_shared_readiness_together():
    import json
    from app import database_security, oam_sync_scope_security
    from app.material_request_contact_envelope_security import DATA
    from app.daily_reconciliation.capture_provisioning import REQUIRED_HEAD

    ready = database_security._stock_scrap_readiness.DATA
    assert ready['revision'] == DATA['revision'] == REQUIRED_HEAD == '20261230_0181'
    assert ready['after'] == DATA['readiness']['after']
    frozen = json.loads((ROOT / 'alembic/contact_envelope_0181/catalog.json').read_text())
    # Runtime preserves its predecessor list order; the real catalog probe
    # sorts ACL entries. Compare every entry and every other metadata field.
    for side in ('before', 'after'):
        runtime = deepcopy(DATA['readiness'][side])
        observed = deepcopy(frozen['catalog']['functions'][side]['rsc_oam_runtime_binding_ready_0044()'])
        for row in (runtime, observed):
            row['acl'] = sorted(row['acl'], key=lambda grant: (grant['grantee'], grant['privilege'], grant['grantable']))
        assert runtime == observed
    signature = 'rsc_oam_runtime_binding_ready_0044()'
    assert oam_sync_scope_security.OAM_SYNC_FUNCTION_MANIFEST[signature][-1] == ready['afterSha256']
    for signature, row in DATA['functions'].items():
        assert database_security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[
            (signature[:-2], '')] == row['afterSha256']


class Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class AdmissionConnection:
    """Only admission reads/locks are accepted; any mutation fails the test."""
    def __init__(self):
        self.identity = ('star_oam_migrator', 'star_oam_migrator', 160004, 'read committed')
        self.flags = (False,) * 5
        self.heads = ['20261229_0180']
        self.statements = []

    def execute(self, query):
        sql = str(query)
        self.statements.append(sql)
        if sql.startswith('SELECT current_user,session_user,'):
            return Result(self.identity)
        if sql.startswith('SELECT rolsuper,rolcreatedb,'):
            return Result(self.flags)
        if sql.startswith('SELECT version_num FROM public.alembic_version'):
            return Result(self.heads)
        if sql.startswith('LOCK TABLE public.alembic_version,'):
            return Result(None)
        raise AssertionError('Unexpected SQL before admission: ' + sql)


@pytest.mark.parametrize('case', ('wrong-current-role', 'impersonated-session', 'pg15',
    'repeatable-read', 'elevated-role', 'wrong-head', 'multiple-heads', 'catalog-drift'))
def test_postgres_preflight_rejects_identity_head_or_catalog_drift_before_mutation(monkeypatch, case):
    path = ROOT / 'alembic/versions/20261230_0181_material_request_contact_v2.py'
    spec = importlib.util.spec_from_file_location('contact_admission_0181', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    db = AdmissionConnection()
    error = 'direct PostgreSQL16 read-committed migrator'
    if case in ('wrong-current-role', 'impersonated-session', 'pg15', 'repeatable-read'):
        index, value = {'wrong-current-role': (0, 'star_oam_api'),
            'impersonated-session': (1, 'postgres'), 'pg15': (2, 150012),
            'repeatable-read': (3, 'repeatable read')}[case]
        values = list(db.identity)
        values[index] = value
        db.identity = tuple(values)
    elif case == 'elevated-role':
        db.flags = (False, False, False, False, True)
        error = 'unprivileged migrator'
    elif case in ('wrong-head', 'multiple-heads'):
        db.heads = ['20261228_0179'] if case == 'wrong-head' else ['20261229_0180'] * 2
        error = 'exact predecessor required'
    else:
        error = 'exact predecessor catalog required'
    snapshots = []

    def load_probe(path):
        assert Path(path).name == 'catalog_probe.py', 'Replacement helper must not load'

        def snapshot(connection, **kwargs):
            assert case == 'catalog-drift'
            snapshots.append(kwargs)
            catalog = deepcopy(migration.DATA['catalog'])
            catalog['tables']['material_requests']['owner'] = 'unexpected_owner'
            return dict(tables=catalog['tables'], functions=catalog['functions']['before'])

        return dict(snapshot=snapshot)

    monkeypatch.setattr(migration.runpy, 'run_path', load_probe)
    with pytest.raises(ValueError, match=error):
        migration._postgresql(db, True)
    assert len(snapshots) == int(case == 'catalog-drift')
    assert all(sql.startswith(('SELECT ', 'LOCK TABLE ')) for sql in db.statements)
