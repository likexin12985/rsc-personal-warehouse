"""Persist immutable import coordinates and seal confirmation to count facts.

The error-file purpose binds only to a persisted rejected preview. Public
HTTP and worker integration must retain the same transaction boundaries.
"""

import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '20261120_0141'
down_revision = '20261119_0140'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
seals = runpy.run_path(str(FOLDER.parent / 'opening_import_seals_0141.py'))
previous = runpy.run_path(str(FOLDER / '20261119_0140_opening_count_source_purpose.py'))
report = runpy.run_path(str(FOLDER / '20261115_0136_report_export_job_boundary.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(previous['ready']['_ready'].replace(
    previous['ready']['_ready_parent'], revision).encode()).hexdigest()
REPORT_OLD_BODY = report['BODY']
REPORT_NEW_BODY = REPORT_OLD_BODY.replace(
    "    IF session_user <> 'star_oam_api' THEN",
    "    IF TG_OP IN ('INSERT','UPDATE') AND NEW.job_type='import' THEN\n"
    "        RETURN NEW; -- Exact import admission is owned by the 0141 guard.\n"
    "    END IF;\n    IF session_user <> 'star_oam_api' THEN", 1)
REPORT_NEW_HASH = hashlib.sha256(REPORT_NEW_BODY.encode()).hexdigest()
FUNCTION = 'rsc_guard_opening_import_job_0141'
TERMINAL_FUNCTION = 'rsc_check_opening_import_transaction_0141'
TRIGGER = 'trg_file_jobs_import_guard_0141'
TERMINAL_TRIGGER = 'trg_file_jobs_import_terminal_0141'
COLUMNS = ('import_binding_jsonb', 'import_preview_jsonb', 'import_completion_id',
           'import_error_sha256', 'import_error_size_bytes')
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _conditions(sqlite=False):
    def value(column, path):
        if sqlite:
            return f"json_extract(NEW.{column}, '$.{path}')"
        return "NEW." + column + "#>>'{" + path.replace('.', ',') + "}'"

    binding = lambda key: value('import_binding_jsonb', key)
    preview = lambda key: value('import_preview_jsonb', key)
    identifier = lambda expression: f"replace({expression},'-','')" if sqlite else expression
    as_text = lambda column: column if sqlite else f"{column}::text"
    obj = lambda column: f"json_type(NEW.{column})='object'" if sqlite else f"jsonb_typeof(NEW.{column})='object'"
    equal = 'IS' if sqlite else 'IS NOT DISTINCT FROM'
    parameters = "json_object('import','opening_count','template_version',1)" if sqlite else "jsonb_build_object('import','opening_count','template_version',1)"
    shape = f"""
      {obj('import_binding_jsonb')}
      AND {binding('version')} = {'1' if sqlite else "'1'"}
      AND NEW.parameters_jsonb {equal} {parameters}
      AND NEW.export_authorization_version IS NULL AND NEW.export_scope_jsonb IS NULL
      AND NEW.export_ledger_cursor IS NULL AND NEW.result_file_id IS NULL
      AND NEW.result_sha256 IS NULL AND NEW.result_size_bytes IS NULL
      AND length(NEW.parameters_hash)=64 AND length(NEW.idempotency_key)=64
      AND length({binding('source_sha256')})=64 AND length({binding('count_key_sha256')})=64
      AND CAST({binding('authorization_version')} AS bigint)>0
      AND NEW.download_count=0
    """
    relation = f"""EXISTS (
      SELECT 1 FROM files f JOIN stocktake_tasks t ON {as_text('t.id')}={identifier(binding('task_id'))}
      JOIN stocktake_rounds r ON {as_text('r.id')}={identifier(binding('round_id'))} AND r.task_id=t.id
      JOIN stocktake_scopes s ON {as_text('s.id')}={identifier(binding('scope_id'))} AND s.task_id=t.id
      WHERE {as_text('f.id')}={identifier(binding('source_file_id'))}
        AND f.status='available' AND f.uploaded_by=NEW.requested_by
        AND f.sha256={binding('source_sha256')} AND f.size_bytes BETWEEN 1 AND 8388608
        AND f.mime_type='{MIME}'
        AND {"json_extract(f.metadata_jsonb,'$.purpose')" if sqlite else "f.metadata_jsonb->>'purpose'"}='opening_count_import'
        AND {"json_extract(f.metadata_jsonb,'$.authorization_version')" if sqlite else "f.metadata_jsonb->>'authorization_version'"}={binding('authorization_version')}
        AND {"json_extract(f.metadata_jsonb,'$.uploader_person_id')" if sqlite else "f.metadata_jsonb->>'uploader_person_id'"}=(
            SELECT {as_text('u.person_id')} FROM users u WHERE u.id=NEW.requested_by)
        AND t.task_type='opening' AND t.status='counting' AND r.status='counting'
        AND t.current_round_no=r.round_no
        AND ((r.round_no=1 AND s.assignee_user_id=NEW.requested_by)
          OR (r.round_no>1 AND EXISTS (
            SELECT 1 FROM stocktake_recount_scope_assignments a
            WHERE a.recount_case_id=r.recount_case_id AND a.task_id=t.id
              AND a.scope_id=s.id AND a.assignee_user_id=NEW.requested_by)))
    )"""
    ready = f"""
      {obj('import_preview_jsonb')}
      AND {preview('version')} = {'1' if sqlite else "'1'"}
      AND {preview('source_sha256')}={binding('source_sha256')}
      AND length({preview('payload_sha256')})=64
      AND CAST({preview('row_count')} AS bigint) BETWEEN 1 AND 10000
      AND CAST({preview('count.observation_count')} AS bigint) BETWEEN 1 AND CAST({preview('row_count')} AS bigint)
      AND {preview('count.observation_count')}={preview('row_count')}
      AND CAST({preview('count.task_version')} AS bigint)>=0
      AND {preview('count.task_id')}={binding('task_id')}
      AND {preview('count.round_id')}={binding('round_id')}
      AND {preview('count.scope_id')}={binding('scope_id')}
      AND {preview('count.actor_authorization_version')}={binding('authorization_version')}
      AND length({preview('count.request_sha256')})=64
      AND length({preview('count.binding_sha256')})=64
      AND {"json_extract(NEW.import_preview_jsonb,'$.errors')='[]'" if sqlite else "NEW.import_preview_jsonb->'errors'='[]'::jsonb"}
      AND {"json_extract(NEW.import_preview_jsonb,'$.count.pending_verification_input_ordinals')='[]'" if sqlite else "NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}'='[]'::jsonb"}
    """
    completion = f"""EXISTS (
      SELECT 1 FROM stocktake_scope_count_completions c
      WHERE c.id=NEW.import_completion_id AND {as_text('c.task_id')}={identifier(binding('task_id'))}
        AND {as_text('c.round_id')}={identifier(binding('round_id'))}
        AND {as_text('c.scope_id')}={identifier(binding('scope_id'))}
        AND c.completed_by_user_id=NEW.requested_by
        AND c.authorization_version=CAST({binding('authorization_version')} AS bigint)
        AND c.idempotency_key_hash={binding('count_key_sha256')}
        AND c.request_sha256={preview('count.request_sha256')}
        AND c.completed_at>=NEW.started_at AND c.completed_at<=NEW.completed_at
        {'' if sqlite else 'AND c.xmin::text::numeric=mod(pg_current_xact_id()::text::numeric,4294967296)'}
    )"""
    if not sqlite:
        shape += " AND NEW.parameters_hash ~ '^[0-9a-f]{64}$' AND NEW.idempotency_key ~ '^[0-9a-f]{64}$'"
        for field in ('source_file_id','source_sha256','task_id','round_id','scope_id','count_key_sha256'):
            shape += f" AND jsonb_typeof(NEW.import_binding_jsonb->'{field}')='string'"
        ready += """
          AND NEW.import_preview_jsonb->'version'='1'::jsonb
          AND (SELECT count(*) FROM jsonb_object_keys(NEW.import_preview_jsonb))=6
          AND (SELECT count(*) FROM jsonb_object_keys(NEW.import_preview_jsonb->'count'))=9
          AND octet_length(NEW.import_preview_jsonb::text)<=2097152
          AND NEW.import_preview_jsonb->>'payload_sha256' ~ '^[0-9a-f]{64}$'
          AND NEW.import_preview_jsonb#>>'{count,request_sha256}' ~ '^[0-9a-f]{64}$'
          AND NEW.import_preview_jsonb#>>'{count,binding_sha256}' ~ '^[0-9a-f]{64}$'
          AND EXISTS(SELECT 1 FROM stocktake_tasks t
            WHERE t.id::text=NEW.import_binding_jsonb->>'task_id'
              AND t.version::text=NEW.import_preview_jsonb#>>'{count,task_version}')
        """
        for path in ('row_count','count.task_version','count.actor_authorization_version','count.observation_count'):
            pg_path = "'{" + path.replace('.', ',') + "}'"
            ready += f" AND jsonb_typeof(NEW.import_preview_jsonb#>{pg_path})='number'"
            ready += f" AND NEW.import_preview_jsonb#>>{pg_path} ~ '^[0-9]+$'"
    authority = previous['_AUTHORITY'].replace('NEW.uploaded_by', 'NEW.requested_by').replace(
        "(NEW.metadata_jsonb->>'authorization_version')::bigint",
        "(NEW.import_binding_jsonb->>'authorization_version')::bigint").replace('0140', '0141').replace('transaction_timestamp()', 'clock_timestamp()')
    return shape, relation, ready, completion, authority


