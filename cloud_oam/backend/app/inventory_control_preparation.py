"""Owner-only preservation of control evidence; never publishes or posts stock.

The caller owns the transaction. Source/catalogue approvals, capture attestation,
normalization and publisher authorization are separate, still required facts.
No production route, worker or collector calls this module.
"""
from datetime import datetime, timezone
import hashlib
import json
import re

from sqlalchemy import select, text

from .foundation_models import Organization, SourceSystem
from .inventory_control_evidence import InventoryControlEvidenceError, _canonical, _sha, _object, _invalid_number
from .inventory_control_publication import _publication_documents
from .inventory_control_models import (InventoryControlSourceBinding as Binding, InventoryControlCatalogVersion as Catalog,
    InventoryControlCaptureChain as Capture, InventoryControlCaptureSnapshot as Snapshot, InventoryControlPreparation as Preparation)
from .models import ExternalSyncSnapshot, ExternalSyncSnapshotBatch, ExternalSyncSnapshotRecord
from .schemas import EdgeSyncSnapshotCompleteIn


def _fail(code):
    raise InventoryControlEvidenceError(code)


def _json(value):
    try:
        return json.loads(value,object_pairs_hook=_object,parse_constant=_invalid_number)
    except (ValueError,TypeError,RecursionError):
        _fail('invalid_staged_control_evidence')


def _aware(value):
    # SQLite stores the same UTC instant without its zone; user input is checked
    # by the strict pure schemas before it ever reaches these ORM comparisons.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _owner(db):
    if db.get_bind().dialect.name=='postgresql':
        if db.scalar(text('SELECT current_user'))!='star_oam_migrator':
            _fail('control_preparation_requires_schema_owner')
    elif db.get_bind().dialect.name!='sqlite':
        _fail('unsupported_control_preparation_database')


def _begin_outer(db):
    connection = db.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')


def _staging(db,documents):
    """Bind every frozen snapshot to an exact completed authenticated inbox row.

    This verifies stored ingress coordinates and content, not the new capture
    attestation or the independent catalogue's real-world completeness.
    """
    binding=documents['source_binding'];linked=[]
    for evidence in documents['capture_chain']['snapshots']:
        manifest=evidence['manifest'];rows=tuple(db.scalars(select(ExternalSyncSnapshot).where(
            ExternalSyncSnapshot.source_instance==binding['source_instance'],
            ExternalSyncSnapshot.snapshot_id==manifest['snapshot_id']).execution_options(populate_existing=True)))
        if len(rows)!=1:_fail('control_staging_snapshot_not_unique')
        source=rows[0]
        if source.status!='complete' or source.completed_at is None or any(
                getattr(source,key)!=binding[key] for key in ('source_system','source_instance','company_id','org_code','scope_key')):
            _fail('control_staging_binding_mismatch')
        if source.sync_mode!=manifest['sync_mode'] or _aware(source.snapshot_at)!=datetime.fromisoformat(manifest['snapshot_at'].replace('Z','+00:00')):
            _fail('control_staging_time_or_mode_mismatch')
        expected_manifest=EdgeSyncSnapshotCompleteIn.model_validate(manifest).model_dump(mode='json')
        try:
            staged_manifest=EdgeSyncSnapshotCompleteIn.model_validate(_json(source.manifest_json)).model_dump(mode='json')
        except ValueError:
            _fail('control_staging_manifest_mismatch')
        if staged_manifest!=expected_manifest or not re.fullmatch('[a-f0-9]{64}',source.manifest_sha256 or '') \
                or hashlib.sha256(source.manifest_json.encode('utf-8')).hexdigest()!=source.manifest_sha256:
            _fail('control_staging_manifest_mismatch')
        batches=tuple(db.scalars(select(ExternalSyncSnapshotBatch).where(
            ExternalSyncSnapshotBatch.snapshot_ref_id==source.id).execution_options(populate_existing=True)))
        expected=evidence['batches'];by_sequence={row['sequence']:row for row in expected}
        if len(batches)!=len(expected):_fail('control_staging_batches_incomplete')
        for row in batches:
            original=by_sequence.get(row.sequence)
            if original is None or row.entity_type!='inventory' or row.source_instance!=binding['source_instance'] \
                    or row.total_sequences!=len(expected) or row.record_count!=len(original['records']) or not re.fullmatch('[a-f0-9]{64}',row.body_sha256 or ''):
                _fail('control_staging_batch_mismatch')
        records=tuple(db.scalars(select(ExternalSyncSnapshotRecord).where(
            ExternalSyncSnapshotRecord.snapshot_ref_id==source.id).execution_options(populate_existing=True)))
        expected_records={row['business_key']:row for batch in expected for row in batch['records']}
        if len(records)!=len(expected_records):_fail('control_staging_records_incomplete')
        for row in records:
            original=expected_records.get(row.business_key)
            if original is None or row.entity_type!='inventory' or row.operation!=original['operation'] \
                    or _json(row.payload_json)!=original['data'] or row.payload_sha256!=_sha(original['data']):
                _fail('control_staging_record_mismatch')
            stamp=original['source_updated_at']
            if (None if row.source_updated_at is None else _aware(row.source_updated_at)) != (None if stamp is None else datetime.fromisoformat(stamp.replace('Z','+00:00'))):
                _fail('control_staging_record_time_mismatch')
        # Ingress hashes the original HTTP bytes. JSON whitespace and timestamp
        # spellings need not equal our canonical evidence document. Preserve the
        # transport hashes independently instead of silently relabelling them.
        envelope=dict(snapshot_ref_id=source.id,source_instance=source.source_instance,manifest_sha256=source.manifest_sha256,
            manifest_json=source.manifest_json,received_at=_aware(source.received_at).isoformat(),
            completed_at=_aware(source.completed_at).isoformat(),
            batches=[dict(id=row.id,batch_id=row.batch_id,sequence=row.sequence,body_sha256=row.body_sha256)
                for row in sorted(batches,key=lambda row:row.sequence)])
        linked.append((source,_sha(envelope)))
    return linked


