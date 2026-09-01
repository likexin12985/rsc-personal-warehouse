from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import stocktake_count as count_service
from app.formal_services import stocktake_close as close_service
from app.formal_services import stocktake_difference as difference_service
from app.formal_services import stocktake_query as query_service
from app.formal_services import stocktake_posting as posting_service
from app.formal_services import stocktake_recount as recount_service
from app.formal_services import stocktake_recount_count as recount_count_service
from app.formal_services import (
    stocktake_recount_difference as recount_difference_service,
)
from app.formal_services import stocktake_review as review_service
from app.formal_services import stocktake_task as task_service
from app.routers import formal_stocktakes
from app.stocktake_read_schemas import StocktakeTaskPageOut
from test_startup_security_boundary import production_settings


TASK_ID = uuid.UUID("b1000000-0000-4000-8000-000000000001")
ROUND_ID = uuid.UUID("b1000000-0000-4000-8000-000000000002")
SCOPE_ID = uuid.UUID("b1000000-0000-4000-8000-000000000003")
REGION_ID = uuid.UUID("b1000000-0000-4000-8000-000000000004")
OWNER_ID = uuid.UUID("b1000000-0000-4000-8000-000000000005")
LOCATION_ID = uuid.UUID("b1000000-0000-4000-8000-000000000006")
PERSON_ID = uuid.UUID("b1000000-0000-4000-8000-000000000007")
ACCOUNT_ID = uuid.UUID("b1000000-0000-4000-8000-000000000008")
MATERIAL_ID = uuid.UUID("b1000000-0000-4000-8000-000000000009")
DIFFERENCE_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000a")
COMPLETION_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000b")
RECONCILIATION_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000f")
CLOSE_ID = uuid.UUID("b1000000-0000-4000-8000-000000000010")
REVIEW_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000c")
RECOUNT_CASE_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000d")
RECOUNT_ROUND_ID = uuid.UUID("b1000000-0000-4000-8000-00000000000e")
SECRET = "stocktake-idempotency-secret-at-least-thirty-two-characters"


class _Db:
    def __init__(self) -> None:
        self.commit = Mock()
        self.rollback = Mock()


class _Principal:
    user_id = "engineer-001"
    person_id = PERSON_ID
    authorization_version = 7
    access_mode = "active"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.permissions = {
            ("stocktake", "read"),
            ("stocktake", "count"),
            ("stocktake", "manage"),
            ("stocktake", "review_region"),
            ("stocktake", "review_headquarters"),
            ("stocktake", "post_difference"),
            ("stocktake", "reconcile"),
            ("stocktake", "close"),
        }

    def allows(self, _db, resource: str, action: str, **_kwargs) -> bool:
        self.calls.append((resource, action))
        return (resource, action) in self.permissions


def _settings(*, enabled: bool = True, secret: str = SECRET) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic",
        stocktake_writes_enabled=enabled,
        stocktake_idempotency_hmac_secret=secret,
    )


@pytest.fixture()
def api_client():
    db = _Db()
    principal = _Principal()
    settings = _settings()
    api = FastAPI()
    api.include_router(formal_stocktakes.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    api.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api) as client:
        yield client, db, principal, settings


def _headers(suffix: str = "0001") -> dict[str, str]:
    return {
        "Idempotency-Key": f"stocktake-key-{suffix}",
        "X-Request-ID": f"stocktake-trace-{suffix}",
    }


def _managed_create_body() -> dict[str, object]:
    return {
        "task_type": "ad_hoc",
        "region_org_id": str(REGION_ID),
        "blind_count": True,
        "scopes": [
            {
                "owner_org_id": str(OWNER_ID),
                "location_id": str(LOCATION_ID),
                "assignee_person_id": str(PERSON_ID),
                "scope_mode": "location_all",
                "freeze_mode": "cutoff_replay",
            }
        ],
        "deadline": None,
        "note": "临时盘点",
    }


def _count_body() -> dict[str, object]:
    return {
        "count_mode": "blind",
        "account_counts": [
            {
                "stock_account_id": str(ACCOUNT_ID),
                "counted_qty": "2.500",
                "count_method": "scan",
                "serial_ids": [],
                "book_qty_confirmation": None,
                "reason_code": "onsite_count",
                "remark": "现场扫码",
            }
        ],
        "physical_observations": [
            {
                "material_id": str(MATERIAL_ID),
                "material_identifier_raw": "SKU-001",
                "material_identifier_type": "sku_code",
                "condition_code": "new",
                "availability_bucket": "available",
                "counted_qty": "1.000",
                "count_method": "manual",
                "reason_code": "unexpected_material",
                "remark": "现场多出",
            }
        ],
        "evidence_file_ids": [],
        "zero_confirmed": False,
    }


