from dataclasses import replace
from hashlib import md5, sha256
from io import BytesIO
from uuid import uuid4

from openpyxl import load_workbook
import pytest
from pydantic import ValidationError

from formal_file_integrity import (
    OPENING_IMPORT_ERROR_MAX_BYTES, FormalFileError, FileUploadIntentInput,
    StoredObjectHead, _prepare_upload,
)
from app.formal_file_schemas import FileUploadIntentIn
from app.formal_services.formal_files import _require_upload_permission
from app.formal_services.file_storage import FileStorageError
from app.formal_services.opening_count_import_document import dump_import_preview
from app.formal_services.opening_count_import_error_report import (
    MIME, PURPOSE, build_opening_count_error_artifact, write_or_recover_opening_count_error,
)
from app.formal_services.opening_count_import_workbook import ImportRowError
from test_opening_count_import_document import documents
from test_report_object_storage import _adapter, FILE_ID


@pytest.fixture
def artifact(documents):
    binding, preview = documents
    preview = replace(preview, errors=(ImportRowError(7, "row", "pending_verification", "物料尚未核实"),),
                      count=replace(preview.count, pending_verification_input_ordinals=(2,)))
    document = dump_import_preview(preview, binding=binding)
    arguments = dict(job_id=uuid4(), binding_document=binding.model_dump(mode="json"), preview_document=document)
    return build_opening_count_error_artifact(**arguments), arguments


def test_same_job_and_preview_recreate_identical_error_bytes_and_private_key(artifact):
    output, arguments = artifact
    assert build_opening_count_error_artifact(**arguments) == output
    other = build_opening_count_error_artifact(**{**arguments, "job_id": uuid4()})
    assert other.file_id != output.file_id and other.storage_key != output.storage_key
    assert other.payload == output.payload
    assert output.sha256 == sha256(output.payload).hexdigest()
    book = load_workbook(BytesIO(output.payload), read_only=True, data_only=False)
    try:
        assert tuple(book.active.values)[1] == ("期初盘点_V1", 7, "row", "pending_verification", "物料尚未核实")
    finally:
        book.close()


def test_ready_preview_is_not_an_error_artifact(documents):
    binding, preview = documents
    with pytest.raises(ValueError, match="没有可生成"):
        build_opening_count_error_artifact(job_id=uuid4(), binding_document=binding.model_dump(mode="json"),
            preview_document=dump_import_preview(preview, binding=binding))


class Storage:
    provider_code = "aliyun_oss_v2"

    def __init__(self, artifact):
        self.calls = []
        self.head = StoredObjectHead(artifact.storage_key, len(artifact.payload), MIME,
            {"sha256": artifact.sha256, "file-id": str(artifact.file_id)}, md5(artifact.payload).hexdigest())

    def put_opening_count_error(self, **binding):
        self.calls.append(("put", binding))
        return self.head

    def head_object(self, **binding):
        self.calls.append(("head", binding))
        return self.head


def test_lost_database_ack_recovery_only_reads_original_object(artifact):
    output, _ = artifact
    storage = Storage(output)
    assert write_or_recover_opening_count_error(storage, artifact=output, allow_new_put=True) == storage.head
    assert write_or_recover_opening_count_error(storage, artifact=output, allow_new_put=False) == storage.head
    assert [name for name, _ in storage.calls] == ["put", "head"]
    assert storage.calls[1][1] == {"storage_key": output.storage_key}


@pytest.mark.parametrize("field,value", [
    ("storage_key", "other/key"), ("size_bytes", 1), ("size_bytes", True),
    ("mime_type", "application/pdf"), ("etag", "multipart-etag-2"),
    ("metadata", {"sha256": "0" * 64, "file-id": str(uuid4())}),
])
def test_error_recovery_rejects_mismatched_head_without_reupload(artifact, field, value):
    output, _ = artifact
    storage = Storage(output)
    storage.head = replace(storage.head, **{field: value})
    with pytest.raises((FormalFileError, FileStorageError)):
        write_or_recover_opening_count_error(storage, artifact=output, allow_new_put=False)
    assert [name for name, _ in storage.calls] == ["head"]


def test_duplicate_metadata_alias_is_not_trusted(artifact):
    output, _ = artifact
    storage = Storage(output)
    storage.head = replace(storage.head, metadata={**storage.head.metadata, "X-OSS-Meta-Sha256": output.sha256})
    with pytest.raises(FormalFileError):
        write_or_recover_opening_count_error(storage, artifact=output, allow_new_put=False)


def test_error_purpose_is_worker_only_and_size_bounded_before_network():
    value = dict(purpose=PURPOSE, original_filename="errors.xlsx", size_bytes=OPENING_IMPORT_ERROR_MAX_BYTES + 1,
                 mime_type=MIME, sha256="a" * 64)
    with pytest.raises(ValidationError):
        FileUploadIntentIn(**value)
    with pytest.raises(FormalFileError, match="后台任务"):
        _require_upload_permission(None, None, PURPOSE)
    with pytest.raises(FormalFileError, match="大小"):
        _prepare_upload(FileUploadIntentInput(**value), maximum_size_bytes=120 * 1024 * 1024)
    adapter, calls = _adapter()
    payload = b"x" * (OPENING_IMPORT_ERROR_MAX_BYTES + 1)
    key = "formal-files/v1/opening_count_import_error/00/" + FILE_ID.replace("-", "")
    with pytest.raises(FileStorageError):
        adapter.put_opening_count_error(storage_key=key, file_id=FILE_ID,
                                       sha256=sha256(payload).hexdigest(), payload=payload)
    assert calls == []
