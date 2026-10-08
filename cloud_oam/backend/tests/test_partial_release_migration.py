from hashlib import sha256
import json
from pathlib import Path
import runpy
from sqlalchemy import create_engine, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from unittest.mock import patch
import pytest

ROOT=Path(__file__).resolve().parents[2]
PATH=ROOT/'backend/alembic/versions/20261219_0170_partial_reservation_release.py'


def test_forward_sources_preserve_original_guard_and_only_change_remaining_capacity():
    revision=runpy.run_path(str(PATH))
    from app.material_request_partial_release_security import DATA
    assert revision['revision']=='20261219_0170' and revision['down_revision']=='20261218_0169'
    assert len(revision['_sources']())==2
    assert revision['DATA']==DATA
    row=DATA['functions']['rsc_guard_reservation_release_binding_0070()']
    assert "request_row.outbound_status <> 'not_started'" in row['before']
    assert 'sum(picked_qty)' in row['after']
    assert 'request_row.status NOT IN' in row['after']
    assert 'm.from_account_id = original.stock_account_id' in row['after']
    assert sha256(row['after'].encode()).hexdigest()==row['afterSha256']


def test_runtime_pins_new_guard_and_readiness_without_adding_privileges():
    from app import database_security as security
    from app.material_request_partial_release_security import DATA
    coordinate=('rsc_guard_reservation_release_binding_0070','')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]==DATA['functions'][coordinate[0]+'()']['afterSha256']
    from app.material_request_remaining_cancel_readiness import DATA as current
    assert current['before']==DATA['readiness']['after']
    from app.material_request_supply_allocation_security import DATA as latest
    assert security._stock_scrap_readiness.DATA['after']['prosrc']==latest['functions']['rsc_oam_runtime_binding_ready_0044()']['after']


def test_sqlite_tooling_downgrade_refuses_late_release_history():
    revision=runpy.run_path(str(PATH))
    with create_engine('sqlite://').begin() as db:
        db.execute(text('CREATE TABLE stock_reservation_releases(request_id TEXT,request_version INTEGER)'))
        db.execute(text('CREATE TABLE stock_reservation_picks(request_id TEXT,request_version INTEGER)'))
        db.execute(text("INSERT INTO stock_reservation_picks VALUES ('r',3)"))
        db.execute(text("INSERT INTO stock_reservation_releases VALUES ('r',4)"))
        revision['downgrade'].__globals__['op']=Operations(MigrationContext.configure(db))
        with patch.object(revision['context'],'is_offline_mode',return_value=False):
            with pytest.raises(ValueError,match='partial release facts exist'):revision['downgrade']()
        assert db.scalar(text('SELECT count(*) FROM stock_reservation_releases'))==1
