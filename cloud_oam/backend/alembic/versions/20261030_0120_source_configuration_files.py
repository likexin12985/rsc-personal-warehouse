"""Purpose-bound completed evidence for source configuration and publication."""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = '20261030_0120'
down_revision = '20261029_0119'
branch_labels = depends_on = None
PURPOSE = 'source_configuration_evidence'
FUNCTION = 'rsc_guard_source_evidence_0120'
TABLES = ('inventory_control_authority_decisions', 'inventory_control_mapping_decisions',
          'material_source_authority_decisions', 'material_projection_publications')
TRIGGERS = {f'trg_{table}_file_0120': table for table in TABLES}
_folder = Path(__file__).parent
_receipt = runpy.run_path(str(_folder/'20260926_0086_receipt_evidence_files.py'))
_previous = runpy.run_path(str(_folder/'20261029_0119_material_publications.py'))
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_previous['_ready'].replace(_previous['_older']['revision'], revision).encode()).hexdigest()


def files(expanded=True):
    namespace = _receipt['_files'](True)
    if expanded:
        globals_ = namespace['_postgresql_file_function_sql'].__globals__
        globals_['FORMAL_PURPOSES_SQL'] = globals_['FORMAL_PURPOSES_SQL'].replace(
            "'receipt_exception_evidence')", f"'receipt_exception_evidence', '{PURPOSE}')")
    return namespace


def uploader_sql(dialect, alias='NEW'):
    prefix = 'public.' if dialect == 'postgresql' else ''
    version = f"({alias}.metadata_jsonb->>'authorization_version')::bigint" if dialect == 'postgresql' else f"json_extract({alias}.metadata_jsonb,'$.authorization_version')"
    # A deny removes that resource's permission; the other explicit source
    # authority may still independently authorize this shared upload purpose.
    return f"""EXISTS (
        SELECT 1 FROM {prefix}users u JOIN {prefix}people person ON person.id=u.person_id
        JOIN {prefix}organizations o ON o.id=person.organization_id
        JOIN {prefix}role_assignments a ON a.user_id=u.id JOIN {prefix}roles r ON r.id=a.role_id
        JOIN {prefix}role_permissions rp ON rp.role_id=r.id JOIN {prefix}permissions p ON p.id=rp.permission_id
        WHERE u.id={alias}.uploaded_by AND u.is_active AND u.account_status='active'
          AND u.authorization_version={version} AND person.employment_status='active'
          AND o.org_type='headquarters' AND o.status='active'
          AND r.code='admin' AND r.status='active' AND NOT r.is_external
          AND a.scope_type='national' AND a.scope_id='*' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=CURRENT_TIMESTAMP AND (a.valid_to IS NULL OR a.valid_to>CURRENT_TIMESTAMP)
          AND p.resource IN ('inventory_control','material_source') AND p.action='authorize' AND p.field_code='' AND rp.effect='allow'
          AND EXISTS (SELECT 1 FROM {prefix}auth_identities i WHERE i.user_id=u.id AND i.status='active'
              AND i.revoked_at IS NULL AND i.verified_at<=CURRENT_TIMESTAMP)
          AND NOT EXISTS (SELECT 1 FROM {prefix}role_assignments da JOIN {prefix}roles dr ON dr.id=da.role_id
              JOIN {prefix}role_permissions dp ON dp.role_id=dr.id JOIN {prefix}permissions d ON d.id=dp.permission_id
              WHERE da.user_id=u.id AND dr.status='active' AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
                AND da.scope_type='national' AND da.scope_id='*' AND da.valid_from<=CURRENT_TIMESTAMP
                AND (da.valid_to IS NULL OR da.valid_to>CURRENT_TIMESTAMP) AND dp.effect='deny'
                AND d.resource=p.resource AND d.action='authorize' AND d.field_code='')
    )"""


def source_changes():
    before = files(False)['_postgresql_file_function_sql']().split('AS $$', 1)[1].rsplit('$$', 1)[0]
    after = files()['_postgresql_file_function_sql']().split('AS $$', 1)[1].rsplit('$$', 1)[0]
    check = f"""
    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.uploaded_by]::text[]);
        IF NOT ({uploader_sql('postgresql')}) THEN
            RAISE EXCEPTION '0120 active headquarters source reviewer required' USING ERRCODE='23514';
        END IF;
    END IF;
"""
    after = after.replace('BEGIN\n', 'BEGIN\n' + check, 1)
    return before, after


FILE_HASHES = tuple(hashlib.sha256(body.encode()).hexdigest() for body in source_changes())


