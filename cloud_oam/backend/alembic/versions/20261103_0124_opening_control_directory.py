"""Read only, region-authorized publication summaries for opening preparation.

No private-table SELECT, quantities, origins, session or file coordinates are
exposed. This directory is never a current-admission or start capability.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = '20261103_0124'
down_revision = '20261102_0123'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261102_0123_seal_audit_lock_scope.py'))
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_previous['_material']['_ready'].replace(
    _previous['_material']['_older']['revision'], revision).encode()).hexdigest()
FUNCTION = 'rsc_opening_control_directory_0124'
ARGUMENTS = 'text, bigint, uuid, integer, uuid'
SIGNATURE = 'public.'+FUNCTION+'('+ARGUMENTS+')'
INDEX = 'ix_control_publications_region_directory_0124'
BODY = """
DECLARE
    at_time timestamptz := statement_timestamp();
    person_id uuid;
    person_org_type text;
    ancestor uuid := p_region;
    path uuid[] := ARRAY[]::uuid[];
    parent uuid;
    assignment record;
    permitted boolean := false;
    rejected boolean := false;
    rows jsonb;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0124 directory requires READ COMMITTED' USING ERRCODE='25001';
    END IF;
    IF p_user IS NULL OR p_version IS NULL OR p_version<1 OR p_region IS NULL
       OR p_region='00000000-0000-0000-0000-000000000000'::uuid
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR p_after='00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION '0124 invalid directory coordinates' USING ERRCODE='22023';
    END IF;
    SELECT p.id,o.org_type INTO person_id,person_org_type
      FROM public.users u JOIN public.people p ON p.id=u.person_id
      JOIN public.organizations o ON o.id=p.organization_id
      WHERE u.id=p_user AND u.is_active AND u.account_status='active'
        AND u.authorization_version=p_version AND p.employment_status='active' AND o.status='active'
        AND EXISTS (SELECT 1 FROM public.auth_identities i WHERE i.user_id=u.id
            AND i.status='active' AND i.revoked_at IS NULL AND i.verified_at<=at_time);
    IF person_id IS NULL OR NOT EXISTS (SELECT 1 FROM public.organizations
          WHERE id=p_region AND status='active' AND org_type='region_company') THEN
        RAISE EXCEPTION '0124 directory scope forbidden' USING ERRCODE='42501';
    END IF;
    -- Bound and validate the entire ancestry, including global deny scopes.
    WHILE ancestor IS NOT NULL LOOP
        IF ancestor=ANY(path) OR cardinality(path)>=1000 THEN
            RAISE EXCEPTION '0124 directory scope forbidden' USING ERRCODE='42501';
        END IF;
        SELECT parent_id INTO parent FROM public.organizations WHERE id=ancestor AND status='active';
        IF NOT FOUND THEN RAISE EXCEPTION '0124 directory scope forbidden' USING ERRCODE='42501'; END IF;
        path:=array_append(path,ancestor); ancestor:=parent;
    END LOOP;
    FOR assignment IN SELECT a.*,r.code AS role_code FROM public.role_assignments a
        JOIN public.roles r ON r.id=a.role_id
        WHERE a.user_id=p_user AND r.status='active' AND a.status IN ('active','scheduled')
          AND a.revoked_at IS NULL AND a.valid_from<=at_time AND (a.valid_to IS NULL OR at_time<a.valid_to)
        ORDER BY a.id LIMIT 1001 LOOP
        -- Match formal principal validation: an invalid active assignment
        -- invalidates the principal; do not quietly ignore it.
        IF NOT COALESCE(CASE assignment.role_code
            WHEN 'admin' THEN assignment.scope_type='national' AND assignment.scope_id='*' AND person_org_type='headquarters'
            WHEN 'provincial_manager' THEN assignment.scope_type='organization'
                AND person_org_type IN ('headquarters','region_company','department')
                AND EXISTS (SELECT 1 FROM public.organizations WHERE id::text=lower(assignment.scope_id)
                    AND org_type='region_company' AND status='active')
            WHEN 'technician' THEN assignment.scope_type='person' AND lower(assignment.scope_id)=person_id::text
                AND person_org_type IN ('headquarters','region_company','department')
            WHEN 'star_headquarters_approver' THEN assignment.scope_type='document'
                AND length(btrim(assignment.scope_id))>0 AND person_org_type='external_approval_org'
            ELSE false END,false) THEN
            RAISE EXCEPTION '0124 directory scope forbidden' USING ERRCODE='42501';
        END IF;
        IF (assignment.scope_type='national' AND assignment.scope_id='*') OR
           (assignment.scope_type='organization' AND lower(assignment.scope_id)=ANY(path::text[])) THEN
            rejected:=rejected OR EXISTS (SELECT 1 FROM public.role_permissions rp
                JOIN public.permissions p ON p.id=rp.permission_id WHERE rp.role_id=assignment.role_id
                AND rp.effect='deny' AND p.resource='stocktake' AND p.action='manage' AND p.field_code='');
        END IF;
        IF (assignment.role_code='admin' AND assignment.scope_type='national' AND assignment.scope_id='*') OR
           (assignment.role_code='provincial_manager' AND assignment.scope_type='organization' AND lower(assignment.scope_id)=p_region::text) THEN
            permitted:=permitted OR EXISTS (SELECT 1 FROM public.role_permissions rp
                JOIN public.permissions p ON p.id=rp.permission_id WHERE rp.role_id=assignment.role_id
                AND rp.effect='allow' AND p.resource='stocktake' AND p.action='manage' AND p.field_code='');
        END IF;
    END LOOP;
    IF NOT permitted OR rejected OR (SELECT count(*) FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        WHERE a.user_id=p_user AND r.status='active' AND a.status IN ('active','scheduled')
          AND a.revoked_at IS NULL AND a.valid_from<=at_time AND (a.valid_to IS NULL OR at_time<a.valid_to))>1000 THEN
        RAISE EXCEPTION '0124 directory scope forbidden' USING ERRCODE='42501';
    END IF;
    SELECT COALESCE(jsonb_agg(item ORDER BY id),'[]'::jsonb) INTO rows FROM (
        SELECT cp.id,jsonb_build_object('publication_id',cp.id::text,
            'source_system_id',cp.source_system_id::text,'source_name',s.name,
            'captured_at',cp.captured_at,'published_at',cp.created_at,'valid_until',cp.valid_until,
            'record_count',cp.record_count,'is_latest',NOT EXISTS (SELECT 1 FROM public.control_projection_publications n
                WHERE n.previous_publication_id=cp.id)) AS item
        FROM public.control_projection_publications cp JOIN public.source_systems s ON s.id=cp.source_system_id
        WHERE cp.region_org_id=p_region AND (p_after IS NULL OR cp.id>p_after)
        ORDER BY cp.id LIMIT p_limit+1
    ) bounded;
    RETURN jsonb_build_object('actor_person_id',person_id::text,'authorization_version',p_version,
        'region_org_id',p_region::text,'items',rows);
