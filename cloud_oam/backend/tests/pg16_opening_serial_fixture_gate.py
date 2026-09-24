"""Preserve cross-table SN and QR alias DB rejection on an immutable SN SKU."""
from decimal import Decimal
from unittest.mock import patch
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session
import pytest
from app.formal_access import load_formal_principal
from app.formal_services import opening_stocktake_count as opening_count_service
from app.formal_services.opening_stocktake_count import (
    OpeningStocktakeCountError, OpeningPhysicalObservationInput, SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from pg16_release_gate_diagnostics import SanitizedPostgreSQLDiagnosticError, run_with_sanitized_database_diagnostics


def assert_duplicate_serial_guards(api_engine, *, fixture, assignee_user_id, task_id, round_id, scope_id, snapshot):
    opening_token = str(fixture["opening_token"])
    def current_principal(session, user_id):
        return load_formal_principal(session, user_id, now=session.scalar(select(func.clock_timestamp())))
    def submit_cross_table_duplicate_serial(task_id, round_id, scope_id) -> None:
        command = SubmitOpeningStocktakeScopeCountCommand(
            task_id=task_id,
            round_id=round_id,
            scope_id=scope_id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["concurrency_material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["concurrency_serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 已知账户 SN",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["concurrency_material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["concurrency_serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 无账户观察 SN",
                ),
            ),
            zero_confirmed=False,
        )
        with Session(api_engine, expire_on_commit=False) as session:
            with patch.object(
                opening_count_service,
                "_validate_round_serial_uniqueness",
                return_value=None,
            ):
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=command,
                    idempotency_key=(
                        "pg16-opening-count-cross-table-serial-"
                        f"{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-cross-table-serial-"
                        f"{opening_token}"
                    ),
                )
            session.commit()

    def submit_cross_alias_duplicate_serial(task_id, round_id, scope_id) -> None:
        command = SubmitOpeningStocktakeScopeCountCommand(
            task_id=task_id,
            round_id=round_id,
            scope_id=scope_id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["concurrency_material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["concurrency_serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 已知账户 SN 别名",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=(
                        f"PG16-UNKNOWN-MATERIAL-{opening_token[:12]}"
                    ),
                    material_identifier_type="unknown",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["concurrency_serial_qr_code"]).lower(),
                    serial_identifier_type="qr_code",
                    count_method="manual",
                    remark="PG16 0052 pending QR 大小写别名",
                ),
            ),
            zero_confirmed=False,
        )
        with Session(api_engine, expire_on_commit=False) as session:
            with patch.object(
                opening_count_service,
                "_validate_round_serial_uniqueness",
                return_value=None,
            ):
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=command,
                    idempotency_key=(
                        "pg16-opening-count-cross-alias-serial-"
                        f"{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-cross-alias-serial-"
                        f"{opening_token}"
                    ),
                )
            session.commit()

    before = snapshot(api_engine)
    for probe in (submit_cross_table_duplicate_serial, submit_cross_alias_duplicate_serial):
        with pytest.raises(SanitizedPostgreSQLDiagnosticError) as rejected:
            run_with_sanitized_database_diagnostics(api_engine,
                lambda: probe(task_id, round_id, scope_id), replace_unlinked_database_failure_when=lambda failure:
                    isinstance(failure, OpeningStocktakeCountError) and failure.code in (
                        'opening_count_concurrent_conflict', 'opening_count_database_guard_rejected'))
        assert rejected.value.diagnostic.sqlstate == "23514"
        diagnostic = rejected.value.diagnostic
        assert diagnostic.plpgsql_function.split('.')[-1] == 'rsc_require_opening_count_write_current_0052'
        with api_engine.connect() as db:
            body = db.scalar(text("SELECT prosrc FROM pg_proc WHERE oid='public.rsc_require_opening_count_write_current_0052()'::regprocedure"))
        assert '0052 opening round serial is claimed by both count paths' in body.splitlines()[diagnostic.plpgsql_line - 1]
        assert snapshot(api_engine) == before