def _documents(binding,catalog,capture,checked_at):
    return _publication_documents(expected_json=_canonical({
        'schema_version':'rsc.inventory_control_coverage.v1','binding':binding.binding_jsonb,**catalog.catalog_jsonb}),
        evidence_json=_canonical(capture.evidence_jsonb),checked_at=checked_at)


def read_inventory_control_preparation(db,*,preparation_id):
    """Reprove preserved evidence without granting opening/publication readiness."""
    evidence_types=(Binding,Catalog,Capture,Snapshot,Preparation,ExternalSyncSnapshot,ExternalSyncSnapshotBatch,ExternalSyncSnapshotRecord)
    if any(isinstance(row,evidence_types) for row in (*db.new,*db.dirty,*db.deleted)):
        _fail('control_preparation_pending_evidence')
    with db.no_autoflush:
        root=db.get(Preparation,preparation_id,populate_existing=True)
        if root is None:_fail('control_preparation_not_found')
        binding=db.get(Binding,root.binding_id,populate_existing=True)
        catalog=db.get(Catalog,root.catalog_id,populate_existing=True)
        capture=db.get(Capture,root.capture_chain_id,populate_existing=True)
        if binding is None or catalog is None or capture is None or catalog.binding_id!=binding.id or capture.catalog_id!=catalog.id:
            _fail('control_preparation_graph_mismatch')
        documents=_documents(binding,catalog,capture,_aware(root.checked_at))
        if binding.binding_sha256!=_sha(documents['source_binding']) or catalog.catalog_sha256!=_sha(documents['catalog']) \
                or capture.capture_chain_sha256!=_sha(documents['capture_chain']) or root.control_manifest_sha256!=_sha(documents['control_manifest']) \
                or root.control_manifest_jsonb!=documents['control_manifest'] or catalog.catalog_revision!=documents['catalog']['catalog_revision'] \
                or binding.target_region_code!=documents['catalog']['target_region_code']:
            _fail('control_preparation_hash_mismatch')
        links=tuple(db.scalars(select(Snapshot).where(Snapshot.capture_chain_id==capture.id).order_by(Snapshot.sequence).execution_options(populate_existing=True)))
        staging=_staging(db,documents)
        if capture.snapshot_count!=len(staging) or len(links)!=len(staging):_fail('control_preparation_links_incomplete')
        for sequence,(link,(source,staging_hash),evidence) in enumerate(zip(links,staging,documents['capture_chain']['snapshots']),1):
            if link.sequence!=sequence or link.snapshot_ref_id!=source.id or link.manifest_sha256!=source.manifest_sha256 or link.evidence_sha256!=_sha(evidence) or link.staging_sha256!=staging_hash:
                _fail('control_preparation_snapshot_mismatch')
        if _aware(root.checked_at)>_aware(root.created_at):_fail('control_preparation_time_mismatch')
        return dict(preparation_id=root.id,source_system_id=binding.source_system_id,region_org_id=binding.region_org_id,
            source_binding_sha256=binding.binding_sha256,catalog_sha256=catalog.catalog_sha256,
            capture_chain_sha256=capture.capture_chain_sha256,control_manifest_sha256=root.control_manifest_sha256,
            checked_at=_aware(root.checked_at),source_authenticated=False,catalog_authenticated=False,
            capture_attested=False,projection_published=False,start_ready=False)


