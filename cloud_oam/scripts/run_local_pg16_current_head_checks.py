#!/usr/bin/env python3
"""Check the current migration and runtime grants in a new owned PG16 cluster.

This local supplement never accepts a DSN or runs the destructive CI release
gate. The cluster helper retains its evidence and stops only its own server.
"""

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool


CLOUD = Path(__file__).resolve().parents[1]
HEAD = "20261119_0140"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True)
    parser.add_argument("--report-full-flow", action="store_true",
                        help="also establish formal opening and exercise report HTTP/worker end to end")
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend/tests")]
    from local_pg16_cluster import native_cluster

    directory = None
    try:
        with native_cluster(
            postgres_bin=args.postgres_bin,
            artifact_root=CLOUD / "artifacts/local-current-head-pg16/checks",
        ) as (directory, engines):
            url = engines["star_oam_migrator"].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT="production", OAM_DATABASE_URL=url,
                               OAM_DATABASE_EXPECTED_MIGRATION_ROLE="star_oam_migrator",
                               OAM_DATABASE_EXPECTED_RUNTIME_ROLE="star_oam_api")

            def command(label, argv):
                with (directory / (label + ".log")).open("wb") as output:
                    result = subprocess.run(argv, cwd=CLOUD, env=environment,
                                            stdout=output, stderr=subprocess.STDOUT, timeout=600)
                if result.returncode:
                    raise RuntimeError(label + "_failed")

            command("upgrade-current-head", [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"])
            with engines["star_oam_migrator"].connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
                export_columns = {
                    column["name"] for column in inspect(connection).get_columns("file_jobs")
                }
                assert {
                    "export_authorization_version", "export_scope_jsonb",
                    "export_ledger_cursor", "result_sha256", "result_size_bytes",
                    "download_count",
                } <= export_columns
                export_grants = connection.execute(text("""
                    SELECT r.code, rp.effect
                    FROM permissions p
                    JOIN role_permissions rp ON rp.permission_id = p.id
                    JOIN roles r ON r.id = rp.role_id
                    WHERE p.resource = 'report' AND p.action = 'export'
                      AND p.field_code = ''
                    ORDER BY r.code, rp.effect
                """)).all()
                assert export_grants == [("admin", "allow"), ("provincial_manager", "allow")]
                assert connection.scalar(text("""
                    SELECT count(*) FROM permissions
                    WHERE resource = 'report' AND action = 'export' AND field_code = ''
                """)) == 1
                assert connection.scalar(text("""
                    SELECT count(*) FROM pg_index ix
                    JOIN pg_class table_row ON table_row.oid=ix.indrelid
                    JOIN pg_namespace n ON n.oid=table_row.relnamespace
                    JOIN pg_class index_row ON index_row.oid=ix.indexrelid
                    WHERE n.nspname='public' AND table_row.relname='file_jobs'
                      AND index_row.relname='uq_file_jobs_result_file_id'
                      AND ix.indisunique AND ix.indisvalid AND ix.indisready
                      AND pg_get_indexdef(ix.indexrelid) LIKE '%(result_file_id)'
                """)) == 1

            psql = str(Path(args.postgres_bin).resolve() / "psql")
            command("edge-staging-grants", [psql, "-X", "-w", "--set=ON_ERROR_STOP=1",
                "--dbname", url.replace("postgresql+psycopg:", "postgresql:", 1),
                "-v", "edge_role=edge_inbox", "-f", str(CLOUD / "deployment/create_oam_edge_staging.sql")])

            runpy.run_path(str(CLOUD / "backend/tests/conftest.py"))
            from app.daily_reconciliation.capture_provisioning import provision_capture_roles
            from app.daily_reconciliation.capture_role_contract import ROLES
            from app.daily_reconciliation.capture_security import validate_capture_roles
            from app.database_security import validate_production_database_security
            from app.edge_database_security import read_oam_sync_scope_boundary
            from pg16_sms_profile_gate import run as run_sms_profile_gate
            from pg16_inventory_report_gate import assert_report_job_runtime_gate
            from pg16_opening_source_purpose_gate import assert_opening_source_runtime_gate
            from secrets import token_urlsafe
            from uuid import uuid4

            admin = create_engine(URL.create("postgresql+psycopg", username="postgres",
                database="rsc_pg16_release_gate", query={"host":str(Path(
                    json.loads((directory / "cluster-state.json").read_text())["socketDirectory"]))}),
                poolclass=NullPool, hide_parameters=True)
            try:
                with admin.begin() as connection:
                    assert not provision_capture_roles(connection, database="rsc_pg16_release_gate", apply=False)["configured"]
                    configured = provision_capture_roles(connection, database="rsc_pg16_release_gate",
                        passwords={role:token_urlsafe(40) for role in ROLES}, apply=True)
                    assert configured["configured"] and configured["changed"]
                    assert validate_capture_roles(connection)
                validate_production_database_security(engines["star_oam_api"],
                    expected_runtime_role="star_oam_api", expected_migration_role="star_oam_migrator")
                with engines["star_oam_api"].connect() as connection:
                    report_job_acl = connection.execute(text("""
                        SELECT has_table_privilege(current_user, 'public.file_jobs', 'SELECT'),
                               has_table_privilege(current_user, 'public.file_jobs', 'INSERT'),
                               has_table_privilege(current_user, 'public.file_jobs', 'UPDATE')
                    """)).one()
                assert report_job_acl == (True, True, False)
                with engines["star_oam_api"].connect() as connection:
                    report_job_columns = connection.execute(text("""
                        SELECT has_column_privilege(current_user,'public.file_jobs','status','UPDATE'),
                               has_column_privilege(current_user,'public.file_jobs','parameters_jsonb','UPDATE'),
                               has_column_privilege(current_user,'public.file_jobs','download_count','UPDATE')
                    """)).one()
                assert report_job_columns == (True, False, True)
                with engines["star_oam_api"].begin() as connection:
                    try:
                        with connection.begin_nested():
                            connection.execute(text("""
                                INSERT INTO public.file_jobs (
                                  id,job_type,requested_by,parameters_jsonb,parameters_hash,
                                  idempotency_key,status,export_authorization_version,
                                  export_scope_jsonb,export_ledger_cursor,download_count
                                ) VALUES (
                                  :id,'export',:requester,
                                  CAST(:parameters AS jsonb),
                                  :digest,:digest,'succeeded',1,
                                  CAST(:scope AS jsonb),0,0
                                )
                            """), {"id":uuid4(), "requester":str(uuid4()), "digest":"a"*64,
                                   "parameters":json.dumps({"report":"inventory_balances","filters":{}}),
                                   "scope":json.dumps({"version":1,"account_ids":[],"assignment_ids":[]})})
                    except DBAPIError as error:
                        assert error.orig.sqlstate == "23514", error.orig.sqlstate
                    else:
                        raise AssertionError("API runtime accepted a forged completed export job")
                report_job_runtime = assert_report_job_runtime_gate(
                    engines["star_oam_migrator"], engines["star_oam_api"],
                )
                opening_source_runtime = assert_opening_source_runtime_gate(
                    engines["star_oam_migrator"], engines["star_oam_api"],
                )
                with engines["edge_inbox"].connect() as connection:
                    edge = dict(read_oam_sync_scope_boundary(connection, expected_role="edge_inbox"))
                with engines["star_oam_projector"].connect() as connection:
                    projector = dict(read_oam_sync_scope_boundary(connection, expected_role="star_oam_projector"))
                # A fresh cluster has no approved OAM source binding. Both
                # narrow identities must remain closed until provisioning.
                closed = {"boundary_ok":False, "boundary_failures":"revision_and_binding"}
                assert edge == closed and projector == closed
                sms = run_sms_profile_gate(engines["star_oam_migrator"], engines["star_oam_api"], admin)
                assert sms["status"] == "passed" and len(sms["cases"]) == 9
                report_full_flow = None
                if args.report_full_flow:
                    from pg16_opening_fixture_gate import run as run_opening_fixture
                    from pg16_inventory_report_full_flow import assert_report_full_flow
                    opening_fixture = run_opening_fixture(engines, establish_dynamic_peer=True)
                    assert opening_fixture["status"] == "passed"
                    report_full_flow = assert_report_full_flow(
                        engines["star_oam_migrator"], engines["star_oam_api"],
                    )
                result = {"status":"passed", "head":HEAD, "captureRoles":sorted(ROLES),
                          "reportExportGrants":[list(grant) for grant in export_grants],
                          "reportJobGuardedRuntimeAcl":True,
                          "reportJobForgedCompletionRejected":True,
                          "reportJobRuntimeTransitions":report_job_runtime,
                          "openingSourceRuntime":opening_source_runtime,
                          "reportResultFileExclusive":True,
                          "edgeRole":"edge_inbox", "runtimeRole":"star_oam_api",
                          "scopeClosedUntilProvisioned":True,
                          "edgeBoundary":edge, "projectorBoundary":projector,
                          "smsProfile":sms,
                          "ciReleaseGate":False}
                if report_full_flow is not None:
                    result["reportFullFlow"] = report_full_flow
                (directory / "checks.json").write_text(json.dumps(result, sort_keys=True, indent=2, default=str) + "\n")
                print(json.dumps({"status":"passed", "evidenceDirectory":str(directory), "head":HEAD,
                                  "captureRoleCount":len(ROLES), "smsCases":len(sms["cases"]),
                                  "ciReleaseGate":False}), flush=True)
            finally:
                admin.dispose()
    except BaseException as error:
        failure = {"status":"failed", "errorType":type(error).__name__,
                   "evidenceDirectory":str(directory) if directory else None,
                   "frames":[{"file":frame.filename,"line":frame.lineno,"function":frame.name}
                             for frame in traceback.extract_tb(error.__traceback__)]}
        if directory is not None:
            (directory / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
        print(json.dumps(failure), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
