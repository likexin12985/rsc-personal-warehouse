"""SQLite character-boundary regression; never PostgreSQL runtime evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import runpy
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin


MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/20261229_0180_openbao_data_key_pins.py"
MODELS = (OpenBaoDataKeyPin, ApplicationKeyVersionClaim)
INSTANCE_VALID = ("abc", "0a-", "abcdefghijklmnopqrstuvwxyz0123456789-", "a" * 63)
INSTANCE_INVALID = (
    "", "ab", "a" * 64, "-ab", "Abc", "ab_", "ab/", "ab.", "ab ",
    "ab\n", "ab\t", "ab*", "ab?", "ab[", "ab]", "ab界", "abé", "abＡ",
    "abc\0", "abc\0invalid", "ab\0c", "\0abc",
)
HASH_VALID = ("a" * 64, "0123456789abcdef" * 4)
HASH_INVALID = (
    "", "a" * 63, "a" * 65, "A" * 64, "a" * 63 + "g", "a" * 63 + "-",
    "a" * 63 + " ", "a" * 63 + "\n", "a" * 63 + "界", "a" * 63 + "Ａ",
    "a" * 64 + "\0", "a" * 64 + "\0invalid", "a" * 31 + "\0" + "a" * 32,
    "\0" + "a" * 64,
)
# Captured before this SQLite-only repair with the locked SQLAlchemy version.
# The migration already included the separately validated public schema fix.
REVIEWED_PG_DDL_SHA256 = {
    "migration": {
        "openbao_data_key_pins": "1cb5007113a079e8fa711c85d3694401a9b17bca17842fc3d3aa1e0c77c78411",
        "application_key_version_claims": "d70324583006342ca8e089b1ce0657f3cfa38b2704414a156d6c74fde2b069e5",
    },
    "orm": {
        "openbao_data_key_pins": "de2d2f4c8f1a25654c63f69cbabcb48d34cad94c09dccb4f88a1654d4e8f5bb9",
        "application_key_version_claims": "8cf6404b4b3ebc2125ab0547047d8d9898fb422a305dc38e66fa3730f58165fc",
    },
}


def _migration_tables(connection):
    migration = runpy.run_path(str(MIGRATION))
    with Operations.context(MigrationContext.configure(connection)):
        migration["_create_tables"]()


@pytest.fixture(params=("migration", "orm"))
def character_db(request):
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        if request.param == "migration":
            with engine.begin() as connection:
                _migration_tables(connection)
        else:
            OpenBaoDataKeyPin.metadata.create_all(engine, tables=[model.__table__ for model in MODELS])
        yield engine
    finally:
        engine.dispose()


def _pin(**changes):
    values = dict(purpose="authentication_idempotency", environment="test", provider_instance_id="abc",
        key_path="transit/keys/rsc-authentication-idempotency", application_key_version=1,
        transit_key_version=1, ciphertext_sha256="a" * 64, context_sha256="b" * 64,
        associated_data_sha256="c" * 64, created_at=datetime(2026, 10, 8, tzinfo=timezone.utc))
    return values | changes


def _attempt(engine, table, values, *, valid):
    # Each vector is independent; successful synthetic rows are rolled back too.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            statement = sa.text(f"INSERT INTO {table} (" + ",".join(values) + ") VALUES ("
                                + ",".join(":" + name for name in values) + ")")
            if valid:
                connection.execute(statement, values)
                assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 1
            else:
                with pytest.raises(sa.exc.IntegrityError, match="CHECK constraint failed"):
                    connection.execute(statement, values)
                assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 0
        finally:
            transaction.rollback()


def test_sqlite_instance_alphabet_boundaries_and_nul_are_enforced(character_db):
    for valid, values in ((True, INSTANCE_VALID), (False, INSTANCE_INVALID)):
        for value in values:
            _attempt(character_db, "openbao_data_key_pins", _pin(provider_instance_id=value), valid=valid)


@pytest.mark.parametrize("table,column", (
    ("openbao_data_key_pins", "ciphertext_sha256"),
    ("openbao_data_key_pins", "context_sha256"),
    ("openbao_data_key_pins", "associated_data_sha256"),
    ("application_key_version_claims", "ciphertext_sha256"),
))
def test_sqlite_hash_alphabet_boundaries_and_nul_are_enforced(character_db, table, column):
    for valid, values in ((True, HASH_VALID), (False, HASH_INVALID)):
        for value in values:
            row = (_pin() if table == "openbao_data_key_pins" else dict(
                purpose="authentication_idempotency", application_key_version=1,
                provider="openbao_transit_v1", ciphertext_sha256="a" * 64,
                created_at=datetime(2026, 10, 8, tzinfo=timezone.utc)))
            _attempt(character_db, table, row | {column: value}, valid=valid)


def test_postgresql_ddl_is_byte_identical_to_before_sqlite_repair():
    statements = []
    connection = sa.create_mock_engine("postgresql://", lambda statement, *_a, **_kw: statements.append(statement))
    _migration_tables(connection)
    sources = {"migration": statements, "orm": [sa.schema.CreateTable(model.__table__) for model in MODELS]}
    for source, ddl in sources.items():
        actual = {item.element.name: sha256(str(item.compile(dialect=connection.dialect)).encode()).hexdigest()
                  for item in ddl}
        assert actual == REVIEWED_PG_DDL_SHA256[source]
