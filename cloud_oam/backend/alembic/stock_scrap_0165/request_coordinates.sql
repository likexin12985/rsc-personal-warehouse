-- The parent and its exact scrap/recovery child share one command. Only those
-- two facts and the single posting may share its audit/request coordinates.
-- Client-key provenance and durable request seals are separate mandatory gates.
CREATE FUNCTION public.rsc_check_scrap_coordinates_0165(kind text, checked_fact uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE fact jsonb; source_table text; aggregate text; child_table text; child_aggregate text;
    child_id uuid; child jsonb; actor text; request text; key text; reference text; posting text;
    other_table text; keyed boolean; collision boolean;
BEGIN
    source_table:=CASE kind WHEN 'original' THEN 'stock_loss_dispositions'
        WHEN 'correction' THEN 'stock_loss_correction_executions' WHEN 'inverse' THEN 'stock_loss_disposition_reversals' END;
    aggregate:=CASE kind WHEN 'original' THEN 'stock_loss_disposition'
        WHEN 'correction' THEN 'stock_loss_correction_execution' WHEN 'inverse' THEN 'stock_loss_disposition_reversal' END;
    IF source_table IS NULL OR checked_fact IS NULL THEN
        RAISE EXCEPTION '0165 exact scrap request fact required' USING ERRCODE='23514'; END IF;
    EXECUTE format('SELECT to_jsonb(f) FROM public.%I f WHERE id=$1',source_table) INTO fact USING checked_fact;
    IF kind='inverse' THEN
        child_table:='stock_scrap_recovery_executions';child_aggregate:='stock_scrap_recovery_execution';
        child_id:=(fact->>'scrap_recovery_execution_id')::uuid;
    ELSE
        child_table:='stock_operation_orders';child_aggregate:='stock_operation_scrap';
        child_id:=(fact->>'scrap_operation_id')::uuid;
    END IF;
    EXECUTE format('SELECT to_jsonb(f) FROM public.%I f WHERE id=$1',child_table) INTO child USING child_id;
    actor:=fact->>'actor_user_id';request:=fact->>'request_id';key:=fact->>'idempotency_key_hash';
    posting:=fact->>'posting_transaction_id';
    IF fact IS NULL OR child IS NULL OR actor IS NULL OR request IS NULL OR key IS NULL OR posting IS NULL
       OR child->>'actor_user_id' IS DISTINCT FROM actor OR child->>'request_id' IS DISTINCT FROM request
       OR child->>'idempotency_key_hash' IS DISTINCT FROM key
       OR child->>'request_hash' IS DISTINCT FROM fact->>'request_hash'
       OR child->>'plan_hash' IS DISTINCT FROM fact->>'plan_hash' THEN
        RAISE EXCEPTION '0165 exact shared parent child command required' USING ERRCODE='23514'; END IF;
    -- No arbitrary exemption by document type: only the exact proved IDs are
    -- excluded. A sibling order, approval, seal or recovery remains a conflict.
    FOR other_table,keyed IN SELECT * FROM (VALUES
        ('stock_operation_orders',true),('stock_loss_dispositions',true),('stock_loss_regional_reviews',true),
        ('stock_loss_headquarters_reviews',true),('stock_loss_request_seals',true),('stock_loss_review_request_seals',true),
        ('stock_loss_disposition_request_seals',false),('stock_operation_cancellations',true),
        ('stock_operation_command_seals',false),('stock_operation_outbounds',true),('stock_operation_shipments',false),
        ('stock_operation_receipts',false),('stock_operation_return_inbounds',true),('stock_operation_return_inbound_seals',false),
        ('stock_loss_disposition_reversals',true),('stock_loss_correction_decisions',true),('stock_loss_correction_executions',true),
        ('stock_loss_inverse_request_seals',true),('stock_loss_correction_approval_seals',true),('stock_loss_correction_execution_seals',true),
        ('stock_scrap_recovery_requests',true),('stock_scrap_recovery_regional_reviews',true),
        ('stock_scrap_recovery_headquarters_reviews',true),('stock_scrap_recovery_executions',true)) requests(name,keyed) LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I WHERE ((actor_user_id=$1 AND request_id=$2)'
            ||CASE WHEN keyed THEN ' OR idempotency_key_hash=$3' ELSE '' END||')'
            ||CASE WHEN other_table=source_table THEN ' AND id<>$4' ELSE '' END
            ||CASE WHEN other_table=child_table THEN ' AND id<>$5' ELSE '' END||')',other_table)
            INTO collision USING actor,request,key,checked_fact,child_id;
        IF collision THEN RAISE EXCEPTION '0165 scrap request conflicts with retained command' USING ERRCODE='23514'; END IF;
    END LOOP;
    IF (SELECT count(*) FROM public.inventory_transactions WHERE idempotency_key_hash=key)<>1
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=key AND id::text IS DISTINCT FROM posting)
       OR EXISTS(SELECT 1 FROM public.shipments WHERE idempotency_key_hash=key)
       OR EXISTS(SELECT 1 FROM public.receipts WHERE idempotency_key_hash=key) THEN
        RAISE EXCEPTION '0165 exact keyed scrap posting required' USING ERRCODE='23514'; END IF;
    reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')
        ||decode('00','hex')||convert_to(request,'UTF8')),'hex');
    IF (SELECT count(*) FROM public.audit_events WHERE actor_user_id=actor
            AND stream_key IN ('inventory','material_request') AND request_id IN (request,reference))<>3
       OR EXISTS(SELECT 1 FROM public.audit_events e WHERE e.actor_user_id=actor
            AND e.stream_key IN ('inventory','material_request') AND e.request_id IN (request,reference)
            AND NOT ((e.aggregate_type=aggregate AND e.aggregate_id=checked_fact::text AND e.request_id=request)
                OR (e.aggregate_type=child_aggregate AND e.aggregate_id=child_id::text AND e.request_id=request)
                OR (e.aggregate_type='inventory_transaction' AND e.aggregate_id=posting AND e.request_id=reference))) THEN
        RAISE EXCEPTION '0165 scrap audit coordinates contain detached evidence' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.state_transition_events WHERE actor_id=actor
            AND NOT (COALESCE(aggregate_type IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
                AND metadata_jsonb->>'operation'='formal_authentication_state_transition'
                AND metadata_jsonb->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
                AND metadata_jsonb->>'request_reference' IS NULL,false))
            AND (metadata_jsonb->>'request_id'=request OR metadata_jsonb->>'request_reference'=reference))<>3
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE actor_id=actor
            AND NOT (COALESCE(aggregate_type IN ('authentication_attempt','login_challenge','sms_dispatch','auth_session')
                AND metadata_jsonb->>'operation'='formal_authentication_state_transition'
                AND metadata_jsonb->>'request_id' ~ '^authreq-[a-f0-9]{64}$'
                AND metadata_jsonb->>'request_reference' IS NULL,false))
            AND (metadata_jsonb->>'request_id'=request OR metadata_jsonb->>'request_reference'=reference)
            AND NOT ((aggregate_type=aggregate AND aggregate_id=checked_fact::text)
                OR (aggregate_type=child_aggregate AND aggregate_id=child_id::text)
                OR (aggregate_type='inventory_transaction' AND aggregate_id=posting))) THEN
        RAISE EXCEPTION '0165 scrap state coordinates contain detached evidence' USING ERRCODE='23514'; END IF;
    -- Exact required event counts/content, including the child's no-notify
    -- rule, are proved by rsc_check_scrap_history_0165 before this function.
    IF EXISTS(SELECT 1 FROM public.outbox_events WHERE payload_jsonb->>'actor_user_id'=actor
            AND payload_jsonb->>'request_id'=request
            AND NOT ((aggregate_type=aggregate AND aggregate_id=checked_fact::text)
                OR (aggregate_type=child_aggregate AND aggregate_id=child_id::text)))
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE payload_jsonb->>'actor_user_id'=actor
            AND payload_jsonb->>'request_id'=request
            AND (business_type IS DISTINCT FROM aggregate OR business_id IS DISTINCT FROM checked_fact::text)) THEN
        RAISE EXCEPTION '0165 scrap domain coordinates contain detached evidence' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_coordinates_0165(text,uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
