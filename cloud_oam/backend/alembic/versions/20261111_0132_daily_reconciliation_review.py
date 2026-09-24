"""Immutable daily review commands with atomic generic projections and audit.

Frozen 8.07 native-PG16-verified rules. Existing opening branches remain exact;
populated downgrade refuses before changing facts. No inventory posting.
"""
import hashlib
from pathlib import Path
from types import FunctionType,SimpleNamespace
import runpy
import uuid
from datetime import datetime,timezone
import sqlalchemy as sa
from alembic import op
revision='20261111_0132'
down_revision='20261110_0131'
branch_labels=depends_on=None
OLD_HASH='c25cc7581169076ad39752300007a3dcc651daef9337b37dad1c0d1fc88441d5'
NEW_HASH='5779bf72dc3add0e92087e77b33576333a0ad48b23a801ca8ba6bcf7fca70ec1'
DDL = {'postgresql': ['\n'
                'CREATE TABLE daily_review_events (\n'
                '\tid UUID NOT NULL, \n'
                '\trun_id UUID NOT NULL, \n'
                '\tcutoff_id UUID NOT NULL, \n'
                '\tversion BIGINT NOT NULL, \n'
                '\tactor_user_id VARCHAR(36) NOT NULL, \n'
                '\tactor_person_id UUID NOT NULL, \n'
                '\tauth_session_id VARCHAR(36) NOT NULL, \n'
                '\taccess_issued_at BIGINT NOT NULL, \n'
                '\taccess_expires_at BIGINT NOT NULL, \n'
                '\tidempotency_key VARCHAR(128) NOT NULL, \n'
                '\trequest_id VARCHAR(160) NOT NULL, \n'
                '\tpayload_jsonb JSONB NOT NULL, \n'
                '\tafter_state_jsonb JSONB NOT NULL, \n'
                '\treceipt_jsonb JSONB NOT NULL, \n'
                '\tpayload_sha256 VARCHAR(64) NOT NULL, \n'
                '\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
                '\ttransaction_id BIGINT NOT NULL, \n'
                '\tPRIMARY KEY (id), \n'
                '\tCONSTRAINT ck_daily_review_event_version CHECK (version>0), \n'
                '\tCONSTRAINT ck_daily_review_event_hash CHECK (length(payload_sha256)=64), \n'
                '\tCONSTRAINT ck_daily_review_event_token_time CHECK (access_issued_at>0 AND '
                'access_expires_at>access_issued_at), \n'
                '\tCONSTRAINT uq_daily_review_event_version UNIQUE (run_id, version), \n'
                '\tCONSTRAINT uq_daily_review_actor_key UNIQUE (actor_user_id, idempotency_key), \n'
                '\tCONSTRAINT uq_daily_review_actor_request UNIQUE (actor_user_id, request_id), \n'
                '\tFOREIGN KEY(cutoff_id) REFERENCES daily_reconciliation_cutoffs (id), \n'
                '\tFOREIGN KEY(actor_user_id) REFERENCES users (id), \n'
                '\tFOREIGN KEY(actor_person_id) REFERENCES people (id), \n'
                '\tFOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id)\n'
                ')\n'
                '\n',
                '\n'
                'CREATE TABLE daily_review_bindings (\n'
                '\trun_id UUID NOT NULL, \n'
                '\tcutoff_id UUID NOT NULL, \n'
                '\tversion BIGINT NOT NULL, \n'
                '\tlast_event_id UUID NOT NULL, \n'
                '\titem_ids JSONB NOT NULL, \n'
                '\tstate_jsonb JSONB NOT NULL, \n'
                '\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
                '\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n'
                '\tPRIMARY KEY (run_id), \n'
                '\tCONSTRAINT ck_daily_review_binding_version CHECK (version>0), \n'
                '\tFOREIGN KEY(run_id) REFERENCES reconciliation_runs (id) DEFERRABLE INITIALLY DEFERRED, \n'
                '\tUNIQUE (cutoff_id), \n'
                '\tFOREIGN KEY(cutoff_id) REFERENCES daily_reconciliation_cutoffs (id), \n'
                '\tUNIQUE (last_event_id), \n'
                '\tFOREIGN KEY(last_event_id) REFERENCES daily_review_events (id)\n'
                ')\n'
                '\n',
                '\n'
                'CREATE TABLE daily_review_consumptions (\n'
                '\tevent_id UUID NOT NULL, \n'
                '\taudit_event_id UUID NOT NULL, \n'
                '\ttransition_event_id UUID, \n'
                '\ttransaction_id BIGINT NOT NULL, \n'
                '\tPRIMARY KEY (event_id), \n'
                '\tFOREIGN KEY(event_id) REFERENCES daily_review_events (id), \n'
                '\tUNIQUE (audit_event_id), \n'
                '\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n'
                '\tUNIQUE (transition_event_id), \n'
                '\tFOREIGN KEY(transition_event_id) REFERENCES state_transition_events (id)\n'
                ')\n'
                '\n',
                'ALTER TABLE daily_review_events ADD CONSTRAINT fk_daily_review_event_consumed FOREIGN '
                'KEY(id) REFERENCES daily_review_consumptions (event_id) DEFERRABLE INITIALLY DEFERRED',
                'ALTER TABLE daily_review_events ADD CONSTRAINT fk_daily_review_event_run FOREIGN '
                'KEY(run_id) REFERENCES daily_review_bindings (run_id) DEFERRABLE INITIALLY DEFERRED'],
 'sqlite': ['\n'
            'CREATE TABLE daily_review_events (\n'
            '\tid CHAR(32) NOT NULL, \n'
            '\trun_id CHAR(32) NOT NULL, \n'
            '\tcutoff_id CHAR(32) NOT NULL, \n'
            '\tversion BIGINT NOT NULL, \n'
            '\tactor_user_id VARCHAR(36) NOT NULL, \n'
            '\tactor_person_id CHAR(32) NOT NULL, \n'
            '\tauth_session_id VARCHAR(36) NOT NULL, \n'
            '\taccess_issued_at BIGINT NOT NULL, \n'
            '\taccess_expires_at BIGINT NOT NULL, \n'
            '\tidempotency_key VARCHAR(128) NOT NULL, \n'
            '\trequest_id VARCHAR(160) NOT NULL, \n'
            '\tpayload_jsonb JSON NOT NULL, \n'
            '\tafter_state_jsonb JSON NOT NULL, \n'
            '\treceipt_jsonb JSON NOT NULL, \n'
            '\tpayload_sha256 VARCHAR(64) NOT NULL, \n'
            '\tcreated_at DATETIME NOT NULL, \n'
            '\ttransaction_id BIGINT NOT NULL, \n'
            '\tPRIMARY KEY (id),\n'
            '\tCONSTRAINT fk_daily_review_event_run FOREIGN KEY(run_id) REFERENCES daily_review_bindings '
            '(run_id) DEFERRABLE INITIALLY DEFERRED,\n'
            '\tCONSTRAINT fk_daily_review_event_consumed FOREIGN KEY(id) REFERENCES '
            'daily_review_consumptions (event_id) DEFERRABLE INITIALLY DEFERRED, \n'
            '\tCONSTRAINT ck_daily_review_event_version CHECK (version>0), \n'
            '\tCONSTRAINT ck_daily_review_event_hash CHECK (length(payload_sha256)=64), \n'
            '\tCONSTRAINT ck_daily_review_event_token_time CHECK (access_issued_at>0 AND '
            'access_expires_at>access_issued_at), \n'
            '\tCONSTRAINT uq_daily_review_event_version UNIQUE (run_id, version), \n'
            '\tCONSTRAINT uq_daily_review_actor_key UNIQUE (actor_user_id, idempotency_key), \n'
            '\tCONSTRAINT uq_daily_review_actor_request UNIQUE (actor_user_id, request_id), \n'
            '\tFOREIGN KEY(cutoff_id) REFERENCES daily_reconciliation_cutoffs (id), \n'
            '\tFOREIGN KEY(actor_user_id) REFERENCES users (id), \n'
            '\tFOREIGN KEY(actor_person_id) REFERENCES people (id), \n'
            '\tFOREIGN KEY(auth_session_id) REFERENCES auth_sessions (id)\n'
            ')\n'
            '\n',
            '\n'
            'CREATE TABLE daily_review_bindings (\n'
            '\trun_id CHAR(32) NOT NULL, \n'
            '\tcutoff_id CHAR(32) NOT NULL, \n'
            '\tversion BIGINT NOT NULL, \n'
            '\tlast_event_id CHAR(32) NOT NULL, \n'
            '\titem_ids JSON NOT NULL, \n'
            '\tstate_jsonb JSON NOT NULL, \n'
            '\tcreated_at DATETIME NOT NULL, \n'
            '\tupdated_at DATETIME NOT NULL, \n'
            '\tPRIMARY KEY (run_id), \n'
            '\tCONSTRAINT ck_daily_review_binding_version CHECK (version>0), \n'
            '\tFOREIGN KEY(run_id) REFERENCES reconciliation_runs (id) DEFERRABLE INITIALLY DEFERRED, \n'
            '\tUNIQUE (cutoff_id), \n'
            '\tFOREIGN KEY(cutoff_id) REFERENCES daily_reconciliation_cutoffs (id), \n'
            '\tUNIQUE (last_event_id), \n'
            '\tFOREIGN KEY(last_event_id) REFERENCES daily_review_events (id)\n'
            ')\n'
            '\n',
            '\n'
            'CREATE TABLE daily_review_consumptions (\n'
            '\tevent_id CHAR(32) NOT NULL, \n'
            '\taudit_event_id CHAR(32) NOT NULL, \n'
            '\ttransition_event_id CHAR(32), \n'
            '\ttransaction_id BIGINT NOT NULL, \n'
            '\tPRIMARY KEY (event_id), \n'
            '\tFOREIGN KEY(event_id) REFERENCES daily_review_events (id), \n'
            '\tUNIQUE (audit_event_id), \n'
            '\tFOREIGN KEY(audit_event_id) REFERENCES audit_events (id), \n'
            '\tUNIQUE (transition_event_id), \n'
            '\tFOREIGN KEY(transition_event_id) REFERENCES state_transition_events (id)\n'
            ')\n'
            '\n']}
