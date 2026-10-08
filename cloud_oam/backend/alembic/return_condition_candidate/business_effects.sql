-- Reconstruct effects from real facts, never from a caller supplied payload.
-- Existing audit-chain, immutable fact and provider delivery guards remain.
CREATE FUNCTION public.rsc_condition_check_business_effects(checked_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.stock_condition_events%ROWTYPE; c public.stock_condition_cases%ROWTYPE;
    recipient uuid; members uuid[]; body jsonb; kind text; key text; total bigint;
    a public.audit_events%ROWTYPE; s public.state_transition_events%ROWTYPE;
    o public.outbox_events%ROWTYPE; n public.notification_events%ROWTYPE;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'condition effects require read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition effects ledger required' USING ERRCODE='23514'; END IF;
    SELECT * INTO e FROM public.stock_condition_events WHERE id=checked_event;
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=e.case_id;
    SELECT custodian_person_id INTO recipient FROM public.stock_accounts WHERE id=c.source_account_id;
    IF e.id IS NULL OR c.id IS NULL OR recipient IS NULL THEN
        RAISE EXCEPTION 'condition effects event and source required' USING ERRCODE='23514'; END IF;
    kind:='stock_condition.'||e.kind; key:=kind||':'||e.id::text;
    body:=jsonb_build_object('schema_version','condition_result/1','case_id',c.id::text,'event_id',e.id::text,
        'inbound_line_id',c.inbound_line_id::text,'action',e.kind,'status',e.to_state,
        'quantity',c.quantity::text,'actor_user_id',e.actor_user_id,'actor_person_id',e.actor_person_id::text,
        'authorization_version',e.authorization_version,'request_id',e.request_id,'request_hash',e.request_hash,
        'plan_hash',e.plan_hash,'posting_transaction_id',e.posting_transaction_id::text,
        'posting_movement_id',e.posting_movement_id::text,'stock_effect',COALESCE(e.movement_type,'none'),'reason',e.reason);
    -- A union, not a filter on already-correct fields: contradictory extras
    -- attached through either the business identity or unique key must fail.
    SELECT count(*) INTO total FROM public.audit_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR request_id=key;
    SELECT * INTO a FROM public.audit_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR request_id=key;
    members:=public.rsc_loss_inventory_audit_members_0159();
    IF total<>1 OR a.id IS NULL OR NOT COALESCE(a.id=ANY(members),false)
       OR a.stream_key IS DISTINCT FROM 'inventory' OR a.aggregate_type IS DISTINCT FROM 'stock_condition_event'
       OR a.aggregate_id IS DISTINCT FROM e.id::text OR a.request_id IS DISTINCT FROM key
       OR a.action IS DISTINCT FROM kind OR a.actor_user_id IS DISTINCT FROM e.actor_user_id
       OR a.before_jsonb IS DISTINCT FROM '{}'::jsonb OR a.after_jsonb IS DISTINCT FROM body
       OR a.created_at IS DISTINCT FROM e.created_at OR a.occurred_at IS DISTINCT FROM e.created_at THEN
        RAISE EXCEPTION 'condition exact business audit required' USING ERRCODE='23514'; END IF;
    SELECT count(*) INTO total FROM public.state_transition_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR idempotency_key=key;
    SELECT * INTO s FROM public.state_transition_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR idempotency_key=key;
    IF total<>1 OR s.id IS NULL OR s.aggregate_type IS DISTINCT FROM 'stock_condition_event'
       OR s.aggregate_id IS DISTINCT FROM e.id::text OR s.idempotency_key IS DISTINCT FROM key
       OR s.from_status IS DISTINCT FROM e.from_state OR s.to_status IS DISTINCT FROM e.to_state
       OR s.actor_id IS DISTINCT FROM e.actor_user_id OR s.reason IS DISTINCT FROM kind
       OR s.metadata_jsonb IS DISTINCT FROM body OR s.created_at IS DISTINCT FROM e.created_at
       OR s.occurred_at IS DISTINCT FROM e.created_at THEN
        RAISE EXCEPTION 'condition exact business state required' USING ERRCODE='23514'; END IF;
    SELECT count(*) INTO total FROM public.outbox_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR idempotency_key=key;
    SELECT * INTO o FROM public.outbox_events
        WHERE (aggregate_type='stock_condition_event' AND aggregate_id=e.id::text) OR idempotency_key=key;
    IF total<>1 OR o.id IS NULL OR o.aggregate_type IS DISTINCT FROM 'stock_condition_event'
       OR o.aggregate_id IS DISTINCT FROM e.id::text OR o.idempotency_key IS DISTINCT FROM key
       OR o.event_type IS DISTINCT FROM kind OR o.payload_jsonb IS DISTINCT FROM body
       OR o.created_at IS DISTINCT FROM e.created_at THEN
        RAISE EXCEPTION 'condition exact business outbox required' USING ERRCODE='23514'; END IF;
    SELECT count(*) INTO total FROM public.notification_events
        WHERE (business_type='stock_condition_event' AND business_id=e.id::text) OR dedup_key=key;
    SELECT * INTO n FROM public.notification_events
        WHERE (business_type='stock_condition_event' AND business_id=e.id::text) OR dedup_key=key;
    IF total<>1 OR n.id IS NULL OR n.business_type IS DISTINCT FROM 'stock_condition_event'
       OR n.business_id IS DISTINCT FROM e.id::text OR n.dedup_key IS DISTINCT FROM key
       OR n.event_type IS DISTINCT FROM kind OR n.payload_jsonb IS DISTINCT FROM body
       OR n.created_at IS DISTINCT FROM e.created_at OR n.occurred_at IS DISTINCT FROM e.created_at
       OR n.target_manifest_sha256 IS DISTINCT FROM encode(sha256(convert_to(
            'notification-person-targets.v1'||chr(10)||recipient::text,'UTF8')),'hex')
       OR (SELECT count(*) FROM public.notification_person_targets WHERE event_id=n.id)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_person_targets
            WHERE event_id=n.id AND person_id=recipient AND created_at=e.created_at) THEN
        RAISE EXCEPTION 'condition exact business notification targets required' USING ERRCODE='23514'; END IF;
