"""Dedicated condition uploads and completed-object proof, not physical proof.

The public file contract is paired with the formal migration, binding guards
and scoped historical download. An available upload does not authorize a correction; the atomic action must bind it exactly once and
record this snapshot in its immutable request. Historical verification must
not revalidate an old uploader's current permissions.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.foundation_models import Organization, Person
from app.models import User
from formal_file_integrity import (
    _canonical_hash, _fail, _validate_file_row, _validate_intent_metadata,
)

PURPOSE = 'return_condition_evidence'
UPLOAD_ACTIONS = {
    'provincial_manager': ('submit_return_condition', 'supplement_return_condition',
                           'review_return_condition_regional'),
    'admin': ('review_return_condition_headquarters', 'cancel_return_condition_approval'),
}


def require_upload_permission(db, actor):
    """Called with the freshly loaded, locked principal by formal_files."""
    user = db.get(User, actor.user_id, populate_existing=True)
    organization = db.scalar(select(Organization).join(
        Person, Person.organization_id == Organization.id).where(Person.id == actor.person_id)
        .execution_options(populate_existing=True))
    if (user is None or not user.is_active or organization is None or organization.status != 'active'
            or organization.org_type not in {'headquarters', 'region_company', 'department'}):
        _denied()
    for assignment in actor.assignments:
        actions = UPLOAD_ACTIONS.get(assignment.role_code, ())
        if assignment.role_code == 'provincial_manager' and assignment.scope_type == 'organization':
            try:
                identifier = UUID(assignment.scope_id)
            except (ValueError, TypeError, AttributeError):
                continue
            scope = db.get(Organization, identifier, populate_existing=True)
            if scope is None or scope.status != 'active' or scope.org_type != 'region_company':
                continue
        elif not (assignment.role_code == 'admin' and assignment.scope_type == 'national'
                  and assignment.scope_id == '*' and organization.org_type == 'headquarters'):
            continue
        for action in actions:
            if (any(e.assignment_id == assignment.assignment_id and e.role_code == assignment.role_code
                    and (e.scope_type, e.scope_id) == (assignment.scope_type, assignment.scope_id)
                    and (e.resource, e.action, e.field_code, e.effect)
                    == ('stock_operation', action, '', 'allow') for e in actor.entitlements)
                    # There is no business scope yet. Any effective deny for
                    # this action conservatively blocks this upload capability.
                    and actor.allows(db, 'stock_operation', action)):
                return
    _denied()


def _denied():
    _fail('file_purpose_forbidden', 'forbidden', '当前账号不能上传入库成色纠正证据')


@dataclass(frozen=True)
class CompletedEvidence:
    file_id: UUID
    metadata_sha256: str
    content_sha256: str
    size_bytes: int
    mime_type: str


def completed_evidence(row, *, uploader_user_id, uploader_person_id,
                       authorization_version, provider_code, recorded_at=None):
    """Validate a loaded file against exact persisted or current actor facts.

    No DB principal lookup: historic proofs deliberately use the actor/version
    saved on the original event. New actions must separately prove CURRENT
    authority and file exclusivity under locks. Supply the original event time
    for historical binding; a new preview defaults to the current time.
    OSS completion metadata is
    evidence of an object check, not proof of the photographed physical goods.
    """
    from app.foundation_models import FileObject
    if (not isinstance(row, FileObject) or not isinstance(uploader_user_id, str)
            or not uploader_user_id or type(uploader_person_id) is not UUID or not uploader_person_id.int
            or type(authorization_version) is not int or authorization_version <= 0
            or not isinstance(provider_code, str) or not provider_code):
        _fail('condition_evidence_invalid', 'precondition_failed', '纠正附件或上传者坐标无效')
    _validate_file_row(row)
    metadata = _validate_intent_metadata(row, allow_completed=True)
    if (row.status != 'available' or 'completion' not in metadata or metadata['purpose'] != PURPOSE
            or row.uploaded_by != uploader_user_id or metadata['uploader_user_id'] != uploader_user_id
            or metadata['uploader_person_id'] != str(uploader_person_id)
            or metadata['authorization_version'] != authorization_version
            or metadata['provider'] != provider_code):
        _fail('condition_evidence_unavailable', 'precondition_failed', '纠正附件尚未完成或不属于准确上传身份和用途')
    cutoff = recorded_at if recorded_at is not None else datetime.now(timezone.utc)
    created = row.created_at
    # SQLite materializes timestamptz as naive UTC; native PostgreSQL retains
    # the zone. Never coerce the caller's event boundary into a guessed zone.
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    verified = datetime.fromisoformat(metadata['completion']['verified_at'])
    if (not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None
            or not isinstance(created, datetime) or not created <= verified <= cutoff):
        _fail('condition_evidence_time_invalid', 'precondition_failed', '纠正凭证须在本次事实发生前完成上传')
    return CompletedEvidence(row.id, _canonical_hash(metadata), row.sha256, row.size_bytes, row.mime_type)
