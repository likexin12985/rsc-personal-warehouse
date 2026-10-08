-- Four previously unregistered actions. Legacy inverse/correction bindings and
-- their three aliases retain their original values, ownership and meaning.
CREATE TABLE public.stock_scrap_request_key_bindings (
    fact_id uuid PRIMARY KEY,
    binding_kind varchar(24) NOT NULL,
    root_disposition_id uuid NOT NULL REFERENCES public.stock_loss_dispositions(id) ON DELETE RESTRICT,
    actor_user_id varchar(36) NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    actor_person_id uuid NOT NULL REFERENCES public.people(id) ON DELETE RESTRICT,
    request_id varchar(160) NOT NULL CHECK (request_id ~ '^[A-Za-z0-9._:-]{8,160}$'),
    request_hash varchar(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    key_token varchar(64) NOT NULL UNIQUE CHECK (key_token ~ '^[a-f0-9]{64}$'),
    reversal_key_hash varchar(64) NOT NULL UNIQUE CHECK (reversal_key_hash ~ '^[a-f0-9]{64}$'),
    approval_key_hash varchar(64) NOT NULL UNIQUE CHECK (approval_key_hash ~ '^[a-f0-9]{64}$'),
    correction_key_hash varchar(64) NOT NULL UNIQUE CHECK (correction_key_hash ~ '^[a-f0-9]{64}$'),
    recovery_key_hash varchar(64) NOT NULL UNIQUE CHECK (recovery_key_hash ~ '^[a-f0-9]{64}$'),
    scrap_key_hash varchar(64) NOT NULL UNIQUE CHECK (scrap_key_hash ~ '^[a-f0-9]{64}$'),
    original_id uuid REFERENCES public.stock_loss_dispositions(id) ON DELETE RESTRICT,
    application_id uuid REFERENCES public.stock_scrap_recovery_requests(id) ON DELETE RESTRICT,
    regional_id uuid REFERENCES public.stock_scrap_recovery_regional_reviews(id) ON DELETE RESTRICT,
    headquarters_id uuid REFERENCES public.stock_scrap_recovery_headquarters_reviews(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL,
    UNIQUE(actor_user_id,request_id),
    CHECK ((binding_kind='original' AND original_id IS NOT NULL AND original_id=fact_id
            AND original_id=root_disposition_id AND application_id IS NULL AND regional_id IS NULL AND headquarters_id IS NULL)
        OR (binding_kind='apply' AND application_id IS NOT NULL AND application_id=fact_id
            AND original_id IS NULL AND regional_id IS NULL AND headquarters_id IS NULL)
        OR (binding_kind='regional' AND regional_id IS NOT NULL AND regional_id=fact_id
            AND original_id IS NULL AND application_id IS NULL AND headquarters_id IS NULL)
        OR (binding_kind='headquarters' AND headquarters_id IS NOT NULL AND headquarters_id=fact_id
            AND original_id IS NULL AND application_id IS NULL AND regional_id IS NULL)),
    CHECK (reversal_key_hash NOT IN (approval_key_hash,correction_key_hash,recovery_key_hash,scrap_key_hash)
        AND approval_key_hash NOT IN (correction_key_hash,recovery_key_hash,scrap_key_hash)
        AND correction_key_hash NOT IN (recovery_key_hash,scrap_key_hash) AND recovery_key_hash<>scrap_key_hash)
);
REVOKE ALL ON public.stock_scrap_request_key_bindings
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
GRANT SELECT ON public.stock_scrap_request_key_bindings TO star_oam_api,star_oam_backup;

CREATE FUNCTION public.rsc_scrap_binding_fact_0165(checked_kind text, checked_fact uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE source_table text; fact jsonb; root uuid;
BEGIN
    source_table:=CASE checked_kind WHEN 'original' THEN 'stock_loss_dispositions'
        WHEN 'apply' THEN 'stock_scrap_recovery_requests' WHEN 'regional' THEN 'stock_scrap_recovery_regional_reviews'
        WHEN 'headquarters' THEN 'stock_scrap_recovery_headquarters_reviews' END;
    IF source_table IS NULL OR checked_fact IS NULL THEN
        RAISE EXCEPTION '0165 exact typed scrap binding required' USING ERRCODE='23514'; END IF;
    EXECUTE format('SELECT to_jsonb(f) FROM public.%I f WHERE id=$1',source_table) INTO fact USING checked_fact;
    IF checked_kind='original' THEN
        root:=checked_fact;
        IF fact->>'disposition' IS DISTINCT FROM 'scrap' OR fact#>>'{command_jsonb,action}' IS DISTINCT FROM 'scrap'
           OR fact#>>'{command_jsonb,intent,source,kind}' IS DISTINCT FROM 'original' THEN
            RAISE EXCEPTION '0165 original scrap binding source required' USING ERRCODE='23514'; END IF;
        fact:=fact||jsonb_build_object('actor_person_id',fact->>'executor_person_id');
    ELSE
        SELECT root_disposition_id INTO root FROM public.stock_scrap_lines WHERE id=(fact->>'scrap_line_id')::uuid;
        IF fact#>>'{command_jsonb,action}' IS DISTINCT FROM (CASE checked_kind
            WHEN 'apply' THEN 'apply_scrap_recovery' WHEN 'regional' THEN 'review_scrap_recovery_region'
            WHEN 'headquarters' THEN 'review_scrap_recovery_headquarters' END) THEN
            RAISE EXCEPTION '0165 recovery binding action required' USING ERRCODE='23514'; END IF;
    END IF;
    IF fact IS NULL OR root IS NULL OR fact->>'actor_person_id' IS NULL THEN
        RAISE EXCEPTION '0165 complete scrap binding fact required' USING ERRCODE='23514'; END IF;
    RETURN fact||jsonb_build_object('root_disposition_id',root,'source_table',source_table);
END;
$function$;

CREATE FUNCTION public.rsc_check_scrap_request_binding_0165(checked_kind text, checked_fact uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE binding public.stock_scrap_request_key_bindings%ROWTYPE; fact jsonb; keys text[];
    other_table text; collision boolean;
BEGIN
    SELECT * INTO binding FROM public.stock_scrap_request_key_bindings WHERE fact_id=checked_fact AND binding_kind=checked_kind;
    IF binding.fact_id IS NULL THEN
        RAISE EXCEPTION '0165 exact database-owned scrap binding required' USING ERRCODE='23514'; END IF;
    fact:=public.rsc_scrap_binding_fact_0165(checked_kind,checked_fact);
    keys:=ARRAY[binding.reversal_key_hash,binding.approval_key_hash,binding.correction_key_hash,binding.recovery_key_hash,binding.scrap_key_hash];
    IF (fact->>'root_disposition_id')::uuid IS DISTINCT FROM binding.root_disposition_id
        OR fact->>'actor_user_id' IS DISTINCT FROM binding.actor_user_id
        OR (fact->>'actor_person_id')::uuid IS DISTINCT FROM binding.actor_person_id
        OR fact->>'request_id' IS DISTINCT FROM binding.request_id OR fact->>'request_hash' IS DISTINCT FROM binding.request_hash
        OR (fact->>'created_at')::timestamptz IS DISTINCT FROM binding.created_at
        OR fact->>'idempotency_key_hash' IS DISTINCT FROM (CASE WHEN checked_kind='original' THEN keys[5] ELSE keys[4] END) THEN
        RAISE EXCEPTION '0165 scrap binding differs from exact fact' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM public.stock_loss_request_key_bindings b WHERE b.key_token=binding.key_token
        OR (b.actor_user_id=binding.actor_user_id AND b.request_id=binding.request_id)
        OR ARRAY[b.reversal_key_hash,b.approval_key_hash,b.correction_key_hash,b.recovery_key_hash,b.scrap_key_hash]::text[] && keys)
       OR EXISTS(SELECT 1 FROM public.stock_scrap_request_key_bindings b WHERE b.fact_id<>checked_fact
        AND (b.key_token=binding.key_token OR (b.actor_user_id=binding.actor_user_id AND b.request_id=binding.request_id)
        OR ARRAY[b.reversal_key_hash,b.approval_key_hash,b.correction_key_hash,b.recovery_key_hash,b.scrap_key_hash]::text[] && keys)) THEN
        RAISE EXCEPTION '0165 raw request key reused across registries' USING ERRCODE='23514'; END IF;
    FOREACH other_table IN ARRAY ARRAY['stock_operation_orders','stock_loss_dispositions','stock_loss_regional_reviews',
        'stock_loss_headquarters_reviews','stock_loss_request_seals','stock_loss_review_request_seals',
        'stock_loss_disposition_request_seals','stock_operation_cancellations','stock_operation_command_seals',
        'stock_operation_outbounds','stock_operation_shipments','stock_operation_receipts','stock_operation_return_inbounds',
        'stock_operation_return_inbound_seals','stock_loss_disposition_reversals','stock_loss_correction_decisions',
        'stock_loss_correction_executions','stock_loss_inverse_request_seals','stock_loss_correction_approval_seals',
        'stock_loss_correction_execution_seals','stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews',
        'stock_scrap_recovery_headquarters_reviews','stock_scrap_recovery_executions'] LOOP
        -- JSON extraction also handles old seal tables without a primary hash.
        -- Only this source and its exact original-scrap child may share keys.
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I f WHERE '
            '((to_jsonb(f)->>''actor_user_id''=$2 AND to_jsonb(f)->>''request_id''=$3) OR '
            'ARRAY[to_jsonb(f)->>''idempotency_key_hash'',to_jsonb(f)->>''reversal_key_hash'', '
            'to_jsonb(f)->>''approval_key_hash'',to_jsonb(f)->>''correction_key_hash''] && $1)'
            ||CASE WHEN other_table=fact->>'source_table' THEN ' AND id<>$4' ELSE '' END
            ||CASE WHEN checked_kind='original' AND other_table='stock_operation_orders'
                THEN ' AND id IS DISTINCT FROM $5' ELSE '' END||')',other_table)
            INTO collision USING keys,binding.actor_user_id,binding.request_id,checked_fact,(fact->>'scrap_operation_id')::uuid;
        IF collision THEN RAISE EXCEPTION '0165 scrap request key reused across actions' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=ANY(keys)
        AND id::text IS DISTINCT FROM fact->>'posting_transaction_id')
        OR EXISTS(SELECT 1 FROM public.shipments WHERE idempotency_key_hash=ANY(keys))
        OR EXISTS(SELECT 1 FROM public.receipts WHERE idempotency_key_hash=ANY(keys)) THEN
        RAISE EXCEPTION '0165 scrap request aliases contain detached posting' USING ERRCODE='23514'; END IF;
