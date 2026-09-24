"""Independent reviewed control publications and immutable origin graph."""
import hashlib
from pathlib import Path
import runpy
from alembic import op

revision = '20261031_0121'
down_revision = '20261030_0120'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261030_0120_source_configuration_files.py'))
_material = runpy.run_path(str(_folder/'20261029_0119_material_publications.py'))
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_material['_ready'].replace(_material['_older']['revision'], revision).encode()).hexdigest()
TABLES = ('control_projection_publications','control_projection_lines','control_projection_origins','control_projection_closures')
CORE = ('sync_runs','sync_batches','sync_inbox_events','external_objects','external_object_versions')
FACT_FUNCTION = 'rsc_guard_control_publication_0121'
GRAPH_FUNCTION = 'rsc_guard_control_projection_graph_0121'
# Frozen DDL; no runtime ORM imports when applying or reverting this migration.
DDL = {
'postgresql': (
    'CREATE TABLE control_projection_publications (\n\tid UUID NOT NULL, \n\tpreparation_id UUID NOT NULL, \n\tsource_system_id UUID NOT NULL, \n\tregion_org_id UUID NOT NULL, \n\tmapping_decision_id UUID NOT NULL, \n\tprevious_publication_id UUID, \n\tsync_run_id UUID NOT NULL, \n\tsync_batch_id UUID NOT NULL, \n\tactor_user_id VARCHAR(36) NOT NULL, \n\tactor_person_id UUID NOT NULL, \n\tactor_authorization_version BIGINT NOT NULL, \n\tauth_session_id VARCHAR(36) NOT NULL, \n\tevidence_file_id UUID NOT NULL, \n\tevidence_sha256 VARCHAR(64) NOT NULL, \n\tcaptured_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tvalid_until TIMESTAMP WITH TIME ZONE NOT NULL, \n\trecord_count INTEGER NOT NULL, \n\torigin_count INTEGER NOT NULL, \n\tidempotency_key VARCHAR(128) NOT NULL, \n\trequest_id VARCHAR(160) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\treview_sha256 VARCHAR(64) NOT NULL, \n\tpayload_jsonb JSONB NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\taudit_event_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_control_publication_key UNIQUE (actor_user_id, idempotency_key), \n\tCONSTRAINT uq_control_publication_request UNIQUE (actor_user_id, request_id), \n\tCONSTRAINT uq_control_publication_capture UNIQUE (source_system_id, region_org_id, captured_at), \n\tCONSTRAINT ck_control_publication_count CHECK (record_count>=0 AND origin_count>=record_count AND actor_authorization_version>0), \n\tCONSTRAINT ck_control_publication_time CHECK (captured_at<=created_at AND created_at<valid_until), \n\tCONSTRAINT ck_control_publication_hashes CHECK (length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64), \n\tUNIQUE (preparation_id), \n\tFOREIGN KEY(preparation_id) REFERENCES inventory_control_preparations (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(source_system_id) REFERENCES source_systems (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(region_org_id) REFERENCES organizations (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(mapping_decision_id) REFERENCES inventory_control_mapping_decisions (id) ON DELETE RESTRICT, \n\tUNIQUE (previous_publication_id), \n\tFOREIGN KEY(previous_publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_run_id), \n\tFOREIGN KEY(sync_run_id) REFERENCES sync_runs (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_batch_id), \n\tFOREIGN KEY(sync_batch_id) REFERENCES sync_batches (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(evidence_file_id) REFERENCES files (id) ON DELETE RESTRICT, \n\tUNIQUE (audit_event_id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT\n)',
    'CREATE UNIQUE INDEX uq_control_publication_first ON control_projection_publications (source_system_id, region_org_id) WHERE previous_publication_id IS NULL',
    "CREATE TABLE control_projection_lines (\n\tid UUID NOT NULL, \n\tpublication_id UUID NOT NULL, \n\tsequence INTEGER NOT NULL, \n\texternal_business_key VARCHAR(250) NOT NULL, \n\texternal_object_id UUID NOT NULL, \n\tversion_id UUID NOT NULL, \n\tsync_inbox_event_id UUID NOT NULL, \n\tprevious_line_id UUID, \n\tmaterial_id UUID NOT NULL, \n\tcondition_code VARCHAR(24) NOT NULL, \n\tcontrol_qty NUMERIC(18, 3) NOT NULL, \n\tsource_updated_at TIMESTAMP WITH TIME ZONE, \n\tpayload_jsonb JSONB NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_control_line_sequence UNIQUE (publication_id, sequence), \n\tCONSTRAINT uq_control_line_business_key UNIQUE (publication_id, external_business_key), \n\tCONSTRAINT uq_control_line_publication UNIQUE (id, publication_id), \n\tCONSTRAINT ck_control_line_value CHECK (sequence>0 AND control_qty>=0 AND length(payload_sha256)=64), \n\tCONSTRAINT ck_control_line_condition CHECK (condition_code IN ('new','used','damaged','scrapped')), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(external_object_id) REFERENCES external_objects (id) ON DELETE RESTRICT, \n\tUNIQUE (version_id), \n\tFOREIGN KEY(version_id) REFERENCES external_object_versions (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_inbox_event_id), \n\tFOREIGN KEY(sync_inbox_event_id) REFERENCES sync_inbox_events (id) ON DELETE RESTRICT, \n\tUNIQUE (previous_line_id), \n\tFOREIGN KEY(previous_line_id) REFERENCES control_projection_lines (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(material_id) REFERENCES materials (id) ON DELETE RESTRICT\n)",
    'CREATE INDEX ix_control_projection_lines_external_object_id ON control_projection_lines (external_object_id)',
    'CREATE TABLE control_projection_origins (\n\tid UUID NOT NULL, \n\tpublication_id UUID NOT NULL, \n\tline_id UUID NOT NULL, \n\tsequence INTEGER NOT NULL, \n\texternal_business_key VARCHAR(200) NOT NULL, \n\tcapture_snapshot_id UUID NOT NULL, \n\tstaging_record_id VARCHAR(36) NOT NULL, \n\tmaterial_line_id UUID NOT NULL, \n\tpayload_jsonb JSONB NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(line_id, publication_id) REFERENCES control_projection_lines (id, publication_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_control_origin_key UNIQUE (publication_id, external_business_key), \n\tCONSTRAINT uq_control_origin_sequence UNIQUE (line_id, sequence), \n\tCONSTRAINT ck_control_origin_value CHECK (sequence>0 AND length(payload_sha256)=64), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(capture_snapshot_id) REFERENCES inventory_control_capture_snapshots (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(staging_record_id) REFERENCES external_sync_snapshot_records (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(material_line_id) REFERENCES material_projection_lines (id) ON DELETE RESTRICT\n)',
    "CREATE TABLE control_projection_closures (\n\tid UUID NOT NULL, \n\tpublication_id UUID NOT NULL, \n\tclosed_line_id UUID NOT NULL, \n\treason VARCHAR(24) NOT NULL, \n\tpayload_jsonb JSONB NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_control_closure_value CHECK (reason IN ('replaced','absent') AND length(payload_sha256)=64), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tUNIQUE (closed_line_id), \n\tFOREIGN KEY(closed_line_id) REFERENCES control_projection_lines (id) ON DELETE RESTRICT\n)",
    'CREATE INDEX ix_control_projection_closures_publication_id ON control_projection_closures (publication_id)',
),
'sqlite': (
    'CREATE TABLE control_projection_publications (\n\tid CHAR(32) NOT NULL, \n\tpreparation_id CHAR(32) NOT NULL, \n\tsource_system_id CHAR(32) NOT NULL, \n\tregion_org_id CHAR(32) NOT NULL, \n\tmapping_decision_id CHAR(32) NOT NULL, \n\tprevious_publication_id CHAR(32), \n\tsync_run_id CHAR(32) NOT NULL, \n\tsync_batch_id CHAR(32) NOT NULL, \n\tactor_user_id VARCHAR(36) NOT NULL, \n\tactor_person_id CHAR(32) NOT NULL, \n\tactor_authorization_version BIGINT NOT NULL, \n\tauth_session_id VARCHAR(36) NOT NULL, \n\tevidence_file_id CHAR(32) NOT NULL, \n\tevidence_sha256 VARCHAR(64) NOT NULL, \n\tcaptured_at DATETIME NOT NULL, \n\tvalid_until DATETIME NOT NULL, \n\trecord_count INTEGER NOT NULL, \n\torigin_count INTEGER NOT NULL, \n\tidempotency_key VARCHAR(128) NOT NULL, \n\trequest_id VARCHAR(160) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\treview_sha256 VARCHAR(64) NOT NULL, \n\tpayload_jsonb JSON NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\taudit_event_id CHAR(32) NOT NULL, \n\tcreated_at DATETIME NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_control_publication_key UNIQUE (actor_user_id, idempotency_key), \n\tCONSTRAINT uq_control_publication_request UNIQUE (actor_user_id, request_id), \n\tCONSTRAINT uq_control_publication_capture UNIQUE (source_system_id, region_org_id, captured_at), \n\tCONSTRAINT ck_control_publication_count CHECK (record_count>=0 AND origin_count>=record_count AND actor_authorization_version>0), \n\tCONSTRAINT ck_control_publication_time CHECK (captured_at<=created_at AND created_at<valid_until), \n\tCONSTRAINT ck_control_publication_hashes CHECK (length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64), \n\tUNIQUE (preparation_id), \n\tFOREIGN KEY(preparation_id) REFERENCES inventory_control_preparations (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(source_system_id) REFERENCES source_systems (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(region_org_id) REFERENCES organizations (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(mapping_decision_id) REFERENCES inventory_control_mapping_decisions (id) ON DELETE RESTRICT, \n\tUNIQUE (previous_publication_id), \n\tFOREIGN KEY(previous_publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_run_id), \n\tFOREIGN KEY(sync_run_id) REFERENCES sync_runs (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_batch_id), \n\tFOREIGN KEY(sync_batch_id) REFERENCES sync_batches (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(evidence_file_id) REFERENCES files (id) ON DELETE RESTRICT, \n\tUNIQUE (audit_event_id), \n\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT\n)',
    'CREATE UNIQUE INDEX uq_control_publication_first ON control_projection_publications (source_system_id, region_org_id) WHERE previous_publication_id IS NULL',
    "CREATE TABLE control_projection_lines (\n\tid CHAR(32) NOT NULL, \n\tpublication_id CHAR(32) NOT NULL, \n\tsequence INTEGER NOT NULL, \n\texternal_business_key VARCHAR(250) NOT NULL, \n\texternal_object_id CHAR(32) NOT NULL, \n\tversion_id CHAR(32) NOT NULL, \n\tsync_inbox_event_id CHAR(32) NOT NULL, \n\tprevious_line_id CHAR(32), \n\tmaterial_id CHAR(32) NOT NULL, \n\tcondition_code VARCHAR(24) NOT NULL, \n\tcontrol_qty NUMERIC(18, 3) NOT NULL, \n\tsource_updated_at DATETIME, \n\tpayload_jsonb JSON NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_control_line_sequence UNIQUE (publication_id, sequence), \n\tCONSTRAINT uq_control_line_business_key UNIQUE (publication_id, external_business_key), \n\tCONSTRAINT uq_control_line_publication UNIQUE (id, publication_id), \n\tCONSTRAINT ck_control_line_value CHECK (sequence>0 AND control_qty>=0 AND length(payload_sha256)=64), \n\tCONSTRAINT ck_control_line_condition CHECK (condition_code IN ('new','used','damaged','scrapped')), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(external_object_id) REFERENCES external_objects (id) ON DELETE RESTRICT, \n\tUNIQUE (version_id), \n\tFOREIGN KEY(version_id) REFERENCES external_object_versions (id) ON DELETE RESTRICT, \n\tUNIQUE (sync_inbox_event_id), \n\tFOREIGN KEY(sync_inbox_event_id) REFERENCES sync_inbox_events (id) ON DELETE RESTRICT, \n\tUNIQUE (previous_line_id), \n\tFOREIGN KEY(previous_line_id) REFERENCES control_projection_lines (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(material_id) REFERENCES materials (id) ON DELETE RESTRICT\n)",
    'CREATE INDEX ix_control_projection_lines_external_object_id ON control_projection_lines (external_object_id)',
    'CREATE TABLE control_projection_origins (\n\tid CHAR(32) NOT NULL, \n\tpublication_id CHAR(32) NOT NULL, \n\tline_id CHAR(32) NOT NULL, \n\tsequence INTEGER NOT NULL, \n\texternal_business_key VARCHAR(200) NOT NULL, \n\tcapture_snapshot_id CHAR(32) NOT NULL, \n\tstaging_record_id VARCHAR(36) NOT NULL, \n\tmaterial_line_id CHAR(32) NOT NULL, \n\tpayload_jsonb JSON NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(line_id, publication_id) REFERENCES control_projection_lines (id, publication_id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_control_origin_key UNIQUE (publication_id, external_business_key), \n\tCONSTRAINT uq_control_origin_sequence UNIQUE (line_id, sequence), \n\tCONSTRAINT ck_control_origin_value CHECK (sequence>0 AND length(payload_sha256)=64), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(capture_snapshot_id) REFERENCES inventory_control_capture_snapshots (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(staging_record_id) REFERENCES external_sync_snapshot_records (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(material_line_id) REFERENCES material_projection_lines (id) ON DELETE RESTRICT\n)',
    "CREATE TABLE control_projection_closures (\n\tid CHAR(32) NOT NULL, \n\tpublication_id CHAR(32) NOT NULL, \n\tclosed_line_id CHAR(32) NOT NULL, \n\treason VARCHAR(24) NOT NULL, \n\tpayload_jsonb JSON NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_control_closure_value CHECK (reason IN ('replaced','absent') AND length(payload_sha256)=64), \n\tFOREIGN KEY(publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, \n\tUNIQUE (closed_line_id), \n\tFOREIGN KEY(closed_line_id) REFERENCES control_projection_lines (id) ON DELETE RESTRICT\n)",
    'CREATE INDEX ix_control_projection_closures_publication_id ON control_projection_closures (publication_id)',
),
}

