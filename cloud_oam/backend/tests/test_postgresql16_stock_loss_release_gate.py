"""Explicit CI-only loss gate; each tracking leg owns a fresh PG16 service.

Excluded from static discovery. A missing disposable acknowledgement or invalid
matrix setting fails before database access; it must never become a green skip.
"""
import json
import os

import pytest
from sqlalchemy import create_engine

import test_postgresql16_release_gate as gate


def test_postgresql16_stock_loss_release_gate():
    if not gate._gate_enabled():
        pytest.fail("loss gate requires acknowledged disposable GitHub-hosted PostgreSQL 16")
    tracking = os.getenv("RSC_PG16_LOSS_TRACKING", "")
    if tracking not in ("quantity", "serial"):
        pytest.fail("loss gate requires an explicit quantity or serial matrix leg")
    flow = os.getenv("RSC_PG16_LOSS_FLOW", "")
    if flow not in ("submission", "submission_http", "review_seals", "disposition", "return_preview", "return_submission", "return_outbound", "return_shipment", "return_receipt", "sender_http", "sender_seals", "execution_seals", "correction_restore", "correction_used", "correction_damaged", "correction_generations", "correction_seal_retention", "execution_http_disposition", "execution_http_return", "correction_request_seals", "correction_http_sources", "return_quality_whole", "return_quality_mixed", "return_stop_seals", "return_stop_http", "return_stop_negative", "return_stop_seal_first", "return_stop_execute_first", "return_stop_stop_first", "return_stop_outbound_first"):
        pytest.fail("loss gate requires an explicit submission, submission_http, review_seals, disposition, return_preview, return_submission, return_outbound, return_shipment, return_receipt, sender_http, sender_seals, execution_seals, correction_restore, correction_used, correction_damaged, correction_generations, correction_seal_retention, execution_http_disposition, execution_http_return, correction_request_seals, correction_http_sources, return_quality_whole, return_quality_mixed, return_stop_seals, return_stop_http, return_stop_negative, return_stop_seal_first, return_stop_execute_first, return_stop_stop_first or return_stop_outbound_first matrix leg")
    gate._assert_fresh_disposable_postgresql16()
    gate._bootstrap_roles()
    gate._provision_edge_receiver_role()

    if flow in ("submission", "review_seals"):
        from pg16_stock_loss_release_checks import run
    elif flow == "submission_http":
        from pg16_stock_loss_write_http_gate import release as run
    elif flow == "disposition":
        from pg16_loss_disposition_recovery_gate import release as run
    elif flow == "return_preview":
        from pg16_stock_loss_return_preview_gate import release as run
    elif flow == "return_submission":
        from pg16_loss_disposition_recovery_gate import release as run
    elif flow == "return_outbound":
        from pg16_stock_loss_return_outbound_gate import release as run
    elif flow == "return_shipment":
        from pg16_stock_loss_return_shipment_gate import release as run
    elif flow.startswith("return_stop_"):
        from pg16_return_stop_gate import release as run
    elif flow in ("return_quality_whole", "return_quality_mixed"):
        from pg16_return_quality_gate import release as run
    elif flow == "return_receipt":
        from pg16_stock_loss_return_receipt_gate import release as run
    elif flow in ("execution_http_disposition", "execution_http_return"):
        from pg16_loss_execution_http_gate import release as run
    elif flow == "sender_http":
        from pg16_loss_sender_http_gate import release as run
    elif flow == "sender_seals":
        from pg16_loss_sender_seal_gate import release as run
    elif flow in ('correction_request_seals', 'correction_http_sources'):
        from pg16_loss_correction_request_gate import release as run
    elif flow in ('correction_generations', 'correction_seal_retention'):
        from pg16_loss_multigeneration_gate import release as run
    elif flow.startswith('correction_'):
        from pg16_loss_correction_gate import release as run
    else:
        from pg16_loss_execution_seal_gate import release as run

    engines = {}
    try:
        for role in ("star_oam_migrator", "star_oam_api", gate.EDGE_RECEIVER_ROLE):
            engines[role] = create_engine(
                gate._sqlalchemy_url(role=role, password=gate._role_password(role)),
                pool_pre_ping=True,
            )

        def migrate(label, action, revision, expected=None):
            completed = gate._run_alembic(action, revision, expect_success=expected is None)
            if expected:
                assert expected in completed.stdout + completed.stderr, label

        result = run(
            engines, tracking=tracking, migrate=migrate,
            provision=gate._provision_and_verify_deployment_acl,
            **({"scenario": flow.removeprefix("return_stop_")} if flow.startswith("return_stop_") else {}),
            **({"check_review_seals": True} if flow == "review_seals" else {}),
            **({"scenario": "whole" if flow == "return_quality_whole" else "mixed"}
               if flow in ("return_quality_whole", "return_quality_mixed") else {}),
            **({"flow": "disposition" if flow == "disposition" else "return"}
               if flow in ("disposition", "return_submission") else {}),
            **({"flow": "disposition" if flow == "execution_http_disposition" else "return"}
               if flow in ("execution_http_disposition", "execution_http_return") else {}),
            **({"correction_kind": {"correction_restore": "restore_available", "correction_used": "convert_used",
                "correction_damaged": "convert_damaged"}[flow]} if flow in ("correction_restore", "correction_used", "correction_damaged") else {}),
            **({"scenario": {"correction_generations": "generations",
                "correction_seal_retention": "seal_retention"}[flow]}
                if flow in ("correction_generations", "correction_seal_retention") else {}),
            **({'scenario': {'correction_request_seals':'seals', 'correction_http_sources':'http_sources'}[flow]}
                if flow in ('correction_request_seals','correction_http_sources') else {}),
        )
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        for engine in engines.values():
            engine.dispose()
