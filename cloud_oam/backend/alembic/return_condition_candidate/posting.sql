-- Candidate posting edges. Complete authority, audit, outbox, request hashes,
-- source history and opening proofs remain independent admission requirements.
CREATE FUNCTION public.rsc_condition_check_posting(case_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE c public.stock_condition_cases%ROWTYPE; source public.stock_accounts%ROWTYPE;
        frozen public.stock_accounts%ROWTYPE; target public.stock_accounts%ROWTYPE;
        original public.inventory_movements%ROWTYPE; original_tx public.inventory_transactions%ROWTYPE;
        inbound public.stock_operation_return_inbound_lines%ROWTYPE;
        e record; tx public.inventory_transactions%ROWTYPE; m public.inventory_movements%ROWTYPE;
        sn record; prior record; expected_ids uuid[]; actual_ids uuid[];
BEGIN
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=case_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'condition posting case missing' USING ERRCODE='23514'; END IF;
    SELECT * INTO source FROM public.stock_accounts WHERE id=c.source_account_id;
    SELECT * INTO frozen FROM public.stock_accounts WHERE id=c.frozen_account_id;
    SELECT * INTO inbound FROM public.stock_operation_return_inbound_lines WHERE id=c.inbound_line_id;
    SELECT * INTO original FROM public.inventory_movements WHERE id=c.original_movement_id;
    SELECT * INTO original_tx FROM public.inventory_transactions WHERE id=c.original_transaction_id;
    IF NOT COALESCE(source.id IS NOT NULL AND frozen.id IS NOT NULL
        AND source.availability_bucket='available' AND source.condition_code=c.recorded_condition
        AND frozen.availability_bucket='frozen' AND frozen.condition_code=source.condition_code
        AND source.custodian_person_id IS NOT NULL
        AND ROW(source.owner_org_id,source.custodian_person_id,source.location_id,source.material_id,source.lot_id)
            IS NOT DISTINCT FROM ROW(frozen.owner_org_id,frozen.custodian_person_id,frozen.location_id,frozen.material_id,frozen.lot_id)
        AND original.id IS NOT NULL AND original_tx.id IS NOT NULL AND inbound.id IS NOT NULL
        AND original.transaction_id=original_tx.id AND original_tx.status='posted'
        AND original_tx.movement_type='transfer' AND original_tx.ledger_cursor=c.original_ledger_cursor
        AND original_tx.source_document_type='stock_return_receipt_inbound'
        AND original_tx.source_document_id=c.inbound_id::text
        AND original.from_account_id=inbound.source_account_id AND original.to_account_id=source.id
        AND original.line_no=inbound.line_no AND original.quantity=inbound.accepted_qty
        AND source.material_id=inbound.material_id AND source.lot_id IS NOT DISTINCT FROM inbound.lot_id,
        false) OR original.external_boundary_code IS NOT NULL OR original_tx.reversed_transaction_id IS NOT NULL THEN
        RAISE EXCEPTION 'condition original movement or account dimensions mismatch' USING ERRCODE='23514';
    END IF;
    SELECT COALESCE(array_agg(serial_id ORDER BY serial_id),'{}'::uuid[]) INTO expected_ids
        FROM public.stock_condition_serials WHERE stock_condition_serials.case_id=c.id;
    IF EXISTS (SELECT 1 FROM public.stock_condition_serials s
        LEFT JOIN public.inventory_serials i ON i.id=s.serial_id
        WHERE s.case_id=c.id AND (i.id IS NULL OR i.material_id IS DISTINCT FROM source.material_id
            OR i.lot_id IS DISTINCT FROM source.lot_id)) THEN
        RAISE EXCEPTION 'condition serial material or lot mismatch' USING ERRCODE='23514';
    END IF;
    -- Event order cannot disagree with the inventory ledger, even across cases.
    IF EXISTS (SELECT 1 FROM (
        SELECT t.ledger_cursor, lag(t.ledger_cursor,1,c.original_ledger_cursor)
            OVER (ORDER BY ordered_event.event_sequence) AS previous_cursor
        FROM public.stock_condition_events ordered_event JOIN public.inventory_transactions t ON t.id=ordered_event.posting_transaction_id
        WHERE ordered_event.inbound_line_id=c.inbound_line_id
    ) ordered WHERE ledger_cursor<=previous_cursor) THEN
        RAISE EXCEPTION 'condition posting ledger order mismatch' USING ERRCODE='23514';
    END IF;
    FOR e IN SELECT * FROM public.stock_condition_events WHERE stock_condition_events.case_id=c.id
        AND kind IN ('submit','release','execute') ORDER BY event_sequence LOOP
        SELECT * INTO tx FROM public.inventory_transactions WHERE id=e.posting_transaction_id;
        SELECT * INTO m FROM public.inventory_movements WHERE id=e.posting_movement_id;
        SELECT * INTO target FROM public.stock_accounts WHERE id=e.to_account_id;
        IF NOT COALESCE(tx.id IS NOT NULL AND m.id IS NOT NULL AND target.id IS NOT NULL
            AND tx.status='posted' AND tx.movement_type=e.movement_type
            AND tx.source_document_type='stock_condition_event' AND tx.source_document_id=e.id::text
            AND tx.actor_user_id=e.actor_user_id AND tx.ledger_cursor>c.original_ledger_cursor
            AND tx.effective_at=e.created_at AND tx.created_at=tx.posted_at AND tx.posted_at>=e.created_at
            AND m.created_at=tx.posted_at AND m.transaction_id=tx.id AND m.line_no=1
            AND m.from_account_id=e.from_account_id AND m.to_account_id=e.to_account_id AND m.quantity=c.quantity
            AND e.actor_person_id=source.custodian_person_id
            AND ROW(source.owner_org_id,source.custodian_person_id,source.location_id,source.material_id,source.lot_id)
                IS NOT DISTINCT FROM ROW(target.owner_org_id,target.custodian_person_id,target.location_id,target.material_id,target.lot_id)
            AND (e.kind<>'execute' OR (target.condition_code='damaged' AND target.availability_bucket='available')),
            false) OR m.external_boundary_code IS NOT NULL OR tx.reversed_transaction_id IS NOT NULL
            OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>1
            OR (SELECT count(*) FROM public.inventory_transactions
                WHERE source_document_type='stock_condition_event' AND source_document_id=e.id::text)<>1 THEN
            RAISE EXCEPTION 'condition exact single posting edge required' USING ERRCODE='23514';
        END IF;
        SELECT COALESCE(array_agg(serial_id ORDER BY serial_id),'{}'::uuid[]) INTO actual_ids
            FROM public.inventory_movement_serials WHERE movement_id=m.id;
        IF actual_ids IS DISTINCT FROM expected_ids OR EXISTS (
            SELECT 1 FROM public.inventory_movement_serials WHERE movement_id=m.id
            AND (transaction_id<>tx.id OR created_at<>tx.posted_at)
        ) THEN
            RAISE EXCEPTION 'condition posting serial set mismatch' USING ERRCODE='23514';
        END IF;
        FOR sn IN SELECT unnest(expected_ids) AS id LOOP
            SELECT pm.id,pm.to_account_id,pt.status,pt.ledger_cursor INTO prior
                FROM public.inventory_movement_serials ps
                JOIN public.inventory_movements pm ON pm.id=ps.movement_id AND pm.transaction_id=ps.transaction_id
                JOIN public.inventory_transactions pt ON pt.id=pm.transaction_id
                WHERE ps.serial_id=sn.id AND pt.ledger_cursor<tx.ledger_cursor
                ORDER BY pt.ledger_cursor DESC,pm.line_no DESC LIMIT 1;
            IF NOT FOUND OR prior.status<>'posted' OR prior.to_account_id IS DISTINCT FROM m.from_account_id
                OR (e.kind IN ('execute','release') AND prior.id<>c.freeze_movement_id)
                OR (e.kind='submit' AND prior.id<>c.original_movement_id AND NOT EXISTS (
                    SELECT 1 FROM public.stock_condition_events r
                    JOIN public.stock_condition_serials rs ON rs.case_id=r.case_id AND rs.serial_id=sn.id
                    WHERE r.inbound_line_id=c.inbound_line_id AND r.kind='release'
                        AND r.posting_movement_id=prior.id AND r.event_sequence<e.event_sequence
                )) THEN
                RAISE EXCEPTION 'condition serial previous movement mismatch' USING ERRCODE='23514';
            END IF;
        END LOOP;
        IF e.kind='submit' AND cardinality(expected_ids)=0 AND EXISTS (
            SELECT 1 FROM public.inventory_movements pm JOIN public.inventory_transactions pt ON pt.id=pm.transaction_id
            WHERE pm.from_account_id=source.id AND pt.ledger_cursor>c.original_ledger_cursor AND pt.ledger_cursor<tx.ledger_cursor
              AND NOT EXISTS (SELECT 1 FROM public.stock_condition_events ce
                WHERE ce.inbound_line_id=c.inbound_line_id AND ce.kind='submit' AND ce.posting_movement_id=pm.id
                    AND ce.event_sequence<e.event_sequence)
        ) THEN
            RAISE EXCEPTION 'condition later outgoing requires reconciliation' USING ERRCODE='23514';
        END IF;
    END LOOP;
END $$;

-- Check both directions at COMMIT, including writes after an earlier successful
-- validation. Parent-only orphan transactions and late extra SN rows cannot
-- evade checks by omitting another insert into the business event table.
CREATE FUNCTION public.rsc_condition_posting_fence() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE old_row jsonb; new_row jsonb; tx_id uuid; event_id uuid; checked uuid; ids uuid[];
BEGIN
    IF TG_OP<>'INSERT' THEN old_row:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN new_row:=to_jsonb(NEW); END IF;
    IF TG_TABLE_NAME IN ('stock_condition_cases','stock_condition_events','stock_condition_serials') THEN
        ids:=CASE WHEN TG_TABLE_NAME='stock_condition_cases' THEN ARRAY[(new_row->>'id')::uuid]
            ELSE ARRAY[(new_row->>'case_id')::uuid] END;
    ELSIF TG_TABLE_NAME='inventory_serials' THEN
        SELECT array_agg(DISTINCT case_id) INTO ids FROM public.stock_condition_serials
            WHERE serial_id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid);
    ELSIF TG_TABLE_NAME='stock_accounts' THEN
        SELECT array_agg(DISTINCT c.id) INTO ids FROM public.stock_condition_cases c
        LEFT JOIN public.stock_condition_events e ON e.case_id=c.id
        WHERE c.source_account_id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid)
           OR c.frozen_account_id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid)
           OR e.to_account_id IN ((old_row->>'id')::uuid,(new_row->>'id')::uuid);
    ELSE
        ids:='{}'::uuid[];
        FOREACH tx_id IN ARRAY CASE WHEN TG_TABLE_NAME='inventory_transactions'
            THEN ARRAY[(old_row->>'id')::uuid,(new_row->>'id')::uuid]
            ELSE ARRAY[(old_row->>'transaction_id')::uuid,(new_row->>'transaction_id')::uuid] END LOOP
            IF tx_id IS NULL THEN CONTINUE; END IF;
            IF EXISTS (SELECT 1 FROM public.inventory_transactions WHERE id=tx_id AND
                (source_document_type='stock_condition_event' OR posting_key LIKE 'stock-condition:%'))
                AND NOT EXISTS (SELECT 1 FROM public.stock_condition_events WHERE posting_transaction_id=tx_id) THEN
                RAISE EXCEPTION 'condition transaction requires exact event' USING ERRCODE='23514';
            END IF;
            -- Source account movements matter even without a condition tag:
            -- an interposed SN move or fungible withdrawal changes history.
            SELECT ids || COALESCE(array_agg(DISTINCT c.id),'{}'::uuid[]) INTO ids
                FROM public.stock_condition_cases c
                LEFT JOIN public.stock_condition_events e ON e.case_id=c.id
                WHERE c.original_transaction_id=tx_id OR e.posting_transaction_id=tx_id
                   OR EXISTS (SELECT 1 FROM public.inventory_movements m WHERE m.transaction_id=tx_id
                       AND (m.from_account_id IN (c.source_account_id,c.frozen_account_id)
                            OR m.to_account_id IN (c.source_account_id,c.frozen_account_id)))
                   OR EXISTS (SELECT 1 FROM public.stock_condition_serials cs
                       WHERE cs.case_id=c.id AND (cs.serial_id IN ((old_row->>'serial_id')::uuid,(new_row->>'serial_id')::uuid)
                           OR EXISTS (SELECT 1 FROM public.inventory_movement_serials ms
                               WHERE ms.transaction_id=tx_id AND ms.serial_id=cs.serial_id)));
        END LOOP;
    END IF;
    FOR checked IN SELECT DISTINCT unnest(ids) LOOP
        PERFORM public.rsc_condition_check_posting(checked);
    END LOOP;
    RETURN NULL;
END $$;