_legacy = runpy.run_path(str(_folder/'20261023_0113_inventory_control_authority.py'))
_principal = _legacy['BODY'].split('    IF NEW.catalog_id IS NOT NULL THEN')[0].replace('0113','0121')
_principal = _principal.replace('    file_evidence jsonb;', '''    file_evidence jsonb;
    prepared public.inventory_control_preparations%ROWTYPE;
    chain public.inventory_control_capture_chains%ROWTYPE;
    mapping public.inventory_control_mapping_decisions%ROWTYPE;
    review jsonb;
    plan jsonb;
    capture jsonb;
    coverage tstzmultirange;
    current_pair jsonb;
    material_origin jsonb;
    started timestamptz;
    completed timestamptz;''')
_principal = _principal.replace('    PERFORM public.rsc_lock_formal_principal_graph_0026', """    IF TG_TABLE_NAME<>'control_projection_publications' THEN RETURN NEW; END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0121 READ COMMITTED required' USING ERRCODE='25000'; END IF;
    PERFORM id FROM public.auth_sessions WHERE id=NEW.auth_session_id FOR UPDATE;
    PERFORM public.rsc_lock_formal_principal_graph_0026""")
_principal = _principal.replace('    SELECT * INTO STRICT binding FROM public.inventory_control_source_bindings WHERE id=NEW.binding_id FOR UPDATE;', '''    SELECT * INTO STRICT prepared FROM public.inventory_control_preparations WHERE id=NEW.preparation_id;
    SELECT * INTO STRICT binding FROM public.inventory_control_source_bindings WHERE id=prepared.binding_id FOR UPDATE;
    PERFORM pg_advisory_xact_lock(hashtextextended('rsc.control-publication.scope:'||binding.source_system_id::text||':'||binding.region_org_id::text,0));''')
