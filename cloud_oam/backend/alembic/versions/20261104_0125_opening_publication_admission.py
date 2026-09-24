"""Bind every new PostgreSQL opening to a currently admissible publication.

Historical tasks are unchanged. The read capability resolves immutable exact
publication rows; only new creation acquires current-evidence locks. Neither
capability grants runtime SELECT on source-authority or publication tables.
"""
import hashlib
from pathlib import Path
import runpy
from alembic import op

revision = '20261104_0125'
down_revision = '20261103_0124'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261103_0124_opening_control_directory.py'))
_material = runpy.run_path(str(_folder/'20261029_0119_material_publications.py'))
_files = runpy.run_path(str(_folder/'20261030_0120_source_configuration_files.py'))
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_material['_ready'].replace(_material['_older']['revision'],revision).encode()).hexdigest()
_file_binding = _files['files']()['_postgresql_binding_file_sql'](
    file_expression='e.file_id', purpose='source_configuration_evidence',
    user_expression='file_row.uploaded_by', person_expression=None,
    bound_at_expression='clock_timestamp()', require_current_identity=False)

ASSERT_BODY = """
DECLARE
    pub public.control_projection_publications%ROWTYPE;
    prepared public.inventory_control_preparations%ROWTYPE;
    binding public.inventory_control_source_bindings%ROWTYPE;
    mapping public.inventory_control_mapping_decisions%ROWTYPE;
    authority jsonb;
    item record;
    e record;
    proof jsonb;
    capture jsonb;
    starts timestamptz;
    ends timestamptz;
    coverage tstzmultirange;
    observed timestamptz;
    expires_at timestamptz;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0125 READ COMMITTED required' USING ERRCODE='25001';
    END IF;
    SELECT * INTO pub FROM public.control_projection_publications
        WHERE sync_run_id=p_run AND source_system_id=p_source AND region_org_id=p_region;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0125 reviewed control publication required' USING ERRCODE='23514';
    END IF;
    SELECT * INTO STRICT prepared FROM public.inventory_control_preparations WHERE id=pub.preparation_id;
    -- Publisher takes the scope key before the source binding. Match that
    -- order; authority/mapping writers take binding but never this scope key.
    PERFORM pg_advisory_xact_lock(hashtextextended('rsc.control-publication.scope:'||p_source::text||':'||p_region::text,0));
    SELECT * INTO STRICT binding FROM public.inventory_control_source_bindings WHERE id=prepared.binding_id FOR UPDATE;
    PERFORM id FROM public.source_systems WHERE id=p_source FOR SHARE;
    PERFORM id FROM public.organizations WHERE id=p_region FOR SHARE;
    SELECT * INTO STRICT mapping FROM public.inventory_control_mapping_decisions WHERE id=pub.mapping_decision_id;
    authority:=pub.payload_jsonb->'review'->'plan'->'basis'->'authorization_evidence';
    -- Exact source/catalogue grants used in all capture intervals and at
    -- publication remain evidence; a later replacement cannot bless a revoked
    -- historical grant. Inspect only the original set, not unrelated files.
    FOR e IN
        SELECT pub.evidence_file_id file_id,pub.evidence_sha256 sha,pub.payload_jsonb->'review'->'evidence_file' expected
        UNION SELECT mapping.evidence_file_id,mapping.evidence_sha256,mapping.payload_jsonb->'evidence_file'
        UNION SELECT d.evidence_file_id,d.evidence_sha256,d.payload_jsonb->'evidence_file'
          FROM public.inventory_control_authority_decisions d WHERE d.id IN (
            SELECT (x->>'source_grant_id')::uuid FROM (
              SELECT authority->'current_authority' x UNION ALL
              SELECT span FROM jsonb_array_elements(authority->'captures') c,
                   LATERAL jsonb_array_elements(c->'authorization_spans') span) pairs
            UNION SELECT (x->>'catalog_grant_id')::uuid FROM (
              SELECT authority->'current_authority' x UNION ALL
              SELECT span FROM jsonb_array_elements(authority->'captures') c,
                   LATERAL jsonb_array_elements(c->'authorization_spans') span) pairs)
        ORDER BY file_id
    LOOP
        PERFORM id FROM public.files WHERE id=e.file_id FOR SHARE;
        SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,'mime_type',f.mime_type)
          INTO proof FROM public.files f WHERE f.id=e.file_id AND f.sha256=e.sha AND f.status='available';
        IF proof IS NULL OR proof IS DISTINCT FROM (e.expected-'storage_key') OR NOT (__FILE_BINDING__) THEN
            RAISE EXCEPTION '0125 current source evidence file required' USING ERRCODE='23514';
        END IF;
    END LOOP;
    observed:=clock_timestamp();
    IF pub.valid_until<=observed OR pub.captured_at>observed OR NOT isfinite(pub.valid_until)
       OR EXISTS (SELECT 1 FROM public.control_projection_publications WHERE previous_publication_id=pub.id)
       OR NOT EXISTS (SELECT 1 FROM public.source_systems WHERE id=p_source AND code='oam' AND mode='read_only' AND enabled)
       OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id=p_region AND org_type='region_company' AND status='active')
       OR mapping.action<>'grant' OR mapping.valid_from>observed OR mapping.valid_to<=observed
       OR EXISTS (SELECT 1 FROM public.inventory_control_mapping_decisions WHERE revoked_grant_id=mapping.id)
    THEN RAISE EXCEPTION '0125 current control publication required' USING ERRCODE='23514'; END IF;
    FOR proof IN SELECT authority->'current_authority' UNION ALL
        SELECT span FROM jsonb_array_elements(authority->'captures') c,
             LATERAL jsonb_array_elements(c->'authorization_spans') span
    LOOP
        IF NOT EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions s
            JOIN public.inventory_control_authority_decisions c ON c.source_grant_id=s.id
            WHERE s.id=(proof->>'source_grant_id')::uuid AND c.id=(proof->>'catalog_grant_id')::uuid
              AND s.payload_sha256=proof->>'source_grant_sha256' AND c.payload_sha256=proof->>'catalog_grant_sha256'
              AND s.binding_id=binding.id AND c.binding_id=binding.id AND c.catalog_id=prepared.catalog_id
              AND s.action='source_grant' AND c.action='catalog_grant'
              AND NOT EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id IN(s.id,c.id))) THEN
            RAISE EXCEPTION '0125 original source grant revoked' USING ERRCODE='23514';
        END IF;
    END LOOP;
    SELECT range_agg(tstzrange(GREATEST(s.valid_from,c.valid_from),LEAST(s.valid_to,c.valid_to),'[)')) INTO coverage
        FROM public.inventory_control_authority_decisions s JOIN public.inventory_control_authority_decisions c ON c.source_grant_id=s.id
        WHERE s.id=(authority->'current_authority'->>'source_grant_id')::uuid
          AND c.id=(authority->'current_authority'->>'catalog_grant_id')::uuid;
    IF coverage IS NULL OR NOT coverage @> observed THEN
        RAISE EXCEPTION '0125 current original source authority required' USING ERRCODE='23514'; END IF;
    SELECT evidence_jsonb->'snapshots'->-1 INTO capture FROM public.inventory_control_capture_chains WHERE id=prepared.capture_chain_id;
    SELECT min((value->>'started_at')::timestamptz),max((value->>'completed_at')::timestamptz) INTO starts,ends
        FROM jsonb_array_elements(capture->'warehouses');
    IF starts IS NULL OR ends IS NULL OR observed>=starts+interval '45 minutes' OR ends>observed THEN
        RAISE EXCEPTION '0125 capture expired' USING ERRCODE='23514'; END IF;
    -- A zero publication has no material origin set; its complete catalogue,
    -- signed capture and source proof above still apply.
    FOR item IN SELECT DISTINCT m.sku_code FROM public.control_projection_lines l
        JOIN public.materials m ON m.id=l.material_id WHERE l.publication_id=pub.id ORDER BY m.sku_code LOOP
        PERFORM pg_advisory_xact_lock_shared(hashtextextended('rsc.material-publication.sku:'||item.sku_code,0));
    END LOOP;
    FOR item IN SELECT DISTINCT mp.binding_id FROM public.control_projection_origins o
        JOIN public.material_projection_lines ml ON ml.id=o.material_line_id
        JOIN public.material_projection_publications mp ON mp.id=ml.publication_id
        WHERE o.publication_id=pub.id ORDER BY mp.binding_id LOOP
        PERFORM id FROM public.oam_material_capture_bindings WHERE id=item.binding_id FOR UPDATE;
    END LOOP;
    FOR e IN
        SELECT mp.evidence_file_id file_id,mp.evidence_sha256 sha,mp.payload_jsonb->'evidence_file' expected
          FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id
          JOIN public.material_projection_publications mp ON mp.id=ml.publication_id WHERE o.publication_id=pub.id
        UNION SELECT g.evidence_file_id,g.evidence_sha256,g.payload_jsonb->'evidence_file'
          FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id
          JOIN public.material_projection_publications mp ON mp.id=ml.publication_id
          JOIN public.material_source_authority_decisions g ON g.id IN (
              SELECT (mp.payload_jsonb->'source'->>'current_decision_id')::uuid
              UNION SELECT (value->>'decision_id')::uuid FROM jsonb_array_elements(mp.payload_jsonb->'source'->'authority_spans'))
          WHERE o.publication_id=pub.id
        ORDER BY file_id
    LOOP
        PERFORM id FROM public.files WHERE id=e.file_id FOR SHARE;
        SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,'mime_type',f.mime_type)
          INTO proof FROM public.files f WHERE f.id=e.file_id AND f.sha256=e.sha AND f.status='available';
        IF proof IS NULL OR proof IS DISTINCT FROM (e.expected-'storage_key') OR NOT (__FILE_BINDING__) THEN
            RAISE EXCEPTION '0125 current material evidence file required' USING ERRCODE='23514';
        END IF;
    END LOOP;
    -- Lock every mutable master in publisher order before a fresh time sample.
    PERFORM m.id FROM public.materials m WHERE m.id IN (SELECT material_id FROM public.control_projection_lines WHERE publication_id=pub.id) ORDER BY m.id FOR SHARE;
    PERFORM x.id FROM public.external_objects x WHERE x.id IN (SELECT ml.external_object_id FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id WHERE o.publication_id=pub.id) ORDER BY x.id FOR SHARE;
    PERFORM v.id FROM public.external_object_versions v WHERE v.id IN (SELECT ml.version_id FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id WHERE o.publication_id=pub.id) ORDER BY v.id FOR SHARE;
    PERFORM p.id FROM public.material_inventory_policies p WHERE p.id IN (SELECT ml.policy_id FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id WHERE o.publication_id=pub.id) ORDER BY p.id FOR SHARE;
    observed:=clock_timestamp();
    IF observed>=pub.valid_until OR NOT coverage @> observed OR mapping.valid_to<=observed THEN
        RAISE EXCEPTION '0125 evidence expired while waiting' USING ERRCODE='23514'; END IF;
    expires_at:=pub.valid_until;
    FOR item IN SELECT ml.*,mp.binding_id,mp.receipt_id,mp.payload_jsonb AS publication_payload
        FROM public.control_projection_origins o JOIN public.material_projection_lines ml ON ml.id=o.material_line_id
        JOIN public.material_projection_publications mp ON mp.id=ml.publication_id WHERE o.publication_id=pub.id LOOP
        FOR proof IN SELECT value FROM jsonb_array_elements(item.publication_payload->'source'->'authority_spans') LOOP
            IF NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions g
                WHERE g.id=(proof->>'decision_id')::uuid AND g.binding_id=item.binding_id AND g.action='grant'
                  AND g.payload_sha256=proof->>'decision_sha256'
                  AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions WHERE revoked_grant_id=g.id)) THEN
                RAISE EXCEPTION '0125 original material capture authority revoked' USING ERRCODE='23514'; END IF;
        END LOOP;
        SELECT LEAST(mb.valid_to,r.capture_started_at+interval '45 minutes',p.effective_to,g.valid_to) INTO ends FROM public.oam_material_capture_bindings mb
            JOIN public.oam_material_capture_receipts r ON r.id=item.receipt_id AND r.binding_id=mb.id
            JOIN public.external_objects x ON x.id=item.external_object_id
            JOIN public.external_object_versions v ON v.id=item.version_id
            JOIN public.materials m ON m.id=item.material_id
            JOIN public.material_inventory_policies p ON p.id=item.policy_id
            JOIN public.material_source_authority_decisions g ON g.id=(item.publication_payload->'source'->>'current_decision_id')::uuid
            WHERE mb.id=item.binding_id AND mb.source_system_id=p_source
              AND mb.source_instance=binding.binding_jsonb->>'source_instance'
              AND mb.revoked_at IS NULL AND mb.valid_from<=observed AND observed<mb.valid_to
              AND r.capture_started_at+interval '45 minutes'>observed AND r.capture_completed_at<=observed
              AND x.current_version_id=v.id AND x.deleted_at IS NULL AND v.is_current AND v.valid_to IS NULL
              AND m.external_object_id=x.id AND m.status='active' AND p.material_id=m.id
              AND to_jsonb(m) @> (item.payload_jsonb->'normalized') AND to_jsonb(p) @> (item.payload_jsonb->'policy')
              AND p.effective_from<=observed AND (p.effective_to IS NULL OR observed<p.effective_to)
              AND g.binding_id=mb.id AND g.action='grant' AND g.valid_from<=observed AND observed<g.valid_to
              AND g.payload_sha256=item.publication_payload->'source'->>'current_decision_sha256'
              AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions WHERE revoked_grant_id=g.id);
        IF ends IS NULL THEN
            RAISE EXCEPTION '0125 current material source required' USING ERRCODE='23514'; END IF;
        expires_at:=LEAST(expires_at,ends);
    END LOOP;
    IF clock_timestamp()>=expires_at THEN
        RAISE EXCEPTION '0125 evidence expired during proof' USING ERRCODE='23514'; END IF;
    RETURN pub.id;
END
""".replace('__FILE_BINDING__',_file_binding)