def _review_body() -> dict[str, object]:
    return {
        "expected_task_version": 3,
        "decision": "approve",
        "items": [
            {
                "difference_id": str(DIFFERENCE_ID),
                "decision": "accept_for_posting",
                "comment": "证据一致",
            }
        ],
        "comment": "",
    }


def test_list_is_scope_service_only_and_never_commits(api_client, monkeypatch):
    client, db, principal, _settings_value = api_client
    service = Mock(return_value=StocktakeTaskPageOut(items=(), next_after_id=None))
    monkeypatch.setattr(query_service, "list_stocktake_tasks", service)

    response = client.get("/api/v1/stocktakes?limit=20")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "items": [],
        "next_after_id": None,
    }
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    service.assert_called_once_with(db, actor=principal, limit=20, after_id=None)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_managed_create_projects_only_business_input_and_commits_once(
    api_client, monkeypatch
):
    client, db, principal, _settings_value = api_client
    service = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            task_no="ST-20260901-0001",
            task_type="ad_hoc",
            status="draft",
            version=0,
            scope_count=1,
            replayed=False,
        )
    )
    monkeypatch.setattr(task_service, "create_stocktake_task_draft", service)

    response = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers=_headers(),
    )

    assert response.status_code == 201
    assert response.json()["task_id"] == str(TASK_ID)
    assert response.json()["task_version"] == 0
    assert response.headers["idempotency-replayed"] == "false"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    call = service.call_args
    assert call.kwargs["actor"] is principal
    assert call.kwargs["idempotency_key"] == "stocktake-key-0001"
    assert call.kwargs["idempotency_hmac_secret"] == SECRET
    assert call.kwargs["trace_request_id"] == "stocktake-trace-0001"
    assert call.kwargs["draft"].scopes[0].assignee_person_id == PERSON_ID
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_personal_create_and_start_use_count_permission(api_client, monkeypatch):
    client, db, principal, _settings_value = api_client
    create = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            task_no="ST-SELF-0001",
            task_type="personal",
            status="draft",
            version=0,
            scope_count=1,
            replayed=True,
        )
    )
    start = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            task_type="personal",
            status="counting",
            version=1,
            cutoff_ledger_cursor=9,
            initial_round_id=ROUND_ID,
            scope_count=1,
            snapshot_line_count=2,
            active_freeze_count=1,
            replayed=False,
        )
    )
    monkeypatch.setattr(task_service, "create_personal_stocktake_draft", create)
    monkeypatch.setattr(task_service, "start_stocktake_task", start)

    created = client.post(
        "/api/v1/stocktakes/personal",
        json={"blind_count": True, "freeze_mode": "cutoff_replay", "note": ""},
        headers=_headers("0002"),
    )
    started = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/start",
        json={"expected_version": 0},
        headers=_headers("0003"),
    )

    assert created.status_code == 201
    assert created.headers["idempotency-replayed"] == "true"
    assert started.status_code == 200
    assert started.json()["cutoff_ledger_cursor"] == 9
    assert ("stocktake", "count") in principal.calls
    assert db.commit.call_count == 2


def test_initial_count_maps_decimal_and_never_accepts_server_owned_fields(
    api_client, monkeypatch
):
    client, db, _principal, _settings_value = api_client
    service = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            round_id=ROUND_ID,
            scope_id=SCOPE_ID,
            task_status="submitted",
            round_status="submitted",
            task_version=2,
            scope_completed=True,
            round_submitted=True,
            evidence_file_count=0,
            replayed=False,
        )
    )
    monkeypatch.setattr(count_service, "submit_stocktake_initial_scope_count", service)

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/initial-count",
        json=_count_body(),
        headers=_headers("0004"),
    )

    assert response.status_code == 200
    command = service.call_args.kwargs["command"]
    assert command.count_mode == "blind"
    assert command.account_counts[0].counted_qty == Decimal("2.500")
    assert command.physical_observations[0].counted_qty == Decimal("1.000")
    assert command.task_id == TASK_ID
    assert response.json()["round_submitted"] is True
    db.commit.assert_called_once_with()

    bad_body = _count_body()
    bad_body["cutoff_ledger_cursor"] = 99
    rejected = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/initial-count",
        json=bad_body,
        headers=_headers("0005"),
    )
    assert rejected.status_code == 422
    assert service.call_count == 1


