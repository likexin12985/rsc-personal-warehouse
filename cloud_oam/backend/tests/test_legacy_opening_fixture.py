"""Historical acceptance must prove old facts, not manufacture new evidence."""
import ast
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import pg16_legacy_opening_fixture as legacy


def test_archived_expected_request_matches_original_completion_hash():
    fixture = legacy.load_fixture()
    evidence = fixture['evidence']
    expected = evidence['expected_request_jsonb']
    canonical = json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    assert hashlib.sha256(canonical.encode()).hexdigest() == evidence['request_sha256']
    physical, = expected['physical_observations']
    assert physical['material_id'] is not None  # recoverable input, not a guessed historical resolution
    assert physical['serial_id'] is None and physical['counted_qty'] == '1'
    item, = evidence['expected_request_resolution_jsonb']['items']
    assert item['target_type'] == 'observation'
    assert item['serial_alias_keys'] == [physical['serial_no_raw'].lower()]
    assert fixture['sourceCommit'] == '7556de2aeb1cf64cff18758204a89f5f17f694b8'


def test_frozen_seed_has_no_later_columns_or_runtime_imports():
    fixture = legacy.load_fixture()
    for row in fixture['statements']:
        assert row['sql'].startswith(('INSERT INTO ', 'UPDATE '))
        assert not any(c in row['sql'] for c in ('request_jsonb','request_resolution_jsonb','opening_authorization_version'))
    module = ast.parse(Path(legacy.__file__).read_text())
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('app') for n in ast.walk(module))
    gate = ast.parse(Path(__file__).with_name('test_postgresql16_release_gate.py').read_text())
    seed = next(n for n in gate.body if isinstance(n,ast.FunctionDef) and n.name=='_seed_0051_observation_only_completion')
    assert not any(isinstance(n,ast.ImportFrom) and (n.module or '').startswith('app') for n in ast.walk(seed))
    assert any(isinstance(n,ast.Name) and n.id=='seed_legacy_completion' for n in ast.walk(seed))


def test_changed_frozen_evidence_is_rejected_before_database_work(monkeypatch,tmp_path):
    altered=tmp_path/'altered.json';altered.write_bytes(legacy.FIXTURE_PATH.read_bytes()+b' ')
    monkeypatch.setattr(legacy,'FIXTURE_PATH',altered)
    with pytest.raises(ValueError,match='fixture changed'):legacy.load_fixture()


def test_historical_fixture_is_not_a_sqlite_service_fallback():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    try:
        with pytest.raises(ValueError,match='isolated PostgreSQL 16'):legacy.seed_legacy_completion(engine)
    finally:engine.dispose()
