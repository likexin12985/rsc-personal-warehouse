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
    gate._assert_fresh_disposable_postgresql16()
    gate._bootstrap_roles()
    gate._provision_edge_receiver_role()

    from pg16_stock_loss_release_checks import run

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
        )
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        for engine in engines.values():
            engine.dispose()