def test_difference_and_both_reviews_are_independent_transitions(
    api_client, monkeypatch
):
    client, db, _principal, _settings_value = api_client
    difference = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            round_id=ROUND_ID,
            completion_id=COMPLETION_ID,
            task_status="region_review",
            round_status="submitted",
            task_version=3,
            difference_status="evaluated",
            difference_count=1,
            physical_difference_count=1,
            pending_observation_difference_count=0,
            total_affected_qty=Decimal("1.000"),
            difference_manifest_sha256="a" * 64,
            replayed=False,
        )
    )
    region = Mock(
        return_value=SimpleNamespace(
            review_id=REVIEW_ID,
            task_id=TASK_ID,
            round_id=ROUND_ID,
            review_stage="region",
            decision="approve",
            resulting_task_status="hq_review",
            task_version=4,
            item_count=1,
            pending_verification_count=0,
            ready_for_posting=False,
            replayed=False,
        )
    )
    headquarters = Mock(
        return_value=SimpleNamespace(
            review_id=uuid.uuid4(),
            task_id=TASK_ID,
            round_id=ROUND_ID,
            review_stage="headquarters",
            decision="approve",
            resulting_task_status="approved",
            task_version=5,
            item_count=1,
            pending_verification_count=0,
            ready_for_posting=True,
            replayed=False,
        )
    )
    monkeypatch.setattr(
        difference_service, "generate_stocktake_initial_differences", difference
    )
    monkeypatch.setattr(review_service, "submit_stocktake_region_review", region)
    monkeypatch.setattr(
        review_service, "submit_stocktake_headquarters_review", headquarters
    )

    diff_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/differences",
        json={"expected_task_version": 2},
        headers=_headers("0006"),
    )
    region_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/reviews/region",
        json=_review_body(),
        headers=_headers("0007"),
    )
    headquarters_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/reviews/headquarters",
        json={**_review_body(), "expected_task_version": 4},
        headers=_headers("0008"),
    )

    assert diff_response.status_code == 200
    assert diff_response.json()["total_affected_qty"] == "1.000"
    assert region_response.status_code == 200
    assert region_response.json()["ready_for_posting"] is False
    assert headquarters_response.status_code == 200
    assert headquarters_response.json()["ready_for_posting"] is True
    assert region.call_args.kwargs["command"].expected_task_version == 3
    assert headquarters.call_args.kwargs["command"].expected_task_version == 4
    assert db.commit.call_count == 3


def test_difference_posting_is_an_independent_strict_posted_transition(
    api_client, monkeypatch
):
    client, db, principal, _settings_value = api_client
    service = Mock(
        return_value=SimpleNamespace(
            completion_id=COMPLETION_ID,
            task_id=TASK_ID,
            terminal_round_id=ROUND_ID,
            resulting_task_status="posted",
            task_version=6,
            scope_count=2,
            difference_count=2,
            accepted_difference_count=1,
            no_adjustment_count=1,
            transaction_count=1,
            movement_count=1,
            total_quantity=Decimal("1.000"),
            first_ledger_cursor=20,
            last_ledger_cursor=20,
            replayed=False,
        )
    )
    monkeypatch.setattr(
        posting_service, "post_approved_stocktake_differences", service
    )

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/post-differences",
        json={"expected_task_version": 5},
        headers=_headers("0020"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "completion_id": str(COMPLETION_ID),
        "task_id": str(TASK_ID),
        "terminal_round_id": str(ROUND_ID),
        "resulting_task_status": "posted",
        "task_version": 6,
        "scope_count": 2,
        "difference_count": 2,
        "accepted_difference_count": 1,
        "no_adjustment_count": 1,
        "transaction_count": 1,
        "movement_count": 1,
        "total_quantity": "1.000",
        "first_ledger_cursor": 20,
        "last_ledger_cursor": 20,
        "replayed": False,
    }
    command = service.call_args.kwargs["command"]
    assert command.task_id == TASK_ID
    assert command.expected_task_version == 5
    assert service.call_args.kwargs["actor"] is principal
    assert service.call_args.kwargs["idempotency_key"] == "stocktake-key-0020"
    assert service.call_args.kwargs["idempotency_hmac_secret"] == SECRET
    assert service.call_args.kwargs["trace_request_id"] == "stocktake-trace-0020"
    assert ("stocktake", "post_difference") in principal.calls
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()

    spoofed = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/post-differences",
        json={
            "expected_task_version": 5,
            "resulting_task_status": "closed",
        },
        headers=_headers("0021"),
    )
    coerced = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/post-differences",
        json={"expected_task_version": "5"},
        headers=_headers("0022"),
    )
    assert spoofed.status_code == 422
    assert coerced.status_code == 422
    assert service.call_count == 1