TABLES = ('daily_review_events', 'daily_review_bindings', 'daily_review_consumptions')
FUNCTIONS = {}
def function(name,args,result,body,api=False):
    FUNCTIONS[name]=(args,result,body,api)

function('rsc_daily_review_hash_0132','value jsonb','text',"""
BEGIN RETURN encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(value),'UTF8')),'hex'); END
""",api=False)

function('rsc_daily_review_snapshot_0132','cutoff uuid','jsonb',"""
DECLARE c public.daily_reconciliation_cutoffs%ROWTYPE; stamp text;
BEGIN
 SELECT * INTO STRICT c FROM public.daily_reconciliation_cutoffs WHERE id=cutoff;
 stamp:=to_char(c.source_captured_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS');
 IF extract(microseconds FROM c.source_captured_at)::bigint%1000000<>0 THEN
  stamp:=stamp||'.'||to_char(c.source_captured_at AT TIME ZONE 'UTC','US'); END IF;
 RETURN jsonb_build_object('cutoff_id',c.id,'cutoff_sha256',c.payload_sha256,
  'comparison_sha256',c.payload_jsonb#>>'{comparison,comparison,report_sha256}',
  'source_system_id',c.source_system_id,'region_org_id',c.region_org_id,'business_date',c.business_date,
  'external_snapshot_at',stamp||'+00:00','local_ledger_cursor',c.local_ledger_cursor,
  'comparison_status',c.payload_jsonb#>>'{comparison,comparison,status}',
  'items',c.payload_jsonb#>'{comparison,comparison,items}');
END
""",api=False)

function('rsc_daily_review_live_0132','e public.daily_review_events, at_time timestamptz','void',"""
DECLARE a jsonb:=e.payload_jsonb->'actor'; op text:=e.payload_jsonb#>>'{request,operation}';
 action_name text; region uuid; f jsonb; grant_ids uuid[];
BEGIN
 SELECT region_org_id INTO STRICT region FROM public.daily_reconciliation_cutoffs WHERE id=e.cutoff_id;
 action_name:=CASE op WHEN 'open' THEN 'create_daily' WHEN 'explain' THEN 'explain_daily'
   WHEN 'approve' THEN 'approve_daily' WHEN 'request_changes' THEN 'approve_daily' ELSE NULL END;
 IF action_name IS NULL OR NOT EXISTS (
  SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
   JOIN public.organizations o ON o.id=p.organization_id
   JOIN public.role_assignments g ON g.user_id=u.id JOIN public.roles r ON r.id=g.role_id
  WHERE u.id=e.actor_user_id AND u.person_id=e.actor_person_id AND u.is_active AND u.account_status='active'
   AND p.employment_status='active' AND o.status='active' AND u.authorization_version=(a->>'authorization_version')::bigint
   AND g.id=(a->>'assignment_id')::uuid AND r.status='active' AND NOT r.is_external
   AND g.status IN ('active','scheduled') AND g.revoked_at IS NULL AND g.valid_from<=at_time AND (g.valid_to IS NULL OR g.valid_to>at_time)
   AND r.code=a->>'role_code' AND g.scope_type=a->>'scope_type' AND g.scope_id=a->>'scope_id'
   AND g.valid_from=(a->>'valid_from')::timestamptz AND g.valid_to IS NOT DISTINCT FROM (a->>'valid_to')::timestamptz
   AND ((r.code='admin' AND g.scope_type='national' AND g.scope_id='*' AND op<>'explain' AND o.org_type='headquarters')
    OR (r.code='provincial_manager' AND g.scope_type='organization' AND g.scope_id=region::text AND op IN ('open','explain')))
   AND EXISTS(SELECT 1 FROM public.auth_identities i WHERE i.user_id=u.id AND i.status='active' AND i.revoked_at IS NULL AND i.verified_at<=at_time)
   AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['read',action_name]) needed WHERE NOT EXISTS(
     SELECT 1 FROM public.role_permissions rp JOIN public.permissions pm ON pm.id=rp.permission_id
     WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='reconciliation' AND pm.action=needed AND pm.field_code=''))
 ) OR EXISTS (
  SELECT 1 FROM public.role_assignments g JOIN public.roles r ON r.id=g.role_id
   JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions pm ON pm.id=rp.permission_id
  WHERE g.user_id=e.actor_user_id AND g.status IN ('active','scheduled') AND g.revoked_at IS NULL
   AND g.valid_from<=at_time AND (g.valid_to IS NULL OR g.valid_to>at_time) AND r.status='active'
   AND rp.effect='deny' AND pm.resource='reconciliation' AND pm.action IN ('read',action_name)
 ) THEN RAISE EXCEPTION 'daily review current authority denied' USING ERRCODE='23514'; END IF;
 WITH eligible AS (
  SELECT g.id,r.code FROM public.role_assignments g JOIN public.roles r ON r.id=g.role_id
  WHERE g.user_id=e.actor_user_id AND g.status IN ('active','scheduled') AND g.revoked_at IS NULL
   AND r.status='active' AND NOT r.is_external AND g.valid_from<=at_time AND (g.valid_to IS NULL OR g.valid_to>at_time)
   AND ((r.code='admin' AND g.scope_type='national' AND g.scope_id='*' AND op<>'explain')
    OR (r.code='provincial_manager' AND g.scope_type='organization' AND g.scope_id=region::text AND op IN ('open','explain')))
   AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['read',action_name]) needed WHERE NOT EXISTS(
    SELECT 1 FROM public.role_permissions rp JOIN public.permissions pm ON pm.id=rp.permission_id
    WHERE rp.role_id=r.id AND rp.effect='allow' AND pm.resource='reconciliation' AND pm.action=needed AND pm.field_code=''))
 ) SELECT array_agg(id ORDER BY id) INTO grant_ids FROM eligible
   WHERE op<>'open' OR code='admin' OR NOT EXISTS(SELECT 1 FROM eligible WHERE code='admin');
 IF grant_ids IS DISTINCT FROM ARRAY[(a->>'assignment_id')::uuid]
 THEN RAISE EXCEPTION 'daily review exact grant ambiguous or changed' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.auth_sessions s WHERE s.id=e.auth_session_id AND s.user_id=e.actor_user_id
  AND s.revoked_at IS NULL AND s.client_type='web' AND length(btrim(s.device_id))>0 AND s.expires_at>at_time
  AND s.ip_address ~ '^hmac:[1-9][0-9]*:[a-f0-9]{64}$'
  AND extract(epoch FROM s.created_at)<e.access_issued_at+1 AND e.access_issued_at<=extract(epoch FROM at_time)
  AND extract(epoch FROM at_time)<e.access_expires_at)
 THEN RAISE EXCEPTION 'daily review session expired' USING ERRCODE='23514'; END IF;
 FOR f IN SELECT value FROM jsonb_array_elements(e.payload_jsonb->'evidence') LOOP
  IF NOT EXISTS(SELECT 1 FROM public.files x WHERE x.id=(f->>'file_id')::uuid AND x.status='available'
   AND x.sha256=f->>'sha256' AND to_jsonb(x.size_bytes)=f->'size_bytes' AND x.size_bytes>0 AND x.mime_type=f->>'mime_type'
   AND f=jsonb_build_object('file_id',x.id,'sha256',x.sha256,'size_bytes',x.size_bytes,'mime_type',x.mime_type,'status','available','accessible',true)
   AND (op<>'explain' OR x.uploaded_by=e.actor_user_id))
  THEN RAISE EXCEPTION 'daily review evidence unavailable or not owned' USING ERRCODE='23514'; END IF;
 END LOOP;
END
""",api=False)

