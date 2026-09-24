"""Actual PG16 material ingress concurrency, RLS, revocation and retention gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import SourceSystem
from app.material_capture_models import MaterialCaptureBinding as Binding
from app import material_capture_ingress as service
from app.routers import integrations
from test_material_capture_ingress import SOURCE, KEY, SECRET, bundle, packet, proof
from pg16_inventory_control_preparation_gate import _formal_stock, _rejected


def snapshot(engine):
    with engine.connect() as c:
        return tuple(c.scalars(text('SELECT to_jsonb(t) FROM public.oam_material_capture_receipts t ORDER BY id')))


def assert_material_capture_gate(owner_engine, edge_engine, api_engine, projector_engine, backup_engine,
                                 validate_runtime_security, verify_edge_boundary):
    with owner_engine.connect() as c:
        assert int(c.scalar(text('SHOW server_version_num')))//10000==16
    stock_before=_formal_stock(owner_engine)
    def register(key):
        with Session(owner_engine) as db:
            source=db.scalars(select(SourceSystem).where(SourceSystem.code=='oam')).one()
            now=db.scalar(text('SELECT clock_timestamp()'))
            row=Binding(source_system_id=source.id,source_instance=SOURCE,key_id=key,
                key_fingerprint=service.key_fingerprint(SECRET,SOURCE),created_at=now,valid_from=now,valid_to=now+timedelta(hours=1))
            db.add(row);db.flush();identity=row.id;db.commit();return identity
    binding_id=register(KEY)
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=SOURCE,edge_material_capture_enabled=True,edge_material_capture_key_id=KEY,
        edge_sync_legacy_personnel_projection_enabled=False))
    value=bundle(now=datetime.now(timezone.utc))
    def receive():
        with Session(edge_engine) as db:
            return integrations.receive_material_master_capture(proof(packet(value)),db)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(integrations,'settings',settings)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:receive(),range(2)))
        assert len({result['receipt_id'] for result in results})==1
        assert sorted(result['duplicate'] for result in results)==[False,True]
        with Session(edge_engine) as held:
            service.handle_material_capture(held,verified=proof(packet(value,'status')),operation='status')
            with owner_engine.connect() as waiting:
                waiting.execute(text("SET LOCAL lock_timeout='200ms'"))
                with pytest.raises(DBAPIError) as blocked:
                    waiting.execute(text('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id'),{'id':binding_id})
                assert blocked.value.orig.sqlstate=='55P03'
                waiting.rollback()
            held.rollback()
        # Explicit revocation stops even correctly signed requests; a new
        # registered key can recover old receipt status without re-uploading it.
        with owner_engine.begin() as c:
            c.execute(text('UPDATE public.oam_material_capture_bindings SET revoked_at=clock_timestamp() WHERE id=:id'),{'id':binding_id})
        with Session(edge_engine) as db:
            with pytest.raises(HTTPException):integrations.read_material_master_capture_status(proof(packet(value,'status')),db)
        with edge_engine.connect() as c:
            assert c.scalar(text('SELECT count(*) FROM public.oam_material_capture_receipts'))==0
        settings.edge_material_capture_key_id=KEY+'-rotated'
        register(settings.edge_material_capture_key_id)
        with Session(edge_engine) as db:
            old=integrations.read_material_master_capture_status(proof(packet(value,'status',key=settings.edge_material_capture_key_id)),db)
            assert old['receipt_id']==results[0]['receipt_id'] and old['key_id']==KEY and not old['projection_published']
        empty=bundle([],now=datetime.now(timezone.utc))
        with Session(edge_engine) as db:
            zero=integrations.receive_material_master_capture(proof(packet(empty,key=settings.edge_material_capture_key_id)),db)
            assert zero['observed_count']==0 and zero['channel_attested'] and not zero['full_catalog_verified']
    saved=snapshot(owner_engine)
    assert len(saved)==2 and snapshot(backup_engine)==saved
    for engine in (api_engine,projector_engine):
        for table in ('oam_material_capture_bindings','oam_material_capture_receipts'):
            _rejected(engine,'SELECT * FROM public.'+table,state='42501')
        _rejected(engine,"SELECT public.rsc_oam_material_capture_visible_0116('synthetic')",state='42501')
    _rejected(edge_engine,'SELECT * FROM public.oam_material_capture_bindings',state='42501')
    _rejected(backup_engine,'INSERT INTO public.oam_material_capture_receipts DEFAULT VALUES',state='42501')
    for statement in ('UPDATE public.oam_material_capture_receipts SET key_id=key_id',
        'DELETE FROM public.oam_material_capture_receipts','TRUNCATE public.oam_material_capture_receipts'):
        _rejected(owner_engine,statement,state='23514',message='append-only')
        _rejected(edge_engine,statement,state='42501')
    _rejected(owner_engine,'UPDATE public.oam_material_capture_bindings SET key_id=key_id',state='23514')
    _rejected(owner_engine,'DELETE FROM public.oam_material_capture_bindings',state='23514')
    assert snapshot(owner_engine)==saved and _formal_stock(owner_engine)==stock_before
    verify_edge_boundary(edge_engine)
    validate_runtime_security(api_engine)
    print('PG16 material capture: HMAC, concurrent first receive, exact RLS/ACL, revocation/rotation, zero and immutable history PASS',flush=True)