# Check role validity again at the deferred commit boundary, after any lock wait.
_principal = _principal.replace('a.valid_to>NEW.created_at','a.valid_to>clock_timestamp()')
FACT_BODY = _principal + """
    review:=NEW.payload_jsonb->'review'; plan:=review->'plan'; request:=review->'command';
    SELECT * INTO STRICT chain FROM public.inventory_control_capture_chains WHERE id=prepared.capture_chain_id;
    SELECT * INTO STRICT mapping FROM public.inventory_control_mapping_decisions WHERE id=NEW.mapping_decision_id;
    SELECT * INTO STRICT catalogue FROM public.inventory_control_catalog_versions WHERE id=prepared.catalog_id;
    PERFORM id FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    PERFORM id FROM public.organizations WHERE id=binding.region_org_id FOR SHARE;
    PERFORM id FROM public.files WHERE id=NEW.evidence_file_id FOR SHARE;
    SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,'mime_type',f.mime_type)
        INTO file_evidence FROM public.files f WHERE f.id=NEW.evidence_file_id AND f.sha256=NEW.evidence_sha256
        AND f.status='available' AND f.size_bytes>0;
    IF NEW.valid_until<=clock_timestamp() OR NOT isfinite(NEW.valid_until) OR NOT isfinite(NEW.captured_at)
       OR NEW.captured_at IS DISTINCT FROM (chain.evidence_jsonb->'snapshots'->-1->'manifest'->>'snapshot_at')::timestamptz
       OR NEW.source_system_id<>binding.source_system_id OR NEW.region_org_id<>binding.region_org_id
       OR NOT EXISTS (SELECT 1 FROM public.source_systems WHERE id=NEW.source_system_id AND code='oam' AND enabled AND mode='read_only')
       OR NOT EXISTS (SELECT 1 FROM public.organizations WHERE id=NEW.region_org_id AND status='active' AND org_type='region_company')
       OR mapping.binding_id<>binding.id OR mapping.catalog_id<>catalogue.id OR mapping.action<>'grant'
       OR mapping.valid_from>NEW.created_at OR mapping.valid_to<=clock_timestamp()
       OR EXISTS (SELECT 1 FROM public.inventory_control_mapping_decisions WHERE revoked_grant_id=mapping.id)
       OR file_evidence IS NULL OR review->'evidence_file' IS DISTINCT FROM file_evidence
       OR request->>'preparation_id' IS DISTINCT FROM prepared.id::text
       OR request->>'preparation_sha256' IS DISTINCT FROM prepared.control_manifest_sha256
       OR request->>'mapping_decision_id' IS DISTINCT FROM mapping.id::text
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key OR request->>'request_id' IS DISTINCT FROM NEW.request_id
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR review->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id OR review->>'actor_person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (review->>'actor_authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->>'auth_session_id' IS DISTINCT FROM NEW.auth_session_id
       OR COALESCE(NEW.payload_jsonb->>'access_issued_at','') !~ '^[0-9]+$'
       OR COALESCE(NEW.payload_jsonb->>'access_expires_at','') !~ '^[0-9]+$'
       OR NOT EXISTS (SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id AND s.user_id=NEW.actor_user_id
          AND s.client_type='web' AND length(btrim(s.device_id))>0 AND s.ip_address ~ '^hmac:[0-9]+:[a-f0-9]{64}$'
          AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
          AND extract(epoch FROM s.created_at)<(NEW.payload_jsonb->>'access_issued_at')::bigint+1
          AND (NEW.payload_jsonb->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
          AND extract(epoch FROM clock_timestamp())<(NEW.payload_jsonb->>'access_expires_at')::bigint)
    THEN RAISE EXCEPTION '0121 current publication authority or evidence mismatch' USING ERRCODE='23514'; END IF;
    -- Source and catalogue grants must continuously cover each capture and now.
    SELECT range_agg(tstzrange(GREATEST(s.valid_from,c.valid_from),LEAST(s.valid_to,c.valid_to),'[)')) INTO coverage
        FROM public.inventory_control_authority_decisions s JOIN public.inventory_control_authority_decisions c ON c.source_grant_id=s.id
        JOIN public.files sf ON sf.id=s.evidence_file_id JOIN public.files cf ON cf.id=c.evidence_file_id
        WHERE s.binding_id=binding.id AND s.action='source_grant' AND c.action='catalog_grant' AND c.catalog_id=catalogue.id
          AND (LEAST(s.valid_to,c.valid_to) IS NULL OR GREATEST(s.valid_from,c.valid_from)<LEAST(s.valid_to,c.valid_to))
          AND NOT EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions r WHERE r.revoked_grant_id IN (s.id,c.id))
          AND sf.status='available' AND sf.sha256=s.evidence_sha256 AND cf.status='available' AND cf.sha256=c.evidence_sha256;
    IF coverage IS NULL OR NOT coverage @> clock_timestamp() THEN
        RAISE EXCEPTION '0121 current source and catalogue coverage required' USING ERRCODE='23514'; END IF;
    FOR capture IN SELECT value FROM jsonb_array_elements(chain.evidence_jsonb->'snapshots') LOOP
        SELECT min((value->>'started_at')::timestamptz),max((value->>'completed_at')::timestamptz) INTO started,completed
            FROM jsonb_array_elements(capture->'warehouses');
        IF started IS NULL OR completed IS NULL OR NOT coverage @> tstzrange(started,completed,'[]') THEN
            RAISE EXCEPTION '0121 capture authority gap' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF NEW.valid_until>started+interval '45 minutes' OR (mapping.valid_to IS NOT NULL AND NEW.valid_until>mapping.valid_to) THEN
        RAISE EXCEPTION '0121 publication deadline exceeds evidence' USING ERRCODE='23514'; END IF;
    current_pair:=plan->'basis'->'authorization_evidence'->'current_authority';
    IF NOT EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions s JOIN public.inventory_control_authority_decisions c ON c.source_grant_id=s.id
        WHERE s.id=(current_pair->>'source_grant_id')::uuid AND c.id=(current_pair->>'catalog_grant_id')::uuid
          AND s.payload_sha256=current_pair->>'source_grant_sha256' AND c.payload_sha256=current_pair->>'catalog_grant_sha256'
          AND s.binding_id=binding.id AND c.catalog_id=catalogue.id AND s.action='source_grant' AND c.action='catalog_grant'
          AND s.valid_from<=NEW.created_at AND c.valid_from<=NEW.created_at
          AND (s.valid_to IS NULL OR NEW.valid_until<=s.valid_to) AND (c.valid_to IS NULL OR NEW.valid_until<=c.valid_to)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id IN (s.id,c.id))) THEN
        RAISE EXCEPTION '0121 current grant identity mismatch' USING ERRCODE='23514'; END IF;
    FOR material_origin IN SELECT o.value FROM jsonb_array_elements(plan->'lines') l
        CROSS JOIN LATERAL jsonb_array_elements(l.value->'origins') o LOOP
        IF NOT EXISTS (SELECT 1 FROM public.material_projection_lines ml
            JOIN public.material_projection_publications mp ON mp.id=ml.publication_id
            JOIN public.oam_material_capture_receipts receipt ON receipt.id=mp.receipt_id
            JOIN public.oam_material_capture_bindings mb ON mb.id=mp.binding_id
            JOIN public.external_objects obj ON obj.id=ml.external_object_id
            JOIN public.external_object_versions v ON v.id=ml.version_id
            JOIN public.materials material ON material.id=ml.material_id
            JOIN public.material_inventory_policies policy ON policy.id=ml.policy_id
            JOIN public.material_source_authority_decisions original_grant ON original_grant.id=(mp.payload_jsonb->'source'->>'current_decision_id')::uuid
            WHERE ml.id=(material_origin->>'material_line_id')::uuid AND ml.payload_sha256=material_origin->>'material_line_sha256'
              AND mb.source_system_id=NEW.source_system_id AND mb.source_instance=binding.binding_jsonb->>'source_instance'
              AND mb.revoked_at IS NULL AND mb.valid_from<=NEW.created_at AND NEW.valid_until<=mb.valid_to
              AND receipt.capture_started_at+interval '45 minutes'>=NEW.valid_until
              AND obj.current_version_id=v.id AND obj.deleted_at IS NULL AND v.is_current AND v.valid_to IS NULL
              AND material.status='active' AND policy.effective_from<=NEW.created_at AND (policy.effective_to IS NULL OR policy.effective_to>=NEW.valid_until)
              AND original_grant.binding_id=mb.id AND original_grant.action='grant'
              AND original_grant.payload_sha256=mp.payload_jsonb->'source'->>'current_decision_sha256'
              AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions WHERE revoked_grant_id=original_grant.id)
              AND EXISTS (SELECT 1 FROM public.material_source_authority_decisions g
                  WHERE g.binding_id=mb.id AND g.action='grant' AND g.valid_from<=NEW.created_at AND g.valid_to>=NEW.valid_until
                    AND NOT EXISTS (SELECT 1 FROM public.material_source_authority_decisions WHERE revoked_grant_id=g.id)))
        THEN RAISE EXCEPTION '0121 current reviewed material source required' USING ERRCODE='23514'; END IF;
    END LOOP;
    expected_audit:=jsonb_build_object('publication_id',NEW.id::text,'sync_run_id',NEW.sync_run_id::text,
        'preparation_id',NEW.preparation_id::text,'record_count',NEW.record_count,'origin_count',NEW.origin_count,
        'payload_sha256',NEW.payload_sha256,'review_sha256',NEW.review_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.action='inventory_control.publish' AND a.aggregate_type='control_projection_publication' AND a.aggregate_id=NEW.id::text
        AND a.actor_user_id=NEW.actor_user_id AND a.request_id='control-publication:'||NEW.id::text
        AND a.occurred_at=NEW.created_at AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit) THEN
        RAISE EXCEPTION '0121 publication audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

GRAPH_BODY = """
DECLARE
    changed uuid;
    publication public.control_projection_publications%ROWTYPE;
    previous public.control_projection_publications%ROWTYPE;
    item public.control_projection_lines%ROWTYPE;
    origin public.control_projection_origins%ROWTYPE;
    closure public.control_projection_closures%ROWTYPE;
    child public.control_projection_lines%ROWTYPE;
    object public.external_objects%ROWTYPE;
    version public.external_object_versions%ROWTYPE;
    event public.sync_inbox_events%ROWTYPE;
    run public.sync_runs%ROWTYPE;
    batch public.sync_batches%ROWTYPE;
    prepared public.inventory_control_preparations%ROWTYPE;
    link public.inventory_control_capture_snapshots%ROWTYPE;
    stage public.external_sync_snapshot_records%ROWTYPE;
    master public.material_projection_lines%ROWTYPE;
    plan jsonb;
    planned jsonb;
    detail jsonb;
    expected jsonb;
    hashes jsonb;
    closing_at timestamptz;