function('rsc_daily_review_step_0132','snap jsonb, prior jsonb, e jsonb','jsonb',"""
DECLARE q jsonb:=e->'request'; op text:=q->>'operation'; items jsonb:=prior->'items';
 authors jsonb:=prior->'explanation_authors'; a jsonb:=e->'actor'; row jsonb; old_item jsonb;
 value_item jsonb; originals jsonb:=snap->'items'; n integer; i integer; expected_files jsonb; current_files jsonb;
 status text; allowed text[]; seen integer[]:=ARRAY[]::integer[];
BEGIN
 allowed:=ARRAY['operation','cutoff_id','expected_cutoff_sha256','expected_comparison_sha256','expected_version','idempotency_key','request_id'];
 IF op='explain' THEN allowed:=allowed||ARRAY['items'];
 ELSIF op IN ('approve','request_changes') THEN allowed:=allowed||ARRAY['comment','ordinals'];
 ELSIF op<>'open' OR op IS NULL THEN RAISE EXCEPTION 'daily review unknown command'; END IF;
 IF jsonb_typeof(q)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(q))<>cardinality(allowed)
  OR EXISTS(SELECT 1 FROM jsonb_object_keys(q) key WHERE NOT key=ANY(allowed))
  OR q->'expected_version' IS DISTINCT FROM prior->'version'
  OR q->'cutoff_id' IS DISTINCT FROM snap->'cutoff_id'
  OR q->'expected_cutoff_sha256' IS DISTINCT FROM snap->'cutoff_sha256'
  OR q->'expected_comparison_sha256' IS DISTINCT FROM snap->'comparison_sha256'
  OR COALESCE(q->>'idempotency_key','')!~'^[A-Za-z0-9._:-]{16,128}$'
  OR COALESCE(q->>'request_id','')!~'^[A-Za-z0-9._:-]{8,160}$'
  OR prior->>'review_status'='approved'
  OR jsonb_typeof(e->'evidence')<>'array'
  OR (SELECT count(*) FROM jsonb_array_elements(e->'evidence'))<>(SELECT count(DISTINCT value->>'file_id') FROM jsonb_array_elements(e->'evidence'))
 THEN RAISE EXCEPTION 'daily review request shape, version or binding invalid' USING ERRCODE='23514'; END IF;
 IF op='open' THEN
  IF prior->>'version'<>'0' OR e->'evidence'<>'[]'::jsonb THEN RAISE EXCEPTION 'daily review already open'; END IF;
 ELSE
  IF prior->>'version'='0' THEN RAISE EXCEPTION 'daily review not open'; END IF;
  IF op='explain' THEN
   IF jsonb_typeof(q->'items')<>'array' OR jsonb_array_length(q->'items') NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'daily review explanation list invalid'; END IF;
   FOR row IN SELECT value FROM jsonb_array_elements(q->'items') LOOP
    IF (SELECT count(*) FROM jsonb_object_keys(row))<>5 OR NOT row ?& ARRAY['ordinal','expected_item_version','explanation','evidence_file_id','evidence_sha256']
      OR COALESCE(row->>'ordinal','')!~'^[1-9][0-9]*$' OR jsonb_typeof(row->'ordinal')<>'number'
      OR char_length(row->>'explanation') NOT BETWEEN 4 AND 2000 OR jsonb_typeof(row->'explanation')<>'string'
      OR row->>'explanation'<>btrim(row->>'explanation') OR row->>'explanation'~'[[:cntrl:]]'
    THEN RAISE EXCEPTION 'daily review explanation shape invalid'; END IF;
    n:=(row->>'ordinal')::integer;
    IF n=ANY(seen) OR n>jsonb_array_length(items) THEN RAISE EXCEPTION 'daily review item duplicate or missing'; END IF;
    seen:=array_append(seen,n); old_item:=items->(n-1);
    IF originals->(n-1)->>'status'<>'difference' OR row->'expected_item_version' IS DISTINCT FROM old_item->'version'
     THEN RAISE EXCEPTION 'daily review item version or numeric status invalid'; END IF;
    SELECT value INTO STRICT value_item FROM jsonb_array_elements(e->'evidence') WHERE value->'file_id'=row->'evidence_file_id';
    IF value_item->'sha256' IS DISTINCT FROM row->'evidence_sha256' THEN RAISE EXCEPTION 'daily review evidence changed'; END IF;
    items:=jsonb_set(items,ARRAY[(n-1)::text],jsonb_build_object('ordinal',n,'version',(old_item->>'version')::bigint+1,
      'explanation',row->>'explanation','evidence',value_item,'explained_by_person_id',a->'person_id','revision_requested',false,'review_comment',''));
   END LOOP;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements(e->'evidence') f WHERE NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(q->'items') x WHERE x->'evidence_file_id'=f->'file_id')) THEN RAISE EXCEPTION 'daily review extra evidence'; END IF;
   SELECT jsonb_agg(v ORDER BY v#>>'{}') INTO authors FROM (SELECT DISTINCT v FROM jsonb_array_elements(authors||jsonb_build_array(a->'person_id')) x(v)) all_authors;
  ELSE
   IF authors @> jsonb_build_array(a->'person_id') THEN RAISE EXCEPTION 'daily review self review forbidden'; END IF;
   IF jsonb_typeof(q->'comment')<>'string' OR char_length(q->>'comment') NOT BETWEEN 4 AND 2000
     OR q->>'comment'<>btrim(q->>'comment') OR q->>'comment'~'[[:cntrl:]]' OR jsonb_typeof(q->'ordinals')<>'array'
     THEN RAISE EXCEPTION 'daily review comment invalid'; END IF;
   IF op='request_changes' THEN
    IF jsonb_array_length(q->'ordinals') NOT BETWEEN 1 AND 100 OR e->'evidence'<>'[]'::jsonb THEN RAISE EXCEPTION 'daily review selection invalid'; END IF;
    FOR row IN SELECT value FROM jsonb_array_elements(q->'ordinals') LOOP
     IF jsonb_typeof(row)<>'number' OR row#>>'{}'!~'^[1-9][0-9]*$' THEN RAISE EXCEPTION 'daily review ordinal invalid'; END IF;
     n:=(row#>>'{}')::integer;
     IF n=ANY(seen) OR n>jsonb_array_length(items) THEN RAISE EXCEPTION 'daily review selection duplicate or missing'; END IF;
     seen:=array_append(seen,n);old_item:=items->(n-1);
     IF originals->(n-1)->>'status'<>'difference' OR old_item->>'explanation'='' OR old_item->>'revision_requested'<>'false' THEN RAISE EXCEPTION 'daily review item not explained'; END IF;
     items:=jsonb_set(items,ARRAY[(n-1)::text],old_item||jsonb_build_object('revision_requested',true,'review_comment',q->>'comment','version',(old_item->>'version')::bigint+1));
    END LOOP;
   ELSE
    IF q->'ordinals'<>'[]'::jsonb OR EXISTS(SELECT 1 FROM jsonb_array_elements(items) x
       WHERE originals->((x->>'ordinal')::integer-1)->>'status'='difference' AND (x->>'explanation'='' OR x->>'revision_requested'<>'false'))
     THEN RAISE EXCEPTION 'daily review unexplained differences'; END IF;
    SELECT COALESCE(jsonb_agg(f ORDER BY f->>'file_id'),'[]'::jsonb) INTO expected_files FROM
      (SELECT DISTINCT x->'evidence' f FROM jsonb_array_elements(items) x WHERE x->'evidence'<>'null'::jsonb) z;
    SELECT COALESCE(jsonb_agg(f ORDER BY f->>'file_id'),'[]'::jsonb) INTO current_files FROM jsonb_array_elements(e->'evidence') f;
    IF expected_files IS DISTINCT FROM current_files THEN RAISE EXCEPTION 'daily review evidence changed'; END IF;
    RETURN jsonb_build_object('version',(prior->>'version')::bigint+1,'review_status','approved','items',items,
     'explanation_authors',authors,'approved_by_person_id',a->'person_id','approval_comment',q->>'comment');
   END IF;
  END IF;
 END IF;
 status:=CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(items) x WHERE x->>'revision_requested'='true') THEN 'changes_requested'
 WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(items) x WHERE originals->((x->>'ordinal')::integer-1)->>'status'='difference' AND x->>'explanation'='')
 THEN 'awaiting_explanations' ELSE 'pending_review' END;
 RETURN jsonb_build_object('version',(prior->>'version')::bigint+1,'review_status',status,'items',items,
  'explanation_authors',authors,'approved_by_person_id',NULL,'approval_comment','');
END
""",api=False)

