-- Candidate dependency of recovery_approval.sql. Existing 0036 file-object
-- guards and 0017 audit-chain guards remain mandatory in the formal migration.
CREATE FUNCTION public.rsc_check_scrap_recovery_files_with_audit_0165(checked_request uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE app public.stock_scrap_recovery_requests%ROWTYPE; binding public.stock_scrap_recovery_files%ROWTYPE;
    file public.files%ROWTYPE; metadata jsonb; completion jsonb; expected jsonb;
    file_key text; verified timestamptz;  creation public.audit_events%ROWTYPE; completed public.audit_events%ROWTYPE;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    SELECT * INTO app FROM public.stock_scrap_recovery_requests WHERE id=checked_request;
    IF app.id IS NULL THEN RAISE EXCEPTION '0165 recovery application required for files' USING ERRCODE='23514'; END IF;
    FOR binding IN SELECT * FROM public.stock_scrap_recovery_files WHERE recovery_request_id=app.id ORDER BY file_id LOOP
        SELECT * INTO file FROM public.files WHERE id=binding.file_id FOR SHARE;
        metadata:=file.metadata_jsonb; completion:=metadata->'completion';
        file_key:='formal-files/v1/stock_loss_evidence/'||substr(replace(file.id::text,'-',''),1,2)||'/'||replace(file.id::text,'-','');
        IF file.id IS NULL OR file.status IS DISTINCT FROM 'available'
           OR file.uploaded_by IS DISTINCT FROM app.actor_user_id
           OR file.storage_key IS DISTINCT FROM file_key
           OR NOT COALESCE(file.sha256 ~ '^[a-f0-9]{64}$' AND file.size_bytes BETWEEN 1 AND 125829120,false)
           OR NOT COALESCE(jsonb_typeof(completion)='object'
                AND completion->>'etag_sha256' ~ '^[a-f0-9]{64}$'
                AND completion->>'head_manifest_sha256' ~ '^[a-f0-9]{64}$'
                AND completion->>'verified_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
                AND metadata->>'idempotency_key_hash' ~ '^[a-f0-9]{64}$',false) THEN
            RAISE EXCEPTION '0165 completed recovery upload required' USING ERRCODE='23514';
        END IF;
        BEGIN verified:=(completion->>'verified_at')::timestamptz;
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RAISE EXCEPTION '0165 recovery upload timestamp invalid' USING ERRCODE='23514';
        END;
        IF NOT COALESCE(file.created_at<=verified AND verified<=app.created_at,false)
           OR binding.created_at IS DISTINCT FROM app.created_at THEN
            RAISE EXCEPTION '0165 recovery upload must precede application' USING ERRCODE='23514';
        END IF;
        IF NOT COALESCE(length(file.original_filename) BETWEEN 1 AND 200
           AND octet_length(file.original_filename)<=255
           AND file.original_filename=normalize(file.original_filename,NFC)
           AND file.original_filename=btrim(file.original_filename)
           AND position('/' IN file.original_filename)=0 AND position(chr(92) IN file.original_filename)=0
           AND EXISTS(SELECT 1 FROM (VALUES ('application/pdf','.pdf'),('image/jpeg','.jpg'),('image/jpeg','.jpeg'),
                ('image/png','.png'),('image/webp','.webp'),('image/heic','.heic'),('image/heif','.heif'),
                ('video/mp4','.mp4'),('video/quicktime','.mov')) allowed(mime,suffix)
                WHERE file.mime_type=allowed.mime AND right(lower(file.original_filename),length(allowed.suffix))=allowed.suffix),false) THEN
            RAISE EXCEPTION '0165 recovery file shape invalid' USING ERRCODE='23514'; END IF;
        expected:=jsonb_build_object('schema','cloud_oam.formal_file_upload_intent.v1',
            'file_id',file.id::text,'storage_key',file_key,'purpose','stock_loss_evidence',
            'provider','aliyun_oss_v2','uploader_user_id',app.actor_user_id,
            'uploader_person_id',app.actor_person_id::text,'authorization_version',app.authorization_version,
            'idempotency_key_hash',metadata->>'idempotency_key_hash',
            'request_sha256',encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
                jsonb_build_object('mime_type',file.mime_type,'original_filename',file.original_filename,
                    'purpose','stock_loss_evidence','sha256',file.sha256,'size_bytes',file.size_bytes)),'UTF8')),'hex'),
            'completion',jsonb_build_object('etag_sha256',completion->>'etag_sha256',
                'head_manifest_sha256',completion->>'head_manifest_sha256','verified_at',completion->>'verified_at'));
        IF public.rsc_canonical_reconciliation_json_0026(metadata) IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected)
           OR binding.metadata_sha256 IS DISTINCT FROM encode(sha256(convert_to(
                public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
            RAISE EXCEPTION '0165 exact recovery upload manifest required' USING ERRCODE='23514';
        END IF;
        -- HEAD may contain extra provider metadata, so prove its exact digest
        -- against the original completed-upload audit rather than guess keys.
        SELECT * INTO creation FROM public.audit_events WHERE aggregate_type='formal_file'
            AND aggregate_id=file.id::text AND action='file.upload_intent.created';
        SELECT * INTO completed FROM public.audit_events WHERE aggregate_type='formal_file'
            AND aggregate_id=file.id::text AND action='file.upload_completed';
        expected:=jsonb_build_object('mime_type',file.mime_type,'purpose','stock_loss_evidence',
            'sha256',file.sha256,'size_bytes',file.size_bytes);
        IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type='formal_file'
                AND aggregate_id=file.id::text AND action='file.upload_intent.created')<>1
           OR (SELECT count(*) FROM public.audit_events WHERE aggregate_type='formal_file'
                AND aggregate_id=file.id::text AND action='file.upload_completed')<>1
           OR NOT COALESCE(creation.id=ANY(audit_ids) AND completed.id=ANY(audit_ids)
                AND creation.actor_user_id=app.actor_user_id AND completed.actor_user_id=app.actor_user_id
                AND creation.occurred_at=file.created_at AND completed.occurred_at=verified
                AND creation.stream_version<completed.stream_version
                AND creation.created_at>=creation.occurred_at AND completed.created_at>=completed.occurred_at
                AND completed.created_at<=app.created_at,false)
           OR NOT (creation.before_jsonb IS NULL OR creation.before_jsonb='null'::jsonb)
           OR public.rsc_canonical_reconciliation_json_0026(creation.after_jsonb) IS DISTINCT FROM
                public.rsc_canonical_reconciliation_json_0026(expected||jsonb_build_object('status','pending'))
           OR completed.before_jsonb IS DISTINCT FROM jsonb_build_object('status','pending')
           OR public.rsc_canonical_reconciliation_json_0026(completed.after_jsonb) IS DISTINCT FROM
                public.rsc_canonical_reconciliation_json_0026(expected||jsonb_build_object('status','available',
                    'head_manifest_sha256',completion->>'head_manifest_sha256'))
           OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE aggregate_type='stock_scrap_recovery_apply'
                AND aggregate_id=app.id::text AND id=ANY(audit_ids) AND stream_version>completed.stream_version) THEN
            RAISE EXCEPTION '0165 original upload creation and completion audits required' USING ERRCODE='23514';
        END IF;
    END LOOP;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_recovery_files_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_recovery_files_0165(checked_request uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_recovery_files_with_audit_0165(checked_request,audit_ids);
END;
$function$;

CREATE FUNCTION public.rsc_check_scrap_recovery_events_with_audit_0165(fact jsonb, stage text, recipient uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE aggregate text:='stock_scrap_recovery_'||stage; kind text:='stock_scrap.recovery_'||stage;
    identifier text:=fact->>'id'; key text; at timestamptz:=(fact->>'created_at')::timestamptz;
    before_state text; after_state text; body jsonb; 
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    IF stage='apply' THEN before_state:='unsubmitted'; after_state:='awaiting_regional';
    ELSIF stage='regional' THEN before_state:='awaiting_regional';
        after_state:=CASE fact->>'decision' WHEN 'verified' THEN 'awaiting_headquarters' WHEN 'needs_evidence' THEN 'needs_evidence' END;
    ELSIF stage='headquarters' THEN before_state:='awaiting_headquarters';
        after_state:=CASE fact->>'decision' WHEN 'approve' THEN 'approved_pending_execution' WHEN 'request_regional_review' THEN 'awaiting_regional' END;
    END IF;
    IF identifier IS NULL OR after_state IS NULL OR recipient IS NULL THEN
        RAISE EXCEPTION '0165 known recovery event stage required' USING ERRCODE='23514'; END IF;
    key:=kind||':'||identifier;
    body:=jsonb_build_object('fact_id',identifier,
        'recovery_request_id',CASE WHEN stage='apply' THEN identifier ELSE fact->>'recovery_request_id' END,
        'scrap_line_id',fact->>'scrap_line_id','stage',stage,'status',after_state,'stock_effect','none',
        'actor_person_id',fact->>'actor_person_id','authorization_version',(fact->>'authorization_version')::bigint,
        'request_id',fact->>'request_id','request_hash',fact->>'request_hash','reason',fact->>'reason',
        'decision',fact->'decision','regional_review_id',CASE WHEN stage='headquarters' THEN fact->>'regional_review_id' END);
    IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.id=ANY(audit_ids)
           AND e.stream_key='inventory' AND e.aggregate_type=aggregate AND e.aggregate_id=identifier
           AND e.actor_user_id=fact->>'actor_user_id' AND e.action=kind AND e.request_id=key
           AND e.before_jsonb='{}'::jsonb AND public.rsc_canonical_reconciliation_json_0026(e.after_jsonb)=public.rsc_canonical_reconciliation_json_0026(body) AND e.created_at=at AND e.occurred_at=at)
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=identifier
           AND e.from_status=before_state AND e.to_status=after_state AND e.actor_id=fact->>'actor_user_id'
           AND e.reason=kind AND e.idempotency_key=key AND e.created_at=at AND e.occurred_at=at AND public.rsc_canonical_reconciliation_json_0026(e.metadata_jsonb)=public.rsc_canonical_reconciliation_json_0026(body))
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=identifier
           AND e.event_type=kind AND e.idempotency_key=key AND e.created_at=at AND public.rsc_canonical_reconciliation_json_0026(e.payload_jsonb)=public.rsc_canonical_reconciliation_json_0026(body))
       OR (SELECT count(*) FROM public.notification_events WHERE business_type=aggregate AND business_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events e WHERE e.business_type=aggregate AND e.business_id=identifier
           AND e.event_type=kind AND e.dedup_key=key AND e.created_at=at AND e.occurred_at=at AND public.rsc_canonical_reconciliation_json_0026(e.payload_jsonb)=public.rsc_canonical_reconciliation_json_0026(body)
           AND e.target_manifest_sha256=encode(sha256(convert_to('notification-person-targets.v1'||chr(10)||recipient::text,'UTF8')),'hex')
           AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=e.id)=1
           AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=e.id AND person_id=recipient AND created_at=at)) THEN
        RAISE EXCEPTION '0165 exact recovery approval event bundle required' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=fact->>'idempotency_key_hash'
        OR (source_document_type=aggregate AND source_document_id=identifier)) THEN
        RAISE EXCEPTION '0165 recovery approval cannot post stock' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_recovery_events_with_audit_0165(jsonb,text,uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_recovery_events_0165(fact jsonb, stage text, recipient uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_recovery_events_with_audit_0165(fact,stage,recipient,audit_ids);
END;
$function$;

CREATE FUNCTION public.rsc_fence_scrap_recovery_events_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE row_value jsonb; candidates jsonb; event_kind text; aggregate text; identifier text; checked_scrap uuid; event public.notification_events%ROWTYPE;
BEGIN
    -- Select OLD on mutation so changing an aggregate cannot hide the original
    -- immutable business binding. Worker delivery/attempt fields remain mutable.
    candidates:=CASE WHEN TG_OP='INSERT' THEN jsonb_build_array(to_jsonb(NEW))
        WHEN TG_OP='DELETE' THEN jsonb_build_array(to_jsonb(OLD))
        ELSE jsonb_build_array(to_jsonb(OLD),to_jsonb(NEW)) END;
    FOR row_value IN SELECT DISTINCT value FROM jsonb_array_elements(candidates) LOOP
    event_kind:=COALESCE(row_value->>'event_type',row_value->>'action');
    IF TG_TABLE_NAME='notification_person_targets' THEN
        SELECT * INTO event FROM public.notification_events WHERE id=(row_value->>'event_id')::uuid;
        aggregate:=event.business_type; identifier:=event.business_id;
    ELSIF TG_TABLE_NAME='notification_events' THEN
        aggregate:=row_value->>'business_type'; identifier:=row_value->>'business_id';
    ELSE aggregate:=row_value->>'aggregate_type'; identifier:=row_value->>'aggregate_id'; END IF;
    IF event_kind IN ('stock_scrap.recovery_apply','stock_scrap.recovery_regional','stock_scrap.recovery_headquarters')
       AND aggregate IS DISTINCT FROM replace(event_kind,'stock_scrap.recovery_','stock_scrap_recovery_') THEN
        RAISE EXCEPTION '0165 recovery event type and aggregate mismatch' USING ERRCODE='23514'; END IF;
    IF aggregate NOT IN ('stock_scrap_recovery_apply','stock_scrap_recovery_regional','stock_scrap_recovery_headquarters')
       OR aggregate IS NULL THEN CONTINUE; END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION '0165 recovery events cannot be deleted' USING ERRCODE='23514'; END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 recovery event writes require read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    IF aggregate='stock_scrap_recovery_apply' THEN
        SELECT scrap_line_id INTO checked_scrap FROM public.stock_scrap_recovery_requests WHERE id::text=identifier;
    ELSIF aggregate='stock_scrap_recovery_regional' THEN
        SELECT scrap_line_id INTO checked_scrap FROM public.stock_scrap_recovery_regional_reviews WHERE id::text=identifier;
    ELSE SELECT scrap_line_id INTO checked_scrap FROM public.stock_scrap_recovery_headquarters_reviews WHERE id::text=identifier; END IF;
    IF checked_scrap IS NULL THEN
        RAISE EXCEPTION '0165 detached recovery event has no request' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_scrap_recovery_approvals_0165(checked_scrap);
    END LOOP;
    RETURN NULL;
END;
$function$;

REVOKE ALL ON FUNCTION public.rsc_check_scrap_recovery_files_0165(uuid),
    public.rsc_check_scrap_recovery_events_0165(jsonb,text,uuid),public.rsc_fence_scrap_recovery_events_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $block$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['audit_events','outbox_events','state_transition_events',
        'notification_events','notification_person_targets'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_recovery_events_0165()',
            'trg_'||name||'_scrap_recovery_0165',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER %I',name,'trg_'||name||'_scrap_recovery_0165');
    END LOOP;
END;
$block$;
