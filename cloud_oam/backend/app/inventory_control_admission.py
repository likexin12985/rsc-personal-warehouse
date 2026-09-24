"""Locked source/catalogue authorization for an authenticated capture chain.

This is a current observation, not a reusable publication capability. The caller
owns the transaction and must revalidate in the eventual publisher transaction.
No inventory, SyncRun, authorization, audit or preparation fact is written here.
"""
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, text

from . import inventory_control_authority as authority
from . import inventory_control_attestation as attestation
from . import inventory_control_preparation as preparation
from .foundation_models import FileObject, Organization, SourceSystem
from .inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from .inventory_control_models import InventoryControlPreparation as Preparation
from .inventory_control_models import InventoryControlSourceBinding as Binding
from .inventory_control_models import InventoryControlCaptureChain as Chain


class ControlAdmissionError(RuntimeError):
    pass


def _fail(code):
    raise ControlAdmissionError('control_admission_' + code)


def _owner(db):
    if db.new or db.dirty or db.deleted:
        _fail('requires_clean_session')
    dialect = db.get_bind().dialect.name
    if dialect == 'postgresql':
        if db.execute(text('SELECT current_user, session_user')).one() != ('star_oam_migrator', 'star_oam_migrator'):
            _fail('requires_direct_owner')
        # An old repeatable-read snapshot can hide a newly committed revocation
        # even after locking the unchanged binding row. Refuse it explicitly.
        if db.scalar(text('SHOW transaction_isolation')) != 'read committed':
            _fail('requires_read_committed')
    elif dialect != 'sqlite':
        _fail('unsupported_database')


def _pair_at(rows, catalog_id, revoked, instant):
    def active(row):
        return row.id not in revoked and preparation._aware(row.valid_from) <= instant \
            and (row.valid_to is None or instant < preparation._aware(row.valid_to))

    sources = [row for row in rows if row.action == 'source_grant' and active(row)]
    if len(sources) != 1:
        _fail('source_authority_missing' if not sources else 'source_authority_ambiguous')
    source = sources[0]
    catalogues = [row for row in rows if row.action == 'catalog_grant' and row.catalog_id == catalog_id
                  and row.source_grant_id == source.id and active(row)]
    if len(catalogues) != 1:
        _fail('catalog_authority_missing' if not catalogues else 'catalog_authority_ambiguous')
    return source, catalogues[0]


def _proof_pair(pair):
    source, catalog = pair
    return dict(source_grant_id=str(source.id), source_grant_sha256=source.payload_sha256,
                catalog_grant_id=str(catalog.id), catalog_grant_sha256=catalog.payload_sha256)


def _coverage(rows, catalog_id, revoked, started, completed):
    """Cover the whole closed capture interval, including renewal boundaries.

    Grant ends are exclusive. A grant starting exactly at capture completion
    contributes a final inclusive point, so no expiry endpoint is overlooked.
    """
    if completed < started:
        _fail('capture_interval_invalid')
    boundaries = {started, completed}
    for row in rows:
        if row.action == 'source_grant' or (row.action == 'catalog_grant' and row.catalog_id == catalog_id):
            for value in (row.valid_from, row.valid_to):
                if value is not None and started < preparation._aware(value) < completed:
                    boundaries.add(preparation._aware(value))
    points = sorted(boundaries)
    spans, used = [], {}
    for number, instant in enumerate(points):
        pair = _pair_at(rows, catalog_id, revoked, instant)
        used.update({row.id: row for row in pair})
        spans.append(dict(started_at=instant.isoformat(),
                          completed_at=points[min(number + 1, len(points) - 1)].isoformat(),
                          end_inclusive=number == len(points) - 1, **_proof_pair(pair)))
    return spans, used


