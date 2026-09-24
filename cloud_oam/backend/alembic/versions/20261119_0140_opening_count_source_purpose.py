"""Admit bounded private opening-count XLSX uploads with current count authority."""

from pathlib import Path
import hashlib
import runpy

from alembic import op


revision = '20261119_0140'
down_revision = '20261118_0139'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261118_0139_report_result_job_binding.py'))
report_file = previous['previous']['previous']
ready = previous['ready']
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
SIGNATURE = 'public.rsc_guard_formal_file_object_0036()'
PURPOSE = 'opening_count_import'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAX_BYTES = 8 * 1024 * 1024
OLD_BODY = report_file['NEW_BODY']
OLD_FILE_HASH = report_file['NEW_FILE_HASH']


def _extend_shape(source: str, *, sqlite: bool) -> str:
    purpose_marker = "'daily_reconciliation_evidence', 'inventory_report_export')"
    assert source.count(purpose_marker) == (1 if sqlite else 3)
    source = source.replace(purpose_marker, f"'daily_reconciliation_evidence', 'inventory_report_export', '{PURPOSE}')")
    for alias in ((source.split('.', 1)[0],) if sqlite else ('NEW', 'OLD')):
        purpose = (f"json_extract({alias}.metadata_jsonb, '$.purpose')" if sqlite
                   else f"({alias}.metadata_jsonb->>'purpose')")
        pair = f"({purpose} = 'inventory_report_export')"
        assert source.count(pair) == (1 if sqlite else (2 if alias == 'NEW' else 1))
        source = source.replace(pair, f"({purpose} IN ('inventory_report_export', '{PURPOSE}'))")
        size = f"{alias}.size_bytes BETWEEN 1 AND 125829120"
        assert source.count(size) == (1 if sqlite else (2 if alias == 'NEW' else 1))
        source = source.replace(size, size + f" AND ({purpose} <> '{PURPOSE}' OR {alias}.size_bytes <= {MAX_BYTES})")
    return source


_AUTHORITY = """
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.uploaded_by]::text[]);
        IF NOT EXISTS (
            SELECT 1 FROM public.users u JOIN public.people person ON person.id=u.person_id
            JOIN public.role_assignments a ON a.user_id=u.id
            JOIN public.roles r ON r.id=a.role_id
            WHERE u.id=NEW.uploaded_by AND u.is_active AND u.account_status='active'
              AND u.authorization_version=(NEW.metadata_jsonb->>'authorization_version')::bigint
              AND person.employment_status='active'
              AND r.code IN ('admin','provincial_manager','technician')
              AND r.status='active' AND NOT r.is_external
              AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=transaction_timestamp()
              AND (a.valid_to IS NULL OR a.valid_to>transaction_timestamp())
              AND EXISTS (
                  SELECT 1 FROM public.role_permissions rp JOIN public.permissions pm ON pm.id=rp.permission_id
                  WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='stocktake'
                    AND pm.action='count' AND pm.field_code=''
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.role_assignments da JOIN public.roles dr ON dr.id=da.role_id
                  JOIN public.role_permissions dp ON dp.role_id=dr.id
                  JOIN public.permissions denied ON denied.id=dp.permission_id
                  WHERE da.user_id=u.id AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
                    AND da.valid_from<=transaction_timestamp()
                    AND (da.valid_to IS NULL OR da.valid_to>transaction_timestamp())
                    AND dp.effect='deny' AND denied.resource='stocktake'
                    AND denied.action='count' AND denied.field_code=''
              )
        ) THEN
            RAISE EXCEPTION '0140 current stocktake count authority required' USING ERRCODE='23514';
        END IF;
"""

NEW_BODY = _extend_shape(OLD_BODY, sqlite=False).replace(
    'BEGIN\n',
    f"BEGIN\n    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN\n{_AUTHORITY}    END IF;\n",
    1,
)
NEW_FILE_HASH = hashlib.sha256(NEW_BODY.encode()).hexdigest()