SHAPE, RELATION, READY, COMPLETION, AUTHORITY = _conditions()
ERROR_PURPOSE = 'opening_count_import_error'
ERROR_MAX_BYTES = 2097152
PENDING_READY = READY.replace("NEW.import_preview_jsonb->'errors'='[]'::jsonb", 'TRUE').replace(
    "NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}'='[]'::jsonb",
    """jsonb_typeof(NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}')='array'
       AND jsonb_array_length(NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}')>0
       AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(
         NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}') ordinal
         WHERE jsonb_typeof(ordinal)<>'number' OR ordinal#>>'{}' !~ '^[1-9][0-9]*$'
           OR (ordinal#>>'{}')::numeric>(NEW.import_preview_jsonb#>>'{count,observation_count}')::numeric)
       AND (SELECT count(DISTINCT ordinal) FROM jsonb_array_elements(
         NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}') ordinal)
         =jsonb_array_length(NEW.import_preview_jsonb#>'{count,pending_verification_input_ordinals}')""")
_CURRENT_TASK_VERSION = """AND EXISTS(SELECT 1 FROM stocktake_tasks t
            WHERE t.id::text=NEW.import_binding_jsonb->>'task_id'
              AND t.version::text=NEW.import_preview_jsonb#>>'{count,task_version}')"""
