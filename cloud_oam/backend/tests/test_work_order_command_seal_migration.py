"""Seal storage is append-only, guarded against delayed commands, and pinned."""
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

MIGRATION = Path(__file__).parents[1] / 'alembic/versions/20261004_0094_work_order_command_seals.py'


def test_seal_security_manifest_pins_sql_sources_triggers_and_minimum_privileges():
    m = runpy.run_path(str(MIGRATION))
    previous = runpy.run_path(str(MIGRATION.with_name('20261003_0093_work_order_replacements.py')))
    assert m['down_revision'] == previous['revision'] and m['OLD_HASH'] == previous['NEW_HASH']
    assert OAM_SYNC_FUNCTION_MANIFEST['rsc_oam_runtime_binding_ready_0044()'][6] == m['NEW_HASH']
    for coordinate, (_, _, body) in m['FUNCTIONS'].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        assert coordinate not in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    for name, (table, _, function, kind, deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,function,'A',kind,deferred,deferred,deferred)
    assert 'work_order_command_seals' in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert 'work_order_command_seals' not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES


@pytest.mark.parametrize('operation', ['upgrade', 'downgrade'])
def test_seal_migration_parses_without_accidental_binds_and_guards_both_commit_orders(monkeypatch, operation):
    m = runpy.run_path(str(MIGRATION)); statements = []
    monkeypatch.setattr(m['op'], 'get_bind', lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    monkeypatch.setattr(m['op'], 'execute', statements.append)
    monkeypatch.setattr(m['op'], 'create_table', lambda *args, **kwargs: None)
    monkeypatch.setattr(m['op'], 'drop_table', lambda *args, **kwargs: None)
    m[operation]()
    parser = pytest.importorskip('pglast.parser')
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(('CREATE FUNCTION', 'DO ')):
            parser.parse_plpgsql_json(statement)
    assert statements[0] == 'LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE'
    assert 'work_order_material_operations' in statements[1] and 'inventory_ledger_heads' in statements[1]
    if operation == 'upgrade':
        assert sum('CREATE CONSTRAINT TRIGGER' in sql for sql in statements) == 2
        assert 'GRANT SELECT, INSERT ON TABLE public.work_order_command_seals TO star_oam_api' in statements
        body = m['CHECK_BODY']
        assert '0094 executed request cannot be sealed' in body and '0094 sealed request cannot execute' in body
        assert "decode('00', 'hex')" in body
    else:
        assert any('command seals must be retained' in sql for sql in statements)
        assert any('0094 function source, configuration or ownership drift' in sql for sql in statements)