function('rsc_guard_daily_review_event_0132','','trigger',"""
DECLARE snap jsonb; prior jsonb; parent public.daily_review_events%ROWTYPE; state jsonb; expected_receipt jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'daily review history immutable' USING ERRCODE='23514'; END IF;
 IF session_user NOT IN ('star_oam_api','star_oam_migrator') OR current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION 'daily review runtime or isolation invalid' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.auth_sessions WHERE id=NEW.auth_session_id FOR UPDATE;
 PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
 PERFORM pg_advisory_xact_lock(hashtextextended('daily-review:'||NEW.cutoff_id::text,0));
 PERFORM 1 FROM public.daily_review_bindings WHERE cutoff_id=NEW.cutoff_id FOR UPDATE;
 snap:=public.rsc_daily_review_snapshot_0132(NEW.cutoff_id);
 SELECT * INTO parent FROM public.daily_review_events WHERE cutoff_id=NEW.cutoff_id ORDER BY version DESC LIMIT 1;
 IF parent.id IS NULL THEN
  SELECT jsonb_build_object('version',0,'review_status','not_recorded','items',COALESCE(jsonb_agg(jsonb_build_object('ordinal',i,
    'version',0,'explanation','','evidence',NULL,'explained_by_person_id',NULL,'revision_requested',false,'review_comment','') ORDER BY i),'[]'::jsonb),
    'explanation_authors','[]'::jsonb,'approved_by_person_id',NULL,'approval_comment','') INTO prior
    FROM generate_series(1,jsonb_array_length(snap->'items')) i;
 ELSE
  IF parent.run_id<>NEW.run_id OR NOT EXISTS(SELECT 1 FROM public.daily_review_consumptions WHERE event_id=parent.id)
    OR NEW.created_at<parent.created_at THEN RAISE EXCEPTION 'daily review prior event incomplete'; END IF;
  prior:=parent.after_state_jsonb;
 END IF;
 IF NEW.transaction_id<>txid_current() OR NEW.created_at>clock_timestamp() OR NEW.created_at<transaction_timestamp()
  OR NEW.version<>(prior->>'version')::bigint+1 OR jsonb_typeof(NEW.payload_jsonb)<>'object'
  OR (SELECT count(*) FROM jsonb_object_keys(NEW.payload_jsonb))<>9
  OR NOT NEW.payload_jsonb ?& ARRAY['event_id','version','previous_sha256','snapshot_sha256','request','actor','occurred_at','evidence','sha256']
  OR NEW.payload_jsonb->>'event_id' IS DISTINCT FROM NEW.id::text OR NEW.payload_jsonb->'version' IS DISTINCT FROM to_jsonb(NEW.version)
  OR NEW.payload_jsonb->>'previous_sha256' IS DISTINCT FROM COALESCE(parent.payload_sha256,repeat('0',64))
  OR NEW.payload_jsonb->>'snapshot_sha256' IS DISTINCT FROM public.rsc_daily_review_hash_0132(snap)
  OR NEW.payload_jsonb->>'sha256' IS DISTINCT FROM NEW.payload_sha256
  OR NEW.payload_sha256 IS DISTINCT FROM public.rsc_daily_review_hash_0132(NEW.payload_jsonb-'sha256')
  OR (NEW.payload_jsonb->>'occurred_at')::timestamptz IS DISTINCT FROM NEW.created_at
  OR NEW.payload_jsonb#>>'{actor,user_id}' IS DISTINCT FROM NEW.actor_user_id
  OR NEW.payload_jsonb#>>'{actor,person_id}' IS DISTINCT FROM NEW.actor_person_id::text
  OR NEW.payload_jsonb#>>'{request,request_id}' IS DISTINCT FROM NEW.request_id
  OR NEW.payload_jsonb#>>'{request,idempotency_key}' IS DISTINCT FROM NEW.idempotency_key
  OR jsonb_typeof(NEW.payload_jsonb->'actor')<>'object' OR (SELECT count(*) FROM jsonb_object_keys(NEW.payload_jsonb->'actor'))<>9
  OR NOT NEW.payload_jsonb->'actor' ?& ARRAY['user_id','person_id','authorization_version','assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']
 THEN RAISE EXCEPTION 'daily review event binding invalid' USING ERRCODE='23514'; END IF;
 -- Lock evidence in deterministic order before the final audit stream lock.
 PERFORM 1 FROM public.files WHERE id IN(SELECT (value->>'file_id')::uuid FROM jsonb_array_elements(NEW.payload_jsonb->'evidence')) ORDER BY id FOR SHARE;
 PERFORM public.rsc_daily_review_live_0132(NEW,clock_timestamp());
 state:=public.rsc_daily_review_step_0132(snap,prior,NEW.payload_jsonb);
 expected_receipt:=jsonb_build_object('recorded',true,'event_id',NEW.id,'event_sha256',NEW.payload_sha256,'version',NEW.version,
  'review_status',state->>'review_status','comparison_status',snap->>'comparison_status','stock_written',false);
 IF NEW.after_state_jsonb IS DISTINCT FROM state OR NEW.receipt_jsonb IS DISTINCT FROM expected_receipt
 THEN RAISE EXCEPTION 'daily review result forged' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END
""",api=False)

