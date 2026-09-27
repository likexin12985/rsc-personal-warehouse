"""Dedicated private loss evidence; no loss posting or permission activation.

PostgreSQL verifies current direct authority at upload/completion. Existing
file purposes retain their exact previous guards. SQLite stays fail closed
for this new purpose; production acceptance requires actual PostgreSQL 16.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op

revision = '20261122_0143'
down_revision = '20261121_0142'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
PURPOSE = 'stock_loss_evidence'
SIGNATURE = 'public.rsc_guard_formal_file_object_0036()'
previous = runpy.run_path(str(FOLDER / '20261120_0141_opening_count_import_jobs.py'))
ready = previous['previous']['ready']
OLD_READY_HASH = '894d246b037847695e5c753735daab9977e59a3a4e79046f891681eb5ce38c8b'
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
OLD_BODY = previous['FILE_NEW_BODY']
OLD_FILE_HASH = previous['FILE_NEW_HASH']

AUTHORITY = """
        PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.uploaded_by]::text[]);
        PERFORM organization.id FROM public.organizations organization
          WHERE organization.id IN (
              SELECT person.organization_id FROM public.people person
              JOIN public.users actor ON actor.person_id=person.id WHERE actor.id=NEW.uploaded_by
          ) OR organization.id::text IN (
              SELECT assignment.scope_id FROM public.role_assignments assignment
              WHERE assignment.user_id=NEW.uploaded_by AND assignment.scope_type='organization'
          ) ORDER BY organization.id FOR SHARE;
        -- The graph lock may wait; evaluate expiry using the database clock
        -- after it is acquired, never the transaction start timestamp.
        IF NOT EXISTS (
            SELECT 1 FROM public.users u JOIN public.people person ON person.id=u.person_id
            JOIN public.organizations actor_org ON actor_org.id=person.organization_id
            JOIN public.role_assignments a ON a.user_id=u.id
            JOIN public.roles r ON r.id=a.role_id
            JOIN public.role_permissions rp ON rp.role_id=r.id
            JOIN public.permissions permission ON permission.id=rp.permission_id
            WHERE u.id=NEW.uploaded_by AND u.is_active AND u.account_status='active'
              AND person.employment_status='active' AND actor_org.status='active'
              AND actor_org.org_type IN ('headquarters','region_company','department')
              AND u.authorization_version=(NEW.metadata_jsonb->>'authorization_version')::bigint
              AND EXISTS(SELECT 1 FROM public.auth_identities identity
                WHERE identity.user_id=u.id AND identity.status='active'
                  AND identity.verified_at IS NOT NULL AND identity.revoked_at IS NULL)
              AND r.status='active' AND NOT r.is_external
              AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())
              AND rp.effect='allow' AND permission.resource='stock_operation' AND permission.field_code=''
              AND (
                  (r.code='technician' AND a.scope_type='person' AND a.scope_id=person.id::text
                    AND permission.action='submit_loss')
                  OR (r.code='provincial_manager' AND a.scope_type='organization'
                    AND permission.action IN ('submit_loss','review_loss_regional')
                    AND EXISTS(SELECT 1 FROM public.organizations scope WHERE scope.id::text=a.scope_id
                      AND scope.status='active' AND scope.org_type='region_company'))
                  OR (r.code='admin' AND a.scope_type='national' AND a.scope_id='*'
                    AND actor_org.org_type='headquarters'
                    AND permission.action IN ('submit_loss','finalize_loss','reverse_loss'))
              )
              AND NOT EXISTS (
                  SELECT 1 FROM public.role_assignments da JOIN public.roles dr ON dr.id=da.role_id
                  JOIN public.role_permissions dp ON dp.role_id=dr.id
                  JOIN public.permissions denied ON denied.id=dp.permission_id
                  WHERE da.user_id=u.id AND dr.status='active'
                    AND da.status IN ('active','scheduled') AND da.revoked_at IS NULL
                    AND da.valid_from<=clock_timestamp() AND (da.valid_to IS NULL OR da.valid_to>clock_timestamp())
                    AND dp.effect='deny' AND denied.resource='stock_operation'
                    AND denied.action=permission.action AND denied.field_code=''
              )
        ) THEN
            RAISE EXCEPTION '0143 current loss evidence authority required' USING ERRCODE='23514';
        END IF;
