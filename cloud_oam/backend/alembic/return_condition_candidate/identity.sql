-- Canonical fact-derived command bindings. These hashes do not establish
-- current authority, truthful physical/file evidence, audit or request seals.
CREATE FUNCTION public.rsc_condition_check_identity(checked_case uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE c public.stock_condition_cases%ROWTYPE; header public.stock_operation_orders%ROWTYPE;
        line public.stock_operation_lines%ROWTYPE; source public.stock_accounts%ROWTYPE;
        event_row record; tx public.inventory_transactions%ROWTYPE;
        actor jsonb; plan jsonb; command jsonb; files jsonb; ids jsonb; actual_ids jsonb;
        previous_hash text; plan_hash text; key text; posting_key text; posting_document jsonb;
BEGIN
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=checked_case;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition identity case missing' USING ERRCODE='23514'; END IF;
    SELECT * INTO header FROM public.stock_operation_orders WHERE id=c.id;
    SELECT * INTO line FROM public.stock_operation_lines WHERE id=c.line_id;
    SELECT * INTO source FROM public.stock_accounts WHERE id=c.source_account_id;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb) INTO ids
        FROM public.stock_condition_serials WHERE case_id=c.id;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb) INTO actual_ids
        FROM public.stock_operation_serials WHERE line_id=c.line_id;
    IF actual_ids IS DISTINCT FROM ids OR EXISTS (SELECT 1 FROM public.stock_operation_serials
        WHERE line_id=c.line_id AND (NOT sku_verified OR NOT qr_verified OR created_at<>header.created_at)) THEN
        RAISE EXCEPTION 'condition common operation serial proof mismatch' USING ERRCODE='23514';
    END IF;
    IF NOT COALESCE(header.id IS NOT NULL AND header.operation_type='condition_correction'
        AND header.status='submitted' AND header.operation_no='COND-'||upper(replace(c.id::text,'-',''))
        AND header.source_location_id=source.location_id AND header.requester_id=source.custodian_person_id
        AND line.id IS NOT NULL AND line.operation_id=c.id AND line.operation_type='condition_correction'
        AND line.line_no=1 AND line.material_id=source.material_id AND line.created_at=header.created_at
        AND line.reason=header.reason,false)
        OR (SELECT count(*) FROM public.stock_operation_lines WHERE operation_id=c.id)<>1 THEN
        RAISE EXCEPTION 'condition common operation identity mismatch' USING ERRCODE='23514';
    END IF;
    FOR event_row IN SELECT * FROM public.stock_condition_events WHERE case_id=c.id ORDER BY event_sequence LOOP
        actor:=jsonb_build_object('user_id',event_row.actor_user_id,'person_id',event_row.actor_person_id::text,
            'authorization_version',event_row.authorization_version);
        plan:=jsonb_build_object('schema_version','condition_plan/1','case_id',c.id::text,'event_id',event_row.id::text,
            'action',event_row.kind,'actor',actor,'history_hash',c.history_hash,'source_hash',c.source_hash,
            'root_disposition_id',c.root_disposition_id::text,'inbound_id',c.inbound_id::text,
            'original_transaction_id',c.original_transaction_id::text,'original_movement_id',c.original_movement_id::text,
            'original_ledger_cursor',c.original_ledger_cursor,'custody_assignment_id',c.custody_assignment_id::text,
            'recorded_condition',c.recorded_condition,'target_condition',c.target_condition,
            'affected_quantity',trim_scale(c.affected_quantity)::text,'tracking_mode',c.tracking_mode,
            'quantity_scale',c.quantity_scale,'allow_fraction',c.allow_fraction,
            'inbound_line_id',c.inbound_line_id::text,'source_account_id',c.source_account_id::text,
            'frozen_account_id',c.frozen_account_id::text,'quantity',trim_scale(c.quantity)::text,'serial_ids',ids,
            'previous_event_id',event_row.previous_event_id::text,'decision_event_id',event_row.decision_event_id::text,
            'movement_type',event_row.movement_type,'from_account_id',event_row.from_account_id::text,
            'to_account_id',event_row.to_account_id::text);
        plan_hash:=encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(plan),'UTF8')),'hex');
        SELECT request_hash INTO previous_hash FROM public.stock_condition_events WHERE id=event_row.previous_event_id;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('file_id',file_id::text,'metadata_sha256',metadata_sha256)
            ORDER BY file_id::text),'[]'::jsonb) INTO files FROM public.stock_condition_files WHERE event_id=event_row.id;
        IF (event_row.kind IN ('submit','verify_region','supplement') AND jsonb_array_length(files)=0)
            OR EXISTS (SELECT 1 FROM public.stock_condition_files WHERE event_id=event_row.id
                AND (created_at<>event_row.created_at OR metadata_sha256 !~ '^[0-9a-f]{64}$')) THEN
            RAISE EXCEPTION 'condition exact evidence references required' USING ERRCODE='23514';
        END IF;
        command:=jsonb_build_object('schema_version','condition_command/1','action',event_row.kind,
            'case_id',c.id::text,'event_id',event_row.id::text,'request_id',event_row.request_id,'actor',actor,
            'idempotency_key_hash',event_row.idempotency_key_hash,'expected_plan_hash',plan_hash,
            'previous_event_id',event_row.previous_event_id::text,'previous_request_hash',previous_hash,
            'reason',event_row.reason,'evidence',files);
        IF event_row.plan_jsonb IS DISTINCT FROM plan OR event_row.plan_hash IS DISTINCT FROM plan_hash
            OR event_row.command_jsonb IS DISTINCT FROM command
            OR event_row.request_hash IS DISTINCT FROM encode(sha256(convert_to(
                public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex')
            OR event_row.idempotency_key_hash !~ '^[0-9a-f]{64}$'
            OR event_row.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
            OR event_row.reason !~ '[^[:space:]]' THEN
            RAISE EXCEPTION 'condition canonical command identity mismatch' USING ERRCODE='23514';
        END IF;
        IF event_row.kind='submit' THEN
            IF header.requester_id IS DISTINCT FROM event_row.actor_person_id THEN
                RAISE EXCEPTION 'condition common requester mismatch' USING ERRCODE='23514';
            END IF;
            FOREACH key IN ARRAY ARRAY['created_at','actor_user_id','authorization_version','reason','request_id',
                'idempotency_key_hash','request_hash','plan_hash','command_jsonb','plan_jsonb','posting_transaction_id'] LOOP
                IF to_jsonb(header)->key IS DISTINCT FROM to_jsonb(event_row)->key THEN
                    RAISE EXCEPTION 'condition common submission context mismatch' USING ERRCODE='23514';
                END IF;
            END LOOP;
        END IF;
        IF event_row.kind NOT IN ('submit','release','execute') THEN CONTINUE; END IF;
        SELECT * INTO tx FROM public.inventory_transactions WHERE id=event_row.posting_transaction_id;
        posting_key:='stock-condition:posting:'||event_row.id::text||':'||event_row.idempotency_key_hash;
        posting_document:=jsonb_build_object('operation','post','actor',actor,'command',jsonb_build_object(
            'effective_at',to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'movement_type',event_row.movement_type,'movements',jsonb_build_array(jsonb_build_object(
                'external_boundary_code',NULL,'from_account_id',event_row.from_account_id::text,
                'to_account_id',event_row.to_account_id::text,'quantity',trim_scale(c.quantity)::text,'serial_ids',ids)),
            'posting_key',posting_key,'source_document_id',event_row.id::text,'source_document_type','stock_condition_event',
            'transaction_no','INV-COND-'||upper(left(event_row.idempotency_key_hash,24))));
        IF tx.posting_key IS DISTINCT FROM posting_key
            OR tx.transaction_no IS DISTINCT FROM 'INV-COND-'||upper(left(event_row.idempotency_key_hash,24))
            OR tx.idempotency_key_hash IS DISTINCT FROM encode(sha256(
                convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')||convert_to(posting_key,'UTF8')),'hex')
            OR tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(
                public.rsc_canonical_reconciliation_json_0026(posting_document),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'condition canonical inventory request mismatch' USING ERRCODE='23514';
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION public.rsc_condition_identity_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE old_row jsonb; new_row jsonb; checked uuid; ids uuid[];
BEGIN
    IF TG_OP<>'INSERT' THEN old_row:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN new_row:=to_jsonb(NEW); END IF;
    IF TG_TABLE_NAME='stock_condition_cases' THEN ids:=ARRAY[(new_row->>'id')::uuid];
    ELSIF TG_TABLE_NAME IN ('stock_condition_events','stock_condition_serials') THEN ids:=ARRAY[(new_row->>'case_id')::uuid];
    ELSIF TG_TABLE_NAME='stock_condition_files' THEN
        SELECT array_agg(case_id) INTO ids FROM public.stock_condition_events WHERE id=(new_row->>'event_id')::uuid;
    ELSIF TG_TABLE_NAME='stock_operation_orders' THEN
        SELECT array_agg(id) INTO ids FROM public.stock_condition_cases
            WHERE id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid);
    ELSIF TG_TABLE_NAME='stock_operation_lines' THEN
        SELECT array_agg(id) INTO ids FROM public.stock_condition_cases
            WHERE id IN ((old_row->>'operation_id')::uuid,(new_row->>'operation_id')::uuid);
    ELSIF TG_TABLE_NAME='stock_operation_serials' THEN
        SELECT array_agg(id) INTO ids FROM public.stock_condition_cases
            WHERE line_id IN ((old_row->>'line_id')::uuid,(new_row->>'line_id')::uuid);
    ELSE
        SELECT array_agg(case_id) INTO ids FROM public.stock_condition_events
            WHERE posting_transaction_id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid);
    END IF;
    FOR checked IN SELECT DISTINCT unnest(ids) LOOP
        PERFORM public.rsc_condition_check_identity(checked);
    END LOOP;
    RETURN NULL;
END $$;
