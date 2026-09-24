"""Record immutable daily mapping approvals and captured comparison cutoffs.

The API cannot write these internal facts. Existing stock and reconciliation
rows are retained; populated downgrade is refused. Capture is not approval.
"""
import hashlib
from pathlib import Path
import runpy
from alembic import op
revision='20261109_0130'
down_revision='20261108_0129'
branch_labels=depends_on=None
OLD_HASH='81ad8bfa4b70b8fbb1d84a624e368b40b4c36a1eeb97d575138634d0c52ee2a4'
NEW_HASH='ef4d75933015da040fbb6f632cab2c7741566608d87949bc391dcdf8cc6e7d7e'
DDL = {
    'postgresql': [
"""
CREATE TABLE daily_comparison_mapping_decisions (
	id UUID NOT NULL, 
	binding_id UUID NOT NULL, 
	catalog_id UUID NOT NULL, 
	action VARCHAR(24) NOT NULL, 
	revoked_grant_id UUID, 
	rules_revision VARCHAR(80) NOT NULL, 
	rules_jsonb JSONB NOT NULL, 
	rules_sha256 VARCHAR(64) NOT NULL, 
	valid_from TIMESTAMP WITH TIME ZONE NOT NULL, 
	valid_to TIMESTAMP WITH TIME ZONE, 
	actor_user_id VARCHAR(36) NOT NULL, 
	actor_person_id UUID NOT NULL, 
	actor_authorization_version BIGINT NOT NULL, 
	auth_session_id VARCHAR(36) NOT NULL, 
	evidence_file_id UUID NOT NULL, 
	evidence_sha256 VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_id VARCHAR(160) NOT NULL, 
	request_sha256 VARCHAR(64) NOT NULL, 
	review_sha256 VARCHAR(64) NOT NULL, 
	payload_jsonb JSONB NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	audit_event_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_daily_mapping_action CHECK (action IN ('grant','revoke')), 
	CONSTRAINT ck_daily_mapping_validity CHECK (valid_from>=created_at AND (valid_to IS NULL OR valid_to>valid_from)), 
	CONSTRAINT ck_daily_mapping_shape CHECK ((action='grant' AND revoked_grant_id IS NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND valid_to IS NULL AND valid_from=created_at)), 
	CONSTRAINT ck_daily_mapping_proof CHECK (actor_authorization_version>0 AND length(rules_sha256)=64 AND length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64), 
	CONSTRAINT uq_daily_mapping_actor_key UNIQUE (actor_user_id, idempotency_key), 
	CONSTRAINT uq_daily_mapping_actor_request UNIQUE (actor_user_id, request_id), 
	CONSTRAINT uq_daily_mapping_revocation UNIQUE (revoked_grant_id), 
	FOREIGN KEY(binding_id) REFERENCES inventory_control_source_bindings (id) ON DELETE RESTRICT, 
	FOREIGN KEY(catalog_id) REFERENCES inventory_control_catalog_versions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(revoked_grant_id) REFERENCES daily_comparison_mapping_decisions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, 
	FOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(evidence_file_id) REFERENCES files (id) ON DELETE RESTRICT, 
	UNIQUE (audit_event_id), 
	FOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
)

""",
"""
CREATE TABLE daily_reconciliation_cutoffs (
	id UUID NOT NULL, 
	business_date DATE NOT NULL, 
	source_publication_id UUID NOT NULL, 
	mapping_decision_id UUID NOT NULL, 
	source_system_id UUID NOT NULL, 
	region_org_id UUID NOT NULL, 
	local_ledger_cursor BIGINT NOT NULL, 
	source_captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	local_captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor_user_id VARCHAR(36) NOT NULL, 
	actor_person_id UUID NOT NULL, 
	actor_authorization_version BIGINT NOT NULL, 
	auth_session_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_id VARCHAR(160) NOT NULL, 
	request_sha256 VARCHAR(64) NOT NULL, 
	payload_jsonb JSONB NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	audit_event_id UUID NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_cutoff_actor_key UNIQUE (actor_user_id, idempotency_key), 
	CONSTRAINT uq_daily_cutoff_actor_request UNIQUE (actor_user_id, request_id), 
	CONSTRAINT uq_daily_cutoff_basis UNIQUE (source_publication_id, mapping_decision_id, local_ledger_cursor), 
	CONSTRAINT ck_daily_cutoff_versions CHECK (local_ledger_cursor>=0 AND actor_authorization_version>0), 
	CONSTRAINT ck_daily_cutoff_time CHECK (source_captured_at<=local_captured_at AND local_captured_at<=created_at), 
	CONSTRAINT ck_daily_cutoff_hashes CHECK (length(payload_sha256)=64 AND length(request_sha256)=64), 
	FOREIGN KEY(source_publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, 
	FOREIGN KEY(mapping_decision_id) REFERENCES daily_comparison_mapping_decisions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(source_system_id) REFERENCES source_systems (id) ON DELETE RESTRICT, 
	FOREIGN KEY(region_org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, 
	FOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, 
	UNIQUE (audit_event_id), 
	FOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
)

""",
"""CREATE INDEX ix_daily_mapping_subject ON daily_comparison_mapping_decisions (binding_id, catalog_id, created_at, id)""",
"""CREATE UNIQUE INDEX uq_daily_mapping_revision ON daily_comparison_mapping_decisions (binding_id, catalog_id, rules_revision) WHERE action='grant'""",
    ],
    'sqlite': [
"""
CREATE TABLE daily_comparison_mapping_decisions (
	id CHAR(32) NOT NULL, 
	binding_id CHAR(32) NOT NULL, 
	catalog_id CHAR(32) NOT NULL, 
	action VARCHAR(24) NOT NULL, 
	revoked_grant_id CHAR(32), 
	rules_revision VARCHAR(80) NOT NULL, 
	rules_jsonb JSON NOT NULL, 
	rules_sha256 VARCHAR(64) NOT NULL, 
	valid_from DATETIME NOT NULL, 
	valid_to DATETIME, 
	actor_user_id VARCHAR(36) NOT NULL, 
	actor_person_id CHAR(32) NOT NULL, 
	actor_authorization_version BIGINT NOT NULL, 
	auth_session_id VARCHAR(36) NOT NULL, 
	evidence_file_id CHAR(32) NOT NULL, 
	evidence_sha256 VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_id VARCHAR(160) NOT NULL, 
	request_sha256 VARCHAR(64) NOT NULL, 
	review_sha256 VARCHAR(64) NOT NULL, 
	payload_jsonb JSON NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	audit_event_id CHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_daily_mapping_action CHECK (action IN ('grant','revoke')), 
	CONSTRAINT ck_daily_mapping_validity CHECK (valid_from>=created_at AND (valid_to IS NULL OR valid_to>valid_from)), 
	CONSTRAINT ck_daily_mapping_shape CHECK ((action='grant' AND revoked_grant_id IS NULL) OR (action='revoke' AND revoked_grant_id IS NOT NULL AND valid_to IS NULL AND valid_from=created_at)), 
	CONSTRAINT ck_daily_mapping_proof CHECK (actor_authorization_version>0 AND length(rules_sha256)=64 AND length(payload_sha256)=64 AND length(request_sha256)=64 AND length(review_sha256)=64 AND length(evidence_sha256)=64), 
	CONSTRAINT uq_daily_mapping_actor_key UNIQUE (actor_user_id, idempotency_key), 
	CONSTRAINT uq_daily_mapping_actor_request UNIQUE (actor_user_id, request_id), 
	CONSTRAINT uq_daily_mapping_revocation UNIQUE (revoked_grant_id), 
	FOREIGN KEY(binding_id) REFERENCES inventory_control_source_bindings (id) ON DELETE RESTRICT, 
	FOREIGN KEY(catalog_id) REFERENCES inventory_control_catalog_versions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(revoked_grant_id) REFERENCES daily_comparison_mapping_decisions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, 
	FOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(evidence_file_id) REFERENCES files (id) ON DELETE RESTRICT, 
	UNIQUE (audit_event_id), 
	FOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
)

""",
"""
CREATE TABLE daily_reconciliation_cutoffs (
	id CHAR(32) NOT NULL, 
	business_date DATE NOT NULL, 
	source_publication_id CHAR(32) NOT NULL, 
	mapping_decision_id CHAR(32) NOT NULL, 
	source_system_id CHAR(32) NOT NULL, 
	region_org_id CHAR(32) NOT NULL, 
	local_ledger_cursor BIGINT NOT NULL, 
	source_captured_at DATETIME NOT NULL, 
	local_captured_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	actor_user_id VARCHAR(36) NOT NULL, 
	actor_person_id CHAR(32) NOT NULL, 
	actor_authorization_version BIGINT NOT NULL, 
	auth_session_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(128) NOT NULL, 
	request_id VARCHAR(160) NOT NULL, 
	request_sha256 VARCHAR(64) NOT NULL, 
	payload_jsonb JSON NOT NULL, 
	payload_sha256 VARCHAR(64) NOT NULL, 
	audit_event_id CHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_cutoff_actor_key UNIQUE (actor_user_id, idempotency_key), 
	CONSTRAINT uq_daily_cutoff_actor_request UNIQUE (actor_user_id, request_id), 
	CONSTRAINT uq_daily_cutoff_basis UNIQUE (source_publication_id, mapping_decision_id, local_ledger_cursor), 
	CONSTRAINT ck_daily_cutoff_versions CHECK (local_ledger_cursor>=0 AND actor_authorization_version>0), 
	CONSTRAINT ck_daily_cutoff_time CHECK (source_captured_at<=local_captured_at AND local_captured_at<=created_at), 
	CONSTRAINT ck_daily_cutoff_hashes CHECK (length(payload_sha256)=64 AND length(request_sha256)=64), 
	FOREIGN KEY(source_publication_id) REFERENCES control_projection_publications (id) ON DELETE RESTRICT, 
	FOREIGN KEY(mapping_decision_id) REFERENCES daily_comparison_mapping_decisions (id) ON DELETE RESTRICT, 
	FOREIGN KEY(source_system_id) REFERENCES source_systems (id) ON DELETE RESTRICT, 
	FOREIGN KEY(region_org_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(actor_person_id) REFERENCES people (id) ON DELETE RESTRICT, 
	FOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id) ON DELETE RESTRICT, 
	UNIQUE (audit_event_id), 
	FOREIGN KEY(audit_event_id) REFERENCES audit_events (id) ON DELETE RESTRICT
)

""",
"""CREATE INDEX ix_daily_mapping_subject ON daily_comparison_mapping_decisions (binding_id, catalog_id, created_at, id)""",
"""CREATE UNIQUE INDEX uq_daily_mapping_revision ON daily_comparison_mapping_decisions (binding_id, catalog_id, rules_revision) WHERE action='grant'""",
    ],
}
TABLES = ('daily_comparison_mapping_decisions', 'daily_reconciliation_cutoffs')
FUNCTIONS = {
    ('rsc_validate_daily_mapping_0130', 'jsonb, uuid, uuid'): {
        "args": 'rules jsonb,binding_id uuid,catalog_id uuid', "result": 'void',
        "body": """
DECLARE m jsonb; b public.inventory_control_source_bindings%ROWTYPE; c public.inventory_control_catalog_versions%ROWTYPE;
 locations jsonb; organizations jsonb; warehouses jsonb;
BEGIN
 IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
  RAISE EXCEPTION 'daily mapping requires direct owner' USING ERRCODE='42501'; END IF;
 LOCK TABLE public.organizations, public.stock_locations IN SHARE MODE;
 SELECT * INTO STRICT b FROM public.inventory_control_source_bindings WHERE id=binding_id FOR UPDATE;
 SELECT * INTO STRICT c FROM public.inventory_control_catalog_versions WHERE id=catalog_id AND inventory_control_catalog_versions.binding_id=b.id;
 m:=rules->'mapping';
 IF jsonb_typeof(rules) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(rules))<>2
  OR jsonb_typeof(m) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(m))<>13
  OR rules->>'revision' IS DISTINCT FROM m->>'version_id'
  OR (m->>'version_id')::uuid='00000000-0000-0000-0000-000000000000'::uuid
  OR m->>'schema' IS DISTINCT FROM 'rsc.daily_comparison_mapping_candidate.v1'
  OR m->>'source_system_id' IS DISTINCT FROM b.source_system_id::text
  OR m->>'region_id' IS DISTINCT FROM b.region_org_id::text
  OR m->>'catalog_sha256' IS DISTINCT FROM c.catalog_sha256
  OR m->>'scope_policy' IS DISTINCT FROM 'explicit_physical_location'
  OR m->>'source_quantity_policy' IS DISTINCT FROM 'published_quantity_locked_separate'
  OR m->>'content_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(m-'content_sha256'),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'daily mapping shape or subject invalid' USING ERRCODE='23514'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]') INTO locations FROM
  (SELECT id,owner_org_id,parent_id,location_type,status,custodian_person_id,code FROM public.stock_locations) t;
 SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]') INTO organizations FROM
  (SELECT id,parent_id,org_type,status,code FROM public.organizations) t;
 IF m->>'location_facts_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(locations),'UTF8')),'hex')
  OR m->>'organization_facts_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(organizations),'UTF8')),'hex')
 THEN RAISE EXCEPTION 'daily mapping reference facts changed' USING ERRCODE='23514'; END IF;
 SELECT COALESCE(jsonb_agg(w->'warehouse_code' ORDER BY w->>'warehouse_code'),'[]') INTO warehouses
 FROM jsonb_array_elements(c.catalog_jsonb->'warehouses') w WHERE EXISTS (
  SELECT 1 FROM jsonb_array_elements(w->'positions') p WHERE p->>'region_code'=b.target_region_code);
 IF jsonb_typeof(m->'warehouses') IS DISTINCT FROM 'array' OR jsonb_array_length(warehouses)=0
  OR (SELECT jsonb_agg(w ORDER BY w) FROM jsonb_array_elements(m->'warehouses') w) IS DISTINCT FROM warehouses
  OR jsonb_typeof(m->'included_buckets') IS DISTINCT FROM 'array' OR jsonb_array_length(m->'included_buckets')=0
  OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(m->'included_buckets') v WHERE v NOT IN
   ('available','reserved','picking','outbound','in_transit','arrived_pending','frozen','return_pending','scrap_pending'))
  OR (SELECT count(*)<>count(DISTINCT v) FROM jsonb_array_elements(m->'included_buckets') v)
  OR jsonb_typeof(m->'location_bindings') IS DISTINCT FROM 'array'
  OR jsonb_array_length(m->'location_bindings')<>jsonb_array_length(locations)
 THEN RAISE EXCEPTION 'daily mapping coverage invalid' USING ERRCODE='23514'; END IF;
 IF EXISTS (SELECT 1 FROM jsonb_array_elements(m->'location_bindings') v WHERE jsonb_typeof(v) IS DISTINCT FROM 'object'
   OR (SELECT count(*) FROM jsonb_object_keys(v))<>3
   OR NOT EXISTS (SELECT 1 FROM public.stock_locations l WHERE l.id::text=v->>'location_id')
   OR NOT EXISTS (SELECT 1 FROM public.organizations o WHERE o.id::text=v->>'region_id' AND o.org_type='region_company')
   OR (v->>'region_id'=b.region_org_id::text AND NOT (warehouses @> jsonb_build_array(v->'warehouse_code')))
   OR (v->>'region_id'<>b.region_org_id::text AND v->'warehouse_code' IS DISTINCT FROM 'null'::jsonb))
  OR (SELECT count(*)<>count(DISTINCT v->>'location_id') FROM jsonb_array_elements(m->'location_bindings') v)
 THEN RAISE EXCEPTION 'daily mapping locations invalid' USING ERRCODE='23514'; END IF;
END;
""",
        "sha256": 'dd3cae1c40f0cb87f9a4aefc0a319156ebb60cf0d14fff2e92b22ece6323204f',
    },
    ('rsc_guard_daily_mapping_0130', ''): {
        "args": '', "result": 'trigger',
        "body": """
DECLARE
    binding public.inventory_control_source_bindings%ROWTYPE;
    parent public.daily_comparison_mapping_decisions%ROWTYPE;
    catalogue public.inventory_control_catalog_versions%ROWTYPE;
    subject jsonb;
    expected_audit jsonb;
    request jsonb;
    file_evidence jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0130 authority decisions are append-only' USING ERRCODE='23514';
    END IF;
    IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
        RAISE EXCEPTION '0130 authority requires direct schema owner' USING ERRCODE='42501';
    END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    SELECT * INTO STRICT binding FROM public.inventory_control_source_bindings WHERE id=NEW.binding_id FOR UPDATE;
    IF NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp() THEN
        RAISE EXCEPTION '0130 authority cannot backdate its decision' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
        JOIN public.organizations o ON o.id=p.organization_id
        JOIN public.role_assignments a ON a.user_id=u.id
        JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id
        JOIN public.permissions permission ON permission.id=rp.permission_id
        WHERE u.id=NEW.actor_user_id AND u.person_id=NEW.actor_person_id AND u.is_active AND u.account_status='active'
          AND u.authorization_version=NEW.actor_authorization_version AND p.employment_status='active'
          AND o.status='active' AND o.org_type='headquarters'
          AND r.code='admin' AND r.status='active' AND NOT r.is_external
          AND a.scope_type='national' AND a.scope_id='*' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=NEW.created_at AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at)
          AND permission.resource='inventory_control' AND permission.action='authorize' AND permission.field_code='' AND rp.effect='allow'
          AND EXISTS (SELECT 1 FROM public.auth_identities identity WHERE identity.user_id=u.id
              AND identity.status='active' AND identity.revoked_at IS NULL AND identity.verified_at<=NEW.created_at)
    ) OR EXISTS (
        SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=NEW.actor_user_id AND r.status='active' AND a.scope_type='national' AND a.scope_id='*'
          AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL AND a.valid_from<=NEW.created_at
          AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at) AND rp.effect='deny'
          AND p.resource='inventory_control' AND p.action='authorize' AND p.field_code=''
    ) THEN RAISE EXCEPTION '0130 active headquarters authority required' USING ERRCODE='23514'; END IF;
    IF NEW.catalog_id IS NOT NULL THEN
        SELECT * INTO catalogue FROM public.inventory_control_catalog_versions WHERE id=NEW.catalog_id AND binding_id=NEW.binding_id;
        IF catalogue.id IS NULL THEN RAISE EXCEPTION '0130 catalogue binding mismatch' USING ERRCODE='23514'; END IF;
    END IF;
    subject:=jsonb_build_object('binding_id',binding.id::text,'binding_sha256',binding.binding_sha256,
        'source_system_id',binding.source_system_id::text,'region_org_id',binding.region_org_id::text,
        'target_region_code',binding.target_region_code,'catalog_id',NEW.catalog_id::text,'catalog_sha256',catalogue.catalog_sha256);
    SELECT jsonb_build_object('file_id',f.id::text,'sha256',f.sha256,'size_bytes',f.size_bytes,
        'mime_type',f.mime_type,'storage_key',f.storage_key) INTO file_evidence FROM public.files f
        WHERE f.id=NEW.evidence_file_id AND f.sha256=NEW.evidence_sha256 AND f.status='available' AND f.size_bytes>0 FOR SHARE;

    PERFORM id FROM public.source_systems WHERE id=binding.source_system_id FOR SHARE;
    PERFORM id FROM public.organizations WHERE id=binding.region_org_id FOR SHARE;
    request:=NEW.payload_jsonb->'request';
    IF file_evidence IS NULL OR NEW.payload_jsonb->'evidence_file' IS DISTINCT FROM file_evidence
       OR NEW.payload_jsonb->'subject' IS DISTINCT FROM subject
       OR NEW.payload_jsonb->>'schema_version' IS DISTINCT FROM 'rsc.daily_comparison_mapping_decision.v1'
       OR NEW.payload_jsonb->>'decision_id' IS DISTINCT FROM NEW.id::text
       OR NEW.payload_jsonb->>'action' IS DISTINCT FROM NEW.action
       OR NEW.payload_jsonb->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR NEW.payload_jsonb->'rules' IS DISTINCT FROM NEW.rules_jsonb
       OR NEW.rules_jsonb->>'revision' IS DISTINCT FROM NEW.rules_revision
       OR NEW.payload_jsonb->>'rules_sha256' IS DISTINCT FROM NEW.rules_sha256
       OR NEW.rules_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.rules_jsonb),'UTF8')),'hex')
       OR (NEW.payload_jsonb->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
       OR (NEW.payload_jsonb->>'valid_from')::timestamptz IS DISTINCT FROM NEW.valid_from
       OR (NEW.payload_jsonb->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR NEW.payload_jsonb->>'actor_user_id' IS DISTINCT FROM NEW.actor_user_id
       OR NEW.payload_jsonb->>'actor_person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (NEW.payload_jsonb->>'actor_authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->'actor_snapshot'->>'user_id' IS DISTINCT FROM NEW.actor_user_id
       OR NEW.payload_jsonb->'actor_snapshot'->>'person_id' IS DISTINCT FROM NEW.actor_person_id::text
       OR (NEW.payload_jsonb->'actor_snapshot'->>'authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
       OR NEW.payload_jsonb->>'auth_session_id' IS DISTINCT FROM NEW.auth_session_id
       OR request->>'action' IS DISTINCT FROM NEW.action OR request->>'binding_id' IS DISTINCT FROM NEW.binding_id::text
       OR request->>'catalog_id' IS DISTINCT FROM NEW.catalog_id::text
       OR request->>'revoked_grant_id' IS DISTINCT FROM NEW.revoked_grant_id::text
       OR request->>'evidence_file_id' IS DISTINCT FROM NEW.evidence_file_id::text
       OR request->>'evidence_sha256' IS DISTINCT FROM NEW.evidence_sha256
       OR request->>'request_id' IS DISTINCT FROM NEW.request_id
       OR request->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
       OR (request->>'valid_to')::timestamptz IS DISTINCT FROM NEW.valid_to
       OR COALESCE((request->>'valid_from')::timestamptz,NEW.created_at) IS DISTINCT FROM NEW.valid_from
       OR length(btrim(COALESCE(request->>'reason',''))) NOT BETWEEN 1 AND 1000
       OR NEW.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR NEW.idempotency_key !~ '^[A-Za-z0-9._:-]{16,128}$'
       OR NEW.review_sha256 !~ '^[a-f0-9]{64}$'
       OR NEW.payload_jsonb->>'review_sha256' IS DISTINCT FROM NEW.review_sha256
       OR NEW.payload_jsonb->>'request_sha256' IS DISTINCT FROM NEW.request_sha256
       OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
           jsonb_build_object('actor_user_id',NEW.actor_user_id,'request',request)),'UTF8')),'hex')
       OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(NEW.payload_jsonb),'UTF8')),'hex')
    THEN RAISE EXCEPTION '0130 mapping evidence mismatch' USING ERRCODE='23514'; END IF;
    IF NEW.action='grant' THEN
        PERFORM public.rsc_validate_daily_mapping_0130(NEW.rules_jsonb,NEW.binding_id,NEW.catalog_id);
    END IF;
    IF COALESCE(NEW.payload_jsonb->>'access_issued_at','') !~ '^[0-9]+$'
       OR COALESCE(NEW.payload_jsonb->>'access_expires_at','') !~ '^[0-9]+$'
       OR NOT EXISTS (SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id
          AND s.user_id=NEW.actor_user_id AND s.client_type='web' AND length(btrim(s.device_id))>0
          AND s.revoked_at IS NULL AND s.expires_at>NEW.created_at
          AND extract(epoch FROM s.created_at)<(NEW.payload_jsonb->>'access_issued_at')::bigint+1
          AND (NEW.payload_jsonb->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
          AND extract(epoch FROM NEW.created_at)<(NEW.payload_jsonb->>'access_expires_at')::bigint)
    THEN RAISE EXCEPTION '0130 current web session required' USING ERRCODE='23514'; END IF;
    IF NEW.action='revoke' THEN
        SELECT * INTO parent FROM public.daily_comparison_mapping_decisions WHERE id=NEW.revoked_grant_id;
        IF parent.id IS NULL OR parent.action<>'grant' OR parent.binding_id<>NEW.binding_id OR parent.catalog_id<>NEW.catalog_id
           OR parent.rules_jsonb IS DISTINCT FROM NEW.rules_jsonb OR parent.created_at>NEW.created_at
           OR request->>'expected_subject_sha256' IS DISTINCT FROM parent.payload_sha256
           OR request->'rules' IS DISTINCT FROM 'null'::jsonb
           OR EXISTS (SELECT 1 FROM public.daily_comparison_mapping_decisions WHERE revoked_grant_id=parent.id)
        THEN RAISE EXCEPTION '0130 exact original mapping required' USING ERRCODE='23514'; END IF;
    ELSE
        IF request->'rules' IS DISTINCT FROM NEW.rules_jsonb
           OR request->>'expected_subject_sha256' IS DISTINCT FROM catalogue.catalog_sha256
           OR NOT EXISTS (SELECT 1 FROM public.source_systems s WHERE s.id=binding.source_system_id AND lower(btrim(s.code))='oam' AND s.enabled AND s.mode='read_only')
           OR NOT EXISTS (SELECT 1 FROM public.organizations o WHERE o.id=binding.region_org_id AND o.status='active' AND o.org_type='region_company')
        THEN RAISE EXCEPTION '0130 reviewed mapping source unavailable' USING ERRCODE='23514'; END IF;
        IF EXISTS (SELECT 1 FROM public.daily_comparison_mapping_decisions g
            LEFT JOIN public.daily_comparison_mapping_decisions revoked ON revoked.revoked_grant_id=g.id
            WHERE g.binding_id=NEW.binding_id AND g.catalog_id=NEW.catalog_id AND g.action='grant'
              AND (LEAST(g.valid_to,revoked.created_at) IS NULL OR LEAST(g.valid_to,revoked.created_at)>NEW.valid_from)
              AND (NEW.valid_to IS NULL OR NEW.valid_to>g.valid_from))
        THEN RAISE EXCEPTION '0130 mapping windows overlap' USING ERRCODE='23514'; END IF;
    END IF;
    expected_audit:=jsonb_build_object('decision_id',NEW.id::text,'action',NEW.action,'binding_id',NEW.binding_id::text,
        'catalog_id',NEW.catalog_id::text,'rules_sha256',NEW.rules_sha256,'payload_sha256',NEW.payload_sha256);
    IF NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
        AND a.actor_user_id=NEW.actor_user_id AND a.action='daily_reconciliation.mapping.'||NEW.action
        AND a.aggregate_type='daily_comparison_mapping_decision' AND a.aggregate_id=NEW.id::text
        AND a.request_id='daily-mapping:'||NEW.id::text AND a.occurred_at=NEW.created_at
        AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=expected_audit)
    THEN RAISE EXCEPTION '0130 mapping audit mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
""",
        "sha256": 'f7648152a740c1700ac6f359c067cdb74f9ffe61dd891e3c66696217c2cf07b4',
    },
    ('rsc_guard_daily_cutoff_0130', ''): {
        "args": '', "result": 'trigger',
        "body": """
DECLARE p jsonb; command jsonb; ledger jsonb; source jsonb; mapping jsonb; report jsonb; facts jsonb;
 mapped public.daily_comparison_mapping_decisions%ROWTYPE;
 pub public.control_projection_publications%ROWTYPE;
 root public.inventory_control_preparations%ROWTYPE;
 head bigint; tx_count bigint; movement_count bigint; accounts jsonb; items jsonb; excluded jsonb;
 admission jsonb; source_grant public.inventory_control_authority_decisions%ROWTYPE;
 catalog_grant public.inventory_control_authority_decisions%ROWTYPE;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'daily cutoff is append-only' USING ERRCODE='23514'; END IF;
 IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
  RAISE EXCEPTION 'daily cutoff direct owner required' USING ERRCODE='42501'; END IF;
 PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    IF NOT EXISTS (
        SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
        JOIN public.organizations o ON o.id=p.organization_id
        JOIN public.role_assignments a ON a.user_id=u.id
        JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id
        JOIN public.permissions permission ON permission.id=rp.permission_id
        WHERE u.id=NEW.actor_user_id AND u.person_id=NEW.actor_person_id AND u.is_active AND u.account_status='active'
          AND u.authorization_version=NEW.actor_authorization_version AND p.employment_status='active'
          AND o.status='active' AND o.org_type='headquarters'
          AND r.code='admin' AND r.status='active' AND NOT r.is_external
          AND a.scope_type='national' AND a.scope_id='*' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=NEW.created_at AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at)
          AND permission.resource='inventory_control' AND permission.action='authorize' AND permission.field_code='' AND rp.effect='allow'
          AND EXISTS (SELECT 1 FROM public.auth_identities identity WHERE identity.user_id=u.id
              AND identity.status='active' AND identity.revoked_at IS NULL AND identity.verified_at<=NEW.created_at)
    ) OR EXISTS (
        SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=NEW.actor_user_id AND r.status='active' AND a.scope_type='national' AND a.scope_id='*'
          AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL AND a.valid_from<=NEW.created_at
          AND (a.valid_to IS NULL OR a.valid_to>NEW.created_at) AND rp.effect='deny'
          AND p.resource='inventory_control' AND p.action='authorize' AND p.field_code=''
    ) THEN RAISE EXCEPTION 'daily-cutoff active headquarters authority required' USING ERRCODE='23514'; END IF;

 p:=NEW.payload_jsonb;command:=p->'command';ledger:=p->'ledger';source:=p->'source';mapping:=p->'mapping';
 report:=p->'comparison'->'comparison';facts:=ledger->'facts';admission:=p->'source_current_authority'->'observation';
 SELECT * INTO STRICT pub FROM public.control_projection_publications WHERE id=NEW.source_publication_id;
 SELECT * INTO STRICT root FROM public.inventory_control_preparations WHERE id=pub.preparation_id;
 PERFORM id FROM public.inventory_control_source_bindings WHERE id=root.binding_id FOR UPDATE;
 SELECT * INTO STRICT mapped FROM public.daily_comparison_mapping_decisions WHERE id=NEW.mapping_decision_id;
 PERFORM public.rsc_validate_daily_mapping_0130(mapped.rules_jsonb,root.binding_id,root.catalog_id);
 LOCK TABLE public.stock_accounts IN SHARE MODE;
 SELECT next_cursor-1 INTO STRICT head FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
 IF NEW.created_at<transaction_timestamp() OR NEW.created_at>clock_timestamp()
  OR mapped.action<>'grant' OR mapped.binding_id<>root.binding_id OR mapped.catalog_id<>root.catalog_id
  OR mapped.valid_from>NEW.source_captured_at OR (mapped.valid_to IS NOT NULL AND mapped.valid_to<=NEW.created_at)
  OR EXISTS(SELECT 1 FROM public.daily_comparison_mapping_decisions WHERE revoked_grant_id=mapped.id)
  OR pub.source_system_id<>NEW.source_system_id OR pub.region_org_id<>NEW.region_org_id
  OR pub.captured_at<>NEW.source_captured_at OR pub.valid_until<=NEW.created_at
  OR head<>NEW.local_ledger_cursor OR (ledger->>'cutoff_cursor')::bigint<>head
  OR (ledger->'ledger_head'->>'next_cursor')::bigint<>head+1
  OR NEW.business_date<>(NEW.source_captured_at AT TIME ZONE 'Asia/Shanghai')::date
  OR NEW.business_date<>(NEW.local_captured_at AT TIME ZONE 'Asia/Shanghai')::date
  OR NEW.local_captured_at-NEW.source_captured_at>interval '300 seconds'
  OR (ledger->'observation'->>'captured_at')::timestamptz IS DISTINCT FROM NEW.local_captured_at
  OR (source->>'captured_at')::timestamptz IS DISTINCT FROM NEW.source_captured_at
  OR source->>'publication_sha256' IS DISTINCT FROM pub.payload_sha256
  OR command->>'source_publication_sha256' IS DISTINCT FROM pub.payload_sha256
  OR command->>'mapping_decision_sha256' IS DISTINCT FROM mapped.payload_sha256
  OR command->>'source_publication_id' IS DISTINCT FROM pub.id::text
  OR command->>'mapping_decision_id' IS DISTINCT FROM mapped.id::text
  OR command->>'business_date' IS DISTINCT FROM NEW.business_date::text
  OR command->>'idempotency_key' IS DISTINCT FROM NEW.idempotency_key
  OR command->>'request_id' IS DISTINCT FROM NEW.request_id
  OR mapping IS DISTINCT FROM mapped.rules_jsonb->'mapping'
  OR p->'mapping_approval'->>'decision_sha256' IS DISTINCT FROM mapped.payload_sha256
 THEN RAISE EXCEPTION 'daily cutoff basis mismatch or expired' USING ERRCODE='23514'; END IF;
 IF p->>'schema' IS DISTINCT FROM 'rsc.daily_cutoff_receipt_candidate.v1' OR p->>'cutoff_id' IS DISTINCT FROM NEW.id::text
  OR p->'actor_snapshot'->>'user_id' IS DISTINCT FROM NEW.actor_user_id
  OR p->'actor_snapshot'->>'person_id' IS DISTINCT FROM NEW.actor_person_id::text
  OR (p->'actor_snapshot'->>'authorization_version')::bigint IS DISTINCT FROM NEW.actor_authorization_version
  OR p->>'auth_session_id' IS DISTINCT FROM NEW.auth_session_id
  OR (p->>'created_at')::timestamptz IS DISTINCT FROM NEW.created_at
  OR p->'daily_reconciliation_approved' IS DISTINCT FROM 'false'::jsonb OR p->'stock_written' IS DISTINCT FROM 'false'::jsonb
  OR NEW.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(jsonb_build_object('actor_user_id',NEW.actor_user_id,'command',command)),'UTF8')),'hex')
  OR NEW.payload_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(p),'UTF8')),'hex')
  OR source->>'content_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(source-'content_sha256'),'UTF8')),'hex')
  OR ledger->>'content_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(ledger-'content_sha256'),'UTF8')),'hex')
  OR p->'comparison'->>'content_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026((p->'comparison')-'content_sha256'),'UTF8')),'hex')
  OR report->>'report_sha256' IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(report-'report_sha256'),'UTF8')),'hex')
  OR p->'comparison'->'input_hashes' IS DISTINCT FROM jsonb_build_object('source',source->>'content_sha256','ledger',ledger->>'content_sha256','mapping',mapping->>'content_sha256')
  OR NOT EXISTS(SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id AND s.user_id=NEW.actor_user_id
   AND s.client_type='web' AND length(btrim(s.device_id))>0 AND s.revoked_at IS NULL AND s.expires_at>NEW.created_at
   AND extract(epoch FROM s.created_at)<(p->>'access_issued_at')::bigint+1
   AND (p->>'access_issued_at')::bigint<=extract(epoch FROM NEW.created_at)
   AND extract(epoch FROM NEW.created_at)<(p->>'access_expires_at')::bigint)
 THEN RAISE EXCEPTION 'daily cutoff payload or session invalid' USING ERRCODE='23514'; END IF;
 SELECT * INTO STRICT source_grant FROM public.inventory_control_authority_decisions WHERE id=(admission->'current_authority'->>'source_grant_id')::uuid;
 SELECT * INTO STRICT catalog_grant FROM public.inventory_control_authority_decisions WHERE id=(admission->'current_authority'->>'catalog_grant_id')::uuid;
 IF source_grant.action<>'source_grant' OR catalog_grant.action<>'catalog_grant' OR catalog_grant.source_grant_id<>source_grant.id
  OR source_grant.binding_id<>root.binding_id OR catalog_grant.binding_id<>root.binding_id OR catalog_grant.catalog_id<>root.catalog_id
  OR source_grant.valid_from>NEW.created_at OR catalog_grant.valid_from>NEW.created_at
  OR source_grant.valid_to<=NEW.created_at OR catalog_grant.valid_to<=NEW.created_at
  OR (admission->>'valid_until')::timestamptz<=NEW.created_at
  OR source_grant.payload_sha256 IS DISTINCT FROM admission->'current_authority'->>'source_grant_sha256'
  OR catalog_grant.payload_sha256 IS DISTINCT FROM admission->'current_authority'->>'catalog_grant_sha256'
  OR EXISTS(SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id IN (source_grant.id,catalog_grant.id))
  OR EXISTS(SELECT 1 FROM public.inventory_control_mapping_decisions WHERE revoked_grant_id=pub.mapping_decision_id)
 THEN RAISE EXCEPTION 'daily cutoff source authority unavailable' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO tx_count FROM public.inventory_transactions WHERE ledger_cursor<=head;
 SELECT count(*) INTO movement_count FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=head;
 IF tx_count<>head OR jsonb_array_length(facts->'inventory_transactions')<>tx_count
  OR jsonb_array_length(facts->'inventory_movements')<>movement_count
  OR (SELECT count(*)<>count(DISTINCT v->>'id') FROM jsonb_array_elements(facts->'inventory_transactions') v)
  OR (SELECT count(*)<>count(DISTINCT v->>'id') FROM jsonb_array_elements(facts->'inventory_movements') v)
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(facts->'inventory_transactions') v WHERE NOT EXISTS(
   SELECT 1 FROM public.inventory_transactions t WHERE t.id::text=v->>'id' AND t.ledger_cursor=(v->>'ledger_cursor')::bigint
    AND t.ledger_cursor<=head AND t.movement_type=v->>'movement_type' AND t.status=v->>'status'
    AND t.reversed_transaction_id::text IS NOT DISTINCT FROM v->>'reversed_transaction_id'
    AND t.effective_at=(v->>'effective_at')::timestamptz AND t.posted_at=(v->>'posted_at')::timestamptz))
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(facts->'inventory_movements') v WHERE NOT EXISTS(
   SELECT 1 FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id
   WHERE m.id::text=v->>'id' AND m.transaction_id::text=v->>'transaction_id' AND t.ledger_cursor<=head
    AND m.line_no=(v->>'line_no')::integer AND m.quantity=(v->>'quantity')::numeric
    AND m.from_account_id::text IS NOT DISTINCT FROM v->>'from_account_id'
    AND m.to_account_id::text IS NOT DISTINCT FROM v->>'to_account_id'
    AND m.external_boundary_code IS NOT DISTINCT FROM v->>'external_boundary_code'))
 THEN RAISE EXCEPTION 'daily cutoff ledger facts mismatch' USING ERRCODE='23514'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]') INTO accounts FROM
  (SELECT id,owner_org_id,custodian_person_id,location_id,material_id,condition_code,availability_bucket,lot_id FROM public.stock_accounts) t;
 IF facts->'stock_accounts' IS DISTINCT FROM accounts
  OR encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(facts->'stock_locations'),'UTF8')),'hex') IS DISTINCT FROM mapping->>'location_facts_sha256'
  OR encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(facts->'organizations'),'UTF8')),'hex') IS DISTINCT FROM mapping->>'organization_facts_sha256'
 THEN RAISE EXCEPTION 'daily cutoff reference facts mismatch' USING ERRCODE='23514'; END IF;
 -- Independently compare totals from actual immutable source origins and
 -- actual local movement prefix. Do not trust the submitted report arithmetic.
 WITH amounts AS (
  SELECT m.from_account_id AS id,-m.quantity AS quantity FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=head AND m.from_account_id IS NOT NULL
  UNION ALL SELECT m.to_account_id,m.quantity FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=head AND m.to_account_id IS NOT NULL
 ), local_totals AS (
  SELECT l->>'warehouse_code' AS warehouse,a.material_id,a.condition_code,sum(v.quantity) AS quantity
  FROM amounts v JOIN public.stock_accounts a ON a.id=v.id JOIN jsonb_array_elements(mapping->'location_bindings') l ON l->>'location_id'=a.location_id::text
  WHERE l->>'region_id'=NEW.region_org_id::text AND mapping->'included_buckets' @> to_jsonb(ARRAY[a.availability_bucket])
  GROUP BY 1,2,3
 ), external_totals AS (
  SELECT o.payload_jsonb->'origin'->>'warehouse_code' AS warehouse,l.material_id,l.condition_code,sum((o.payload_jsonb->'origin'->>'control_qty')::numeric) AS quantity
  FROM public.control_projection_origins o JOIN public.control_projection_lines l ON l.id=o.line_id WHERE o.publication_id=pub.id GROUP BY 1,2,3
 ), compared AS (
  SELECT COALESCE(e.warehouse,l.warehouse) AS warehouse,COALESCE(e.material_id,l.material_id) AS material_id,COALESCE(e.condition_code,l.condition_code) AS condition,
   COALESCE(e.quantity,0) AS external_qty,COALESCE(l.quantity,0) AS local_qty
  FROM external_totals e FULL JOIN local_totals l USING(warehouse,material_id,condition_code)
 ) SELECT COALESCE(jsonb_agg(jsonb_build_object('warehouse_code',warehouse,'material_id',material_id::text,'condition',condition,
  'external_qty',external_qty::numeric(18,3)::text,'local_qty',local_qty::numeric(18,3)::text,
  'difference',(external_qty-local_qty)::numeric(18,3)::text,'status',CASE WHEN external_qty=local_qty THEN 'matched' ELSE 'difference' END)
  ORDER BY warehouse,material_id,condition),'[]') INTO items FROM compared;
 IF report->'items' IS DISTINCT FROM items
  OR report->>'status' IS DISTINCT FROM (CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(items) i WHERE i->>'status'='difference') THEN 'differences' ELSE 'matched' END)
  OR (report->>'local_ledger_cursor')::bigint IS DISTINCT FROM head
  OR report->>'publication_id' IS DISTINCT FROM pub.id::text
  OR report->>'source_system_id' IS DISTINCT FROM NEW.source_system_id::text
  OR report->>'region_id' IS DISTINCT FROM NEW.region_org_id::text
  OR report->>'publication_sha256' IS DISTINCT FROM pub.payload_sha256
  OR report->>'catalog_sha256' IS DISTINCT FROM mapping->>'catalog_sha256'
  OR report->>'mapping_version_id' IS DISTINCT FROM mapping->>'version_id'
  OR report->>'mapping_sha256' IS DISTINCT FROM mapping->>'content_sha256'
  OR (report->>'external_snapshot_at')::timestamptz IS DISTINCT FROM NEW.source_captured_at
  OR (report->>'local_snapshot_at')::timestamptz IS DISTINCT FROM NEW.local_captured_at
 THEN RAISE EXCEPTION 'daily cutoff comparison mismatch' USING ERRCODE='23514'; END IF;
 WITH amounts AS (
  SELECT m.from_account_id AS id,-m.quantity AS quantity FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=head AND m.from_account_id IS NOT NULL
  UNION ALL SELECT m.to_account_id,m.quantity FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE t.ledger_cursor<=head AND m.to_account_id IS NOT NULL
 ), excluded_totals AS (
  SELECT l->>'warehouse_code' AS warehouse,a.material_id,a.condition_code,a.availability_bucket,sum(v.quantity) AS quantity
  FROM amounts v JOIN public.stock_accounts a ON a.id=v.id JOIN jsonb_array_elements(mapping->'location_bindings') l ON l->>'location_id'=a.location_id::text
  WHERE l->>'region_id'=NEW.region_org_id::text AND NOT(mapping->'included_buckets' @> to_jsonb(ARRAY[a.availability_bucket]))
  GROUP BY 1,2,3,4
 ) SELECT COALESCE(jsonb_agg(jsonb_build_object('warehouse_code',warehouse,'material_id',material_id::text,'condition',condition_code,
  'bucket',availability_bucket,'quantity',quantity::numeric(18,3)::text) ORDER BY warehouse,material_id,condition_code,availability_bucket),'[]') INTO excluded FROM excluded_totals;
 IF report->'excluded_local_quantities' IS DISTINCT FROM excluded THEN RAISE EXCEPTION 'daily cutoff excluded quantities mismatch' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
  AND a.action='daily_reconciliation.capture' AND a.aggregate_type='daily_reconciliation_cutoff' AND a.aggregate_id=NEW.id::text
  AND a.actor_user_id=NEW.actor_user_id AND a.request_id='daily-cutoff:'||NEW.id::text AND a.occurred_at=NEW.created_at
  AND a.before_jsonb='{}'::jsonb AND a.after_jsonb=jsonb_build_object('cutoff_id',NEW.id::text,
   'source_publication_id',pub.id::text,'mapping_decision_id',mapped.id::text,'business_date',NEW.business_date::text,
   'local_ledger_cursor',head,'payload_sha256',NEW.payload_sha256))
 THEN RAISE EXCEPTION 'daily cutoff audit mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END;
""",
        "sha256": 'baa260ca3ef056afa6427d57af0235047d11ba7f8a0eecce4910bec558e42182',
    },
    ('rsc_guard_daily_commit_0130', ''): {
        "args": '', "result": 'trigger',
        "body": """
DECLARE at_time timestamptz := clock_timestamp(); p jsonb := NEW.payload_jsonb;
 mapped public.daily_comparison_mapping_decisions%ROWTYPE;
 pub public.control_projection_publications%ROWTYPE; grant_id uuid;
BEGIN
 IF TG_OP<>'INSERT' OR current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
  RAISE EXCEPTION '0130 direct owner insert required' USING ERRCODE='42501'; END IF;
 -- Original INSERT locks this graph. Time still advances while locks are held.
 -- Recheck the current instant independently of caller-supplied created_at.
 IF NOT EXISTS (
  SELECT 1 FROM public.users u JOIN public.people person ON person.id=u.person_id
  JOIN public.organizations o ON o.id=person.organization_id
  JOIN public.role_assignments a ON a.user_id=u.id JOIN public.roles r ON r.id=a.role_id
  JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions permission ON permission.id=rp.permission_id
  WHERE u.id=NEW.actor_user_id AND u.person_id=NEW.actor_person_id AND u.is_active AND u.account_status='active'
   AND u.authorization_version=NEW.actor_authorization_version AND person.employment_status='active'
   AND o.status='active' AND o.org_type='headquarters' AND r.code='admin' AND r.status='active' AND NOT r.is_external
   AND a.scope_type='national' AND a.scope_id='*' AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
   AND a.valid_from<=at_time AND (a.valid_to IS NULL OR a.valid_to>at_time)
   AND permission.resource='inventory_control' AND permission.action='authorize' AND permission.field_code='' AND rp.effect='allow'
   AND EXISTS(SELECT 1 FROM public.auth_identities identity WHERE identity.user_id=u.id AND identity.status='active'
    AND identity.revoked_at IS NULL AND identity.verified_at<=at_time)
 ) OR EXISTS (
  SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
  JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions permission ON permission.id=rp.permission_id
  WHERE a.user_id=NEW.actor_user_id AND r.status='active' AND a.scope_type='national' AND a.scope_id='*'
   AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL AND a.valid_from<=at_time
   AND (a.valid_to IS NULL OR a.valid_to>at_time) AND rp.effect='deny'
   AND permission.resource='inventory_control' AND permission.action='authorize' AND permission.field_code=''
 ) THEN RAISE EXCEPTION '0130 daily actor expired or denied at commit' USING ERRCODE='23514'; END IF;
 IF COALESCE(p->>'access_issued_at','')!~'^[0-9]+$' OR COALESCE(p->>'access_expires_at','')!~'^[0-9]+$'
  OR NOT EXISTS(SELECT 1 FROM public.auth_sessions s WHERE s.id=NEW.auth_session_id AND s.user_id=NEW.actor_user_id
   AND s.client_type='web' AND length(btrim(s.device_id))>0 AND s.revoked_at IS NULL AND s.expires_at>at_time
   AND extract(epoch FROM s.created_at)<(p->>'access_issued_at')::bigint+1
   AND (p->>'access_issued_at')::bigint<=extract(epoch FROM at_time)
   AND extract(epoch FROM at_time)<(p->>'access_expires_at')::bigint)
 THEN RAISE EXCEPTION '0130 daily session expired at commit' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='daily_comparison_mapping_decisions' THEN
  IF NEW.action='grant' AND NEW.valid_to IS NOT NULL AND NEW.valid_to<=at_time THEN
   RAISE EXCEPTION '0130 daily mapping expired at commit' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='daily_reconciliation_cutoffs' THEN
  SELECT * INTO STRICT mapped FROM public.daily_comparison_mapping_decisions WHERE id=NEW.mapping_decision_id;
  SELECT * INTO STRICT pub FROM public.control_projection_publications WHERE id=NEW.source_publication_id;
  IF mapped.valid_from>at_time OR (mapped.valid_to IS NOT NULL AND mapped.valid_to<=at_time)
   OR EXISTS(SELECT 1 FROM public.daily_comparison_mapping_decisions WHERE revoked_grant_id=mapped.id)
   OR pub.valid_until<=at_time OR (p->'source_current_authority'->'observation'->>'valid_until')::timestamptz<=at_time
   OR EXISTS(SELECT 1 FROM public.inventory_control_mapping_decisions WHERE revoked_grant_id=pub.mapping_decision_id)
  THEN RAISE EXCEPTION '0130 daily source or mapping expired at commit' USING ERRCODE='23514'; END IF;
  FOREACH grant_id IN ARRAY ARRAY[(p->'source_current_authority'->'observation'->'current_authority'->>'source_grant_id')::uuid,
    (p->'source_current_authority'->'observation'->'current_authority'->>'catalog_grant_id')::uuid]
  LOOP
   IF NOT EXISTS(SELECT 1 FROM public.inventory_control_authority_decisions g WHERE g.id=grant_id
    AND g.valid_from<=at_time AND (g.valid_to IS NULL OR g.valid_to>at_time))
    OR EXISTS(SELECT 1 FROM public.inventory_control_authority_decisions WHERE revoked_grant_id=grant_id)
   THEN RAISE EXCEPTION '0130 daily source authority expired at commit' USING ERRCODE='23514'; END IF;
  END LOOP;
 ELSE RAISE EXCEPTION '0130 unexpected daily commit table' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END
""",
        "sha256": 'e7143e16364a0c2d8e3bd9b6be3a3a0d6af12527ca384d793a73614718aeaf3f',
    },
}
TRIGGERS = {'trg_daily_mapping_facts_0130': ('daily_comparison_mapping_decisions',
                                  'rsc_guard_daily_mapping_0130',
                                  'INSERT OR UPDATE OR DELETE',
                                  31,
                                  False),
 'trg_daily_mapping_truncate_0130': ('daily_comparison_mapping_decisions',
                                     'rsc_guard_daily_mapping_0130',
                                     'TRUNCATE',
                                     34,
                                     False),
 'trg_daily_mapping_commit_0130': ('daily_comparison_mapping_decisions',
                                   'rsc_guard_daily_commit_0130',
                                   'INSERT',
                                   5,
                                   True),
 'trg_daily_cutoff_facts_0130': ('daily_reconciliation_cutoffs',
                                 'rsc_guard_daily_cutoff_0130',
                                 'INSERT OR UPDATE OR DELETE',
                                 31,
                                 False),
 'trg_daily_cutoff_truncate_0130': ('daily_reconciliation_cutoffs',
                                    'rsc_guard_daily_cutoff_0130',
                                    'TRUNCATE',
                                    34,
                                    False),
 'trg_daily_cutoff_commit_0130': ('daily_reconciliation_cutoffs',
                                  'rsc_guard_daily_commit_0130',
                                  'INSERT',
                                  5,
                                  True)}


