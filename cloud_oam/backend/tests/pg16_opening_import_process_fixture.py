"""Fixed spawn targets for a caller-owned native PG16 fixture only.

Keep module import standard-library-only: spawn must set the isolated app
environment before any model imports. Local connection details travel only in
the private input pipe; progress/result messages contain no DSN or source data.
"""
import json
import os
from pathlib import Path
import stat
import sys
import time
from uuid import UUID


def validate_owned_native_url(database_url):
    from sqlalchemy.engine import make_url
    url = make_url(database_url)
    host = url.query.get("host")
    if (url.drivername != "postgresql+psycopg" or url.username != "star_oam_api"
            or url.database != "rsc_pg16_release_gate" or url.host is not None
            or url.port is not None or url.password is not None
            or set(url.query) != {"host"} or type(host) is not str):
        raise RuntimeError("owned_native_import_database_required")
    directory = Path(host).resolve(strict=True)
    metadata = directory.stat()
    if (directory.parent != Path("/tmp").resolve() or not directory.name.startswith("rsc-pg16-")
            or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise RuntimeError("owned_native_import_socket_required")
    return url


def _isolate_app_environment():
    if "app.database" in sys.modules:
        raise RuntimeError("spawn_imported_application_before_test_boundary")
    for name in tuple(os.environ):
        if name.startswith(("PG", "OAM_", "RSC_PG16_", "ALIBABA_CLOUD_", "OSS_", "AWS_")):
            del os.environ[name]
    os.environ.update(OAM_ENVIRONMENT="test", OAM_DATABASE_URL="sqlite+pysqlite:///:memory:",
        OAM_DATABASE_SCHEMA_MODE="bootstrap",
        OAM_JWT_SECRET="synthetic-owned-import-child-secret-thirty-two-characters")


def _execute(payload, expires, *, fault):
    _isolate_app_environment()
    from sqlalchemy import create_engine, event, select, text
    from sqlalchemy.pool import NullPool
    from app.config import get_settings
    from app.database_security import validate_production_database_security
    from app.foundation_models import FileJob
    from app.opening_count_import_worker import process_one_opening_count_import
    from app.opening_import_worker_database import opening_import_worker_session_factory

    assert type(payload) is dict and set(payload) == {
        "database_url", "job_id", "source_coordinates", "source_bytes", "application_name", "progress"}
    assert fault in {None, "after_commit", "source_locked"}
    identifier = UUID(payload["job_id"])
    assert str(identifier) == payload["job_id"] and type(payload["source_bytes"]) is bytes
    assert payload["application_name"].startswith("pg16-import-owned-")
    assert 0 < expires - time.monotonic() <= 60
    settings = get_settings()
    assert settings.environment == "test" and settings.database_url == "sqlite+pysqlite:///:memory:"
    url = validate_owned_native_url(payload["database_url"])

    def report(kind, *, backend_pid=None):
        message = {"event": kind, "pid": os.getpid(), "backend_pid": backend_pid,
                   "job_id": str(identifier)}
        data = json.dumps(message, separators=(",", ":")).encode()
        assert len(data) < 1024
        payload["progress"].send_bytes(data)

    def hang():
        while True:
            time.sleep(0.02)

    report("ready")
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
        connect_args={"connect_timeout": 2, "tcp_user_timeout": 1000,
                      "application_name": payload["application_name"]})
    try:
        assert isinstance(engine.pool, NullPool)
        with engine.connect() as db:
            actual = db.execute(text("SELECT current_user,current_database(),"
                                     "current_setting('server_version_num')::int")).one()
            assert actual[:2] == ("star_oam_api", "rsc_pg16_release_gate")
            assert 160000 <= actual[2] < 170000
        validate_production_database_security(engine, expected_runtime_role="star_oam_api",
                                             expected_migration_role="star_oam_migrator")
        factory = opening_import_worker_session_factory(engine)
        current_backend = {}

        @event.listens_for(factory, "after_begin")
        def remember_owned_backend(session, transaction, connection):
            pid = connection.scalar(text("SELECT pg_backend_pid()"))
            session.info["owned_backend_pid"] = current_backend["pid"] = pid

        if fault == "after_commit":
            @event.listens_for(factory, "before_commit")
            def select_preview_commit(session):
                session.info["hang_after_committed_preview"] = session.scalar(
                    select(FileJob.status).where(FileJob.id == identifier)) == "awaiting_confirmation"

            @event.listens_for(factory, "after_commit")
            def lose_committed_preview_response(session):
                if session.info.get("hang_after_committed_preview"):
                    # The DBAPI COMMIT, including real deferred guards, has
                    # completed. Parent independently rereads that real fact.
                    report("committed", backend_pid=session.info["owned_backend_pid"])
                    hang()

        class SyntheticSource:
            provider_code = "aliyun_oss_v2"

            def read_opening_count_source(self, **coordinates):
                assert coordinates == payload["source_coordinates"]
                report("source_read", backend_pid=current_backend["pid"])
                if fault == "source_locked":
                    # Real read_authorized_opening_count_source holds its
                    # uploader graph and FileObject FOR UPDATE at this point.
                    report("source_locked", backend_pid=current_backend["pid"])
                    hang()
                return payload["source_bytes"]

            def put_opening_count_error(self, **coordinates):
                report("unexpected_object_io")
                raise AssertionError("valid source may not create an error object")

            def head_object(self, **coordinates):
                report("unexpected_object_io")
                raise AssertionError("valid source may not recover an error object")

        result = process_one_opening_count_import(factory, storage=SyntheticSource(), job_id=identifier)
        return {"job_id": str(result.job_id), "status": result.status, "recovered": result.recovered}
    finally:
        engine.dispose()


def commit_then_hang(payload, expires):
    return _execute(payload, expires, fault="after_commit")


def source_lock_then_hang(payload, expires):
    return _execute(payload, expires, fault="source_locked")


def healthy(payload, expires):
    return _execute(payload, expires, fault=None)
