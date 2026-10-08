-- Controlled candidate persistence. Install only after the exact seal table,
-- canonical/source/current-authority components and predecessor catalog.
CREATE FUNCTION public.rsc_scrap_seal_payload_0165(checked_id uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
    SELECT (to_jsonb(s)-ARRAY['key_token','idempotency_key_hash','reversal_key_hash','approval_key_hash',
        'correction_key_hash','recovery_key_hash','scrap_key_hash'])
        ||jsonb_build_object('request_state','sealed','retry_allowed',false,'stock_effect','none',
            'created_at',to_char(s.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
    FROM public.stock_scrap_request_seals s WHERE id=checked_id;
$function$;

CREATE FUNCTION public.rsc_assert_scrap_seal_absence_0165(checked_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE seal public.stock_scrap_request_seals%ROWTYPE; keys text[]; name text; predicate text;
    column_name text; collision boolean; reference text;
BEGIN
    SELECT * INTO seal FROM public.stock_scrap_request_seals WHERE id=checked_id;
    IF seal.id IS NULL THEN RAISE EXCEPTION '0165 exact retained seal required' USING ERRCODE='23514'; END IF;
    keys:=ARRAY[seal.reversal_key_hash,seal.approval_key_hash,seal.correction_key_hash,seal.recovery_key_hash,seal.scrap_key_hash];
    FOREACH name IN ARRAY ARRAY['stock_loss_request_key_bindings','stock_scrap_request_key_bindings','stock_scrap_request_seals'] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE '
            '(key_token=$1 OR (actor_user_id=$2 AND request_id=$3) OR '
            'reversal_key_hash=ANY($4) OR approval_key_hash=ANY($4) OR correction_key_hash=ANY($4) '
            'OR recovery_key_hash=ANY($4) OR scrap_key_hash=ANY($4))'
            ||CASE WHEN name='stock_scrap_request_seals' THEN ' AND id<>$5' ELSE '' END||')',name)
            INTO collision USING seal.key_token,seal.actor_user_id,seal.request_id,keys,seal.id;
        IF collision THEN RAISE EXCEPTION '0165 sealed key or request has another registry fact' USING ERRCODE='23514'; END IF;
    END LOOP;
    FOREACH name IN ARRAY ARRAY['stock_operation_orders','stock_loss_dispositions','stock_loss_regional_reviews',
        'stock_loss_headquarters_reviews','stock_loss_request_seals','stock_loss_review_request_seals',
        'stock_loss_disposition_request_seals','stock_operation_cancellations','stock_operation_command_seals',
        'stock_operation_outbounds','stock_operation_shipments','stock_operation_receipts','stock_operation_return_inbounds',
        'stock_operation_return_inbound_seals','stock_loss_disposition_reversals','stock_loss_correction_decisions',
        'stock_loss_correction_executions','stock_loss_inverse_request_seals','stock_loss_correction_approval_seals',
        'stock_loss_correction_execution_seals','stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews',
        'stock_scrap_recovery_headquarters_reviews','stock_scrap_recovery_executions'] LOOP
        predicate:='(actor_user_id=$2 AND request_id=$3)';
        -- Resolve fixed, actual columns, retaining indexable equality clauses.
        FOREACH column_name IN ARRAY ARRAY['idempotency_key_hash','reversal_key_hash','approval_key_hash',
            'correction_key_hash','recovery_key_hash','scrap_key_hash'] LOOP
            IF EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=('public.'||name)::regclass
                AND attname=column_name AND attnum>0 AND NOT attisdropped) THEN
                predicate:=predicate||format(' OR %I=ANY($1)',column_name);
            END IF;
        END LOOP;
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE %s)',name,predicate)
            INTO collision USING keys,seal.actor_user_id,seal.request_id;
        IF collision THEN RAISE EXCEPTION '0165 executed or conflicting request cannot be sealed' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=ANY(keys))
       OR EXISTS(SELECT 1 FROM public.shipments WHERE idempotency_key_hash=ANY(keys))
       OR EXISTS(SELECT 1 FROM public.receipts WHERE idempotency_key_hash=ANY(keys)) THEN
        RAISE EXCEPTION '0165 seal aliases contain stock or shipping evidence' USING ERRCODE='23514'; END IF;
    reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')
        ||decode('00','hex')||convert_to(seal.request_id,'UTF8')),'hex');
    IF EXISTS(SELECT 1 FROM public.audit_events e WHERE e.actor_user_id=seal.actor_user_id
        AND e.stream_key IN ('inventory','material_request')
        AND (e.request_id IN (seal.request_id,reference) OR e.after_jsonb->>'request_id'=seal.request_id)
        AND NOT (e.aggregate_type='stock_scrap_request_seal' AND e.aggregate_id=seal.id::text))
       OR EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.actor_id=seal.actor_user_id
        AND (e.metadata_jsonb->>'request_id'=seal.request_id OR e.metadata_jsonb->>'request_reference'=reference)
        AND NOT COALESCE(e.aggregate_type IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
            AND e.metadata_jsonb->>'operation'='formal_authentication_state_transition'
            AND e.metadata_jsonb->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
            AND e.metadata_jsonb->>'request_reference' IS NULL,false)) THEN
        RAISE EXCEPTION '0165 seal request contains detached audit or state evidence' USING ERRCODE='23514'; END IF;
    FOREACH name IN ARRAY ARRAY['outbox_events','notification_events'] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I n WHERE '
            'payload_jsonb->>''request_id''=$1 AND ('
            'payload_jsonb->>''actor_user_id''=$2 OR payload_jsonb->>''actor_person_id''=$3 '
            'OR EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.%I '
                'AND a.aggregate_id=n.%I AND a.actor_user_id=$2) '
            'OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.aggregate_type=n.%I '
                'AND a.aggregate_id=n.%I AND a.actor_user_id<>$2)))',name,
            CASE name WHEN 'outbox_events' THEN 'aggregate_type' ELSE 'business_type' END,
            CASE name WHEN 'outbox_events' THEN 'aggregate_id' ELSE 'business_id' END,
            CASE name WHEN 'outbox_events' THEN 'aggregate_type' ELSE 'business_type' END,
            CASE name WHEN 'outbox_events' THEN 'aggregate_id' ELSE 'business_id' END)
            INTO collision USING seal.request_id,seal.actor_user_id,seal.actor_person_id::text;
        IF collision THEN RAISE EXCEPTION '0165 seal request contains delivery evidence' USING ERRCODE='23514'; END IF;
    END LOOP;
