"""Allow a worker-owned private XLSX result purpose without opening evidence uploads."""

from pathlib import Path
import hashlib
import runpy

from alembic import op


revision = '20261116_0137'
down_revision = '20261115_0136'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261115_0136_report_export_job_boundary.py'))
evidence = runpy.run_path(str(FOLDER / '20261112_0133_daily_review_evidence.py'))
ready = runpy.run_path(str(FOLDER / '20261108_0129_notification_expansion_status.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
PURPOSE = 'inventory_report_export'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SIGNATURE = 'public.rsc_guard_formal_file_object_0036()'
OLD_BODY = evidence['SOURCES'][SIGNATURE][1]
OLD_FILE_HASH = hashlib.sha256(OLD_BODY.encode()).hexdigest()


def _add_shape(source: str, *, sqlite: bool) -> str:
    purpose_marker = "'daily_reconciliation_evidence')"
    assert source.count(purpose_marker) == (1 if sqlite else 3)
    source = source.replace(purpose_marker, f"'daily_reconciliation_evidence', '{PURPOSE}')")
    mime_marker = "'image/heic','image/heif','video/mp4','video/quicktime'"
    assert source.count(mime_marker) == (1 if sqlite else 3)
    source = source.replace(mime_marker, mime_marker + f",'{MIME}'")
    aliases = ((source.split('.', 1)[0], 1),) if sqlite else (('OLD', 1), ('NEW', 2))
    for alias, count in aliases:
        needle = f")\nAND {alias}.original_filename IS NOT NULL"
        assert source.count(needle) == count
        if not count:
            continue
        report_purpose = (
            f"json_extract({alias}.metadata_jsonb, '$.purpose')"
            if sqlite else f"({alias}.metadata_jsonb->>'purpose')"
        )
        branch = (
            f" OR ({alias}.mime_type = '{MIME}' AND lower({alias}.original_filename) LIKE '%.xlsx')\n"
            f")\nAND (({report_purpose} = '{PURPOSE}') = ({alias}.mime_type = '{MIME}'))"
            f"\nAND {alias}.original_filename IS NOT NULL"
        )
        source = source.replace(needle, branch)
    return source


_AUTHORITY = f"""
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.uploaded_by]::text[]);
        IF NOT EXISTS (
            SELECT 1 FROM public.users u JOIN public.people person ON person.id=u.person_id
            JOIN public.role_assignments a ON a.user_id=u.id
            JOIN public.roles r ON r.id=a.role_id
            WHERE u.id=NEW.uploaded_by AND u.is_active AND u.account_status='active'
              AND u.authorization_version=(NEW.metadata_jsonb->>'authorization_version')::bigint
              AND person.employment_status='active'
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
            RAISE EXCEPTION '0137 current report authority required' USING ERRCODE='23514';
        END IF;
"""

NEW_BODY = _add_shape(OLD_BODY, sqlite=False).replace(
    'BEGIN\n',
    f"BEGIN\n    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN\n{_AUTHORITY}    END IF;\n",
    1,
)
NEW_FILE_HASH = hashlib.sha256(NEW_BODY.encode()).hexdigest()


def _sqlite_namespace(expanded: bool):
    namespace = evidence['files'](True)
    if expanded:
        shape = namespace['_sqlite_file_shape_sql']
        globals_ = shape.__globals__
        old = globals_['FORMAL_PURPOSES_SQL']
        assert old.endswith("'daily_reconciliation_evidence')")
        globals_['FORMAL_PURPOSES_SQL'] = old[:-1] + f", '{PURPOSE}')"

        def report_shape(alias, *, status, require_current_identity):
            source = shape(alias, status=status, require_current_identity=require_current_identity)
            # The purpose list was expanded by the shared generator already.
            source = source.replace(f"'daily_reconciliation_evidence', '{PURPOSE}')",
                                    "'daily_reconciliation_evidence')")
            return _add_shape(source, sqlite=True)

        globals_['_sqlite_file_shape_sql'] = report_shape
    return namespace


def _sqlite_files(up: bool):
    namespace = _sqlite_namespace(up)
    for name in namespace['_sqlite_trigger_names']():
        op.execute('DROP TRIGGER ' + name)
    namespace['_create_sqlite_triggers']()
    role_check = f"""EXISTS (
      SELECT 1 FROM users u JOIN people p ON p.id=u.person_id
      JOIN role_assignments a ON a.user_id=u.id JOIN roles r ON r.id=a.role_id
      WHERE u.id=NEW.uploaded_by AND u.is_active AND u.account_status='active'
        AND p.employment_status='active' AND r.code IN ('admin','provincial_manager')
        AND r.status='active' AND NOT r.is_external AND a.status IN ('active','scheduled')
        AND a.revoked_at IS NULL AND a.valid_from<=CURRENT_TIMESTAMP
        AND (a.valid_to IS NULL OR a.valid_to>CURRENT_TIMESTAMP)
        AND u.authorization_version=json_extract(NEW.metadata_jsonb,'$.authorization_version')
        AND EXISTS(SELECT 1 FROM role_permissions rp JOIN permissions pm ON pm.id=rp.permission_id
          WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='report' AND pm.action='export' AND pm.field_code='')
        AND EXISTS(SELECT 1 FROM role_permissions rp JOIN permissions pm ON pm.id=rp.permission_id
          WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='inventory' AND pm.action='read' AND pm.field_code='')
    )"""
    for event in ('INSERT', 'UPDATE'):
        name = f'trg_files_report_export_{event.lower()}_0137'
        if up:
            op.execute(f"CREATE TRIGGER {name} BEFORE {event} ON files WHEN json_extract(NEW.metadata_jsonb,'$.purpose')='{PURPOSE}' AND NOT ({role_check}) BEGIN SELECT RAISE(ABORT,'0137 current report authority required'); END")
        else:
            op.execute(f'DROP TRIGGER {name}')


def _transition(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0137 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.files,public.file_jobs,public.users,public.people,public.roles,public.role_assignments,public.role_permissions,public.permissions IN ACCESS EXCLUSIVE MODE')
        if not up:
            helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE metadata_jsonb->>'purpose'='{PURPOSE}')", '0137 report files must be retained')
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature=SIGNATURE, expected_hash=OLD_FILE_HASH if up else NEW_FILE_HASH,
                replacement_hash=NEW_FILE_HASH if up else OLD_FILE_HASH,
                replacements=((OLD_BODY,NEW_BODY),) if up else ((NEW_BODY,OLD_BODY),),
                label='report_export_file_purpose_0137')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
                expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
                replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
                replacements=((down_revision,revision),) if up else ((revision,down_revision),),
                label='report_export_file_readiness_0137')
    elif dialect == 'sqlite':
        if not up:
            helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE json_extract(metadata_jsonb,'$.purpose')='{PURPOSE}')", '0137 report files must be retained')
        _sqlite_files(up)
    else:
        raise RuntimeError('0137 PostgreSQL or SQLite required')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
