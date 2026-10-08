-- Historical reference binding is separate from current permissions and the
-- complete historical business proof composed by the source helper below.
CREATE FUNCTION public.rsc_condition_decision_seal_reference(command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prepared jsonb; c record; previous record; submitted record; regional record;
    line record; header record; source record; kind text; regional_user text; regional_person uuid;
BEGIN
    prepared:=public.rsc_condition_decision_seal_canonical(command);
    kind:=prepared->>'kind';
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=(prepared->>'case_id')::uuid;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal exact case required' USING ERRCODE='23514'; END IF;
    SELECT * INTO previous FROM public.stock_condition_events
        WHERE id=(prepared->>'expected_event_id')::uuid AND case_id=c.id;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal exact historical event required' USING ERRCODE='23514'; END IF;
    SELECT * INTO submitted FROM public.stock_condition_events e WHERE e.id=c.submit_event_id AND e.case_id=c.id AND e.kind='submit';
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal original submission required' USING ERRCODE='23514'; END IF;
    SELECT * INTO line FROM public.stock_operation_return_inbound_lines WHERE id=c.inbound_line_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal original inbound line required' USING ERRCODE='23514'; END IF;
    SELECT * INTO header FROM public.stock_operation_return_inbounds WHERE id=line.inbound_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal original inbound required' USING ERRCODE='23514'; END IF;
    SELECT * INTO source FROM public.stock_accounts WHERE id=line.target_account_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal original account required' USING ERRCODE='23514'; END IF;
    IF c.operation_type IS DISTINCT FROM 'condition_correction' OR c.inbound_id IS DISTINCT FROM header.id
       OR c.source_account_id IS DISTINCT FROM source.id OR c.recorded_condition IS DISTINCT FROM source.condition_code
       OR c.original_transaction_id IS DISTINCT FROM header.posting_transaction_id
       OR header.status IS DISTINCT FROM 'posted' OR header.plan_jsonb->>'schema_version' IS DISTINCT FROM '1.0'
       OR line.condition_code NOT IN ('new','used') OR source.condition_code IS DISTINCT FROM line.condition_code
       OR source.availability_bucket IS DISTINCT FROM 'available'
       OR source.material_id IS DISTINCT FROM line.material_id OR source.lot_id IS DISTINCT FROM line.lot_id
       OR source.location_id IS DISTINCT FROM header.target_location_id
       OR source.custodian_person_id IS DISTINCT FROM header.operator_person_id
       OR submitted.actor_person_id IS DISTINCT FROM source.custodian_person_id
       OR submitted.submit_event_id IS DISTINCT FROM submitted.id
       OR previous.submit_event_id IS DISTINCT FROM submitted.id
       OR previous.inbound_line_id IS DISTINCT FROM line.id OR submitted.inbound_line_id IS DISTINCT FROM line.id
       OR previous.source_account_id IS DISTINCT FROM source.id OR submitted.source_account_id IS DISTINCT FROM source.id
       OR previous.event_sequence<submitted.event_sequence
       OR NOT EXISTS (SELECT 1 FROM (VALUES
          ('verify_region','awaiting_regional'),('return_evidence','awaiting_regional'),
          ('supplement','needs_evidence'),('return_region','awaiting_headquarters'),
          ('reject_region','awaiting_regional'),('reject_hq','awaiting_headquarters'),
          ('approve_hq','awaiting_headquarters'),('withdraw','awaiting_regional'),
          ('withdraw','awaiting_headquarters'),('withdraw','needs_evidence'),('cancel_approved','approved'),
          ('execute','approved'),('release','rejected_pending_release'),('release','cancelled_pending_release')
       ) allowed(action,state) WHERE action=kind AND state=previous.to_state) THEN
        RAISE EXCEPTION 'decision seal exact source and historical action required' USING ERRCODE='23514'; END IF;
    IF kind IN ('return_region','reject_hq','approve_hq','cancel_approved') THEN
        SELECT * INTO regional FROM public.stock_condition_events e
            WHERE e.case_id=c.id AND e.kind='verify_region' AND e.event_sequence<=previous.event_sequence
            ORDER BY e.event_sequence DESC LIMIT 1;
        IF NOT FOUND THEN RAISE EXCEPTION 'decision seal historical regional verification required' USING ERRCODE='23514'; END IF;
        regional_user:=regional.actor_user_id; regional_person:=regional.actor_person_id;
    END IF;
    -- Do not compare the claimed old preflight hash to previous.request_hash,
    -- require the latest event, or check today's custody/available balance.
    RETURN prepared||jsonb_build_object('root_disposition_id',c.root_disposition_id,
        'inbound_id',c.inbound_id,'inbound_line_id',c.inbound_line_id,'source_account_id',source.id,
        'original_transaction_id',c.original_transaction_id,'original_movement_id',c.original_movement_id,
        'original_ledger_cursor',c.original_ledger_cursor,'historical_state',previous.to_state,
        'historical_sequence',previous.event_sequence,'owner_org_id',source.owner_org_id,
        'requester_user_id',submitted.actor_user_id,'requester_person_id',submitted.actor_person_id,
        'regional_user_id',regional_user,'regional_person_id',regional_person,
        'source_created_at',greatest(header.created_at,submitted.created_at,previous.created_at));
END $$;

CREATE FUNCTION public.rsc_condition_decision_seal_actor(actor_id text, actor_person uuid, prepared jsonb)
RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    IF actor_id IS NULL OR actor_person IS NULL OR prepared IS NULL
       OR prepared->>'kind' IS NULL OR prepared->>'kind' NOT IN ('supplement','withdraw','verify_region',
            'return_evidence','reject_region','return_region','reject_hq','approve_hq','cancel_approved','execute','release')
       OR prepared->>'requester_user_id' IS NULL
       OR prepared->>'requester_person_id' IS NULL THEN
        RAISE EXCEPTION 'decision seal original actor binding required' USING ERRCODE='23514'; END IF;
    IF prepared->>'kind' IN ('supplement','withdraw','execute','release') THEN
        IF actor_id IS DISTINCT FROM prepared->>'requester_user_id'
           OR actor_person::text IS DISTINCT FROM prepared->>'requester_person_id' THEN
            RAISE EXCEPTION 'decision seal only original requester may close own action' USING ERRCODE='23514'; END IF;
    ELSE
        IF actor_id=prepared->>'requester_user_id' OR actor_person::text=prepared->>'requester_person_id' THEN
            RAISE EXCEPTION 'decision seal requester cannot self review' USING ERRCODE='23514'; END IF;
        IF prepared->>'kind' IN ('return_region','reject_hq','approve_hq','cancel_approved') AND
           (prepared->>'regional_user_id' IS NULL OR prepared->>'regional_person_id' IS NULL
            OR actor_id=prepared->>'regional_user_id' OR actor_person::text=prepared->>'regional_person_id') THEN
            RAISE EXCEPTION 'decision seal headquarters reviewer must remain independent' USING ERRCODE='23514'; END IF;
    END IF;
END $$;

-- All business/source verifiers are real candidate functions. Do not replace
-- them with stubs to claim a successful source proof or grant a write API.
CREATE FUNCTION public.rsc_condition_decision_seal_source(command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prepared jsonb; fresh jsonb; original_line uuid; original_root uuid; item record; snapshot jsonb;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'decision seal source requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal source ledger head required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_condition_decision_seal_reference(command);
    original_line:=(prepared->>'inbound_line_id')::uuid;
    original_root:=(prepared->>'root_disposition_id')::uuid;
    PERFORM 1 FROM public.stock_operation_return_inbound_lines WHERE id=original_line FOR NO KEY UPDATE;
    IF NOT EXISTS(SELECT 1 FROM public.stock_loss_dispositions d
        JOIN public.stock_operation_shipments p ON p.operation_id=d.return_operation_id
        JOIN public.stock_operation_return_inbounds h ON h.shipment_id=p.id
        WHERE d.id=original_root AND d.disposition='return_to_region' AND h.id=(prepared->>'inbound_id')::uuid)
       OR (SELECT count(*) FROM public.stock_loss_dispositions d
        JOIN public.stock_operation_shipments p ON p.operation_id=d.return_operation_id
        JOIN public.stock_operation_return_inbounds h ON h.shipment_id=p.id
        WHERE d.disposition='return_to_region' AND h.id=(prepared->>'inbound_id')::uuid)<>1 THEN
        RAISE EXCEPTION 'decision seal unique historical return root required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_history_graph_0159(original_root);
    PERFORM public.rsc_check_stock_return_inbound_0111((prepared->>'inbound_id')::uuid);
    PERFORM public.rsc_condition_check_source(original_line);
    FOR item IN SELECT id FROM public.stock_condition_cases WHERE inbound_line_id=original_line ORDER BY id LOOP
        PERFORM public.rsc_condition_check_posting(item.id);
        PERFORM public.rsc_condition_check_identity(item.id);
    END LOOP;
    FOR item IN SELECT id FROM public.stock_condition_events WHERE inbound_line_id=original_line ORDER BY event_sequence LOOP
        PERFORM public.rsc_condition_check_event_files(item.id);
        PERFORM public.rsc_condition_check_request_input(item.id);
        PERFORM public.rsc_condition_check_settlement_input(item.id);
        PERFORM public.rsc_condition_check_request_key(item.id);
        PERFORM public.rsc_condition_check_business_effects(item.id);
    END LOOP;
    fresh:=public.rsc_condition_decision_seal_reference(command);
    IF fresh IS DISTINCT FROM prepared THEN
        RAISE EXCEPTION 'decision seal source changed during verification' USING ERRCODE='23514'; END IF;
    SELECT jsonb_build_object('cases',COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id)
        FROM public.stock_condition_cases c WHERE c.inbound_line_id=original_line),'[]'::jsonb),
        'events',COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.event_sequence)
        FROM public.stock_condition_events e WHERE e.inbound_line_id=original_line),'[]'::jsonb)) INTO snapshot;
    -- This is the SQL observation digest at closure, not the client's old
    -- preflight and not a requirement that later history never advances.
    RETURN prepared||jsonb_build_object('history_hash',encode(sha256(convert_to(
        public.rsc_canonical_reconciliation_json_0026(snapshot),'UTF8')),'hex'));
