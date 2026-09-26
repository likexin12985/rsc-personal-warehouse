"""Versioned, strict documents stored with an opening-count import job.

These documents are server-produced evidence, never browser authorization.
Loading one does not replace current source, principal or scope checks.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .opening_count_import_prevalidation import OpeningCountImportBusinessPreview
from .opening_count_import_workbook import FIELDS, MAX_ERRORS, MAX_ROWS, ImportRowError
from .opening_stocktake_count import OpeningStocktakeScopeCountPrevalidation


Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpeningCountImportBinding(_Document):
    version: Literal[1] = 1
    source_file_id: UUID
    source_sha256: Digest
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    authorization_version: Positive
    count_key_sha256: Digest


class _CountDocument(_Document):
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    task_version: Nonnegative
    actor_authorization_version: Positive
    observation_count: Annotated[int, Field(strict=True, ge=1, le=MAX_ROWS)]
    request_sha256: Digest
    binding_sha256: Digest
    pending_verification_input_ordinals: tuple[Positive, ...]

    @model_validator(mode="after")
    def validate_pending_ordinals(self):
        values = self.pending_verification_input_ordinals
        if tuple(sorted(set(values))) != values or any(value > self.observation_count for value in values):
            raise ValueError("invalid pending observation coordinates")
        return self


class _ErrorDocument(_Document):
    row: Annotated[int, Field(strict=True, ge=2, le=MAX_ROWS + 1)]
    field: str
    code: Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,79}$")]
    message: Annotated[str, Field(strict=True, min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_controlled_metadata(self):
        if (self.field not in (*FIELDS, "row")
                or any(ord(character) < 32 for character in self.message)
                or self.message.lstrip().startswith(("=", "+", "-", "@"))):
            raise ValueError("invalid controlled error metadata")
        return self


class _PreviewDocument(_Document):
    version: Literal[1] = 1
    source_sha256: Digest
    payload_sha256: Digest | None
    row_count: Annotated[int, Field(strict=True, ge=0, le=MAX_ROWS)]
    errors: tuple[_ErrorDocument, ...] = Field(max_length=MAX_ERRORS)
    count: _CountDocument | None

    @model_validator(mode="after")
    def validate_consistent_preview(self):
        if self.count is not None:
            if self.payload_sha256 is None or self.row_count != self.count.observation_count:
                raise ValueError("count proof does not match parsed workbook")
            if bool(self.count.pending_verification_input_ordinals) != bool(self.errors):
                raise ValueError("pending observations do not match error state")
        elif not self.errors or self.payload_sha256 is not None:
            raise ValueError("format rejection requires errors and no count proof")
        # row_count counts nonempty rows. Source coordinates can be larger
        # when a workbook contains blank rows, so only MAX_ROWS bounds them.
        return self


class OpeningCountImportDocumentError(ValueError):
    """A stored import document is absent, corrupt or belongs to another job."""


def _parse_document(model, document):
    try:
        if type(document) is not dict or type(document.get("version")) is not int:
            raise ValueError("document object and integer version required")
        # Accept only JSON values; refuse implicit datetime/UUID/string coercion
        # and nonfinite numbers. JSON UUID strings become UUID objects below.
        payload = json.dumps(document, allow_nan=False, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("import document too large")
        return model.model_validate_json(payload, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise OpeningCountImportDocumentError("导入任务绑定或预校验记录无效") from exc


def load_import_binding(document: dict) -> OpeningCountImportBinding:
    return _parse_document(OpeningCountImportBinding, document)


def dump_import_preview(preview: OpeningCountImportBusinessPreview, *, binding: OpeningCountImportBinding) -> dict:
    if type(preview) is not OpeningCountImportBusinessPreview:
        raise OpeningCountImportDocumentError("导入任务缺少正式预校验证据")
    document = {"version": 1, **asdict(preview)}
    # Dataclasses carry UUIDs/tuples in memory. Convert only those defined
    # coordinates to JSON; arbitrary objects remain serialization errors.
    if preview.count is not None:
        for field in ("task_id", "round_id", "scope_id"):
            document["count"][field] = str(document["count"][field])
    load_import_preview(document, binding=binding)
    return json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))


def load_import_preview(document: dict, *, binding: OpeningCountImportBinding) -> OpeningCountImportBusinessPreview:
    preview = _parse_document(_PreviewDocument, document)
    if type(binding) is not OpeningCountImportBinding or preview.source_sha256 != binding.source_sha256:
        raise OpeningCountImportDocumentError("导入预校验与任务源文件不一致")
    count = preview.count
    if count is not None and (
        (count.task_id, count.round_id, count.scope_id, count.actor_authorization_version)
        != (binding.task_id, binding.round_id, binding.scope_id, binding.authorization_version)
    ):
        raise OpeningCountImportDocumentError("导入预校验与任务范围或权限版本不一致")
    return OpeningCountImportBusinessPreview(
        source_sha256=preview.source_sha256,
        payload_sha256=preview.payload_sha256,
        row_count=preview.row_count,
        errors=tuple(ImportRowError(**item.model_dump()) for item in preview.errors),
        count=OpeningStocktakeScopeCountPrevalidation(**count.model_dump()) if count is not None else None,
    )