END $$;

-- The same resolver examines OLD and NEW. Moving a row away from the reserved
-- namespace, or disguising it with a generic aggregate, cannot evade checks.
CREATE FUNCTION public.rsc_condition_effect_ids(table_name text, row_value jsonb)
RETURNS uuid[] LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE aggregate text; identifier text; kind text; key text; payload jsonb; ids uuid[];
BEGIN
    IF row_value IS NULL THEN RETURN ARRAY[]::uuid[]; END IF;
    IF table_name='stock_condition_events' THEN RETURN ARRAY[(row_value->>'id')::uuid]; END IF;
    IF table_name='stock_condition_cases' THEN
        SELECT array_agg(id) INTO ids FROM public.stock_condition_events WHERE case_id=(row_value->>'id')::uuid;
        RETURN COALESCE(ids,ARRAY[]::uuid[]);
    END IF;
    IF table_name='notification_person_targets' THEN
        SELECT to_jsonb(n) INTO row_value FROM public.notification_events n WHERE id=(row_value->>'event_id')::uuid;
        table_name:='notification_events';
    END IF;
    IF table_name='notification_events' THEN
        aggregate:=row_value->>'business_type'; identifier:=row_value->>'business_id';
    ELSE aggregate:=row_value->>'aggregate_type'; identifier:=row_value->>'aggregate_id'; END IF;
    kind:=COALESCE(row_value->>'event_type',row_value->>'action',row_value->>'reason');
    key:=COALESCE(row_value->>'idempotency_key',row_value->>'dedup_key',row_value->>'request_id');
    payload:=COALESCE(row_value->'payload_jsonb',row_value->'metadata_jsonb',row_value->'after_jsonb');
    IF NOT COALESCE(aggregate='stock_condition_event' OR starts_with(kind,'stock_condition.')
        OR starts_with(key,'stock_condition.') OR payload->>'schema_version'='condition_result/1',false) THEN
        RETURN ARRAY[]::uuid[]; END IF;
    IF aggregate IS DISTINCT FROM 'stock_condition_event' OR identifier IS NULL
       OR NOT COALESCE(identifier ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$',false) THEN
        RAISE EXCEPTION 'condition effect namespace and identity required' USING ERRCODE='23514'; END IF;
    RETURN ARRAY[identifier::uuid];
END $$;

CREATE FUNCTION public.rsc_condition_effect_lock()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE prior jsonb; current_row jsonb; ids uuid[]; mutable text[]:=ARRAY[]::text[];
BEGIN
    IF TG_OP<>'INSERT' THEN prior:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN current_row:=to_jsonb(NEW); END IF;
    ids:=public.rsc_condition_effect_ids(TG_TABLE_NAME,prior)||public.rsc_condition_effect_ids(TG_TABLE_NAME,current_row);
    IF cardinality(ids)>0 THEN
        IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION 'condition effects require read committed' USING ERRCODE='23514'; END IF;
        PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'condition effects ledger required' USING ERRCODE='23514'; END IF;
        IF TG_TABLE_NAME='outbox_events' THEN
            mutable:=ARRAY['status','attempts','available_at','locked_at','locked_by','published_at','last_error','updated_at'];
        ELSIF TG_TABLE_NAME='notification_events' THEN mutable:=ARRAY['status']; END IF;
        IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND (prior-mutable) IS DISTINCT FROM (current_row-mutable)) THEN
            RAISE EXCEPTION 'condition business effect facts are immutable' USING ERRCODE='23514'; END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_effect_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE prior jsonb; current_row jsonb; checked uuid;
BEGIN
    IF TG_OP<>'INSERT' THEN prior:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN current_row:=to_jsonb(NEW); END IF;
    FOR checked IN SELECT DISTINCT unnest(public.rsc_condition_effect_ids(TG_TABLE_NAME,prior)
        ||public.rsc_condition_effect_ids(TG_TABLE_NAME,current_row)) LOOP
        PERFORM public.rsc_condition_check_business_effects(checked);
    END LOOP;
    RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION public.rsc_condition_check_business_effects(uuid),
    public.rsc_condition_effect_ids(text,jsonb),public.rsc_condition_effect_lock(),public.rsc_condition_effect_fence()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