END;
$function$;

CREATE FUNCTION public.rsc_check_scrap_seal_0165(checked_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE seal public.stock_scrap_request_seals%ROWTYPE; prepared jsonb; field text; members uuid[]; payload jsonb;
BEGIN
    SELECT * INTO seal FROM public.stock_scrap_request_seals WHERE id=checked_id;
    IF seal.id IS NULL THEN RAISE EXCEPTION '0165 exact retained seal required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_scrap_seal_source_0165(seal.kind,seal.command_jsonb);
    FOREACH field IN ARRAY ARRAY['kind','command_jsonb','request_id','reason','request_hash','plan_hash',
        'loss_operation_id','loss_line_id','scrap_disposition','root_disposition_id','original_decision_id',
        'reversal_id','correction_decision_id','scrap_line_id','recovery_request_id','regional_review_id',
        'headquarters_review_id','regional_decision','headquarters_decision'] LOOP
        IF to_jsonb(seal)->field IS DISTINCT FROM prepared->field THEN
            RAISE EXCEPTION '0165 retained seal differs from canonical historical source' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF seal.created_at<(prepared->>'source_created_at')::timestamptz OR seal.created_at>clock_timestamp() THEN
        RAISE EXCEPTION '0165 seal time precedes source or is future' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_scrap_seal_absence_0165(seal.id);
    members:=public.rsc_loss_inventory_audit_members_0159();
    payload:=public.rsc_scrap_seal_payload_0165(seal.id);
    IF (SELECT count(*) FROM public.audit_events WHERE aggregate_id=seal.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.id=ANY(members) AND e.stream_key='inventory'
            AND e.aggregate_type='stock_scrap_request_seal' AND e.aggregate_id=seal.id::text
            AND e.action='seal_scrap_request' AND e.actor_user_id=seal.actor_user_id
            AND e.request_id='scrap-seal:'||seal.id::text AND e.before_jsonb='{}'::jsonb
            AND e.created_at=seal.created_at AND e.occurred_at=seal.created_at
            AND public.rsc_canonical_reconciliation_json_0026(e.after_jsonb)=public.rsc_canonical_reconciliation_json_0026(payload))
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_id=seal.id::text)
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_id=seal.id::text)
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE business_id=seal.id::text) THEN
        RAISE EXCEPTION '0165 exactly one seal audit and no stock delivery bundle required' USING ERRCODE='23514'; END IF;
