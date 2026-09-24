"""Compose a verified XLSX source with the read-only opening count rules.

The caller must authorize the private file and exact task/round/scope before
calling. This function neither creates a FileJob nor grants execution rights;
confirmation must reread the object and rerun the formal count command.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal
from .file_storage import FileStorageAdapter
from .opening_count_import_source import read_authorized_opening_count_source
from .opening_count_import_workbook import (
    ImportRowError,
    MAX_ERRORS,
    MAX_FILE_BYTES,
    OpeningCountImportFormatError,
    prevalidate_opening_count_workbook,
)
from .opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    OpeningStocktakeScopeCountPrevalidation,
    SubmitOpeningStocktakeScopeCountCommand,
    prevalidate_opening_stocktake_scope_count,
)


@dataclass(frozen=True, slots=True)
class OpeningCountImportBusinessPreview:
    source_sha256: str
    payload_sha256: str | None
    row_count: int
    errors: tuple[ImportRowError, ...]
    count: OpeningStocktakeScopeCountPrevalidation | None

    @property
    def ready(self) -> bool:
        return self.count is not None and self.payload_sha256 is not None and not self.errors


def prevalidate_authorized_opening_count_import(
    session_factory: Callable[[], Session],
    *,
    actor: FormalPrincipal,
    storage: FileStorageAdapter,
    file_id: UUID,
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    idempotency_key: str,
    request_id: str,
) -> OpeningCountImportBusinessPreview:
    """Read the private source, release its locks, then prove count authority.

    Source identity and business scope use distinct transactions to preserve
    the count service's task-first lock order.  This is only a preview: no
    FileJob is issued and confirmation must repeat both proofs.
    """

    with session_factory() as source_db:
        source = read_authorized_opening_count_source(
            source_db, actor=actor, file_id=file_id, storage=storage,
        )
    with session_factory() as count_db:
        return prevalidate_opening_count_import_bytes(
            count_db,
            actor=actor,
            data=source.data,
            expected_source_sha256=source.source_sha256,
            task_id=task_id,
            round_id=round_id,
            scope_id=scope_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )


def prevalidate_opening_count_import_bytes(
    db: Session,
    *,
    actor: FormalPrincipal,
    data: bytes,
    expected_source_sha256: str,
    task_id: UUID,
    round_id: UUID,
    scope_id: UUID,
    idempotency_key: str,
    request_id: str,
) -> OpeningCountImportBusinessPreview:
    """Produce no partial executable rows when format or references need work."""

    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE_BYTES:
        raise OpeningCountImportFormatError("文件大小不在允许范围内")
    if (
        not isinstance(expected_source_sha256, str)
        or len(expected_source_sha256) != 64
        or sha256(data).hexdigest() != expected_source_sha256
    ):
        raise OpeningCountImportFormatError("期初盘点源文件摘要不一致")
    workbook = prevalidate_opening_count_workbook(data)
    if not workbook.ready:
        return OpeningCountImportBusinessPreview(
            workbook.source_sha256, None, workbook.row_count, workbook.errors, None,
        )

    command = SubmitOpeningStocktakeScopeCountCommand(
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        physical_observations=tuple(
            OpeningPhysicalObservationInput(**item.model_dump(mode="python"))
            for item in workbook.observations
        ),
    )
    count = prevalidate_opening_stocktake_scope_count(
        db, actor=actor, command=command,
        idempotency_key=idempotency_key, request_id=request_id,
    )
    errors = _pending_verification_errors(
        workbook.observation_source_rows,
        count.pending_verification_input_ordinals,
    )
    return OpeningCountImportBusinessPreview(
        workbook.source_sha256, workbook.payload_sha256,
        workbook.row_count, errors, count,
    )


def _pending_verification_errors(
    source_rows: tuple[int, ...], input_ordinals: tuple[int, ...]
) -> tuple[ImportRowError, ...]:
    errors: list[ImportRowError] = []
    for ordinal in input_ordinals:
        if not 1 <= ordinal <= len(source_rows):
            raise OpeningCountImportFormatError("期初盘点预检行号与源文件不一致")
        row = source_rows[ordinal - 1]
        if len(errors) == MAX_ERRORS:
            errors[-1] = ImportRowError(
                row=row, field="row", code="error_limit_reached",
                message="仅保留前 999 项错误，其余请修正后重试",
            )
            break
        errors.append(ImportRowError(
            row=row,
            field="row",
            code="pending_verification",
            message="物料、批次或序列号尚未完成主数据核实",
        ))
    return tuple(errors)