END $$;

CREATE FUNCTION public.rsc_condition_decision_seal_authority(
    actor_id text, actor_version bigint, actor_person uuid, command jsonb, require_write boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prepared jsonb; verified jsonb;
BEGIN
    -- Match every other condition writer: inventory head before principal
    -- graph and source locks, including when an existing seal is rechecked.
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'decision seal authority requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'decision seal authority ledger head required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_condition_decision_seal_reference(command);
    PERFORM public.rsc_condition_decision_seal_permission(actor_id,actor_version,actor_person,
        (prepared->>'owner_org_id')::uuid,prepared->>'kind',require_write);
    PERFORM public.rsc_condition_decision_seal_actor(actor_id,actor_person,prepared);
    verified:=public.rsc_condition_decision_seal_source(command);
    IF verified-'history_hash' IS DISTINCT FROM prepared THEN
        RAISE EXCEPTION 'decision seal historical binding changed' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_condition_decision_seal_permission(actor_id,actor_version,actor_person,
        (verified->>'owner_org_id')::uuid,verified->>'kind',require_write);
    PERFORM public.rsc_condition_decision_seal_actor(actor_id,actor_person,verified);
    RETURN verified;
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_decision_seal_reference(jsonb),
    public.rsc_condition_decision_seal_actor(text,uuid,jsonb),
    public.rsc_condition_decision_seal_source(jsonb),
    public.rsc_condition_decision_seal_authority(text,bigint,uuid,jsonb,boolean)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