END
"""
BODY_HASH = hashlib.sha256(BODY.encode()).hexdigest()


def _verify():
    op.execute(f"""DO $verify_0124$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='{SIGNATURE}'::regprocedure
            AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
            AND p.prokind='f' AND p.prorettype='jsonb'::regtype AND NOT p.proretset
            AND p.prosecdef AND p.provolatile='s' AND NOT p.proisstrict AND p.proparallel='u'
            AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            AND p.proconfig=ARRAY['search_path=pg_catalog, public']
            AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{BODY_HASH}'
            AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee NOT IN (p.proowner,(SELECT oid FROM pg_roles WHERE rolname='star_oam_api'))
                   OR (a.grantee<>p.proowner AND a.is_grantable))
            AND has_function_privilege('star_oam_api',p.oid,'EXECUTE')) THEN
            RAISE EXCEPTION '0124 directory function source or ACL drift';
        END IF;
    END $verify_0124$""")
    op.execute(f"""DO $index_0124$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            WHERE c.oid='public.{INDEX}'::regclass AND i.indrelid='public.control_projection_publications'::regclass
              AND i.indisvalid AND i.indisready AND NOT i.indisunique AND i.indpred IS NULL
              AND pg_get_indexdef(c.oid)='CREATE INDEX {INDEX} ON public.control_projection_publications USING btree (region_org_id, id)') THEN
            RAISE EXCEPTION '0124 directory index drift';
        END IF;
    END $index_0124$""")


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}: raise RuntimeError('0124 unsupported database')
    if dialect=='sqlite': return
    op.execute("""DO $owner_0124$ BEGIN
        IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
            RAISE EXCEPTION '0124 direct schema owner required';
        END IF;
    END $owner_0124$""")
    op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if up:
        # Bound regional keyset reads without scanning unrelated regions. This
        # PostgreSQL-only nonunique index adds no fact or data constraint.
        op.execute(f'CREATE INDEX {INDEX} ON public.control_projection_publications (region_org_id,id)')
        # CREATE (not REPLACE) refuses an unexpected pre-existing capability.
        op.execute(f'CREATE FUNCTION public.{FUNCTION}(p_user text,p_version bigint,p_region uuid,p_limit integer,p_after uuid) '
            'RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+BODY+'$body$')
        op.execute(f'REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION {SIGNATURE} TO star_oam_api')
        _verify()
    else:
        _verify()
        op.execute(f'DROP FUNCTION {SIGNATURE}')
        op.execute(f'DROP INDEX public.{INDEX}')
    replace=runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
        expected_hash=OLD_HASH if up else NEW_HASH,replacement_hash=NEW_HASH if up else OLD_HASH,
        replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='opening_directory_readiness_0124')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