def _sqlite_namespace(expanded: bool):
    namespace = report_file['_sqlite_namespace'](True)
    if expanded:
        shape = namespace['_sqlite_file_shape_sql']
        globals_ = shape.__globals__
        old_purposes = globals_['FORMAL_PURPOSES_SQL']
        assert old_purposes.endswith("'inventory_report_export')")
        globals_['FORMAL_PURPOSES_SQL'] = old_purposes[:-1] + f", '{PURPOSE}')"

        def import_shape(alias, *, status, require_current_identity):
            source = shape(alias, status=status, require_current_identity=require_current_identity)
            # The shared generator emits the expanded purpose list. Rebuild
            # both later shape layers from its original evidence-only marker.
            source = source.replace(
                f"'daily_reconciliation_evidence', 'inventory_report_export', '{PURPOSE}')",
                "'daily_reconciliation_evidence')",
            )
            source = report_file['_add_shape'](source, sqlite=True)
            return _extend_shape(source, sqlite=True)

        globals_['_sqlite_file_shape_sql'] = import_shape
    return namespace


def _sqlite_files(up: bool):
    namespace = _sqlite_namespace(up)
    for name in namespace['_sqlite_trigger_names']():
        op.execute('DROP TRIGGER ' + name)
    namespace['_create_sqlite_triggers']()
    authority = """EXISTS (
      SELECT 1 FROM users u JOIN people p ON p.id=u.person_id
      JOIN role_assignments a ON a.user_id=u.id JOIN roles r ON r.id=a.role_id
      WHERE u.id=NEW.uploaded_by AND u.is_active AND u.account_status='active'
        AND u.authorization_version=json_extract(NEW.metadata_jsonb,'$.authorization_version')
        AND p.employment_status='active' AND r.code IN ('admin','provincial_manager','technician')
        AND r.status='active' AND NOT r.is_external AND a.status IN ('active','scheduled')
        AND a.revoked_at IS NULL AND a.valid_from<=CURRENT_TIMESTAMP
        AND (a.valid_to IS NULL OR a.valid_to>CURRENT_TIMESTAMP)
        AND EXISTS(SELECT 1 FROM role_permissions rp JOIN permissions pm ON pm.id=rp.permission_id
          WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='stocktake' AND pm.action='count' AND pm.field_code='')
        AND NOT EXISTS(SELECT 1 FROM role_assignments da JOIN roles dr ON dr.id=da.role_id
          JOIN role_permissions dp ON dp.role_id=dr.id JOIN permissions denied ON denied.id=dp.permission_id
          WHERE da.user_id=u.id AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
            AND da.valid_from<=CURRENT_TIMESTAMP AND (da.valid_to IS NULL OR da.valid_to>CURRENT_TIMESTAMP)
            AND dp.effect='deny' AND denied.resource='stocktake' AND denied.action='count' AND denied.field_code='')
    )"""
    for event in ('INSERT', 'UPDATE'):
        name = f'trg_files_opening_count_import_{event.lower()}_0140'
        if up:
            op.execute(f"CREATE TRIGGER {name} BEFORE {event} ON files WHEN json_extract(NEW.metadata_jsonb,'$.purpose')='{PURPOSE}' AND NOT ({authority}) BEGIN SELECT RAISE(ABORT,'0140 current stocktake count authority required'); END")
        else:
            op.execute(f'DROP TRIGGER {name}')


def _transition(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0140 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.files,public.users,public.people,public.roles,public.role_assignments,public.role_permissions,public.permissions IN ACCESS EXCLUSIVE MODE')
        if not up:
            helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE metadata_jsonb->>'purpose'='{PURPOSE}')", '0140 import source files must be retained')
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature=SIGNATURE, expected_hash=OLD_FILE_HASH if up else NEW_FILE_HASH,
                replacement_hash=NEW_FILE_HASH if up else OLD_FILE_HASH,
                replacements=((OLD_BODY, NEW_BODY),) if up else ((NEW_BODY, OLD_BODY),),
                label='opening_count_source_purpose_0140')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
                expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
                replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
                replacements=((down_revision, revision),) if up else ((revision, down_revision),),
                label='opening_count_source_readiness_0140')
    elif dialect == 'sqlite':
        if not up:
            helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE json_extract(metadata_jsonb,'$.purpose')='{PURPOSE}')", '0140 import source files must be retained')
        _sqlite_files(up)
    else:
        raise RuntimeError('0140 PostgreSQL or SQLite required')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
