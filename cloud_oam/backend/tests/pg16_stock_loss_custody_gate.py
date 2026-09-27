"""0151 direct COMMIT custody checks on an already owned synthetic PG16 fixture."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from migration_script_cache import cache_migration_compilation

from app.formal_access import load_formal_principal
from app.inventory_models import CustodyAssignment
from app.stock_loss_schemas import StockLossDispositionExecuteIn
from app.formal_services import stock_loss_disposition_plan as plan
from app.formal_services import stock_loss_disposition_commands as commands
from app.formal_services import stock_loss_disposition_facts as facts
from app.formal_services.inventory_query import InventoryReadError


def assert_custody_uniqueness(context, *, request, preview):
    from pg16_stock_loss_disposition_gate import snapshot
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        other = load_formal_principal(db, context['admin_id']).person_id
        assert other != context['person_id']
        now = datetime.now(timezone.utc)
        overlap = CustodyAssignment(location_id=context['location_id'], custodian_person_id=other,
            valid_from=now, valid_to=now + timedelta(hours=1))
        db.add(overlap)
        db.commit()
        overlap_id = overlap.id
    before = snapshot(owner)
    with Session(api) as db:
        with pytest.raises(InventoryReadError) as caught:
            plan.preview_disposition(db, actor=load_formal_principal(db, context['admin_id']), request=request)
        assert caught.value.code == 'stock_loss_disposition_custody_changed'
    assert snapshot(owner) == before
    command = StockLossDispositionExecuteIn(**request.model_dump(), expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    with Session(api) as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        # Deliberately bypass only application preview/readback; leave every
        # production trigger, role, posting and audit operation intact.
        with patch.object(plan, 'preview_disposition', return_value=preview), patch.object(facts, 'verified', return_value=None):
            commands.execute_disposition(db, actor=load_formal_principal(db, context['admin_id']), request=command)
        with pytest.raises(DBAPIError, match='0150 current disposition custody or material changed') as refused:
            db.commit()
        assert refused.value.orig.sqlstate == '23514'
        db.rollback()
    assert snapshot(owner) == before
    with Session(owner) as db:
        db.get(CustodyAssignment, overlap_id).valid_to = datetime.now(timezone.utc)
        future = CustodyAssignment(location_id=context['location_id'], custodian_person_id=other,
            valid_from=datetime.now(timezone.utc) + timedelta(days=1),
            valid_to=datetime.now(timezone.utc) + timedelta(days=2))
        db.add(future)
        db.commit()
    with Session(api) as db:
        current = plan.preview_disposition(db, actor=load_formal_principal(db, context['admin_id']), request=request)
        assert current['plan_hash'] == preview['plan_hash']
    assert snapshot(owner) == before
    assert_current_custody_write_is_serialized(context, request=request, preview=preview)
    print('PG16 disposition ' + context['tracking'] + ': whole-location custody; raw API COMMIT refused with full rollback; expired/future intervals allowed PASS', flush=True)


def assert_original_retention(owner):
    """The new guard must not erase independent evidence for the 0150 guard."""
    from pg16_stock_loss_disposition_gate import snapshot
    folder = Path(__file__).parents[1] / 'alembic/versions'
    with cache_migration_compilation(folder):
        migration = runpy.run_path(str(folder / '20261129_0150_stock_loss_disposition.py'))
    before = snapshot(owner)
    def catalog():
        with owner.connect() as db:
            return tuple(db.execute(text("SELECT p.oid,p.prosrc,p.proowner,p.proacl::text,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY p.oid")))
    original = catalog()
    with owner.connect() as db:
        transaction = db.begin()
        try:
            with Operations.context(MigrationContext.configure(db)):
                with pytest.raises(DBAPIError, match='0150 immutable disposition history requires retention'):
                    migration['downgrade']()
        finally:
            transaction.rollback()
    assert snapshot(owner) == before and catalog() == original


def assert_current_custody_write_is_serialized(context, *, request, preview):
    from pg16_stock_loss_disposition_gate import snapshot
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    before = snapshot(owner)
    command = StockLossDispositionExecuteIn(**request.model_dump(), expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        result = commands.execute_disposition(db, actor=actor, request=command)
        assert result['status'] == 'posted'
        with Session(owner) as competing:
            competing.execute(text("SET LOCAL lock_timeout = '250ms'"))
            competing.execute(text("SET LOCAL statement_timeout = '2s'"))
            now = datetime.now(timezone.utc)
            competing.add(CustodyAssignment(location_id=context['location_id'],
                custodian_person_id=actor.person_id, valid_from=now, valid_to=now + timedelta(hours=1)))
            with pytest.raises(DBAPIError) as waiting:
                competing.flush()
            assert waiting.value.orig.sqlstate == '55P03'
            competing.rollback()
        # This probe proves the real API command's source-location lock. Leave
        # posting to the normal successful scenario below, with an exact key.
        db.rollback()
    assert snapshot(owner) == before
    print('PG16 disposition ' + context['tracking'] + ': second connection custody insertion blocked by actual posting lock, both rolled back PASS', flush=True)