END;
$function$;

CREATE FUNCTION public.rsc_register_scrap_request_binding_0165(checked_kind text, checked_fact uuid, client_key text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE fact jsonb; hashes text[]; token text; prior public.stock_scrap_request_key_bindings%ROWTYPE; source record;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' OR client_key IS NULL
        OR client_key !~ '^[A-Za-z0-9._:-]{8,200}$' THEN
        RAISE EXCEPTION '0165 exact client key and read committed required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 request binding ledger required' USING ERRCODE='23514'; END IF;
    fact:=public.rsc_scrap_binding_fact_0165(checked_kind,checked_fact);
    SELECT array_agg(encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')
        ||convert_to(a.prefix||client_key,'UTF8')),'hex') ORDER BY a.ordinal) INTO hashes
        FROM (VALUES ('stock-loss:reverse_loss:',1),('stock-loss:approve_loss_correction:',2),
            ('stock-loss:correct_loss:',3),('stock-scrap-recovery:',4),('stock-scrap:',5)) a(prefix,ordinal);
    token:=encode(sha256(convert_to('cloud_oam.loss.correction.key.v1','UTF8')||decode('00','hex')||convert_to(client_key,'UTF8')),'hex');
    IF fact->>'idempotency_key_hash' IS DISTINCT FROM (CASE WHEN checked_kind='original' THEN hashes[5] ELSE hashes[4] END) THEN
        RAISE EXCEPTION '0165 client key does not prove stored scrap action hash' USING ERRCODE='23514'; END IF;
    SELECT * INTO prior FROM public.stock_scrap_request_key_bindings WHERE fact_id=checked_fact;
    IF prior.fact_id IS NOT NULL THEN
        IF prior.binding_kind IS DISTINCT FROM checked_kind OR prior.key_token IS DISTINCT FROM token
            OR ARRAY[prior.reversal_key_hash,prior.approval_key_hash,prior.correction_key_hash,prior.recovery_key_hash,prior.scrap_key_hash]::text[]
                IS DISTINCT FROM hashes THEN
            RAISE EXCEPTION '0165 immutable scrap request binding conflict' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_scrap_request_binding_0165(checked_kind,checked_fact);
        RETURN;
    END IF;
    INSERT INTO public.stock_scrap_request_key_bindings(fact_id,binding_kind,root_disposition_id,actor_user_id,actor_person_id,
        request_id,request_hash,key_token,reversal_key_hash,approval_key_hash,correction_key_hash,recovery_key_hash,scrap_key_hash,
        original_id,application_id,regional_id,headquarters_id,created_at)
    VALUES(checked_fact,checked_kind,(fact->>'root_disposition_id')::uuid,fact->>'actor_user_id',(fact->>'actor_person_id')::uuid,
        fact->>'request_id',fact->>'request_hash',token,hashes[1],hashes[2],hashes[3],hashes[4],hashes[5],
        CASE WHEN checked_kind='original' THEN checked_fact END,CASE WHEN checked_kind='apply' THEN checked_fact END,
        CASE WHEN checked_kind='regional' THEN checked_fact END,CASE WHEN checked_kind='headquarters' THEN checked_fact END,
        (fact->>'created_at')::timestamptz);
    PERFORM public.rsc_check_scrap_request_binding_0165(checked_kind,checked_fact);
    IF checked_kind='original' THEN
        PERFORM public.rsc_check_scrap_current_0165('original',checked_fact);
    ELSE
        SELECT * INTO source FROM public.rsc_scrap_recovery_source_0165((fact->>'scrap_line_id')::uuid);
        PERFORM public.rsc_assert_scrap_recovery_authority_0165(fact->>'actor_user_id',(fact->>'authorization_version')::bigint,
            (fact->>'actor_person_id')::uuid,source.owner_org_id,source.location_id,source.requester_id,checked_kind);
        PERFORM public.rsc_check_scrap_recovery_approvals_0165((fact->>'scrap_line_id')::uuid);
    END IF;