assert _CURRENT_TASK_VERSION in PENDING_READY
PENDING_READY = PENDING_READY.replace(_CURRENT_TASK_VERSION, 'AND TRUE')
ERROR_PREVIEW = f"""
    jsonb_typeof(NEW.import_preview_jsonb)='object'
    AND (SELECT count(*) FROM jsonb_object_keys(NEW.import_preview_jsonb))=6
    AND octet_length(NEW.import_preview_jsonb::text)<=2097152
    AND NEW.import_preview_jsonb->'version'='1'::jsonb
    AND NEW.import_preview_jsonb->>'source_sha256'=NEW.import_binding_jsonb->>'source_sha256'
    AND jsonb_typeof(NEW.import_preview_jsonb->'row_count')='number'
    AND NEW.import_preview_jsonb->>'row_count' ~ '^[0-9]+$'
    AND (NEW.import_preview_jsonb->>'row_count')::numeric BETWEEN 0 AND 10000
    AND jsonb_typeof(NEW.import_preview_jsonb->'errors')='array'
    AND jsonb_array_length(NEW.import_preview_jsonb->'errors') BETWEEN 1 AND 1000
    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(NEW.import_preview_jsonb->'errors') e
      WHERE jsonb_typeof(e)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(e))<>4
        OR jsonb_typeof(e->'row') IS DISTINCT FROM 'number' OR e->>'row' !~ '^[0-9]+$'
        OR (e->>'row')::numeric NOT BETWEEN 2 AND 10001
        OR COALESCE(e->>'field','') NOT IN ('row','material_identifier_raw','material_identifier_type',
          'condition_code','availability_bucket','counted_qty','lot_no_raw','serial_no_raw',
          'serial_identifier_type','reason_code','remark')
        OR jsonb_typeof(e->'code') IS DISTINCT FROM 'string' OR e->>'code' !~ '^[a-z][a-z0-9_]{{0,79}}$'
        OR jsonb_typeof(e->'message') IS DISTINCT FROM 'string' OR length(e->>'message') NOT BETWEEN 1 AND 500
        OR e->>'message' ~ ('[' || chr(1) || '-' || chr(31) || chr(127) || ']') OR ltrim(e->>'message') ~ '^[=+@-]')
    AND CASE WHEN NEW.import_preview_jsonb->'count'='null'::jsonb
        THEN NEW.import_preview_jsonb->'payload_sha256'='null'::jsonb
        ELSE COALESCE(({PENDING_READY}),FALSE) END
"""
ERROR_FILE = f"""EXISTS (
    SELECT 1 FROM public.files f WHERE f.id=NEW.error_file_id AND f.status='available'
      AND f.uploaded_by=NEW.requested_by AND f.sha256=NEW.import_error_sha256
      AND f.size_bytes=NEW.import_error_size_bytes AND f.size_bytes BETWEEN 1 AND {ERROR_MAX_BYTES}
      AND f.mime_type='{MIME}' AND f.metadata_jsonb->>'purpose'='{ERROR_PURPOSE}'
      AND f.metadata_jsonb->>'idempotency_key_hash'=NEW.idempotency_key
      AND f.metadata_jsonb->>'authorization_version'=NEW.import_binding_jsonb->>'authorization_version'
)"""
BODY = f"""
DECLARE
    mutable_columns text[] := ARRAY['status','started_at','completed_at','confirmed_by',
        'import_preview_jsonb','import_completion_id','error_file_id','import_error_sha256',
        'import_error_size_bytes','error_detail','updated_at'];
BEGIN
    IF TG_OP='UPDATE' AND OLD.job_type IS DISTINCT FROM NEW.job_type THEN
        RAISE EXCEPTION '0141 file job type is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.job_type<>'import' THEN
        IF NEW.import_binding_jsonb IS NOT NULL OR NEW.import_preview_jsonb IS NOT NULL
           OR NEW.import_completion_id IS NOT NULL OR NEW.import_error_sha256 IS NOT NULL
           OR NEW.import_error_size_bytes IS NOT NULL THEN
            RAISE EXCEPTION '0141 import fields require an import job' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF session_user<>'star_oam_api' THEN
        RAISE EXCEPTION '0141 import writes require the API identity' USING ERRCODE='23514';
    END IF;
    IF NOT COALESCE(({SHAPE}),FALSE)
       OR jsonb_object_length_placeholder THEN
        RAISE EXCEPTION '0141 import binding shape invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' OR NEW.status IN ('prevalidating','awaiting_confirmation','running') THEN
        -- Task first, then principal graph; callers must use the same order.
        PERFORM 1 FROM public.stocktake_tasks t
          WHERE t.id::text=NEW.import_binding_jsonb->>'task_id' FOR UPDATE;
        {AUTHORITY}
        IF NOT COALESCE(({RELATION}),FALSE) THEN
            RAISE EXCEPTION '0141 current source and assigned opening scope required' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'queued' OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
           OR NEW.confirmed_by IS NOT NULL OR NEW.import_preview_jsonb IS NOT NULL
           OR NEW.import_completion_id IS NOT NULL OR NEW.error_file_id IS NOT NULL
           OR NEW.import_error_sha256 IS NOT NULL OR NEW.import_error_size_bytes IS NOT NULL
           OR NEW.error_detail IS NOT NULL THEN
            RAISE EXCEPTION '0141 import must start queued' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF to_jsonb(NEW)-mutable_columns IS DISTINCT FROM to_jsonb(OLD)-mutable_columns
       OR (OLD.import_preview_jsonb IS NOT NULL AND NEW.import_preview_jsonb IS DISTINCT FROM OLD.import_preview_jsonb)
       OR (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.import_error_sha256 IS NOT NULL AND (NEW.import_error_sha256 IS DISTINCT FROM OLD.import_error_sha256
           OR NEW.import_error_size_bytes IS DISTINCT FROM OLD.import_error_size_bytes))
       OR OLD.status IN ('succeeded','failed','cancelled') THEN
        RAISE EXCEPTION '0141 import identity, preview and terminal facts are immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.status IN ('prevalidating','awaiting_confirmation','running') THEN
        IF NEW.started_at IS NULL OR NEW.started_at<NEW.created_at OR NEW.started_at>clock_timestamp()
           OR NEW.completed_at IS NOT NULL OR NEW.import_completion_id IS NOT NULL
           OR NEW.error_file_id IS NOT NULL OR NEW.error_detail IS NOT NULL THEN
            RAISE EXCEPTION '0141 active import shape invalid' USING ERRCODE='23514';
        END IF;
        IF OLD.status='queued' AND NEW.status='prevalidating'
           AND NEW.import_preview_jsonb IS NULL AND NEW.confirmed_by IS NULL
           AND NEW.import_error_sha256 IS NULL AND NEW.import_error_size_bytes IS NULL THEN RETURN NEW; END IF;
        IF OLD.status='prevalidating' AND NEW.status='prevalidating' AND OLD.import_preview_jsonb IS NULL
           AND NEW.confirmed_by IS NULL AND COALESCE(({ERROR_PREVIEW}),FALSE)
           AND NEW.import_error_sha256 ~ '^[0-9a-f]{{64}}$'
           AND NEW.import_error_size_bytes BETWEEN 1 AND {ERROR_MAX_BYTES} THEN RETURN NEW; END IF;
        IF NEW.import_error_sha256 IS NOT NULL OR NEW.import_error_size_bytes IS NOT NULL
           OR NOT COALESCE(({READY}),FALSE) THEN
            RAISE EXCEPTION '0141 complete verified preview required' USING ERRCODE='23514';
        END IF;
        IF OLD.status='prevalidating' AND NEW.status='awaiting_confirmation'
           AND NEW.confirmed_by IS NULL THEN RETURN NEW; END IF;
        IF OLD.status='awaiting_confirmation' AND NEW.status='running'
           AND NEW.confirmed_by=NEW.requested_by THEN RETURN NEW; END IF;
    END IF;
    IF NEW.status IN ('succeeded','failed','cancelled') THEN
        IF NEW.completed_at IS NULL OR NEW.completed_at<COALESCE(OLD.started_at,OLD.created_at)
           OR NEW.completed_at>clock_timestamp()
           OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION '0141 import completion time invalid' USING ERRCODE='23514';
        END IF;
        IF OLD.status='running' AND NEW.status='succeeded'
           AND NEW.confirmed_by=OLD.confirmed_by AND NEW.confirmed_by=NEW.requested_by
           AND NEW.error_file_id IS NULL AND NEW.import_error_sha256 IS NULL
           AND NEW.import_error_size_bytes IS NULL AND NEW.error_detail IS NULL
           AND COALESCE(({COMPLETION}),FALSE) THEN RETURN NEW; END IF;
        IF OLD.status='prevalidating' AND NEW.status='failed' AND NEW.import_completion_id IS NULL
           AND NEW.confirmed_by IS NULL AND NEW.error_detail='opening_import_prevalidation_failed'
           AND OLD.import_error_sha256 IS NOT NULL
           AND NEW.import_preview_jsonb IS NOT DISTINCT FROM OLD.import_preview_jsonb
           AND COALESCE(({ERROR_PREVIEW}),FALSE) AND COALESCE(({ERROR_FILE}),FALSE) THEN RETURN NEW; END IF;
        IF OLD.status IN ('queued','prevalidating','awaiting_confirmation')
           AND NEW.status IN ('failed','cancelled') AND NEW.import_completion_id IS NULL
           AND NEW.confirmed_by IS NULL AND NEW.error_file_id IS NULL
           AND NEW.import_error_sha256 IS NOT DISTINCT FROM OLD.import_error_sha256
           AND NEW.import_error_size_bytes IS NOT DISTINCT FROM OLD.import_error_size_bytes
           AND NEW.import_preview_jsonb IS NOT DISTINCT FROM OLD.import_preview_jsonb
        THEN
            IF NEW.status='cancelled' AND NEW.error_detail='opening_import_cancelled' THEN
                {AUTHORITY}
                RETURN NEW;
            END IF;
            IF NEW.status='failed' AND NEW.error_detail IN
               ('opening_import_context_changed','opening_import_source_invalid') THEN RETURN NEW; END IF;
        END IF;
    END IF;
    RAISE EXCEPTION '0141 import transition invalid' USING ERRCODE='23514';
END;
""".replace('jsonb_object_length_placeholder', "(SELECT count(*) FROM jsonb_object_keys(NEW.import_binding_jsonb))<>8\n"
    "       OR NEW.import_binding_jsonb->'version' IS DISTINCT FROM '1'::jsonb\n"
    "       OR jsonb_typeof(NEW.import_binding_jsonb->'authorization_version') IS DISTINCT FROM 'number'\n"
    "       OR NEW.import_binding_jsonb->>'authorization_version' !~ '^[1-9][0-9]*$'\n"
    "       OR NEW.import_binding_jsonb->>'count_key_sha256' IS DISTINCT FROM\n"
    "          encode(sha256(convert_to('cloud_oam.opening_stocktake.scope_count.idempotency.v1','UTF8')\n"
    "          ||decode('00','hex')||convert_to(NEW.idempotency_key,'UTF8')),'hex')")