function('rsc_guard_daily_review_binding_0132','','trigger',"""
DECLARE e public.daily_review_events%ROWTYPE; snap jsonb;
BEGIN
 IF TG_OP NOT IN ('INSERT','UPDATE') THEN RAISE EXCEPTION 'daily review binding immutable'; END IF;
 SELECT * INTO STRICT e FROM public.daily_review_events WHERE id=NEW.last_event_id;
 snap:=public.rsc_daily_review_snapshot_0132(e.cutoff_id);
 IF e.transaction_id<>txid_current() OR EXISTS(SELECT 1 FROM public.daily_review_consumptions WHERE event_id=e.id)
  OR NEW.run_id<>e.run_id OR NEW.cutoff_id<>e.cutoff_id OR NEW.version<>e.version OR NEW.updated_at<>e.created_at
  OR NEW.state_jsonb IS DISTINCT FROM e.after_state_jsonb OR jsonb_typeof(NEW.item_ids)<>'array'
  OR jsonb_array_length(NEW.item_ids)<>jsonb_array_length(snap->'items')
  OR (SELECT count(DISTINCT value) FROM jsonb_array_elements(NEW.item_ids))<>jsonb_array_length(NEW.item_ids)
  OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.item_ids) x WHERE jsonb_typeof(x)<>'string' OR (x#>>'{}')::uuid='00000000-0000-0000-0000-000000000000'::uuid)
 THEN RAISE EXCEPTION 'daily review binding invalid'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.version<>1 OR NEW.created_at<>e.created_at OR EXISTS(SELECT 1 FROM public.reconciliation_runs WHERE id=NEW.run_id)
  THEN RAISE EXCEPTION 'daily review creation invalid'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['version','last_event_id','state_jsonb','updated_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['version','last_event_id','state_jsonb','updated_at'])
   OR NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'daily review immutable binding changed'; END IF;
 END IF;
 RETURN NEW;
END
""",api=False)

function('rsc_daily_review_projection_gate_0132','rid uuid','void',"""
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.daily_review_bindings b JOIN public.daily_review_events e ON e.id=b.last_event_id
  WHERE b.run_id=rid AND b.version=e.version AND e.transaction_id=txid_current()
   AND NOT EXISTS(SELECT 1 FROM public.daily_review_consumptions s WHERE s.event_id=e.id))
 THEN RAISE EXCEPTION 'daily review projection requires unconsumed command'; END IF;
END
""",api=True)

function('rsc_daily_review_projection_proof_0132','rid uuid','void',"""
DECLARE b public.daily_review_bindings%ROWTYPE; c public.daily_reconciliation_cutoffs%ROWTYPE;
 e public.daily_review_events%ROWTYPE; r public.reconciliation_runs%ROWTYPE; x public.reconciliation_items%ROWTYPE;
 original jsonb; item jsonb; n integer; wanted_status text; touched timestamptz; expected_key text;
BEGIN
 SELECT * INTO STRICT b FROM public.daily_review_bindings WHERE run_id=rid;
 SELECT * INTO STRICT c FROM public.daily_reconciliation_cutoffs WHERE id=b.cutoff_id;
 SELECT * INTO STRICT e FROM public.daily_review_events WHERE id=b.last_event_id;
 SELECT * INTO STRICT r FROM public.reconciliation_runs WHERE id=rid;
 IF b.version<>e.version OR b.state_jsonb IS DISTINCT FROM e.after_state_jsonb OR b.updated_at<>e.created_at
  OR r.run_key<>'daily-control:'||c.id::text OR r.source_system_id<>c.source_system_id
  OR r.scope<>'daily:'||c.region_org_id::text||':'||c.business_date::text OR r.external_snapshot_at<>c.source_captured_at
  OR r.local_ledger_cursor<>c.local_ledger_cursor::text OR r.status<>(CASE WHEN b.state_jsonb->>'review_status'='approved' THEN 'approved' ELSE c.payload_jsonb#>>'{comparison,comparison,status}' END)
  OR r.summary_jsonb IS DISTINCT FROM jsonb_build_object('schema','rsc.daily_reconciliation.summary.v1','cutoff_id',c.id,
     'cutoff_sha256',c.payload_sha256,'comparison_sha256',c.payload_jsonb#>>'{comparison,comparison,report_sha256}','item_count',jsonb_array_length(b.item_ids))
  OR r.started_at IS DISTINCT FROM b.created_at OR r.completed_at IS DISTINCT FROM b.created_at OR r.created_at<>b.created_at OR r.updated_at<>b.updated_at
  OR (SELECT count(*) FROM public.reconciliation_items WHERE run_id=rid)<>jsonb_array_length(b.item_ids)
 THEN RAISE EXCEPTION 'daily review run projection invalid'; END IF;
 FOR n IN 1..jsonb_array_length(b.item_ids) LOOP
  SELECT * INTO STRICT x FROM public.reconciliation_items WHERE id=(b.item_ids->>(n-1))::uuid AND run_id=rid;
  original:=c.payload_jsonb#>ARRAY['comparison','comparison','items',(n-1)::text];item:=b.state_jsonb->'items'->(n-1);
  expected_key:=public.rsc_canonical_reconciliation_json_0026(jsonb_build_array(original->>'warehouse_code',original->>'material_id',original->>'condition'));
  wanted_status:=CASE WHEN original->>'status'='matched' THEN 'matched' WHEN b.state_jsonb->>'review_status'='approved' THEN 'resolved'
   WHEN item->>'explanation'<>'' AND item->>'revision_requested'='false' THEN 'explained' ELSE 'difference' END;
  SELECT COALESCE(max(v.created_at),b.created_at) INTO touched FROM public.daily_review_events v WHERE v.run_id=rid AND (
   (v.payload_jsonb#>>'{request,operation}'='approve' AND original->>'status'='difference')
   OR (v.payload_jsonb#>>'{request,operation}'='request_changes' AND v.payload_jsonb#>'{request,ordinals}' @> to_jsonb(n))
   OR (v.payload_jsonb#>>'{request,operation}'='explain' AND EXISTS(SELECT 1 FROM jsonb_array_elements(v.payload_jsonb#>'{request,items}') z WHERE z->'ordinal'=to_jsonb(n))));
  IF x.business_key<>expected_key OR x.external_qty<>(original->>'external_qty')::numeric OR x.local_qty<>(original->>'local_qty')::numeric
   OR x.difference<>(original->>'difference')::numeric OR x.status<>wanted_status OR x.explanation IS DISTINCT FROM item->>'explanation'
   OR x.evidence_file_id IS DISTINCT FROM (item#>>'{evidence,file_id}')::uuid OR x.created_at<>b.created_at OR x.updated_at<>touched
  THEN RAISE EXCEPTION 'daily review item projection invalid'; END IF;
 END LOOP;
END
""",api=False)

