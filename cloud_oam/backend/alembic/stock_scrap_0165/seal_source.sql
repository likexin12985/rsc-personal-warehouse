-- Private persisted-source proof for seal registration, not an authorization
-- or absence proof. Historical sources remain valid after later executions.
CREATE FUNCTION public.rsc_scrap_seal_source_0165(checked_kind text, command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE prepared jsonb; source jsonb; anchors jsonb; source_fact jsonb; source_time timestamptz;
    original_decision public.stock_loss_headquarters_decisions%ROWTYPE;
    headquarters_review public.stock_loss_headquarters_reviews%ROWTYPE;
    loss_order public.stock_operation_orders%ROWTYPE; loss_line public.stock_operation_lines%ROWTYPE;
    root public.stock_loss_dispositions%ROWTYPE; inverse public.stock_loss_disposition_reversals%ROWTYPE;
    correction public.stock_loss_correction_decisions%ROWTYPE; account public.stock_accounts%ROWTYPE;
    scrap public.stock_scrap_lines%ROWTYPE; application public.stock_scrap_recovery_requests%ROWTYPE;
    region public.stock_scrap_recovery_regional_reviews%ROWTYPE;
    final public.stock_scrap_recovery_headquarters_reviews%ROWTYPE;
BEGIN
    prepared:=public.rsc_scrap_seal_canonical_0165(checked_kind,command);
    anchors:=jsonb_build_object('root_disposition_id',NULL,'original_decision_id',NULL,
        'reversal_id',NULL,'correction_decision_id',NULL,'scrap_line_id',NULL,'recovery_request_id',NULL,
        'regional_review_id',NULL,'headquarters_review_id',NULL,'regional_decision',NULL,'headquarters_decision',NULL);
    IF checked_kind IN ('original','correction') THEN source:=command#>'{intent,source}';
    ELSE source:=command->'source'; END IF;
    IF checked_kind='original' THEN
        SELECT * INTO original_decision FROM public.stock_loss_headquarters_decisions WHERE id=(source->>'headquarters_decision_id')::uuid;
        SELECT * INTO headquarters_review FROM public.stock_loss_headquarters_reviews WHERE id=original_decision.review_id;
        SELECT * INTO loss_line FROM public.stock_operation_lines WHERE id=original_decision.line_id;
        SELECT * INTO loss_order FROM public.stock_operation_orders WHERE id=loss_line.operation_id;
        IF original_decision.id IS NULL OR headquarters_review.id IS NULL
           OR original_decision.disposition IS DISTINCT FROM 'scrap'
           OR headquarters_review.operation_id IS DISTINCT FROM loss_order.id
           OR headquarters_review.request_hash IS DISTINCT FROM source->>'expected_headquarters_review_hash'
           OR loss_order.plan_hash IS DISTINCT FROM source->>'expected_submission_plan_hash' THEN
            RAISE EXCEPTION '0165 exact original seal approval required' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_loss_inventory_audit_members_0159();
        PERFORM public.rsc_check_loss_submission_history_0159(loss_order.id);
        PERFORM public.rsc_check_loss_regional_history_0159(headquarters_review.regional_review_id);
        PERFORM public.rsc_check_loss_headquarters_history_0159(headquarters_review.id);
        -- No root is fabricated or returned, even if another request later
        -- executes this decision. Existing histories must still be complete.
        FOR root IN SELECT * FROM public.stock_loss_dispositions WHERE headquarters_decision_id=original_decision.id LOOP
            PERFORM public.rsc_check_loss_history_graph_0159(root.id);
        END LOOP;
        anchors:=anchors||jsonb_build_object('original_decision_id',original_decision.id);
        source_time:=headquarters_review.created_at;
    ELSIF checked_kind='correction' THEN
        SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=(source->>'root_disposition_id')::uuid;
        SELECT * INTO inverse FROM public.stock_loss_disposition_reversals WHERE id=(source->>'reversal_id')::uuid;
        SELECT * INTO correction FROM public.stock_loss_correction_decisions WHERE id=(source->>'correction_decision_id')::uuid;
        SELECT * INTO loss_line FROM public.stock_operation_lines WHERE id=root.line_id;
        SELECT * INTO loss_order FROM public.stock_operation_orders WHERE id=root.operation_id;
        IF root.id IS NULL OR inverse.id IS NULL OR correction.id IS NULL
           OR root.request_hash IS DISTINCT FROM source->>'expected_root_request_hash'
           OR loss_order.plan_hash IS DISTINCT FROM source->>'expected_submission_plan_hash'
           OR inverse.root_disposition_id IS DISTINCT FROM root.id
           OR inverse.request_hash IS DISTINCT FROM source->>'expected_reversal_hash'
           OR correction.root_disposition_id IS DISTINCT FROM root.id
           OR correction.reversal_id IS DISTINCT FROM inverse.id
           OR correction.expected_reversal_hash IS DISTINCT FROM inverse.request_hash
           OR correction.request_hash IS DISTINCT FROM source->>'expected_correction_decision_hash'
           OR correction.disposition IS DISTINCT FROM 'scrap' THEN
            RAISE EXCEPTION '0165 exact corrected seal approval required' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_check_loss_history_graph_0159(root.id);
        anchors:=anchors||jsonb_build_object('root_disposition_id',root.id,'reversal_id',inverse.id,
            'correction_decision_id',correction.id);
        source_time:=correction.created_at;
    ELSE
        SELECT * INTO scrap FROM public.stock_scrap_lines WHERE id=(source->>'scrap_line_id')::uuid;
        SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=scrap.root_disposition_id;
        SELECT * INTO loss_line FROM public.stock_operation_lines WHERE id=scrap.loss_line_id;
        SELECT * INTO loss_order FROM public.stock_operation_orders WHERE id=root.operation_id;
        IF scrap.id IS NULL OR root.id IS NULL THEN
            RAISE EXCEPTION '0165 exact seal scrap source required' USING ERRCODE='23514'; END IF;
        IF scrap.source_kind='original' THEN source_fact:=to_jsonb(root);
        ELSE
            SELECT to_jsonb(c) INTO source_fact FROM public.stock_loss_correction_executions c WHERE c.id=scrap.correction_execution_id;
        END IF;
        IF source_fact IS NULL OR source_fact->>'request_hash' IS DISTINCT FROM source->>'expected_scrap_request_hash' THEN
            RAISE EXCEPTION '0165 seal scrap request hash differs' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_scrap_recovery_source_0165(scrap.id);
        PERFORM public.rsc_check_loss_history_graph_0159(root.id);
        PERFORM public.rsc_check_scrap_recovery_approvals_0165(scrap.id);
        anchors:=anchors||jsonb_build_object('root_disposition_id',root.id,'scrap_line_id',scrap.id);
        source_time:=scrap.created_at;
        IF checked_kind<>'apply' THEN
            SELECT * INTO application FROM public.stock_scrap_recovery_requests WHERE id=(command->>'recovery_request_id')::uuid;
            IF application.id IS NULL OR application.scrap_line_id IS DISTINCT FROM scrap.id
               OR application.request_hash IS DISTINCT FROM command->>'expected_request_hash' THEN
                RAISE EXCEPTION '0165 exact seal recovery application required' USING ERRCODE='23514'; END IF;
            anchors:=anchors||jsonb_build_object('recovery_request_id',application.id);
            source_time:=application.created_at;
            IF checked_kind='headquarters' THEN
                SELECT * INTO region FROM public.stock_scrap_recovery_regional_reviews WHERE id=(command->>'regional_review_id')::uuid;
                IF region.id IS NULL OR region.scrap_line_id IS DISTINCT FROM scrap.id
                   OR region.recovery_request_id IS DISTINCT FROM application.id OR region.decision IS DISTINCT FROM 'verified'
                   OR region.request_hash IS DISTINCT FROM command->>'expected_regional_hash' THEN
                    RAISE EXCEPTION '0165 exact verified seal regional source required' USING ERRCODE='23514'; END IF;
                anchors:=anchors||jsonb_build_object('regional_review_id',region.id,'regional_decision','verified');
                source_time:=region.created_at;
            ELSIF checked_kind='execute' THEN
                SELECT * INTO final FROM public.stock_scrap_recovery_headquarters_reviews WHERE id=(command->>'headquarters_review_id')::uuid;
                IF final.id IS NULL OR final.scrap_line_id IS DISTINCT FROM scrap.id
                   OR final.recovery_request_id IS DISTINCT FROM application.id OR final.decision IS DISTINCT FROM 'approve'
                   OR final.request_hash IS DISTINCT FROM command->>'expected_headquarters_hash' THEN
                    RAISE EXCEPTION '0165 exact approved seal headquarters source required' USING ERRCODE='23514'; END IF;
                anchors:=anchors||jsonb_build_object('headquarters_review_id',final.id,'headquarters_decision','approve');
                source_time:=final.created_at;
            END IF;
        END IF;
    END IF;
    SELECT * INTO account FROM public.stock_accounts WHERE id=loss_line.reserved_account_id;
    IF loss_order.id IS NULL OR loss_line.id IS NULL OR account.id IS NULL OR source_time IS NULL
       OR source_time>clock_timestamp() OR loss_line.operation_id IS DISTINCT FROM loss_order.id
       OR loss_order.operation_type IS DISTINCT FROM 'loss_report' OR loss_line.operation_type IS DISTINCT FROM 'loss_report'
       OR account.location_id IS DISTINCT FROM loss_order.source_location_id
       OR account.custodian_person_id IS DISTINCT FROM loss_order.requester_id
       OR account.availability_bucket IS DISTINCT FROM 'frozen' THEN
        RAISE EXCEPTION '0165 exact original loss scope for seal required' USING ERRCODE='23514'; END IF;
    RETURN prepared||anchors||jsonb_build_object('loss_operation_id',loss_order.id,'loss_line_id',loss_line.id,
        'scrap_disposition','scrap','source_created_at',source_time,'owner_org_id',account.owner_org_id,
        'location_id',account.location_id,'requester_id',loss_order.requester_id,
        'applicant_user_id',application.actor_user_id,'applicant_person_id',application.actor_person_id,
        'regional_user_id',region.actor_user_id,'regional_person_id',region.actor_person_id);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_seal_source_0165(text,jsonb)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_prepare_scrap_seal_source_0165(checked_kind text, command jsonb, client_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
BEGIN
    RETURN public.rsc_prepare_scrap_seal_request_0165(checked_kind,command,client_key)
        ||public.rsc_scrap_seal_source_0165(checked_kind,command);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_prepare_scrap_seal_source_0165(text,jsonb,text)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
