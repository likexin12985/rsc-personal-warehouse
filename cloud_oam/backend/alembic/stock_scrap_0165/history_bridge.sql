-- Historical proof only. Current authority, request admission, serial lifecycle
-- and runtime readiness must be integrated before enabling new business writes.
CREATE FUNCTION public.rsc_check_scrap_history_0165(kind text, checked_fact uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; account public.stock_accounts%ROWTYPE;
    plan jsonb; effective timestamptz;
BEGIN
    IF kind='original' THEN
        SELECT * INTO STRICT line FROM public.stock_scrap_lines
            WHERE source_kind='original' AND root_disposition_id=checked_fact;
        SELECT plan_jsonb,created_at INTO plan,effective FROM public.stock_loss_dispositions
            WHERE id=checked_fact AND disposition='scrap';
    ELSIF kind='correction' THEN
        SELECT * INTO STRICT line FROM public.stock_scrap_lines
            WHERE source_kind='correction' AND correction_execution_id=checked_fact;
        SELECT plan_jsonb,created_at INTO plan,effective FROM public.stock_loss_correction_executions
            WHERE id=checked_fact AND disposition='scrap';
    ELSIF kind='inverse' THEN
        SELECT l.* INTO STRICT line FROM public.stock_scrap_lines l
            JOIN public.stock_scrap_recovery_executions e ON e.scrap_line_id=l.id
            JOIN public.stock_loss_disposition_reversals r ON r.id=e.reversal_id AND r.scrap_line_id=l.id
            WHERE r.id=checked_fact;
        SELECT plan_jsonb,created_at INTO plan,effective FROM public.stock_loss_disposition_reversals
            WHERE id=checked_fact;
    ELSE
        RAISE EXCEPTION '0165 explicit scrap history fact kind required' USING ERRCODE='23514';
    END IF;
    IF plan IS NULL OR effective IS NULL THEN
        RAISE EXCEPTION '0165 retained scrap history fact required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_scrap_execution_events_0165(line.id);
    PERFORM public.rsc_check_loss_upstream_history_0159(line.root_disposition_id);
    SELECT * INTO account FROM public.stock_accounts WHERE id=line.frozen_account_id;
    IF NOT EXISTS(SELECT 1 FROM public.inventory_opening_establishments e
        JOIN public.stocktake_postings p ON p.id=e.posting_id AND p.task_id=e.task_id AND p.round_id=e.round_id
        WHERE e.owner_org_id=account.owner_org_id AND e.location_id=account.location_id AND e.established_at<=effective
          AND public.rsc_loss_opening_history_complete_0159(e.task_id,p.inventory_transaction_id)) THEN
        RAISE EXCEPTION '0165 complete established scrap opening required' USING ERRCODE='23514'; END IF;
    RETURN plan;
EXCEPTION WHEN no_data_found OR too_many_rows THEN
    RAISE EXCEPTION '0165 unique retained scrap history binding required' USING ERRCODE='23514';
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_history_0165(text,uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