def test_difference_posting_requires_dedicated_permission(api_client, monkeypatch):
    client, db, principal, _settings_value = api_client
    service = Mock()
    monkeypatch.setattr(
        posting_service, "post_approved_stocktake_differences", service
    )
    principal.permissions.remove(("stocktake", "post_difference"))

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/post-differences",
        json={"expected_task_version": 5},
        headers=_headers("0023"),
    )

    assert response.status_code == 403
    assert service.call_count == 0
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_difference_posting_domain_error_rolls_back_without_closing(
    api_client, monkeypatch
):
    client, db, _principal, _settings_value = api_client
    monkeypatch.setattr(
        posting_service,
        "post_approved_stocktake_differences",
        Mock(
            side_effect=posting_service.StocktakeDifferencePostingError(
                "stocktake_posting_task_not_postable",
                "precondition_failed",
                "盘点任务尚未满足安全过账条件",
            )
        ),
    )

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/post-differences",
        json={"expected_task_version": 5},
        headers=_headers("0024"),
    )

    assert response.status_code == 412
    assert response.json() == {
        "detail": {
            "code": "stocktake_posting_task_not_postable",
            "category": "precondition_failed",
            "message": "盘点任务尚未满足安全过账条件",
        }
    }
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_reconcile_and_close_are_two_strict_independent_api_transitions(
    api_client, monkeypatch
):
    client, db, principal, _settings_value = api_client
    occurred_at = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    reconcile = Mock(
        return_value=SimpleNamespace(
            completion_id=RECONCILIATION_ID,
            task_id=TASK_ID,
            posting_completion_id=COMPLETION_ID,
            reconciliation_no=1,
            reconciliation_ledger_cursor=20,
            resulting_task_status="posted",
            task_version=7,
            scope_count=2,
            account_count=3,
            scoped_account_count=2,
            serial_count=1,
            transaction_count=4,
            movement_count=5,
            book_total_qty=Decimal("8.000"),
            physical_total_qty=Decimal("8.000"),
            reconciled_at=occurred_at,
            replayed=False,
        )
    )
    close = Mock(
        return_value=SimpleNamespace(
            completion_id=CLOSE_ID,
            task_id=TASK_ID,
            reconciliation_completion_id=RECONCILIATION_ID,
            reconciliation_no=1,
            reconciliation_ledger_cursor=20,
            resulting_task_status="closed",
            task_version=8,
            closed_at=occurred_at + timedelta(minutes=1),
            replayed=False,
        )
    )
    monkeypatch.setattr(close_service, "reconcile_posted_stocktake_for_close", reconcile)
    monkeypatch.setattr(close_service, "close_reconciled_stocktake", close)

    reconcile_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/reconcile",
        json={"expected_task_version": 6},
        headers=_headers("0030"),
    )
    close_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/close",
        json={"expected_task_version": 7},
        headers=_headers("0031"),
    )

    assert reconcile_response.status_code == 200
    assert reconcile_response.json()["resulting_task_status"] == "posted"
    assert reconcile_response.json()["completion_id"] == str(RECONCILIATION_ID)
    assert reconcile_response.json()["book_total_qty"] == "8.000"
    assert close_response.status_code == 200
    assert close_response.json()["resulting_task_status"] == "closed"
    assert close_response.json()["reconciliation_completion_id"] == str(
        RECONCILIATION_ID
    )
    assert reconcile.call_args.kwargs["command"].expected_task_version == 6
    assert close.call_args.kwargs["command"].expected_task_version == 7
    assert reconcile.call_args.kwargs["trace_request_id"] == "stocktake-trace-0030"
    assert close.call_args.kwargs["trace_request_id"] == "stocktake-trace-0031"
    assert ("stocktake", "reconcile") in principal.calls
    assert ("stocktake", "close") in principal.calls
    assert db.commit.call_count == 2
    db.rollback.assert_not_called()