SELECT_BODY = """
DECLARE
    pub public.control_projection_publications%ROWTYPE;
    principal jsonb;
    rows jsonb;
BEGIN
    IF p_current IS NULL OR (p_publication IS NULL AND (p_source IS NULL OR p_run IS NULL)) THEN
        RAISE EXCEPTION '0125 exact selection required' USING ERRCODE='22023'; END IF;
    principal:=public.rsc_opening_control_directory_0124(p_user,p_version,p_region,1,NULL);
    SELECT * INTO pub FROM public.control_projection_publications
      WHERE region_org_id=p_region AND (p_publication IS NULL OR id=p_publication)
        AND (p_source IS NULL OR source_system_id=p_source) AND (p_run IS NULL OR sync_run_id=p_run);
    IF NOT FOUND THEN RAISE EXCEPTION '0125 selection unavailable' USING ERRCODE='23514'; END IF;
    IF p_current THEN PERFORM public.rsc_assert_opening_publication_0125(pub.source_system_id,pub.sync_run_id,p_region); END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('sync_inbox_event_id',l.sync_inbox_event_id,
        'external_object_version_id',l.version_id,'external_business_key',l.external_business_key,
        'material_id',l.material_id,'condition_code',l.condition_code,'control_qty',l.control_qty::text,
        'mapping_status','resolved','mapping_note','','source_updated_at',l.source_updated_at,
        'payload_sha256',l.payload_jsonb->>'control_payload_sha256') ORDER BY l.sequence),'[]'::jsonb)
        INTO rows FROM public.control_projection_lines l WHERE l.publication_id=pub.id;
    IF jsonb_array_length(rows)<>pub.record_count THEN
        RAISE EXCEPTION '0125 incomplete control selection' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('publication_id',pub.id,'publication_sha256',pub.payload_sha256,
        'actor_person_id',principal->>'actor_person_id','authorization_version',p_version,'region_org_id',p_region,
        'source_system_id',pub.source_system_id,'sync_run_id',pub.sync_run_id,
        'sync_scope_key',(SELECT scope_key FROM public.sync_runs WHERE id=pub.sync_run_id),
        'captured_at',pub.captured_at,'valid_until',pub.valid_until,'control_lines',rows);
END
"""
GUARD_BODY = """
BEGIN
    IF NEW.task_type='opening' THEN
        PERFORM public.rsc_assert_opening_publication_0125(NEW.control_source_system_id,NEW.control_sync_run_id,NEW.region_org_id);
    END IF;
    RETURN NEW;
END
"""
FUNCTIONS = {
    'rsc_assert_opening_publication_0125': ('uuid, uuid, uuid','p_source uuid,p_run uuid,p_region uuid','uuid',ASSERT_BODY,False),
    'rsc_opening_control_selection_0125': ('text, bigint, uuid, uuid, uuid, uuid, boolean','p_user text,p_version bigint,p_region uuid,p_publication uuid,p_source uuid,p_run uuid,p_current boolean','jsonb',SELECT_BODY,True),
    'rsc_guard_opening_publication_0125': ('','','trigger',GUARD_BODY,False),
}
BODY_HASHES = {name:hashlib.sha256(row[3].encode()).hexdigest() for name,row in FUNCTIONS.items()}
TRIGGERS = {'trg_opening_publication_insert_0125':(7,False), 'trg_opening_publication_commit_0125':(5,True)}


