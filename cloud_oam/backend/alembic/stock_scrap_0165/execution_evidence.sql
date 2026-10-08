-- Candidate execution evidence; retains the original upload-audit proof.
CREATE FUNCTION public.rsc_check_scrap_execution_files_with_audit_0165(checked_scrap uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE app record; binding public.stock_scrap_files%ROWTYPE;
    file public.files%ROWTYPE; metadata jsonb; completion jsonb; expected jsonb;
    file_key text; verified timestamptz;  creation public.audit_events%ROWTYPE; completed public.audit_events%ROWTYPE;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.rsc_scrap_recovery_source_0165(checked_scrap);
    SELECT o.*,CASE WHEN l.source_kind='original' THEN r.executor_person_id ELSE c.actor_person_id END AS actor_person_id
        INTO app FROM public.stock_scrap_lines l JOIN public.stock_operation_orders o ON o.id=l.operation_id
        JOIN public.stock_loss_dispositions r ON r.id=l.root_disposition_id
        LEFT JOIN public.stock_loss_correction_executions c ON c.id=l.correction_execution_id WHERE l.id=checked_scrap;
    IF app.id IS NULL THEN RAISE EXCEPTION '0165 scrap execution required for files' USING ERRCODE='23514'; END IF;
    FOR binding IN SELECT * FROM public.stock_scrap_files WHERE scrap_line_id=checked_scrap ORDER BY file_id LOOP
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
            RAISE EXCEPTION '0165 completed scrap upload required' USING ERRCODE='23514';
        END IF;
        BEGIN verified:=(completion->>'verified_at')::timestamptz;
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RAISE EXCEPTION '0165 scrap upload timestamp invalid' USING ERRCODE='23514';
        END;
        IF NOT COALESCE(file.created_at<=verified AND verified<=app.created_at,false)
           OR binding.created_at IS DISTINCT FROM app.created_at THEN
            RAISE EXCEPTION '0165 scrap upload must precede execution' USING ERRCODE='23514';
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
            RAISE EXCEPTION '0165 scrap file shape invalid' USING ERRCODE='23514'; END IF;
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
            RAISE EXCEPTION '0165 exact scrap upload manifest required' USING ERRCODE='23514';
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
           OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE aggregate_type='stock_operation_scrap'
                AND aggregate_id=app.id::text AND id=ANY(audit_ids) AND stream_version>completed.stream_version) THEN
            RAISE EXCEPTION '0165 original upload creation and completion audits required' USING ERRCODE='23514';
        END IF;
    END LOOP;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_execution_files_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_execution_files_0165(checked_scrap uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_execution_files_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;

REVOKE ALL ON FUNCTION public.rsc_check_scrap_execution_files_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Posting-bundle definitions retain the frozen 0159 canonical encodings.
CREATE FUNCTION public.rsc_check_scrap_posting_events_with_audit_0165(fact jsonb, is_inverse boolean, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE tx public.inventory_transactions%ROWTYPE; at timestamptz; suffix text; kind text; reference text;
     inventory_body jsonb; state_body jsonb; outbox_body jsonb;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
        SELECT * INTO tx FROM public.inventory_transactions WHERE id=(fact->>'posting_transaction_id')::uuid;
        at:=tx.posted_at;suffix:=CASE WHEN is_inverse THEN 'reversed' ELSE 'posted' END;
        kind:='inventory.transaction.'||suffix;
        IF tx.posted_at IS NULL OR tx.created_at IS DISTINCT FROM tx.posted_at
           OR tx.idempotency_key_hash IS DISTINCT FROM fact->>'idempotency_key_hash'
           OR (fact->>'created_at')::timestamptz>tx.posted_at
           OR NOT EXISTS(SELECT 1 FROM public.inventory_movements WHERE id=(fact->>'posting_movement_id')::uuid AND created_at=at)
           OR EXISTS(SELECT 1 FROM public.inventory_movement_serials WHERE movement_id=(fact->>'posting_movement_id')::uuid
                AND created_at IS DISTINCT FROM at) THEN
            RAISE EXCEPTION '0165 exact posting time and request key required' USING ERRCODE='23514'; END IF;
        reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(fact->>'request_id','UTF8')),'hex');
        inventory_body:=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_count',1,'movement_type',tx.movement_type,
            'posting_key',tx.posting_key,'reversed_transaction_id',tx.reversed_transaction_id::text,'status','posted');
        state_body:=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type',tx.movement_type,'request_reference',reference);
        outbox_body:=jsonb_build_object('transaction_id',tx.id::text,'transaction_no',tx.transaction_no,'movement_type',tx.movement_type,
            'ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',tx.reversed_transaction_id::text);
        IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
           OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.id=ANY(audit_ids) AND e.stream_key='inventory'
                AND e.aggregate_type='inventory_transaction' AND e.aggregate_id=tx.id::text AND e.action=kind
                AND e.actor_user_id=tx.actor_user_id AND e.request_id=reference
                AND (e.before_jsonb IS NULL OR e.before_jsonb='null'::jsonb) AND e.created_at=at AND e.occurred_at=at
                AND public.rsc_canonical_reconciliation_json_0026(e.after_jsonb)=public.rsc_canonical_reconciliation_json_0026(inventory_body))
           OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
           OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type='inventory_transaction' AND e.aggregate_id=tx.id::text
                AND e.from_status IS NULL AND e.to_status='posted' AND e.actor_id=tx.actor_user_id AND e.reason='inventory_transaction_'||suffix
                AND e.idempotency_key='inventory-state-'||encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8')||decode('00','hex')||convert_to(tx.id::text,'UTF8')||decode('00','hex')||convert_to(suffix,'UTF8')),'hex')
                AND e.created_at=at AND e.occurred_at=at
                AND public.rsc_canonical_reconciliation_json_0026(e.metadata_jsonb)=public.rsc_canonical_reconciliation_json_0026(state_body))
           OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text)<>1
           OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type='inventory_transaction' AND e.aggregate_id=tx.id::text
                AND e.event_type=kind AND e.idempotency_key='inventory-outbox-'||encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8')||decode('00','hex')||convert_to(tx.id::text,'UTF8')||decode('00','hex')||convert_to(suffix,'UTF8')),'hex')
                AND e.created_at=at
                AND public.rsc_canonical_reconciliation_json_0026(e.payload_jsonb)=public.rsc_canonical_reconciliation_json_0026(outbox_body)) THEN
            RAISE EXCEPTION '0165 exact canonical inventory posting bundle required' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_posting_events_with_audit_0165(jsonb,boolean,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_posting_events_0165(fact jsonb, is_inverse boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_posting_events_with_audit_0165(fact,is_inverse,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_posting_events_0165(jsonb,boolean)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_assert_scrap_domain_events_with_audit_0165(fact jsonb, aggregate text, kind text, body jsonb, recipient uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE identifier text:=fact->>'id'; at timestamptz:=(fact->>'created_at')::timestamptz;
    key text:=kind||':'||identifier; 
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    IF identifier IS NULL OR aggregate NOT IN ('stock_loss_disposition','stock_loss_correction_execution',
        'stock_loss_disposition_reversal','stock_operation_scrap','stock_scrap_recovery_execution') THEN
        RAISE EXCEPTION '0165 explicit scrap event coordinates required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.id=ANY(audit_ids) AND e.stream_key='inventory'
            AND e.aggregate_type=aggregate AND e.aggregate_id=identifier AND e.action=kind
            AND e.actor_user_id=fact->>'actor_user_id' AND e.request_id=fact->>'request_id'
            AND e.before_jsonb='{}'::jsonb AND e.created_at=at AND e.occurred_at=at
            AND public.rsc_canonical_reconciliation_json_0026(e.after_jsonb)=public.rsc_canonical_reconciliation_json_0026(body))
       OR (SELECT count(*) FROM public.state_transition_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=identifier
            AND e.from_status='pending' AND e.to_status='posted' AND e.actor_id=fact->>'actor_user_id'
            AND e.reason=kind AND e.idempotency_key=key AND e.created_at=at AND e.occurred_at=at
            AND public.rsc_canonical_reconciliation_json_0026(e.metadata_jsonb)=public.rsc_canonical_reconciliation_json_0026(body))
       OR (SELECT count(*) FROM public.outbox_events WHERE aggregate_type=aggregate AND aggregate_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.outbox_events e WHERE e.aggregate_type=aggregate AND e.aggregate_id=identifier
            AND e.event_type=kind AND e.idempotency_key=key AND e.created_at=at
            AND public.rsc_canonical_reconciliation_json_0026(e.payload_jsonb)=public.rsc_canonical_reconciliation_json_0026(body)) THEN
        RAISE EXCEPTION '0165 exact scrap domain event bundle required' USING ERRCODE='23514'; END IF;
    IF recipient IS NULL THEN
        IF EXISTS(SELECT 1 FROM public.notification_events WHERE business_type=aggregate AND business_id=identifier) THEN
            RAISE EXCEPTION '0165 scrap child cannot duplicate parent notification' USING ERRCODE='23514'; END IF;
    ELSIF (SELECT count(*) FROM public.notification_events WHERE business_type=aggregate AND business_id=identifier)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events e WHERE e.business_type=aggregate AND e.business_id=identifier
            AND e.event_type=kind AND e.dedup_key=key AND e.created_at=at AND e.occurred_at=at
            AND public.rsc_canonical_reconciliation_json_0026(e.payload_jsonb)=public.rsc_canonical_reconciliation_json_0026(body)
            AND e.target_manifest_sha256=encode(sha256(convert_to('notification-person-targets.v1'||chr(10)||recipient::text,'UTF8')),'hex')
            AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=e.id)=1
            AND EXISTS(SELECT 1 FROM public.notification_person_targets WHERE event_id=e.id AND person_id=recipient AND created_at=at)) THEN
        RAISE EXCEPTION '0165 exact single scrap notification intent required' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_assert_scrap_domain_events_with_audit_0165(jsonb,text,text,jsonb,uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_assert_scrap_domain_events_0165(fact jsonb, aggregate text, kind text, body jsonb, recipient uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_assert_scrap_domain_events_with_audit_0165(fact,aggregate,kind,body,recipient,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_assert_scrap_domain_events_0165(jsonb,text,text,jsonb,uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_check_scrap_execution_events_with_audit_0165(checked_scrap uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; root public.stock_loss_dispositions%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE; header public.stock_operation_orders%ROWTYPE;
    recovery public.stock_scrap_recovery_executions%ROWTYPE; inverse public.stock_loss_disposition_reversals%ROWTYPE;
    fact jsonb; common jsonb; body jsonb; aggregate text; kind text; item record;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_scrap_historical_plans_with_audit_0165(checked_scrap,audit_ids);
    PERFORM public.rsc_check_scrap_execution_files_with_audit_0165(checked_scrap,audit_ids);
    SELECT * INTO line FROM public.stock_scrap_lines WHERE id=checked_scrap;
    SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=line.root_disposition_id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=root.operation_id;
    SELECT * INTO header FROM public.stock_operation_orders WHERE id=line.operation_id;
    FOR item IN
        SELECT 'original' AS category,to_jsonb(r) AS fact FROM public.stock_loss_dispositions r
            WHERE line.source_kind='original' AND id=root.id
        UNION ALL SELECT 'correction',to_jsonb(c) FROM public.stock_loss_correction_executions c WHERE id=line.correction_execution_id
        UNION ALL SELECT 'inverse',to_jsonb(i) FROM public.stock_loss_disposition_reversals i
            JOIN public.stock_scrap_recovery_executions r ON r.reversal_id=i.id WHERE r.scrap_line_id=line.id
    LOOP
        fact:=item.fact;
        IF item.category='original' THEN
            aggregate:='stock_loss_disposition';kind:='stock_loss.disposition_posted';
            body:=jsonb_build_object('disposition_id',root.id::text,'operation_id',root.operation_id::text,'line_id',root.line_id::text,
                'headquarters_decision_id',root.headquarters_decision_id::text,'disposition','scrap','executor_person_id',root.executor_person_id::text,
                'authorization_version',root.authorization_version,'posting_transaction_id',root.posting_transaction_id::text,
                'posting_movement_id',root.posting_movement_id::text,'quantity',to_char(root.quantity,'FM999999999999990.000'),
                'source_account_id',root.source_account_id::text,'target_account_id',NULL,
                'request_id',root.request_id,'request_hash',root.request_hash,'plan_hash',root.plan_hash,'status','posted');
        ELSE
            common:=jsonb_build_object('root_disposition_id',root.id::text,'operation_id',parent.id::text,
                'line_id',root.line_id::text,'original_headquarters_decision_id',root.headquarters_decision_id::text,
                'requester_person_id',parent.requester_id::text,'actor_user_id',fact->>'actor_user_id',
                'actor_person_id',fact->>'actor_person_id','authorization_version',(fact->>'authorization_version')::bigint,
                'reason',fact->>'reason','request_id',fact->>'request_id','request_hash',fact->>'request_hash',
                'posting_transaction_id',fact->>'posting_transaction_id','posting_movement_id',fact->>'posting_movement_id',
                'source_account_id',fact->'source_account_id','target_account_id',fact->'target_account_id',
                'quantity',to_char((fact->>'quantity')::numeric,'FM999999999999990.000'),'plan_hash',fact->>'plan_hash','status','posted');
            IF item.category='inverse' THEN
                aggregate:='stock_loss_disposition_reversal';kind:='stock_loss.disposition_reversed';
                body:=common||jsonb_build_object('reversal_id',fact->>'id',
                    'original_execution_id',COALESCE(fact->>'reversed_correction_id',root.id::text),
                    'reversed_correction_id',fact->'reversed_correction_id','original_transaction_id',fact->>'original_transaction_id',
                    'original_movement_id',fact->>'original_movement_id','stock_effect','restores_original_frozen_share');
            ELSE
                aggregate:='stock_loss_correction_execution';kind:='stock_loss.correction_posted';
                body:=common||jsonb_build_object('correction_execution_id',fact->>'id','correction_decision_id',fact->>'correction_decision_id',
                    'reversal_id',fact->>'reversal_id','disposition','scrap','return_operation_id',NULL,
                    'return_fulfillment_required',false,'stock_effect','removed_from_managed_assets');
            END IF;
        END IF;
        PERFORM public.rsc_assert_scrap_domain_events_with_audit_0165(fact,aggregate,kind,body,parent.requester_id,audit_ids);
        PERFORM public.rsc_check_scrap_posting_events_with_audit_0165(fact,item.category='inverse',audit_ids);
    END LOOP;
    body:=jsonb_build_object('scrap_operation_id',header.id::text,'scrap_line_id',line.id::text,
        'source_kind',line.source_kind,'root_disposition_id',root.id::text,'correction_execution_id',line.correction_execution_id::text,
        'posting_transaction_id',line.posting_transaction_id::text,'posting_movement_id',line.posting_movement_id::text,
        'quantity',to_char(line.quantity,'FM999999999999990.000'),'source_account_id',line.frozen_account_id::text,
        'target_account_id',NULL,'status','posted','stock_effect','removed_from_managed_assets',
        'request_id',header.request_id,'request_hash',header.request_hash,'plan_hash',header.plan_hash);
    PERFORM public.rsc_assert_scrap_domain_events_with_audit_0165(to_jsonb(header),'stock_operation_scrap','stock_scrap.posted',body,NULL,audit_ids);
    SELECT * INTO recovery FROM public.stock_scrap_recovery_executions WHERE scrap_line_id=line.id;
    IF recovery.id IS NOT NULL THEN
        SELECT * INTO inverse FROM public.stock_loss_disposition_reversals WHERE id=recovery.reversal_id;
        body:=jsonb_build_object('recovery_execution_id',recovery.id::text,'recovery_request_id',recovery.recovery_request_id::text,
            'headquarters_review_id',recovery.headquarters_review_id::text,'scrap_line_id',line.id::text,'reversal_id',inverse.id::text,
            'posting_transaction_id',inverse.posting_transaction_id::text,'request_id',inverse.request_id,
            'request_hash',inverse.request_hash,'plan_hash',inverse.plan_hash,'status','posted','stock_effect','restores_original_frozen_share');
        PERFORM public.rsc_assert_scrap_domain_events_with_audit_0165(to_jsonb(recovery),'stock_scrap_recovery_execution','stock_scrap.recovered',body,NULL,audit_ids);
    END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_execution_events_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_execution_events_0165(checked_scrap uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_execution_events_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_execution_events_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_fence_scrap_execution_evidence_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE candidate jsonb; candidates jsonb; identifiers text[]:=ARRAY[]::text[]; aggregate text; identifier text;
    event_kind text; checked uuid; related uuid[]; event public.notification_events%ROWTYPE;
BEGIN
    candidates:=CASE WHEN TG_OP='INSERT' THEN jsonb_build_array(to_jsonb(NEW))
        WHEN TG_OP='DELETE' THEN jsonb_build_array(to_jsonb(OLD)) ELSE jsonb_build_array(to_jsonb(OLD),to_jsonb(NEW)) END;
    FOR candidate IN SELECT DISTINCT value FROM jsonb_array_elements(candidates) LOOP
        related:=ARRAY[]::uuid[];
        IF TG_TABLE_NAME IN ('audit_events','outbox_events','state_transition_events','notification_events','notification_person_targets') THEN
            event_kind:=COALESCE(candidate->>'event_type',candidate->>'action');
            IF TG_TABLE_NAME='notification_person_targets' THEN
                SELECT * INTO event FROM public.notification_events WHERE id=(candidate->>'event_id')::uuid;
                aggregate:=event.business_type;identifier:=event.business_id;
            ELSIF TG_TABLE_NAME='notification_events' THEN
                aggregate:=candidate->>'business_type';identifier:=candidate->>'business_id';
            ELSE aggregate:=candidate->>'aggregate_type';identifier:=candidate->>'aggregate_id'; END IF;
            IF (event_kind='stock_scrap.posted' AND aggregate IS DISTINCT FROM 'stock_operation_scrap')
                OR (event_kind='stock_scrap.recovered' AND aggregate IS DISTINCT FROM 'stock_scrap_recovery_execution') THEN
                RAISE EXCEPTION '0165 scrap execution event kind and aggregate mismatch' USING ERRCODE='23514'; END IF;
            SELECT COALESCE(array_agg(DISTINCT l.id),ARRAY[]::uuid[]) INTO related
                FROM public.stock_scrap_lines l LEFT JOIN public.stock_scrap_recovery_executions r ON r.scrap_line_id=l.id
                LEFT JOIN public.stock_loss_disposition_reversals i ON i.id=r.reversal_id
                WHERE (aggregate='stock_operation_scrap' AND identifier=l.operation_id::text)
                    OR (aggregate='stock_scrap_recovery_execution' AND identifier=r.id::text)
                    OR (aggregate='stock_loss_disposition' AND identifier=l.root_disposition_id::text)
                    OR (aggregate='stock_loss_correction_execution' AND identifier=l.correction_execution_id::text)
                    OR (aggregate='stock_loss_disposition_reversal' AND identifier=i.id::text)
                    OR (aggregate='inventory_transaction' AND identifier IN (l.posting_transaction_id::text,i.posting_transaction_id::text))
                    OR (aggregate='formal_file' AND EXISTS(SELECT 1 FROM public.stock_scrap_files f
                        WHERE f.scrap_line_id=l.id AND f.file_id::text=identifier));
            IF cardinality(related)=0 AND aggregate IN ('stock_operation_scrap','stock_scrap_recovery_execution') THEN
                RAISE EXCEPTION '0165 orphan scrap execution event' USING ERRCODE='23514'; END IF;
        ELSIF TG_TABLE_NAME='audit_chain_heads' THEN
            IF candidate->>'stream_key'='inventory' THEN
                SELECT COALESCE(array_agg(id),ARRAY[]::uuid[]) INTO related FROM public.stock_scrap_lines;
            END IF;
        ELSE
            identifier:=candidate->>'id';
            SELECT COALESCE(array_agg(DISTINCT l.id),ARRAY[]::uuid[]) INTO related
                FROM public.stock_scrap_lines l LEFT JOIN public.stock_scrap_recovery_executions r ON r.scrap_line_id=l.id
                LEFT JOIN public.stock_loss_disposition_reversals i ON i.id=r.reversal_id
                WHERE identifier IN (l.id::text,l.operation_id::text,l.root_disposition_id::text,l.correction_execution_id::text,
                    r.id::text,i.id::text,l.posting_transaction_id::text,i.posting_transaction_id::text)
                    OR candidate->>'scrap_line_id'=l.id::text
                    OR (TG_TABLE_NAME='files' AND EXISTS(SELECT 1 FROM public.stock_scrap_files f
                        WHERE f.scrap_line_id=l.id AND f.file_id::text=identifier));
        END IF;
        IF cardinality(related)=0 THEN CONTINUE; END IF;
        IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION '0165 scrap execution evidence requires read committed' USING ERRCODE='23514'; END IF;
        PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
        FOREACH checked IN ARRAY related LOOP
            PERFORM public.rsc_check_scrap_execution_events_0165(checked);
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_fence_scrap_execution_evidence_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_lines','stock_scrap_files','stock_operation_orders','stock_scrap_recovery_executions',
        'stock_loss_dispositions','stock_loss_correction_executions','stock_loss_disposition_reversals','inventory_transactions',
        'files','audit_events','audit_chain_heads','outbox_events','state_transition_events','notification_events','notification_person_targets'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_scrap_execution_evidence_0165 AFTER INSERT OR UPDATE OR DELETE ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_execution_evidence_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_scrap_execution_evidence_0165',name);
    END LOOP;
END;
$install$;
