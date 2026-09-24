"""Current, transaction-bound evidence for exactly the requested material SKUs.

No caller flag, legacy master row or partial source catalogue proves this set.
The owner holds the same SKU keys used by publication before acquiring source,
file or mutable master locks. Returned evidence must be re-read in any future
inventory publisher transaction; this read service publishes no inventory.
"""
from datetime import datetime
import re
from uuid import UUID

from sqlalchemy import and_, select, text

from . import inventory_control_admission as admission
from . import inventory_control_authority as authority
from . import inventory_control_preparation as preparation
from . import material_projection as publication
from . import material_source_authority as source
from .foundation_models import ExternalObject, ExternalObjectVersion, FileObject
from .formal_services.audit_chain import AuditChainError
from .inventory_models import FormalMaterial, MaterialInventoryPolicy
from .material_capture_models import MaterialCaptureBinding as Binding
from .material_projection_models import MaterialProjectionLine as Line, MaterialProjectionPublication as Publication
from .material_source_authority_models import MaterialSourceAuthorityDecision as Decision
from .material_master_capture_evidence import MaterialMasterCaptureError, SOURCE_ID


class MaterialSourceProofError(RuntimeError):
    pass


def _fail(code):
    raise MaterialSourceProofError('material_source_proof_' + code)


def lock_material_skus(db, codes):
    admission._owner(db)
    if not isinstance(codes, (tuple, list)) or any(type(code) is not str or
            not re.fullmatch(r'[A-Z0-9][A-Z0-9._/-]{0,79}', code) for code in codes):
        _fail('invalid_sku_set')
    ordered = tuple(sorted(set(codes)))
    if tuple(codes) != ordered:
        _fail('invalid_sku_set')
    preparation._begin_outer(db)
    if db.get_bind().dialect.name == 'postgresql':
        for code in ordered:
            db.execute(text('SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))'),
                       {'key': 'rsc.material-publication.sku:' + code})
    return ordered


def _publication_evidence(db, row, source_id, source_instance):
    publication._prove(db, row)
    binding, subject = source._subject(db, row.binding_id)
    if binding.source_system_id != source_id or binding.source_instance != source_instance:
        _fail('source_binding_mismatch')
    current = publication._observation(db, row.receipt_id)
    original = row.payload_jsonb['source']
    if original['subject'] != subject or original['authority_spans'] != current['authority_spans']:
        _fail('publication_source_mismatch')
    decision = db.get(Decision, UUID(original['current_decision_id']), populate_existing=True)
    source._prove(db, decision)
    if decision.binding_id != binding.id or decision.action != 'grant' or decision.payload_sha256 != original['current_decision_sha256'] \
            or not preparation._aware(decision.valid_from) <= preparation._aware(row.created_at) < preparation._aware(decision.valid_to) \
            or db.scalar(select(Decision.id).where(Decision.revoked_grant_id == decision.id)) is not None:
        _fail('publication_authority_unavailable')
    for identifier in sorted({row.evidence_file_id, decision.evidence_file_id}, key=str):
        db.scalar(select(FileObject.id).where(FileObject.id == identifier).with_for_update(read=True))
    if authority._file(db, row.evidence_file_id, row.evidence_sha256) != row.payload_jsonb['evidence_file'] \
            or authority._file(db, decision.evidence_file_id, decision.evidence_sha256) != decision.payload_jsonb['evidence_file']:
        _fail('review_file_changed')
    if authority._now(db) >= datetime.fromisoformat(current['valid_until']):
        _fail('expired_during_review')
    return dict(publication_id=str(row.id), publication_sha256=row.payload_sha256,
        publication_audit_event_id=str(row.audit_event_id), receipt_id=str(row.receipt_id),
        capture_sha256=current['capture_sha256'], binding_id=str(binding.id),
        source_admission_sha256=preparation._sha(current),
        current_authority_decision_id=current['current_decision_id'],
        current_authority_decision_sha256=current['current_decision_sha256'],
        valid_until=current['valid_until'])


