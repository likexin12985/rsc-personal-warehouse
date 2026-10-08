-- Candidate component, not a release migration or a posting authorization.
-- Proves ordered recovery requests against their exact original/corrected scrap.
-- Requires recovery_evidence.sql plus retained file/audit guards. Historical
-- stock/SN, current authority and typed request seals still require proofs.
CREATE FUNCTION public.rsc_check_scrap_recovery_command_0165(fact jsonb, expected jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE n integer;
BEGIN
    IF NOT COALESCE(
        fact->>'request_id' ~ '^[A-Za-z0-9._:-]{8,160}$'
        AND fact->>'request_hash' ~ '^[a-f0-9]{64}$'
        AND fact->>'idempotency_key_hash' ~ '^[a-f0-9]{64}$'
        AND fact->>'actor_user_id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
        AND fact->>'actor_user_id'<>'00000000-0000-0000-0000-000000000000'
        AND fact->>'actor_person_id'<>'00000000-0000-0000-0000-000000000000'
        AND (fact->>'authorization_version')::bigint>0
        AND (fact->>'created_at')::timestamptz<=clock_timestamp()
        AND length(fact->>'reason') BETWEEN 1 AND 500
        AND btrim(fact->>'reason',chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288))=fact->>'reason',false)
       OR EXISTS(SELECT 1 FROM generate_series(1,length(fact->>'reason')) p
           WHERE ascii(substr(fact->>'reason',p,1))<32 AND ascii(substr(fact->>'reason',p,1)) NOT IN (9,10))
       OR fact->'command_jsonb' IS DISTINCT FROM expected
       OR public.rsc_canonical_reconciliation_json_0026(fact->'command_jsonb') IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected)
       OR fact->>'request_hash' IS DISTINCT FROM encode(sha256(convert_to(
           public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0165 exact canonical recovery command required' USING ERRCODE='23514';
    END IF;
    SELECT count(*) INTO n FROM (
        SELECT actor_user_id,request_id,idempotency_key_hash FROM public.stock_scrap_recovery_requests
        UNION ALL SELECT actor_user_id,request_id,idempotency_key_hash FROM public.stock_scrap_recovery_regional_reviews
        UNION ALL SELECT actor_user_id,request_id,idempotency_key_hash FROM public.stock_scrap_recovery_headquarters_reviews
        UNION ALL SELECT actor_user_id,request_id,idempotency_key_hash FROM public.stock_scrap_recovery_executions
    ) r WHERE r.idempotency_key_hash=fact->>'idempotency_key_hash'
        OR (r.actor_user_id=fact->>'actor_user_id' AND r.request_id=fact->>'request_id');
    IF n<>1 THEN
        RAISE EXCEPTION '0165 recovery request coordinates reused' USING ERRCODE='23514';
    END IF;
END;
$function$;

CREATE FUNCTION public.rsc_check_scrap_recovery_approvals_with_audit_0165(checked_scrap uuid, audit_ids uuid[])
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; root public.stock_loss_dispositions%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE; source_fact jsonb; source jsonb; command jsonb;
    app public.stock_scrap_recovery_requests%ROWTYPE;
    region public.stock_scrap_recovery_regional_reviews%ROWTYPE;
    hq public.stock_scrap_recovery_headquarters_reviews%ROWTYPE;
    execution public.stock_scrap_recovery_executions%ROWTYPE;
    stage text; previous_at timestamptz; closed_at timestamptz; evidence jsonb;
    consumed uuid[]; result jsonb:='[]'::jsonb; final_hq uuid; terminal boolean:=false;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    SELECT * INTO line FROM public.stock_scrap_lines WHERE id=checked_scrap;
    SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=line.root_disposition_id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=root.operation_id;
    IF line.source_kind='original' THEN source_fact:=to_jsonb(root);
    ELSIF line.source_kind='correction' THEN
        SELECT to_jsonb(c) INTO source_fact FROM public.stock_loss_correction_executions c
            WHERE c.id=line.correction_execution_id AND c.root_disposition_id=root.id;
    END IF;
    IF line.id IS NULL OR parent.id IS NULL OR source_fact IS NULL
       OR source_fact->>'disposition' IS DISTINCT FROM 'scrap'
       OR source_fact->>'scrap_operation_id' IS DISTINCT FROM line.operation_id::text
       OR source_fact->>'posting_transaction_id' IS DISTINCT FROM line.posting_transaction_id::text
       OR source_fact->>'posting_movement_id' IS DISTINCT FROM line.posting_movement_id::text
       OR source_fact->>'source_account_id' IS DISTINCT FROM line.frozen_account_id::text
       OR (source_fact->>'quantity')::numeric IS DISTINCT FROM line.quantity
       OR source_fact->>'request_hash' !~ '^[a-f0-9]{64}$' THEN
        RAISE EXCEPTION '0165 exact recovery scrap source required' USING ERRCODE='23514';
    END IF;
    source:=jsonb_build_object('scrap_line_id',line.id::text,
        'expected_scrap_request_hash',source_fact->>'request_hash');
    closed_at:=line.created_at;
    FOR app IN SELECT * FROM public.stock_scrap_recovery_requests WHERE scrap_line_id=line.id ORDER BY created_at,id LOOP
        IF terminal OR app.created_at<=closed_at OR app.actor_person_id IS DISTINCT FROM parent.requester_id
           OR app.expected_scrap_request_hash IS DISTINCT FROM source_fact->>'request_hash' THEN
            RAISE EXCEPTION '0165 new application requires closed previous application and exact requester' USING ERRCODE='23514';
        END IF;
        SELECT jsonb_agg(f.file_id::text ORDER BY f.file_id::text) INTO evidence
            FROM public.stock_scrap_recovery_files f WHERE f.recovery_request_id=app.id;
        IF evidence IS NULL OR jsonb_array_length(evidence) NOT BETWEEN 1 AND 20
           OR EXISTS(SELECT 1 FROM public.stock_scrap_recovery_files f WHERE f.recovery_request_id=app.id
                AND (f.created_at<>app.created_at OR f.metadata_sha256 !~ '^[a-f0-9]{64}$')) THEN
            RAISE EXCEPTION '0165 exact recovery evidence bindings required' USING ERRCODE='23514';
        END IF;
        command:=jsonb_build_object('action','apply_scrap_recovery','source',source,'request_id',app.request_id,
            'reason',app.reason,'evidence_file_ids',evidence);
        PERFORM public.rsc_check_scrap_recovery_command_0165(to_jsonb(app),command);
        PERFORM public.rsc_check_scrap_recovery_files_with_audit_0165(app.id,audit_ids);
        PERFORM public.rsc_check_scrap_recovery_events_with_audit_0165(to_jsonb(app),'apply',app.actor_person_id,audit_ids);
        stage:='awaiting_regional'; previous_at:=app.created_at; consumed:='{}'::uuid[]; final_hq:=NULL;
        FOR region IN SELECT * FROM public.stock_scrap_recovery_regional_reviews
            WHERE recovery_request_id=app.id ORDER BY created_at,id LOOP
            IF stage<>'awaiting_regional' OR region.created_at<=previous_at OR region.scrap_line_id<>line.id
               OR region.actor_user_id=app.actor_user_id OR region.actor_person_id=app.actor_person_id
               OR region.expected_request_hash IS DISTINCT FROM app.request_hash
               OR region.decision NOT IN ('verified','needs_evidence') THEN
                RAISE EXCEPTION '0165 independent ordered regional recovery review required' USING ERRCODE='23514';
            END IF;
            command:=jsonb_build_object('action','review_scrap_recovery_region','source',source,
                'request_id',region.request_id,'reason',region.reason,'recovery_request_id',app.id::text,
                'expected_request_hash',app.request_hash,'decision',region.decision);
            PERFORM public.rsc_check_scrap_recovery_command_0165(to_jsonb(region),command);
            PERFORM public.rsc_check_scrap_recovery_events_with_audit_0165(to_jsonb(region),'regional',app.actor_person_id,audit_ids);
            stage:=CASE region.decision WHEN 'verified' THEN 'awaiting_headquarters' ELSE 'needs_evidence' END;
            previous_at:=region.created_at; final_hq:=NULL;
            SELECT * INTO hq FROM public.stock_scrap_recovery_headquarters_reviews
                WHERE regional_review_id=region.id;
            IF hq.id IS NOT NULL THEN
                IF stage<>'awaiting_headquarters' OR hq.created_at<=previous_at
                   OR hq.recovery_request_id<>app.id OR hq.scrap_line_id<>line.id
                   OR hq.regional_decision IS DISTINCT FROM 'verified'
                   OR hq.actor_user_id IN (app.actor_user_id,region.actor_user_id)
                   OR hq.actor_person_id IN (app.actor_person_id,region.actor_person_id)
                   OR hq.expected_regional_hash IS DISTINCT FROM region.request_hash
                   OR hq.decision NOT IN ('approve','request_regional_review') THEN
                    RAISE EXCEPTION '0165 independent ordered headquarters recovery review required' USING ERRCODE='23514';
                END IF;
                command:=jsonb_build_object('action','review_scrap_recovery_headquarters','source',source,
                    'request_id',hq.request_id,'reason',hq.reason,'recovery_request_id',app.id::text,
                    'expected_request_hash',app.request_hash,'regional_review_id',region.id::text,
                    'expected_regional_hash',region.request_hash,'decision',hq.decision);
                PERFORM public.rsc_check_scrap_recovery_command_0165(to_jsonb(hq),command);
                PERFORM public.rsc_check_scrap_recovery_events_with_audit_0165(to_jsonb(hq),'headquarters',app.actor_person_id,audit_ids);
                stage:=CASE hq.decision WHEN 'approve' THEN 'approved_pending_execution' ELSE 'awaiting_regional' END;
                previous_at:=hq.created_at; final_hq:=hq.id; consumed:=array_append(consumed,hq.id);
            END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM public.stock_scrap_recovery_headquarters_reviews
            WHERE recovery_request_id=app.id AND NOT(id=ANY(consumed))) THEN
            RAISE EXCEPTION '0165 detached headquarters recovery review' USING ERRCODE='23514';
        END IF;
        FOR execution IN SELECT * FROM public.stock_scrap_recovery_executions WHERE recovery_request_id=app.id LOOP
            IF stage<>'approved_pending_execution' OR execution.headquarters_review_id IS DISTINCT FROM final_hq
               OR execution.scrap_line_id<>line.id OR execution.created_at<=previous_at
               OR execution.headquarters_decision IS DISTINCT FROM 'approve'
               OR execution.expected_headquarters_hash IS DISTINCT FROM hq.request_hash
               OR execution.plan_hash !~ '^[a-f0-9]{64}$' THEN
                RAISE EXCEPTION '0165 recovery execution requires exact final independent approval' USING ERRCODE='23514';
            END IF;
            command:=jsonb_build_object('action','execute_scrap_recovery','source',source,
                'request_id',execution.request_id,'reason',execution.reason,'recovery_request_id',app.id::text,
                'expected_request_hash',app.request_hash,'headquarters_review_id',hq.id::text,
                'expected_headquarters_hash',hq.request_hash,'expected_plan_hash',execution.plan_hash);
            PERFORM public.rsc_check_scrap_recovery_command_0165(to_jsonb(execution),command);
            stage:='executed'; previous_at:=execution.created_at;
        END LOOP;
        terminal:=stage<>'needs_evidence'; closed_at:=previous_at;
        result:=result||jsonb_build_array(jsonb_build_object('recovery_request_id',app.id::text,
            'status',stage,'headquarters_review_id',final_hq));
    END LOOP;
    RETURN result;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_recovery_approvals_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_recovery_approvals_0165(checked_scrap uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    RETURN public.rsc_check_scrap_recovery_approvals_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;

CREATE FUNCTION public.rsc_fence_scrap_recovery_approvals_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE checked_scrap uuid;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 recovery writes require read committed' USING ERRCODE='23514';
    END IF;
    -- Same serialization root as inventory posting. Waiting here establishes a
    -- fresh RC snapshot before checking other transactions' stage/key inserts.
    PERFORM 1 FROM public.inventory_ledger_heads FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='stock_scrap_recovery_files' THEN
        SELECT scrap_line_id INTO checked_scrap FROM public.stock_scrap_recovery_requests WHERE id=NEW.recovery_request_id;
    ELSE checked_scrap:=NEW.scrap_line_id;
    END IF;
    PERFORM public.rsc_check_scrap_recovery_approvals_0165(checked_scrap);
    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION public.rsc_check_scrap_recovery_command_0165(jsonb,jsonb),
    public.rsc_check_scrap_recovery_approvals_0165(uuid),
    public.rsc_fence_scrap_recovery_approvals_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_scrap_facts_append_only_0165()
RETURNS trigger LANGUAGE plpgsql SET search_path TO pg_catalog, public
AS $function$
BEGIN
    RAISE EXCEPTION '0165 scrap and recovery facts are append only' USING ERRCODE='23514';
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_facts_append_only_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $block$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_recovery_requests','stock_scrap_recovery_files',
        'stock_scrap_recovery_regional_reviews','stock_scrap_recovery_headquarters_reviews',
        'stock_scrap_recovery_executions'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER %I AFTER INSERT ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '
            'EXECUTE FUNCTION public.rsc_fence_scrap_recovery_approvals_0165()',
            'trg_'||name||'_approval_0165',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER %I',name,'trg_'||name||'_approval_0165');
    END LOOP;
    FOREACH name IN ARRAY ARRAY['stock_scrap_lines','stock_scrap_serials','stock_scrap_files',
        'stock_scrap_recovery_requests','stock_scrap_recovery_files',
        'stock_scrap_recovery_regional_reviews','stock_scrap_recovery_headquarters_reviews',
        'stock_scrap_recovery_executions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165()',
            'trg_'||name||'_immutable_0165',name);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON public.%I '
            'FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165()',
            'trg_'||name||'_truncate_0165',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER %I',name,'trg_'||name||'_immutable_0165');
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER %I',name,'trg_'||name||'_truncate_0165');
    END LOOP;
END;
$block$;
