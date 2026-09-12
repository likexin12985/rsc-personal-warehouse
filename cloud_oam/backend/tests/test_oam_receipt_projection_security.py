from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from app import oam_receipt_projection_security as security
from app.oam_projection_security import OamProjectionDatabaseBoundaryError
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST, RECEIPT_RLS_SIGNATURE


def test_receipt_boundary_is_noop_for_non_postgresql_engines() -> None:
    security.verify_oam_receipt_projection_database_boundary(
        SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )


@pytest.mark.parametrize('ready', [True, False, None, 1])
def test_receipt_worker_requires_exact_read_write_pair_after_catalog_proof(monkeypatch, ready):
    events = []
    @contextmanager
    def connect():
        events.append('binding_query')
        yield SimpleNamespace(scalar=lambda statement: ready)
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=connect)
    monkeypatch.setattr(security, 'verify_oam_projection_database_boundary',
                        lambda *args, **kwargs: events.append('catalog_proof'))
    if ready is True:
        security.verify_oam_receipt_projection_database_boundary(engine)
    else:
        with pytest.raises(security.OamReceiptProjectionDatabaseBoundaryError, match='read_write_pair'):
            security.verify_oam_receipt_projection_database_boundary(engine)
    assert events == ['catalog_proof', 'binding_query']


def test_receipt_worker_never_executes_an_unverified_helper(monkeypatch):
    def reject(*args, **kwargs):
        raise OamProjectionDatabaseBoundaryError('catalog drift')
    monkeypatch.setattr(security, 'verify_oam_projection_database_boundary', reject)
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    with pytest.raises(OamProjectionDatabaseBoundaryError, match='catalog drift'):
        security.verify_oam_receipt_projection_database_boundary(engine)


def test_receipt_function_manifest_pins_the_complete_migration_body():
    path = Path(__file__).parents[1] / 'alembic/versions/20260922_0082_oam_receipt_projector_boundary.py'
    migration = runpy.run_path(str(path))
    body = migration['_RECEIPT_RLS_CHECK_SQL'].split('AS $$', 1)[1].split('$$;', 1)[0]
    assert OAM_SYNC_FUNCTION_MANIFEST[RECEIPT_RLS_SIGNATURE] == (
        False, 's', 'plpgsql', 'boolean', False, 'u', hashlib.sha256(body.encode()).hexdigest(),
    )
