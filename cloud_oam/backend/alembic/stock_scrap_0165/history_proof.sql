-- Private values scoped to one read-only history-graph call. The original
-- complete proof runs for every distinct fact; no session or table cache.
CREATE FUNCTION public.rsc_build_scrap_history_proof_0165(checked_root uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE item record; plans jsonb:='{}'::jsonb; key text; plan jsonb; audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 history proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    IF checked_root IS NULL OR NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions WHERE id=checked_root) THEN
        RAISE EXCEPTION '0165 retained proof root required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_scrap_upstream_with_audit_0165(checked_root,audit_ids);
    FOR item IN
        SELECT 'original' AS kind,id FROM public.stock_loss_dispositions
            WHERE id=checked_root AND disposition='scrap'
        UNION ALL SELECT 'correction',id FROM public.stock_loss_correction_executions
            WHERE root_disposition_id=checked_root AND disposition='scrap'
        UNION ALL SELECT 'inverse',id FROM public.stock_loss_disposition_reversals
            WHERE root_disposition_id=checked_root AND scrap_line_id IS NOT NULL
        ORDER BY kind,id
    LOOP
        key:=item.kind||':'||item.id::text;
        IF plans ? key THEN RAISE EXCEPTION '0165 duplicate proof fact' USING ERRCODE='23514'; END IF;
        plan:=public.rsc_scrap_fact_with_root_proof_0165(item.kind,item.id,checked_root,audit_ids);
        IF jsonb_typeof(plan) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION '0165 complete fact plan required' USING ERRCODE='23514'; END IF;
        plans:=plans||jsonb_build_object(key,plan);
    END LOOP;
    RETURN jsonb_build_object('schema','rsc.private_scrap_history_proof.v1',
        'root_disposition_id',checked_root::text,'plans',plans);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_build_scrap_history_proof_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;

CREATE FUNCTION public.rsc_scrap_history_from_proof_0165(kind text, checked_fact uuid, checked_root uuid, checked_proof jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE plan jsonb;
BEGIN
    IF kind IS NULL OR kind NOT IN ('original','correction','inverse') OR checked_fact IS NULL
       OR checked_root IS NULL OR checked_proof IS NULL
       OR checked_proof->>'schema' IS DISTINCT FROM 'rsc.private_scrap_history_proof.v1'
       OR checked_proof->>'root_disposition_id' IS DISTINCT FROM checked_root::text THEN
        RAISE EXCEPTION '0165 exact private history proof scope required' USING ERRCODE='23514'; END IF;
    plan:=checked_proof->'plans'->(kind||':'||checked_fact::text);
    IF jsonb_typeof(plan) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION '0165 verified history fact required' USING ERRCODE='23514'; END IF;
    RETURN plan;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_history_from_proof_0165(text,uuid,uuid,jsonb)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
