"""Deterministic, local-only settings for the backend test process.

Several modules construct FastAPI and SQLAlchemy singletons at import time.  Set
the explicit test boundary before pytest imports any test module so collection
order can never select production defaults or an external database.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

os.environ.update(
    {
        "OAM_ENVIRONMENT": "test",
        "OAM_DATABASE_URL": f"sqlite+pysqlite:///{ROOT / '.test_oam.db'}",
        "OAM_DATABASE_SCHEMA_MODE": "bootstrap_with_seed",
        "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "true",
        "OAM_JWT_SECRET": "test-secret-with-at-least-thirty-two-characters",
        "OAM_COOKIE_SECURE": "false",
        "OAM_ADMIN_MOBILE": "18660255681",
        "OAM_ADMIN_NAME": "系统管理员",
        "OAM_ADMIN_INITIAL_PASSWORD": "Temporary-Admin-Password-2026!",
        "OAM_UPLOAD_DIR": str(ROOT / ".test_uploads"),
        "OAM_PASSWORD_LOGIN_ENABLED": "true",
        "OAM_SMS_LOGIN_ENABLED": "true",
        "OAM_SMS_PROVIDER": "mock",
        "OAM_SMS_TEST_CODE": "246810",
        "OAM_WECHAT_LOGIN_ENABLED": "true",
        "OAM_WECHAT_PROVIDER": "mock",
        "OAM_WECHAT_APP_ID": "wx-test-rsc",
        "OAM_WECHAT_TEST_MOBILE": "18660255681",
        "OAM_EDGE_SYNC_ENABLED": "true",
        "OAM_EDGE_SYNC_SECRET": (
            "edge-sync-test-secret-with-at-least-32-characters"
        ),
        "OAM_EDGE_SYNC_ALLOWED_SOURCES": "",
        "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "true",
        "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "true",
    }
)