def record_inventory_control_preparation(db,*,source_system_id,region_org_id,expected_json,evidence_json,checked_at):
    """Preserve a complete preparation atomically; exact content replays reuse it.

    The registered source/region are explicit inputs, never inferred from names.
    Refuse pending ORM writes so validation cannot refresh away a caller's edits.
    """
    if db.new or db.dirty or db.deleted:_fail('control_preparation_requires_clean_session')
    _begin_outer(db)
    with db.begin_nested():
        return _record(db,source_system_id=source_system_id,region_org_id=region_org_id,
            expected_json=expected_json,evidence_json=evidence_json,checked_at=checked_at)


def _record(db,*,source_system_id,region_org_id,expected_json,evidence_json,checked_at):
    _owner(db)
    documents=_publication_documents(expected_json=expected_json,evidence_json=evidence_json,checked_at=checked_at)
    source=db.get(SourceSystem,source_system_id,populate_existing=True);region=db.get(Organization,region_org_id,populate_existing=True)
    if source is None or source.code.strip().casefold()!='oam' or source.mode!='read_only' or not source.enabled \
            or region is None or region.org_type!='region_company' or region.status!='active':
        _fail('control_formal_binding_unavailable')
    staging=_staging(db,documents);now=datetime.now(timezone.utc)
    if checked_at>now:_fail('control_preparation_future_check')
    binding_hash=_sha(documents['source_binding']);catalog_doc=documents['catalog']
    if db.get_bind().dialect.name=='postgresql':
        coordinate=_sha([str(source_system_id),str(region_org_id),catalog_doc['target_region_code'],binding_hash])
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(coordinate[:16],16)-(1<<63)})
    binding=db.scalar(select(Binding).where(Binding.source_system_id==source_system_id,Binding.region_org_id==region_org_id,
        Binding.target_region_code==catalog_doc['target_region_code'],Binding.binding_sha256==binding_hash))
    if binding is None:
        binding=Binding(source_system_id=source_system_id,region_org_id=region_org_id,target_region_code=catalog_doc['target_region_code'],
            binding_jsonb=documents['source_binding'],binding_sha256=binding_hash,created_at=now);db.add(binding);db.flush()
    elif binding.binding_jsonb!=documents['source_binding']:_fail('control_binding_digest_conflict')
    catalog=db.scalar(select(Catalog).where(Catalog.binding_id==binding.id,Catalog.catalog_revision==catalog_doc['catalog_revision']))
    if catalog is None:
        catalog=Catalog(binding_id=binding.id,catalog_revision=catalog_doc['catalog_revision'],catalog_jsonb=catalog_doc,
            catalog_sha256=_sha(catalog_doc),created_at=now);db.add(catalog);db.flush()
    elif catalog.catalog_jsonb!=catalog_doc or catalog.catalog_sha256!=_sha(catalog_doc):_fail('control_catalog_revision_conflict')
    capture_hash=_sha(documents['capture_chain'])
    capture=db.scalar(select(Capture).where(Capture.catalog_id==catalog.id,Capture.capture_chain_sha256==capture_hash))
    if capture is None:
        capture=Capture(catalog_id=catalog.id,evidence_jsonb=documents['capture_chain'],capture_chain_sha256=capture_hash,
            snapshot_count=len(staging),created_at=now);db.add(capture);db.flush()
        for sequence,((staged,staging_hash),evidence) in enumerate(zip(staging,documents['capture_chain']['snapshots']),1):
            db.add(Snapshot(capture_chain_id=capture.id,sequence=sequence,snapshot_ref_id=staged.id,
                manifest_sha256=staged.manifest_sha256,evidence_sha256=_sha(evidence),staging_sha256=staging_hash,created_at=now))
        db.flush()
    elif capture.evidence_jsonb!=documents['capture_chain']:_fail('control_capture_digest_conflict')
    manifest_hash=_sha(documents['control_manifest'])
    root=db.scalar(select(Preparation).where(Preparation.capture_chain_id==capture.id,Preparation.control_manifest_sha256==manifest_hash))
    if root is None:
        root=Preparation(binding_id=binding.id,catalog_id=catalog.id,capture_chain_id=capture.id,checked_at=checked_at,
            control_manifest_jsonb=documents['control_manifest'],control_manifest_sha256=manifest_hash,created_at=now)
        db.add(root);db.flush()
    return read_inventory_control_preparation(db,preparation_id=root.id)
