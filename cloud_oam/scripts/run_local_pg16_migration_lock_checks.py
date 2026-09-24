#!/usr/bin/env python3
"""Reproduce migration lock probes in a newly owned local PostgreSQL 16 cluster.

No external DSN or existing data directory is accepted. This supplements the
GitHub-only runtime gate; it neither enables nor impersonates that gate.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import psycopg


CLOUD = Path(__file__).resolve().parents[1]
# Match the existing local migration supplement's budget. A cold native
# laptop process can load the historical graph more slowly than a CI runner.
LOCAL_COMMAND_TIMEOUT_SECONDS = 600


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend/tests")]
    from local_pg16_cluster import native_cluster, DATABASE
    from pg16_migration_lock_wait import (
        observe_projector_preflight_lock,
        observe_version_maintenance_lock,
        wait_for_migration_lock,
    )

    with native_cluster(
        postgres_bin=args.postgres_bin,
        artifact_root=CLOUD / "artifacts/local-migration-lock-pg16/checks",
    ) as (directory, engines):
        socket = json.loads((directory / "cluster-state.json").read_text())["socketDirectory"]
        environment = dict(
            os.environ,
            OAM_ENVIRONMENT="production",
            OAM_DATABASE_URL=engines["star_oam_migrator"].url.render_as_string(hide_password=False),
            OAM_DATABASE_EXPECTED_MIGRATION_ROLE="star_oam_migrator",
            OAM_DATABASE_EXPECTED_RUNTIME_ROLE="star_oam_api",
        )

        def connect(role, *, autocommit=False):
            return psycopg.connect(host=socket, dbname=DATABASE, user=role, autocommit=autocommit)

        def migrate(label, revision, *, success=True):
            output = directory / (label + ".log")
            with output.open("wb") as log:
                result = subprocess.run(
                    [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", revision],
                    cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT,
                    timeout=LOCAL_COMMAND_TIMEOUT_SECONDS, check=False,
                )
            if (result.returncode == 0) != success:
                raise RuntimeError(label + "_unexpected_exit")
            return output.read_text()

        migrate("upgrade-0043", "20260902_0043")
        source_id, run_id = uuid4(), uuid4()
        with connect("star_oam_migrator", autocommit=True) as owner:
            owner.execute("""
                INSERT INTO source_systems
                    (id,code,name,mode,enabled,configuration_jsonb,created_at,updated_at)
                VALUES (%s,%s,'Local synthetic migration blocker','read_only',true,'{}',now(),now())
            """, (source_id, "local-lock-" + source_id.hex))
        with connect("star_oam_projector") as writer:
            writer.execute("""
                INSERT INTO sync_runs
                    (id,source_system_id,run_key,scope_key,mode,status,started_at,created_at,updated_at)
                VALUES (%s,%s,%s,%s,'full','pending',now(),now(),now())
            """, (run_id, source_id, "local-lock-" + run_id.hex, "oam-work-order-scope:" + "a" * 64))
            with ThreadPoolExecutor(max_workers=1) as executor:
                started = time.monotonic()
                future = executor.submit(migrate, "blocked-0044", "head", success=False)
                try:
                    with connect("postgres", autocommit=True) as observer:
                        assert wait_for_migration_lock(future, lambda: observe_projector_preflight_lock(
                            observer, blocker_pid=writer.info.backend_pid,
                        ), timeout_seconds=LOCAL_COMMAND_TIMEOUT_SECONDS) is True
                        startup_seconds = time.monotonic() - started
                        assert not observe_projector_preflight_lock(observer, blocker_pid=observer.info.backend_pid)
                    writer.commit()
                    output = future.result(timeout=LOCAL_COMMAND_TIMEOUT_SECONDS)
                    assert "0044 requires an empty OAM sync graph" in output
                finally:
                    writer.rollback()
        with connect("star_oam_migrator", autocommit=True) as owner:
            assert owner.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20260902_0043"
            assert owner.execute("SELECT count(*) FROM sync_runs WHERE id=%s", (run_id,)).fetchone()[0] == 1
            assert owner.execute("SELECT to_regclass('public.oam_sync_scope_bindings')").fetchone()[0] is None
            # Only this probe's synthetic objects in its newly owned cluster.
            owner.execute("DELETE FROM sync_runs WHERE id=%s", (run_id,))
            owner.execute("DELETE FROM source_systems WHERE id=%s", (source_id,))
        print("0044 actual projector blocker observed; committed writer refused atomically PASS", flush=True)

        with connect("postgres") as maintenance, ThreadPoolExecutor(max_workers=1) as executor:
            maintenance.execute("LOCK TABLE public.alembic_version IN SHARE UPDATE EXCLUSIVE MODE")
            future = executor.submit(migrate, "version-maintenance-head", "head")
            try:
                with connect("postgres", autocommit=True) as observer:
                    proof = wait_for_migration_lock(future, lambda: observe_version_maintenance_lock(
                        observer, blocker_pid=maintenance.info.backend_pid,
                    ), timeout_seconds=LOCAL_COMMAND_TIMEOUT_SECONDS)
                    assert proof == [("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE", [])]
                    assert not observe_version_maintenance_lock(observer, blocker_pid=observer.info.backend_pid)
                maintenance.rollback()
                future.result(timeout=LOCAL_COMMAND_TIMEOUT_SECONDS)
            finally:
                maintenance.rollback()
        with connect("star_oam_migrator", autocommit=True) as owner:
            head = owner.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        print("Version maintenance exact blocker observed before write locks; migration completed PASS", flush=True)
        result = dict(
            status="passed", actualPostgreSQL16=True, migrationHead=head,
            preflightBlockerObserved=True, wrongBlockerRefused=True,
            nonempty0044RejectedAtomically=True, versionLockBeforeDml=True,
            migrationStartupSeconds=round(startup_seconds, 3),
            ciReleaseGate=False, productionAcceptance=False,
        )
        (directory / "checks.json").write_text(json.dumps(result, indent=2) + "\n")
        print(str(directory), flush=True)


if __name__ == "__main__":
    main()