BODY_HASH = hashlib.sha256(BODY.encode()).hexdigest()
TERMINAL_BODY = """
BEGIN
    IF EXISTS(SELECT 1 FROM public.file_jobs WHERE id=NEW.id AND job_type='import' AND status='running') THEN
        RAISE EXCEPTION '0141 import count and result must commit together' USING ERRCODE='23514';
    END IF;
    IF NEW.job_type='import' AND NEW.status IN ('failed','cancelled')
       AND NEW.error_detail IN ('opening_import_cancelled','opening_import_context_changed','opening_import_source_invalid')
       AND NOT EXISTS (
          SELECT 1 FROM public.audit_events a
          WHERE a.aggregate_type='file_job' AND a.aggregate_id=NEW.id::text
            AND a.action='opening_count_import_terminated'
            AND a.after_jsonb->>'status'=NEW.status AND a.after_jsonb->>'reason'=NEW.error_detail
            AND a.after_jsonb->>'source_file_id'=NEW.import_binding_jsonb->>'source_file_id'
            AND a.after_jsonb->>'error_sha256' IS NOT DISTINCT FROM NEW.import_error_sha256
            AND a.occurred_at=NEW.completed_at
            AND ((NEW.status='cancelled' AND a.actor_user_id=NEW.requested_by)
              OR (NEW.status='failed' AND a.actor_user_id IS NULL))
            AND a.xmin::text::numeric=mod(pg_current_xact_id()::text::numeric,4294967296)
       ) THEN
        RAISE EXCEPTION '0141 import termination requires same transaction audit' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
"""
TERMINAL_HASH = hashlib.sha256(TERMINAL_BODY.encode()).hexdigest()