@pytest.mark.parametrize(
    ("path", "permission", "service_name"),
    (
        ("reconcile", ("stocktake", "reconcile"), "reconcile_posted_stocktake_for_close"),
        ("close", ("stocktake", "close"), "close_reconciled_stocktake"),
    ),
)
def test_reconcile_and_close_each_require_their_exact_permission(
    api_client, monkeypatch, path, permission, service_name
):
    client, db, principal, _settings_value = api_client
    service = Mock()
    monkeypatch.setattr(close_service, service_name, service)
    principal.permissions.remove(permission)

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/{path}",
        json={"expected_task_version": 6},
        headers=_headers(f"003-{path}"),
    )

    assert response.status_code == 403
    service.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_close_domain_error_rolls_back_and_does_not_call_reconcile(
    api_client, monkeypatch
):
    client, db, _principal, _settings_value = api_client
    reconcile = Mock()
    close = Mock(
        side_effect=close_service.StocktakeCloseError(
            "stocktake_close_reconciliation_stale",
            "precondition_failed",
            "内部对账后库存总账已变化，请重新执行内部对账",
        )
    )
    monkeypatch.setattr(close_service, "reconcile_posted_stocktake_for_close", reconcile)
    monkeypatch.setattr(close_service, "close_reconciled_stocktake", close)

    response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/close",
        json={"expected_task_version": 7},
        headers=_headers("0032"),
    )

    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "stocktake_close_reconciliation_stale"
    close.assert_called_once()
    reconcile.assert_not_called()
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_recount_open_count_and_difference_are_three_independent_transitions(
    api_client, monkeypatch
):
    client, db, principal, _settings_value = api_client
    opened = Mock(
        return_value=SimpleNamespace(
            recount_case_id=RECOUNT_CASE_ID,
            task_id=TASK_ID,
            source_round_id=ROUND_ID,
            next_round_id=RECOUNT_ROUND_ID,
            next_round_no=2,
            scope_count=1,
            assignment_count=1,
            resulting_task_status="counting",
            task_version=6,
            replayed=False,
        )
    )
    counted = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            round_id=RECOUNT_ROUND_ID,
            scope_id=SCOPE_ID,
            recount_case_id=RECOUNT_CASE_ID,
            task_status="submitted",
            round_status="submitted",
            task_version=7,
            scope_completed=True,
            round_submitted=True,
            count_ledger_cursor=19,
            evidence_file_count=0,
            replayed=False,
        )
    )
    evaluated = Mock(
        return_value=SimpleNamespace(
            task_id=TASK_ID,
            round_id=RECOUNT_ROUND_ID,
            recount_case_id=RECOUNT_CASE_ID,
            completion_id=COMPLETION_ID,
            task_status="region_review",
            round_status="submitted",
            task_version=8,
            difference_status="evaluated",
            difference_count=0,
            physical_difference_count=0,
            pending_observation_difference_count=0,
            total_affected_qty=Decimal("0.000"),
            difference_manifest_sha256="b" * 64,
            selected_scope_count=1,
            replayed=False,
        )
    )
    monkeypatch.setattr(recount_service, "open_stocktake_recount", opened)
    monkeypatch.setattr(
        recount_count_service, "submit_stocktake_recount_scope_count", counted
    )
    monkeypatch.setattr(
        recount_difference_service,
        "generate_stocktake_recount_differences",
        evaluated,
    )

    open_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/recount",
        json={
            "expected_task_version": 5,
            "assignments": [
                {"scope_id": str(SCOPE_ID), "assignee_user_id": "engineer-001"}
            ],
            "reason": "区域复核要求精确范围复盘",
        },
        headers=_headers("0016"),
    )
    count_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{RECOUNT_ROUND_ID}/scopes/{SCOPE_ID}/recount-count",
        json=_count_body(),
        headers=_headers("0017"),
    )
    difference_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{RECOUNT_ROUND_ID}/recount-differences",
        json={"expected_task_version": 7},
        headers=_headers("0018"),
    )

    assert open_response.status_code == 200
    assert open_response.json()["next_round_no"] == 2
    assert count_response.status_code == 200
    assert count_response.json()["count_ledger_cursor"] == 19
    assert difference_response.status_code == 200
    assert difference_response.json()["selected_scope_count"] == 1
    assert difference_response.json()["total_affected_qty"] == "0.000"
    assert opened.call_args.kwargs["command"].assignments[0].scope_id == SCOPE_ID
    assert counted.call_args.kwargs["command"].account_counts[0].counted_qty == (
        Decimal("2.500")
    )
    assert evaluated.call_args.kwargs["command"].expected_task_version == 7
    assert ("stocktake", "manage") in principal.calls
    assert ("stocktake", "count") in principal.calls
    assert db.commit.call_count == 3

    duplicate = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/recount",
        json={
            "expected_task_version": 5,
            "assignments": [
                {"scope_id": str(SCOPE_ID), "assignee_user_id": "engineer-001"},
                {"scope_id": str(SCOPE_ID), "assignee_user_id": "engineer-002"},
            ],
            "reason": "重复范围必须拒绝",
        },
        headers=_headers("0019"),
    )
    assert duplicate.status_code == 422
    assert opened.call_count == 1


