"""Focused SQLite structural/atomicity evidence, never PG16 permission proof.

The real role, concurrent unique-index wait, catalog and backup checks belong
to the isolated PostgreSQL 16 gate. These tests do not replay older suites.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa


VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PINS = "openbao_data_key_pins"
CLAIMS = "application_key_version_claims"
LEGACY = "kms_data_key_pins"


def _module(filename):
    spec = importlib.util.spec_from_file_location(filename[:-3], VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def structural_db(tmp_path, monkeypatch):
    legacy = _module("20260901_0040_kms_data_key_pins.py")
    current = _module("20261229_0180_openbao_data_key_pins.py")
    monkeypatch.setattr(current.context, "is_offline_mode", lambda: False)
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'bindings.db'}")
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            legacy.upgrade()
    yield engine, current
    engine.dispose()


def _apply(engine, migration, action="upgrade"):
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            getattr(migration, action)()


def _legacy(version=1, purpose=AUTH, digest="a"):
    return dict(purpose=purpose, kms_key_id="reviewed-aliyun-key",
        application_key_version=version, kms_key_version_id="reviewed-kms-version",
        ciphertext_sha256=digest * 64, created_at=NOW)


def _openbao(version=2, purpose=AUTH, digest="b", **changes):
    value = dict(purpose=purpose, environment="test", provider_instance_id="isolated-openbao-2-7-1",
        key_path="transit/keys/" + ("rsc-authentication-idempotency" if purpose == AUTH else "rsc-material-request-contact"),
        application_key_version=version, transit_key_version=1,
        ciphertext_sha256=digest * 64, context_sha256="c" * 64,
        associated_data_sha256="d" * 64, created_at=NOW)
    value.update(changes)
    return value


def _insert(engine, table, values, *, replace=False):
    with engine.begin() as db:
        command = "INSERT OR REPLACE" if replace else "INSERT"
        db.execute(sa.text(f"{command} INTO {table} (" + ",".join(values) + ") VALUES (" + ",".join(":" + name for name in values) + ")"), values)


def _rows(engine, table):
    with engine.connect() as db:
        return [dict(row) for row in db.execute(sa.text(f"SELECT * FROM {table} ORDER BY purpose,application_key_version")).mappings()]


def test_backfill_is_exact_legacy_fact_and_models_match_migration(structural_db):
    from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin

    engine, migration = structural_db
    _insert(engine, LEGACY, _legacy())
    original = _rows(engine, LEGACY)
    _apply(engine, migration)
    assert _rows(engine, LEGACY) == original
    assert _rows(engine, CLAIMS) == [{key: value for key, value in original[0].items() if key not in {"kms_key_id", "kms_key_version_id"}} | {"provider": "aliyun_kms"}]
    _insert(engine, LEGACY, _legacy(version=3, digest="e"))
    _insert(engine, PINS, _openbao())
    assert [(row["application_key_version"], row["provider"]) for row in _rows(engine, CLAIMS)] == [(1, "aliyun_kms"), (2, "openbao_transit_v1"), (3, "aliyun_kms")]
    inspector = sa.inspect(engine)
    for model in (ApplicationKeyVersionClaim, OpenBaoDataKeyPin):
        assert [column["name"] for column in inspector.get_columns(model.__tablename__)] == list(model.__table__.columns.keys())
        assert all(not column["nullable"] for column in inspector.get_columns(model.__tablename__))
        actual = {row["name"]: row["sqltext"] for row in inspector.get_check_constraints(model.__tablename__)}
        expected = {constraint.name: str(constraint.sqltext) for constraint in model.__table__.constraints if isinstance(constraint, sa.CheckConstraint)}
        assert actual == expected
        assert inspector.get_pk_constraint(model.__tablename__)["constrained_columns"] == ["purpose", "application_key_version"]


@pytest.mark.parametrize("first", ["aliyun", "openbao"])
def test_cross_provider_conflict_rolls_back_losing_pin(structural_db, first):
    engine, migration = structural_db
    _apply(engine, migration)
    attempts = [(LEGACY, _legacy()), (PINS, _openbao(version=1))]
    if first == "openbao":
        attempts.reverse()
    _insert(engine, *attempts[0])
    before = {table: _rows(engine, table) for table in (LEGACY, PINS, CLAIMS)}
    with pytest.raises(sa.exc.IntegrityError, match="claim already exists"):
        _insert(engine, *attempts[1])
    assert {table: _rows(engine, table) for table in before} == before


def test_purpose_is_separate_but_environment_instance_and_provider_are_not_version_namespaces(structural_db):
    engine, migration = structural_db
    _apply(engine, migration)
    _insert(engine, LEGACY, _legacy())
    _insert(engine, PINS, _openbao(version=1, purpose=CONTACT))
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, PINS, _openbao(version=1, purpose=CONTACT, digest="e", environment="production", provider_instance_id="another-instance"))
    assert len(_rows(engine, CLAIMS)) == 2


@pytest.mark.parametrize("provider", ["aliyun_kms", "openbao_transit_v1"])
def test_direct_orphan_claim_cannot_reserve_application_version(structural_db, provider):
    engine, migration = structural_db
    _apply(engine, migration)
    with pytest.raises(sa.exc.IntegrityError, match="exact existing pin"):
        _insert(engine, CLAIMS, dict(purpose=AUTH, application_key_version=8, provider=provider, ciphertext_sha256="f" * 64, created_at=NOW))
    assert _rows(engine, CLAIMS) == []


@pytest.mark.parametrize("table", [PINS, CLAIMS, LEGACY])
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_binding_and_legacy_mutations_are_rejected(structural_db, table, action):
    engine, migration = structural_db
    _apply(engine, migration)
    _insert(engine, LEGACY, _legacy())
    _insert(engine, PINS, _openbao())
    before = _rows(engine, table)
    command = f"DELETE FROM {table}" if action == "DELETE" else f"UPDATE {table} SET ciphertext_sha256='{'f' * 64}'"
    with pytest.raises(sa.exc.IntegrityError, match="immutable"):
        with engine.begin() as db:
            db.exec_driver_sql(command)
    assert _rows(engine, table) == before


@pytest.mark.parametrize("changes", [
    {"purpose": "other"}, {"environment": "other"},
    {"provider_instance_id": " ab"}, {"provider_instance_id": "-ab"},
    {"provider_instance_id": "Abc"}, {"provider_instance_id": "界abc"},
    {"application_key_version": 0}, {"transit_key_version": 2147483648},
    {"key_path": "transit/keys/rsc-material-request-contact"},
    {"ciphertext_sha256": "A" * 64}, {"context_sha256": "a" * 63},
    {"associated_data_sha256": "b" * 63 + "z"},
])
def test_invalid_openbao_coordinates_never_create_claim(structural_db, changes):
    engine, migration = structural_db
    _apply(engine, migration)
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, PINS, _openbao(**changes))
    assert _rows(engine, PINS) == _rows(engine, CLAIMS) == []


@pytest.mark.parametrize("table, original, replacement", [
    (LEGACY, _legacy(), _legacy(digest="e")),
    (LEGACY, _legacy(), _legacy(version=3)),
    (PINS, _openbao(), _openbao(digest="e")),
    (PINS, _openbao(), _openbao(version=3)),
])
def test_sqlite_replace_does_not_bypass_immutable_identity(structural_db, table, original, replacement):
    engine, migration = structural_db
    _apply(engine, migration)
    _insert(engine, table, original)
    before = {name: _rows(engine, name) for name in (LEGACY, PINS, CLAIMS)}
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, table, replacement, replace=True)
    assert {name: _rows(engine, name) for name in before} == before


@pytest.mark.parametrize("with_legacy", [False, True])
def test_empty_or_exact_legacy_mirror_downgrade_preserves_legacy_facts(structural_db, with_legacy):
    engine, migration = structural_db
    if with_legacy:
        _insert(engine, LEGACY, _legacy())
    before = _rows(engine, LEGACY)
    _apply(engine, migration)
    _apply(engine, migration, "downgrade")
    assert PINS not in sa.inspect(engine).get_table_names()
    assert CLAIMS not in sa.inspect(engine).get_table_names()
    assert _rows(engine, LEGACY) == before
    _apply(engine, migration)
    assert len(_rows(engine, CLAIMS)) == len(before)


def test_openbao_pin_blocks_downgrade_before_structure_change(structural_db):
    engine, migration = structural_db
    _apply(engine, migration)
    _insert(engine, PINS, _openbao())
    before = _rows(engine, CLAIMS)
    with pytest.raises(RuntimeError, match="cannot downgrade 0180"):
        _apply(engine, migration, "downgrade")
    assert _rows(engine, CLAIMS) == before
    assert len(_rows(engine, PINS)) == 1


@pytest.mark.parametrize("drift", ["missing", "wrong_hash", "orphan"])
def test_legacy_mirror_drift_blocks_downgrade(structural_db, drift):
    engine, migration = structural_db
    _insert(engine, LEGACY, _legacy())
    _apply(engine, migration)
    with engine.begin() as db:
        # Deliberate owner-level tampering is a negative fixture, never runtime.
        if drift == "missing":
            db.exec_driver_sql("DROP TRIGGER trg_application_key_version_claims_immutable_delete_0180")
            db.exec_driver_sql(f"DELETE FROM {CLAIMS}")
        elif drift == "wrong_hash":
            db.exec_driver_sql("DROP TRIGGER trg_application_key_version_claims_immutable_update_0180")
            db.exec_driver_sql(f"UPDATE {CLAIMS} SET ciphertext_sha256='{'f' * 64}'")
        else:
            db.exec_driver_sql("DROP TRIGGER trg_application_key_version_claims_insert_0180")
            db.execute(sa.text(f"INSERT INTO {CLAIMS} (purpose,application_key_version,provider,ciphertext_sha256,created_at) VALUES (:purpose,99,'aliyun_kms',:digest,:created)"), {"purpose": AUTH, "digest": "f" * 64, "created": NOW})
    before = _rows(engine, CLAIMS)
    with pytest.raises(RuntimeError, match="cannot downgrade 0180"):
        _apply(engine, migration, "downgrade")
    assert _rows(engine, CLAIMS) == before
    assert len(_rows(engine, LEGACY)) == 1


def test_offline_migration_is_rejected_before_any_sql(structural_db, monkeypatch):
    engine, migration = structural_db
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: True)
    with pytest.raises(ValueError, match="online predecessor"):
        _apply(engine, migration)
    assert PINS not in sa.inspect(engine).get_table_names()
