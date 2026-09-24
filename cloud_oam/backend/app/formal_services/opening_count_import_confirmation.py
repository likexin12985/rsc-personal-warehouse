"""Recheck frozen import bytes and business interpretation before count writes.

This is an internal transaction primitive, not a public confirmation API.
The importer must load its authorized persisted preview, reread the private
source, and commit its job result with the returned count completion. The
preview must never be supplied by a browser. Persistence and recovery remain
the import job owner's responsibility; this function never commits.
"""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal
from .opening_count_import_prevalidation import OpeningCountImportBusinessPreview
from .opening_count_import_workbook import (
    MAX_FILE_BYTES,
    prevalidate_opening_count_workbook,
)
from .opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    OpeningStocktakeScopeCountPrevalidation,
    OpeningStocktakeScopeCountResult,
    SubmitOpeningStocktakeScopeCountCommand,
    confirm_prevalidated_opening_stocktake_scope_count,
)


class OpeningCountImportConfirmationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status_code = 412


def confirm_opening_count_import_bytes(
    db: Session,
    *,
    actor: FormalPrincipal,
    data: bytes,
    preview: OpeningCountImportBusinessPreview,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeScopeCountResult:
    """Reject any source/format/binding drift before writing a complete scope.

    Human review may be in an earlier transaction. The count service repeats
    current authority and reference checks and compares the entire proof
    under its task/reference/audit locks immediately before writing.
    """

    if (
        type(preview) is not OpeningCountImportBusinessPreview
        or not preview.ready
        or type(preview.count) is not OpeningStocktakeScopeCountPrevalidation
        or type(preview.row_count) is not int
        or preview.row_count <= 0
    ):
        raise OpeningCountImportConfirmationError(
            "opening_import_confirmation_not_ready", "导入尚未完成可确认的业务预校验",
        )
    if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES
            or sha256(data).hexdigest() != preview.source_sha256):
        raise OpeningCountImportConfirmationError(
            "opening_import_confirmation_source_changed", "导入源文件与预校验文件不一致",
        )
    workbook = prevalidate_opening_count_workbook(data)
    if (
        not workbook.ready
        or workbook.payload_sha256 != preview.payload_sha256
        or workbook.row_count != preview.row_count
        or len(workbook.observations) != preview.count.observation_count
    ):
        raise OpeningCountImportConfirmationError(
            "opening_import_confirmation_payload_changed", "导入解析结果与预校验结果不一致",
        )
    command = SubmitOpeningStocktakeScopeCountCommand(
        task_id=preview.count.task_id,
        round_id=preview.count.round_id,
        scope_id=preview.count.scope_id,
        physical_observations=tuple(
            OpeningPhysicalObservationInput(**item.model_dump(mode="python"))
            for item in workbook.observations
        ),
    )
    return confirm_prevalidated_opening_stocktake_scope_count(
        db, actor=actor, command=command,
        expected_prevalidation=preview.count,
        idempotency_key=idempotency_key, request_id=request_id,
    )