def inspect_current_material_sources(db, *, source_system_id, source_instance, sku_codes):
    """Prove current publication and source approval under exact SKU locks.

Missing/invalid evidence produces explicit per-SKU failures and no partial
verified set. Driver/permission failures propagate, never masquerade as absence.
An empty target set is explicitly not_required, not a full-catalogue claim.
"""
    codes = lock_material_skus(db, sku_codes)
    if not isinstance(source_system_id, UUID) or source_system_id.int == 0 or type(source_instance) is not str \
            or not SOURCE_ID.fullmatch(source_instance):
        _fail('invalid_source_binding')
    candidates = tuple(db.execute(select(FormalMaterial, ExternalObject, Line)
        .outerjoin(ExternalObject, ExternalObject.id == FormalMaterial.external_object_id)
        .outerjoin(Line, and_(Line.material_id == FormalMaterial.id, Line.version_id == ExternalObject.current_version_id))
        .where(FormalMaterial.sku_code.in_(codes)).order_by(FormalMaterial.sku_code)
        .execution_options(populate_existing=True))) if codes else ()
    by_code = {material.sku_code:(material,obj,line) for material,obj,line in candidates}
    ids = sorted({line.publication_id for _,_,line in candidates if line is not None}, key=str)
    publications = {row.id:row for row in db.scalars(select(Publication).where(Publication.id.in_(ids))
        .execution_options(populate_existing=True))} if ids else {}
    # Source bindings precede mutable master locks, matching publication order.
    for identifier in sorted({row.binding_id for row in publications.values()}, key=str):
        db.scalar(select(Binding.id).where(Binding.id == identifier).with_for_update())
    proofs, publication_issues = {}, {}
    for identifier in ids:
        try:
            proofs[str(identifier)] = _publication_evidence(db, publications.get(identifier), source_system_id, source_instance)
        except MaterialSourceProofError as error:
            publication_issues[identifier] = str(error).removeprefix('material_source_proof_')
        except (publication.MaterialProjectionError, source.MaterialSourceAuthorityError,
                authority.ControlAuthorityError, MaterialMasterCaptureError, AuditChainError, KeyError, TypeError, ValueError, AttributeError):
            publication_issues[identifier] = 'evidence_unavailable'
    for model, identifiers in (
        (FormalMaterial, [row.id for row,_,_ in candidates]),
        (ExternalObject, [row.id for _,row,_ in candidates if row is not None]),
        (ExternalObjectVersion, [line.version_id for _,_,line in candidates if line is not None]),
        (MaterialInventoryPolicy, [line.policy_id for _,_,line in candidates if line is not None]),
    ):
        for identifier in sorted(set(identifiers), key=str):
            db.scalar(select(model.id).where(model.id == identifier).with_for_update(read=True))
    materials, issues = {}, {}
    for code in codes:
        candidate = by_code.get(code)
        if candidate is None:
            issues[code] = 'material_missing'
            continue
        material, obj, line = candidate
        if line is None:
            issues[code] = 'publication_missing'
            continue
        if line.publication_id in publication_issues:
            issues[code] = publication_issues[line.publication_id]
            continue
        try:
            publication._proof_line(db, line, current=True)
            if material.status != 'active':
                _fail('material_unavailable')
            policy = db.get(MaterialInventoryPolicy, line.policy_id, populate_existing=True)
            now = authority._now(db)
            if not preparation._aware(policy.effective_from) <= now or policy.effective_to is not None and now >= preparation._aware(policy.effective_to):
                _fail('policy_unavailable')
            materials[code] = dict(material_id=str(material.id), external_object_id=str(obj.id),
                version_id=str(line.version_id), line_id=str(line.id), line_sha256=line.payload_sha256,
                publication_id=str(line.publication_id), policy_id=str(line.policy_id),
                normalized_sha256=preparation._sha(line.payload_jsonb['normalized']), policy_sha256=preparation._sha(line.payload_jsonb['policy']))
        except (MaterialSourceProofError, publication.MaterialProjectionError):
            issues[code] = 'current_projection_unavailable'
    finished = authority._now(db)
    end = min((datetime.fromisoformat(row['valid_until']) for row in proofs.values()), default=None)
    valid_until = end.isoformat() if end else None
    if end is not None and finished >= end:
        _fail('expired_during_review')
    document = dict(schema_version='rsc.material_current_source_evidence.v1',
        source_system_id=str(source_system_id), source_instance=source_instance, requested_sku_codes=list(codes),
        status='not_required' if not codes else 'blocked' if issues else 'verified', issues=issues,
        materials=materials, publications=proofs, valid_until=valid_until, full_catalog_verified=False)
    return dict(checked_at=finished.isoformat(), evidence=document, evidence_sha256=preparation._sha(document))
