"""Reconstruct warehouse posting proof from accepted facts and original cursor."""
VALIDATE = """
DECLARE fact public.material_request_rejection_inbounds%ROWTYPE;
    receipt public.material_request_rejection_receipts%ROWTYPE; parent public.material_request_rejection_returns%ROWTYPE;
    source public.stock_accounts%ROWTYPE; target public.stock_accounts%ROWTYPE;
    tx public.inventory_transactions%ROWTYPE; r public.material_requests%ROWTYPE;
    part public.material_request_rejection_inbound_parts%ROWTYPE; piece record; move public.inventory_movements%ROWTYPE;
    plan jsonb; pieces jsonb:='[]'::jsonb; movements jsonb:='[]'::jsonb; sn jsonb; actual_sn jsonb;
    balance jsonb; dims jsonb; positions jsonb; people jsonb; input jsonb; command jsonb; expected jsonb;
    number integer:=0; n bigint; matches bigint; reference text; suffix bytea; notification_id uuid; manifest text;
BEGIN
    SELECT * INTO STRICT fact FROM public.material_request_rejection_inbounds WHERE id=checked_id;
    SELECT * INTO STRICT receipt FROM public.material_request_rejection_receipts WHERE id=fact.receipt_id;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=fact.return_id;
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=fact.request_id;
    SELECT * INTO STRICT source FROM public.stock_accounts WHERE id=fact.source_account_id;
    SELECT * INTO STRICT tx FROM public.inventory_transactions WHERE id=fact.posting_transaction_id;
    PERFORM public.rsc_validate_rejection_return_0172(parent.id);
    PERFORM public.rsc_validate_rejection_progress_0173(receipt.handover_id);
    PERFORM public.rsc_validate_rejection_receipt_0174(receipt.id);
    IF receipt.return_id<>parent.id OR parent.request_id<>r.id OR fact.request_version<receipt.request_version
       OR fact.request_version>r.version OR fact.source_account_id<>parent.in_transit_account_id
       OR fact.target_location_id<>receipt.target_location_id OR fact.accepted_qty<>receipt.accepted_qty
       OR fact.damaged_qty<>receipt.damaged_qty OR fact.recorded_at<receipt.recorded_at
       OR source.availability_bucket<>'in_transit'
       OR tx.status<>'posted' OR tx.movement_type<>'transfer' OR tx.reversed_transaction_id IS NOT NULL
       OR tx.source_document_type<>'material_request_rejection_inbound' OR tx.source_document_id<>fact.id::text
       OR tx.posting_key<>'rejection-inbound:'||receipt.id::text OR tx.actor_user_id<>fact.actor_user_id
       OR tx.transaction_no<>'INV-REJECT-IN-'||upper(left(replace(fact.id::text,'-',''),20))
       OR tx.effective_at<>fact.recorded_at OR tx.posted_at<fact.recorded_at
       OR tx.idempotency_key_hash<>encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')||convert_to(fact.idempotency_key_hash,'UTF8')),'hex')
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
       OR NOT EXISTS(SELECT 1 FROM public.custody_assignments c WHERE c.id=fact.custody_assignment_id
            AND c.location_id=fact.target_location_id AND c.custodian_person_id=fact.actor_person_id
            AND c.valid_from<=fact.recorded_at AND (c.valid_to IS NULL OR c.valid_to>fact.recorded_at)) THEN
        RAISE EXCEPTION '0175 exact inbound source, posting and custody required' USING ERRCODE='23514'; END IF;
    FOR piece IN SELECT * FROM (VALUES
        (receipt.evidence_jsonb->'origin'->>'source_condition',receipt.accepted_qty-receipt.damaged_qty,1),
        ('damaged',receipt.damaged_qty,2)) AS expected_part(condition_code,quantity,ordinal)
        WHERE quantity>0 ORDER BY ordinal LOOP
        number:=number+1;
        SELECT * INTO STRICT part FROM public.material_request_rejection_inbound_parts
            WHERE inbound_id=fact.id AND condition_code=piece.condition_code;
        SELECT * INTO STRICT target FROM public.stock_accounts WHERE id=part.target_account_id;
        dims:=public.rsc_rejection_inbound_dimensions_0175(source.id)||jsonb_build_object(
            'location_id',fact.target_location_id::text,'custodian_person_id',fact.actor_person_id::text,
            'availability_bucket','available','condition_code',piece.condition_code);
        IF part.quantity<>piece.quantity OR public.rsc_rejection_inbound_dimensions_0175(target.id) IS DISTINCT FROM dims THEN
            RAISE EXCEPTION '0175 exact accepted condition account required' USING ERRCODE='23514'; END IF;
        SELECT COALESCE(jsonb_agg(s.serial_id::text ORDER BY s.serial_id),'[]'::jsonb) INTO sn
            FROM public.material_request_rejection_receipt_serials s WHERE s.receipt_id=receipt.id AND s.result='accepted'
              AND (CASE WHEN s.damaged THEN 'damaged' ELSE receipt.evidence_jsonb->'origin'->>'source_condition' END)=piece.condition_code;
        SELECT COALESCE(jsonb_agg(s.serial_id::text ORDER BY s.serial_id),'[]'::jsonb) INTO actual_sn
            FROM public.material_request_rejection_inbound_serials s WHERE s.inbound_id=fact.id AND s.condition_code=piece.condition_code;
        IF sn IS DISTINCT FROM actual_sn THEN RAISE EXCEPTION '0175 exact accepted serial partitions required' USING ERRCODE='23514'; END IF;
        balance:=public.rsc_rejection_inbound_balance_0175(target.id,tx.ledger_cursor);
        IF fact.plan_jsonb->'parts'->(number-1)->'target_balance'='null'::jsonb THEN
            IF balance<>jsonb_build_object('quantity','0.000','version',0,'ledger_cursor',0)
               OR target.created_at<>fact.recorded_at THEN
                RAISE EXCEPTION '0175 absent target balance requires exact first account' USING ERRCODE='23514'; END IF;
            balance:='null'::jsonb;
        END IF;
        pieces:=pieces||jsonb_build_array(jsonb_build_object('condition_code',piece.condition_code,
            'quantity',piece.quantity::numeric(18,3)::text,'target_account_id',target.id::text,
            'target_dimensions',dims,'target_balance',balance,'serial_ids',sn));
        SELECT * INTO STRICT move FROM public.inventory_movements WHERE transaction_id=tx.id AND line_no=number;
        SELECT COALESCE(jsonb_agg(s.serial_id::text ORDER BY s.serial_id),'[]'::jsonb) INTO actual_sn
            FROM public.inventory_movement_serials s WHERE s.movement_id=move.id AND s.transaction_id=tx.id;
        IF move.from_account_id<>source.id OR move.to_account_id<>target.id OR move.quantity<>part.quantity
           OR move.external_boundary_code IS NOT NULL OR actual_sn IS DISTINCT FROM sn THEN
            RAISE EXCEPTION '0175 exact accepted inventory movement required' USING ERRCODE='23514'; END IF;
        movements:=movements||jsonb_build_array(jsonb_build_object('external_boundary_code',NULL,
            'from_account_id',source.id::text,'to_account_id',target.id::text,
            'quantity',trim_scale(part.quantity)::text,'serial_ids',sn));
    END LOOP;
    IF (SELECT count(*) FROM public.material_request_rejection_inbound_parts WHERE inbound_id=fact.id)<>number
       OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>number
       OR (SELECT count(*) FROM public.inventory_movement_serials WHERE transaction_id=tx.id)<>
          (SELECT count(*) FROM public.material_request_rejection_inbound_serials WHERE inbound_id=fact.id) THEN
        RAISE EXCEPTION '0175 no extra posting parts or serials allowed' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',s.serial_id::text,'last_movement_id',prior.id::text) ORDER BY s.serial_id),'[]'::jsonb),
        count(*) FILTER(WHERE prior.to_account_id IS DISTINCT FROM source.id) INTO positions,n
      FROM public.material_request_rejection_inbound_serials s
      LEFT JOIN LATERAL (SELECT m.id,m.to_account_id FROM public.inventory_movement_serials ms
          JOIN public.inventory_movements m ON m.id=ms.movement_id JOIN public.inventory_transactions t ON t.id=m.transaction_id
          WHERE ms.serial_id=s.serial_id AND t.status='posted' AND t.ledger_cursor<tx.ledger_cursor
          ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1) prior ON true WHERE s.inbound_id=fact.id;
    balance:=public.rsc_rejection_inbound_balance_0175(source.id,tx.ledger_cursor);
    IF n<>0 OR (balance->>'quantity')::numeric<fact.accepted_qty THEN
        RAISE EXCEPTION '0175 original source balance or serial predecessor mismatch' USING ERRCODE='23514'; END IF;
    SELECT jsonb_agg(p::text ORDER BY p),encode(sha256(convert_to(E'notification-person-targets.v1\n'||string_agg(p::text,',' ORDER BY p),'UTF8')),'hex')
        INTO people,manifest FROM (SELECT DISTINCT p FROM unnest(ARRAY[r.requester_person_id,source.custodian_person_id,fact.actor_person_id]) p WHERE p IS NOT NULL) targets;
    plan:=jsonb_build_object('schema','rsc.material_request_rejection_inbound.v1','receipt_id',receipt.id::text,
        'receipt_request_hash',receipt.request_hash,'receipt_evidence_sha256',receipt.evidence_sha256,
        'return_id',parent.id::text,'request_id',r.id::text,'request_version',fact.request_version,
        'actor_user_id',fact.actor_user_id,'actor_person_id',fact.actor_person_id::text,
        'actor_role_assignment_id',fact.actor_role_assignment_id::text,'authorization_version',fact.authorization_version,
        'target_location_id',fact.target_location_id::text,'custody_assignment_id',fact.custody_assignment_id::text,
        'source_account_id',source.id::text,'source_dimensions',public.rsc_rejection_inbound_dimensions_0175(source.id),
        'source_balance',balance,'serial_positions',positions,'parts',pieces,'notification_person_ids',people);
    input:=jsonb_build_object('expected_request_version',fact.request_version,'reason',fact.reason,
        'receipt_request_hash',receipt.request_hash,'expected_plan_hash',fact.plan_hash);
    IF fact.plan_jsonb IS DISTINCT FROM plan OR fact.plan_hash IS DISTINCT FROM
        encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(plan),'UTF8')),'hex')
       OR fact.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
        jsonb_build_object('return_id',parent.id::text,'receipt_id',receipt.id::text,'actor_user_id',fact.actor_user_id,
            'actor_person_id',fact.actor_person_id::text,'input',input)),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0175 canonical inbound plan or request mismatch' USING ERRCODE='23514'; END IF;
    command:=jsonb_build_object('effective_at',to_char(fact.recorded_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'movement_type','transfer','movements',movements,'posting_key',tx.posting_key,'source_document_id',fact.id::text,
        'source_document_type','material_request_rejection_inbound','transaction_no',tx.transaction_no);
    IF tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
        jsonb_build_object('operation','post','actor',jsonb_build_object('authorization_version',fact.authorization_version,
            'person_id',fact.actor_person_id::text,'user_id',fact.actor_user_id),'command',command)),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0175 exact unified posting command hash required' USING ERRCODE='23514'; END IF;
    reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(fact.trace_request_id,'UTF8')),'hex');
    suffix:=decode('00','hex')||convert_to(tx.id::text,'UTF8')||decode('00','hex')||convert_to('posted','UTF8');
    expected:=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_count',number,'movement_type','transfer',
        'posting_key',tx.posting_key,'reversed_transaction_id',NULL,'status','posted');
    SELECT count(*),count(*) FILTER(WHERE stream_key='inventory' AND actor_user_id=fact.actor_user_id
        AND action='inventory.transaction.posted' AND request_id=reference AND (before_jsonb IS NULL OR before_jsonb='null'::jsonb) AND after_jsonb=expected)
        INTO n,matches FROM public.audit_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inventory audit required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('transaction_id',tx.id::text,'transaction_no',tx.transaction_no,'movement_type','transfer',
        'ledger_cursor',tx.ledger_cursor,'reversed_transaction_id',NULL);
    SELECT count(*),count(*) FILTER(WHERE event_type='inventory.transaction.posted' AND payload_jsonb=expected
        AND idempotency_key='inventory-outbox-'||encode(sha256(convert_to('cloud_oam.inventory.outbox.v1','UTF8')||suffix),'hex'))
        INTO n,matches FROM public.outbox_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inventory outbox required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('ledger_cursor',tx.ledger_cursor,'movement_type','transfer','request_reference',reference);
    SELECT count(*),count(*) FILTER(WHERE from_status IS NULL AND to_status='posted' AND actor_id=fact.actor_user_id
        AND reason='inventory_transaction_posted' AND metadata_jsonb=expected
        AND idempotency_key='inventory-state-'||encode(sha256(convert_to('cloud_oam.inventory.state.v1','UTF8')||suffix),'hex'))
        INTO n,matches FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND aggregate_id=tx.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inventory state event required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('id',fact.id::text,'receipt_id',receipt.id::text,'return_id',parent.id::text,'request_id',r.id::text,
        'source_account_id',source.id::text,'target_location_id',fact.target_location_id::text,'custody_assignment_id',fact.custody_assignment_id::text,
        'actor_person_id',fact.actor_person_id::text,'actor_role_assignment_id',fact.actor_role_assignment_id::text,
        'posting_transaction_id',tx.id::text,'request_version',fact.request_version,'authorization_version',fact.authorization_version,
        'request_hash',fact.request_hash,'plan_hash',fact.plan_hash,'idempotency_key_hash',fact.idempotency_key_hash,
        'accepted_qty',fact.accepted_qty::text,'damaged_qty',fact.damaged_qty::text);
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.rejection_return.inbound'
        AND actor_user_id=fact.actor_user_id AND request_id=fact.trace_request_id
        AND occurred_at=fact.recorded_at AND created_at=fact.recorded_at
        AND before_jsonb=jsonb_build_object('warehouse_inbound','not_posted') AND after_jsonb=expected)
        INTO n,matches FROM public.audit_events WHERE aggregate_type='material_request_rejection_inbound' AND aggregate_id=fact.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inbound audit required' USING ERRCODE='23514'; END IF;
    SELECT count(*),count(*) FILTER(WHERE event_type='material_request.rejection_return.inbound' AND payload_jsonb=expected
        AND idempotency_key='rejection-inbound:'||fact.id::text) INTO n,matches
        FROM public.outbox_events WHERE aggregate_type='material_request_rejection_inbound' AND aggregate_id=fact.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inbound outbox required' USING ERRCODE='23514'; END IF;
    SELECT count(*),count(*) FILTER(WHERE event_type='material_request.rejection_return.inbound' AND payload_jsonb=expected
        AND dedup_key='rejection-inbound-notification:'||fact.id::text AND target_manifest_sha256=manifest)
        INTO n,matches FROM public.notification_events WHERE business_type='material_request_rejection_inbound' AND business_id=fact.id::text;
    IF n<>1 OR matches<>1 THEN RAISE EXCEPTION '0175 exact inbound notification required' USING ERRCODE='23514'; END IF;
    SELECT id INTO STRICT notification_id FROM public.notification_events WHERE business_type='material_request_rejection_inbound' AND business_id=fact.id::text;
    SELECT COALESCE(jsonb_agg(person_id::text ORDER BY person_id),'[]'::jsonb) INTO actual_sn
        FROM public.notification_person_targets nt WHERE nt.event_id=notification_id;
    IF actual_sn IS DISTINCT FROM people THEN RAISE EXCEPTION '0175 exact affected people manifest required' USING ERRCODE='23514'; END IF;
END;
"""
