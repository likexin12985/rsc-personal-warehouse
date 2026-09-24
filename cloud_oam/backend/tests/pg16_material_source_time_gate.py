"""Actual PG16 nullable-time storage, runtime read-only catalog and retention."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import FormalAccessError, load_formal_principal
from app.formal_services.material_catalog import list_active_materials
from app.foundation_models import ExternalObject
from app.inventory_models import FormalMaterial, MaterialInventoryPolicy
from app.models import User
from pg16_inventory_control_preparation_gate import _formal_stock, _rejected


def assert_material_source_time_gate(owner_engine, api_engine, backup_engine):
    before = _formal_stock(owner_engine)
    identifier = uuid4()
    code = 'GATE-SOURCE-TIME-' + uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
        assert not db.scalar(text("SELECT attnotnull FROM pg_attribute WHERE attrelid='public.materials'::regclass AND attname='source_updated_at' AND NOT attisdropped"))
        # New synthetic master only; never clear a known timestamp in existing facts.
        source_id = db.scalar(select(ExternalObject.source_system_id).join(FormalMaterial, FormalMaterial.external_object_id == ExternalObject.id).order_by(FormalMaterial.id).limit(1))
        assert source_id is not None
        external = ExternalObject(source_system_id=source_id, entity_type='material', external_id=code)
        db.add(external); db.flush()
        material = FormalMaterial(id=identifier, external_object_id=external.id, sku_code=code,
            name='Synthetic source-time gate', specification='', base_unit='EA', status='active', source_updated_at=None)
        db.add(material); db.flush()
        db.add(MaterialInventoryPolicy(material_id=identifier, tracking_mode='none', quantity_scale=0,
            allow_fraction=False, effective_from=now-timedelta(minutes=1)))
        db.commit()
        actor = None
        for user_id in db.scalars(select(User.id).order_by(User.id)):
            try: candidate = load_formal_principal(db, user_id, now=now)
            except FormalAccessError: continue
            if candidate.access_mode == 'active' and candidate.allows(db, 'inventory', 'read'):
                actor = candidate; break
        assert actor is not None
    with Session(api_engine) as db:
        page = list_active_materials(db, actor=actor, query=code, limit=10, now=now)
        assert page.schema_version == '2.0' and len(page.items) == 1
        assert page.items[0].material_id == identifier and page.items[0].source_updated_at is None
        db.rollback()
    _rejected(api_engine, "UPDATE public.materials SET source_updated_at=clock_timestamp() WHERE id=:id",
        {'id':identifier}, state='42501')
    with backup_engine.connect() as db:
        assert db.execute(text('SELECT source_updated_at FROM public.materials WHERE id=:id'), {'id':identifier}).one() == (None,)
    assert _formal_stock(owner_engine) == before
    print('PG16 material source time: explicit NULL, current read-only API catalog and backup; no inventory facts changed PASS', flush=True)
    return identifier