def binding_sql(dialect, alias='NEW'):
    # Uploader and reviewer may be different authorized headquarters people.
    # Preserve historical evidence even if the uploader later changes roles.
    return files()[f'_{dialect}_binding_file_sql'](
        file_expression=f'{alias}.evidence_file_id', purpose=PURPOSE,
        user_expression='file_row.uploaded_by', person_expression=None,
        bound_at_expression=f'{alias}.created_at', require_current_identity=False)


BODY = f"""
BEGIN
    PERFORM id FROM public.files WHERE id=NEW.evidence_file_id FOR SHARE;
    IF NOT ({binding_sql('postgresql')}) THEN
        RAISE EXCEPTION '0120 completed source configuration evidence required' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""
FUNCTION_HASH = hashlib.sha256(BODY.encode()).hexdigest()


def _sqlite_files(expanded):
    namespace = files(expanded)
    for name in namespace['_sqlite_trigger_names']():
        op.execute(f'DROP TRIGGER {name}')
    namespace['_create_sqlite_triggers']()
    for event in ('INSERT', 'UPDATE'):
        name = 'trg_files_source_reviewer_' + event.lower() + '_0120'
        if expanded:
            op.execute(f"""CREATE TRIGGER {name} BEFORE {event} ON files
                WHEN json_extract(NEW.metadata_jsonb,'$.purpose')='{PURPOSE}' AND NOT ({uploader_sql('sqlite')})
                BEGIN SELECT RAISE(ABORT,'0120 active headquarters source reviewer required'); END""")
        else:
            op.execute(f'DROP TRIGGER {name}')


def _transition(upgrade):
    db = op.get_bind()
    dialect = db.dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0120 supports PostgreSQL and SQLite')
    if dialect == 'sqlite':
        files()['_ensure_sqlite_migration_transaction']()
    else:
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE ' + ','.join('public.'+t for t in ('files', *TABLES)) + ' IN ACCESS EXCLUSIVE MODE')
    if upgrade:
        predicate = ' OR '.join(f'EXISTS (SELECT 1 FROM {t} original WHERE NOT ({binding_sql(dialect,"original")}))' for t in TABLES)
        message = '0120 upgrade blocked: existing source evidence requires reviewed migration'
    else:
        purpose = "metadata_jsonb->>'purpose'" if dialect == 'postgresql' else "json_extract(metadata_jsonb,'$.purpose')"
        predicate = f"EXISTS (SELECT 1 FROM files WHERE {purpose}='{PURPOSE}')"
        predicate += ' OR ' + ' OR '.join(f'EXISTS (SELECT 1 FROM {t})' for t in TABLES)
        message = '0120 downgrade blocked: source evidence or review facts must be retained'
    if dialect == 'postgresql':
        op.execute(f"DO $$ BEGIN IF {predicate} THEN RAISE EXCEPTION '{message}'; END IF; END $$")
        replace = runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        before, after = source_changes()
        replace(signature='public.rsc_guard_formal_file_object_0036()',
                expected_hash=FILE_HASHES[0 if upgrade else 1], replacement_hash=FILE_HASHES[1 if upgrade else 0],
                replacements=((before, after),) if upgrade else ((after, before),), label='source_files_0120')
        if upgrade:
            op.execute(f'CREATE FUNCTION public.{FUNCTION}() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog, public AS $$' + BODY + '$$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{FUNCTION}() FROM PUBLIC, star_oam_api, star_oam_edge, edge_inbox, star_oam_projector, star_oam_backup')
            for name, table in TRIGGERS.items():
                op.execute(f'CREATE TRIGGER {name} BEFORE INSERT ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        else:
            for name, table in TRIGGERS.items():
                op.execute(f'DROP TRIGGER {name} ON public.{table}')
            op.execute(f'DROP FUNCTION public.{FUNCTION}()')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()', expected_hash=OLD_HASH if upgrade else NEW_HASH,
                replacement_hash=NEW_HASH if upgrade else OLD_HASH,
                replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label='source_files_readiness_0120')
    else:
        if db.exec_driver_sql('SELECT ' + predicate).scalar():
            raise RuntimeError(message)
        _sqlite_files(upgrade)
        for name, table in TRIGGERS.items():
            if upgrade:
                op.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN NOT ({binding_sql('sqlite')}) BEGIN SELECT RAISE(ABORT,'0120 completed source configuration evidence required'); END")
            else:
                op.execute(f'DROP TRIGGER {name}')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
