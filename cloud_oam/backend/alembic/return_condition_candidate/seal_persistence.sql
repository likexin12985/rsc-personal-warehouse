-- Keep authentication outside inventory serialization, matching the formal
-- request-evidence contract. Reserved seal markers are never exempt.
CREATE FUNCTION public.rsc_condition_seal_relevant(table_name text, raw jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
    SELECT CASE
        WHEN raw IS NULL THEN false
        WHEN raw->>'aggregate_type'='stock_condition_request_seal'
          OR raw->>'business_type'='stock_condition_request_seal'
          OR raw->>'action'='seal_condition_request' OR raw->>'request_id' LIKE 'condition-seal:%' THEN true
        WHEN table_name='audit_events' AND raw->>'stream_key' NOT IN ('inventory','material_request') THEN false
        WHEN table_name='state_transition_events' AND COALESCE(
            raw->>'aggregate_type' IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
            AND raw->'metadata_jsonb'->>'operation'='formal_authentication_state_transition'
            AND raw->'metadata_jsonb'->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
            AND raw->'metadata_jsonb'->>'request_reference' IS NULL,false) THEN false
        ELSE true END;
$$;

CREATE FUNCTION public.rsc_condition_seal_payload(checked_id uuid)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
    SELECT (to_jsonb(s)-ARRAY['key_token','idempotency_key_hash',__ALIASES__])
        ||jsonb_build_object('request_state','sealed','retry_allowed',false,'stock_effect','none',
            'created_at',to_char(s.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
    FROM public.stock_condition_request_seals s WHERE id=checked_id;
$$;

CREATE FUNCTION public.rsc_condition_seal_absence(checked_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.stock_condition_request_seals%ROWTYPE; keys text[]; reference text;
BEGIN
    SELECT * INTO s FROM public.stock_condition_request_seals WHERE id=checked_id;
    IF s.id IS NULL THEN RAISE EXCEPTION 'condition exact seal required' USING ERRCODE='23514'; END IF;
    SELECT array_agg(value) INTO keys FROM jsonb_each_text(to_jsonb(s)) WHERE key=ANY(ARRAY[__ALIASES__]);
    __COLLISION_CHECKS__
    reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')
        ||decode('00','hex')||convert_to(s.request_id,'UTF8')),'hex');
    IF EXISTS(SELECT 1 FROM public.audit_events a WHERE a.actor_user_id=s.actor_user_id
        AND a.stream_key IN ('inventory','material_request')
        AND (a.request_id IN (s.request_id,reference) OR a.after_jsonb->>'request_id'=s.request_id
             OR a.after_jsonb->>'request_reference'=reference)
        AND NOT (a.aggregate_type='stock_condition_request_seal' AND a.aggregate_id=s.id::text))
       OR EXISTS(SELECT 1 FROM public.state_transition_events a WHERE a.actor_id=s.actor_user_id
        AND (a.metadata_jsonb->>'request_id'=s.request_id OR a.metadata_jsonb->>'request_reference'=reference)
        AND NOT COALESCE(a.aggregate_type IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
            AND a.metadata_jsonb->>'operation'='formal_authentication_state_transition'
            AND a.metadata_jsonb->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
            AND a.metadata_jsonb->>'request_reference' IS NULL,false)) THEN
        RAISE EXCEPTION 'condition sealed request has detached audit or state' USING ERRCODE='23514'; END IF;
    __DELIVERY_CHECKS__
END $$;

CREATE FUNCTION public.rsc_check_condition_seal(checked_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE s public.stock_condition_request_seals%ROWTYPE; prepared jsonb; key text; members uuid[]; body jsonb;
BEGIN
    SELECT * INTO s FROM public.stock_condition_request_seals WHERE id=checked_id;
    IF s.id IS NULL THEN RAISE EXCEPTION 'condition exact seal required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_condition_seal_source(s.command_jsonb);
    FOREACH key IN ARRAY ARRAY['kind','command_jsonb','request_id','reason','request_hash','idempotency_key_hash',
        'root_disposition_id','inbound_id','inbound_line_id','source_account_id','original_transaction_id',
        'original_movement_id','original_ledger_cursor'] LOOP
        IF to_jsonb(s)->key IS DISTINCT FROM prepared->key THEN
            RAISE EXCEPTION 'condition retained seal differs from exact source' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF s.actor_person_id IS DISTINCT FROM (prepared->>'requester_id')::uuid
       OR s.created_at<(prepared->>'source_created_at')::timestamptz OR s.created_at>clock_timestamp() THEN
        RAISE EXCEPTION 'condition seal source person and historical time required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_condition_seal_absence(s.id);
    body:=public.rsc_condition_seal_payload(s.id);
    members:=public.rsc_loss_inventory_audit_members_0159();
    IF (SELECT count(*) FROM public.audit_events a WHERE (aggregate_id=s.id::text OR request_id='condition-seal:'||s.id::text)
        AND public.rsc_condition_seal_relevant('audit_events',to_jsonb(a)))<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.id=ANY(members) AND a.stream_key='inventory'
            AND a.aggregate_type='stock_condition_request_seal' AND a.aggregate_id=s.id::text
            AND a.action='seal_condition_request' AND a.actor_user_id=s.actor_user_id
            AND a.request_id='condition-seal:'||s.id::text AND a.before_jsonb='{}'::jsonb
            AND a.created_at=s.created_at AND a.occurred_at=s.created_at
            AND public.rsc_canonical_reconciliation_json_0026(a.after_jsonb)=public.rsc_canonical_reconciliation_json_0026(body))
       OR EXISTS(SELECT 1 FROM public.state_transition_events a WHERE aggregate_id=s.id::text
            AND public.rsc_condition_seal_relevant('state_transition_events',to_jsonb(a)))
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_id=s.id::text)
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE business_id=s.id::text)
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE source_document_id=s.id::text) THEN
        RAISE EXCEPTION 'condition seal requires one audit and no business effects' USING ERRCODE='23514'; END IF;
