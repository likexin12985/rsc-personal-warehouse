"""Four real 0180 downgrade refusals on the caller-owned legacy-only fixture.

No connection discovery, bootstrap or execution happens on import. Every
synthetic corruption, trigger toggle and attempted downgrade is rolled back.
This phase must run before the binding gate adds any OpenBao pins.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy import text

from app import key_provider_binding_security as security
from app.stock_scrap_security_probe import snapshot
from pg16_key_provider_binding_gate import (
    AUTH, HEAD, NOW, OWNER, TABLES, KeyProviderBindingGateError,
    _identity, _limits, _require, _snapshot,
)


PATH = Path(__file__).resolve().parents[1] / "alembic/versions/20261229_0180_openbao_data_key_pins.py"
CASES = ("missing", "hash", "time", "orphan")


def _migration():
    spec = importlib.util.spec_from_file_location("key_provider_legacy_downgrade_0180", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _require(module.revision == HEAD, "legacy_downgrade_migration_identity")
    return module


def _facts(db):
    return {table: [dict(row) for row in db.execute(text(
        "SELECT * FROM public." + table + " ORDER BY purpose,application_key_version"
    )).mappings()] for table in TABLES}


def _same_catalog(db):
    _require(snapshot(db, table_names=security.OBSERVED_TABLES,
        function_names=security.FUNCTIONS) == security.DATA,
        "legacy_downgrade_catalog_changed")
    _require(db.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all() == [HEAD],
        "legacy_downgrade_head_changed")


def _online(db, migration, *, downgrade):
    # Use Alembic's real online context. No monkeypatch of context.is_offline_mode,
    # migration guards or SQL implementation is required.
    with EnvironmentContext(Config(), None) as environment:
        environment.configure(connection=db)
        _require(not environment.is_offline_mode(), "legacy_downgrade_online_context")
        with Operations.context(environment.get_context()):
            if downgrade:
                migration.downgrade()
            else:
                migration._check_readiness_source(False)


def _corrupt(db, case, original):
    expected = deepcopy(original)
    if case == "orphan":
        guard = "trg_application_key_version_claims_insert_0180"
        db.execute(text("ALTER TABLE public.application_key_version_claims DISABLE TRIGGER " + guard))
        values = dict(purpose=AUTH, application_key_version=1_800_901,
            provider="aliyun_kms", ciphertext_sha256="e" * 64, created_at=NOW)
        changed = db.execute(text("INSERT INTO public.application_key_version_claims "
            "(purpose,application_key_version,provider,ciphertext_sha256,created_at) "
            "VALUES (:purpose,:application_key_version,:provider,:ciphertext_sha256,:created_at)"), values)
        expected[TABLES[2]].append(values)
    else:
        guard = "trg_application_key_version_claims_immutable_0180"
        db.execute(text("ALTER TABLE public.application_key_version_claims DISABLE TRIGGER " + guard))
        where = " WHERE purpose='authentication_idempotency' AND application_key_version=1"
        if case == "missing":
            changed = db.execute(text("DELETE FROM public.application_key_version_claims" + where))
            expected[TABLES[2]] = []
        elif case == "hash":
            changed = db.execute(text("UPDATE public.application_key_version_claims SET ciphertext_sha256=:digest" + where), {"digest": "e" * 64})
            expected[TABLES[2]][0]["ciphertext_sha256"] = "e" * 64
        else:
            _require(case == "time", "legacy_downgrade_case")
            from datetime import timedelta
            changed = db.execute(text("UPDATE public.application_key_version_claims SET created_at=created_at+interval '1 second'" + where))
            expected[TABLES[2]][0]["created_at"] += timedelta(seconds=1)
    _require(changed.rowcount == 1, "legacy_downgrade_fixture_rowcount")
    db.execute(text("ALTER TABLE public.application_key_version_claims ENABLE ALWAYS TRIGGER " + guard))
    _require(_facts(db) == expected and expected[TABLES[1]] == [], "legacy_downgrade_exact_corruption")
    _same_catalog(db)
    return expected


def run(engines):
    """Call the unchanged production downgrade; every negative is rollback-only."""
    stage = "preflight"
    try:
        _identity(engines)
        original = _snapshot(engines[OWNER])
        migration = _migration()
        with engines["star_oam_api"].connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            _limits(db)
            security.verify(db)
        for case in CASES:
            stage = case
            with engines[OWNER].connect() as db:
                transaction = db.begin()
                try:
                    _limits(db)
                    expected_corruption = _corrupt(db, case, original)
                    refused = False
                    savepoint = db.begin_nested()
                    try:
                        _online(db, migration, downgrade=True)
                    except RuntimeError as error:
                        _require(type(error) is RuntimeError and str(error) == migration.DOWNGRADE_BLOCKER,
                            "legacy_downgrade_exact_blocker")
                        refused = True
                    finally:
                        savepoint.rollback()
                    _require(refused, "legacy_downgrade_was_admitted")
                    _same_catalog(db)
                    _online(db, migration, downgrade=False)
                    _require(_facts(db) == expected_corruption, "legacy_downgrade_changed_corrupt_fixture")
                finally:
                    transaction.rollback()
            # Fresh connections provide authoritative post-rollback evidence.
            _identity(engines)
            _require(_snapshot(engines[OWNER]) == original, "legacy_downgrade_seed_not_restored")
            with engines["star_oam_api"].connect() as db:
                db = db.execution_options(isolation_level="REPEATABLE READ")
                db.execute(text("SET TRANSACTION READ ONLY"))
                _limits(db)
                security.verify(db)
            with engines[OWNER].connect() as db:
                _limits(db)
                _online(db, migration, downgrade=False)
        return dict(schema="rsc.key-provider.legacy-downgrade.pg16.v1", result="passed",
            syntheticOnly=True, productionReady=False, actualMigrationDowngrade=True,
            onlineAlembicContext=True, guardMonkeypatches=0, rejectedCases={case: True for case in CASES},
            exactBlockerCount=4, noOpenBaoPinsInAnyCase=True, allWritesRolledBack=True,
            allTriggersAlwaysBeforeDowngrade=True, completeCatalogReadbackAfterEveryCase=True,
            exactLegacySeedAndClaimsRestoredAfterEveryCase=True, headPreserved=True)
    except KeyProviderBindingGateError as error:
        raise KeyProviderBindingGateError(str(error)) from None
    except BaseException:
        raise KeyProviderBindingGateError("unexpected_legacy_downgrade_" + stage) from None
