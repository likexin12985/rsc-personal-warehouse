-- The registrar alone derives aliases. API callers never supply stored hashes.
CREATE FUNCTION public.rsc_condition_check_request_key(checked_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.stock_condition_events%ROWTYPE; b public.stock_condition_request_key_bindings%ROWTYPE;
    keys text[];
BEGIN
    SELECT * INTO e FROM public.stock_condition_events WHERE id=checked_event;
    SELECT * INTO b FROM public.stock_condition_request_key_bindings WHERE event_id=checked_event;
    IF e.id IS NULL OR b.event_id IS NULL OR b.case_id IS DISTINCT FROM e.case_id
       OR b.actor_user_id IS DISTINCT FROM e.actor_user_id OR b.actor_person_id IS DISTINCT FROM e.actor_person_id
       OR b.request_id IS DISTINCT FROM e.request_id OR b.request_hash IS DISTINCT FROM e.request_hash
       OR b.created_at IS DISTINCT FROM e.created_at OR b.condition_key_hash IS DISTINCT FROM e.idempotency_key_hash THEN
        RAISE EXCEPTION 'condition exact durable request binding required' USING ERRCODE='23514'; END IF;
    SELECT array_agg(value) INTO keys FROM jsonb_each_text(to_jsonb(b)) WHERE key=ANY(ARRAY[__ALIASES__]);
    __COLLISION_CHECKS__
END $$;

CREATE FUNCTION public.rsc_register_condition_request_key(checked_event uuid, client_key text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.stock_condition_events%ROWTYPE; prior public.stock_condition_request_key_bindings%ROWTYPE;
    record_json jsonb; aliases jsonb; token text;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' OR client_key IS NULL
       OR client_key !~ '^[A-Za-z0-9._:-]{8,200}$' THEN
        RAISE EXCEPTION 'condition binding exact raw key and read committed required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition binding ledger lock required' USING ERRCODE='23514'; END IF;
    SELECT * INTO e FROM public.stock_condition_events WHERE id=checked_event;
    IF e.id IS NULL THEN RAISE EXCEPTION 'condition binding event required' USING ERRCODE='23514'; END IF;
    SELECT jsonb_object_agg(name,encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')
        ||convert_to(prefix||client_key,'UTF8')),'hex')) INTO aliases FROM (VALUES __KEY_DOMAINS__) d(name,prefix);
    IF e.idempotency_key_hash IS DISTINCT FROM aliases->>'condition_key_hash' THEN
        RAISE EXCEPTION 'condition raw key does not prove event' USING ERRCODE='23514'; END IF;
    token:=encode(sha256(convert_to('cloud_oam.loss.correction.key.v1','UTF8')||decode('00','hex')||convert_to(client_key,'UTF8')),'hex');
    record_json:=jsonb_build_object('event_id',e.id,'case_id',e.case_id,'actor_user_id',e.actor_user_id,
        'actor_person_id',e.actor_person_id,'request_id',e.request_id,'request_hash',e.request_hash,
        'created_at',e.created_at,'key_token',token)||aliases;
    SELECT * INTO prior FROM public.stock_condition_request_key_bindings WHERE event_id=e.id;
    IF prior.event_id IS NULL THEN
        INSERT INTO public.stock_condition_request_key_bindings
            SELECT * FROM jsonb_populate_record(NULL::public.stock_condition_request_key_bindings,record_json);
    ELSIF to_jsonb(prior) IS DISTINCT FROM record_json THEN
        RAISE EXCEPTION 'condition immutable raw key binding conflict' USING ERRCODE='23514';
    END IF;
    PERFORM public.rsc_condition_check_request_key(e.id);
    PERFORM public.rsc_condition_check_current_event(e.id);
END $$;

-- Every participating legacy writer joins the same serialization order, even
-- before any condition registry row exists. No old function body is changed.
CREATE FUNCTION public.rsc_condition_request_key_lock()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'condition key fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition key fence ledger required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_request_key_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE body jsonb:=to_jsonb(NEW); keys text[]; matched uuid;
BEGIN
    IF TG_TABLE_NAME='stock_condition_events' THEN
        PERFORM public.rsc_condition_check_request_key(NEW.id);
    ELSIF TG_TABLE_NAME='stock_condition_request_key_bindings' THEN
        PERFORM public.rsc_condition_check_request_key(NEW.event_id);
    END IF;
    SELECT array_agg(value) INTO keys FROM jsonb_each_text(body) WHERE key=ANY(ARRAY[__HASH_COLUMNS__]);
    FOR matched IN SELECT b.event_id FROM public.stock_condition_request_key_bindings b
        WHERE b.key_token=body->>'key_token' OR (b.actor_user_id=body->>'actor_user_id' AND b.request_id=body->>'request_id')
        OR (__REVERSE_ALIASES__)
    LOOP
        PERFORM public.rsc_condition_check_request_key(matched);
    END LOOP;
    RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION public.rsc_condition_check_request_key(uuid),
    public.rsc_register_condition_request_key(uuid,text),public.rsc_condition_request_key_lock(),
    public.rsc_condition_request_key_fence()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
GRANT EXECUTE ON FUNCTION public.rsc_register_condition_request_key(uuid,text) TO star_oam_api;
CREATE TRIGGER condition_key_immutable BEFORE UPDATE OR DELETE ON public.stock_condition_request_key_bindings
FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_reject_mutation();
ALTER TABLE public.stock_condition_request_key_bindings ENABLE ALWAYS TRIGGER condition_key_immutable;