FILE_OLD_BODY = previous['NEW_BODY']
FILE_OLD_HASH = previous['NEW_FILE_HASH']


def _error_file_body():
    body = FILE_OLD_BODY
    marker = "'daily_reconciliation_evidence', 'inventory_report_export', 'opening_count_import')"
    assert body.count(marker) == 3
    body = body.replace(marker, marker[:-1] + f", '{ERROR_PURPOSE}')")
    for alias, count in (('NEW', 2), ('OLD', 1)):
        purpose = f"({alias}.metadata_jsonb->>'purpose')"
        pair = f"({purpose} IN ('inventory_report_export', 'opening_count_import'))"
        assert body.count(pair) == count
        body = body.replace(pair, f"({purpose} IN ('inventory_report_export', 'opening_count_import', '{ERROR_PURPOSE}'))")
        bound = f"{alias}.size_bytes BETWEEN 1 AND 125829120"
        assert body.count(bound) == count
        body = body.replace(bound, bound + f" AND ({purpose}<>'{ERROR_PURPOSE}' OR {alias}.size_bytes<={ERROR_MAX_BYTES})")
    authority = previous['_AUTHORITY'].replace('0140', '0141').replace('transaction_timestamp()', 'clock_timestamp()')
    admission = f"""
    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{ERROR_PURPOSE}' THEN
        IF session_user<>'star_oam_api' THEN
            RAISE EXCEPTION '0141 error file requires API identity' USING ERRCODE='23514';
        END IF;
        {authority}
        IF NOT EXISTS (
            SELECT 1 FROM public.file_jobs j
            WHERE j.job_type='import' AND j.status='prevalidating'
              AND j.requested_by=NEW.uploaded_by AND j.import_error_sha256=NEW.sha256
              AND j.import_error_size_bytes=NEW.size_bytes
              AND j.idempotency_key=NEW.metadata_jsonb->>'idempotency_key_hash'
              AND j.import_binding_jsonb->>'authorization_version'=NEW.metadata_jsonb->>'authorization_version'
              AND jsonb_array_length(j.import_preview_jsonb->'errors')>0
        ) THEN
            RAISE EXCEPTION '0141 error file must match its prepared import' USING ERRCODE='23514';
        END IF;
    END IF;
"""
    return body.replace('BEGIN\n', 'BEGIN\n' + admission, 1)


