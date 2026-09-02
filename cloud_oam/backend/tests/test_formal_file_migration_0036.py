from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import psycopg


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
HEAD = "20260901_0036"
PREVIOUS_HEAD = "20260901_0035"
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0036_formal_file_runtime_boundary.py"
)


def _config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _offline_config(database_url: str, output: io.StringIO) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0036", MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_identity(
    connection: sa.Connection,
    *,
    now: str,
) -> tuple[str, str]:
    organization_id = uuid.uuid4().hex
    person_id = uuid.uuid4().hex
    user_id = str(uuid.uuid4())
    connection.exec_driver_sql(
        "INSERT INTO organizations "
        "(id, external_object_id, code, name, parent_id, org_type, "
        "province_code, status, created_at, updated_at) "
        "VALUES (?, NULL, ?, ?, NULL, 'headquarters', NULL, 'active', ?, ?)",
        (organization_id, f"FILE-{organization_id[:8]}", "文件测试总部", now, now),
    )
    connection.exec_driver_sql(
        "INSERT INTO people "
        "(id, external_object_id, organization_id, employee_no, name, "
        "mobile_encrypted, mobile_hash, employment_status, source_updated_at, "
        "created_at, updated_at) "
        "VALUES (?, NULL, ?, ?, ?, NULL, NULL, 'active', NULL, ?, ?)",
        (person_id, organization_id, f"FILE-{person_id[:8]}", "文件上传人", now, now),
    )
    connection.exec_driver_sql(
        "INSERT INTO users "
        "(id, person_id, account_status, last_login_at, authorization_version, "
        "mobile, name, password_hash, role, province, is_active, "
        "require_password_change, created_at, updated_at) "
        "VALUES (?, ?, 'active', NULL, 1, ?, ?, ?, 'technician', NULL, 1, 0, ?, ?)",
        (
            user_id,
            person_id,
            f"19{uuid.uuid4().int % 10**9:09d}",
            "文件上传人",
            "local-test-only",
            now,
            now,
        ),
    )
    return user_id, person_id


def _metadata(
    *,
    file_id: uuid.UUID,
    user_id: str,
    person_id: str,
    purpose: str = "request_attachment",
    completion: dict[str, str] | None = None,
) -> dict[str, object]:
    storage_key = (
        f"formal-files/v1/{purpose}/{file_id.hex[:2]}/{file_id.hex}"
    )
    value: dict[str, object] = {
        "authorization_version": 1,
        "file_id": str(file_id),
        "idempotency_key_hash": "b" * 64,
        "provider": "aliyun_oss_v2",
        "purpose": purpose,
        "request_sha256": "c" * 64,
        "schema": "cloud_oam.formal_file_upload_intent.v1",
        "storage_key": storage_key,
        "uploader_person_id": str(uuid.UUID(person_id)),
        "uploader_user_id": user_id,
    }
    if completion is not None:
        value["completion"] = completion
    return value


def _insert_file(
    connection: sa.Connection,
    *,
    file_id: uuid.UUID,
    user_id: str,
    metadata: dict[str, object],
    now: str,
    status: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO files "
        "(id, storage_key, sha256, size_bytes, mime_type, original_filename, "
        "uploaded_by, status, metadata_jsonb, created_at) "
        "VALUES (?, ?, ?, 128, 'image/jpeg', 'proof.jpg', ?, ?, ?, ?)",
        (
            file_id.hex,
            metadata["storage_key"],
            "a" * 64,
            user_id,
            status,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            now,
        ),
    )


def test_0036_sqlite_allows_only_exact_pending_to_available_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'formal-file-guard.db'}"
    command.upgrade(_config(database_url), HEAD)
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with engine.begin() as connection:
            user_id, person_id = _seed_identity(connection, now=now)
            file_id = uuid.uuid4()
            pending = _metadata(
                file_id=file_id,
                user_id=user_id,
                person_id=person_id,
            )
            _insert_file(
                connection,
                file_id=file_id,
                user_id=user_id,
                metadata=pending,
                now=now,
                status="pending",
            )

        with pytest.raises(sa.exc.DatabaseError, match="completion transition"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE files SET sha256 = ? WHERE id = ?",
                    ("f" * 64, file_id.hex),
                )

        completion = {
            "etag_sha256": "d" * 64,
            "head_manifest_sha256": "e" * 64,
            "verified_at": now,
        }
        available = _metadata(
            file_id=file_id,
            user_id=user_id,
            person_id=person_id,
            completion=completion,
        )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE files SET status = 'available', metadata_jsonb = ? "
                "WHERE id = ?",
                (
                    json.dumps(
                        available,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    file_id.hex,
                ),
            )
        with pytest.raises(sa.exc.DatabaseError, match="completion transition"):
            with engine.begin() as connection:
                damaged = dict(available)
                damaged["completion"] = {"verified_at": now}
                connection.exec_driver_sql(
                    "UPDATE files SET metadata_jsonb = ? WHERE id = ?",
                    (json.dumps(damaged), file_id.hex),
                )
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM files WHERE id = ?", (file_id.hex,)
                )

        with pytest.raises(sa.exc.DatabaseError, match="upload intent"):
            with engine.begin() as connection:
                wrong_id = uuid.uuid4()
                wrong = _metadata(
                    file_id=wrong_id,
                    user_id=user_id,
                    person_id=person_id,
                    purpose="request_attachment",
                )
                wrong["purpose"] = "stocktake_evidence"
                _insert_file(
                    connection,
                    file_id=wrong_id,
                    user_id=user_id,
                    metadata=wrong,
                    now=now,
                    status="pending",
                )
    finally:
        engine.dispose()


