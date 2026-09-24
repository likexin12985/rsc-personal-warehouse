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


import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.fixture(scope="session", autouse=True)
def _reuse_unchanged_alembic_script_directory():
    """Share the read-only production migration graph within one test process.

    Migration tests still run every requested upgrade and downgrade against
    their own databases and Config objects.  Test-created script directories
    and nonstandard Alembic options keep Alembic's normal construction path.
    """
    actual_from_config = ScriptDirectory.from_config
    cached: ScriptDirectory | None = None

    def from_config(config: Config) -> ScriptDirectory:
        nonlocal cached
        location = config.get_alembic_option("script_location")
        config_file = config.config_file_name
        if (not location or not config_file
                or Path(location).resolve() != ROOT / "backend/alembic"
                or Path(config_file).resolve() != ROOT / "alembic.ini"
                or config.get_version_locations_list()
                or config.get_alembic_boolean_option("sourceless")):
            return actual_from_config(config)
        if cached is None:
            cached = actual_from_config(config)
        return cached

    patcher = pytest.MonkeyPatch()
    patcher.setattr(ScriptDirectory, "from_config", staticmethod(from_config))
    yield
    patcher.undo()
