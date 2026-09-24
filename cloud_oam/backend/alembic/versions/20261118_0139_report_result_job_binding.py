"""Bind a completed report file to the exact export job idempotency key."""

import hashlib
from pathlib import Path
import runpy

from alembic import op


revision = '20261118_0139'
down_revision = '20261117_0138'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261117_0138_report_result_exclusivity.py'))
ready = runpy.run_path(str(FOLDER / '20261108_0129_notification_expansion_status.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(
    ready['_ready'].replace(ready['_ready_parent'], revision).encode()
).hexdigest()
FUNCTION = 'rsc_guard_report_result_job_binding_0139'
TRIGGER = 'trg_file_jobs_result_binding_0139'

BODY = """
BEGIN
    IF session_user <> 'star_oam_api' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '0139 report result update required' USING ERRCODE='23514';
    END IF;
    IF OLD.job_type='export' AND OLD.status='running' AND NEW.status='succeeded' THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.files f
            WHERE f.id=NEW.result_file_id
              AND f.metadata_jsonb->>'purpose'='inventory_report_export'
              AND f.metadata_jsonb->>'idempotency_key_hash'=NEW.idempotency_key
              AND f.metadata_jsonb->>'authorization_version'=NEW.export_authorization_version::text
        ) THEN
            RAISE EXCEPTION '0139 report result is not bound to its job' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()


def _transition(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0139 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.file_jobs,public.files IN ACCESS EXCLUSIVE MODE')
    elif dialect != 'sqlite':
        raise RuntimeError('0139 PostgreSQL or SQLite required')

    if up:
        if dialect == 'postgresql':
            helper['_preflight']("EXISTS (SELECT 1 FROM file_jobs j LEFT JOIN files f ON f.id=j.result_file_id "
                "WHERE j.job_type='export' AND j.status='succeeded' AND "
                "(f.id IS NULL OR j.export_authorization_version IS NULL "
                "OR j.result_sha256 IS NULL OR j.result_size_bytes IS NULL "
                "OR COALESCE(f.status,'')<>'available' OR COALESCE(f.uploaded_by,'')<>j.requested_by "
                "OR COALESCE(f.sha256,'')<>j.result_sha256 OR f.size_bytes IS NULL OR f.size_bytes<>j.result_size_bytes "
                "OR COALESCE(f.mime_type,'')<>'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' "
                "OR COALESCE(f.metadata_jsonb->>'purpose','')<>'inventory_report_export' "
                "OR COALESCE(f.metadata_jsonb->>'idempotency_key_hash','')<>j.idempotency_key "
                "OR COALESCE(f.metadata_jsonb->>'authorization_version','')<>CAST(j.export_authorization_version AS text)))",
                '0139 existing report result is not bound to its job')
            op.execute(f"CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $job${BODY}$job$")
            op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC,star_oam_api')
            op.execute(f'CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON public.file_jobs FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()')
            op.execute(f'ALTER TABLE public.file_jobs ENABLE ALWAYS TRIGGER {TRIGGER}')
        else:
            helper['_preflight']("EXISTS (SELECT 1 FROM file_jobs j LEFT JOIN files f ON f.id=j.result_file_id "
                "WHERE j.job_type='export' AND j.status='succeeded' AND "
                "(f.id IS NULL OR j.export_authorization_version IS NULL "
                "OR j.result_sha256 IS NULL OR j.result_size_bytes IS NULL "
                "OR COALESCE(f.status,'')<>'available' OR COALESCE(f.uploaded_by,'')<>j.requested_by "
                "OR COALESCE(f.sha256,'')<>j.result_sha256 OR f.size_bytes IS NULL OR f.size_bytes<>j.result_size_bytes "
                "OR COALESCE(f.mime_type,'')<>'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' "
                "OR COALESCE(json_extract(f.metadata_jsonb,'$.purpose'),'')<>'inventory_report_export' "
                "OR COALESCE(json_extract(f.metadata_jsonb,'$.idempotency_key_hash'),'')<>j.idempotency_key "
                "OR COALESCE(json_extract(f.metadata_jsonb,'$.authorization_version'),'')<>CAST(j.export_authorization_version AS text)))",
                '0139 existing report result is not bound to its job')
            op.execute(f"CREATE TRIGGER {TRIGGER} BEFORE UPDATE ON file_jobs "
                "WHEN OLD.job_type='export' AND OLD.status='running' AND NEW.status='succeeded' "
                "AND NOT EXISTS (SELECT 1 FROM files f WHERE f.id=NEW.result_file_id "
                "AND json_extract(f.metadata_jsonb,'$.purpose')='inventory_report_export' "
                "AND json_extract(f.metadata_jsonb,'$.idempotency_key_hash')=NEW.idempotency_key "
                "AND CAST(json_extract(f.metadata_jsonb,'$.authorization_version') AS text)=CAST(NEW.export_authorization_version AS text)) "
                "BEGIN SELECT RAISE(ABORT,'0139 report result is not bound to its job'); END")
    else:
        helper['_preflight']("EXISTS(SELECT 1 FROM file_jobs WHERE job_type='export' AND status='succeeded')",
                             '0139 completed report bindings must be retained')
        if dialect == 'postgresql':
            op.execute(f'DROP TRIGGER {TRIGGER} ON public.file_jobs')
            op.execute(f'DROP FUNCTION public.{FUNCTION}()')
        else:
            op.execute(f'DROP TRIGGER {TRIGGER}')

    if dialect == 'postgresql':
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(
            signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision, revision),) if up else ((revision, down_revision),),
            label='report_result_job_binding_0139',
        )


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