END;
$function$;

CREATE FUNCTION public.rsc_register_scrap_seal_0165(
    actor_id text, actor_version bigint, actor_person uuid, checked_kind text, command jsonb, client_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE prepared jsonb; record_json jsonb; seal public.stock_scrap_request_seals%ROWTYPE; prior public.stock_scrap_request_seals%ROWTYPE;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 seal write requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 seal ledger lock required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    PERFORM 1 FROM public.audit_chain_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 seal audit head required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_prepare_scrap_seal_authority_0165(actor_id,actor_version,actor_person,checked_kind,command,client_key);
    SELECT * INTO prior FROM public.stock_scrap_request_seals WHERE key_token=prepared->>'key_token'
        OR (actor_user_id=actor_id AND request_id=prepared->>'request_id') ORDER BY id LIMIT 1;
    IF prior.id IS NOT NULL THEN
        IF prior.kind IS DISTINCT FROM checked_kind OR prior.actor_user_id IS DISTINCT FROM actor_id
           OR prior.actor_person_id IS DISTINCT FROM actor_person OR prior.command_jsonb IS DISTINCT FROM command
           OR prior.key_token IS DISTINCT FROM prepared->>'key_token' THEN
            RAISE EXCEPTION '0165 exact sealed request conflict' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_scrap_seal_0165(prior.id);
        RETURN jsonb_build_object('seal',to_jsonb(prior),'audit_payload',public.rsc_scrap_seal_payload_0165(prior.id),'created',false);
    END IF;
    record_json:=prepared||jsonb_build_object('id',gen_random_uuid(),'created_at',clock_timestamp(),
        'actor_user_id',actor_id,'actor_person_id',actor_person,'authorization_version',actor_version);
    SELECT * INTO seal FROM jsonb_populate_record(NULL::public.stock_scrap_request_seals,record_json);
    INSERT INTO public.stock_scrap_request_seals SELECT seal.*;
    PERFORM public.rsc_assert_scrap_seal_absence_0165(seal.id);
    RETURN jsonb_build_object('seal',to_jsonb(seal),'audit_payload',public.rsc_scrap_seal_payload_0165(seal.id),'created',true);
END;
$function$;

CREATE FUNCTION public.rsc_fence_scrap_seals_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE raw jsonb:=to_jsonb(NEW); item public.stock_scrap_request_seals%ROWTYPE; keys text[]; body jsonb;
BEGIN
    IF TG_OP<>'INSERT' OR TG_TABLE_SCHEMA<>'public' OR current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 seal insert fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 seal ledger lock required' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME='stock_scrap_request_seals' THEN
        PERFORM public.rsc_check_scrap_seal_0165((raw->>'id')::uuid);
        PERFORM public.rsc_scrap_seal_authority_0165(raw->>'actor_user_id',(raw->>'authorization_version')::bigint,
            (raw->>'actor_person_id')::uuid,raw->>'kind',raw->'command_jsonb');
    ELSIF raw->>'aggregate_type'='stock_scrap_request_seal' OR raw->>'action'='seal_scrap_request' THEN
        PERFORM public.rsc_check_scrap_seal_0165((raw->>'aggregate_id')::uuid);
    END IF;
    keys:=array_remove(ARRAY[raw->>'idempotency_key_hash',raw->>'reversal_key_hash',raw->>'approval_key_hash',
        raw->>'correction_key_hash',raw->>'recovery_key_hash',raw->>'scrap_key_hash'],NULL);
    body:=COALESCE(raw->'after_jsonb',raw->'metadata_jsonb',raw->'payload_jsonb','{}'::jsonb);
    FOR item IN SELECT * FROM public.stock_scrap_request_seals s WHERE s.key_token=raw->>'key_token'
        OR (s.actor_user_id=raw->>'actor_user_id' AND s.request_id=raw->>'request_id')
        OR s.reversal_key_hash=ANY(keys) OR s.approval_key_hash=ANY(keys) OR s.correction_key_hash=ANY(keys)
        OR s.recovery_key_hash=ANY(keys) OR s.scrap_key_hash=ANY(keys)
        OR s.id::text=raw->>'aggregate_id' OR s.id::text=raw->>'business_id'
        OR s.request_id=body->>'request_id' OR s.id::text=body->>'id'
        OR (raw->>'actor_user_id'=s.actor_user_id AND raw->>'request_id'='inventory-request-'||
            encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')
                ||convert_to(s.request_id,'UTF8')),'hex'))
        OR body->>'request_reference'='inventory-request-'||encode(sha256(
            convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(s.request_id,'UTF8')),'hex')
    LOOP PERFORM public.rsc_check_scrap_seal_0165(item.id); END LOOP;
    RETURN NULL;
