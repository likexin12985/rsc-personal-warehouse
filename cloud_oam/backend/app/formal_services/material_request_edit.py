"""Owner-only plaintext projection for editing one current demand draft.

Ordinary list/detail reads remain irreversibly masked.  This narrow query is
available only when the same current requester is still authorized to update
the exact draft/reapproval revision.  It decrypts through the injected KMS
boundary, revalidates the stored masked projections, and repeats the formal
authorization/version read after decryption so a concurrent revocation or
state change cannot disclose a stale editable snapshot.
"""

from __future__ import annotations

from typing import Any, Final, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..demand_models import MaterialRequestRevision
from ..demand_schemas import MaterialRequestCreateIn
from ..formal_access import FormalPrincipal
from ..material_request_read_schemas import MaterialRequestDetailOut
from . import material_request_query as query_service
from .material_request_contact import (
    MaterialRequestContactCipher,
    MaterialRequestContactProtectionError,
    reveal_material_request_contact,
)
from .material_request_draft import (
    MaterialRequestDraftError,
    mask_material_request_contact,
)


_HTTP_STATUS_BY_CATEGORY: Final[dict[str, int]] = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestEditableDraftError(RuntimeError):
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


class MaterialRequestEditableDraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    request_id: uuid.UUID
    request_version: int = Field(ge=0)
    draft: MaterialRequestCreateIn


def material_request_editable_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    request_id: uuid.UUID,
    cipher: MaterialRequestContactCipher,
    kms_key_id: str,
    mobile_hmac_secret: bytes | str,
    mobile_hash_version: int,
) -> MaterialRequestEditableDraftOut:
    """Return the exact current editable draft without mutating the session."""

    if not isinstance(request_id, uuid.UUID) or request_id.int == 0:
        _fail("material_request_id_invalid", "invalid_request", "需求单标识无效")
    with db.no_autoflush:
        before = _authorized_detail(db, actor=actor, request_id=request_id)
        revision = db.scalar(
            select(MaterialRequestRevision)
            .where(
                MaterialRequestRevision.id == before.current_revision_id,
                MaterialRequestRevision.request_id == before.request_id,
                MaterialRequestRevision.revision_no == before.current_revision_no,
            )
            .execution_options(populate_existing=True)
        )
        if revision is None:
            _fail(
                "material_request_edit_revision_changed",
                "conflict",
                "需求单可编辑版本已变化，请重新读取",
            )
        expected_revision_status = (
            "draft" if before.states.request_status == "draft" else "sealed"
        )
        if revision.status != expected_revision_status:
            _fail(
                "material_request_edit_revision_state_invalid",
                "service_unavailable",
                "需求单状态与当前可编辑来源版本不一致",
            )
        if revision.approval_mode != "external_registration":
            _fail(
                "material_request_edit_approval_mode_invalid",
                "service_unavailable",
                "需求单审批模式不符合一期正式契约",
            )
        try:
            contact = reveal_material_request_contact(
                cipher=cipher,
                kms_key_id=kms_key_id,
                mobile_hmac_secret=mobile_hmac_secret,
                mobile_hash_version=mobile_hash_version,
                request_id=before.request_id,
                requester_person_id=before.requester_person_id,
                envelope=revision.contact_snapshot_jsonb,
            )
            if mask_material_request_contact(**contact) != dict(
                revision.contact_masked_jsonb
            ):
                _fail(
                    "material_request_edit_contact_projection_invalid",
                    "service_unavailable",
                    "联系人脱敏投影与加密快照不一致",
                )
            address = MaterialRequestCreateIn.model_validate(
                {
                    "work_order_id": revision.work_order_id,
                    "purpose": revision.purpose,
                    "urgency": revision.urgency,
                    "expected_date": revision.expected_date,
                    "address": dict(revision.address_snapshot_jsonb),
                    "contact": contact,
                    "attachment_file_ids": tuple(
                        item.file_id for item in before.attachment_refs
                    ),
                    "note": revision.note,
                    "lines": tuple(
                        {
                            "material_id": row.material_id,
                            "requested_qty": format(row.requested_qty, ".3f"),
                            "required_date": row.required_date,
                            "suggested_substitute_material_id": (
                                row.suggested_substitute_material_id
                            ),
                            "note": row.note,
                        }
                        for row in before.lines
                    ),
                }
            )
        except MaterialRequestEditableDraftError:
            raise
        except MaterialRequestDraftError:
            _fail(
                "material_request_edit_contact_projection_invalid",
                "service_unavailable",
                "联系人脱敏投影与加密快照不一致",
            )
        except MaterialRequestContactProtectionError:
            _fail(
                "material_request_edit_contact_unavailable",
                "service_unavailable",
                "联系人加密快照无法安全解密",
            )
        except ValidationError:
            _fail(
                "material_request_edit_projection_invalid",
                "service_unavailable",
                "需求单可编辑投影未通过正式契约校验",
            )

        expected_address_mask = {
            "province_code": address.address.province_code,
            "province_name": address.address.province_name,
            "city_name": address.address.city_name,
            "district_name": address.address.district_name,
            "detail_masked": "******",
        }
        if expected_address_mask != dict(revision.address_masked_jsonb):
            _fail(
                "material_request_edit_address_projection_invalid",
                "service_unavailable",
                "收货地址脱敏投影与当前快照不一致",
            )

        after = _authorized_detail(db, actor=actor, request_id=request_id)
        if _read_coordinate(after) != _read_coordinate(before):
            _fail(
                "material_request_edit_snapshot_changed",
                "conflict",
                "需求单或授权在解密期间发生变化，请重新读取",
            )
        try:
            return MaterialRequestEditableDraftOut(
                request_id=before.request_id,
                request_version=before.request_version,
                draft=address,
            )
        except ValidationError:
            _fail(
                "material_request_edit_projection_invalid",
                "service_unavailable",
                "需求单可编辑投影未通过正式契约校验",
            )


def _authorized_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    request_id: uuid.UUID,
) -> MaterialRequestDetailOut:
    try:
        detail = query_service.material_request_detail(
            db,
            actor=actor,
            request_id=request_id,
        )
    except query_service.MaterialRequestReadError as exc:
        raise MaterialRequestEditableDraftError(
            exc.code,
            exc.category,
            exc.message,
        ) from None
    except DBAPIError:
        _fail(
            "material_request_edit_database_unavailable",
            "service_unavailable",
            "需求单可编辑投影暂时不可用",
        )
    if "update" not in detail.allowed_actions:
        _fail(
            "material_request_edit_forbidden",
            "forbidden",
            "当前主体不能读取该需求单的可编辑明文快照",
        )
    if detail.states.request_status not in {"draft", "returned"}:
        _fail(
            "material_request_edit_state_invalid",
            "precondition_failed",
            "需求单当前状态不可编辑",
        )
    return detail


def _read_coordinate(detail: MaterialRequestDetailOut) -> tuple[Any, ...]:
    return (
        detail.request_id,
        detail.request_version,
        detail.current_revision_id,
        detail.current_revision_no,
        detail.states.request_status,
        detail.requester_person_id,
        detail.requester_org_id,
        detail.allowed_actions,
    )


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestEditableDraftError(code, category, message)


__all__ = [
    "MaterialRequestEditableDraftError",
    "MaterialRequestEditableDraftOut",
    "material_request_editable_draft",
]