function('rsc_guard_daily_review_consumption_0132','','trigger',"""
DECLARE e public.daily_review_events%ROWTYPE; prior_status text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'daily review consumption immutable'; END IF;
 SELECT * INTO STRICT e FROM public.daily_review_events WHERE id=NEW.event_id;
 SELECT after_state_jsonb->>'review_status' INTO prior_status FROM public.daily_review_events WHERE run_id=e.run_id AND version=e.version-1;
 IF NEW.transaction_id<>txid_current() OR e.transaction_id<>txid_current()
  OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.id=NEW.audit_event_id AND a.stream_key='authorization'
   AND a.action='daily_reconciliation.'||(e.payload_jsonb#>>'{request,operation}') AND a.aggregate_type='daily_reconciliation_run'
   AND a.aggregate_id=e.run_id::text AND a.actor_user_id=e.actor_user_id AND a.request_id='daily-review:'||e.id::text
   AND a.occurred_at=e.created_at AND a.before_jsonb=jsonb_build_object('version',e.version-1)
   AND a.after_jsonb=jsonb_build_object('event_id',e.id,'event_sha256',e.payload_sha256,'receipt',e.receipt_jsonb))
  OR (prior_status IS NOT DISTINCT FROM e.after_state_jsonb->>'review_status' AND NEW.transition_event_id IS NOT NULL)
  OR (prior_status IS DISTINCT FROM e.after_state_jsonb->>'review_status' AND NOT EXISTS(SELECT 1 FROM public.state_transition_events t WHERE t.id=NEW.transition_event_id
   AND t.aggregate_type='daily_reconciliation_run' AND t.aggregate_id=e.run_id::text AND t.actor_id=e.actor_user_id
   AND t.from_status IS NOT DISTINCT FROM prior_status AND t.to_status=e.after_state_jsonb->>'review_status'
   AND t.reason='daily_reconciliation.'||(e.payload_jsonb#>>'{request,operation}') AND t.occurred_at=e.created_at
   AND t.idempotency_key='daily-review:'||e.id::text AND t.metadata_jsonb=jsonb_build_object('event_id',e.id,'event_sha256',e.payload_sha256)))
 THEN RAISE EXCEPTION 'daily review effects or transaction invalid'; END IF;
 PERFORM public.rsc_daily_review_projection_proof_0132(e.run_id);
 RETURN NEW;
END
""",api=False)

function('rsc_guard_daily_review_commit_0132','','trigger',"""
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.daily_review_consumptions WHERE event_id=NEW.id AND transaction_id=NEW.transaction_id)
 THEN RAISE EXCEPTION 'daily review command unconsumed'; END IF;
 PERFORM public.rsc_daily_review_live_0132(NEW,clock_timestamp());
 PERFORM public.rsc_daily_review_projection_proof_0132(NEW.run_id);
 RETURN NEW;
END
""",api=False)

function('rsc_guard_daily_review_transition_0132','','trigger',"""
DECLARE e public.daily_review_events%ROWTYPE;
BEGIN
 IF TG_OP='TRUNCATE' THEN
  IF EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_type='daily_reconciliation_run') THEN RAISE EXCEPTION 'daily review transition immutable'; END IF;
  RETURN NULL;
 END IF;
 IF TG_OP IN ('UPDATE','DELETE') AND OLD.aggregate_type='daily_reconciliation_run' THEN RAISE EXCEPTION 'daily review transition immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF NEW.aggregate_type<>'daily_reconciliation_run' THEN RETURN NEW; END IF;
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'daily review transition insert required'; END IF;
 SELECT * INTO STRICT e FROM public.daily_review_events WHERE id=(NEW.metadata_jsonb->>'event_id')::uuid;
 IF TG_WHEN='BEFORE' THEN
  PERFORM public.rsc_daily_review_projection_gate_0132(e.run_id);
  IF e.transaction_id<>txid_current() OR NEW.aggregate_id<>e.run_id::text OR NEW.actor_id<>e.actor_user_id
   THEN RAISE EXCEPTION 'daily review transition command mismatch'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.daily_review_consumptions WHERE event_id=e.id AND transition_event_id=NEW.id)
   THEN RAISE EXCEPTION 'daily review orphan transition'; END IF;
 END IF;
 RETURN NEW;
END
""",api=False)

function('rsc_guard_daily_review_audit_0132','','trigger',"""
DECLARE e public.daily_review_events%ROWTYPE;
BEGIN
 IF NEW.aggregate_type<>'daily_reconciliation_run' AND NEW.action NOT IN ('daily_reconciliation.open','daily_reconciliation.explain','daily_reconciliation.approve','daily_reconciliation.request_changes') THEN RETURN NEW; END IF;
 SELECT * INTO STRICT e FROM public.daily_review_events WHERE id=(NEW.after_jsonb->>'event_id')::uuid;
 IF TG_WHEN='BEFORE' THEN
  PERFORM public.rsc_daily_review_projection_gate_0132(e.run_id);
  IF e.transaction_id<>txid_current() OR NEW.aggregate_type<>'daily_reconciliation_run'
   OR NEW.aggregate_id<>e.run_id::text OR NEW.actor_user_id<>e.actor_user_id
   THEN RAISE EXCEPTION 'daily review audit command mismatch'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM public.daily_review_consumptions WHERE event_id=e.id AND audit_event_id=NEW.id)
   THEN RAISE EXCEPTION 'daily review orphan audit'; END IF;
 END IF;
 RETURN NEW;
END
""",api=False)

TRIGGERS = {'trg_daily_review_events_facts_0132': ('daily_review_events',
                                        'rsc_guard_daily_review_event_0132',
                                        'INSERT OR UPDATE OR DELETE',
                                        31,
                                        False),
 'trg_daily_review_events_truncate_0132': ('daily_review_events',
                                           'rsc_guard_daily_review_event_0132',
                                           'TRUNCATE',
                                           34,
                                           False),
 'trg_daily_review_bindings_facts_0132': ('daily_review_bindings',
                                          'rsc_guard_daily_review_binding_0132',
                                          'INSERT OR UPDATE OR DELETE',
                                          31,
                                          False),
 'trg_daily_review_bindings_truncate_0132': ('daily_review_bindings',
                                             'rsc_guard_daily_review_binding_0132',
                                             'TRUNCATE',
                                             34,
                                             False),
 'trg_daily_review_consumptions_facts_0132': ('daily_review_consumptions',
                                              'rsc_guard_daily_review_consumption_0132',
                                              'INSERT OR UPDATE OR DELETE',
                                              31,
                                              False),
 'trg_daily_review_consumptions_truncate_0132': ('daily_review_consumptions',
                                                 'rsc_guard_daily_review_consumption_0132',
                                                 'TRUNCATE',
                                                 34,
                                                 False),
 'trg_daily_review_commit_0132': ('daily_review_events', 'rsc_guard_daily_review_commit_0132', 'INSERT', 5, True),
 'trg_daily_review_transition_facts_0132': ('state_transition_events',
                                            'rsc_guard_daily_review_transition_0132',
                                            'INSERT OR UPDATE OR DELETE',
                                            31,
                                            False),
 'trg_daily_review_transition_truncate_0132': ('state_transition_events',
                                               'rsc_guard_daily_review_transition_0132',
                                               'TRUNCATE',
                                               34,
                                               False),
 'trg_daily_review_transition_commit_0132': ('state_transition_events',
                                             'rsc_guard_daily_review_transition_0132',
                                             'INSERT',
                                             5,
                                             True),
 'trg_daily_review_audit_facts_0132': ('audit_events', 'rsc_guard_daily_review_audit_0132', 'INSERT', 7, False),
 'trg_daily_review_audit_commit_0132': ('audit_events', 'rsc_guard_daily_review_audit_0132', 'INSERT', 5, True)}