"""

AUTHORITY_FUNCTION = 'rsc_assert_stock_loss_file_authority_0143'
AUTHORITY_SIGNATURE = 'text, bigint'
AUTHORITY_BODY = '\nBEGIN\n' + AUTHORITY.replace('NEW.uploaded_by', 'actor_id').replace(
    "(NEW.metadata_jsonb->>'authorization_version')::bigint", 'actor_version') + '\nEND;\n'
COMMIT_FUNCTION = 'rsc_guard_stock_loss_file_commit_0143'
COMMIT_BODY = f"""
BEGIN
    IF NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN
        PERFORM public.{AUTHORITY_FUNCTION}(NEW.uploaded_by,
            (NEW.metadata_jsonb->>'authorization_version')::bigint);
    END IF;
    RETURN NULL;
END;
"""
COMMIT_TRIGGER = 'trg_files_loss_authority_commit_0143'


def _body():
    marker = "'daily_reconciliation_evidence', 'inventory_report_export', 'opening_count_import', 'opening_count_import_error')"
    assert OLD_BODY.count(marker) == 3
    expanded = OLD_BODY.replace(marker, marker[:-1] + f", '{PURPOSE}')")
    return expanded.replace('BEGIN\n', 'BEGIN\n' +
        f"    IF TG_OP IN ('INSERT','UPDATE') AND NEW.metadata_jsonb->>'purpose'='{PURPOSE}' THEN\n" +
        f"        PERFORM public.{AUTHORITY_FUNCTION}(NEW.uploaded_by, (NEW.metadata_jsonb->>'authorization_version')::bigint);\n" +
        '    END IF;\n', 1)


NEW_BODY = _body()
NEW_FILE_HASH = hashlib.sha256(NEW_BODY.encode()).hexdigest()


def _transition(up):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0143 PostgreSQL or SQLite required')
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0143 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.files IN ACCESS EXCLUSIVE MODE')
    purpose = "metadata_jsonb->>'purpose'" if dialect == 'postgresql' else "json_extract(metadata_jsonb,'$.purpose')"
    # Old schemas cannot have authentic new-purpose files. Reject corruption
    # on upgrade and preserve all pending/completed evidence on downgrade.
    helper['_preflight'](f"EXISTS(SELECT 1 FROM files WHERE {purpose}='{PURPOSE}')",
                         '0143 loss evidence requires retention and explicit migration')
    if dialect == 'postgresql':
        if up:
            op.execute(f'CREATE FUNCTION public.{AUTHORITY_FUNCTION}(actor_id text, actor_version bigint) RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${AUTHORITY_BODY}$body$')
            op.execute(f'CREATE FUNCTION public.{COMMIT_FUNCTION}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${COMMIT_BODY}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{AUTHORITY_FUNCTION}({AUTHORITY_SIGNATURE}), public.{COMMIT_FUNCTION}() FROM PUBLIC,star_oam_api')
            op.execute(f'CREATE CONSTRAINT TRIGGER {COMMIT_TRIGGER} AFTER INSERT OR UPDATE ON public.files DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{COMMIT_FUNCTION}()')
            op.execute(f'ALTER TABLE public.files ENABLE ALWAYS TRIGGER {COMMIT_TRIGGER}')
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature=SIGNATURE, expected_hash=OLD_FILE_HASH if up else NEW_FILE_HASH,
                replacement_hash=NEW_FILE_HASH if up else OLD_FILE_HASH,
                replacements=((OLD_BODY, NEW_BODY),) if up else ((NEW_BODY, OLD_BODY),),
                label='stock_loss_evidence_purpose_0143')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
                expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
                replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
                replacements=((down_revision, revision),) if up else ((revision, down_revision),),
                label='stock_loss_evidence_readiness_0143')
        if not up:
            op.execute(f'DROP TRIGGER {COMMIT_TRIGGER} ON public.files')
            op.execute(f'DROP FUNCTION public.{COMMIT_FUNCTION}(), public.{AUTHORITY_FUNCTION}({AUTHORITY_SIGNATURE})')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
