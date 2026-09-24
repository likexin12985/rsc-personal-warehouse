"""Synthetic 0112 proof, invoked only inside the protected disposable PG16 gate.

Actual edge-role ingress and owner-role persistence are exercised. Authentication
is an explicit VerifiedEdgeRequest test dependency; this is not source capture
attestation, inventory publication, or a real OAM/Feishu call.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import inventory_control_preparation as service
from app.foundation_models import Organization, SourceSystem
from app.inventory_control_models import InventoryControlPreparation, TABLES
from test_inventory_control_evidence import CHECK_TIME, fake_expectation, fake_records, fake_snapshot, upsert
from test_inventory_control_preparation import record, stage


def snapshot(engine):
    with engine.connect() as connection:
        return tuple(tuple(connection.scalars(text(f'SELECT to_jsonb(t) FROM public.{table} t ORDER BY to_jsonb(t)::text')))
                     for table in TABLES)


def _formal_stock(engine):
    with engine.connect() as connection:
        return tuple(tuple(connection.scalars(text(f'SELECT to_jsonb(t) FROM public.{table} t ORDER BY to_jsonb(t)::text')))
                     for table in ('sync_runs', 'inventory_transactions', 'stock_balances', 'stocktake_tasks', 'outbox_events'))


def _rejected(engine, statement, parameters=None, *, state='23514', message=None):
    with engine.connect() as connection:
        try:
            with pytest.raises(DBAPIError) as caught:
                connection.execute(text(statement), parameters or {})
                connection.commit()
            assert caught.value.orig.sqlstate == state
            if message is not None:
                assert message in str(caught.value.orig)
        finally:
            connection.rollback()


def _prepare(owner_engine):
    expected = fake_expectation()
    expected['binding']['source_instance'] = 'pg16-control-preparation'
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT session_user')) == 'star_oam_migrator'
        sources = tuple(db.scalars(select(SourceSystem).where(func.lower(func.btrim(SourceSystem.code)) == 'oam')))
        assert len(sources) == 1, 'Synthetic gate requires one pre-existing formal OAM source'
        source = sources[0]
        assert source.mode == 'read_only' and source.enabled
        headquarters = Organization(code='pg16-control-hq', name='Synthetic control HQ',
                                    org_type='headquarters', status='active')
        db.add(headquarters)
        db.flush()
        region = Organization(code='pg16-control-region', name='Synthetic control region',
                              org_type='region_company', status='active', parent_id=headquarters.id)
        db.add(region)
        db.flush()
        # Explicit synthetic inventory ingress only. The work-order projector
        # does not acquire inventory bindings, facts, or publisher authority.
        db.execute(text('''INSERT INTO public.oam_sync_scope_bindings
            (id, principal_name, capability, source_system, source_instance,
             scope_key, company_id, org_code, entity_type)
            VALUES (:id, 'edge_inbox', 'edge_ingress', :source_system,
                    :source_instance, :scope_key, :company_id, :org_code, 'inventory')'''),
            {'id': uuid4(), **expected['binding']})
        result = SimpleNamespace(expected=expected, snapshots=[], source=source.id,
                                 region=region.id, checked_at=CHECK_TIME)
        db.commit()
    return result


def assert_inventory_control_preparation_gate(owner_engine, edge_engine, api_engine,
                                             projector_engine, backup_engine, validate_runtime_security):
    prepared = _prepare(owner_engine)
    before_stock = _formal_stock(owner_engine)
    results = []
    base = fake_snapshot(prepared.expected, fake_records())
    changed = fake_records()
    changed[0]['data']['qtyStock'] = '4.000'
    delta = fake_snapshot(prepared.expected, changed, number=2, mode='incremental',
                          previous=base, changes=[upsert(changed[0])])
    zero = fake_snapshot(prepared.expected, [], number=3)
    for number, (incoming, chain) in enumerate(((base, [base]), (delta, [base, delta]), (zero, [zero]))):
        with Session(edge_engine) as db:
            assert db.scalar(text('SELECT current_user')) == 'edge_inbox'
            stage(db, prepared.expected, incoming)
        prepared.snapshots = chain
        prepared.checked_at = CHECK_TIME + timedelta(minutes=10 * number)
        with Session(owner_engine) as db:
            value = record(db, prepared)
            db.commit()
            assert record(db, prepared) == value
            db.commit()
        results.append(value)
        assert all(value[key] is False for key in (
            'source_authenticated', 'catalog_authenticated', 'capture_attested', 'projection_published', 'start_ready'))
        # The reader must work in a real database read-only transaction.
        with Session(owner_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            assert service.read_inventory_control_preparation(db, preparation_id=value['preparation_id']) == value
    saved = snapshot(owner_engine)
    assert tuple(map(len, saved)) == (1, 1, 3, 4, 3)
    assert snapshot(backup_engine) == saved
    assert _formal_stock(owner_engine) == before_stock

    # Exact table ACLs and service identity refusal are separate proofs.
    for runtime in (api_engine, edge_engine, projector_engine):
        for table in TABLES:
            _rejected(runtime, f'SELECT * FROM public.{table}', state='42501')
            _rejected(runtime, f'INSERT INTO public.{table} DEFAULT VALUES', state='42501')
        with Session(runtime) as db:
            with pytest.raises(service.InventoryControlEvidenceError, match='requires_schema_owner'):
                record(db, prepared)
    for table in TABLES:
        _rejected(backup_engine, f'INSERT INTO public.{table} DEFAULT VALUES', state='42501')
        for operation in (f'UPDATE public.{table} SET id=id', f'DELETE FROM public.{table}', f'TRUNCATE public.{table}'):
            _rejected(owner_engine, operation, message='facts are append-only')

    # A correct hash cannot turn an internally consistent preparation into a
    # published snapshot or rebind the exact graph to an unrelated owner row.
    with Session(owner_engine) as db:
        root = db.get(InventoryControlPreparation, results[0]['preparation_id'])
        manifest = deepcopy(root.control_manifest_jsonb)
    manifest['projection_published'] = True
    _rejected(owner_engine, '''INSERT INTO public.inventory_control_preparations
        (id, created_at, binding_id, catalog_id, capture_chain_id, checked_at,
         control_manifest_jsonb, control_manifest_sha256)
        SELECT :id, created_at, binding_id, catalog_id, capture_chain_id, checked_at,
               CAST(:manifest AS jsonb), :digest FROM public.inventory_control_preparations WHERE id=:original''',
        {'id': uuid4(), 'manifest': service._canonical(manifest), 'digest': service._sha(manifest),
         'original': results[0]['preparation_id']}, message='cannot confer publication')
    _rejected(owner_engine, '''INSERT INTO public.inventory_control_preparations
        (id, created_at, binding_id, catalog_id, capture_chain_id, checked_at,
         control_manifest_jsonb, control_manifest_sha256)
        SELECT :id, created_at, :wrong, catalog_id, capture_chain_id, checked_at,
               control_manifest_jsonb, control_manifest_sha256
        FROM public.inventory_control_preparations WHERE id=:original''',
        {'id': uuid4(), 'wrong': uuid4(), 'original': results[0]['preparation_id']}, message='cannot confer publication')
    _rejected(owner_engine, '''INSERT INTO public.inventory_control_capture_snapshots
        (id, created_at, capture_chain_id, sequence, snapshot_ref_id, manifest_sha256, evidence_sha256, staging_sha256)
        SELECT :id, created_at, capture_chain_id, sequence, snapshot_ref_id, manifest_sha256, evidence_sha256, staging_sha256
        FROM public.inventory_control_capture_snapshots ORDER BY id LIMIT 1''',
        {'id': uuid4()}, message='exact capture snapshot required')

    # Reversible grant drift must fail the production startup check. Revoke in
    # finally so no following gate receives altered privileges.
    from app.database_security import DatabaseSecurityBoundaryError
    table = TABLES[0]
    try:
        with owner_engine.begin() as connection:
            connection.execute(text(f'GRANT SELECT ON public.{table} TO star_oam_api'))
        with pytest.raises(DatabaseSecurityBoundaryError):
            validate_runtime_security(api_engine)
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text(f'REVOKE SELECT ON public.{table} FROM star_oam_api'))
    validate_runtime_security(api_engine)
    assert snapshot(owner_engine) == saved and _formal_stock(owner_engine) == before_stock
    print('PG16 control preparation: actual ingress, full/delta/zero, exact replay, private ACL, immutable graph; publication remains false PASS', flush=True)
