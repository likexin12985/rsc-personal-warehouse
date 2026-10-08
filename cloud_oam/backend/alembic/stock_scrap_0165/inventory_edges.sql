-- Exact historical ledger edges. This composes with (does not replace) the
-- complete loss-hold, opening, plan, event, balance and serial-history proofs.
-- No runtime grants or migration activation are supplied here.
CREATE FUNCTION public.rsc_assert_scrap_tracking_0165(checked_account uuid, amount numeric, serial_count integer, at timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE account public.stock_accounts%ROWTYPE; policy public.material_inventory_policies%ROWTYPE;
BEGIN
    SELECT * INTO account FROM public.stock_accounts WHERE id=checked_account;
    SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=account.material_id
        AND effective_from<=at AND (effective_to IS NULL OR effective_to>at);
    IF NOT COALESCE(account.id IS NOT NULL AND policy.id IS NOT NULL AND amount>0
        AND amount<1000000000000000 AND serial_count>=0
        AND policy.tracking_mode IN ('none','lot','serial','lot_and_serial')
        AND scale(trim_scale(amount))<=policy.quantity_scale
        AND (policy.allow_fraction OR amount=trunc(amount)),false)
        OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=account.material_id
            AND effective_from<=at AND (effective_to IS NULL OR effective_to>at))<>1
        OR (policy.tracking_mode IN ('serial','lot_and_serial') AND amount<>serial_count)
        OR (policy.tracking_mode IN ('none','lot') AND serial_count<>0)
        OR (policy.tracking_mode IN ('lot','lot_and_serial') AND account.lot_id IS NULL)
        OR (policy.tracking_mode IN ('none','serial') AND account.lot_id IS NOT NULL) THEN
        RAISE EXCEPTION '0165 historical scrap quantity and tracking policy required' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_assert_scrap_tracking_0165(uuid,numeric,integer,timestamptz)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_check_scrap_inventory_edges_with_audit_0165(checked_scrap uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; fact jsonb; source record;
    tx public.inventory_transactions%ROWTYPE; move public.inventory_movements%ROWTYPE;
    inverse public.stock_loss_disposition_reversals%ROWTYPE;
    recovery public.stock_scrap_recovery_executions%ROWTYPE;
    inverse_tx public.inventory_transactions%ROWTYPE; inverse_move public.inventory_movements%ROWTYPE;
    account public.stock_accounts%ROWTYPE; sn record; prior record; admission record;
    ids jsonb; actual jsonb; expected jsonb; actor jsonb; command jsonb;
    kind text; key text; inverse_json jsonb; recovery_json jsonb;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    SELECT * INTO source FROM public.rsc_scrap_recovery_source_0165(checked_scrap);
    SELECT * INTO line FROM public.stock_scrap_lines WHERE id=checked_scrap;
    SELECT * INTO account FROM public.stock_accounts WHERE id=line.frozen_account_id;
    IF line.source_kind='original' THEN
        SELECT to_jsonb(f) INTO fact FROM public.stock_loss_dispositions f WHERE id=line.root_disposition_id;
        kind:='stock_loss_disposition';
    ELSE
        SELECT to_jsonb(f) INTO fact FROM public.stock_loss_correction_executions f WHERE id=line.correction_execution_id;
        kind:='stock_loss_correction_execution';
    END IF;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=line.posting_transaction_id;
    SELECT * INTO move FROM public.inventory_movements WHERE id=line.posting_movement_id;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb)
        INTO ids FROM public.stock_scrap_serials WHERE scrap_line_id=line.id;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb)
        INTO actual FROM public.stock_operation_serials WHERE line_id=line.loss_line_id;
    IF actual IS DISTINCT FROM ids THEN
        RAISE EXCEPTION '0165 scrap serial set must equal the exact loss line' USING ERRCODE='23514'; END IF;
    IF NOT COALESCE(tx.id IS NOT NULL AND move.id IS NOT NULL AND tx.status='posted' AND tx.movement_type='scrap'
        AND tx.source_document_type=kind AND tx.source_document_id=fact->>'id'
        AND tx.actor_user_id=fact->>'actor_user_id' AND tx.idempotency_key_hash=fact->>'idempotency_key_hash'
        AND tx.transaction_no='INV-SCRAP-'||upper(left(fact->>'idempotency_key_hash',24))
        AND tx.posting_key='stock-scrap:'||(fact->>'id')||':'||(fact->>'idempotency_key_hash')
        AND tx.effective_at=line.created_at AND tx.created_at=tx.posted_at AND tx.posted_at>=line.created_at
        AND move.transaction_id=tx.id AND move.line_no=1 AND move.from_account_id=account.id
        AND move.quantity=line.quantity AND move.external_boundary_code='stock_operation_scrap'
        AND move.created_at=tx.posted_at,false) OR tx.reversed_transaction_id IS NOT NULL OR move.to_account_id IS NOT NULL
        OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=tx.id)<>1
        OR (SELECT count(*) FROM public.inventory_transactions WHERE source_document_type=kind AND source_document_id=fact->>'id')<>1 THEN
        RAISE EXCEPTION '0165 exact single scrap posting edge required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb) INTO actual
        FROM public.inventory_movement_serials WHERE movement_id=move.id;
    IF actual IS DISTINCT FROM ids OR EXISTS(SELECT 1 FROM public.inventory_movement_serials
        WHERE movement_id=move.id AND (transaction_id<>tx.id OR created_at<>tx.posted_at))
        OR EXISTS(SELECT 1 FROM public.stock_scrap_serials WHERE scrap_line_id=line.id AND created_at<>line.created_at) THEN
        RAISE EXCEPTION '0165 scrap movement serial bindings required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_scrap_tracking_0165(account.id,line.quantity,jsonb_array_length(ids),tx.effective_at);
    actor:=jsonb_build_object('user_id',fact->>'actor_user_id','person_id',
        CASE line.source_kind WHEN 'original' THEN fact->>'executor_person_id' ELSE fact->>'actor_person_id' END,
        'authorization_version',(fact->>'authorization_version')::bigint);
    command:=jsonb_build_object('effective_at',to_char(tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'movement_type','scrap','movements',jsonb_build_array(jsonb_build_object(
            'external_boundary_code','stock_operation_scrap','from_account_id',account.id::text,
            'to_account_id',NULL,'quantity',trim_scale(line.quantity)::text,'serial_ids',ids)),
        'posting_key',tx.posting_key,'source_document_id',fact->>'id','source_document_type',kind,
        'transaction_no',tx.transaction_no);
    expected:=jsonb_build_object('operation','post','actor',actor,'command',command);
    IF tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(
        public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0165 exact canonical scrap posting request required' USING ERRCODE='23514'; END IF;
    FOR sn IN SELECT s.*,i.material_id,i.lot_id FROM public.stock_scrap_serials s
        JOIN public.inventory_serials i ON i.id=s.serial_id WHERE s.scrap_line_id=line.id ORDER BY s.serial_id LOOP
        SELECT m.*,t.ledger_cursor,t.status,t.movement_type INTO prior
            FROM public.inventory_movement_serials s JOIN public.inventory_movements m ON m.id=s.movement_id
            JOIN public.inventory_transactions t ON t.id=m.transaction_id
            WHERE s.serial_id=sn.serial_id AND t.ledger_cursor<tx.ledger_cursor
            ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1;
        SELECT m.*,t.ledger_cursor,t.status INTO admission
            FROM public.inventory_movement_serials s JOIN public.inventory_movements m ON m.id=s.movement_id
            JOIN public.inventory_transactions t ON t.id=m.transaction_id
            WHERE s.serial_id=sn.serial_id AND t.ledger_cursor<tx.ledger_cursor
            ORDER BY t.ledger_cursor,m.line_no LIMIT 1;
        IF NOT COALESCE(prior.id IS NOT NULL AND prior.id=sn.previous_movement_id AND prior.to_account_id=account.id
            AND prior.status='posted' AND prior.movement_type<>'scrap' AND admission.id IS NOT NULL
            AND admission.id=sn.admission_movement_id AND admission.status='posted'
            AND admission.ledger_cursor<=prior.ledger_cursor AND sn.material_id=account.material_id,false)
            OR admission.from_account_id IS NOT NULL OR admission.to_account_id IS NULL
            OR sn.lot_id IS DISTINCT FROM account.lot_id THEN
            RAISE EXCEPTION '0165 exact previous and first-admission serial edges required' USING ERRCODE='23514'; END IF;
    END LOOP;
    SELECT * INTO recovery FROM public.stock_scrap_recovery_executions WHERE scrap_line_id=line.id;
    IF recovery.id IS NULL THEN
        IF EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=tx.id)
            OR EXISTS(SELECT 1 FROM public.stock_loss_disposition_reversals
                WHERE original_transaction_id=tx.id OR scrap_line_id=line.id) THEN
            RAISE EXCEPTION '0165 scrap inverse requires independent recovery execution' USING ERRCODE='23514'; END IF;
        RETURN;
    END IF;
    PERFORM public.rsc_check_scrap_recovery_approvals_with_audit_0165(line.id,audit_ids);
    SELECT * INTO inverse FROM public.stock_loss_disposition_reversals WHERE id=recovery.reversal_id;
    SELECT * INTO inverse_tx FROM public.inventory_transactions WHERE id=inverse.posting_transaction_id;
    SELECT * INTO inverse_move FROM public.inventory_movements WHERE id=inverse.posting_movement_id;
    IF NOT COALESCE(inverse.id IS NOT NULL AND inverse.scrap_recovery_execution_id=recovery.id
        AND inverse.scrap_line_id=line.id AND inverse.scrap_source_kind=line.source_kind
        AND inverse.root_disposition_id=line.root_disposition_id AND inverse.original_transaction_id=tx.id
        AND inverse.original_movement_id=move.id AND inverse.target_account_id=account.id AND inverse.quantity=line.quantity
        AND inverse_tx.id IS NOT NULL AND inverse_move.id IS NOT NULL AND inverse_tx.status='posted'
        AND inverse_tx.movement_type='reversal' AND inverse_tx.reversed_transaction_id=tx.id
        AND inverse_tx.ledger_cursor>tx.ledger_cursor AND inverse.created_at>line.created_at
        AND inverse_tx.source_document_type='stock_loss_disposition_reversal' AND inverse_tx.source_document_id=inverse.id::text
        AND inverse_tx.actor_user_id=inverse.actor_user_id AND inverse_tx.idempotency_key_hash=inverse.idempotency_key_hash
        AND inverse_tx.transaction_no='INV-LOSS-REV-'||upper(left(inverse.idempotency_key_hash,20))
        AND inverse_tx.posting_key='stock-loss:reverse_loss:'||inverse.id::text||':'||inverse.idempotency_key_hash
        AND inverse_tx.effective_at=inverse.created_at AND inverse_tx.created_at=inverse_tx.posted_at
        AND inverse_tx.posted_at>=inverse.created_at AND inverse_move.created_at=inverse_tx.posted_at
        AND inverse_move.transaction_id=inverse_tx.id AND inverse_move.line_no=move.line_no
        AND inverse_move.to_account_id=account.id AND inverse_move.quantity=move.quantity
        AND inverse_move.external_boundary_code=move.external_boundary_code,false)
        OR inverse.reversed_correction_id IS DISTINCT FROM line.correction_execution_id
        OR inverse.source_account_id IS NOT NULL OR inverse_move.from_account_id IS NOT NULL
        OR (SELECT count(*) FROM public.inventory_movements WHERE transaction_id=inverse_tx.id)<>1
        OR (SELECT count(*) FROM public.inventory_transactions WHERE source_document_type='stock_loss_disposition_reversal'
            AND source_document_id=inverse.id::text)<>1 THEN
        RAISE EXCEPTION '0165 exact single approved scrap inverse edge required' USING ERRCODE='23514'; END IF;
    inverse_json:=to_jsonb(inverse); recovery_json:=to_jsonb(recovery);
    FOREACH key IN ARRAY ARRAY['created_at','actor_user_id','actor_person_id','authorization_version','request_id',
        'request_hash','idempotency_key_hash','reason','command_jsonb','plan_hash','plan_jsonb'] LOOP
        IF (inverse_json->key)::text IS DISTINCT FROM (recovery_json->key)::text THEN
            RAISE EXCEPTION '0165 recovery and inverse command context must agree' USING ERRCODE='23514'; END IF;
    END LOOP;
    actor:=jsonb_build_object('user_id',inverse.actor_user_id,'person_id',inverse.actor_person_id::text,
        'authorization_version',inverse.authorization_version);
    command:=jsonb_build_object('effective_at',to_char(inverse_tx.effective_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'original_transaction_id',tx.id::text,'posting_key',inverse_tx.posting_key,'source_document_id',inverse.id::text,
        'source_document_type','stock_loss_disposition_reversal','transaction_no',inverse_tx.transaction_no);
    expected:=jsonb_build_object('operation','reverse','actor',actor,'command',command);
    IF inverse_tx.request_hash IS DISTINCT FROM encode(sha256(convert_to(
        public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0165 exact canonical scrap inverse request required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(serial_id::text ORDER BY serial_id::text),'[]'::jsonb) INTO actual
        FROM public.inventory_movement_serials WHERE movement_id=inverse_move.id;
    IF actual IS DISTINCT FROM ids OR EXISTS(SELECT 1 FROM public.inventory_movement_serials
        WHERE movement_id=inverse_move.id AND (transaction_id<>inverse_tx.id OR created_at<>inverse_tx.posted_at)) THEN
        RAISE EXCEPTION '0165 inverse must restore exactly the scrapped serial set' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_scrap_tracking_0165(account.id,inverse.quantity,jsonb_array_length(ids),inverse_tx.effective_at);
    FOR sn IN SELECT serial_id FROM public.stock_scrap_serials WHERE scrap_line_id=line.id LOOP
        SELECT m.id INTO prior FROM public.inventory_movement_serials s
            JOIN public.inventory_movements m ON m.id=s.movement_id JOIN public.inventory_transactions t ON t.id=m.transaction_id
            WHERE s.serial_id=sn.serial_id AND t.ledger_cursor<inverse_tx.ledger_cursor
            ORDER BY t.ledger_cursor DESC,m.line_no DESC LIMIT 1;
        IF prior.id IS DISTINCT FROM move.id THEN
            RAISE EXCEPTION '0165 recovery must reverse the last exact serial scrap' USING ERRCODE='23514'; END IF;
    END LOOP;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_inventory_edges_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_inventory_edges_0165(checked_scrap uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_inventory_edges_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_inventory_edges_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_fence_scrap_inventory_edges_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE old_row jsonb; new_row jsonb; identifiers uuid[]; transactions uuid[]; checked uuid; tx_id uuid;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 scrap inventory proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory serialization head required' USING ERRCODE='23514'; END IF;
    IF TG_OP<>'INSERT' THEN old_row:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN new_row:=to_jsonb(NEW); END IF;
    identifiers:=ARRAY[(old_row->>'id')::uuid,(new_row->>'id')::uuid];
    transactions:=CASE WHEN TG_TABLE_NAME='inventory_transactions' THEN identifiers ELSE
        ARRAY[(old_row->>'transaction_id')::uuid,(new_row->>'transaction_id')::uuid,
            (old_row->>'posting_transaction_id')::uuid,(new_row->>'posting_transaction_id')::uuid] END;
    -- Orphan transactions using the scrap boundary must never hide behind a
    -- missing business child. Reverse checks cover both OLD and NEW bindings.
    FOREACH tx_id IN ARRAY transactions LOOP
        IF tx_id IS NOT NULL AND (EXISTS(SELECT 1 FROM public.inventory_transactions WHERE id=tx_id AND movement_type='scrap')
            OR EXISTS(SELECT 1 FROM public.inventory_movements WHERE transaction_id=tx_id AND external_boundary_code='stock_operation_scrap'))
            AND NOT EXISTS(SELECT 1 FROM public.stock_scrap_lines WHERE posting_transaction_id=tx_id)
            AND NOT EXISTS(SELECT 1 FROM public.stock_scrap_recovery_executions r
                JOIN public.stock_loss_disposition_reversals i ON i.id=r.reversal_id WHERE i.posting_transaction_id=tx_id) THEN
            RAISE EXCEPTION '0165 scrap boundary requires exact business fact' USING ERRCODE='23514'; END IF;
    END LOOP;
    FOR checked IN SELECT DISTINCT l.id FROM public.stock_scrap_lines l
        LEFT JOIN public.stock_scrap_recovery_executions r ON r.scrap_line_id=l.id
        LEFT JOIN public.stock_loss_disposition_reversals i ON i.id=r.reversal_id
        WHERE l.id=ANY(identifiers) OR l.root_disposition_id=ANY(identifiers) OR l.correction_execution_id=ANY(identifiers)
            OR l.frozen_account_id=ANY(identifiers) OR r.id=ANY(identifiers) OR i.id=ANY(identifiers)
            OR l.posting_transaction_id=ANY(transactions) OR i.posting_transaction_id=ANY(transactions)
            OR l.id IN ((old_row->>'scrap_line_id')::uuid,(new_row->>'scrap_line_id')::uuid)
            OR (TG_TABLE_NAME='stock_operation_serials' AND l.loss_line_id IN (
                (old_row->>'line_id')::uuid,(new_row->>'line_id')::uuid))
            OR (TG_TABLE_NAME='material_inventory_policies' AND EXISTS(SELECT 1 FROM public.stock_accounts a
                WHERE a.id=l.frozen_account_id AND a.material_id IN ((old_row->>'material_id')::uuid,(new_row->>'material_id')::uuid)))
            OR EXISTS(SELECT 1 FROM public.stock_scrap_serials s WHERE s.scrap_line_id=l.id AND (
                s.previous_movement_id=ANY(identifiers) OR s.admission_movement_id=ANY(identifiers)
                OR (TG_TABLE_NAME='inventory_serials' AND s.serial_id=ANY(identifiers))
                OR s.serial_id IN ((old_row->>'serial_id')::uuid,(new_row->>'serial_id')::uuid)
                OR EXISTS(SELECT 1 FROM public.inventory_movement_serials link
                    WHERE link.serial_id=s.serial_id AND link.transaction_id=ANY(transactions))))
        ORDER BY l.id LOOP
        PERFORM public.rsc_check_scrap_inventory_edges_0165(checked);
    END LOOP;
    RETURN COALESCE(NEW,OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_fence_scrap_inventory_edges_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_lines','stock_scrap_serials','stock_scrap_recovery_executions',
        'stock_loss_dispositions','stock_loss_correction_executions','stock_loss_disposition_reversals',
        'inventory_transactions','inventory_movements','inventory_movement_serials','stock_accounts',
        'stock_operation_serials','material_inventory_policies','inventory_serials'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_scrap_inventory_edges_0165 AFTER INSERT OR UPDATE OR DELETE ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_inventory_edges_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_scrap_inventory_edges_0165',name);
    END LOOP;
END;
$install$;