_FOLDER=Path(__file__).parent

def _types(args):return ', '.join(p.strip().split(' ',1)[1] for p in args.split(',')) if args else ''

def _guard_sources():
    legacy=runpy.run_path(str(_FOLDER/'20260831_0026_opening_control_reconciliation.py'))
    statements=[];create=legacy['_create_postgresql_guards']
    FunctionType(create.__code__,dict(create.__globals__,op=SimpleNamespace(execute=statements.append)))()
    result={}
    for name,field in [('rsc_guard_reconciliation_run_projection_0026','id'),('rsc_guard_reconciliation_item_projection_0026','run_id')]:
        statement=next(s for s in statements if 'CREATE FUNCTION public.'+name+'()' in s)
        old=statement.split('AS $$',1)[1].split('$$',1)[0]
        branch=f"\n    IF TG_OP IN ('INSERT','UPDATE') AND EXISTS(SELECT 1 FROM public.daily_review_bindings WHERE run_id=NEW.{field}) THEN\n        PERFORM public.rsc_daily_review_projection_gate_0132(NEW.{field}); RETURN NEW;\n    END IF;\n"
        result[name]=(old,old.replace('BEGIN','BEGIN'+branch,1))
    return result

GUARD_SOURCES=_guard_sources()
GUARD_HASHES={name:tuple(hashlib.sha256(body.encode()).hexdigest() for body in pair) for name,pair in GUARD_SOURCES.items()}
FUNCTION_HASHES={name:hashlib.sha256(f[2].encode()).hexdigest() for name,f in FUNCTIONS.items()}
SEED_NAMESPACE=uuid.UUID('5a08d3ad-f71d-470b-9944-cb908f801db0')
PERMISSIONS=tuple((uuid.uuid5(SEED_NAMESPACE,'permission:'+a),a) for a in ('create_daily','explain_daily','approve_daily'))
ROLE_PERMISSIONS=tuple((uuid.uuid5(SEED_NAMESPACE,'grant:'+a+':'+role),uuid.UUID(role),dict((a,i) for i,a in PERMISSIONS)[a])
 for a,roles in [('create_daily',('10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002')),
                 ('explain_daily',('10000000-0000-4000-8000-000000000002',)),('approve_daily',('10000000-0000-4000-8000-000000000001',))]
 for role in roles)

def _verify_guard(name,after):
    expected=GUARD_HASHES[name][int(after)]
    table='reconciliation_runs' if name=='rsc_guard_reconciliation_run_projection_0026' else 'reconciliation_items'
    expected_triggers=','.join("('trg_"+table+"_"+suffix+"_0026',"+str(kind)+")" for suffix,kind in [('formal_guard',19),('formal_delete',11),('no_truncate',34),('formal_insert',7)])
    op.execute(f"""DO $guard_0132$ BEGIN
     IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}()'::regprocedure
      AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
      AND p.prokind='f' AND p.prorettype='trigger'::regtype AND NOT p.proretset AND NOT p.prosecdef
      AND NOT p.proisstrict AND NOT p.proleakproof AND p.provolatile='v' AND p.proparallel='u'
      AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql') AND p.pronargdefaults=0 AND p.proargmodes IS NULL
      AND p.proconfig=ARRAY['search_path=pg_catalog, public']
      AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected}'
      AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
      OR (SELECT count(*) FROM pg_trigger WHERE tgfoid='public.{name}()'::regprocedure)<>4
     THEN RAISE EXCEPTION '0132 original projection guard drift'; END IF;
     IF EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgfoid='public.{name}()'::regprocedure
       AND (t.tgenabled<>'A' OR (t.tgname,t.tgtype) NOT IN ({expected_triggers}) OR t.tgrelid<>'public.{table}'::regclass OR t.tgdeferrable OR t.tginitdeferred OR t.tgisinternal
       OR t.tgconstraint<>0 OR t.tgqual IS NOT NULL OR t.tgnargs<>0 OR t.tgattr<>''::int2vector))
     THEN RAISE EXCEPTION '0132 original projection trigger drift'; END IF;
    END $guard_0132$""")