def test_write_gate_headers_and_secret_domain_fail_closed(api_client, monkeypatch):
    client, db, _principal, settings = api_client
    service = Mock()
    monkeypatch.setattr(task_service, "create_stocktake_task_draft", service)

    settings.stocktake_writes_enabled = False
    disabled = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers=_headers("0009"),
    )
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "stocktake_writes_disabled"

    settings.stocktake_writes_enabled = True
    missing_header = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers={"X-Request-ID": "stocktake-trace-0010"},
    )
    assert missing_header.status_code == 400
    assert missing_header.json()["detail"]["code"] == "idempotency_key_invalid"

    settings.stocktake_idempotency_hmac_secret = "short"
    unavailable = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers=_headers("0011"),
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == (
        "stocktake_write_security_unavailable"
    )

    settings.stocktake_idempotency_hmac_secret = SECRET
    settings.jwt_secret = SECRET
    reused = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers=_headers("0012"),
    )
    assert reused.status_code == 503
    assert reused.json()["detail"]["code"] == (
        "stocktake_write_secret_reuse_forbidden"
    )
    service.assert_not_called()
    db.commit.assert_not_called()


def test_service_error_rolls_back_without_database_detail(api_client, monkeypatch):
    client, db, _principal, _settings_value = api_client
    monkeypatch.setattr(
        task_service,
        "create_stocktake_task_draft",
        Mock(
            side_effect=task_service.StocktakeTaskError(
                "stocktake_concurrent_conflict",
                "conflict",
                "盘点任务发生并发冲突，请重新读取",
            )
        ),
    )

    response = client.post(
        "/api/v1/stocktakes",
        json=_managed_create_body(),
        headers=_headers("0013"),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "stocktake_concurrent_conflict",
            "category": "conflict",
            "message": "盘点任务发生并发冲突，请重新读取",
        }
    }
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_schema_rejects_non_string_quantity_and_blind_book_echo(api_client):
    client, db, _principal, _settings_value = api_client
    number_body = _count_body()
    number_body["account_counts"][0]["counted_qty"] = 2.5
    number_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/initial-count",
        json=number_body,
        headers=_headers("0014"),
    )
    assert number_response.status_code == 422

    blind_echo = _count_body()
    blind_echo["account_counts"][0]["book_qty_confirmation"] = "2.500"
    echo_response = client.post(
        f"/api/v1/stocktakes/{TASK_ID}/rounds/{ROUND_ID}/scopes/{SCOPE_ID}/initial-count",
        json=blind_echo,
        headers=_headers("0015"),
    )
    assert echo_response.status_code == 422
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_config_defaults_and_enabled_production_secret_requirement():
    fields = Settings.model_fields
    assert fields["stocktake_writes_enabled"].default is False
    assert fields["stocktake_idempotency_hmac_secret"].default == ""

    ready = production_settings(
        stocktake_writes_enabled=True,
        stocktake_idempotency_hmac_secret=(
            "production-stocktake-idempotency-secret-at-least-32-characters"
        ),
    )
    ready.validate_api_startup()

    with pytest.raises(ValueError, match="stocktake writes require"):
        production_settings(
            stocktake_writes_enabled=True,
            stocktake_idempotency_hmac_secret="",
        ).validate_api_startup()
    with pytest.raises(ValueError, match="must be pairwise distinct"):
        production_settings(
            stocktake_writes_enabled=True,
            stocktake_idempotency_hmac_secret=(
                "production-test-secret-with-at-least-32-characters"
            ),
        ).validate_api_startup()