def _verify():
    for (name,types),f in FUNCTIONS.items():
        op.execute(f"""DO $verify_0130$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p
          WHERE p.oid='public.{name}({types})'::regprocedure AND p.prokind='f'
           AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
           AND p.prorettype='{f['result']}'::regtype AND NOT p.proretset AND NOT p.prosecdef
           AND NOT p.proisstrict AND NOT p.proleakproof AND p.provolatile='v' AND p.proparallel='u'
           AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
           AND p.proargmodes IS NULL AND p.pronargdefaults=0
           AND p.proconfig=ARRAY['search_path=pg_catalog, public']
           AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{f['sha256']}'
           AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
          THEN RAISE EXCEPTION '0130 daily function catalog drift'; END IF; END $verify_0130$""")
    for name,(table,function,events,kind,deferred) in TRIGGERS.items():
        flag=str(deferred).lower()
        op.execute(f"""DO $verify_0130$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger t
          WHERE t.tgrelid='public.{table}'::regclass AND t.tgname='{name}' AND t.tgtype={kind}
           AND t.tgfoid='public.{function}()'::regprocedure AND t.tgenabled='A' AND NOT t.tgisinternal
           AND t.tgdeferrable={flag} AND t.tginitdeferred={flag} AND (t.tgconstraint<>0)={flag}
           AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgattr=''::int2vector)
          THEN RAISE EXCEPTION '0130 daily trigger catalog drift'; END IF; END $verify_0130$""")
    for table in TABLES:
        op.execute(f"""DO $acl_0130$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_class c
          WHERE c.oid='public.{table}'::regclass AND c.relkind='r' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
           AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
           AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a LEFT JOIN pg_roles r ON r.oid=a.grantee
            WHERE a.grantee<>c.relowner AND NOT(r.rolname='star_oam_backup' AND a.privilege_type='SELECT' AND NOT a.is_grantable))
           AND NOT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attacl IS NOT NULL))
          THEN RAISE EXCEPTION '0130 daily table ACL drift'; END IF; END $acl_0130$""")