def test_0036_sqlite_preflight_rejects_damaged_formal_metadata_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'formal-file-preflight.db'}"
    config = _config(database_url)
    command.upgrade(config, PREVIOUS_HEAD)
    engine = sa.create_engine(database_url)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        with engine.begin() as connection:
            user_id, _person_id = _seed_identity(connection, now=now)
            file_id = uuid.uuid4()
            connection.exec_driver_sql(
                "INSERT INTO files "
                "(id, storage_key, sha256, size_bytes, mime_type, "
                "original_filename, uploaded_by, status, metadata_jsonb, "
                "created_at) VALUES (?, ?, ?, 128, 'image/jpeg', "
                "'proof.jpg', ?, 'pending', '{}', ?)",
                (
                    file_id.hex,
                    f"formal-files/v1/request_attachment/{file_id.hex[:2]}/{file_id.hex}",
                    "a" * 64,
                    user_id,
                    now,
                ),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="0036 preflight failed"):
        command.upgrade(config, HEAD)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PREVIOUS_HEAD
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'index' "
                "AND name LIKE '%0036'"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE '%0036'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_0036_material_request_guard_does_not_claim_update_delete_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'formal-file-trigger-shape.db'}"
    command.upgrade(_config(database_url), HEAD)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            triggers = {
                row[0]: row[1]
                for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'material_request_files'"
                ).all()
            }
        assert "trg_material_request_files_formal_insert_0036" in triggers
        assert not any(
            name.endswith(("_update_0036", "_delete_0036"))
            and "material_request_files_formal" in name
            for name in triggers
        )
        assert "BEFORE INSERT" in triggers[
            "trg_material_request_files_formal_insert_0036"
        ]
        with pytest.raises(
            sa.exc.DatabaseError,
            match="formal stocktake evidence",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO document_attachments "
                    "(id, document_type, document_id, file_id, "
                    "attachment_type, status, uploaded_by, created_at) "
                    "VALUES (?, 'material_request', ?, ?, "
                    "'request_attachment', 'active', NULL, ?)",
                    (
                        uuid.uuid4().hex,
                        str(uuid.uuid4()),
                        uuid.uuid4().hex,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
    finally:
        engine.dispose()


def test_0036_postgresql_offline_sql_has_exact_lock_acl_and_guard_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _offline_config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output,
        ),
        f"{PREVIOUS_HEAD}:{HEAD}",
        sql=True,
    )
    sql = output.getvalue()
    lock_markers = [
        f"LOCK TABLE public.{table_name} IN ACCESS EXCLUSIVE MODE"
        for table_name in (
            "files",
            "material_request_files",
            "approval_external_registrations",
            "document_attachments",
            "stocktake_scope_count_completions",
            "users",
            "people",
        )
    ]
    positions = [sql.index(marker) for marker in lock_markers]
    assert positions == sorted(positions)
    assert "GRANT SELECT, INSERT ON TABLE public.files" in sql
    assert "GRANT UPDATE (status, metadata_jsonb) ON TABLE public.files" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.document_attachments" in sql
    assert "GRANT SELECT, INSERT ON TABLE public.material_request_files" not in sql
    assert (
        "BEFORE INSERT ON public.material_request_files FOR EACH ROW"
        in sql
    )
    assert "BEFORE INSERT OR UPDATE OR DELETE ON public.files" in sql
    assert "BEFORE TRUNCATE ON public.files" in sql
    assert (
        "BEFORE TRUNCATE ON public.material_request_files" not in sql
    )
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_guard_formal_file_object_0036()" in sql
    )
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_guard_formal_file_binding_0036()" in sql
    )


def test_0036_postgresql_online_evidence_checks_compile_percent_literals(
) -> None:
    migration = _load_migration_module()
    statements: list[sa.sql.elements.TextClause] = []

    class _Connection:
        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(first=lambda: None)

        def exec_driver_sql(self, _statement):
            raise AssertionError(
                "percent-bearing SQL must use SQLAlchemy compilation"
            )

    migration.op = SimpleNamespace(get_bind=lambda: _Connection())
    migration._online_preflight("postgresql")
    migration._require_safe_downgrade("postgresql")

    assert len(statements) == 2
    for statement in statements:
        assert isinstance(statement, sa.sql.elements.TextClause)
        compiled = str(statement.compile(dialect=psycopg.dialect()))
        assert "LIKE 'formal-files/v1/%%'" in compiled