END $$;

CREATE FUNCTION public.rsc_register_condition_seal(actor_id text, actor_version bigint, actor_person uuid, command jsonb, client_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE prepared jsonb; aliases jsonb; token text; prior public.stock_condition_request_seals%ROWTYPE;
    s public.stock_condition_request_seals%ROWTYPE; raw jsonb;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' OR client_key IS NULL
       OR client_key !~ '^[A-Za-z0-9._:-]{8,200}$' THEN
        RAISE EXCEPTION 'condition seal real client key and read committed required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition seal ledger head required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    PERFORM 1 FROM public.audit_chain_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition seal audit head required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_condition_seal_canonical(command);
    SELECT jsonb_object_agg(name,encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')
        ||convert_to(prefix||client_key,'UTF8')),'hex')) INTO aliases FROM (VALUES __KEY_DOMAINS__) d(name,prefix);
    token:=encode(sha256(convert_to('cloud_oam.loss.correction.key.v1','UTF8')||decode('00','hex')||convert_to(client_key,'UTF8')),'hex');
    IF prepared->>'idempotency_key_hash' IS DISTINCT FROM aliases->>'condition_key_hash' THEN
        RAISE EXCEPTION 'condition seal original key does not match input' USING ERRCODE='23514'; END IF;
    SELECT * INTO prior FROM public.stock_condition_request_seals WHERE key_token=token
        OR (actor_user_id=actor_id AND request_id=prepared->>'request_id') ORDER BY id LIMIT 1;
    IF prior.id IS NOT NULL THEN
        IF prior.actor_user_id IS DISTINCT FROM actor_id OR prior.actor_person_id IS DISTINCT FROM actor_person
           OR prior.command_jsonb IS DISTINCT FROM command OR prior.key_token IS DISTINCT FROM token
           OR (SELECT jsonb_object_agg(key,value) FROM jsonb_each(to_jsonb(prior)) WHERE key=ANY(ARRAY[__ALIASES__])) IS DISTINCT FROM aliases THEN
            RAISE EXCEPTION 'condition sealed original request conflict' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_condition_seal_authority(actor_id,actor_version,actor_person,command,false);
        PERFORM public.rsc_check_condition_seal(prior.id);
        RETURN jsonb_build_object('seal',to_jsonb(prior),'created',false,'audit_payload',public.rsc_condition_seal_payload(prior.id));
    END IF;
    prepared:=public.rsc_condition_seal_authority(actor_id,actor_version,actor_person,command,true);
    raw:=prepared||aliases||jsonb_build_object('id',gen_random_uuid(),'created_at',clock_timestamp(),
        'actor_user_id',actor_id,'actor_person_id',actor_person,'authorization_version',actor_version,'key_token',token);
    SELECT * INTO s FROM jsonb_populate_record(NULL::public.stock_condition_request_seals,raw);
    INSERT INTO public.stock_condition_request_seals SELECT s.*;
    PERFORM public.rsc_condition_seal_absence(s.id);
    RETURN jsonb_build_object('seal',to_jsonb(s),'created',true,'audit_payload',public.rsc_condition_seal_payload(s.id));
