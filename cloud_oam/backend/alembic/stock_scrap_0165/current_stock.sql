-- Current authority applies only to the fact being admitted, never to a
-- historical approver. Historical proof remains independently reconstructible.
CREATE FUNCTION public.rsc_check_scrap_current_0165(checked_kind text, checked_fact uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE fact jsonb; source_table text; root_id uuid; person uuid;
    root public.stock_loss_dispositions%ROWTYPE; parent public.stock_operation_orders%ROWTYPE;
    account public.stock_accounts%ROWTYPE; location public.stock_locations%ROWTYPE;
    custody public.custody_assignments%ROWTYPE; policy public.material_inventory_policies%ROWTYPE;
    effective timestamptz; checked_now timestamptz;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' OR checked_fact IS NULL THEN
        RAISE EXCEPTION '0165 current scrap fact and read committed required' USING ERRCODE='23514'; END IF;
    source_table:=CASE checked_kind WHEN 'original' THEN 'stock_loss_dispositions'
        WHEN 'correction' THEN 'stock_loss_correction_executions' WHEN 'inverse' THEN 'stock_loss_disposition_reversals' END;
    IF source_table IS NULL THEN
        RAISE EXCEPTION '0165 explicit current scrap fact kind required' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 current scrap ledger required' USING ERRCODE='23514'; END IF;
    EXECUTE format('SELECT to_jsonb(f) FROM public.%I f WHERE id=$1',source_table) INTO fact USING checked_fact;
    root_id:=CASE checked_kind WHEN 'original' THEN checked_fact ELSE (fact->>'root_disposition_id')::uuid END;
    SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=root_id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=root.operation_id;
    SELECT * INTO account FROM public.stock_accounts WHERE id=root.source_account_id;
    person:=(CASE checked_kind WHEN 'original' THEN fact->>'executor_person_id' ELSE fact->>'actor_person_id' END)::uuid;
    effective:=(fact->>'created_at')::timestamptz;
    IF fact IS NULL OR root.id IS NULL OR parent.id IS NULL OR account.id IS NULL OR person IS NULL
        OR parent.operation_type IS DISTINCT FROM 'loss_report' OR account.availability_bucket IS DISTINCT FROM 'frozen'
        OR account.custodian_person_id IS DISTINCT FROM parent.requester_id
        OR account.location_id IS DISTINCT FROM parent.source_location_id
        OR effective IS NULL OR effective>clock_timestamp()
        OR (checked_kind IN ('original','correction') AND fact->>'disposition' IS DISTINCT FROM 'scrap')
        OR (checked_kind='inverse' AND fact->>'scrap_line_id' IS NULL) THEN
        RAISE EXCEPTION '0165 exact current scrap context required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[fact->>'actor_user_id']);
    IF checked_kind='original' THEN
        PERFORM public.rsc_assert_loss_disposition_authority_0150(fact->>'actor_user_id',
            (fact->>'authorization_version')::bigint,person,account.owner_org_id);
    ELSIF checked_kind='correction' THEN
        PERFORM public.rsc_assert_loss_correction_authority_0159(fact->>'actor_user_id',
            (fact->>'authorization_version')::bigint,person,account.owner_org_id,'correct_loss');
    ELSE
        PERFORM public.rsc_assert_scrap_recovery_authority_0165(fact->>'actor_user_id',
            (fact->>'authorization_version')::bigint,person,account.owner_org_id,
            account.location_id,parent.requester_id,'execute');
    END IF;
    PERFORM public.rsc_check_scrap_history_0165(checked_kind,checked_fact);
    PERFORM public.rsc_check_scrap_coordinates_0165(checked_kind,checked_fact);
    PERFORM public.rsc_check_loss_history_graph_0159(root.id);
    PERFORM public.rsc_check_loss_hold_0145(account.id);
    SELECT * INTO location FROM public.stock_locations WHERE id=account.location_id FOR UPDATE;
    PERFORM id FROM public.custody_assignments WHERE location_id=location.id ORDER BY id FOR SHARE;
    PERFORM id FROM public.materials WHERE id=account.material_id FOR SHARE;
    PERFORM id FROM public.material_inventory_policies WHERE material_id=account.material_id ORDER BY id FOR SHARE;
    checked_now:=clock_timestamp();
    SELECT * INTO custody FROM public.custody_assignments WHERE id=(fact->>'custody_assignment_id')::uuid;
    IF location.id IS NULL OR location.status IS DISTINCT FROM 'active' OR location.location_type IS DISTINCT FROM 'personal'
        OR location.owner_org_id IS DISTINCT FROM account.owner_org_id
        OR location.custodian_person_id IS DISTINCT FROM parent.requester_id
        OR custody.id IS NULL OR custody.location_id IS DISTINCT FROM location.id
        OR custody.custodian_person_id IS DISTINCT FROM parent.requester_id
        OR custody.valid_from>checked_now OR (custody.valid_to IS NOT NULL AND custody.valid_to<=checked_now)
        OR (SELECT count(*) FROM public.custody_assignments WHERE location_id=location.id
            AND valid_from<=checked_now AND (valid_to IS NULL OR valid_to>checked_now))<>1
        OR NOT EXISTS(SELECT 1 FROM public.materials WHERE id=account.material_id AND status='active') THEN
        RAISE EXCEPTION '0165 current scrap custody or material changed' USING ERRCODE='23514'; END IF;
    SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=account.material_id
        AND effective_from<=checked_now AND (effective_to IS NULL OR effective_to>checked_now);
    IF policy.id IS NULL OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=account.material_id
        AND effective_from<=checked_now AND (effective_to IS NULL OR effective_to>checked_now))<>1
        OR policy.id::text IS DISTINCT FROM fact#>>'{plan_jsonb,policy_fingerprint,0,1}' THEN
        RAISE EXCEPTION '0165 current scrap tracking policy changed' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM public.inventory_freezes f
        JOIN public.stocktake_scopes s ON s.id=f.stocktake_scope_id AND s.task_id=f.task_id
        WHERE f.status IN ('active','released','cancelled') AND f.freeze_mode='hard'
          AND ((f.valid_from<=checked_now AND (f.valid_to IS NULL OR f.valid_to>checked_now))
            OR (f.valid_from<=effective AND (f.valid_to IS NULL OR f.valid_to>effective)))
          AND s.owner_org_id=account.owner_org_id AND s.location_id=account.location_id
          AND (s.scope_mode='location_all' OR (s.scope_mode='filtered'
            AND (s.material_id IS NULL OR s.material_id=account.material_id)
            AND (s.condition_code IS NULL OR s.condition_code=account.condition_code)
            AND (s.availability_bucket IS NULL OR s.availability_bucket=account.availability_bucket)))) THEN
        RAISE EXCEPTION '0165 scrap account is hard frozen' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_current_0165(text,uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