def _patch_guards(up):
    replace=runpy.run_path(str(_FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    for name,(old,new) in GUARD_SOURCES.items():
        _verify_guard(name,not up)
        replace(signature='public.'+name+'()',expected_hash=GUARD_HASHES[name][0 if up else 1],replacement_hash=GUARD_HASHES[name][1 if up else 0],
            replacements=((old,new),) if up else ((new,old),),label='daily_review_projection_0132')
        _verify_guard(name,up)

def _verify():
    for name,(args,result,body,api) in FUNCTIONS.items():
        types=_types(args);expected=FUNCTION_HASHES[name]
        acl="AND NOT(r.rolname='star_oam_api' AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)" if api else ''
        api_check=f"AND has_function_privilege('star_oam_api',p.oid,'EXECUTE')={str(api).lower()}"
        op.execute(f"""DO $function_0132$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p
         WHERE p.oid='public.{name}({types})'::regprocedure AND p.prokind='f' AND p.prorettype='{result}'::regtype
          AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator') AND p.prosecdef
          AND NOT p.proretset AND NOT p.proisstrict AND NOT p.proleakproof AND p.provolatile='v' AND p.proparallel='u'
          AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql') AND p.proargmodes IS NULL AND p.pronargdefaults=0
          AND p.proconfig=ARRAY['search_path=pg_catalog, public'] {api_check}
          AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected}'
          AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a LEFT JOIN pg_roles r ON r.oid=a.grantee
             WHERE a.grantee<>p.proowner {acl})) THEN RAISE EXCEPTION '0132 daily function catalog drift'; END IF; END $function_0132$""")
    for name,(table,function,events,kind,deferred) in TRIGGERS.items():
        flag=str(deferred).lower()
        op.execute(f"""DO $trigger_0132$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid='public.{table}'::regclass AND t.tgname='{name}' AND t.tgtype={kind}
          AND t.tgfoid='public.{function}()'::regprocedure AND t.tgenabled='A' AND NOT t.tgisinternal
          AND t.tgdeferrable={flag} AND t.tginitdeferred={flag} AND (t.tgconstraint<>0)={flag}
          AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgattr=''::int2vector)
         THEN RAISE EXCEPTION '0132 daily trigger catalog drift'; END IF; END $trigger_0132$""")
    for function in (f for f in FUNCTIONS if FUNCTIONS[f][1]=='trigger'):
        count=sum(t[1]==function for t in TRIGGERS.values())
        op.execute(f"DO $closure_0132$ BEGIN IF (SELECT count(*) FROM pg_trigger WHERE tgfoid='public.{function}()'::regprocedure)<>{count} THEN RAISE EXCEPTION '0132 unexpected trigger use'; END IF; END $closure_0132$")
    for table in TABLES:
        column_allowed="AND NOT(a.attname IN ('version','last_event_id','state_jsonb','updated_at') AND r.rolname='star_oam_api' AND acl.privilege_type='UPDATE' AND NOT acl.is_grantable)" if table=='daily_review_bindings' else ''
        op.execute(f"""DO $acl_0132$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_class c
         WHERE c.oid='public.{table}'::regclass AND c.relkind='r' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
          AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
          AND has_table_privilege('star_oam_api',c.oid,'SELECT') AND has_table_privilege('star_oam_api',c.oid,'INSERT')
          AND has_table_privilege('star_oam_backup',c.oid,'SELECT')
          AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee
           WHERE acl.grantee<>c.relowner AND NOT(NOT acl.is_grantable AND ((r.rolname='star_oam_api' AND acl.privilege_type IN ('SELECT','INSERT')) OR (r.rolname='star_oam_backup' AND acl.privilege_type='SELECT'))))
          AND NOT EXISTS(SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) acl LEFT JOIN pg_roles r ON r.oid=acl.grantee WHERE a.attrelid=c.oid {column_allowed}))
         THEN RAISE EXCEPTION '0132 daily table ACL drift'; END IF; END $acl_0132$""")
    for column in ('version','last_event_id','state_jsonb','updated_at'):
        op.execute(f"DO $column_0132$ BEGIN IF NOT has_column_privilege('star_oam_api','public.daily_review_bindings','{column}','UPDATE') THEN RAISE EXCEPTION '0132 binding update privilege missing'; END IF; END $column_0132$")
    for name,target,column in [('fk_daily_review_event_run','daily_review_bindings','run_id'),('fk_daily_review_event_consumed','daily_review_consumptions','id')]:
        target_column='run_id' if column=='run_id' else 'event_id'
        op.execute(f"""DO $fk_0132$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint c
         WHERE c.conrelid='public.daily_review_events'::regclass AND c.conname='{name}' AND c.contype='f' AND c.convalidated
          AND c.condeferrable AND c.condeferred AND c.confrelid='public.{target}'::regclass AND c.confupdtype='a' AND c.confdeltype='a'
          AND c.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.conrelid AND attname='{column}')]::smallint[]
          AND c.confkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid=c.confrelid AND attname='{target_column}')]::smallint[])
         THEN RAISE EXCEPTION '0132 command consumption foreign key drift'; END IF; END $fk_0132$""")

def _permission_tables():
    return (sa.table('permissions',sa.column('id',sa.Uuid()),sa.column('resource',sa.String()),sa.column('action',sa.String()),sa.column('field_code',sa.String()),sa.column('description',sa.String()),sa.column('created_at',sa.DateTime(timezone=True)),sa.column('updated_at',sa.DateTime(timezone=True))),
      sa.table('role_permissions',sa.column('id',sa.Uuid()),sa.column('role_id',sa.Uuid()),sa.column('permission_id',sa.Uuid()),sa.column('effect',sa.String()),sa.column('created_at',sa.DateTime(timezone=True))))

def _seed():
    p,rp=_permission_tables();stamp=datetime(2026,11,11,tzinfo=timezone.utc)
    op.bulk_insert(p,[dict(id=id,resource='reconciliation',action=action,field_code='',description='Daily comparison '+action,created_at=stamp,updated_at=stamp) for id,action in PERMISSIONS])
    op.bulk_insert(rp,[dict(id=id,role_id=role,permission_id=permission,effect='allow',created_at=stamp) for id,role,permission in ROLE_PERMISSIONS])

def _verify_seed_removal(dialect,preflight):
    def identifier(value):return str(value) if dialect=='postgresql' else value.hex
    permission_ids=','.join("'"+identifier(row[0])+"'" for row in PERMISSIONS)
    grant_ids=','.join("'"+identifier(row[0])+"'" for row in ROLE_PERMISSIONS)
    clauses=[]
    for id,action in PERMISSIONS:
        clauses.append("(id='"+identifier(id)+"' AND resource='reconciliation' AND action='"+action+"' AND field_code='' AND description='Daily comparison "+action+"')")
    preflight("(SELECT count(*) FROM permissions WHERE "+' OR '.join(clauses)+")<>3",'0132 permission seed drift: preserve configuration')
    clauses=[]
    for id,role,permission in ROLE_PERMISSIONS:
        clauses.append("(id='"+identifier(id)+"' AND role_id='"+identifier(role)+"' AND permission_id='"+identifier(permission)+"' AND effect='allow')")
    preflight("(SELECT count(*) FROM role_permissions WHERE "+' OR '.join(clauses)+")<>4 OR EXISTS(SELECT 1 FROM role_permissions WHERE permission_id IN ("+permission_ids+") AND id NOT IN ("+grant_ids+"))",'0132 role permission seed drift: preserve configuration')

def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in DDL:raise RuntimeError('0132 PostgreSQL or SQLite required')
    helper=runpy.run_path(str(_FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'));helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $owner_0132$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0132 direct schema owner required'; END IF; END $owner_0132$")
        op.execute('LOCK TABLE public.alembic_version,public.reconciliation_runs,public.reconciliation_items,public.audit_events,public.state_transition_events,public.permissions,public.role_permissions IN ACCESS EXCLUSIVE MODE')
    if up:
        helper['_preflight']("EXISTS(SELECT 1 FROM permissions WHERE resource='reconciliation' AND action IN ('create_daily','explain_daily','approve_daily'))",'0132 preexisting daily permissions require review')
        for sql in DDL[dialect]:op.execute(sql)
        _seed()
    else:
        if dialect=='postgresql':op.execute('LOCK TABLE public.daily_review_events,public.daily_review_bindings,public.daily_review_consumptions IN ACCESS EXCLUSIVE MODE')
        helper['_preflight']("EXISTS(SELECT 1 FROM daily_review_events) OR EXISTS(SELECT 1 FROM daily_review_bindings) OR EXISTS(SELECT 1 FROM daily_review_consumptions) OR EXISTS(SELECT 1 FROM audit_events WHERE aggregate_type='daily_reconciliation_run') OR EXISTS(SELECT 1 FROM state_transition_events WHERE aggregate_type='daily_reconciliation_run')",'0132 populated downgrade refused: retain daily review history')
        _verify_seed_removal(dialect,helper['_preflight'])
    if dialect=='postgresql':
        if up:
            for name,(args,result,body,api) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({_types(args)}) FROM PUBLIC,star_oam_api')
                if api:op.execute(f'GRANT EXECUTE ON FUNCTION public.{name}({_types(args)}) TO star_oam_api')
            for name,(table,function,events,kind,deferred) in TRIGGERS.items():
                clause=f'CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred else f"TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}"
                op.execute(f'CREATE {clause} EXECUTE FUNCTION public.{function}()');op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            for table in TABLES:
                op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox')
                op.execute(f'GRANT SELECT,INSERT ON public.{table} TO star_oam_api');op.execute(f'GRANT SELECT ON public.{table} TO star_oam_backup')
            op.execute('GRANT UPDATE(version,last_event_id,state_jsonb,updated_at) ON public.daily_review_bindings TO star_oam_api')
            _verify();_patch_guards(True)
        else:
            _verify();_patch_guards(False)
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,(args,*_) in reversed(FUNCTIONS.items()):op.execute(f'DROP FUNCTION public.{name}({_types(args)})')
            for fk in ('fk_daily_review_event_consumed','fk_daily_review_event_run'):op.execute('ALTER TABLE daily_review_events DROP CONSTRAINT '+fk)
        replace=runpy.run_path(str(_FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,replacement_hash=NEW_HASH if up else OLD_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='daily_review_readiness_0132')
    elif up:
        for table in ('daily_review_events','daily_review_consumptions'):
            for event in ('UPDATE','DELETE'):op.execute(f"CREATE TRIGGER trg_{table}_{event.lower()}_0132 BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT,'0132 daily review facts immutable'); END")
    if not up:
        for table in ('daily_review_consumptions','daily_review_bindings','daily_review_events'):op.drop_table(table)
        p,rp=_permission_tables()
        op.execute(rp.delete().where(rp.c.id.in_(tuple(row[0] for row in ROLE_PERMISSIONS))))
        op.execute(p.delete().where(p.c.id.in_(tuple(row[0] for row in PERMISSIONS))))

def upgrade():_transition(True)
def downgrade():_transition(False)