END;
$function$;

CREATE FUNCTION public.rsc_fence_scrap_request_bindings_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE raw jsonb:=to_jsonb(NEW); kind text; keys text[]; item record;
BEGIN
    IF TG_OP<>'INSERT' OR TG_TABLE_SCHEMA<>'public' OR current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 scrap binding insert fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 request binding ledger required' USING ERRCODE='23514'; END IF;
    kind:=CASE TG_TABLE_NAME WHEN 'stock_loss_dispositions' THEN CASE WHEN raw->>'disposition'='scrap' THEN 'original' END
        WHEN 'stock_scrap_recovery_requests' THEN 'apply' WHEN 'stock_scrap_recovery_regional_reviews' THEN 'regional'
        WHEN 'stock_scrap_recovery_headquarters_reviews' THEN 'headquarters' END;
    IF kind IS NOT NULL THEN PERFORM public.rsc_check_scrap_request_binding_0165(kind,(raw->>'id')::uuid); END IF;
    keys:=array_remove(ARRAY[raw->>'idempotency_key_hash',raw->>'reversal_key_hash',raw->>'approval_key_hash',
        raw->>'correction_key_hash',raw->>'recovery_key_hash',raw->>'scrap_key_hash'],NULL);
    FOR item IN SELECT binding_kind,fact_id FROM public.stock_scrap_request_key_bindings b
        WHERE b.key_token=raw->>'key_token' OR (b.actor_user_id=raw->>'actor_user_id' AND b.request_id=raw->>'request_id')
        OR ARRAY[b.reversal_key_hash,b.approval_key_hash,b.correction_key_hash,b.recovery_key_hash,b.scrap_key_hash]::text[] && keys
    LOOP PERFORM public.rsc_check_scrap_request_binding_0165(item.binding_kind,item.fact_id); END LOOP;
    RETURN NULL;
