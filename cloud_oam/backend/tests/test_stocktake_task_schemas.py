from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeTaskCreateIn,
    StocktakeTaskCreateOut,
    StocktakeTaskStartOut,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _scope(**changes):
    value = {
        "owner_org_id": _uuid(),
        "location_id": _uuid(),
        "assignee_person_id": _uuid(),
        "scope_mode": "location_all",
        "material_id": None,
        "condition_code": None,
        "availability_bucket": None,
        "freeze_mode": "hard",
    }
    value.update(changes)
    return value


def test_manager_task_contract_accepts_full_scope_without_server_owned_facts():
    value = StocktakeTaskCreateIn.model_validate(
        {
            "task_type": "full",
            "region_org_id": _uuid(),
            "blind_count": True,
            "scopes": [_scope()],
            "deadline": "2026-09-03T12:00:00+08:00",
            "note": "月度全盘",
        }
    )
    dumped = value.model_dump(mode="json")
    assert dumped["task_type"] == "full"
    assert "status" not in dumped
    assert "cutoff_ledger_cursor" not in dumped
    assert "book_qty" not in dumped


def test_filtered_scope_and_duplicate_dimensions_fail_closed():
    with pytest.raises(ValidationError, match="requires at least one filter"):
        StocktakeTaskCreateIn.model_validate(
            {
                "task_type": "sample",
                "region_org_id": _uuid(),
                "scopes": [_scope(scope_mode="filtered")],
            }
        )

    repeated = _scope(scope_mode="filtered", material_id=_uuid())
    with pytest.raises(ValidationError, match="duplicate dimensions"):
        StocktakeTaskCreateIn.model_validate(
            {
                "task_type": "sample",
                "region_org_id": _uuid(),
                "scopes": [repeated, {**repeated, "assignee_person_id": _uuid()}],
            }
        )


def test_full_stocktake_rejects_partial_filter_and_personal_input_has_no_identity():
    with pytest.raises(ValidationError, match="location_all"):
        StocktakeTaskCreateIn.model_validate(
            {
                "task_type": "full",
                "region_org_id": _uuid(),
                "scopes": [_scope(scope_mode="filtered", condition_code="new")],
            }
        )

    personal = PersonalStocktakeCreateIn.model_validate({"blind_count": True})
    assert set(personal.model_dump()) == {"blind_count", "freeze_mode", "note"}
    with pytest.raises(ValidationError):
        PersonalStocktakeCreateIn.model_validate(
            {"blind_count": True, "assignee_person_id": _uuid()}
        )


def test_stocktake_create_and_start_outputs_require_neutral_exact_shapes():
    task_id = _uuid()
    created = StocktakeTaskCreateOut.model_validate(
        {
            "schema_version": "1.0",
            "task_id": task_id,
            "task_no": "ST202609010001",
            "task_type": "personal",
            "status": "draft",
            "task_version": 0,
            "scope_count": 1,
            "idempotency_replayed": False,
        }
    )
    assert created.task_version == 0

    started = StocktakeTaskStartOut.model_validate(
        {
            "schema_version": "1.0",
            "task_id": task_id,
            "task_type": "personal",
            "status": "counting",
            "task_version": 1,
            "cutoff_ledger_cursor": 10,
            "initial_round_id": _uuid(),
            "scope_count": 1,
            "snapshot_line_count": 2,
            "active_freeze_count": 1,
            "idempotency_replayed": False,
        }
    )
    assert started.active_freeze_count == started.scope_count

    with pytest.raises(ValidationError, match="every started"):
        StocktakeTaskStartOut.model_validate(
            {
                **started.model_dump(mode="json"),
                "active_freeze_count": 2,
            }
        )