BEGIN
    IF TG_OP='TRUNCATE' THEN
        IF EXISTS (SELECT 1 FROM public.control_projection_publications) THEN
            RAISE EXCEPTION '0121 managed control graph must be retained' USING ERRCODE='23514'; END IF;
        RETURN NULL;
    END IF;
    changed:=CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END;
    -- Reserved publication identities cannot be committed without a fact root.
    IF TG_OP<>'DELETE' THEN
        IF TG_TABLE_NAME='sync_runs' THEN
            IF NEW.run_key LIKE 'control-publication:%' AND NOT EXISTS (SELECT 1 FROM public.control_projection_publications WHERE sync_run_id=changed) THEN
                RAISE EXCEPTION '0121 unbound control run' USING ERRCODE='23514'; END IF;
        ELSIF TG_TABLE_NAME='external_object_versions' THEN
            IF NEW.source_version LIKE 'control-publication:%' AND NOT EXISTS (SELECT 1 FROM public.control_projection_lines WHERE version_id=changed) THEN
                RAISE EXCEPTION '0121 unbound control version' USING ERRCODE='23514'; END IF;
        ELSIF TG_TABLE_NAME='sync_inbox_events' THEN
            IF NEW.source_version LIKE 'control-publication:%' AND NOT EXISTS (SELECT 1 FROM public.control_projection_lines WHERE sync_inbox_event_id=changed) THEN
                RAISE EXCEPTION '0121 unbound control event' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    FOR publication IN SELECT p.* FROM public.control_projection_publications p WHERE
        (TG_TABLE_NAME='control_projection_publications' AND p.id=changed)
        OR (TG_TABLE_NAME='sync_runs' AND p.sync_run_id=changed)
        OR (TG_TABLE_NAME='sync_batches' AND (p.sync_batch_id=changed OR p.sync_run_id=(SELECT run_id FROM public.sync_batches WHERE id=changed)))
        OR EXISTS (SELECT 1 FROM public.control_projection_lines l WHERE l.publication_id=p.id AND (
            (TG_TABLE_NAME='control_projection_lines' AND l.id=changed)
            OR (TG_TABLE_NAME='sync_inbox_events' AND l.sync_inbox_event_id=changed)
            OR (TG_TABLE_NAME='external_objects' AND l.external_object_id=changed)
            OR (TG_TABLE_NAME='external_object_versions' AND l.external_object_id=(SELECT external_object_id FROM public.external_object_versions WHERE id=changed))
            OR (TG_TABLE_NAME='external_object_versions' AND l.version_id=changed)))
        OR EXISTS (SELECT 1 FROM public.control_projection_origins o WHERE o.publication_id=p.id AND TG_TABLE_NAME='control_projection_origins' AND o.id=changed)
        OR EXISTS (SELECT 1 FROM public.control_projection_closures c WHERE c.publication_id=p.id AND TG_TABLE_NAME='control_projection_closures' AND c.id=changed)
        OR (TG_TABLE_NAME='sync_inbox_events' AND p.sync_batch_id=(SELECT batch_id FROM public.sync_inbox_events WHERE id=changed))
    LOOP
        plan:=publication.payload_jsonb->'review'->'plan';
        SELECT * INTO STRICT prepared FROM public.inventory_control_preparations WHERE id=publication.preparation_id;
        SELECT * INTO run FROM public.sync_runs WHERE id=publication.sync_run_id;
        SELECT * INTO batch FROM public.sync_batches WHERE id=publication.sync_batch_id;
        IF publication.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(publication.payload_jsonb),'UTF8')),'hex')
           OR publication.review_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(publication.payload_jsonb->'review'),'UTF8')),'hex')
           OR publication.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(jsonb_build_object(
               'actor_user_id',publication.actor_user_id,'command',publication.payload_jsonb->'review'->'command')),'UTF8')),'hex')
           OR publication.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.control_projection_publication.v1'
           OR publication.payload_jsonb->>'publication_id' IS DISTINCT FROM publication.id::text
           OR (publication.payload_jsonb->>'created_at')::timestamptz IS DISTINCT FROM publication.created_at
           OR (publication.payload_jsonb->>'captured_at')::timestamptz IS DISTINCT FROM publication.captured_at
           OR (publication.payload_jsonb->>'valid_until')::timestamptz IS DISTINCT FROM publication.valid_until
           OR plan->>'preparation_id' IS DISTINCT FROM prepared.id::text OR plan->>'preparation_sha256' IS DISTINCT FROM prepared.control_manifest_sha256
           OR plan->>'binding_id' IS DISTINCT FROM prepared.binding_id::text OR plan->>'catalog_id' IS DISTINCT FROM prepared.catalog_id::text
           OR plan->>'capture_chain_id' IS DISTINCT FROM prepared.capture_chain_id::text
           OR plan->>'source_system_id' IS DISTINCT FROM publication.source_system_id::text OR plan->>'region_org_id' IS DISTINCT FROM publication.region_org_id::text
           OR plan->>'mapping_decision_id' IS DISTINCT FROM publication.mapping_decision_id::text
           OR (plan->>'valid_until')::timestamptz IS DISTINCT FROM publication.valid_until
           OR plan->>'scope_key' IS DISTINCT FROM 'oam_inventory_control:region:'||publication.region_org_id::text
           OR (plan->>'target_record_count')::integer IS DISTINCT FROM publication.origin_count
           OR (plan->>'line_count')::integer IS DISTINCT FROM publication.record_count
           OR jsonb_array_length(plan->'lines') IS DISTINCT FROM publication.record_count
           OR (SELECT count(*) FROM public.control_projection_lines WHERE publication_id=publication.id)<>publication.record_count
           OR (SELECT count(*) FROM public.control_projection_origins WHERE publication_id=publication.id)<>publication.origin_count
           OR publication.payload_jsonb->'lines' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(jsonb_build_array(id::text,payload_sha256) ORDER BY sequence)
                FROM public.control_projection_lines WHERE publication_id=publication.id),'[]'::jsonb)
           OR EXISTS (SELECT 1 FROM generate_series(1,publication.record_count) n WHERE NOT EXISTS (
                SELECT 1 FROM public.control_projection_lines WHERE publication_id=publication.id AND sequence=n))
           OR run.id IS NULL OR batch.id IS NULL
           OR run IS DISTINCT FROM jsonb_populate_record(NULL::public.sync_runs,publication.payload_jsonb->'run')
           OR batch IS DISTINCT FROM jsonb_populate_record(NULL::public.sync_batches,publication.payload_jsonb->'batch')
           OR run.source_system_id<>publication.source_system_id OR run.run_key<>'control-publication:'||publication.id::text
           OR run.scope_key<>plan->>'scope_key' OR run.status<>'completed' OR run.completed_at<>publication.created_at
           OR batch.run_id<>run.id OR batch.entity_type<>'oam_inventory_control' OR batch.sequence<>1 OR batch.status<>'applied'
           OR batch.record_count<>publication.record_count OR batch.received_at<>publication.created_at OR batch.validated_at<>publication.created_at
           OR (SELECT count(*) FROM public.sync_batches WHERE run_id=run.id)<>1
           OR (SELECT count(*) FROM public.sync_inbox_events WHERE batch_id=batch.id)<>publication.record_count
           OR run.manifest_sha256 IS DISTINCT FROM publication.payload_jsonb->>'control_manifest_sha256'
        THEN RAISE EXCEPTION '0121 incomplete control publication graph' USING ERRCODE='23514'; END IF;
        FOREACH detail IN ARRAY ARRAY[jsonb_build_array(plan->'lines',plan->>'lines_sha256'),
            jsonb_build_array(plan->'transport',plan->>'transport_sha256'),jsonb_build_array(plan->'basis',plan->>'basis_sha256')] LOOP
            IF detail->>1 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(detail->0),'UTF8')),'hex') THEN
                RAISE EXCEPTION '0121 plan hash mismatch' USING ERRCODE='23514'; END IF;
        END LOOP;
        SELECT * INTO previous FROM public.control_projection_publications WHERE id=publication.previous_publication_id;
        IF previous.id IS NOT NULL THEN
            IF previous.source_system_id<>publication.source_system_id OR previous.region_org_id<>publication.region_org_id
               OR previous.captured_at>=publication.captured_at OR previous.created_at>=publication.created_at
               OR publication.payload_jsonb->'review'->'previous_publication' IS DISTINCT FROM jsonb_build_object('publication_id',previous.id::text,'payload_sha256',previous.payload_sha256)
            THEN RAISE EXCEPTION '0121 publication lineage mismatch' USING ERRCODE='23514'; END IF;
        ELSIF publication.payload_jsonb->'review'->'previous_publication' IS DISTINCT FROM 'null'::jsonb THEN
            RAISE EXCEPTION '0121 initial publication mismatch' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT closed_line_id FROM public.control_projection_closures WHERE publication_id=publication.id
                   EXCEPT SELECT id FROM public.control_projection_lines WHERE publication_id=previous.id)
           OR EXISTS (SELECT id FROM public.control_projection_lines WHERE publication_id=previous.id
                   EXCEPT SELECT closed_line_id FROM public.control_projection_closures WHERE publication_id=publication.id)
           OR publication.payload_jsonb->'closures' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(jsonb_build_array(id::text,payload_sha256) ORDER BY closed_line_id)
                FROM public.control_projection_closures WHERE publication_id=publication.id),'[]'::jsonb)
        THEN RAISE EXCEPTION '0121 incomplete closure set' USING ERRCODE='23514'; END IF;
        -- Reconstruct the last preserved row per key; deleted and non-target
        -- rows cannot be fabricated as origins or silently omitted as zero.
        WITH latest AS (
            SELECT DISTINCT ON (s.business_key) s.*,cs.sequence FROM public.inventory_control_capture_snapshots cs
            JOIN public.external_sync_snapshot_records s ON s.snapshot_ref_id=cs.snapshot_ref_id AND s.entity_type='inventory'
            WHERE cs.capture_chain_id=prepared.capture_chain_id ORDER BY s.business_key,cs.sequence DESC
        ), targets AS (
            SELECT w.value->>'warehouse_code' warehouse,p.value->>'position_code' position
            FROM public.inventory_control_catalog_versions c CROSS JOIN LATERAL jsonb_array_elements(c.catalog_jsonb->'warehouses') w
            CROSS JOIN LATERAL jsonb_array_elements(w.value->'positions') p
            WHERE c.id=prepared.catalog_id AND p.value->>'region_code'=c.catalog_jsonb->>'target_region_code'
        )
        SELECT COALESCE(jsonb_agg(latest.business_key ORDER BY latest.business_key),'[]'::jsonb) INTO hashes FROM latest
            JOIN targets ON latest.payload_json::jsonb->>'warehouseCode'=targets.warehouse
                AND latest.payload_json::jsonb->>'positionCode'=targets.position WHERE latest.operation='upsert';
        IF hashes IS DISTINCT FROM COALESCE((SELECT jsonb_agg(external_business_key ORDER BY external_business_key)
            FROM public.control_projection_origins WHERE publication_id=publication.id),'[]'::jsonb) THEN
            RAISE EXCEPTION '0121 complete target origin set required' USING ERRCODE='23514'; END IF;
        FOR item IN SELECT * FROM public.control_projection_lines WHERE publication_id=publication.id ORDER BY sequence LOOP
            SELECT * INTO object FROM public.external_objects WHERE id=item.external_object_id;
            SELECT * INTO version FROM public.external_object_versions WHERE id=item.version_id;
            SELECT * INTO event FROM public.sync_inbox_events WHERE id=item.sync_inbox_event_id;
            planned:=plan->'lines'->(item.sequence-1);
            expected:=jsonb_build_object('external_business_key','control:'||encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
                jsonb_build_array(publication.region_org_id::text,item.material_id::text,item.condition_code)),'UTF8')),'hex'),
                'region_org_id',publication.region_org_id::text,'material_id',item.material_id::text,'condition_code',item.condition_code,
                'control_qty',trim_scale(item.control_qty)::text,'mapping_status','resolved','mapping_note','');
            IF object.id IS NULL OR version.id IS NULL OR event.id IS NULL
               OR planned->'payload' IS DISTINCT FROM expected
               OR (planned->>'sequence')::integer IS DISTINCT FROM item.sequence
               OR EXISTS (SELECT 1 FROM public.external_object_versions v WHERE v.external_object_id=object.id
                   AND NOT EXISTS (SELECT 1 FROM public.control_projection_lines l WHERE l.version_id=v.id))
               OR object.source_system_id<>publication.source_system_id OR object.entity_type<>'oam_inventory_control' OR object.external_id<>item.external_business_key
               OR expected->>'external_business_key' IS DISTINCT FROM item.external_business_key
               OR expected->>'region_org_id' IS DISTINCT FROM publication.region_org_id::text OR expected->>'material_id' IS DISTINCT FROM item.material_id::text
               OR expected->>'condition_code' IS DISTINCT FROM item.condition_code OR (expected->>'control_qty')::numeric IS DISTINCT FROM item.control_qty
               OR expected->>'mapping_status' IS DISTINCT FROM 'resolved' OR expected->>'mapping_note' IS DISTINCT FROM ''
               OR (SELECT count(*) FROM jsonb_object_keys(expected))<>7
               OR version.external_object_id<>object.id OR version.payload_jsonb IS DISTINCT FROM expected OR event.payload_jsonb IS DISTINCT FROM expected
               OR version.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
               OR event.payload_sha256<>version.payload_sha256 OR planned->>'payload_sha256' IS DISTINCT FROM version.payload_sha256
               OR version.source_version IS DISTINCT FROM 'control-publication:'||publication.id::text OR event.source_version IS DISTINCT FROM version.source_version
               OR version.valid_from<>publication.created_at OR version.created_at<>publication.created_at
               OR version.source_updated_at IS DISTINCT FROM item.source_updated_at OR event.source_updated_at IS DISTINCT FROM item.source_updated_at
               OR (planned->>'source_updated_at')::timestamptz IS DISTINCT FROM item.source_updated_at
               OR event.source_system_id<>publication.source_system_id OR event.batch_id<>batch.id OR event.entity_type<>'oam_inventory_control'
               OR event.external_id<>item.external_business_key OR event.external_event_id<>'control-publication:'||item.id::text
               OR event.status<>'applied' OR event.error_code IS NOT NULL OR event.error_detail IS NOT NULL
               OR event.created_at<>publication.created_at OR event.processed_at<>publication.created_at
               OR item.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(item.payload_jsonb),'UTF8')),'hex')
               OR item.payload_jsonb->>'line_id' IS DISTINCT FROM item.id::text OR item.payload_jsonb->>'publication_id' IS DISTINCT FROM publication.id::text
               OR item.payload_jsonb->>'external_object_id' IS DISTINCT FROM object.id::text OR item.payload_jsonb->>'version_id' IS DISTINCT FROM version.id::text
               OR item.payload_jsonb->>'sync_inbox_event_id' IS DISTINCT FROM event.id::text
               OR item.payload_jsonb->>'previous_line_id' IS DISTINCT FROM item.previous_line_id::text
               OR item.payload_jsonb->'control_payload' IS DISTINCT FROM expected
               OR item.payload_jsonb->>'control_payload_sha256' IS DISTINCT FROM version.payload_sha256
               OR item.payload_jsonb->'origins' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(jsonb_build_array(id::text,payload_sha256) ORDER BY sequence)
                    FROM public.control_projection_origins WHERE line_id=item.id),'[]'::jsonb)
               OR jsonb_array_length(planned->'origins')=0 OR jsonb_array_length(planned->'origins') IS DISTINCT FROM (SELECT count(*) FROM public.control_projection_origins WHERE line_id=item.id)
               OR (SELECT sum((payload_jsonb->'origin'->>'control_qty')::numeric) FROM public.control_projection_origins WHERE line_id=item.id) IS DISTINCT FROM item.control_qty
            THEN RAISE EXCEPTION '0121 control line mismatch' USING ERRCODE='23514'; END IF;
            FOR origin IN SELECT * FROM public.control_projection_origins WHERE line_id=item.id ORDER BY sequence LOOP
                detail:=planned->'origins'->(origin.sequence-1);
                SELECT * INTO link FROM public.inventory_control_capture_snapshots WHERE id=origin.capture_snapshot_id;
                SELECT * INTO stage FROM public.external_sync_snapshot_records WHERE id=origin.staging_record_id;
                SELECT * INTO master FROM public.material_projection_lines WHERE id=origin.material_line_id;
                IF origin.payload_jsonb IS DISTINCT FROM jsonb_build_object('origin_id',origin.id::text,'publication_id',publication.id::text,
                    'line_id',item.id::text,'sequence',origin.sequence,'origin',detail)
                   OR origin.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(origin.payload_jsonb),'UTF8')),'hex')
                   OR link.id IS NULL OR stage.id IS NULL OR master.id IS NULL
                   OR link.capture_chain_id<>prepared.capture_chain_id OR stage.snapshot_ref_id<>link.snapshot_ref_id
                   OR stage.entity_type<>'inventory' OR stage.operation<>'upsert' OR stage.business_key<>origin.external_business_key
                   OR detail->>'external_business_key' IS DISTINCT FROM stage.business_key OR detail->>'capture_snapshot_id' IS DISTINCT FROM link.id::text
                   OR detail->>'staging_record_id' IS DISTINCT FROM stage.id OR detail->>'snapshot_ref_id' IS DISTINCT FROM stage.snapshot_ref_id
                   OR detail->>'staging_payload_sha256' IS DISTINCT FROM stage.payload_sha256
                   OR stage.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(stage.payload_json::jsonb),'UTF8')),'hex')
                   OR master.material_id<>item.material_id OR detail->>'material_line_id' IS DISTINCT FROM master.id::text
                   OR detail->>'material_line_sha256' IS DISTINCT FROM master.payload_sha256 OR detail->>'policy_id' IS DISTINCT FROM master.policy_id::text
                   OR detail->>'material_version_id' IS DISTINCT FROM master.version_id::text OR detail->>'material_publication_id' IS DISTINCT FROM master.publication_id::text
                   OR (stage.payload_json::jsonb->>'qtyStock')::numeric IS DISTINCT FROM (detail->>'control_qty')::numeric
                   OR (stage.payload_json::jsonb->>'qtyLock')::numeric IS DISTINCT FROM (detail->>'locked_qty')::numeric
                   OR (detail->>'locked_qty')::numeric<0 OR (detail->>'locked_qty')::numeric>(detail->>'control_qty')::numeric
                   OR stage.payload_json::jsonb->>'materialCode' IS DISTINCT FROM master.sku_code
                   OR stage.payload_json::jsonb->>'unitName' IS DISTINCT FROM master.payload_jsonb->'normalized'->>'base_unit'
                   OR NOT EXISTS (SELECT 1 FROM public.inventory_control_mapping_decisions m
                       CROSS JOIN LATERAL jsonb_array_elements(m.rules_jsonb->'conditions') r WHERE m.id=publication.mapping_decision_id
                       AND r.value->'material_status'=stage.payload_json::jsonb->'materialStatus'
                       AND r.value->'material_stock_type'=stage.payload_json::jsonb->'materialStockType'
                       AND r.value->>'condition_code'=item.condition_code)
                   OR stage.payload_json::jsonb->>'warehouseCode' IS DISTINCT FROM detail->>'warehouse_code'
                   OR stage.payload_json::jsonb->>'positionCode' IS DISTINCT FROM detail->>'position_code'
                   OR EXISTS (SELECT 1 FROM public.inventory_control_capture_snapshots later JOIN public.external_sync_snapshot_records s ON s.snapshot_ref_id=later.snapshot_ref_id
                       WHERE later.capture_chain_id=prepared.capture_chain_id AND later.sequence>link.sequence AND s.entity_type='inventory' AND s.business_key=stage.business_key)
                THEN RAISE EXCEPTION '0121 preserved control origin mismatch' USING ERRCODE='23514'; END IF;
            END LOOP;
            SELECT * INTO closure FROM public.control_projection_closures WHERE closed_line_id=item.id;
            SELECT * INTO child FROM public.control_projection_lines WHERE previous_line_id=item.id;
            IF closure.id IS NULL THEN
                IF child.id IS NOT NULL OR NOT version.is_current OR version.valid_to IS NOT NULL OR object.deleted_at IS NOT NULL OR object.current_version_id IS DISTINCT FROM version.id THEN
                    RAISE EXCEPTION '0121 current control version drift' USING ERRCODE='23514'; END IF;
            ELSE
                SELECT created_at INTO closing_at FROM public.control_projection_publications WHERE id=closure.publication_id;
                IF version.is_current OR version.valid_to IS DISTINCT FROM closing_at OR closing_at<=publication.created_at
                   OR closure.payload_jsonb->>'closed_line_id' IS DISTINCT FROM item.id::text
                   OR closure.payload_jsonb->>'publication_id' IS DISTINCT FROM closure.publication_id::text
                   OR (closure.payload_jsonb->>'closed_at')::timestamptz IS DISTINCT FROM closing_at
                   OR closure.payload_jsonb->>'reason' IS DISTINCT FROM closure.reason
                   OR closure.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(closure.payload_jsonb),'UTF8')),'hex')
                   OR (closure.reason='replaced' AND (child.id IS NULL OR child.publication_id<>closure.publication_id))
                   OR (child.id IS NULL AND (object.current_version_id IS DISTINCT FROM version.id OR object.deleted_at IS DISTINCT FROM closing_at))
                THEN RAISE EXCEPTION '0121 closed control version drift' USING ERRCODE='23514'; END IF;
            END IF;
            IF child.id IS NOT NULL AND (child.external_object_id<>item.external_object_id OR child.material_id<>item.material_id
                OR child.condition_code<>item.condition_code OR (SELECT created_at FROM public.control_projection_publications WHERE id=child.publication_id)<version.valid_to) THEN
                RAISE EXCEPTION '0121 control version lineage mismatch' USING ERRCODE='23514'; END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
