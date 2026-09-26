"""Prove both current admission and the independent deferred insert graph."""

from pathlib import Path
import runpy
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from migration_script_cache import cache_migration_compilation


def assert_raw_opening_insert_rejected(api_engine, *, task_id):
    versions = Path(__file__).parents[1] / "alembic/versions"
    path = next(versions.glob("*0052*.py"))
    with cache_migration_compilation(versions):
        migration = runpy.run_path(str(path))
    columns = (
        "id,task_no,task_type,region_org_id,status,blind_count,cutoff_ledger_cursor,"
        "cutoff_at,scope_manifest_sha256,snapshot_manifest_sha256,control_source_system_id,"
        "control_sync_run_id,control_snapshot_at,control_manifest_sha256,current_round_no,"
        "created_by_user_id,deadline,issued_at,frozen_at,submitted_at,posted_at,closed_at,"
        "cancelled_at,version,note,created_at,updated_at,opening_authorization_version"
    )
    values = (
        ":id,task_no||:suffix,task_type,region_org_id,'cancelled',blind_count,cutoff_ledger_cursor,"
        "cutoff_at,scope_manifest_sha256,snapshot_manifest_sha256,control_source_system_id,"
        "control_sync_run_id,control_snapshot_at,control_manifest_sha256,current_round_no,"
        "created_by_user_id,deadline,issued_at,frozen_at,NULL,NULL,NULL,clock_timestamp(),"
        "version,note,created_at,clock_timestamp(),"
    )
    with api_engine.connect() as db:
        original = db.scalar(text("SELECT to_jsonb(t) FROM public.stocktake_tasks t WHERE id=:id"),
                             {"id": task_id})
    assert original is not None and original["opening_authorization_version"] > 0
    for with_authority in (False, True):
        forged = uuid4()
        # Keep the legitimate source's current authorization evidence only in
        # the graph probe. Otherwise 0126 rightly rejects before 0052 can run.
        authority = "opening_authorization_version" if with_authority else "NULL"
        query = text("INSERT INTO public.stocktake_tasks (" + columns + ") SELECT "
                     + values + authority + " FROM public.stocktake_tasks WHERE id=:source")
        with api_engine.connect() as db:
            with pytest.raises(DBAPIError) as failure:
                result = db.execute(query, {"id": forged, "suffix": "-RAW-" + forged.hex[:8],
                                            "source": task_id})
                assert with_authority and result.rowcount == 1
                trigger = migration["INSERT_GUARD_TRIGGER"]
                assert trigger == "trg_stocktake_tasks_opening_insert_graph_0052"
                db.execute(text('SET CONSTRAINTS "' + trigger + '" IMMEDIATE'))
            assert failure.value.orig.sqlstate == "23514"
            message = (migration["OPENING_INSERT_ERROR"] if with_authority
                       else "0126 new opening requires authorization evidence")
            assert message in str(failure.value.orig)
            db.rollback()
            assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.stocktake_tasks WHERE id=:id)"),
                                 {"id": forged})
            assert db.scalar(text("SELECT to_jsonb(t) FROM public.stocktake_tasks t WHERE id=:id"),
                             {"id": task_id}) == original
    return {"missingAuthorizationRejected": True, "authorizedRawGraphRejected": True,
            "originalTaskUnchanged": True}
