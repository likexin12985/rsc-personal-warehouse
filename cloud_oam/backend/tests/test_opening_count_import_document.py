from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from app.formal_services.opening_count_import_document import (
    OpeningCountImportBinding, OpeningCountImportDocumentError,
    dump_import_preview, load_import_binding, load_import_preview,
)
from app.formal_services.opening_count_import_prevalidation import OpeningCountImportBusinessPreview
from app.formal_services.opening_count_import_workbook import ImportRowError
from app.formal_services.opening_stocktake_count import OpeningStocktakeScopeCountPrevalidation


@pytest.fixture
def documents():
    binding = OpeningCountImportBinding(
        source_file_id=uuid4(), source_sha256="a" * 64,
        task_id=uuid4(), round_id=uuid4(), scope_id=uuid4(),
        authorization_version=3, count_key_sha256="b" * 64,
    )
    count = OpeningStocktakeScopeCountPrevalidation(
        task_id=binding.task_id, round_id=binding.round_id, scope_id=binding.scope_id,
        task_version=0, actor_authorization_version=3, request_sha256="c" * 64,
        binding_sha256="d" * 64, observation_count=2, pending_verification_input_ordinals=(),
    )
    preview = OpeningCountImportBusinessPreview("a" * 64, "e" * 64, 2, (), count)
    return binding, preview


def test_ready_preview_survives_json_storage_without_changing_confirmation_proof(documents):
    binding, preview = documents
    stored_binding = load_import_binding(binding.model_dump(mode="json"))
    stored_preview = dump_import_preview(preview, binding=stored_binding)
    assert load_import_preview(stored_preview, binding=stored_binding) == preview
    assert stored_preview["count"]["task_version"] == 0
    assert stored_preview["count"]["pending_verification_input_ordinals"] == []


def test_sparse_source_errors_keep_the_real_excel_row(documents):
    binding, preview = documents
    errors = (ImportRowError(10001, "row", "pending_verification", "需核实"),)
    pending = replace(preview, errors=errors, count=replace(
        preview.count, pending_verification_input_ordinals=(2,),
    ))
    assert load_import_preview(dump_import_preview(pending, binding=binding), binding=binding) == pending
    invalid = replace(pending, payload_sha256=None, count=None)
    assert load_import_preview(dump_import_preview(invalid, binding=binding), binding=binding) == invalid


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", "1"), ("version", 2), ("version", None),
    ("source_file_id", "invalid"), ("source_sha256", "A" * 64),
    ("authorization_version", True), ("authorization_version", "3"),
    ("authorization_version", 0), ("count_key_sha256", "short"),
    ("unexpected", "value"),
])
def test_binding_rejects_coerced_versions_bad_coordinates_and_extra_fields(documents, field, value):
    binding, _ = documents
    stored = {**binding.model_dump(mode="json"), field: value}
    with pytest.raises(OpeningCountImportDocumentError):
        load_import_binding(stored)


@pytest.mark.parametrize("field,value", [
    ("task_id", str(uuid4())), ("round_id", str(uuid4())), ("scope_id", str(uuid4())),
    ("task_version", -1), ("task_version", True),
    ("actor_authorization_version", 4), ("observation_count", 3), ("observation_count", 1),
    ("observation_count", "2"), ("pending_verification_input_ordinals", [1]),
    ("pending_verification_input_ordinals", [2, 1]),
    ("pending_verification_input_ordinals", [1, 1]),
    ("pending_verification_input_ordinals", [3]),
    ("binding_sha256", "missing"), ("untrusted", "value"),
])
def test_preview_refuses_mismatched_or_malformed_count_proof(documents, field, value):
    binding, preview = documents
    stored = deepcopy(dump_import_preview(preview, binding=binding))
    stored["count"][field] = value
    with pytest.raises(OpeningCountImportDocumentError):
        load_import_preview(stored, binding=binding)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("source_sha256", "b" * 64), ("row_count", True),
    ("row_count", -1), ("row_count", 10001), ("count", None),
    ("payload_sha256", None), ("errors", [{"row": 2, "field": "row", "code": "invalid", "message": "=1+1"}]),
])
def test_preview_refuses_invalid_outer_document(documents, field, value):
    binding, preview = documents
    stored = {**dump_import_preview(preview, binding=binding), field: value}
    with pytest.raises(OpeningCountImportDocumentError):
        load_import_preview(stored, binding=binding)


def test_documents_do_not_accept_python_objects_or_raw_workbook_cells(documents):
    binding, preview = documents
    with pytest.raises(OpeningCountImportDocumentError):
        load_import_binding(binding.model_dump())  # UUID objects are not stored JSON.
    stored = dump_import_preview(preview, binding=binding)
    stored["cells"] = "x" * (2 * 1024 * 1024)
    with pytest.raises(OpeningCountImportDocumentError):
        load_import_preview(stored, binding=binding)