END;
$function$;

REVOKE ALL ON FUNCTION public.rsc_scrap_seal_payload_0165(uuid),public.rsc_assert_scrap_seal_absence_0165(uuid),
    public.rsc_check_scrap_seal_0165(uuid),public.rsc_fence_scrap_seals_0165(),
    public.rsc_register_scrap_seal_0165(text,bigint,uuid,text,jsonb,text)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
REVOKE ALL ON public.stock_scrap_request_seals
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
GRANT SELECT ON public.stock_scrap_request_seals TO star_oam_api,star_oam_backup;
GRANT EXECUTE ON FUNCTION public.rsc_register_scrap_seal_0165(text,bigint,uuid,text,jsonb,text) TO star_oam_api;
CREATE TRIGGER trg_scrap_seal_immutable_0165 BEFORE UPDATE OR DELETE ON public.stock_scrap_request_seals
FOR EACH ROW EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165();
CREATE TRIGGER trg_scrap_seal_no_truncate_0165 BEFORE TRUNCATE ON public.stock_scrap_request_seals
FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165();
ALTER TABLE public.stock_scrap_request_seals ENABLE ALWAYS TRIGGER trg_scrap_seal_immutable_0165;
ALTER TABLE public.stock_scrap_request_seals ENABLE ALWAYS TRIGGER trg_scrap_seal_no_truncate_0165;
DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_request_seals','stock_scrap_request_key_bindings','stock_loss_request_key_bindings',
        'stock_operation_orders','stock_loss_dispositions','stock_loss_regional_reviews','stock_loss_headquarters_reviews',
        'stock_loss_request_seals','stock_loss_review_request_seals','stock_loss_disposition_request_seals',
        'stock_operation_cancellations','stock_operation_command_seals','stock_operation_outbounds','stock_operation_shipments',
        'stock_operation_receipts','stock_operation_return_inbounds','stock_operation_return_inbound_seals',
        'stock_loss_disposition_reversals','stock_loss_correction_decisions','stock_loss_correction_executions',
        'stock_loss_inverse_request_seals','stock_loss_correction_approval_seals','stock_loss_correction_execution_seals',
        'stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews','stock_scrap_recovery_headquarters_reviews',
        'stock_scrap_recovery_executions','inventory_transactions','shipments','receipts',
        'audit_events','state_transition_events','outbox_events','notification_events'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_scrap_seal_fence_0165 AFTER INSERT ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_seals_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_scrap_seal_fence_0165',name);
    END LOOP;
END;
$install$;
