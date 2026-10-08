"""Deterministic, local-only settings for the backend test process.

Several modules construct FastAPI and SQLAlchemy singletons at import time.  Set
the explicit test boundary before pytest imports any test module so collection
order can never select production defaults or an external database.
"""

from __future__ import annotations

import os
from pathlib import Path

from local_test_runtime import DATABASE_PATH, UPLOAD_PATH


ROOT = Path(__file__).resolve().parents[2]

os.environ.update(
    {
        "OAM_ENVIRONMENT": "test",
        "OAM_DATABASE_URL": f"sqlite+pysqlite:///{DATABASE_PATH}",
        "OAM_DATABASE_SCHEMA_MODE": "bootstrap_with_seed",
        "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "true",
        "OAM_JWT_SECRET": "test-secret-with-at-least-thirty-two-characters",
        "OAM_COOKIE_SECURE": "false",
        "OAM_ADMIN_MOBILE": "18660255681",
        "OAM_ADMIN_NAME": "系统管理员",
        "OAM_ADMIN_INITIAL_PASSWORD": "Temporary-Admin-Password-2026!",
        "OAM_UPLOAD_DIR": str(UPLOAD_PATH),
        # The formal client is SMS-only; historical password fields remain in
        # the schema but test runtime must not enable the retired channel.
        "OAM_PASSWORD_LOGIN_ENABLED": "false",
        "OAM_SMS_LOGIN_ENABLED": "true",
        "OAM_SMS_PROVIDER": "mock",
        "OAM_SMS_TEST_CODE": "246810",
        "OAM_WECHAT_LOGIN_ENABLED": "false",
        "OAM_WECHAT_PROVIDER": "disabled",
        "OAM_WECHAT_APP_ID": "",
        "OAM_WECHAT_TEST_MOBILE": "",
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
            from migration_script_cache import cache_migration_compilation
            with cache_migration_compilation(ROOT / "backend/alembic/versions"):
                candidate = actual_from_config(config)
                # ScriptDirectory constructs the revision map lazily. Warm
                # that immutable map while the source-bound compiler cache
                # is active, including for direct catalog tests outside env.py.
                candidate.get_heads()
            cached = candidate
        return cached

    patcher = pytest.MonkeyPatch()
    patcher.setattr(ScriptDirectory, "from_config", staticmethod(from_config))
    yield
    patcher.undo()


@pytest.fixture(scope="session", autouse=True)
def _reuse_immutable_migration_bytecode():
    """Reuse compilation, never execution results, for the reviewed revisions.

    Historical migration tests also load revisions directly through runpy,
    outside Alembic env.py and ScriptDirectory.from_config. Keep the existing
    source-hash-bound compiler cache active for those calls too. Every load
    still executes fresh globals and side effects; changes to a loaded source
    fail closed, and the loader is restored when the test session ends.
    """
    from migration_script_cache import cache_migration_compilation

    with cache_migration_compilation(ROOT / "backend/alembic/versions"):
        yield