def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in DDL:raise RuntimeError('0130 PostgreSQL or SQLite required')
    folder=Path(__file__).parent
    helper=runpy.run_path(str(folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $owner_0130$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0130 direct schema owner required'; END IF; END $owner_0130$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
    if up:
        for sql in DDL[dialect]:op.execute(sql)
    else:
        if dialect=='postgresql':op.execute('LOCK TABLE public.daily_comparison_mapping_decisions, public.daily_reconciliation_cutoffs IN ACCESS EXCLUSIVE MODE')
        helper['_preflight']("EXISTS(SELECT 1 FROM daily_comparison_mapping_decisions) OR EXISTS(SELECT 1 FROM daily_reconciliation_cutoffs) OR EXISTS(SELECT 1 FROM audit_events WHERE action LIKE 'daily_reconciliation.%')",'0130 downgrade blocked: daily facts and audit must be retained')
    if dialect=='postgresql':
        if up:
            for (name,types),f in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({f['args']}) RETURNS {f['result']} LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog, public AS $body$"+f['body']+'$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({types}) FROM PUBLIC')
            for table in TABLES:
                op.execute(f'REVOKE ALL ON TABLE public.{table} FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox')
                op.execute(f'GRANT SELECT ON TABLE public.{table} TO star_oam_backup')
            for name,(table,function,events,kind,deferred) in TRIGGERS.items():
                if deferred:sql=f'CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()'
                else:sql=f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'} EXECUTE FUNCTION public.{function}()"
                op.execute(sql);op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            _verify()
        else:
            _verify()
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,types in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({types})')
        replace=runpy.run_path(str(folder/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
            replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='daily_cutoff_readiness_0130')
    elif up:
        for table in TABLES:
            for event in ['UPDATE','DELETE']:
                op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0130 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0130 daily facts are append-only'); END")
    if not up:
        for table in reversed(TABLES):op.drop_table(table)

def upgrade():_transition(True)
def downgrade():_transition(False)
