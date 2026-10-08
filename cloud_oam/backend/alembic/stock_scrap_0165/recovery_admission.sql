-- Candidate admission component. Install only with the complete 0165 stock,
-- SN, immutable-history, approval/evidence and request-recovery guards.
-- This file neither enables production grants nor replaces the 0164 guards.
CREATE FUNCTION public.rsc_scrap_recovery_source_0165(checked_scrap uuid)
RETURNS TABLE(owner_org_id uuid, location_id uuid, requester_id uuid, request_hash text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; root public.stock_loss_dispositions%ROWTYPE;
    loss_line public.stock_operation_lines%ROWTYPE; loss_order public.stock_operation_orders%ROWTYPE;
    scrap_order public.stock_operation_orders%ROWTYPE; account public.stock_accounts%ROWTYPE;
    custody public.custody_assignments%ROWTYPE; fact jsonb;
BEGIN
    SELECT * INTO line FROM public.stock_scrap_lines WHERE id=checked_scrap;
    SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=line.root_disposition_id;
    SELECT * INTO loss_line FROM public.stock_operation_lines WHERE id=line.loss_line_id;
    SELECT * INTO loss_order FROM public.stock_operation_orders WHERE id=root.operation_id;
    SELECT * INTO scrap_order FROM public.stock_operation_orders WHERE id=line.operation_id;
    SELECT * INTO account FROM public.stock_accounts WHERE id=line.frozen_account_id;
    SELECT * INTO custody FROM public.custody_assignments WHERE id=line.custody_assignment_id;
    IF NOT COALESCE(line.id IS NOT NULL AND root.id IS NOT NULL AND loss_line.id IS NOT NULL
        AND loss_order.id IS NOT NULL AND scrap_order.id IS NOT NULL AND account.id IS NOT NULL
        AND custody.id IS NOT NULL AND line.operation_type='scrap'
        AND root.line_id=loss_line.id AND root.operation_id=loss_line.operation_id
        AND root.source_account_id=account.id AND root.quantity=line.quantity AND line.quantity>0
        AND loss_line.operation_type='loss_report' AND loss_order.operation_type='loss_report'
        AND loss_order.status='submitted' AND loss_line.quantity=line.quantity
        AND loss_line.reserved_account_id=account.id AND loss_line.material_id=account.material_id
        AND loss_line.target_condition=account.condition_code AND account.availability_bucket='frozen'
        AND account.condition_code IN ('new','used','damaged')
        AND account.custodian_person_id=loss_order.requester_id
        AND account.location_id=loss_order.source_location_id
        AND scrap_order.id<>loss_order.id AND scrap_order.operation_type='scrap' AND scrap_order.status='posted'
        AND scrap_order.requester_id=loss_order.requester_id AND scrap_order.source_location_id=account.location_id
        AND scrap_order.posting_transaction_id=line.posting_transaction_id
        AND custody.location_id=account.location_id AND custody.custodian_person_id=loss_order.requester_id
        AND custody.valid_from<=line.created_at AND (custody.valid_to IS NULL OR custody.valid_to>line.created_at),false)
        OR scrap_order.oam_work_order_id IS NOT NULL OR scrap_order.target_location_id IS NOT NULL
        OR scrap_order.transit_location_id IS NOT NULL OR scrap_order.target_custody_assignment_id IS NOT NULL THEN
        RAISE EXCEPTION '0165 exact loss, scrap and frozen custody source required' USING ERRCODE='23514';
    END IF;
    IF line.source_kind='original' THEN
        IF line.original_decision_id IS DISTINCT FROM root.headquarters_decision_id
            OR scrap_order.loss_headquarters_decision_id IS DISTINCT FROM line.original_decision_id
            OR scrap_order.loss_correction_decision_id IS NOT NULL
            OR line.correction_execution_id IS NOT NULL OR line.correction_decision_id IS NOT NULL
            OR line.predecessor_reversal_id IS NOT NULL THEN
            RAISE EXCEPTION '0165 original scrap source binding invalid' USING ERRCODE='23514'; END IF;
        fact:=to_jsonb(root);
    ELSIF line.source_kind='correction' THEN
        SELECT to_jsonb(c) INTO fact FROM public.stock_loss_correction_executions c
            WHERE c.id=line.correction_execution_id AND c.root_disposition_id=root.id
              AND c.reversal_id=line.predecessor_reversal_id AND c.correction_decision_id=line.correction_decision_id;
        IF line.original_decision_id IS NOT NULL OR scrap_order.loss_headquarters_decision_id IS NOT NULL
            OR scrap_order.loss_correction_decision_id IS DISTINCT FROM line.correction_decision_id THEN
            RAISE EXCEPTION '0165 corrected scrap source binding invalid' USING ERRCODE='23514'; END IF;
    ELSE
        RAISE EXCEPTION '0165 explicit scrap source kind required' USING ERRCODE='23514';
    END IF;
    IF NOT COALESCE(fact IS NOT NULL AND fact->>'disposition'='scrap'
        AND fact->>'scrap_operation_id'=line.operation_id::text
        AND fact->>'source_account_id'=account.id::text
        AND fact->>'custody_assignment_id'=custody.id::text
        AND (fact->>'quantity')::numeric=line.quantity
        AND fact->>'posting_transaction_id'=line.posting_transaction_id::text
        AND fact->>'posting_movement_id'=line.posting_movement_id::text
        AND fact->>'request_hash' ~ '^[a-f0-9]{64}$'
        AND fact->>'request_hash'=scrap_order.request_hash
        AND (fact->>'created_at')::timestamptz=line.created_at AND scrap_order.created_at=line.created_at,false)
        OR fact->>'target_account_id' IS NOT NULL OR fact->>'return_operation_id' IS NOT NULL THEN
        RAISE EXCEPTION '0165 exact original or corrected scrap execution required' USING ERRCODE='23514';
    END IF;
    RETURN QUERY SELECT account.owner_org_id,account.location_id,loss_order.requester_id,fact->>'request_hash';
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_recovery_source_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_admit_scrap_recovery_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE source record; stage text;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 recovery admission requires read committed' USING ERRCODE='23514'; END IF;
    stage:=CASE TG_TABLE_NAME WHEN 'stock_scrap_recovery_requests' THEN 'apply'
        WHEN 'stock_scrap_recovery_regional_reviews' THEN 'regional'
        WHEN 'stock_scrap_recovery_headquarters_reviews' THEN 'headquarters'
        WHEN 'stock_scrap_recovery_executions' THEN 'execute' END;
    IF stage IS NULL OR TG_OP<>'INSERT' THEN
        RAISE EXCEPTION '0165 exact recovery admission table required' USING ERRCODE='23514'; END IF;
    -- Match the writer lock order. No client-provided owner, location or
    -- requester can influence current authority. Recheck only NEW at COMMIT;
    -- historical approval/event validation must never reauthorize old actors.
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0165 inventory serialization head required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    SELECT * INTO source FROM public.rsc_scrap_recovery_source_0165(NEW.scrap_line_id);
    IF NEW.command_jsonb->'source' IS DISTINCT FROM jsonb_build_object(
        'scrap_line_id',NEW.scrap_line_id::text,'expected_scrap_request_hash',source.request_hash) THEN
        RAISE EXCEPTION '0165 recovery command must bind exact scrap source' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_scrap_recovery_authority_0165(NEW.actor_user_id,NEW.authorization_version,
        NEW.actor_person_id,source.owner_org_id,source.location_id,source.requester_id,stage);
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_admit_scrap_recovery_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_recovery_requests','stock_scrap_recovery_regional_reviews',
        'stock_scrap_recovery_headquarters_reviews','stock_scrap_recovery_executions'] LOOP
        EXECUTE format('CREATE TRIGGER trg_recovery_admission_before_0165 BEFORE INSERT ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.rsc_admit_scrap_recovery_0165()',name);
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_recovery_admission_commit_0165 AFTER INSERT ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_admit_scrap_recovery_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_recovery_admission_before_0165',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_recovery_admission_commit_0165',name);
    END LOOP;
END;
$install$;