FILE_NEW_BODY = _error_file_body()
FILE_NEW_HASH = hashlib.sha256(FILE_NEW_BODY.encode()).hexdigest()


def _schema(up):
    if up:
        for name in COLUMNS:
            type_ = (sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True),'postgresql')
                     if name.endswith('_jsonb') else sa.Uuid() if name.endswith('_id')
                     else sa.BigInteger() if name.endswith('_bytes') else sa.String(64))
            op.add_column('file_jobs',sa.Column(name,type_,nullable=True))
        op.create_index('uq_file_jobs_import_completion','file_jobs',['import_completion_id'],unique=True)
        op.create_index('uq_file_jobs_error_file_id','file_jobs',['error_file_id'],unique=True)
    else:
        op.drop_index('uq_file_jobs_error_file_id',table_name='file_jobs')
        op.drop_index('uq_file_jobs_import_completion',table_name='file_jobs')
        for name in reversed(COLUMNS):
            op.drop_column('file_jobs',name)


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'): raise RuntimeError('0141 PostgreSQL or SQLite required')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0141 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.file_jobs,public.files,public.stocktake_tasks,public.stocktake_rounds,public.stocktake_scopes,public.stocktake_scope_count_completions IN ACCESS EXCLUSIVE MODE')
    helper['_preflight']("EXISTS(SELECT 1 FROM file_jobs WHERE job_type='import')",
        '0141 existing import jobs require retention and explicit migration')
    purpose_expression = "metadata_jsonb->>'purpose'" if dialect=='postgresql' else "json_extract(metadata_jsonb,'$.purpose')"
    helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE {purpose_expression}='{ERROR_PURPOSE}')",
        '0141 import error files must be retained')
    if not up:
        if dialect=='postgresql': op.execute('LOCK TABLE public.opening_import_command_seals IN ACCESS EXCLUSIVE MODE')
        helper['_preflight']('EXISTS(SELECT 1 FROM opening_import_command_seals)', '0141 original import seals must be retained')
    if up:
        _schema(True)
        seals['schema'](True)
    else:
        seals['triggers'](False)
    if dialect=='postgresql':
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_guard_formal_file_object_0036()',
            expected_hash=FILE_OLD_HASH if up else FILE_NEW_HASH,
            replacement_hash=FILE_NEW_HASH if up else FILE_OLD_HASH,
            replacements=((FILE_OLD_BODY,FILE_NEW_BODY),) if up else ((FILE_NEW_BODY,FILE_OLD_BODY),),
            label='opening_import_error_file_0141')
        replace(signature='public.rsc_guard_report_export_job_0136()',
            expected_hash=report['FUNCTION_HASH'] if up else REPORT_NEW_HASH,
            replacement_hash=REPORT_NEW_HASH if up else report['FUNCTION_HASH'],
            replacements=((REPORT_OLD_BODY,REPORT_NEW_BODY),) if up else ((REPORT_NEW_BODY,REPORT_OLD_BODY),),
            label='opening_import_dispatch_0141')
        if up:
            for name,body in ((FUNCTION,BODY),(TERMINAL_FUNCTION,TERMINAL_BODY)):
                op.execute(f'CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $guard${body}$guard$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC,star_oam_api')
            op.execute(f'CREATE TRIGGER {TRIGGER} BEFORE INSERT OR UPDATE ON public.file_jobs FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()')
            op.execute(f'CREATE CONSTRAINT TRIGGER {TERMINAL_TRIGGER} AFTER INSERT OR UPDATE ON public.file_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{TERMINAL_FUNCTION}()')
            for trigger in (TRIGGER,TERMINAL_TRIGGER): op.execute(f'ALTER TABLE public.file_jobs ENABLE ALWAYS TRIGGER {trigger}')
            op.execute('GRANT UPDATE (import_preview_jsonb,import_completion_id,confirmed_by,error_file_id,import_error_sha256,import_error_size_bytes) ON TABLE public.file_jobs TO star_oam_api')
        else:
            for trigger in (TERMINAL_TRIGGER,TRIGGER): op.execute(f'DROP TRIGGER {trigger} ON public.file_jobs')
            for name in (TERMINAL_FUNCTION,FUNCTION): op.execute(f'DROP FUNCTION public.{name}()')
            op.execute('REVOKE UPDATE (import_preview_jsonb,import_completion_id,confirmed_by,error_file_id,import_error_sha256,import_error_size_bytes) ON TABLE public.file_jobs FROM star_oam_api')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='opening_import_readiness_0141')
    else:
        # Development SQLite cannot prove the production role and deferred
        # transaction boundary. Import lifecycle acceptance uses real PG16.
        if up:
            op.execute("CREATE TRIGGER trg_file_jobs_import_pending_0141 BEFORE INSERT ON file_jobs WHEN NEW.job_type='import' BEGIN SELECT RAISE(ABORT,'0141 import requires PostgreSQL 16'); END")
        else: op.execute('DROP TRIGGER trg_file_jobs_import_pending_0141')
    if up: seals['triggers'](True)
    else:
        seals['schema'](False)
        _schema(False)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