"""
FUNCTIONS={FACT_FUNCTION:(FACT_BODY,False),GRAPH_FUNCTION:(GRAPH_BODY,True)}
FUNCTION_HASHES={name:hashlib.sha256(body.encode()).hexdigest() for name,(body,_) in FUNCTIONS.items()}
TRIGGERS={'trg_control_publication_commit_0121':(TABLES[0],FACT_FUNCTION,'INSERT',5,True)}
for _table in TABLES:
    TRIGGERS['trg_'+_table+'_facts_0121']=(_table,FACT_FUNCTION,'INSERT OR UPDATE OR DELETE',31,False)
    TRIGGERS['trg_'+_table+'_truncate_0121']=(_table,FACT_FUNCTION,'TRUNCATE',34,False)
for _table in (*TABLES,*CORE):
    TRIGGERS['trg_'+_table+'_graph_0121']=(_table,GRAPH_FUNCTION,'INSERT OR UPDATE OR DELETE',29,True)
for _table in CORE:
    TRIGGERS['trg_'+_table+'_truncate_0121']=(_table,GRAPH_FUNCTION,'TRUNCATE',34,False)
FILE_TRIGGER='trg_control_publication_file_0121'


def _transition(upgrade):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}: raise RuntimeError('0121 supports PostgreSQL and SQLite only')
    helper=runpy.run_path(str(_folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql': op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if upgrade:
        for statement in DDL[dialect]: op.execute(statement)
    else:
        if dialect=='postgresql': op.execute('LOCK TABLE '+','.join('public.'+t for t in (*TABLES,*CORE))+' IN ACCESS EXCLUSIVE MODE')
        predicate=' OR '.join(f'EXISTS (SELECT 1 FROM {t})' for t in TABLES)
        predicate+=" OR EXISTS (SELECT 1 FROM audit_events WHERE action='inventory_control.publish' OR aggregate_type='control_projection_publication')"
        helper['_preflight'](predicate,'0121 downgrade blocked: control publications must be retained')
    if dialect=='postgresql':
        revoke=runpy.run_path(str(_folder/'20261026_0116_material_capture_ingress.py'))['_revoke']
        if upgrade:
            for name,(body,definer) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql SECURITY {'DEFINER' if definer else 'INVOKER'} SET search_path=pg_catalog, public AS $body${body}$body$")
                revoke('FUNCTION','public.'+name+'()','pg_proc','public.'+name+'()')
            for table in TABLES:
                revoke('TABLE','public.'+table,'pg_class','public.'+table)
                op.execute(f'GRANT SELECT ON TABLE public.{table} TO star_oam_backup')
            for name,(table,function,events,_,deferred) in TRIGGERS.items():
                op.execute(f"CREATE {'CONSTRAINT ' if deferred else ''}TRIGGER {name} {'AFTER' if deferred else 'BEFORE'} {events} ON public.{table}"
                    +(' DEFERRABLE INITIALLY DEFERRED' if deferred else '')+f" FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{function}()")
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'CREATE TRIGGER {FILE_TRIGGER} BEFORE INSERT ON public.{TABLES[0]} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_source_evidence_0120()')
            op.execute(f'ALTER TABLE public.{TABLES[0]} ENABLE ALWAYS TRIGGER {FILE_TRIGGER}')
        else:
            for name,(body,definer) in FUNCTIONS.items():
                op.execute(f"""DO $body$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}()'::regprocedure
                    AND p.prosecdef={'true' if definer else 'false'} AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                    AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{FUNCTION_HASHES[name]}' AND p.proconfig=ARRAY['search_path=pg_catalog, public']
                    AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
                    THEN RAISE EXCEPTION '0121 guard source, ownership or ACL drift'; END IF; END $body$""")
            op.execute(f'DROP TRIGGER {FILE_TRIGGER} ON public.{TABLES[0]}')
            for name,(table,*_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name in FUNCTIONS: op.execute(f'DROP FUNCTION public.{name}()')
        replace=runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,replacements=((down_revision,revision),) if upgrade else ((revision,down_revision),),label='control_publication_readiness_0121')
    elif upgrade:
        for table in TABLES:
            for event in ('UPDATE','DELETE'):
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0121 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0121 control publications are append-only'); END")
        op.execute(f"CREATE TRIGGER {FILE_TRIGGER} BEFORE INSERT ON {TABLES[0]} WHEN NOT ({_previous['binding_sql']('sqlite')}) BEGIN SELECT RAISE(ABORT,'0121 completed source configuration evidence required'); END")
    if not upgrade:
        for table in reversed(TABLES): op.drop_table(table)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
