"""Formal material-request draft and submit transaction boundary.

This module owns only three phase-one commands: create a draft, replace an
owned draft, and submit it while freezing the exact three-stage approval
route.  It deliberately does not approve, allocate, reserve, pick, dispatch,
ship, receive, notify, reconcile, or post inventory.

The caller owns the surrounding transaction.  Public functions flush but
never commit or roll back.  A create caller must generate ``request_id``
before protecting the contact snapshot so the encrypted envelope can bind its
AAD to that exact request/person pair; the value is an internal server
coordinate and is not accepted from the public HTTP body.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Any, Final, Mapping, Sequence
import uuid

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalAction,
    ApprovalInstance,
    ApprovalRouteStepDef,
    ApprovalRouteVersion,
    ApprovalStep,
    ApprovalStepCandidate,
    MaterialRequest,
    MaterialRequestCommand,
    MaterialRequestFile,
    MaterialRequestLine,
    MaterialRequestRevision,
    OamWorkOrder,
)
from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    lock_formal_principal_graph,
    load_formal_principal,
)
from ..foundation_models import (
    FileObject,
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..inventory_models import FormalMaterial
from . import formal_files as formal_file_service
from .audit_chain import AuditChainError, append_audit_event
from .material_request_contact import (
    MaterialRequestContactProtectionError,
    validate_material_request_contact_envelope,
)
from .material_request_policy import (
    ApprovalCandidateSnapshot,
    MaterialRequestPolicyError,
    assert_approval_did_not_advance_fulfillment_axes,
    require_external_registration_admin_pool,
    require_request_status_transition,
    require_unique_regional_approver,
)


MATERIAL_REQUEST_AUDIT_STREAM: Final[str] = "material_request"
MATERIAL_REQUEST_AGGREGATE: Final[str] = "material_request"
ROUTE_CODE: Final[str] = "material_request_three_stage"
APPROVAL_MODE: Final[str] = "external_registration"
_EXPECTED_ROUTE: Final[tuple[tuple[int, str, str, str], ...]] = (
    (1, "provincial_manager", "internal", "organization"),
    (2, "admin", "internal", "national"),
    (3, "star_headquarters_approver", "external_registration", "document"),
)
_NEUTRAL_AXES: Final[dict[str, str]] = {
    "allocation_status": "not_allocated",
    "reservation_status": "not_reserved",
    "outbound_status": "not_started",
    "shipment_status": "not_started",
    "logistics_signature_status": "not_signed",
    "oam_receipt_status": "not_occurred",
    "personal_inbound_status": "not_started",
    "notification_status": "not_started",
    "reconciliation_status": "not_started",
}
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_QUANTUM: Final[Decimal] = Decimal("0.001")
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestDraftError(RuntimeError):
    """Stable, database-detail-free demand command failure."""

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class MaterialRequestDraftLineInput:
    material_id: uuid.UUID
    requested_qty: Decimal
    required_date: date | None = None
    suggested_substitute_material_id: uuid.UUID | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class MaterialRequestDraftInput:
    """Server-composed draft input; never bind this class to an HTTP body.

    Public HTTP models accept plaintext ``contact`` and ``address`` business
    fields only.  The application composition root must protect that contact,
    build ``contact_masked`` with :func:`mask_material_request_contact`, and
    then construct this internal DTO.  Consequently a client cannot inject an
    envelope or a masked read projection.
    """

    work_order_id: uuid.UUID | None
    purpose: str
    urgency: str
    expected_date: date | None
    address_snapshot: Mapping[str, Any]
    contact_envelope: Mapping[str, Any]
    contact_masked: Mapping[str, Any]
    attachment_file_ids: tuple[uuid.UUID, ...]
    lines: tuple[MaterialRequestDraftLineInput, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class MaterialRequestCreateResult:
    """Dedicated create response; never masquerades as an update result."""

    schema_version: str
    request_id: uuid.UUID
    action: str
    request_no: str
    status: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    line_ids: tuple[uuid.UUID, ...]
    state_axes: Mapping[str, str]
    idempotency_replayed: bool = False


@dataclass(frozen=True, slots=True)
class MaterialRequestDraftResult:
    request_id: uuid.UUID
    request_no: str
    status: str
    version: int
    revision_id: uuid.UUID
    revision_no: int
    line_ids: tuple[uuid.UUID, ...]
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class MaterialRequestSubmitResult:
    request_id: uuid.UUID
    request_no: str
    status: str
    version: int
    revision_id: uuid.UUID
    revision_no: int
    approval_attempt_no: int
    approval_instance_id: uuid.UUID
    approval_step_ids: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
    regional_approver_user_id: str
    headquarters_candidate_user_ids: tuple[str, ...]
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _RequesterContext:
    principal: FormalPrincipal
    person: Person
    region: Organization
    technician_grant: ScopeGrant


@dataclass(frozen=True, slots=True)
class _PreparedDraft:
    work_order_id: uuid.UUID | None
    purpose: str
    urgency: str
    expected_date: date | None
    address_snapshot: dict[str, Any]
    address_masked: dict[str, Any]
    contact_envelope: dict[str, Any]
    contact_masked: dict[str, Any]
    attachment_file_ids: tuple[uuid.UUID, ...]
    lines: tuple[MaterialRequestDraftLineInput, ...]
    note: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    policy: ApprovalCandidateSnapshot
    snapshot: dict[str, Any]


def mask_material_request_contact(*, name: str, mobile: str) -> dict[str, str]:
    """Build the only contact projection allowed on read responses.

    This helper belongs to the server composition boundary.  It intentionally
    keeps at most the first name character and four mobile digits, and always
    includes an explicit mask marker required by the 0029 database guards.
    """

    checked_name = _require_text("contact_name", name, 120, required=True)
    checked_mobile = _require_text("contact_mobile", mobile, 32, required=True)
    digits = "".join(
        character for character in checked_mobile if character in "0123456789"
    )
    if len(digits) < 6:
        _fail(
            "material_request_contact_mobile_invalid",
            "invalid_request",
            "联系人手机号格式无效",
        )
    name_masked = (
        "*" if len(checked_name) == 1 else checked_name[0] + "*" * (len(checked_name) - 1)
    )
    visible_digits = digits[-4:]
    hidden_count = max(2, min(len(digits) - len(visible_digits), 20))
    return {
        "name_masked": name_masked,
        "mobile_masked": "*" * hidden_count + visible_digits,
    }


def create_material_request_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    draft: MaterialRequestDraftInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestCreateResult:
    """Create one owned draft without committing the caller transaction."""

    return _public_boundary(
        lambda: _create_material_request_draft_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            draft=draft,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def derive_material_request_create_id(
    *,
    actor: FormalPrincipal,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
) -> uuid.UUID:
    """Derive the server-only stable create coordinate for contact AAD.

    The raw key is never persisted or returned.  The UUID uses RFC 4122
    version/variant layout bits over a domain-separated HMAC digest, so an
    untrusted client cannot predict another actor's request identifier and a
    router retry can protect the contact against the exact same request ID.
    """

    supplied = _validate_supplied_actor(actor)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    digest = hmac.new(
        secret,
        (
            "cloud_oam.material_request.create_id.v1\0"
            f"actor={supplied.user_id}\0key={raw_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    coordinate = bytearray(digest[:16])
    coordinate[6] = (coordinate[6] & 0x0F) | 0x40
    coordinate[8] = (coordinate[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(coordinate))


def amend_material_request_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    draft: MaterialRequestDraftInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestDraftResult:
    """Replace all editable fields of an owned, never-submitted draft."""

    return _public_boundary(
        lambda: _amend_material_request_draft_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_version=expected_version,
            draft=draft,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def submit_material_request(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestSubmitResult:
    """Submit one draft and freeze one exact three-stage approval attempt."""

    return _public_boundary(
        lambda: _submit_material_request_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def _public_boundary(operation):
    try:
        return operation()
    except MaterialRequestDraftError:
        raise
    except MaterialRequestPolicyError as exc:
        raise MaterialRequestDraftError(exc.code, exc.category, exc.message) from None
    except MaterialRequestContactProtectionError:
        raise MaterialRequestDraftError(
            "material_request_contact_envelope_invalid",
            "invalid_request",
            "联系人加密快照无效",
        ) from None
    except AuditChainError:
        raise MaterialRequestDraftError(
            "material_request_audit_chain_unavailable",
            "service_unavailable",
            "需求单审计链不可用，本次操作未完成",
        ) from None
    except IntegrityError:
        raise MaterialRequestDraftError(
            "material_request_concurrent_conflict",
            "conflict",
            "需求单发生并发冲突，请回滚并重新读取后再操作",
        ) from None
    except DBAPIError:
        raise MaterialRequestDraftError(
            "material_request_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次需求单操作未完成",
        ) from None


def _create_material_request_draft_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    draft: MaterialRequestDraftInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestCreateResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    derived_request_id = derive_material_request_create_id(
        actor=supplied,
        idempotency_key=raw_key,
        idempotency_hmac_secret=secret,
    )
    if request_id != derived_request_id:
        _fail(
            "material_request_create_id_invalid",
            "invalid_request",
            "需求单创建标识必须由服务端幂等坐标派生",
        )
    path = "/api/v1/material-requests"
    key_hash = _idempotency_hmac(secret, supplied.user_id, "POST", path, raw_key)
    _take_advisory_locks(db, key_hash, request_id)

    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    requester = _require_requester_context(db, supplied, "create", now)
    prepared = _validate_draft(
        db,
        draft,
        request_id=request_id,
        requester=requester,
    )
    payload_hash = _draft_request_hash(
        "create", request_id, requester, prepared, expected_version=None
    )
    replay = _load_create_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="create",
        actor=requester.principal,
    )
    if replay is not None:
        return replace(replay, idempotency_replayed=True)
    _validate_draft_references(db, prepared, requester)
    if db.get(MaterialRequest, request_id) is not None:
        _fail("material_request_id_conflict", "conflict", "需求单标识已被占用")

    request_no = _request_number(request_id, now)
    address_masked = prepared.address_masked
    row = MaterialRequest(
        id=request_id,
        request_no=request_no,
        requester_user_id=requester.principal.user_id,
        requester_person_id=requester.person.id,
        requester_org_id=requester.region.id,
        work_order_id=prepared.work_order_id,
        purpose=prepared.purpose,
        urgency=prepared.urgency,
        expected_date=prepared.expected_date,
        address_snapshot_jsonb=prepared.address_snapshot,
        address_masked_jsonb=address_masked,
        contact_snapshot_jsonb=prepared.contact_envelope,
        contact_masked_jsonb=prepared.contact_masked,
        note=prepared.note,
        approval_mode=APPROVAL_MODE,
        status="draft",
        revision_no=1,
        version=0,
        **_NEUTRAL_AXES,
        submitted_at=None,
        decided_at=None,
        withdrawn_at=None,
        cancelled_at=None,
        created_by_user_id=requester.principal.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    revision = _new_revision(
        request=row,
        revision_no=1,
        previous_revision_id=None,
        draft=prepared,
        address_masked=address_masked,
        actor_user_id=requester.principal.user_id,
        now=now,
    )
    db.add(revision)
    db.flush()
    lines = _add_draft_lines(db, row, revision, prepared.lines, now=now)
    _add_request_files(
        db,
        row,
        revision,
        prepared.attachment_file_ids,
        actor_user_id=requester.principal.user_id,
        now=now,
    )
    db.flush()
    _assert_current_projection(row, revision)

    result = _create_result(row, revision, lines)
    command = _command_fact(
        operation="create",
        request=row,
        target_version=0,
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        result=result,
        actor=requester,
        occurred_at=now,
    )
    db.add(command)
    db.add(
        _state_event(
            request=row,
            revision=revision,
            from_status=None,
            to_status="draft",
            reason="material_request_created",
            actor_user_id=requester.principal.user_id,
            key_hash=key_hash,
            suffix="create",
            occurred_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=requester.principal.user_id,
        action="material_request.create",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(row.id),
        before_jsonb=None,
        after_jsonb=_safe_request_snapshot(
            row, revision, lines, len(prepared.attachment_file_ids)
        ),
        request_id=trace_id,
        occurred_at=now,
    )
    db.flush()
    return result


def _amend_material_request_draft_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    draft: MaterialRequestDraftInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestDraftResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    version = _require_version(expected_version)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}"
    key_hash = _idempotency_hmac(secret, supplied.user_id, "PATCH", path, raw_key)
    _take_advisory_locks(db, key_hash, request_id)

    request = _lock_request(db, request_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    requester = _require_requester_context(db, supplied, "update_draft", now)
    _require_owned_request(request, requester)
    prepared = _validate_draft(
        db,
        draft,
        request_id=request_id,
        requester=requester,
    )
    payload_hash = _draft_request_hash(
        "update_draft", request_id, requester, prepared, expected_version=version
    )
    replay = _load_draft_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="update_draft",
        actor=requester.principal,
    )
    if replay is not None:
        return replace(replay, replayed=True)
    _validate_draft_references(db, prepared, requester)
    current_revision = _lock_current_revision(db, request)
    _require_editable_revision(request, current_revision, version)
    old_lines = _lock_revision_lines(db, current_revision)
    old_files = _lock_revision_files(db, current_revision)
    before = _safe_request_snapshot(
        request, current_revision, old_lines, len(old_files)
    )
    address_masked = prepared.address_masked

    if current_revision.status == "sealed":
        _require_returned_revision_source(db, request, current_revision)
        next_revision = _new_revision(
            request=request,
            revision_no=current_revision.revision_no + 1,
            previous_revision_id=current_revision.id,
            draft=prepared,
            address_masked=address_masked,
            actor_user_id=requester.principal.user_id,
            now=now,
        )
        db.add(next_revision)
        # 0029 deliberately uses (request_id, revision_no) as the current
        # coordinate.  The revision must exist before the projection advances.
        db.flush()
        _copy_revision_to_request(request, next_revision)
        request.revision_no = next_revision.revision_no
        db.flush()
    else:
        next_revision = current_revision
        if request.status == "returned":
            _require_returned_draft_chain(db, request, current_revision)
        db.execute(
            delete(MaterialRequestFile).where(
                MaterialRequestFile.revision_id == current_revision.id
            )
        )
        db.execute(
            delete(MaterialRequestLine).where(
                MaterialRequestLine.revision_id == current_revision.id
            )
        )
        _copy_prepared_to_revision(
            next_revision,
            prepared,
            address_masked=address_masked,
            now=now,
        )
        # Database guards require the revision mirror to be written before the
        # aggregate projection is changed.
        db.flush()
        _copy_revision_to_request(request, next_revision)
        db.flush()

    request.version += 1
    request.updated_at = now
    lines = _add_draft_lines(db, request, next_revision, prepared.lines, now=now)
    _add_request_files(
        db,
        request,
        next_revision,
        prepared.attachment_file_ids,
        actor_user_id=requester.principal.user_id,
        now=now,
    )
    db.flush()
    _assert_current_projection(request, next_revision)
    result = _draft_result(request, next_revision, lines)
    db.add(
        _command_fact(
            operation="update_draft",
            request=request,
            target_version=request.version,
            key_hash=key_hash,
            request_reference=path,
            request_hash=payload_hash,
            result=result,
            actor=requester,
            occurred_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=requester.principal.user_id,
        action="material_request.update_draft",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(request.id),
        before_jsonb=before,
        after_jsonb=_safe_request_snapshot(
            request, next_revision, lines, len(prepared.attachment_file_ids)
        ),
        request_id=trace_id,
        occurred_at=now,
    )
    db.flush()
    return result


def _submit_material_request_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestSubmitResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    version = _require_version(expected_version)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}/submit"
    key_hash = _idempotency_hmac(secret, supplied.user_id, "POST", path, raw_key)
    _take_advisory_locks(db, key_hash, request_id)

    request = _lock_request(db, request_id)
    preflight_at = _database_now(db)
    preflight_region_org_id = request.requester_org_id
    candidate_user_ids = _discover_candidate_user_ids(
        db,
        region_org_id=preflight_region_org_id,
        now=preflight_at,
    )
    lock_formal_principal_graph(
        db, tuple(sorted({supplied.user_id, *candidate_user_ids}))
    )
    locked_candidate_user_ids = _discover_candidate_user_ids(
        db,
        region_org_id=preflight_region_org_id,
        now=preflight_at,
    )
    if locked_candidate_user_ids != candidate_user_ids:
        _fail(
            "material_request_candidate_set_changed",
            "conflict",
            "审批候选人在提交锁定期间发生变化，请重新读取后提交",
        )
    now = _database_now(db)
    requester = _require_requester_context(db, supplied, "submit", now)
    _require_owned_request(request, requester)
    revision = _lock_current_revision(db, request)
    payload_hash = _canonical_hash(
        {
            "operation": "submit",
            "request_id": str(request_id),
            "revision_id": str(revision.id),
            "revision_no": revision.revision_no,
            "expected_version": version,
            "actor_user_id": requester.principal.user_id,
            "actor_person_id": str(requester.person.id),
            "requester_org_id": str(requester.region.id),
        }
    )
    replay = _load_submit_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        actor=requester.principal,
    )
    if replay is not None:
        return replace(replay, replayed=True)
    _require_submittable_revision(request, revision, version)
    lines = _lock_revision_lines(db, revision)
    files = _lock_revision_files(db, revision)
    manifest_files = _revalidate_persisted_draft(
        db, request, revision, lines, files, requester
    )
    route, route_steps = _require_exact_active_route(db, now)
    regional, headquarters = _resolve_locked_candidates(
        db,
        requester=requester,
        region_org_id=request.requester_org_id,
        request_id=request.id,
        now=now,
        expected_user_ids=candidate_user_ids,
    )

    before_axes = _request_axes(request)
    assert_approval_did_not_advance_fulfillment_axes(_NEUTRAL_AXES, before_axes)
    before = _safe_request_snapshot(request, revision, lines, len(files))
    source_status = request.status
    require_request_status_transition(source_status, "submitted")
    require_request_status_transition("submitted", "approval_in_progress")

    attempt_no, previous_instance = _next_approval_attempt(
        db, request=request, revision=revision
    )
    revision.content_manifest_sha256 = _revision_content_manifest(
        revision, lines, files, manifest_files
    )
    revision.status = "sealed"
    revision.sealed_at = now
    revision.sealed_by_user_id = requester.principal.user_id
    revision.updated_at = now
    # Sealing is ordered before request submission and instance creation; the
    # 0029 guard verifies both the complete manifest prerequisites and the
    # exact aggregate/revision mirror.
    db.flush()
    if previous_instance is not None:
        previous_instance.status = "superseded"
        previous_instance.current_step_no = None
        previous_instance.current_step_id = None
        previous_instance.completed_at = previous_instance.completed_at or now
        previous_instance.version += 1
        previous_instance.updated_at = now
        db.flush()

    instance = ApprovalInstance(
        id=uuid.uuid4(),
        request_id=request.id,
        request_revision_id=revision.id,
        revision_no=revision.revision_no,
        route_version_id=route.id,
        attempt_no=attempt_no,
        status="active",
        current_step_no=1,
        current_step_id=None,
        version=0,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(instance)
    # Instance attempts count request/revision submissions. Step attempts are
    # local to one approval instance and always start at one; later-step
    # returns append step attempt 2+ without changing the instance attempt.
    initial_step_attempt_no = 1
    step1 = _approval_step(
        instance=instance,
        definition=route_steps[0],
        attempt_no=initial_step_attempt_no,
        predecessor=None,
        status="open",
        assignee_user_id=regional.policy.user_id,
        snapshot=regional.snapshot,
        opened_at=now,
        now=now,
    )
    headquarters_manifest = _candidate_manifest(headquarters)
    step2 = _approval_step(
        instance=instance,
        definition=route_steps[1],
        attempt_no=initial_step_attempt_no,
        predecessor=step1,
        status="pending",
        assignee_user_id=None,
        snapshot=headquarters_manifest,
        opened_at=None,
        now=now,
    )
    step3 = _approval_step(
        instance=instance,
        definition=route_steps[2],
        attempt_no=initial_step_attempt_no,
        predecessor=step2,
        status="pending",
        assignee_user_id=None,
        snapshot={**headquarters_manifest, "approval_mode": APPROVAL_MODE},
        opened_at=None,
        now=now,
    )
    # The deferred composite FK makes the pointer exact at transaction end
    # while still allowing the instance and its first step to be inserted in
    # the same unit of work.
    instance.current_step_id = step1.id
    db.add_all((step1, step2, step3))
    db.flush()
    _add_candidate(db, step1, regional, "assignee", now)
    for candidate in headquarters:
        _add_candidate(db, step2, candidate, "assignee", now)
        _add_candidate(db, step3, candidate, "registrar", now)
        _add_candidate(db, step3, candidate, "verifier", now)

    for line in lines:
        line.status = "approval_pending"
        line.version += 1
        line.updated_at = now
    request.status = "approval_in_progress"
    if request.submitted_at is None:
        request.submitted_at = now
    request.version += 1
    request.updated_at = now
    assert_approval_did_not_advance_fulfillment_axes(before_axes, _request_axes(request))
    db.flush()
    _assert_current_projection(request, revision)

    result = MaterialRequestSubmitResult(
        request_id=request.id,
        request_no=request.request_no,
        status=request.status,
        version=request.version,
        revision_id=revision.id,
        revision_no=revision.revision_no,
        approval_attempt_no=attempt_no,
        approval_instance_id=instance.id,
        approval_step_ids=(step1.id, step2.id, step3.id),
        regional_approver_user_id=regional.policy.user_id,
        headquarters_candidate_user_ids=tuple(
            candidate.policy.user_id for candidate in headquarters
        ),
        state_axes=dict(_request_axes(request)),
    )
    command = _command_fact(
        operation="submit",
        request=request,
        target_version=request.version,
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        result=result,
        actor=requester,
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    db.add_all(
        (
            _state_event(
                request=request,
                revision=revision,
                from_status=source_status,
                to_status="submitted",
                reason="material_request_submitted",
                actor_user_id=requester.principal.user_id,
                key_hash=key_hash,
                suffix="submitted",
                occurred_at=now,
            ),
            _state_event(
                request=request,
                revision=revision,
                from_status="submitted",
                to_status="approval_in_progress",
                reason="regional_approval_opened",
                actor_user_id=requester.principal.user_id,
                key_hash=key_hash,
                suffix="approval-in-progress",
                occurred_at=now,
            ),
            ApprovalAction(
                id=uuid.uuid4(),
                instance_id=instance.id,
                step_id=step1.id,
                command_id=command.id,
                action="submit",
                actor_user_id=requester.principal.user_id,
                actor_person_id=requester.person.id,
                actor_role_assignment_id=requester.technician_grant.assignment_id,
                authorization_version=requester.principal.authorization_version,
                source_mode="internal",
                comment="",
                occurred_at=now,
                created_at=now,
            ),
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=requester.principal.user_id,
        action="material_request.submit",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_request_snapshot(request, revision, lines, len(files)),
            "approval_instance_id": str(instance.id),
            "approval_attempt_no": attempt_no,
            "route_version_id": str(route.id),
            "route_version": route.version,
            "regional_candidate_count": 1,
            "headquarters_candidate_count": len(headquarters),
            "candidate_manifest_sha256": headquarters_manifest[
                "candidate_manifest_sha256"
            ],
        },
        request_id=trace_id,
        occurred_at=now,
    )
    db.flush()
    return result


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "需求单必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("material_request_actor_inactive", "forbidden", "当前账号或人员不可提交需求")
    return actor


def _require_requester_context(
    db: Session,
    supplied: FormalPrincipal,
    action: str,
    now: datetime,
) -> _RequesterContext:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail(
            "material_request_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取后再操作",
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "material_request_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再操作",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("material_request_actor_inactive", "forbidden", "当前账号或人员不可提交需求")
    person = db.scalar(
        select(Person)
        .where(Person.id == current.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None or person.employment_status != "active":
        _fail("material_request_requester_invalid", "forbidden", "申请人当前不可用")
    region = _derive_active_region(db, person.organization_id)
    grants = tuple(
        grant
        for grant in current.assignments
        if grant.role_code == "technician"
        and grant.scope_type == "person"
        and _same_uuid(grant.scope_id, person.id)
    )
    eligible = tuple(
        grant
        for grant in grants
        if _selected_grant_allows(
            db,
            current,
            grant,
            "material_request",
            action,
            target_scope_type="person",
            target_scope_id=str(person.id),
        )
    )
    if len(eligible) != 1:
        _fail(
            "material_request_self_scope_forbidden",
            "forbidden",
            "当前人员没有唯一且覆盖本人的工程师需求权限",
        )
    return _RequesterContext(current, person, region, eligible[0])


def _derive_active_region(db: Session, organization_id: uuid.UUID) -> Organization:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail(
                "material_request_organization_cycle",
                "service_unavailable",
                "申请人组织树存在循环引用",
            )
        seen.add(current_id)
        organization = db.scalar(
            select(Organization)
            .where(Organization.id == current_id)
            .execution_options(populate_existing=True)
        )
        if organization is None or organization.status != "active":
            _fail(
                "material_request_region_unavailable",
                "precondition_failed",
                "申请人没有当前有效的所属区域",
            )
        if organization.org_type == "region_company":
            return organization
        if organization.org_type not in {"department", "headquarters"}:
            break
        current_id = organization.parent_id
    _fail(
        "material_request_region_unavailable",
        "precondition_failed",
        "申请人没有当前有效的所属区域",
    )


def _selected_grant_allows(
    db: Session,
    principal: FormalPrincipal,
    grant: ScopeGrant,
    resource: str,
    action: str,
    *,
    target_scope_type: str,
    target_scope_id: str,
    field_code: str = "",
) -> bool:
    selected = replace(
        principal,
        assignments=(grant,),
        entitlements=tuple(
            row for row in principal.entitlements if row.assignment_id == grant.assignment_id
        ),
    )
    try:
        return principal.allows(
            db,
            resource,
            action,
            field_code=field_code,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        ) and selected.allows(
            db,
            resource,
            action,
            field_code=field_code,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        )
    except FormalAccessError:
        _fail(
            "material_request_scope_graph_invalid",
            "forbidden",
            "需求单权限范围图无效",
        )


def _validate_draft(
    db: Session,
    value: MaterialRequestDraftInput,
    *,
    request_id: uuid.UUID,
    requester: _RequesterContext,
) -> _PreparedDraft:
    if not isinstance(value, MaterialRequestDraftInput):
        _fail("material_request_draft_invalid", "invalid_request", "需求草稿格式无效")
    purpose = _require_text("purpose", value.purpose, 4000, required=True)
    note = _require_text("note", value.note, 10000, required=False)
    if value.urgency not in {"normal", "urgent", "emergency"}:
        _fail("material_request_urgency_invalid", "invalid_request", "紧急程度无效")
    if value.expected_date is not None and not isinstance(value.expected_date, date):
        _fail("material_request_expected_date_invalid", "invalid_request", "期望日期无效")
    address = _validate_address(value.address_snapshot)
    contact = validate_material_request_contact_envelope(value.contact_envelope)
    contact_masked = _validate_contact_masked(value.contact_masked)
    expected_aad = hashlib.sha256(
        (
            "cloud_oam.material_request.contact.envelope.v1\0"
            f"request_id={request_id}\0requester_person_id={requester.person.id}"
        ).encode("ascii")
    ).hexdigest()
    if not hmac.compare_digest(contact["aad_sha256"], expected_aad):
        _fail(
            "material_request_contact_binding_invalid",
            "invalid_request",
            "联系人加密快照未绑定当前需求单和申请人",
        )
    attachments = _validate_uuid_tuple(
        "attachment_file_ids", value.attachment_file_ids, maximum=20
    )
    if not isinstance(value.lines, tuple) or not 1 <= len(value.lines) <= 200:
        _fail("material_request_lines_required", "invalid_request", "需求明细数量无效")
    lines: list[MaterialRequestDraftLineInput] = []
    dimensions: set[tuple[uuid.UUID, date | None, uuid.UUID | None]] = set()
    for item in value.lines:
        if not isinstance(item, MaterialRequestDraftLineInput):
            _fail("material_request_line_invalid", "invalid_request", "需求明细格式无效")
        material_id = _require_uuid("material_id", item.material_id)
        substitute_id = (
            _require_uuid("suggested_substitute_material_id", item.suggested_substitute_material_id)
            if item.suggested_substitute_material_id is not None
            else None
        )
        if substitute_id == material_id:
            _fail("material_request_substitute_same", "invalid_request", "建议替代料不能等于原物料")
        quantity = _require_quantity(item.requested_qty)
        if item.required_date is not None and not isinstance(item.required_date, date):
            _fail("material_request_required_date_invalid", "invalid_request", "明细需求日期无效")
        line_note = _require_text("line_note", item.note, 2000, required=False)
        dimension = (material_id, item.required_date, substitute_id)
        if dimension in dimensions:
            _fail("material_request_line_duplicate", "invalid_request", "需求明细维度重复")
        dimensions.add(dimension)
        lines.append(
            MaterialRequestDraftLineInput(
                material_id=material_id,
                requested_qty=quantity,
                required_date=item.required_date,
                suggested_substitute_material_id=substitute_id,
                note=line_note,
            )
        )
    work_order_id = (
        _require_uuid("work_order_id", value.work_order_id)
        if value.work_order_id is not None
        else None
    )
    return _PreparedDraft(
        work_order_id=work_order_id,
        purpose=purpose,
        urgency=value.urgency,
        expected_date=value.expected_date,
        address_snapshot=address,
        address_masked=_masked_address_projection(address),
        contact_envelope=contact,
        contact_masked=contact_masked,
        attachment_file_ids=attachments,
        lines=tuple(lines),
        note=note,
    )


def _validate_draft_references(
    db: Session,
    draft: _PreparedDraft,
    requester: _RequesterContext,
) -> None:
    """Validate mutable references only after an idempotent replay misses."""

    _require_available_files(
        db, draft.attachment_file_ids, requester.principal.user_id
    )
    material_ids = {
        identifier
        for line in draft.lines
        for identifier in (line.material_id, line.suggested_substitute_material_id)
        if identifier is not None
    }
    _require_active_materials(db, material_ids)
    if draft.work_order_id is not None:
        _require_work_order(db, draft.work_order_id, requester)


def _revalidate_persisted_draft(
    db: Session,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    lines: Sequence[MaterialRequestLine],
    files: Sequence[MaterialRequestFile],
    requester: _RequesterContext,
) -> tuple[FileObject, ...]:
    if not lines:
        _fail("material_request_lines_required", "precondition_failed", "需求单没有有效明细")
    if tuple(line.line_no for line in lines) != tuple(range(1, len(lines) + 1)):
        _fail(
            "material_request_line_sequence_invalid",
            "precondition_failed",
            "需求明细行号不连续",
        )
    if any(
        row.purpose != "request_attachment" or row.request_line_id is not None
        for row in files
    ):
        _fail(
            "material_request_file_binding_invalid",
            "precondition_failed",
            "需求附件绑定范围无效",
        )
    _assert_current_projection(request, revision)
    validate_material_request_contact_envelope(revision.contact_snapshot_jsonb)
    expected_aad = hashlib.sha256(
        (
            "cloud_oam.material_request.contact.envelope.v1\0"
            f"request_id={request.id}\0requester_person_id={requester.person.id}"
        ).encode("ascii")
    ).hexdigest()
    if not hmac.compare_digest(
        revision.contact_snapshot_jsonb.get("aad_sha256", ""), expected_aad
    ):
        _fail(
            "material_request_contact_binding_invalid",
            "precondition_failed",
            "联系人加密快照未绑定当前需求单和申请人",
        )
    _validate_address(revision.address_snapshot_jsonb)
    if revision.address_masked_jsonb != _masked_address_projection(
        revision.address_snapshot_jsonb
    ):
        _fail(
            "material_request_address_projection_invalid",
            "precondition_failed",
            "需求单地址脱敏投影不一致",
        )
    _validate_contact_masked(revision.contact_masked_jsonb)
    material_ids = {
        identifier
        for line in lines
        for identifier in (line.material_id, line.suggested_substitute_material_id)
        if identifier is not None
    }
    _require_active_materials(db, material_ids)
    manifest_files = _require_available_files(
        db, tuple(row.file_id for row in files), requester.principal.user_id
    )
    if revision.work_order_id is not None:
        _require_work_order(db, revision.work_order_id, requester)
    for line in lines:
        _require_quantity(line.requested_qty)
        if line.status != "draft" or line.final_approved_qty != Decimal("0.000") or line.cancelled_qty != Decimal("0.000"):
            _fail(
                "material_request_line_state_invalid",
                "precondition_failed",
                "需求明细已存在非草稿或审批数量事实",
            )
    return manifest_files


def _validate_address(value: Mapping[str, Any]) -> dict[str, str]:
    fields = {
        "province_code": 12,
        "province_name": 80,
        "city_name": 80,
        "district_name": 80,
        "detail": 500,
    }
    if not isinstance(value, Mapping) or set(value) != set(fields):
        _fail("material_request_address_invalid", "invalid_request", "收货地址快照格式无效")
    return {
        field: _require_text(field, value[field], limit, required=True)
        for field, limit in fields.items()
    }


def _masked_address_projection(address: Mapping[str, Any]) -> dict[str, str]:
    checked = _validate_address(address)
    # Detail text is never copied into the read projection.  A fixed mask is
    # both deterministic and less revealing than preserving address length.
    return {
        "province_code": checked["province_code"],
        "province_name": checked["province_name"],
        "city_name": checked["city_name"],
        "district_name": checked["district_name"],
        "detail_masked": "******",
    }


def _validate_contact_masked(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "name_masked",
        "mobile_masked",
    }:
        _fail(
            "material_request_contact_masked_invalid",
            "invalid_request",
            "联系人脱敏投影格式无效",
        )
    result = {
        "name_masked": _require_text(
            "contact_name_masked", value["name_masked"], 120, required=True
        ),
        "mobile_masked": _require_text(
            "contact_mobile_masked", value["mobile_masked"], 32, required=True
        ),
    }
    if not any(marker in result["name_masked"] for marker in ("*", "＊", "•")):
        _fail(
            "material_request_contact_masked_invalid",
            "invalid_request",
            "联系人姓名必须脱敏",
        )
    if not any(marker in result["mobile_masked"] for marker in ("*", "＊", "•")):
        _fail(
            "material_request_contact_masked_invalid",
            "invalid_request",
            "联系人手机号必须脱敏",
        )
    if re.search(r"[0-9]{7}", result["mobile_masked"]):
        _fail(
            "material_request_contact_masked_invalid",
            "invalid_request",
            "联系人手机号脱敏不足",
        )
    return result


def _require_work_order(
    db: Session,
    value: uuid.UUID,
    requester: _RequesterContext,
) -> uuid.UUID:
    work_order_id = _require_uuid("work_order_id", value)
    row = db.scalar(
        select(OamWorkOrder)
        .where(OamWorkOrder.id == work_order_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        _fail("material_request_work_order_not_found", "not_found", "OAM 工单引用不存在")
    if row.status not in {"pending", "active"}:
        _fail("material_request_work_order_inactive", "precondition_failed", "OAM 工单当前不可用于新需求")
    if row.engineer_person_id != requester.person.id:
        _fail("material_request_work_order_forbidden", "forbidden", "只能引用本人当前有效工单")
    if not _organization_descends_from(db, row.organization_id, requester.region.id):
        _fail("material_request_work_order_scope_mismatch", "forbidden", "工单组织不属于申请人区域")
    return row.id


def _require_active_materials(db: Session, material_ids: set[uuid.UUID]) -> None:
    if not material_ids:
        _fail("material_request_materials_required", "invalid_request", "需求单缺少物料")
    rows = tuple(
        db.scalars(
            select(FormalMaterial)
            .where(FormalMaterial.id.in_(tuple(sorted(material_ids, key=str))))
            .execution_options(populate_existing=True)
        ).all()
    )
    if {row.id for row in rows if row.status == "active"} != material_ids:
        _fail("material_request_material_unavailable", "precondition_failed", "需求单包含不存在或停用的物料")


def _require_available_files(
    db: Session,
    file_ids: tuple[uuid.UUID, ...],
    actor_user_id: str,
) -> tuple[FileObject, ...]:
    if not file_ids:
        return ()
    rows = tuple(
        db.scalars(
            select(FileObject)
            .where(FileObject.id.in_(file_ids))
            .order_by(FileObject.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if {row.id for row in rows} != set(file_ids):
        _fail("material_request_file_not_found", "not_found", "需求附件不存在")
    if any(
        not formal_file_service.is_available_formal_file_for_purpose(
            row,
            purpose="request_attachment",
            uploader_user_id=actor_user_id,
        )
        for row in rows
    ):
        _fail("material_request_file_forbidden", "forbidden", "需求附件不可用或不属于当前申请人")
    return rows


def _require_exact_active_route(
    db: Session, now: datetime
) -> tuple[ApprovalRouteVersion, tuple[ApprovalRouteStepDef, ...]]:
    routes = tuple(
        db.scalars(
            select(ApprovalRouteVersion)
            .where(
                ApprovalRouteVersion.route_code == ROUTE_CODE,
                ApprovalRouteVersion.status == "active",
                ApprovalRouteVersion.effective_from <= now,
                or_(
                    ApprovalRouteVersion.effective_to.is_(None),
                    ApprovalRouteVersion.effective_to > now,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(routes) != 1 or routes[0].approval_mode != APPROVAL_MODE:
        _fail(
            "material_request_route_unavailable",
            "precondition_failed",
            "三级审批路由未唯一启用或不是已确认的外部登记模式",
        )
    steps = tuple(
        db.scalars(
            select(ApprovalRouteStepDef)
            .where(ApprovalRouteStepDef.route_version_id == routes[0].id)
            .order_by(ApprovalRouteStepDef.step_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    actual = tuple(
        (row.step_no, row.role_code, row.source_mode, row.scope_type) for row in steps
    )
    if actual != _EXPECTED_ROUTE:
        _fail(
            "material_request_route_shape_invalid",
            "precondition_failed",
            "三级审批路由不是区域、蔚来总部、星星总部固定三步",
        )
    return routes[0], steps


def _discover_candidate_user_ids(
    db: Session, *, region_org_id: uuid.UUID, now: datetime
) -> tuple[str, ...]:
    regional = _candidate_assignment_rows(
        db,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(region_org_id),
        now=now,
    )
    headquarters = _candidate_assignment_rows(
        db,
        role_code="admin",
        scope_type="national",
        scope_id="*",
        now=now,
    )
    return tuple(sorted({row.user_id for row in (*regional, *headquarters)}))


def _resolve_locked_candidates(
    db: Session,
    *,
    requester: _RequesterContext,
    region_org_id: uuid.UUID,
    request_id: uuid.UUID,
    now: datetime,
    expected_user_ids: tuple[str, ...],
) -> tuple[_Candidate, tuple[_Candidate, ...]]:
    regional_rows = _candidate_assignment_rows(
        db,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(region_org_id),
        now=now,
    )
    headquarters_rows = _candidate_assignment_rows(
        db,
        role_code="admin",
        scope_type="national",
        scope_id="*",
        now=now,
    )
    current_user_ids = tuple(
        sorted({row.user_id for row in (*regional_rows, *headquarters_rows)})
    )
    if current_user_ids != expected_user_ids:
        _fail(
            "material_request_candidate_set_changed",
            "conflict",
            "审批候选人在提交期间发生变化，请重新读取后提交",
        )
    regional_candidates = tuple(
        _candidate_from_assignment(
            db,
            row,
            now=now,
            resource_action="approve_region",
            target_scope_type="organization",
            target_scope_id=str(region_org_id),
            field_code="approval_decision",
        )
        for row in regional_rows
    )
    regional_policy = require_unique_regional_approver(
        tuple(candidate.policy for candidate in regional_candidates),
        requester_user_id=requester.principal.user_id,
        requester_person_id=requester.person.id,
    )
    regional = next(
        candidate
        for candidate in regional_candidates
        if candidate.policy.role_assignment_id == regional_policy.role_assignment_id
    )

    headquarters_candidates: list[_Candidate] = []
    for row in headquarters_rows:
        candidate = _candidate_from_assignment(
            db,
            row,
            now=now,
            resource_action="approve_headquarters",
            target_scope_type="organization",
            target_scope_id=str(region_org_id),
            field_code="approval_decision",
        )
        principal = load_formal_principal(db, row.user_id, now=now)
        grant = next(
            grant for grant in principal.assignments if grant.assignment_id == row.id
        )
        for action in ("register_external", "verify_external"):
            if not _selected_grant_allows(
                db,
                principal,
                grant,
                "material_request",
                action,
                field_code="approval_evidence",
                target_scope_type="document",
                target_scope_id=str(request_id),
            ):
                _fail(
                    "material_request_headquarters_candidate_forbidden",
                    "precondition_failed",
                    "总部审批候选人缺少外部登记或复核权限",
                )
        headquarters_candidates.append(candidate)
    eligible_policies = require_external_registration_admin_pool(
        tuple(candidate.policy for candidate in headquarters_candidates),
        requester_user_id=requester.principal.user_id,
        requester_person_id=requester.person.id,
    )
    eligible_ids = {policy.role_assignment_id for policy in eligible_policies}
    headquarters = tuple(
        candidate
        for candidate in headquarters_candidates
        if candidate.policy.role_assignment_id in eligible_ids
    )
    return regional, headquarters


def _candidate_assignment_rows(
    db: Session,
    *,
    role_code: str,
    scope_type: str,
    scope_id: str,
    now: datetime,
) -> tuple[RoleAssignment, ...]:
    return tuple(
        db.scalars(
            select(RoleAssignment)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                Role.code == role_code,
                Role.status == "active",
                RoleAssignment.scope_type == scope_type,
                RoleAssignment.scope_id == scope_id,
                RoleAssignment.status.in_(("scheduled", "active")),
                RoleAssignment.revoked_at.is_(None),
                RoleAssignment.valid_from <= now,
                or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
            )
            .order_by(RoleAssignment.user_id, RoleAssignment.id)
            .execution_options(populate_existing=True)
        ).all()
    )


def _candidate_from_assignment(
    db: Session,
    assignment: RoleAssignment,
    *,
    now: datetime,
    resource_action: str,
    target_scope_type: str,
    target_scope_id: str,
    field_code: str,
) -> _Candidate:
    try:
        principal = load_formal_principal(db, assignment.user_id, now=now)
    except FormalAccessError:
        _fail(
            "material_request_approval_candidate_invalid",
            "precondition_failed",
            "审批候选人的正式权限上下文无效",
        )
    if principal.account_status != "active" or principal.employment_status != "active" or principal.access_mode != "active":
        _fail(
            "material_request_approval_candidate_inactive",
            "precondition_failed",
            "审批候选人的账号或人员当前不可用",
        )
    grants = tuple(
        grant for grant in principal.assignments if grant.assignment_id == assignment.id
    )
    if len(grants) != 1 or not _selected_grant_allows(
        db,
        principal,
        grants[0],
        "material_request",
        resource_action,
        field_code=field_code,
        target_scope_type=target_scope_type,
        target_scope_id=target_scope_id,
    ):
        _fail(
            "material_request_approval_candidate_forbidden",
            "precondition_failed",
            "审批候选人没有覆盖本需求单的精确权限",
        )
    authorization_document = {
        "user_id": principal.user_id,
        "person_id": str(principal.person_id),
        "role_assignment_id": str(assignment.id),
        "role_code": grants[0].role_code,
        "scope_type": grants[0].scope_type,
        "scope_id": grants[0].scope_id,
        "authorization_version": principal.authorization_version,
        "permission_keys": [list(key) for key in principal.permission_keys()],
    }
    authorization_sha256 = _canonical_hash(authorization_document)
    snapshot = {
        key: value
        for key, value in authorization_document.items()
        if key != "permission_keys"
    }
    snapshot["authorization_sha256"] = authorization_sha256
    return _Candidate(
        policy=ApprovalCandidateSnapshot(
            user_id=principal.user_id,
            person_id=principal.person_id,
            role_assignment_id=assignment.id,
            role_code=grants[0].role_code,
            scope_type=grants[0].scope_type,
            scope_id=grants[0].scope_id,
            authorization_version=principal.authorization_version,
            authorization_sha256=authorization_sha256,
        ),
        snapshot=snapshot,
    )


def _approval_step(
    *,
    instance: ApprovalInstance,
    definition: ApprovalRouteStepDef,
    attempt_no: int,
    predecessor: ApprovalStep | None,
    status: str,
    assignee_user_id: str | None,
    snapshot: Mapping[str, Any],
    opened_at: datetime | None,
    now: datetime,
) -> ApprovalStep:
    return ApprovalStep(
        id=uuid.uuid4(),
        instance_id=instance.id,
        step_no=definition.step_no,
        attempt_no=attempt_no,
        predecessor_step_id=predecessor.id if predecessor is not None else None,
        source_mode=definition.source_mode,
        status=status,
        assignee_user_id=assignee_user_id,
        assignee_snapshot_jsonb=dict(snapshot),
        decision_manifest_sha256=None,
        opened_at=opened_at,
        decided_at=None,
        version=0,
        created_at=now,
        updated_at=now,
    )


def _add_candidate(
    db: Session,
    step: ApprovalStep,
    candidate: _Candidate,
    kind: str,
    now: datetime,
) -> None:
    db.add(
        ApprovalStepCandidate(
            id=uuid.uuid4(),
            step_id=step.id,
            user_id=candidate.policy.user_id,
            person_id=candidate.policy.person_id,
            role_assignment_id=candidate.policy.role_assignment_id,
            authorization_version=candidate.policy.authorization_version,
            candidate_kind=kind,
            snapshot_jsonb=dict(candidate.snapshot),
            created_at=now,
        )
    )


def _candidate_manifest(candidates: Sequence[_Candidate]) -> dict[str, Any]:
    documents = tuple(
        sorted(
            (candidate.snapshot for candidate in candidates),
            key=lambda row: (row["user_id"], row["role_assignment_id"]),
        )
    )
    return {
        "candidate_count": len(documents),
        "candidate_manifest_sha256": _canonical_hash(documents),
    }


def _new_revision(
    *,
    request: MaterialRequest,
    revision_no: int,
    previous_revision_id: uuid.UUID | None,
    draft: _PreparedDraft,
    address_masked: Mapping[str, Any],
    actor_user_id: str,
    now: datetime,
) -> MaterialRequestRevision:
    return MaterialRequestRevision(
        id=uuid.uuid4(),
        request_id=request.id,
        revision_no=revision_no,
        previous_revision_id=previous_revision_id,
        work_order_id=draft.work_order_id,
        purpose=draft.purpose,
        urgency=draft.urgency,
        expected_date=draft.expected_date,
        address_snapshot_jsonb=dict(draft.address_snapshot),
        address_masked_jsonb=dict(address_masked),
        contact_snapshot_jsonb=dict(draft.contact_envelope),
        contact_masked_jsonb=dict(draft.contact_masked),
        note=draft.note,
        approval_mode=APPROVAL_MODE,
        status="draft",
        content_manifest_sha256=None,
        sealed_at=None,
        sealed_by_user_id=None,
        created_by_user_id=actor_user_id,
        created_at=now,
        updated_at=now,
    )


def _copy_prepared_to_revision(
    revision: MaterialRequestRevision,
    draft: _PreparedDraft,
    *,
    address_masked: Mapping[str, Any],
    now: datetime,
) -> None:
    if revision.status != "draft":
        _fail(
            "material_request_revision_immutable",
            "conflict",
            "已封存的需求版本不可覆盖",
        )
    revision.work_order_id = draft.work_order_id
    revision.purpose = draft.purpose
    revision.urgency = draft.urgency
    revision.expected_date = draft.expected_date
    revision.address_snapshot_jsonb = dict(draft.address_snapshot)
    revision.address_masked_jsonb = dict(address_masked)
    revision.contact_snapshot_jsonb = dict(draft.contact_envelope)
    revision.contact_masked_jsonb = dict(draft.contact_masked)
    revision.note = draft.note
    revision.approval_mode = APPROVAL_MODE
    revision.updated_at = now


def _copy_revision_to_request(
    request: MaterialRequest, revision: MaterialRequestRevision
) -> None:
    request.work_order_id = revision.work_order_id
    request.purpose = revision.purpose
    request.urgency = revision.urgency
    request.expected_date = revision.expected_date
    request.address_snapshot_jsonb = dict(revision.address_snapshot_jsonb)
    request.address_masked_jsonb = dict(revision.address_masked_jsonb)
    request.contact_snapshot_jsonb = dict(revision.contact_snapshot_jsonb)
    request.contact_masked_jsonb = dict(revision.contact_masked_jsonb)
    request.note = revision.note
    request.approval_mode = revision.approval_mode


def _add_draft_lines(
    db: Session,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    values: Sequence[MaterialRequestDraftLineInput],
    *,
    now: datetime,
) -> tuple[MaterialRequestLine, ...]:
    rows = tuple(
        MaterialRequestLine(
            id=uuid.uuid4(),
            request_id=request.id,
            revision_id=revision.id,
            revision_no=revision.revision_no,
            line_no=index,
            client_line_key=uuid.uuid4(),
            material_id=value.material_id,
            suggested_substitute_material_id=value.suggested_substitute_material_id,
            requested_qty=value.requested_qty,
            required_date=value.required_date,
            note=value.note,
            status="draft",
            final_approved_qty=Decimal("0.000"),
            cancelled_qty=Decimal("0.000"),
            version=0,
            created_at=now,
            updated_at=now,
        )
        for index, value in enumerate(values, start=1)
    )
    db.add_all(rows)
    return rows


def _add_request_files(
    db: Session,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    file_ids: Sequence[uuid.UUID],
    *,
    actor_user_id: str,
    now: datetime,
) -> None:
    db.add_all(
        MaterialRequestFile(
            id=uuid.uuid4(),
            request_id=request.id,
            revision_id=revision.id,
            revision_no=revision.revision_no,
            request_line_id=None,
            file_id=file_id,
            purpose="request_attachment",
            created_by_user_id=actor_user_id,
            created_at=now,
        )
        for file_id in file_ids
    )


def _lock_request(db: Session, request_id: uuid.UUID) -> MaterialRequest:
    row = db.scalar(
        select(MaterialRequest)
        .where(MaterialRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        _fail("material_request_not_found", "not_found", "需求单不存在")
    return row


def _lock_current_revision(
    db: Session, request: MaterialRequest
) -> MaterialRequestRevision:
    """Lock the frozen 0029 current coordinate by both key components.

    0029 intentionally has no ``current_revision_id`` alias.  The sole
    current coordinate is ``(request.id, request.revision_no)``; a missing or
    non-unique match fails closed even though the database also has a unique
    constraint.
    """

    rows = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(
                MaterialRequestRevision.request_id == request.id,
                MaterialRequestRevision.revision_no == request.revision_no,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _fail(
            "material_request_current_revision_invalid",
            "service_unavailable",
            "需求单当前版本坐标不唯一或缺失",
        )
    return rows[0]


def _lock_revision_lines(
    db: Session, revision: MaterialRequestRevision
) -> tuple[MaterialRequestLine, ...]:
    return tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(
                MaterialRequestLine.request_id == revision.request_id,
                MaterialRequestLine.revision_id == revision.id,
                MaterialRequestLine.revision_no == revision.revision_no,
            )
            .order_by(MaterialRequestLine.line_no, MaterialRequestLine.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )


def _lock_revision_files(
    db: Session, revision: MaterialRequestRevision
) -> tuple[MaterialRequestFile, ...]:
    return tuple(
        db.scalars(
            select(MaterialRequestFile)
            .where(
                MaterialRequestFile.request_id == revision.request_id,
                MaterialRequestFile.revision_id == revision.id,
                MaterialRequestFile.revision_no == revision.revision_no,
            )
            .order_by(MaterialRequestFile.id)
            .execution_options(populate_existing=True)
        ).all()
    )


def _require_owned_request(
    request: MaterialRequest, requester: _RequesterContext
) -> None:
    if (
        request.requester_user_id != requester.principal.user_id
        or request.requester_person_id != requester.person.id
        or request.requester_org_id != requester.region.id
        or request.created_by_user_id != requester.principal.user_id
    ):
        _fail("material_request_owner_forbidden", "forbidden", "只能操作本人且所属区域一致的需求单")


def _require_editable_revision(
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    expected_version: int,
) -> None:
    if request.version != expected_version:
        _fail("material_request_version_conflict", "conflict", "需求单版本已变化，请重新读取")
    if request.status not in {"draft", "returned"}:
        _fail("material_request_not_editable_draft", "conflict", "需求单当前状态不可补充或修改")
    if request.status == "draft" and (
        request.submitted_at is not None or revision.status != "draft"
    ):
        _fail("material_request_draft_state_invalid", "precondition_failed", "需求单草稿状态不一致")
    if request.status == "returned" and (
        request.submitted_at is None or revision.status not in {"sealed", "draft"}
    ):
        _fail("material_request_returned_state_invalid", "precondition_failed", "退回需求的版本状态不一致")
    if any(
        value is not None
        for value in (request.decided_at, request.withdrawn_at, request.cancelled_at)
    ):
        _fail("material_request_draft_state_invalid", "precondition_failed", "需求单草稿状态不一致")
    _assert_current_projection(request, revision)
    assert_approval_did_not_advance_fulfillment_axes(_NEUTRAL_AXES, _request_axes(request))


def _require_submittable_revision(
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    expected_version: int,
) -> None:
    _require_editable_revision(request, revision, expected_version)
    if revision.status != "draft":
        _fail(
            "material_request_returned_revision_required",
            "precondition_failed",
            "退回需求必须先生成并补充新的草稿版本",
        )
    if request.status == "returned" and (
        revision.revision_no <= 1 or revision.previous_revision_id is None
    ):
        _fail(
            "material_request_revision_chain_invalid",
            "precondition_failed",
            "退回需求的新版本链不完整",
        )


def _require_returned_revision_source(
    db: Session,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
) -> ApprovalInstance:
    if request.status != "returned" or revision.status != "sealed":
        _fail(
            "material_request_returned_state_invalid",
            "precondition_failed",
            "只有已退回的封存版本可以创建补充版本",
        )
    instances = tuple(
        db.scalars(
            select(ApprovalInstance)
            .where(ApprovalInstance.request_revision_id == revision.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(instances) != 1 or instances[0].status != "returned":
        _fail(
            "material_request_returned_instance_invalid",
            "precondition_failed",
            "退回审批实例与封存版本不一致",
        )
    return instances[0]


def _require_returned_draft_chain(
    db: Session,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
) -> None:
    if (
        request.status != "returned"
        or revision.status != "draft"
        or revision.previous_revision_id is None
        or revision.revision_no <= 1
    ):
        _fail(
            "material_request_revision_chain_invalid",
            "precondition_failed",
            "退回需求的新版本链不完整",
        )
    previous_rows = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(
                MaterialRequestRevision.id == revision.previous_revision_id,
                MaterialRequestRevision.request_id == request.id,
                MaterialRequestRevision.revision_no == revision.revision_no - 1,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(previous_rows) != 1 or previous_rows[0].status != "sealed":
        _fail(
            "material_request_revision_chain_invalid",
            "precondition_failed",
            "退回需求的上一封存版本无效",
        )
    _require_returned_revision_source(db, request, previous_rows[0])


def _next_approval_attempt(
    db: Session,
    *,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
) -> tuple[int, ApprovalInstance | None]:
    instances = tuple(
        db.scalars(
            select(ApprovalInstance)
            .where(ApprovalInstance.request_id == request.id)
            .order_by(ApprovalInstance.attempt_no)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if request.status == "draft":
        if instances or revision.revision_no != 1 or revision.previous_revision_id is not None:
            _fail(
                "material_request_approval_attempt_invalid",
                "precondition_failed",
                "首次审批尝试与需求版本不一致",
            )
        return 1, None

    if tuple(row.attempt_no for row in instances) != tuple(
        range(1, len(instances) + 1)
    ):
        _fail(
            "material_request_approval_attempt_invalid",
            "service_unavailable",
            "需求审批尝试序号不连续",
        )
    previous = instances[-1] if instances else None
    if (
        previous is None
        or previous.status != "returned"
        or revision.previous_revision_id != previous.request_revision_id
        or previous.revision_no != revision.revision_no - 1
    ):
        _fail(
            "material_request_approval_attempt_invalid",
            "precondition_failed",
            "退回版本没有精确匹配的上一审批尝试",
        )
    return previous.attempt_no + 1, previous


def _assert_current_projection(
    request: MaterialRequest, revision: MaterialRequestRevision
) -> None:
    if (
        revision.request_id != request.id
        or revision.revision_no != request.revision_no
        or request.work_order_id != revision.work_order_id
        or request.purpose != revision.purpose
        or request.urgency != revision.urgency
        or request.expected_date != revision.expected_date
        or request.address_snapshot_jsonb != revision.address_snapshot_jsonb
        or request.address_masked_jsonb != revision.address_masked_jsonb
        or request.contact_snapshot_jsonb != revision.contact_snapshot_jsonb
        or request.contact_masked_jsonb != revision.contact_masked_jsonb
        or request.note != revision.note
        or request.approval_mode != revision.approval_mode
    ):
        _fail(
            "material_request_current_projection_invalid",
            "service_unavailable",
            "需求单当前投影与版本快照不一致",
        )


def _revision_content_manifest(
    revision: MaterialRequestRevision,
    lines: Sequence[MaterialRequestLine],
    files: Sequence[MaterialRequestFile],
    file_objects: Sequence[FileObject],
) -> str:
    file_by_id = {row.id: row for row in file_objects}
    if len(file_by_id) != len(file_objects) or set(file_by_id) != {
        row.file_id for row in files
    }:
        _fail(
            "material_request_manifest_file_set_invalid",
            "service_unavailable",
            "需求附件清单无法完整重算",
        )
    return _canonical_hash(
        {
            "revision_id": str(revision.id),
            "request_id": str(revision.request_id),
            "revision_no": revision.revision_no,
            "previous_revision_id": (
                str(revision.previous_revision_id)
                if revision.previous_revision_id is not None
                else None
            ),
            "header": {
                "work_order_id": (
                    str(revision.work_order_id)
                    if revision.work_order_id is not None
                    else None
                ),
                "purpose": revision.purpose,
                "urgency": revision.urgency,
                "expected_date": (
                    revision.expected_date.isoformat()
                    if revision.expected_date is not None
                    else None
                ),
                "address_snapshot": revision.address_snapshot_jsonb,
                "address_masked": revision.address_masked_jsonb,
                "contact_envelope": revision.contact_snapshot_jsonb,
                "contact_masked": revision.contact_masked_jsonb,
                "note": revision.note,
                "approval_mode": revision.approval_mode,
            },
            "lines": [
                {
                    "id": str(line.id),
                    "line_no": line.line_no,
                    "client_line_key": str(line.client_line_key),
                    "material_id": str(line.material_id),
                    "suggested_substitute_material_id": (
                        str(line.suggested_substitute_material_id)
                        if line.suggested_substitute_material_id is not None
                        else None
                    ),
                    "requested_qty": format(line.requested_qty, "f"),
                    "required_date": (
                        line.required_date.isoformat()
                        if line.required_date is not None
                        else None
                    ),
                    "note": line.note,
                }
                for line in lines
            ],
            "files": [
                {
                    "binding_id": str(row.id),
                    "file_id": str(row.file_id),
                    "purpose": row.purpose,
                    "sha256": file_by_id[row.file_id].sha256,
                    "size_bytes": file_by_id[row.file_id].size_bytes,
                    "mime_type": file_by_id[row.file_id].mime_type,
                }
                for row in files
            ],
        }
    )


def _command_fact(
    *,
    operation: str,
    request: MaterialRequest,
    target_version: int,
    key_hash: str,
    request_reference: str,
    request_hash: str,
    result: MaterialRequestCreateResult
    | MaterialRequestDraftResult
    | MaterialRequestSubmitResult,
    actor: _RequesterContext,
    occurred_at: datetime,
) -> MaterialRequestCommand:
    result_json = _result_document(result)
    return MaterialRequestCommand(
        id=uuid.uuid4(),
        operation=operation,
        request_id=request.id,
        target_version=target_version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_hash=request_hash,
        result_hash=_canonical_hash(result_json),
        request_jsonb={
            "schema": "rsc.material_request_command.v1",
            "operation": operation,
            "request_id": str(request.id),
            "revision_id": str(result.revision_id),
            "revision_no": result.revision_no,
            "target_version": target_version,
            "approval_attempt_no": (
                result.approval_attempt_no
                if isinstance(result, MaterialRequestSubmitResult)
                else None
            ),
            "payload_sha256": request_hash,
            "sensitive_fields": "excluded",
        },
        result_jsonb=result_json,
        actor_user_id=actor.principal.user_id,
        actor_person_id=actor.person.id,
        actor_role_assignment_id=actor.technician_grant.assignment_id,
        authorization_version=actor.principal.authorization_version,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _state_event(
    *,
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    from_status: str | None,
    to_status: str,
    reason: str,
    actor_user_id: str,
    key_hash: str,
    suffix: str,
    occurred_at: datetime,
) -> StateTransitionEvent:
    return StateTransitionEvent(
        id=uuid.uuid4(),
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(request.id),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor_id=actor_user_id,
        idempotency_key=f"mr:{key_hash}:{suffix}",
        occurred_at=occurred_at,
        metadata_jsonb={
            "request_id": str(request.id),
            "request_no": request.request_no,
            "revision_id": str(revision.id),
            "revision_no": revision.revision_no,
            "idempotency_key_hash": key_hash,
        },
        created_at=occurred_at,
    )


def _load_create_replay(
    db: Session,
    *,
    key_hash: str,
    request_hash: str,
    operation: str,
    actor: FormalPrincipal,
) -> MaterialRequestCreateResult | None:
    row = _load_command(db, key_hash)
    if row is None:
        return None
    _validate_replay_fact(row, request_hash, operation, actor)
    return _create_result_from_json(row.result_jsonb)


def _load_draft_replay(
    db: Session,
    *,
    key_hash: str,
    request_hash: str,
    operation: str,
    actor: FormalPrincipal,
) -> MaterialRequestDraftResult | None:
    row = _load_command(db, key_hash)
    if row is None:
        return None
    _validate_replay_fact(row, request_hash, operation, actor)
    return _draft_result_from_json(row.result_jsonb)


def _load_submit_replay(
    db: Session,
    *,
    key_hash: str,
    request_hash: str,
    actor: FormalPrincipal,
) -> MaterialRequestSubmitResult | None:
    row = _load_command(db, key_hash)
    if row is None:
        return None
    _validate_replay_fact(row, request_hash, "submit", actor)
    return _submit_result_from_json(row.result_jsonb)


def _load_command(db: Session, key_hash: str) -> MaterialRequestCommand | None:
    return db.scalar(
        select(MaterialRequestCommand)
        .where(MaterialRequestCommand.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )


def _validate_replay_fact(
    row: MaterialRequestCommand,
    request_hash: str,
    operation: str,
    actor: FormalPrincipal,
) -> None:
    if row.request_hash != request_hash or row.operation != operation:
        _fail("material_request_idempotency_conflict", "conflict", "幂等键已用于不同的需求单命令")
    if row.actor_user_id != actor.user_id or row.actor_person_id != actor.person_id:
        _fail("material_request_idempotency_actor_mismatch", "forbidden", "幂等记录不属于当前申请人")
    if not isinstance(row.result_jsonb, dict) or not hmac.compare_digest(
        row.result_hash or "", _canonical_hash(row.result_jsonb)
    ):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )


def _draft_result(
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    lines: Sequence[MaterialRequestLine],
) -> MaterialRequestDraftResult:
    return MaterialRequestDraftResult(
        request_id=request.id,
        request_no=request.request_no,
        status=request.status,
        version=request.version,
        revision_id=revision.id,
        revision_no=revision.revision_no,
        line_ids=tuple(row.id for row in lines),
        state_axes=dict(_request_axes(request)),
    )


def _create_result(
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    lines: Sequence[MaterialRequestLine],
) -> MaterialRequestCreateResult:
    return MaterialRequestCreateResult(
        schema_version="1.0",
        request_id=request.id,
        action="create",
        request_no=request.request_no,
        status=request.status,
        request_version=request.version,
        revision_id=revision.id,
        revision_no=revision.revision_no,
        line_ids=tuple(row.id for row in lines),
        state_axes=dict(_request_axes(request)),
    )


def _result_document(
    result: MaterialRequestCreateResult
    | MaterialRequestDraftResult
    | MaterialRequestSubmitResult,
) -> dict[str, Any]:
    if isinstance(result, MaterialRequestCreateResult):
        return {
            "kind": "create",
            "schema_version": result.schema_version,
            "request_id": str(result.request_id),
            "action": result.action,
            "request_no": result.request_no,
            "status": result.status,
            "request_version": result.request_version,
            "revision_id": str(result.revision_id),
            "revision_no": result.revision_no,
            "line_ids": [str(value) for value in result.line_ids],
            "state_axes": dict(result.state_axes),
        }
    if isinstance(result, MaterialRequestDraftResult):
        return {
            "kind": "draft",
            "request_id": str(result.request_id),
            "request_no": result.request_no,
            "status": result.status,
            "version": result.version,
            "revision_id": str(result.revision_id),
            "revision_no": result.revision_no,
            "line_ids": [str(value) for value in result.line_ids],
            "state_axes": dict(result.state_axes),
        }
    return {
        "kind": "submit",
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "status": result.status,
        "version": result.version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "approval_attempt_no": result.approval_attempt_no,
        "approval_instance_id": str(result.approval_instance_id),
        "approval_step_ids": [str(value) for value in result.approval_step_ids],
        "regional_approver_user_id": result.regional_approver_user_id,
        "headquarters_candidate_user_ids": list(result.headquarters_candidate_user_ids),
        "state_axes": dict(result.state_axes),
    }


def _create_result_from_json(value: Mapping[str, Any]) -> MaterialRequestCreateResult:
    try:
        if (
            value.get("kind") != "create"
            or value.get("schema_version") != "1.0"
            or value.get("action") != "create"
        ):
            raise ValueError
        axes = _exact_axes(value.get("state_axes"))
        return MaterialRequestCreateResult(
            schema_version="1.0",
            request_id=uuid.UUID(str(value["request_id"])),
            action="create",
            request_no=str(value["request_no"]),
            status=str(value["status"]),
            request_version=int(value["request_version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            line_ids=tuple(uuid.UUID(str(item)) for item in value["line_ids"]),
            state_axes=axes,
        )
    except (KeyError, TypeError, ValueError):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )


def _draft_result_from_json(value: Mapping[str, Any]) -> MaterialRequestDraftResult:
    try:
        if value.get("kind") != "draft":
            raise ValueError
        axes = _exact_axes(value.get("state_axes"))
        return MaterialRequestDraftResult(
            request_id=uuid.UUID(str(value["request_id"])),
            request_no=str(value["request_no"]),
            status=str(value["status"]),
            version=int(value["version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            line_ids=tuple(uuid.UUID(str(item)) for item in value["line_ids"]),
            state_axes=axes,
        )
    except (KeyError, TypeError, ValueError):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )


def _submit_result_from_json(value: Mapping[str, Any]) -> MaterialRequestSubmitResult:
    try:
        if value.get("kind") != "submit":
            raise ValueError
        step_ids = tuple(uuid.UUID(str(item)) for item in value["approval_step_ids"])
        if len(step_ids) != 3:
            raise ValueError
        axes = _exact_axes(value.get("state_axes"))
        return MaterialRequestSubmitResult(
            request_id=uuid.UUID(str(value["request_id"])),
            request_no=str(value["request_no"]),
            status=str(value["status"]),
            version=int(value["version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            approval_attempt_no=int(value["approval_attempt_no"]),
            approval_instance_id=uuid.UUID(str(value["approval_instance_id"])),
            approval_step_ids=(step_ids[0], step_ids[1], step_ids[2]),
            regional_approver_user_id=str(value["regional_approver_user_id"]),
            headquarters_candidate_user_ids=tuple(
                str(item) for item in value["headquarters_candidate_user_ids"]
            ),
            state_axes=axes,
        )
    except (KeyError, TypeError, ValueError):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )


def _safe_request_snapshot(
    request: MaterialRequest,
    revision: MaterialRequestRevision,
    lines: Sequence[MaterialRequestLine],
    attachment_count: int,
) -> dict[str, Any]:
    line_manifest = tuple(
        {
            "line_id": str(line.id),
            "line_no": line.line_no,
            "material_id": str(line.material_id),
            "requested_qty": format(line.requested_qty, "f"),
            "status": line.status,
        }
        for line in lines
    )
    return {
        "request_id": str(request.id),
        "request_no": request.request_no,
        "requester_person_id": str(request.requester_person_id),
        "requester_org_id": str(request.requester_org_id),
        "status": request.status,
        "version": request.version,
        "revision_id": str(revision.id),
        "revision_no": revision.revision_no,
        "line_count": len(lines),
        "line_manifest_sha256": _canonical_hash(line_manifest),
        "attachment_count": attachment_count,
        "state_axes": dict(_request_axes(request)),
        "sensitive_fields": "excluded",
    }


def _request_axes(request: MaterialRequest) -> dict[str, str]:
    return {field: getattr(request, field) for field in _NEUTRAL_AXES}


def _exact_axes(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_NEUTRAL_AXES):
        raise ValueError("state axes invalid")
    result = {field: str(value[field]) for field in _NEUTRAL_AXES}
    if result != _NEUTRAL_AXES:
        raise ValueError("state axes advanced")
    return result


def _draft_request_hash(
    operation: str,
    request_id: uuid.UUID,
    requester: _RequesterContext,
    draft: _PreparedDraft,
    *,
    expected_version: int | None,
) -> str:
    return _canonical_hash(
        {
            "operation": operation,
            "request_id": str(request_id),
            "actor_user_id": requester.principal.user_id,
            "requester_person_id": str(requester.person.id),
            "requester_org_id": str(requester.region.id),
            "expected_version": expected_version,
            "work_order_id": str(draft.work_order_id) if draft.work_order_id else None,
            "purpose": draft.purpose,
            "urgency": draft.urgency,
            "expected_date": draft.expected_date.isoformat() if draft.expected_date else None,
            "address": draft.address_snapshot,
            # Encryption is deliberately randomized.  Idempotency compares the
            # stable, domain-separated contact identity proof and its request
            # binding, never ciphertext/nonce/key metadata.
            "contact_proof": {
                "contact_hmac": draft.contact_envelope["contact_hmac"],
                "mobile_hmac": draft.contact_envelope["mobile_hmac"],
                "aad_sha256": draft.contact_envelope["aad_sha256"],
            },
            "contact_masked": draft.contact_masked,
            "attachment_file_ids": [str(value) for value in draft.attachment_file_ids],
            "lines": [
                {
                    "material_id": str(line.material_id),
                    "requested_qty": format(line.requested_qty, "f"),
                    "required_date": line.required_date.isoformat() if line.required_date else None,
                    "suggested_substitute_material_id": (
                        str(line.suggested_substitute_material_id)
                        if line.suggested_substitute_material_id
                        else None
                    ),
                    "note": line.note,
                }
                for line in draft.lines
            ],
            "note": draft.note,
        }
    )


def _request_number(request_id: uuid.UUID, now: datetime) -> str:
    return f"MR-{now:%Y%m%d}-{request_id.hex[:12].upper()}"


def _require_uuid(field: str, value: Any) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 无效")
    return value


def _validate_uuid_tuple(
    field: str, value: Any, *, maximum: int
) -> tuple[uuid.UUID, ...]:
    if not isinstance(value, tuple) or len(value) > maximum:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 无效")
    checked = tuple(_require_uuid(field, item) for item in value)
    if len(set(checked)) != len(checked):
        _fail(f"{field}_duplicate", "invalid_request", f"{field} 不能重复")
    return checked


def _require_quantity(value: Any) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
        or value >= _MAX_QUANTITY
        or value.as_tuple().exponent < -3
    ):
        _fail("material_request_quantity_invalid", "invalid_request", "数量必须为最多三位小数的正数")
    return value.quantize(_QUANTUM)


def _require_text(field: str, value: Any, limit: int, *, required: bool) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > limit or (required and not value):
        _fail(f"material_request_{field}_invalid", "invalid_request", f"{field} 格式无效")
    return value


def _require_version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail("material_request_version_invalid", "invalid_request", "需求单版本无效")
    return value


def _require_trace_request_id(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_TRACE.fullmatch(value) is None:
        _fail("material_request_trace_id_invalid", "invalid_request", "X-Request-ID 无效")
    return value


def _require_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or _PRINTABLE.fullmatch(value) is None:
        _fail("material_request_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(encoded, bytes)
        or len(encoded) < 32
        or any(marker in encoded.lower().decode("utf-8", "ignore") for marker in _PLACEHOLDERS)
    ):
        _fail(
            "material_request_idempotency_hmac_unavailable",
            "service_unavailable",
            "需求单幂等 HMAC 密钥不可用",
        )
    return encoded


def _idempotency_hmac(
    secret: bytes,
    actor_user_id: str,
    method: str,
    path: str,
    raw_key: str,
) -> str:
    document = (
        "cloud_oam.material_request.idempotency.v1\0"
        f"actor={actor_user_id}\0method={method}\0path={path}\0key={raw_key}"
    ).encode("utf-8")
    return hmac.new(secret, document, hashlib.sha256).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("material_request_database_clock_invalid", "service_unavailable", "数据库时间不可用")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _take_advisory_locks(
    db: Session, key_hash: str, request_id: uuid.UUID
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for namespace, value in (
        ("idempotency", key_hash),
        ("request", str(request_id)),
    ):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:coordinate)"),
            {"coordinate": _signed_lock_coordinate(namespace, value)},
        )


def _signed_lock_coordinate(namespace: str, value: str) -> int:
    raw = hashlib.sha256(f"material-request:{namespace}:{value}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def _organization_descends_from(
    db: Session, organization_id: uuid.UUID, ancestor_id: uuid.UUID
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail("material_request_organization_cycle", "service_unavailable", "组织树存在循环引用")
        seen.add(current_id)
        row = db.scalar(
            select(Organization)
            .where(Organization.id == current_id)
            .execution_options(populate_existing=True)
        )
        if row is None or row.status != "active":
            return False
        if row.id == ancestor_id:
            return True
        current_id = row.parent_id
    return False


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestDraftError(code, category, message)


__all__ = [
    "MATERIAL_REQUEST_AUDIT_STREAM",
    "MaterialRequestCreateResult",
    "MaterialRequestDraftError",
    "MaterialRequestDraftInput",
    "MaterialRequestDraftLineInput",
    "MaterialRequestDraftResult",
    "MaterialRequestSubmitResult",
    "amend_material_request_draft",
    "create_material_request_draft",
    "derive_material_request_create_id",
    "mask_material_request_contact",
    "submit_material_request",
]
