"""Real SQLite 0->0180 predecessor, independent of 0181's expected catalog.

One new private database proves the historical graph's actual trigger, exact
contact-only upgrade, rollback roundtrip and fail-closed source drift. PG16
and populated business writers keep their separate acceptance gates.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from alembic import command
from alembic.config import Config
import pytest

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / 'backend/alembic/contact_envelope_0181/catalog.json'


def snapshot(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('BEGIN')
        catalog = connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        facts = {}
        for kind, name, _, _ in catalog:
            if kind != 'table':
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.execute('SELECT * FROM ' + quoted).fetchall()
            facts[name] = (len(rows), hashlib.sha256(repr(sorted(rows, key=repr)).encode()).hexdigest())
        connection.rollback()
    return dict(catalog=catalog, facts=facts)


def test_real_0180_sqlite_upgrade_roundtrip_preserves_unrelated_catalog_and_facts(tmp_path, monkeypatch):
    database = tmp_path / 'real-predecessor.sqlite3'
    url = 'sqlite+pysqlite:///' + str(database)
    environment = {k:v for k,v in os.environ.items()
                   if not k.startswith(('OAM_', 'RSC_', 'PG', 'ALIBABA_', 'OSS_', 'AWS_'))}
    environment.update(OAM_ENVIRONMENT='test', OAM_DATABASE_URL=url,
        OAM_MIGRATION_CACHE_EXECUTION='1', PYTHONPATH=str(ROOT/'backend'))
    # Historical source executes unchanged once, with the production migration
    # runner's source-bound cache. No expected 0181 trigger creates this DB.
    result = subprocess.run([sys.executable, str(ROOT/'backend/migration_runner.py'),
        '--config', str(ROOT/'alembic.ini'), '20261229_0180'], cwd=ROOT,
        env=environment, text=True, capture_output=True, timeout=1200)
    assert result.returncode == 0, result.stderr[-12000:]
    before = snapshot(database)
    expected = json.loads(CATALOG.read_text())['sqlite_triggers']
    actual = {name:sql for kind,name,_,sql in before['catalog'] if kind == 'trigger' and name in expected}
    assert actual == {name:row['before'] for name,row in expected.items()}
    guard = actual['trg_material_requests_update_guard_0029']
    assert "c.operation IN ('reserve', 'release')" in guard
    assert "c.operation IN ('pick', 'outbound')" in guard
    assert "NEW.personal_inbound_status IS NOT OLD.personal_inbound_status" in guard
    assert "  OR NEW.outbound_status <> 'not_started'" not in guard
    monkeypatch.setenv('OAM_ENVIRONMENT', 'test')
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    config = Config(str(ROOT/'alembic.ini'))
    command.upgrade(config, '20261230_0181')
    after = snapshot(database)
    assert after['catalog'] == [(kind,name,table,expected[name]['after'] if name in expected else sql)
                               for kind,name,table,sql in before['catalog']]
    assert {k:v for k,v in after['facts'].items() if k != 'alembic_version'} == {
        k:v for k,v in before['facts'].items() if k != 'alembic_version'}
    command.downgrade(config, '20261229_0180')
    assert snapshot(database) == before
    # A same-semantics comment is still unreviewed source and must fail before
    # any trigger replacement. Do not turn exact matching into text heuristics.
    name = 'trg_material_requests_update_guard_0029'
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TRIGGER ' + name)
        connection.execute(actual[name].replace('BEFORE UPDATE', 'BEFORE /* drift */ UPDATE', 1))
    drifted = snapshot(database)
    with pytest.raises(ValueError, match='^0181 exact SQLite predecessor guard required: ' + name + '$'):
        command.upgrade(config, '20261230_0181')
    assert snapshot(database) == drifted