END $$;

CREATE FUNCTION public.rsc_condition_seal_lock()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE prior jsonb; next_row jsonb;
BEGIN
    IF TG_OP<>'INSERT' THEN prior:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN next_row:=to_jsonb(NEW); END IF;
    IF NOT public.rsc_condition_seal_relevant(TG_TABLE_NAME,prior)
       AND NOT public.rsc_condition_seal_relevant(TG_TABLE_NAME,next_row) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'condition seal fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition seal fence ledger head required' USING ERRCODE='23514'; END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_seal_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE raw jsonb; body jsonb; keys text[]; matched uuid;
BEGIN
    FOR raw IN SELECT value FROM jsonb_array_elements(
        CASE WHEN TG_OP='INSERT' THEN jsonb_build_array(to_jsonb(NEW))
             WHEN TG_OP='DELETE' THEN jsonb_build_array(to_jsonb(OLD))
             ELSE jsonb_build_array(to_jsonb(OLD),to_jsonb(NEW)) END) LOOP
        IF NOT public.rsc_condition_seal_relevant(TG_TABLE_NAME,raw) THEN CONTINUE; END IF;
        IF TG_TABLE_NAME='stock_condition_request_seals' THEN
            PERFORM public.rsc_check_condition_seal((raw->>'id')::uuid);
            PERFORM public.rsc_condition_seal_authority(raw->>'actor_user_id',(raw->>'authorization_version')::bigint,
                (raw->>'actor_person_id')::uuid,raw->'command_jsonb',true);
        -- The derived audit request namespace is internal. Ordinary business
        -- request IDs use the public safe-string contract, including colons.
        ELSIF raw->>'aggregate_type'='stock_condition_request_seal' OR raw->>'business_type'='stock_condition_request_seal'
           OR raw->>'action'='seal_condition_request'
           OR (TG_TABLE_NAME='audit_events' AND raw->>'request_id' LIKE 'condition-seal:%') THEN
            IF COALESCE(raw->>'aggregate_type',raw->>'business_type') IS DISTINCT FROM 'stock_condition_request_seal'
               OR NOT COALESCE(COALESCE(raw->>'aggregate_id',raw->>'business_id') ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$',false) THEN
                RAISE EXCEPTION 'condition seal effect namespace required' USING ERRCODE='23514'; END IF;
            PERFORM public.rsc_check_condition_seal(COALESCE(raw->>'aggregate_id',raw->>'business_id')::uuid);
        END IF;
        SELECT array_agg(value) INTO keys FROM jsonb_each_text(raw) WHERE key=ANY(ARRAY[__HASH_COLUMNS__]);
        body:=COALESCE(raw->'after_jsonb',raw->'metadata_jsonb',raw->'payload_jsonb','{}'::jsonb);
        FOR matched IN SELECT s.id FROM public.stock_condition_request_seals s
            WHERE s.key_token=raw->>'key_token' OR (s.actor_user_id=raw->>'actor_user_id' AND s.request_id=raw->>'request_id')
            OR (__REVERSE_ALIASES__) OR s.id::text=raw->>'aggregate_id' OR s.id::text=raw->>'business_id'
            OR s.id::text=raw->>'source_document_id' OR s.request_id=body->>'request_id'
            OR ((s.actor_user_id=COALESCE(raw->>'actor_user_id',raw->>'actor_id')) AND raw->>'request_id'='inventory-request-'||
                encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(s.request_id,'UTF8')),'hex'))
            OR body->>'request_reference'='inventory-request-'||encode(sha256(
                convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(s.request_id,'UTF8')),'hex')
        LOOP
            PERFORM public.rsc_check_condition_seal(matched);
        END LOOP;
    END LOOP;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_seal_relevant(text,jsonb),public.rsc_condition_seal_payload(uuid),public.rsc_condition_seal_absence(uuid),
    public.rsc_check_condition_seal(uuid),public.rsc_register_condition_seal(text,bigint,uuid,jsonb,text),
    public.rsc_condition_seal_lock(),public.rsc_condition_seal_fence()
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
GRANT EXECUTE ON FUNCTION public.rsc_register_condition_seal(text,bigint,uuid,jsonb,text) TO star_oam_api;
