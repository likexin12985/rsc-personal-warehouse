"""Admit only scoped export jobs to the API runtime and seal their state axis."""

from pathlib import Path
import hashlib
import runpy

from alembic import op


revision = '20261115_0136'
down_revision = '20261114_0135'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261114_0135_inventory_report_export_jobs.py'))
ready = runpy.run_path(str(FOLDER / '20261108_0129_notification_expansion_status.py'))
OLD_READY_HASH = previous['NEW_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
FUNCTION = 'rsc_guard_report_export_job_0136'
ROW_TRIGGER = 'trg_file_jobs_export_guard_0136'
TRUNCATE_TRIGGER = 'trg_file_jobs_export_no_truncate_0136'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

BODY = f"""
DECLARE
    old_unmodified jsonb;
    new_unmodified jsonb;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '0136 report jobs cannot be removed' USING ERRCODE='23514';
    END IF;
    IF session_user <> 'star_oam_api' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.job_type <> 'export' OR NEW.status <> 'queued'
           OR NEW.export_authorization_version IS NULL OR NEW.export_authorization_version <= 0
           OR NEW.export_ledger_cursor IS NULL OR NEW.export_ledger_cursor < 0
           OR jsonb_typeof(NEW.parameters_jsonb) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(NEW.parameters_jsonb)) <> 2
           OR NEW.parameters_jsonb->>'report' <> 'inventory_balances'
           OR jsonb_typeof(NEW.parameters_jsonb->'filters') <> 'object'
           OR jsonb_typeof(NEW.export_scope_jsonb) <> 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(NEW.export_scope_jsonb)) <> 3
           OR NEW.export_scope_jsonb->>'version' <> '1'
           OR jsonb_typeof(NEW.export_scope_jsonb->'account_ids') <> 'array'
           OR jsonb_typeof(NEW.export_scope_jsonb->'assignment_ids') <> 'array'
           OR jsonb_array_length(NEW.export_scope_jsonb->'account_ids') > 20000
           OR NEW.parameters_hash !~ '^[0-9a-f]{{64}}$'
           OR NEW.idempotency_key !~ '^[0-9a-f]{{64}}$'
           OR NEW.download_count <> 0
           OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
           OR NEW.result_file_id IS NOT NULL OR NEW.error_file_id IS NOT NULL
           OR NEW.result_sha256 IS NOT NULL OR NEW.result_size_bytes IS NOT NULL
           OR NEW.confirmed_by IS NOT NULL OR NEW.error_detail IS NOT NULL
        THEN
            RAISE EXCEPTION '0136 queued export job shape invalid' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.requested_by]::text[]);
        IF NOT EXISTS (
            SELECT 1 FROM public.users u
            JOIN public.people p ON p.id=u.person_id
            JOIN public.role_assignments a ON a.user_id=u.id
            JOIN public.roles r ON r.id=a.role_id
            WHERE u.id=NEW.requested_by AND u.is_active AND u.account_status='active'
              AND u.authorization_version=NEW.export_authorization_version
              AND p.employment_status='active'
              AND r.code IN ('admin','provincial_manager') AND r.status='active' AND NOT r.is_external
              AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=transaction_timestamp()
              AND (a.valid_to IS NULL OR a.valid_to>transaction_timestamp())
              AND EXISTS (
                  SELECT 1 FROM public.role_permissions rp JOIN public.permissions pm ON pm.id=rp.permission_id
                  WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='report'
                    AND pm.action='export' AND pm.field_code=''
              )
              AND EXISTS (
                  SELECT 1 FROM public.role_permissions rp JOIN public.permissions pm ON pm.id=rp.permission_id
                  WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='inventory'
                    AND pm.action='read' AND pm.field_code=''
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.role_assignments da JOIN public.roles dr ON dr.id=da.role_id
                  JOIN public.role_permissions dp ON dp.role_id=dr.id
                  JOIN public.permissions denied ON denied.id=dp.permission_id
                  WHERE da.user_id=u.id AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
                    AND da.valid_from<=transaction_timestamp()
                    AND (da.valid_to IS NULL OR da.valid_to>transaction_timestamp())
                    AND dp.effect='deny' AND denied.field_code=''
                    AND ((denied.resource='report' AND denied.action='export')
                      OR (denied.resource='inventory' AND denied.action='read'))
              )
        ) THEN
            RAISE EXCEPTION '0136 current report authority required' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' OR OLD.job_type <> 'export' OR NEW.job_type <> 'export' THEN
        RAISE EXCEPTION '0136 runtime may update only export jobs' USING ERRCODE='23514';
    END IF;
    IF OLD.status='queued' AND NEW.status='running' THEN
        IF OLD.started_at IS NOT NULL OR NEW.started_at IS NULL
           OR NEW.started_at>transaction_timestamp()
           OR NEW.download_count<>0
           OR (to_jsonb(NEW)-ARRAY['status','started_at','updated_at'])
              IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','started_at','updated_at'])
        THEN RAISE EXCEPTION '0136 export claim invalid' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='running' AND NEW.status='succeeded' THEN
        IF NEW.completed_at IS NULL OR NEW.completed_at<OLD.started_at
           OR NEW.completed_at>transaction_timestamp()
           OR NEW.result_file_id IS NULL OR NEW.result_sha256 !~ '^[0-9a-f]{{64}}$'
           OR NEW.result_size_bytes IS NULL OR NEW.result_size_bytes<=0
           OR NEW.download_count<>0
           OR NOT EXISTS (
               SELECT 1 FROM public.files f WHERE f.id=NEW.result_file_id
                 AND f.status='available' AND f.uploaded_by=NEW.requested_by
                 AND f.metadata_jsonb->>'purpose'='inventory_report_export'
                 AND f.sha256=NEW.result_sha256 AND f.size_bytes=NEW.result_size_bytes
                 AND f.mime_type='{MIME}'
           )
           OR (to_jsonb(NEW)-ARRAY['status','completed_at','result_file_id',
                                  'result_sha256','result_size_bytes','updated_at'])
              IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','completed_at','result_file_id',
                                              'result_sha256','result_size_bytes','updated_at'])
        THEN RAISE EXCEPTION '0136 export result invalid' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='running' AND NEW.status='failed' THEN
        IF NEW.completed_at IS NULL OR NEW.completed_at<OLD.started_at
           OR NEW.completed_at>transaction_timestamp()
           OR NEW.error_detail IS NULL OR length(NEW.error_detail)>1000
           OR btrim(NEW.error_detail)=''
           OR (to_jsonb(NEW)-ARRAY['status','completed_at','error_detail','updated_at'])
              IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','completed_at','error_detail','updated_at'])
        THEN RAISE EXCEPTION '0136 export failure invalid' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='succeeded' AND NEW.status='succeeded' THEN
        IF NEW.download_count<>OLD.download_count+1
           OR (to_jsonb(NEW)-ARRAY['download_count','updated_at'])
              IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['download_count','updated_at'])
        THEN RAISE EXCEPTION '0136 download intent increment invalid' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '0136 export state transition invalid' USING ERRCODE='23514';
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()


def _postgresql(up: bool):
    op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0136 direct owner required'; END IF; END $$")
    op.execute('LOCK TABLE public.alembic_version,public.file_jobs,public.files,public.users,public.people,public.roles,public.role_assignments,public.role_permissions,public.permissions IN ACCESS EXCLUSIVE MODE')
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    if up:
        op.execute(f"CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $job${BODY}$job$")
        op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC,star_oam_api')
        op.execute(f'CREATE TRIGGER {ROW_TRIGGER} BEFORE INSERT OR UPDATE OR DELETE ON public.file_jobs FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()')
        op.execute(f'CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON public.file_jobs FOR EACH STATEMENT EXECUTE FUNCTION public.{FUNCTION}()')
        op.execute(f'ALTER TABLE public.file_jobs ENABLE ALWAYS TRIGGER {ROW_TRIGGER}')
        op.execute(f'ALTER TABLE public.file_jobs ENABLE ALWAYS TRIGGER {TRUNCATE_TRIGGER}')
        op.execute('REVOKE ALL ON TABLE public.file_jobs FROM PUBLIC,star_oam_api')
        op.execute('GRANT SELECT,INSERT ON TABLE public.file_jobs TO star_oam_api')
        op.execute('GRANT UPDATE (status,started_at,completed_at,result_file_id,result_sha256,result_size_bytes,error_detail,download_count,updated_at) ON TABLE public.file_jobs TO star_oam_api')
    else:
        helper['_preflight']("EXISTS(SELECT 1 FROM file_jobs WHERE job_type='export')", '0136 export jobs must be retained')
        op.execute('REVOKE ALL ON TABLE public.file_jobs FROM star_oam_api')
        op.execute(f'DROP TRIGGER {TRUNCATE_TRIGGER} ON public.file_jobs')
        op.execute(f'DROP TRIGGER {ROW_TRIGGER} ON public.file_jobs')
        op.execute(f'DROP FUNCTION public.{FUNCTION}()')


def _sqlite(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    if up:
        op.execute(f"""CREATE TRIGGER {ROW_TRIGGER}_insert BEFORE INSERT ON file_jobs
        WHEN NEW.job_type='export' AND (
          NEW.status<>'queued' OR NEW.export_authorization_version IS NULL
          OR NEW.export_authorization_version<=0 OR NEW.export_ledger_cursor IS NULL
          OR NEW.export_ledger_cursor<0 OR json_valid(NEW.parameters_jsonb)=0
          OR json_extract(NEW.parameters_jsonb,'$.report') IS NOT 'inventory_balances'
          OR json_valid(NEW.export_scope_jsonb)=0
          OR json_extract(NEW.export_scope_jsonb,'$.version') IS NOT 1
          OR json_type(NEW.export_scope_jsonb,'$.account_ids') IS NOT 'array'
          OR json_type(NEW.export_scope_jsonb,'$.assignment_ids') IS NOT 'array'
          OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
          OR NEW.result_file_id IS NOT NULL OR NEW.result_sha256 IS NOT NULL
          OR NEW.result_size_bytes IS NOT NULL OR NEW.download_count<>0)
        BEGIN SELECT RAISE(ABORT,'0136 queued export job shape invalid'); END""")
        op.execute(f"""CREATE TRIGGER {ROW_TRIGGER}_update BEFORE UPDATE ON file_jobs
        WHEN OLD.job_type='export' AND NOT (
          NEW.id IS OLD.id AND NEW.job_type IS OLD.job_type AND NEW.requested_by IS OLD.requested_by
          AND NEW.parameters_jsonb IS OLD.parameters_jsonb AND NEW.parameters_hash IS OLD.parameters_hash
          AND NEW.idempotency_key IS OLD.idempotency_key
          AND NEW.export_authorization_version IS OLD.export_authorization_version
          AND NEW.export_scope_jsonb IS OLD.export_scope_jsonb
          AND NEW.export_ledger_cursor IS OLD.export_ledger_cursor
          AND ((OLD.status='queued' AND NEW.status='running' AND NEW.started_at IS NOT NULL
                AND NEW.download_count=0 AND NEW.completed_at IS NULL AND NEW.result_file_id IS NULL)
            OR (OLD.status='running' AND NEW.status='failed' AND NEW.completed_at IS NOT NULL
                AND NEW.error_detail IS NOT NULL AND NEW.result_file_id IS NULL)
            OR (OLD.status='running' AND NEW.status='succeeded' AND NEW.completed_at IS NOT NULL
                AND NEW.result_file_id IS NOT NULL AND NEW.result_sha256 IS NOT NULL
                AND NEW.result_size_bytes>0 AND NEW.download_count=0)
            OR (OLD.status='succeeded' AND NEW.status='succeeded'
                AND NEW.download_count=OLD.download_count+1
                AND NEW.result_file_id IS OLD.result_file_id
                AND NEW.result_sha256 IS OLD.result_sha256
                AND NEW.result_size_bytes IS OLD.result_size_bytes)))
        BEGIN SELECT RAISE(ABORT,'0136 export transition invalid'); END""")
        op.execute(f"CREATE TRIGGER {ROW_TRIGGER}_delete BEFORE DELETE ON file_jobs WHEN OLD.job_type='export' BEGIN SELECT RAISE(ABORT,'0136 export jobs cannot be removed'); END")
    else:
        helper['_preflight']("EXISTS(SELECT 1 FROM file_jobs WHERE job_type='export')", '0136 export jobs must be retained')
        for name in (f'{ROW_TRIGGER}_delete', f'{ROW_TRIGGER}_update', f'{ROW_TRIGGER}_insert'):
            op.execute(f'DROP TRIGGER {name}')


def _transition(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        _postgresql(up)
    elif dialect == 'sqlite':
        _sqlite(up)
    else:
        raise RuntimeError('0136 PostgreSQL or SQLite required')
    if dialect == 'postgresql':
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
                expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
                replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
                replacements=((down_revision,revision),) if up else ((revision,down_revision),),
                label='report_export_job_boundary_0136')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
