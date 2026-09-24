"""Current, authenticated input plan for the independent control publisher.

Resolve every aggregate origin to an immutable capture link, staging record and
reviewed material line. No current-record cache or caller-supplied raw rows are
accepted. This is a read/lock scope, not a persisted publication or permission to
write a SyncRun. The publisher must rebuild it inside its own atomic transaction.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from . import inventory_control_admission as admission
from . import inventory_control_authority as authority
from . import inventory_control_configuration as configuration
from . import inventory_control_normalization as normalization
from . import inventory_control_preparation as preparation
from .inventory_control_evidence import _record, _sha
from .inventory_control_models import InventoryControlCaptureSnapshot as CaptureSnapshot
from .material_projection_models import MaterialProjectionLine
from .models import ExternalSyncSnapshotRecord


class ControlProjectionPlanError(RuntimeError):
    pass


def _fail(code):
    raise ControlProjectionPlanError('control_projection_plan_' + code)


class ControlProjectionPlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    preparation_id: UUID
    preparation_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    mapping_decision_id: UUID

    @model_validator(mode='after')
    def nonzero_identifiers(self):
        if not self.preparation_id.int or not self.mapping_decision_id.int:
            raise ValueError('exact preparation and approved mapping required')
        return self


def _latest_origins(db, root, chain):
    """Rebuild the exact last upsert pointer, retaining unchanged delta origins."""
    links = tuple(db.scalars(select(CaptureSnapshot).where(CaptureSnapshot.capture_chain_id == chain.id)
        .order_by(CaptureSnapshot.sequence).execution_options(populate_existing=True)))
    if len(links) != len(chain.evidence_jsonb['snapshots']):
        _fail('capture_links_incomplete')
    latest, transport = {}, []
    for sequence, (link, capture) in enumerate(zip(links, chain.evidence_jsonb['snapshots'], strict=True), 1):
        if link.sequence != sequence:
            _fail('capture_link_sequence')
        staged = tuple(db.scalars(select(ExternalSyncSnapshotRecord).where(
            ExternalSyncSnapshotRecord.snapshot_ref_id == link.snapshot_ref_id,
            ExternalSyncSnapshotRecord.entity_type == 'inventory').execution_options(populate_existing=True)))
        by_key = {row.business_key: row for row in staged}
        changes = [row for batch in capture['batches'] for row in batch['records']]
        if len(by_key) != len(staged) or len(staged) != len(changes):
            _fail('staging_set_mismatch')
        if capture['manifest']['sync_mode'] == 'full':
            latest = {}
        for change in changes:
            key = change['business_key']
            row = by_key.get(key)
            if row is None or row.operation != change['operation'] \
                    or row.payload_sha256 != _sha(change['data']) or preparation._json(row.payload_json) != change['data']:
                _fail('staging_record_mismatch')
            stored_time = preparation._aware(row.source_updated_at) if row.source_updated_at else None
            source_time = preparation._aware(datetime.fromisoformat(change['source_updated_at'].replace('Z', '+00:00'))) \
                if change['source_updated_at'] is not None else None
            if stored_time != source_time:
                _fail('staging_record_time_mismatch')
            if change['operation'] == 'delete':
                if key not in latest:
                    _fail('delete_origin_missing')
                del latest[key]
            else:
                latest[key] = (deepcopy(_record(change, delta=True)), row, link)
        transport.append(dict(capture_snapshot_id=str(link.id), sequence=sequence,
            snapshot_ref_id=link.snapshot_ref_id, snapshot_id=capture['manifest']['snapshot_id'],
            manifest_sha256=link.manifest_sha256, evidence_sha256=link.evidence_sha256,
            staging_sha256=link.staging_sha256))
    return latest, transport


def _basis(result):
    """Exclude observation clocks only; keep every identity, hash and deadline.

    The original clock-bearing observations are returned separately. This new
    schema does not relabel their original hashes as hashes of modified content.
    """
    authorization = deepcopy(result['observation'])
    authorization.pop('checked_at')
    normalized = deepcopy(result['normalization_review'])
    normalized.pop('checked_at')
    normalized.pop('admission_observation_sha256')
    return dict(schema_version='rsc.inventory_control_publication_basis.v1',
        authorization_evidence=authorization, authorization_evidence_sha256=_sha(authorization),
        normalization_evidence=normalized, normalization_evidence_sha256=_sha(normalized))


def _build(db, request, result):
    review = result['normalization_review']
    if not all(result[name] is True for name in ('source_authorized', 'catalog_authorized',
            'capture_authorized', 'source_authenticated', 'capture_attested', 'normalization_rules_authorized')) \
            or not review['normalization_complete'] or review['blocked_record_count']:
        _fail('normalization_blocked')
    if result['master_source_evidence_required'] and not result['master_source_evidence_verified']:
        _fail('material_source_unverified')
    root, binding, catalog, chain, _, records, targets, _ = normalization._inputs(
        db, request.preparation_id, authority._now(db))
    if root.control_manifest_sha256 != request.preparation_sha256:
        _fail('preparation_mismatch')
    latest, transport = _latest_origins(db, root, chain)
    if tuple(latest[key][0] for key in sorted(latest)) != records:
        _fail('reconstruction_mismatch')
    row_reviews = {row['external_business_key']: row for row in review['rows']}
    target_keys = {key for key, (raw, _, _) in latest.items()
                   if (raw['data']['warehouseCode'], raw['data']['positionCode']) in targets}
    if set(row_reviews) != target_keys or len(row_reviews) != review['target_record_count']:
        _fail('target_origins_incomplete')
    lines, used = [], set()
    material_proofs = review['master_source_evidence']['materials']
    for sequence, group in enumerate(review['candidate_groups'], 1):
        origins = []
        for origin in group['origins']:
            key = origin['external_business_key']
            if key not in target_keys or key in used:
                _fail('origin_missing_or_duplicated')
            used.add(key)
            raw, staged, link = latest[key]
            row = row_reviews[key]
            proof = material_proofs.get(raw['data']['materialCode'])
            if proof is None or row['issues'] or _sha(raw) != origin['raw_record_sha256'] \
                    or origin['raw_record_sha256'] != row['raw_record_sha256'] \
                    or row['material_id'] != group['payload']['material_id'] \
                    or row['condition_code'] != group['payload']['condition_code']:
                _fail('origin_evidence_mismatch')
            material_line = db.get(MaterialProjectionLine, UUID(proof['line_id']), populate_existing=True)
            if material_line is None or material_line.payload_sha256 != proof['line_sha256'] \
                    or str(material_line.material_id) != row['material_id'] \
                    or str(material_line.policy_id) != row['policy_id']:
                _fail('material_line_mismatch')
            origins.append(dict(external_business_key=key, raw_record_sha256=origin['raw_record_sha256'],
                capture_snapshot_id=str(link.id), capture_sequence=link.sequence,
                snapshot_ref_id=link.snapshot_ref_id, staging_record_id=staged.id,
                staging_payload_sha256=staged.payload_sha256, material_line_id=str(material_line.id),
                material_line_sha256=material_line.payload_sha256, material_version_id=str(material_line.version_id),
                material_publication_id=str(material_line.publication_id), policy_id=str(material_line.policy_id),
                warehouse_code=row['warehouse_code'], position_code=row['position_code'],
                control_qty=row['control_qty'], locked_qty=row['locked_qty'], source_updated_at=row['source_updated_at']))
        # Source update time for an aggregate is unknown when its origins do not
        # prove one common known instant. Capture/publication times never fill it.
        with localcontext() as decimal_context:
            decimal_context.prec = 40
            total = sum((Decimal(origin['control_qty']) for origin in origins), Decimal(0))
        expected_payload = normalization.opening_control_projection_payload(
            external_business_key='control:' + _sha([str(binding.region_org_id), group['payload']['material_id'],
                                                      group['payload']['condition_code']]),
            region_org_id=binding.region_org_id, material_id=group['payload']['material_id'],
            condition_code=group['payload']['condition_code'], control_qty=total,
            mapping_status='resolved', mapping_note='')
        if not origins or group['payload'] != expected_payload or group['payload_sha256'] != _sha(expected_payload):
            _fail('aggregate_mismatch')
        stamps = {preparation._aware(datetime.fromisoformat(row['source_updated_at'].replace('Z', '+00:00')))
                  if row['source_updated_at'] is not None else None for row in origins}
        stamp = next(iter(stamps)) if len(stamps) == 1 else None
        lines.append(dict(sequence=sequence, payload=deepcopy(group['payload']), payload_sha256=group['payload_sha256'],
            source_updated_at=stamp.isoformat() if stamp is not None else None,
            origins=origins, origins_sha256=_sha(origins)))
    if used != target_keys:
        _fail('unlinked_target_origin')
    basis = _basis(result)
    return dict(schema_version='rsc.inventory_control_projection_plan.v1', preparation_id=str(root.id),
        preparation_sha256=root.control_manifest_sha256, binding_id=str(binding.id), catalog_id=str(catalog.id),
        capture_chain_id=str(chain.id), source_system_id=str(binding.source_system_id), region_org_id=str(binding.region_org_id),
        scope_key='oam_inventory_control:region:' + str(binding.region_org_id),
        mapping_decision_id=str(request.mapping_decision_id), capture_chain_sha256=chain.capture_chain_sha256,
        transport=transport, transport_sha256=_sha(transport), basis=basis, basis_sha256=_sha(basis),
        target_record_count=len(target_keys), non_target_record_count=review['non_target_record_count'],
        line_count=len(lines), lines=lines, lines_sha256=_sha(lines), valid_until=result['normalization_valid_until'])


def inspect_inventory_control_projection_plan(db, *, access_token, expected_authorization_version, request):
    """Current HQ-only preflight; roll back to release locks after reading it."""
    admission._owner(db)
    if not isinstance(request, ControlProjectionPlanRequest):
        _fail('invalid_request')
    request = ControlProjectionPlanRequest.model_validate(request.model_dump())
    preparation._begin_outer(db)
    claims = configuration._claims(access_token, configuration.get_settings())
    principal, session, claims, settings = configuration._operator_context(db, claims, expected_authorization_version)
    args = dict(preparation_id=request.preparation_id, mapping_decision_id=request.mapping_decision_id)
    observed = normalization.inspect_inventory_control_normalization(db, **args)
    plan = _build(db, request, observed)
    # Reconstruct after origin reads, then revalidate the operator and earliest
    # evidence deadline. A plan is never a cached publication capability.
    current = normalization.inspect_inventory_control_normalization(db, **args)
    if _build(db, request, current) != plan:
        _fail('evidence_changed')
    configuration._finish(db, principal, session, claims, settings)
    if authority._now(db) >= preparation._aware(datetime.fromisoformat(plan['valid_until'])):
        _fail('expired')
    document = dict(actor_user_id=principal.user_id, actor_person_id=str(principal.person_id),
        actor_authorization_version=principal.authorization_version, plan=plan)
    return dict(review=document, review_sha256=_sha(document),
        checked_at=current['normalization_review']['checked_at'],
        admission_observation_sha256=current['observation_sha256'],
        normalization_observation_sha256=current['normalization_review_sha256'],
        recorded=False, write_authorized=False, projection_published=False, start_ready=False)
