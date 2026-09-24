"""Real local ingress and SQL-created preparation facts; no external source."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.database import Base
from app.foundation_models import Organization, SourceSystem, OutboxEvent
from app import inventory_control_preparation as service
from app.inventory_control_models import InventoryControlPreparation as Preparation, InventoryControlCatalogVersion as Catalog, TABLES
from app.models import ExternalSyncSnapshot,ExternalSyncSnapshotBatch,ExternalSyncSnapshotRecord
from app.routers.integrations import receive_snapshot_batch,complete_snapshot,VerifiedEdgeRequest
from app.schemas import EdgeSyncSnapshotBatchIn,EdgeSyncSnapshotCompleteIn
from test_edge_sync_safety import request
from test_inventory_control_evidence import (fake_expectation,fake_records,fake_snapshot,canonical,SCHEMA,CHECK_TIME,BASE_TIME,upsert)

PATH=Path(__file__).parents[1]/'alembic/versions/20261022_0112_inventory_control_preparation_facts.py'


@pytest.fixture
def db():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    @sa.event.listens_for(engine,'connect')
    def foreign_keys(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine,tables=[table for table in Base.metadata.tables.values() if table.name not in TABLES])
    with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(PATH))['upgrade']()
    with Session(engine) as db:yield db
    engine.dispose()


def stage(db,expected,snapshot):
    """The real receiver parses and completes synthetic full/incremental data.

    Authentication itself is outside this fixture: VerifiedEdgeRequest is an
    explicit synthetic dependency. Original HTTP-byte hashes are deliberately
    different from canonical JSON hashes, matching the accepted wire protocol.
    """
    for number,body in enumerate(snapshot['batches'],1):
        raw=('  '+canonical(body)+'\n').encode()
        verified=VerifiedEdgeRequest(source_instance=expected['binding']['source_instance'],
            batch_id=snapshot['manifest']['snapshot_id']+f'-batch-{number}',body=raw,body_sha256=hashlib.sha256(raw).hexdigest())
        result=receive_snapshot_batch(EdgeSyncSnapshotBatchIn.model_validate(body),request(),verified,db)
        assert result['ok']
    raw=('\n'+canonical(snapshot['manifest'])+' ').encode()
    verified=VerifiedEdgeRequest(source_instance=expected['binding']['source_instance'],
        batch_id=snapshot['manifest']['snapshot_id']+'-complete',body=raw,body_sha256=hashlib.sha256(raw).hexdigest())
    result=complete_snapshot(EdgeSyncSnapshotCompleteIn.model_validate(snapshot['manifest']),request(),verified,db)
    assert result['status']=='complete'


@pytest.fixture
def prepared(db):
    source=SourceSystem(code='oam',name='Synthetic control source',mode='read_only',enabled=True)
    region=Organization(code='synthetic-region',name='Synthetic region',org_type='region_company',status='active')
    db.add_all([source,region]);db.commit()
    expected=fake_expectation();snapshot=fake_snapshot(expected,fake_records());stage(db,expected,snapshot)
    return SimpleNamespace(expected=expected,snapshots=[snapshot],source=source.id,region=region.id,checked_at=CHECK_TIME)


def record(db,prepared):
    return service.record_inventory_control_preparation(db,source_system_id=prepared.source,region_org_id=prepared.region,
        expected_json=canonical(prepared.expected),evidence_json=canonical({'schema_version':SCHEMA,'snapshots':prepared.snapshots}),checked_at=prepared.checked_at)


def facts(db):
    return tuple(tuple(db.execute(sa.text(f'SELECT * FROM {name} ORDER BY id'))) for name in TABLES)


@pytest.mark.parametrize('mode',['full','incremental','zero'])
def test_actual_ingress_to_preserved_proof_replays_without_inventory_or_publication(db,prepared,mode):
    if mode=='incremental':
        records=fake_records();records[0]['data']['qtyStock']='4.000'
        snapshot=fake_snapshot(prepared.expected,records,number=2,mode='incremental',previous=prepared.snapshots[0],changes=[upsert(records[0])])
        stage(db,prepared.expected,snapshot);prepared.snapshots.append(snapshot);prepared.checked_at=CHECK_TIME+timedelta(minutes=10)
    elif mode=='zero':
        snapshot=fake_snapshot(prepared.expected,[],number=2);stage(db,prepared.expected,snapshot)
        prepared.snapshots=[snapshot];prepared.checked_at=CHECK_TIME+timedelta(minutes=10)
    before=tuple(db.scalar(sa.text(f'SELECT count(*) FROM {name}')) for name in ('sync_runs','inventory_transactions','stock_balances','stocktake_tasks','outbox_events'))
    value=record(db,prepared);db.commit();saved=facts(db)
    assert record(db,prepared)==value;db.commit();assert saved==facts(db)
    assert service.read_inventory_control_preparation(db,preparation_id=value['preparation_id'])==value
    assert all(value[key] is False for key in ('source_authenticated','catalog_authenticated','capture_attested','projection_published','start_ready'))
    assert before==tuple(db.scalar(sa.text(f'SELECT count(*) FROM {name}')) for name in ('sync_runs','inventory_transactions','stock_balances','stocktake_tasks','outbox_events'))
    assert db.scalar(sa.text('SELECT count(*) FROM inventory_control_capture_snapshots'))==len(prepared.snapshots)


@pytest.mark.parametrize('change',['source','region','manifest','manifest_digest','batch','record','missing_batch','missing_record','capture'])
def test_wrong_or_incomplete_staging_never_leaves_preparation_rows(db,prepared,change):
    if change=='source':prepared.source=uuid4()
    elif change=='region':prepared.region=uuid4()
    elif change=='capture':prepared.snapshots[0]['warehouses'].pop()
    else:
        if change=='manifest':db.scalar(sa.select(ExternalSyncSnapshot)).manifest_json='{}'
        elif change=='manifest_digest':db.scalar(sa.select(ExternalSyncSnapshot)).manifest_sha256='a'*64
        elif change=='batch':db.scalar(sa.select(ExternalSyncSnapshotBatch)).record_count=999
        elif change=='record':db.scalar(sa.select(ExternalSyncSnapshotRecord)).payload_json='{}'
        elif change=='missing_batch':db.delete(db.scalar(sa.select(ExternalSyncSnapshotBatch)))
        else:db.delete(db.scalar(sa.select(ExternalSyncSnapshotRecord)))
        db.commit()
    before=facts(db)
    with pytest.raises(service.InventoryControlEvidenceError):record(db,prepared)
    db.commit();assert facts(db)==before


def test_catalogue_revision_is_immutable_and_conflict_rolls_back(db,prepared):
    record(db,prepared);db.commit();before=facts(db)
    # Preserve the evidence shape and add an independently declared empty
    # position; reusing the same directory revision for new content is refused.
    prepared.expected['warehouses'][0]['positions'].append({'position_code':'fake-new-zero','region_code':'fake-region-A'})
    with pytest.raises(service.InventoryControlEvidenceError,match='catalog_revision_conflict'):record(db,prepared)
    db.commit();assert facts(db)==before


def test_all_facts_reject_edits_and_populated_downgrade(db,prepared):
    record(db,prepared);db.commit();before=facts(db)
    for table in TABLES:
        for statement in (f'UPDATE {table} SET id=id',f'DELETE FROM {table}'):
            with pytest.raises(sa.exc.IntegrityError,match='append-only'),db.begin_nested():db.execute(sa.text(statement))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='facts must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


def test_reader_has_no_autoflush_and_refuses_changed_transport_digest(db,prepared):
    value=record(db,prepared);db.commit()
    pending=OutboxEvent(event_type='synthetic',aggregate_type='test',aggregate_id=uuid4().hex,payload_jsonb={},idempotency_key=uuid4().hex)
    db.add(pending);statements=[];connection=db.connection()
    def capture(_c,_cu,sql,*_):statements.append(sql.strip().split()[0])
    sa.event.listen(connection,'before_cursor_execute',capture)
    try:assert service.read_inventory_control_preparation(db,preparation_id=value['preparation_id'])==value
    finally:sa.event.remove(connection,'before_cursor_execute',capture)
    assert pending in db.new and set(statements)=={'SELECT'};db.rollback()
    db.scalar(sa.select(ExternalSyncSnapshotBatch)).body_sha256='a'*64;db.commit()
    with pytest.raises(service.InventoryControlEvidenceError,match='snapshot_mismatch'):
        service.read_inventory_control_preparation(db,preparation_id=value['preparation_id'])


def test_final_reproof_failure_rolls_back_all_new_facts_even_if_caller_commits(db,prepared,monkeypatch):
    def broken(*args,**kwargs):raise service.InventoryControlEvidenceError('synthetic_final_failure')
    monkeypatch.setattr(service,'read_inventory_control_preparation',broken)
    with pytest.raises(service.InventoryControlEvidenceError,match='synthetic_final_failure'):record(db,prepared)
    db.commit();assert all(not rows for rows in facts(db))


def test_caller_rollback_removes_successful_preparation(db,prepared):
    before=facts(db);record(db,prepared);db.rollback()
    assert facts(db)==before


def test_pending_caller_edits_are_preserved_and_not_refreshed_or_flushed(db,prepared):
    source=db.get(SourceSystem,prepared.source);source.name='Uncommitted owner edit'
    with pytest.raises(service.InventoryControlEvidenceError,match='clean_session'):record(db,prepared)
    assert source in db.dirty and source.name=='Uncommitted owner edit'


def test_explicit_legacy_oam_source_uses_opening_semantics_without_renaming(db,prepared):
    source=db.get(SourceSystem,prepared.source);source.code='OAM';db.commit()
    value=record(db,prepared);db.commit()
    assert value['source_system_id']==source.id and source.code=='OAM'


@pytest.mark.parametrize('kind',['disabled_source','inactive_region','wrong_source'])
def test_unavailable_formal_binding_never_creates_facts(db,prepared,kind):
    if kind=='disabled_source':db.get(SourceSystem,prepared.source).enabled=False
    elif kind=='inactive_region':db.get(Organization,prepared.region).status='inactive'
    else:db.get(SourceSystem,prepared.source).code='different-source'
    db.commit()
    with pytest.raises(service.InventoryControlEvidenceError,match='formal_binding_unavailable'):record(db,prepared)
    db.commit();assert all(not rows for rows in facts(db))


@pytest.mark.parametrize('kind',['fact','staging'])
def test_read_preserves_pending_evidence_instead_of_refreshing_it(db,prepared,kind):
    value=record(db,prepared);db.commit()
    row=db.get(Preparation,value['preparation_id']) if kind=='fact' else db.scalar(sa.select(ExternalSyncSnapshot))
    field='control_manifest_sha256' if kind=='fact' else 'manifest_sha256'
    setattr(row,field,'f'*64)
    with pytest.raises(service.InventoryControlEvidenceError,match='pending_evidence'):
        service.read_inventory_control_preparation(db,preparation_id=value['preparation_id'])
    assert row in db.dirty and getattr(row,field)=='f'*64