def inspect_inventory_control_admission(db, *, preparation_id):
    """Hold exact binding/source/region/file locks through caller completion.

    Retrospective grants cannot bless earlier captures. Explicitly revoked
    grants cannot qualify a new publication even if a replacement is now active;
    natural, uninterrupted renewal may cover different parts of one capture.
    """
    _owner(db)
    if not isinstance(preparation_id, UUID) or preparation_id.int == 0:
        _fail('invalid_preparation_id')
    preparation._begin_outer(db)
    prepared = preparation.read_inventory_control_preparation(db, preparation_id=preparation_id)
    root = db.get(Preparation, preparation_id, populate_existing=True)
    binding = db.scalar(select(Binding).where(Binding.id == root.binding_id).with_for_update()
                        .execution_options(populate_existing=True))
    if binding is None:
        _fail('binding_missing')
    # All authorization writers take this same binding lock. Immutable rows do
    # not need extra row locks; mutable source/file status does.
    for model, identifier in ((SourceSystem, binding.source_system_id), (Organization, binding.region_org_id)):
        db.scalar(select(model.id).where(model.id == identifier).with_for_update(read=True))
    if not authority._binding_available(db, binding):
        _fail('binding_unavailable')
    rows = tuple(db.scalars(select(Decision).where(Decision.binding_id == binding.id)
                           .order_by(Decision.created_at, Decision.id).execution_options(populate_existing=True)))
    for row in rows:
        authority._prove(db, row)
    revoked = {row.revoked_grant_id for row in rows if row.action == 'revoke'}
    chain = db.get(Chain, root.capture_chain_id, populate_existing=True)
    captures, used = [], {}
    for evidence in chain.evidence_jsonb['snapshots']:
        started = min(attestation.instant(w['started_at']) for w in evidence['warehouses'])
        completed = max(attestation.instant(w['completed_at']) for w in evidence['warehouses'])
        spans, grants = _coverage(rows, root.catalog_id, revoked, started, completed)
        used.update(grants)
        captures.append(dict(snapshot_id=evidence['manifest']['snapshot_id'], authorization_spans=spans))
    now = authority._now(db)
    current = _pair_at(rows, root.catalog_id, revoked, now)
    used.update({row.id: row for row in current})
    files = sorted({row.evidence_file_id for row in used.values()}, key=str)
    for identifier in files:
        db.scalar(select(FileObject.id).where(FileObject.id == identifier).with_for_update(read=True))
    for row in used.values():
        if authority._file(db, row.evidence_file_id, row.evidence_sha256) != row.payload_jsonb['evidence_file']:
            _fail('evidence_file_changed')
    # Reprove HMAC receipts and current freshness after all potentially waiting
    # locks; no stored boolean from preparation or a prior observation is used.
    capture = attestation.observe_inventory_control_attestation(db, preparation_id=preparation_id)
    finished = authority._now(db)
    if _pair_at(rows, root.catalog_id, revoked, finished) != current:
        _fail('authority_changed_while_waiting')
    last_started = min(attestation.instant(w['started_at']) for w in chain.evidence_jsonb['snapshots'][-1]['warehouses'])
    valid_until = min([last_started + timedelta(minutes=45)] +
                      [preparation._aware(row.valid_to) for row in current if row.valid_to is not None])
    if finished >= valid_until:
        _fail('expired_while_waiting')
    document = dict(schema_version='rsc.inventory_control_admission_observation.v1', preparation_id=str(preparation_id),
                    source_binding_sha256=prepared['source_binding_sha256'], catalog_sha256=prepared['catalog_sha256'],
                    capture_chain_sha256=prepared['capture_chain_sha256'], control_manifest_sha256=prepared['control_manifest_sha256'],
                    authority_cursor_sha256=preparation._sha([[str(row.id), row.payload_sha256] for row in rows]),
                    attestation_cursor_sha256=capture['attestation_cursor_sha256'], captures=captures,
                    current_authority=_proof_pair(current), checked_at=finished.isoformat(), valid_until=valid_until.isoformat())
    return dict(observation=document, observation_sha256=preparation._sha(document),
                source_authorized=True, catalog_authorized=True, capture_authorized=True,
                source_authenticated=True, capture_attested=True, trusted_key_lifecycle_verified=False,
                projection_published=False, start_ready=False)
