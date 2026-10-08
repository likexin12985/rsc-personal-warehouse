-- Preserve the full old input. This proves syntax/binding, not a lost preflight
-- or the physical truth of old scans. No current SKU/file/stock substitution.
CREATE FUNCTION public.rsc_condition_seal_canonical(command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE expected jsonb; files jsonb; scans jsonb; item jsonb; seen text[]:='{}'; value text; qty numeric; reason text;
BEGIN
    IF command IS NULL OR jsonb_typeof(command)<>'object'
       OR NOT COALESCE(command->>'request_id' ~ '^[A-Za-z0-9._:-]{8,160}$'
          AND command->>'expected_source_hash' ~ '^[a-f0-9]{64}$'
          AND command->>'idempotency_key_hash' ~ '^[a-f0-9]{64}$'
          AND jsonb_typeof(command->'quantity')='string'
          AND command->>'quantity' ~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,3})?$',false) THEN
        RAISE EXCEPTION 'condition seal complete original input required' USING ERRCODE='23514'; END IF;
    qty:=(command->>'quantity')::numeric;
    reason:=command->>'reason';
    IF qty<=0 OR trim_scale(qty)::text IS DISTINCT FROM command->>'quantity'
       OR jsonb_typeof(command->'reason') IS DISTINCT FROM 'string'
       OR NOT COALESCE(length(reason) BETWEEN 1 AND 500,false)
       OR reason IS DISTINCT FROM btrim(reason,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288))
       OR EXISTS(SELECT 1 FROM generate_series(1,length(reason)) p WHERE ascii(substr(reason,p,1))<32 AND ascii(substr(reason,p,1)) NOT IN (9,10)) THEN
        RAISE EXCEPTION 'condition seal exact quantity and reason required' USING ERRCODE='23514'; END IF;
    value:=command->>'inbound_line_id';
    IF value IS NULL OR value::uuid='00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION 'condition seal nonzero inbound reference required' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(command->'evidence_file_ids') IS DISTINCT FROM 'array'
       OR jsonb_typeof(command->'serial_verifications') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'condition seal original evidence arrays required' USING ERRCODE='23514'; END IF;
    files:=command->'evidence_file_ids'; scans:=command->'serial_verifications';
    IF jsonb_array_length(files) NOT BETWEEN 1 AND 20 OR jsonb_array_length(scans)>1000 THEN
        RAISE EXCEPTION 'condition seal original evidence bounds required' USING ERRCODE='23514'; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(files) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'string' OR (item#>>'{}')::uuid='00000000-0000-0000-0000-000000000000'::uuid
           OR (item#>>'{}')::uuid::text IS DISTINCT FROM item#>>'{}' OR item#>>'{}'=ANY(seen) THEN
            RAISE EXCEPTION 'condition seal canonical unique files required' USING ERRCODE='23514'; END IF;
        seen:=seen||(item#>>'{}');
    END LOOP;
    IF files IS DISTINCT FROM (SELECT jsonb_agg(v ORDER BY v COLLATE "C") FROM unnest(seen) v) THEN
        RAISE EXCEPTION 'condition seal sorted files required' USING ERRCODE='23514'; END IF;
    seen:='{}';
    FOR item IN SELECT * FROM jsonb_array_elements(scans) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR item->>'serial_id' IS NULL
           OR item IS DISTINCT FROM jsonb_build_object('serial_id',(item->>'serial_id')::uuid::text,
                'sku_code',item->>'sku_code','serial_no',item->>'serial_no','qr_code',item->>'qr_code')
           OR NOT COALESCE(length(item->>'sku_code') BETWEEN 1 AND 80
                AND length(item->>'serial_no') BETWEEN 1 AND 200 AND length(item->>'qr_code') BETWEEN 1 AND 250,false)
           OR item->>'serial_id'=ANY(seen) THEN
            RAISE EXCEPTION 'condition seal canonical unique scans required' USING ERRCODE='23514'; END IF;
        seen:=seen||(item->>'serial_id');
    END LOOP;
    IF scans IS DISTINCT FROM COALESCE((SELECT jsonb_agg(v ORDER BY v->>'serial_id' COLLATE "C") FROM jsonb_array_elements(scans) v),'[]'::jsonb) THEN
        RAISE EXCEPTION 'condition seal sorted scans required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('schema_version','condition_input/1','action','submit_return_condition',
        'inbound_line_id',value::uuid::text,'expected_source_hash',command->>'expected_source_hash',
        'quantity',trim_scale(qty)::text,'serial_verifications',scans,'evidence_file_ids',files,
        'reason',reason,'request_id',command->>'request_id','idempotency_key_hash',command->>'idempotency_key_hash');
    IF command IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'condition seal exact canonical input required' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('kind','submit','command_jsonb',expected,'request_id',command->>'request_id',
        'reason',reason,'idempotency_key_hash',command->>'idempotency_key_hash',
        'request_hash',encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex'));
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RAISE EXCEPTION 'condition seal original input syntax invalid' USING ERRCODE='23514';
END $$;

CREATE FUNCTION public.rsc_condition_seal_source(command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE prepared jsonb; line public.stock_operation_return_inbound_lines%ROWTYPE;
    header public.stock_operation_return_inbounds%ROWTYPE; account public.stock_accounts%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE; movement public.inventory_movements%ROWTYPE;
    root_id uuid; count_roots bigint;
BEGIN
    prepared:=public.rsc_condition_seal_canonical(command);
    SELECT * INTO line FROM public.stock_operation_return_inbound_lines WHERE id=(command->>'inbound_line_id')::uuid;
    SELECT * INTO header FROM public.stock_operation_return_inbounds WHERE id=line.inbound_id;
    SELECT * INTO account FROM public.stock_accounts WHERE id=line.target_account_id;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=header.posting_transaction_id;
    SELECT * INTO movement FROM public.inventory_movements WHERE transaction_id=tx.id AND line_no=line.line_no;
    SELECT count(*), (array_agg(d.id))[1] INTO count_roots,root_id FROM public.stock_loss_dispositions d
        JOIN public.stock_operation_shipments p ON p.operation_id=d.return_operation_id
        WHERE p.id=header.shipment_id AND d.disposition='return_to_region';
    IF count_roots<>1 OR line.id IS NULL OR header.id IS NULL OR account.id IS NULL OR tx.id IS NULL OR movement.id IS NULL
       OR header.status IS DISTINCT FROM 'posted' OR header.plan_jsonb->>'schema_version' IS DISTINCT FROM '1.0'
       OR line.condition_code NOT IN ('new','used') OR account.condition_code IS DISTINCT FROM line.condition_code
       OR account.availability_bucket IS DISTINCT FROM 'available' OR account.location_id IS DISTINCT FROM header.target_location_id
       OR account.custodian_person_id IS DISTINCT FROM header.operator_person_id
       OR account.material_id IS DISTINCT FROM line.material_id OR account.lot_id IS DISTINCT FROM line.lot_id
       OR movement.from_account_id IS DISTINCT FROM line.source_account_id OR movement.to_account_id IS DISTINCT FROM account.id
       OR movement.quantity IS DISTINCT FROM line.accepted_qty OR tx.status IS DISTINCT FROM 'posted'
       OR tx.source_document_type IS DISTINCT FROM 'stock_return_receipt_inbound' OR tx.source_document_id IS DISTINCT FROM header.id::text
       OR tx.movement_type IS DISTINCT FROM 'transfer' OR tx.reversed_transaction_id IS NOT NULL
       OR movement.external_boundary_code IS NOT NULL OR tx.posted_at>clock_timestamp() THEN
        RAISE EXCEPTION 'condition seal exact historical inbound source required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_history_graph_0159(root_id);
    PERFORM public.rsc_check_stock_return_inbound_0111(header.id);
    PERFORM public.rsc_condition_check_source(line.id);
    RETURN prepared||jsonb_build_object('root_disposition_id',root_id,'inbound_id',header.id,'inbound_line_id',line.id,
        'source_account_id',account.id,'original_transaction_id',tx.id,'original_movement_id',movement.id,
        'original_ledger_cursor',tx.ledger_cursor,'owner_org_id',account.owner_org_id,
        'requester_id',account.custodian_person_id,'source_created_at',greatest(header.created_at,tx.posted_at));
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_seal_canonical(jsonb),public.rsc_condition_seal_source(jsonb)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