def _verify():
    for name,(args,_,returns,body,api) in FUNCTIONS.items():
        allowed="p.proowner,(SELECT oid FROM pg_roles WHERE rolname='star_oam_api')" if api else 'p.proowner'
        op.execute(f"""DO $verify_0125$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({args})'::regprocedure
            AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
            AND p.prokind='f' AND p.prorettype='{returns}'::regtype AND NOT p.proretset
            AND p.prosecdef AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u'
            AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            AND p.proconfig=ARRAY['search_path=pg_catalog, public']
            AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{BODY_HASHES[name]}'
            AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee NOT IN ({allowed}) OR (a.grantee<>p.proowner AND a.is_grantable))
            {"AND has_function_privilege('star_oam_api',p.oid,'EXECUTE')" if api else ''}) THEN
            RAISE EXCEPTION '0125 function source or ACL drift'; END IF;
          END $verify_0125$""")
    for name,(kind,deferred) in TRIGGERS.items():
        flag=str(deferred).lower()
        op.execute(f"""DO $trigger_0125$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.stocktake_tasks'::regclass
            AND t.tgname='{name}' AND t.tgfoid='public.rsc_guard_opening_publication_0125()'::regprocedure
            AND t.tgtype={kind} AND t.tgenabled='A' AND NOT t.tgisinternal
            AND t.tgdeferrable={flag} AND t.tginitdeferred={flag} AND t.tgqual IS NULL AND t.tgnargs=0) THEN
            RAISE EXCEPTION '0125 admission trigger drift'; END IF;
          END $trigger_0125$""")


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}:raise RuntimeError('0125 unsupported database')
    # SQLite remains a nonproduction domain-test reference, never a source of
    # PostgreSQL admission evidence. Public selected-start requires PostgreSQL.
    if dialect=='sqlite':return
    op.execute("""DO $owner_0125$ BEGIN
      IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0125 direct schema owner required'; END IF; END $owner_0125$""")
    op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if up:
        for name,(args,params,returns,body,api) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({params}) RETURNS {returns} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC')
            if api:op.execute(f'GRANT EXECUTE ON FUNCTION public.{name}({args}) TO star_oam_api')
        op.execute('CREATE TRIGGER trg_opening_publication_insert_0125 BEFORE INSERT ON public.stocktake_tasks FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_opening_publication_0125()')
        op.execute('CREATE CONSTRAINT TRIGGER trg_opening_publication_commit_0125 AFTER INSERT ON public.stocktake_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_opening_publication_0125()')
        for name in TRIGGERS:op.execute('ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER '+name)
        _verify()
    else:
        _verify()
        for name in TRIGGERS:op.execute('DROP TRIGGER '+name+' ON public.stocktake_tasks')
        for name,(args,*_) in reversed(tuple(FUNCTIONS.items())):op.execute(f'DROP FUNCTION public.{name}({args})')
    replace=runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='opening_admission_readiness_0125')


def upgrade():_transition(True)
def downgrade():_transition(False)
