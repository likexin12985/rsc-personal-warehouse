"""Local harness cannot redirect a run to a pre-existing PostgreSQL server."""
from datetime import datetime, timezone
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import local_pg16_cluster as harness


@pytest.mark.parametrize('variable', ['PGHOST', 'PGSERVICE', 'PGPORT', 'PGDATABASE', 'PGUSER'])
def test_libpq_override_is_rejected_before_any_process_or_connection(monkeypatch, variable, tmp_path):
    monkeypatch.setenv(variable, 'unexpected-existing-target')
    def forbidden(*args, **kwargs):
        pytest.fail('override must fail before touching any PostgreSQL binary')
    monkeypatch.setattr(harness, '_checked_bin', forbidden)
    with pytest.raises(ValueError, match='unconfigured libpq'):
        with harness.native_cluster(postgres_bin=tmp_path, artifact_root=tmp_path):
            pytest.fail('must not yield')
    assert not list(tmp_path.iterdir())


def test_evidence_root_outside_artifacts_is_rejected_without_starting(monkeypatch, tmp_path):
    for key in tuple(os.environ):
        if key.startswith('PG'):
            monkeypatch.delenv(key)
    monkeypatch.setattr(harness, '_checked_bin', lambda path: tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('outside artifact root must not start PostgreSQL')
    monkeypatch.setattr(harness.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='under cloud_oam/artifacts'):
        with harness.native_cluster(postgres_bin=tmp_path, artifact_root=tmp_path/'outside'):
            pytest.fail('must not yield')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('change', ['version', 'database', 'user', 'directory', 'tcp', 'started', 'pid'])
def test_server_identity_must_match_new_owned_child(tmp_path, change):
    now = datetime.now(timezone.utc)
    (tmp_path/'postmaster.pid').write_text('123\n')
    row = ['postgres', 'postgres', 160015, str(tmp_path), '', now, 'system-identifier']
    if change == 'version': row[2] = 150019
    elif change == 'database': row[0] = 'existing'
    elif change == 'user': row[1] = 'other'
    elif change == 'directory': row[3] = str(tmp_path/'other')
    elif change == 'tcp': row[4] = 'localhost'
    elif change == 'started': row[5] = now.replace(year=now.year-1)
    else: (tmp_path/'postmaster.pid').write_text('456\n')
    connection = SimpleNamespace(execute=lambda sql: SimpleNamespace(fetchone=lambda: tuple(row)))
    with pytest.raises(RuntimeError, match='identity mismatch'):
        harness._identity(connection, tmp_path, 123, now)


@pytest.mark.parametrize('script', ['run_local_pg16_material_checks.py', 'run_local_pg16_opening_fixture_checks.py', 'run_local_pg16_legacy_opening_checks.py', 'run_local_pg16_opening_recount_checks.py'])
def test_cli_accepts_no_existing_database_coordinates(script):
    import importlib.util
    path = Path(__file__).parents[2]/'scripts'/script
    spec = importlib.util.spec_from_file_location('local_material_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit) as error:
        module.main(['--postgres-bin', '/unused', '--database-url', 'postgresql://existing'])
    assert error.value.code == 2