END;
$function$;

REVOKE ALL ON FUNCTION public.rsc_scrap_binding_fact_0165(text,uuid),
    public.rsc_check_scrap_request_binding_0165(text,uuid),public.rsc_fence_scrap_request_bindings_0165(),
    public.rsc_register_scrap_request_binding_0165(text,uuid,text)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
GRANT EXECUTE ON FUNCTION public.rsc_register_scrap_request_binding_0165(text,uuid,text) TO star_oam_api;
CREATE TRIGGER trg_scrap_binding_immutable_0165 BEFORE UPDATE OR DELETE ON public.stock_scrap_request_key_bindings
FOR EACH ROW EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165();
CREATE TRIGGER trg_scrap_binding_no_truncate_0165 BEFORE TRUNCATE ON public.stock_scrap_request_key_bindings
FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_scrap_facts_append_only_0165();
ALTER TABLE public.stock_scrap_request_key_bindings ENABLE ALWAYS TRIGGER trg_scrap_binding_immutable_0165;
ALTER TABLE public.stock_scrap_request_key_bindings ENABLE ALWAYS TRIGGER trg_scrap_binding_no_truncate_0165;
DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_request_key_bindings','stock_loss_request_key_bindings',
        'stock_operation_orders','stock_loss_dispositions','stock_loss_regional_reviews','stock_loss_headquarters_reviews',
        'stock_loss_request_seals','stock_loss_review_request_seals','stock_loss_disposition_request_seals',
        'stock_operation_cancellations','stock_operation_command_seals','stock_operation_outbounds','stock_operation_shipments',
        'stock_operation_receipts','stock_operation_return_inbounds','stock_operation_return_inbound_seals',
        'stock_loss_disposition_reversals','stock_loss_correction_decisions','stock_loss_correction_executions',
        'stock_loss_inverse_request_seals','stock_loss_correction_approval_seals','stock_loss_correction_execution_seals',
        'stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews','stock_scrap_recovery_headquarters_reviews',
        'stock_scrap_recovery_executions','inventory_transactions','shipments','receipts'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_scrap_binding_fence_0165 AFTER INSERT ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_request_bindings_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_scrap_binding_fence_0165',name);
    END LOOP;
END;
$install$;
