-- Candidate historical plans, reconstructed at each transaction's original
-- cursor. Compose with full upstream/event/lifecycle guards before activation.
CREATE FUNCTION public.rsc_scrap_policy_basis_0165(checked_account uuid, at timestamptz, recorded jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE policy public.material_inventory_policies%ROWTYPE; material uuid; expected jsonb; start_text text; end_text text;
BEGIN
    SELECT material_id INTO material FROM public.stock_accounts WHERE id=checked_account;
    SELECT * INTO policy FROM public.material_inventory_policies WHERE material_id=material
        AND effective_from<=at AND (effective_to IS NULL OR effective_to>at);
    IF policy.id IS NULL OR (SELECT count(*) FROM public.material_inventory_policies WHERE material_id=material
        AND effective_from<=at AND (effective_to IS NULL OR effective_to>at))<>1 THEN
        RAISE EXCEPTION '0165 unique historical scrap policy required' USING ERRCODE='23514'; END IF;
    start_text:=to_char(policy.effective_from AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||
        CASE WHEN extract(microseconds FROM policy.effective_from)::bigint%1000000=0 THEN ''
        ELSE to_char(policy.effective_from AT TIME ZONE 'UTC','.US') END||'+00:00';
    end_text:=CASE WHEN policy.effective_to IS NULL THEN NULL ELSE
        to_char(policy.effective_to AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')||
        CASE WHEN extract(microseconds FROM policy.effective_to)::bigint%1000000=0 THEN ''
        ELSE to_char(policy.effective_to AT TIME ZONE 'UTC','.US') END||'+00:00' END;
    expected:=jsonb_build_array(jsonb_build_array(material::text,policy.id::text,policy.tracking_mode,
        policy.quantity_scale,policy.allow_fraction,start_text,recorded#>'{0,6}'));
    -- A later policy closure may add an end date. It cannot rewrite the six
    -- original fields or a non-null end date recorded in the original plan.
    IF public.rsc_canonical_reconciliation_json_0026(recorded) IS DISTINCT FROM
        public.rsc_canonical_reconciliation_json_0026(expected)
        OR (recorded#>'{0,6}' IS DISTINCT FROM 'null'::jsonb AND recorded#>>'{0,6}' IS DISTINCT FROM end_text) THEN
        RAISE EXCEPTION '0165 exact historical scrap policy fingerprint required' USING ERRCODE='23514'; END IF;
    RETURN expected;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_policy_basis_0165(uuid,timestamptz,jsonb)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_check_scrap_historical_plans_with_audit_0165(checked_scrap uuid, audit_ids uuid[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE line public.stock_scrap_lines%ROWTYPE; root public.stock_loss_dispositions%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE; header public.stock_operation_orders%ROWTYPE;
    account public.stock_accounts%ROWTYPE; tx public.inventory_transactions%ROWTYPE;
    inverse public.stock_loss_disposition_reversals%ROWTYPE; inverse_tx public.inventory_transactions%ROWTYPE;
    decision public.stock_loss_headquarters_decisions%ROWTYPE; review public.stock_loss_headquarters_reviews%ROWTYPE;
    correction public.stock_loss_correction_decisions%ROWTYPE; predecessor public.stock_loss_disposition_reversals%ROWTYPE;
    custody public.custody_assignments%ROWTYPE; file public.files%ROWTYPE; binding record;
    fact jsonb; holds jsonb; share jsonb; source jsonb; intent jsonb; command jsonb; expected jsonb;
    serials jsonb; ids jsonb; evidence jsonb:='[]'::jsonb; file_ids jsonb; fingerprint jsonb;
    person text; key text; request_hash text; plan_hash text; metadata_hash text; cutoff bigint; at timestamptz;
BEGIN
    IF audit_ids IS NULL THEN
        RAISE EXCEPTION '0165 internal audit proof required' USING ERRCODE='23514'; END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 historical scrap proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.rsc_scrap_recovery_source_0165(checked_scrap);
    SELECT * INTO line FROM public.stock_scrap_lines WHERE id=checked_scrap;
    SELECT * INTO root FROM public.stock_loss_dispositions WHERE id=line.root_disposition_id;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=root.operation_id;
    SELECT * INTO header FROM public.stock_operation_orders WHERE id=line.operation_id;
    SELECT * INTO account FROM public.stock_accounts WHERE id=line.frozen_account_id;
    SELECT * INTO tx FROM public.inventory_transactions WHERE id=line.posting_transaction_id;
    SELECT * INTO decision FROM public.stock_loss_headquarters_decisions WHERE id=root.headquarters_decision_id;
    SELECT * INTO review FROM public.stock_loss_headquarters_reviews WHERE id=decision.review_id;
    IF line.source_kind='original' THEN
        fact:=to_jsonb(root);person:=root.executor_person_id::text;
        source:=jsonb_build_object('kind','original','headquarters_decision_id',decision.id::text,
            'expected_headquarters_review_hash',review.request_hash,'expected_submission_plan_hash',parent.plan_hash);
        IF NOT COALESCE(decision.disposition='scrap' AND decision.line_id=root.line_id
            AND review.operation_id=parent.id AND review.created_at<=line.created_at,false) THEN
            RAISE EXCEPTION '0165 exact original scrap approval required' USING ERRCODE='23514'; END IF;
    ELSE
        SELECT to_jsonb(c) INTO fact FROM public.stock_loss_correction_executions c WHERE id=line.correction_execution_id;
        person:=fact->>'actor_person_id';
        SELECT * INTO correction FROM public.stock_loss_correction_decisions WHERE id=line.correction_decision_id;
        SELECT * INTO predecessor FROM public.stock_loss_disposition_reversals WHERE id=line.predecessor_reversal_id;
        source:=jsonb_build_object('kind','correction','root_disposition_id',root.id::text,
            'expected_root_request_hash',root.request_hash,'expected_submission_plan_hash',parent.plan_hash,
            'reversal_id',predecessor.id::text,'expected_reversal_hash',predecessor.request_hash,
            'correction_decision_id',correction.id::text,'expected_correction_decision_hash',correction.request_hash);
        IF NOT COALESCE(correction.disposition='scrap' AND correction.root_disposition_id=root.id
            AND predecessor.root_disposition_id=root.id AND correction.reversal_id=predecessor.id
            AND correction.expected_reversal_hash=predecessor.request_hash
            AND predecessor.created_at<=correction.created_at AND correction.created_at<=line.created_at
            AND fact->>'reason'=header.reason,false) THEN
            RAISE EXCEPTION '0165 exact corrected scrap approval required' USING ERRCODE='23514'; END IF;
    END IF;
    at:=line.created_at;cutoff:=tx.ledger_cursor-1;
    SELECT COALESCE(jsonb_agg(file_id::text ORDER BY file_id::text),'[]'::jsonb) INTO file_ids
        FROM public.stock_scrap_files WHERE scrap_line_id=line.id;
    intent:=jsonb_build_object('source',source,'execution_reason',header.reason,'evidence_file_ids',file_ids);
    command:=jsonb_build_object('schema_version',1,'action','scrap','intent',intent,
        'request_id',fact->>'request_id','expected_plan_hash',fact->>'plan_hash');
    SELECT * INTO custody FROM public.custody_assignments WHERE location_id=account.location_id
        AND valid_from<=at AND (valid_to IS NULL OR valid_to>at);
    IF NOT COALESCE(custody.id=line.custody_assignment_id AND custody.id::text=fact->>'custody_assignment_id'
        AND custody.custodian_person_id=account.custodian_person_id,false)
        OR (SELECT count(*) FROM public.custody_assignments WHERE location_id=account.location_id
            AND valid_from<=at AND (valid_to IS NULL OR valid_to>at))<>1 THEN
        RAISE EXCEPTION '0165 unique historical scrap custody required' USING ERRCODE='23514'; END IF;
    holds:=public.rsc_loss_hold_projection_0159(account.id,cutoff);
    SELECT value INTO share FROM jsonb_array_elements(holds->'lines') WHERE value->>'line_id'=root.line_id::text;
    IF share IS NULL OR share->>'active_execution_id' IS NOT NULL
        OR share->>'pending_reversal_id' IS DISTINCT FROM line.predecessor_reversal_id::text
        OR (share->>'frozen_quantity')::numeric IS DISTINCT FROM line.quantity
        OR (holds->>'balance_quantity')::numeric<line.quantity THEN
        RAISE EXCEPTION '0165 exact original-cursor scrap frozen share required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',s.serial_id::text,
        'previous_movement_id',s.previous_movement_id::text,'admission_movement_id',s.admission_movement_id::text,
        'previous_ledger_cursor',t.ledger_cursor,'lifecycle_before','active','lifecycle_after','scrapped') ORDER BY s.serial_id::text),'[]'::jsonb)
        INTO serials FROM public.stock_scrap_serials s JOIN public.inventory_movements m ON m.id=s.previous_movement_id
        JOIN public.inventory_transactions t ON t.id=m.transaction_id WHERE s.scrap_line_id=line.id;
    ids:=share->'frozen_serial_ids';
    FOR binding IN SELECT * FROM public.stock_scrap_files WHERE scrap_line_id=line.id ORDER BY file_id LOOP
        SELECT * INTO file FROM public.files WHERE id=binding.file_id;
        metadata_hash:=encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(file.metadata_jsonb),'UTF8')),'hex');
        IF NOT COALESCE(file.status='available' AND file.uploaded_by=fact->>'actor_user_id'
            AND file.metadata_jsonb->>'purpose'='stock_loss_evidence' AND file.metadata_jsonb->>'provider'='aliyun_oss_v2'
            AND file.metadata_jsonb->>'uploader_person_id'=person
            AND file.metadata_jsonb->'authorization_version'=fact->'authorization_version'
            AND file.created_at<=(file.metadata_jsonb#>>'{completion,verified_at}')::timestamptz
            AND (file.metadata_jsonb#>>'{completion,verified_at}')::timestamptz<=at
            AND binding.created_at=at AND binding.metadata_sha256=metadata_hash,false) THEN
            RAISE EXCEPTION '0165 historical scrap attachment metadata required' USING ERRCODE='23514'; END IF;
        evidence:=evidence||jsonb_build_array(jsonb_build_object('file_id',file.id::text,'original_filename',file.original_filename,
            'sha256',file.sha256,'size_bytes',file.size_bytes,'mime_type',file.mime_type,'metadata_sha256',metadata_hash));
    END LOOP;
    fingerprint:=public.rsc_scrap_policy_basis_0165(account.id,at,fact#>'{plan_jsonb,policy_fingerprint}');
    expected:=jsonb_build_object('schema_version','1.0','stage','scrap_stock_preparation_only','stock_effect','none',
        'intent',intent,'actor_user_id',fact->>'actor_user_id','actor_person_id',person,
        'authorization_version',fact->'authorization_version','operation_id',parent.id::text,'line_id',root.line_id::text,
        'decision_id',COALESCE(line.correction_decision_id,line.original_decision_id)::text,
        'predecessor_reversal_id',line.predecessor_reversal_id::text,'source_account_id',account.id::text,'target_account_id',NULL,
        'owner_org_id',account.owner_org_id::text,'custodian_person_id',account.custodian_person_id::text,
        'location_id',account.location_id::text,'material_id',account.material_id::text,'lot_id',account.lot_id::text,
        'source_condition',account.condition_code,'custody_assignment_id',custody.id::text,
        'quantity',to_char(line.quantity,'FM999999999999990.000'),'serial_ids',ids,'serials',serials,
        'movement_type','scrap','external_boundary_code','stock_operation_scrap','ledger_cursor',cutoff,
        'source_balance_version',holds->'balance_version','source_balance_quantity',holds->'balance_quantity',
        'frozen_holds_before',holds,'policy_fingerprint',fingerprint,'evidence',evidence);
    plan_hash:=encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex');
    IF fact->>'plan_hash' IS DISTINCT FROM plan_hash OR line.plan_hash IS DISTINCT FROM plan_hash
        OR public.rsc_canonical_reconciliation_json_0026(fact->'plan_jsonb') IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected)
        OR public.rsc_canonical_reconciliation_json_0026(line.plan_jsonb) IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected) THEN
        RAISE EXCEPTION '0165 complete historical scrap plan required' USING ERRCODE='23514'; END IF;
    request_hash:=encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex');
    IF public.rsc_canonical_reconciliation_json_0026(fact->'command_jsonb') IS DISTINCT FROM
        public.rsc_canonical_reconciliation_json_0026(command)
        OR fact->>'request_hash' IS DISTINCT FROM request_hash
        OR line.source_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(source),'UTF8')),'hex')
        OR NOT COALESCE(fact->>'request_id' ~ '^[A-Za-z0-9._:-]{8,160}$'
            AND fact->>'idempotency_key_hash' ~ '^[a-f0-9]{64}$' AND (fact->>'authorization_version')::bigint>0
            AND (fact->>'actor_user_id')::uuid::text=fact->>'actor_user_id'
            AND (fact->>'actor_user_id')::uuid<>'00000000-0000-0000-0000-000000000000'::uuid
            AND person::uuid<>'00000000-0000-0000-0000-000000000000'::uuid
            AND length(header.reason) BETWEEN 1 AND 500 AND header.reason=btrim(header.reason,E' \t\n\r')
            AND header.reason !~ '[\x01-\x08\x0B-\x1F]' AND jsonb_array_length(file_ids) BETWEEN 1 AND 20
            AND header.operation_no='SCRAP-'||upper(left(fact->>'idempotency_key_hash',24)),false) THEN
        RAISE EXCEPTION '0165 canonical source-bound scrap command required' USING ERRCODE='23514'; END IF;
    FOREACH key IN ARRAY ARRAY['actor_user_id','authorization_version','request_id','idempotency_key_hash',
        'request_hash','plan_hash','command_jsonb','plan_jsonb','posting_transaction_id'] LOOP
        IF public.rsc_canonical_reconciliation_json_0026(to_jsonb(header)->key) IS DISTINCT FROM
            public.rsc_canonical_reconciliation_json_0026(fact->key) THEN
            RAISE EXCEPTION '0165 scrap parent context must agree' USING ERRCODE='23514'; END IF;
    END LOOP;
    SELECT i.* INTO inverse FROM public.stock_loss_disposition_reversals i
        JOIN public.stock_scrap_recovery_executions r ON r.reversal_id=i.id WHERE r.scrap_line_id=line.id;
    IF inverse.id IS NULL THEN
        PERFORM public.rsc_check_scrap_inventory_edges_with_audit_0165(checked_scrap,audit_ids); RETURN; END IF;
    SELECT * INTO inverse_tx FROM public.inventory_transactions WHERE id=inverse.posting_transaction_id;
    cutoff:=inverse_tx.ledger_cursor-1;at:=inverse.created_at;
    holds:=public.rsc_loss_hold_projection_0159(account.id,cutoff);
    SELECT value INTO share FROM jsonb_array_elements(holds->'lines') WHERE value->>'line_id'=root.line_id::text;
    IF share IS NULL OR share->>'active_execution_id' IS DISTINCT FROM fact->>'id'
        OR share->>'pending_reversal_id' IS NOT NULL OR (share->>'frozen_quantity')::numeric IS DISTINCT FROM 0::numeric THEN
        RAISE EXCEPTION '0165 exact original-cursor recovery share required' USING ERRCODE='23514'; END IF;
    SELECT * INTO custody FROM public.custody_assignments WHERE location_id=account.location_id
        AND valid_from<=at AND (valid_to IS NULL OR valid_to>at);
    IF NOT COALESCE(custody.id=inverse.custody_assignment_id AND custody.custodian_person_id=account.custodian_person_id,false)
        OR (SELECT count(*) FROM public.custody_assignments WHERE location_id=account.location_id
            AND valid_from<=at AND (valid_to IS NULL OR valid_to>at))<>1 THEN
        RAISE EXCEPTION '0165 unique historical recovery custody required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg((value-'lifecycle_before'-'lifecycle_after')||jsonb_build_object(
        'original_movement_id',line.posting_movement_id::text,'lifecycle_before','scrapped','lifecycle_after','active')
        ORDER BY value->>'serial_id'),'[]'::jsonb) INTO serials FROM jsonb_array_elements(serials);
    fingerprint:=public.rsc_scrap_policy_basis_0165(account.id,at,inverse.plan_jsonb->'policy_fingerprint');
    intent:=inverse.command_jsonb-ARRAY['action','request_id','expected_plan_hash'];
    expected:=jsonb_build_object('schema_version','1.0','stage','scrap_recovery_stock_preparation_only','stock_effect','none',
        'intent',intent,'actor_user_id',inverse.actor_user_id,'actor_person_id',inverse.actor_person_id::text,
        'authorization_version',inverse.authorization_version,'root_disposition_id',root.id::text,
        'original_execution_id',fact->>'id','original_transaction_id',tx.id::text,
        'original_movement_id',line.posting_movement_id::text,'original_ledger_cursor',tx.ledger_cursor,
        'source_account_id',NULL,'target_account_id',account.id::text,'quantity',to_char(line.quantity,'FM999999999999990.000'),
        'custody_assignment_id',custody.id::text,'owner_org_id',account.owner_org_id::text,
        'custodian_person_id',account.custodian_person_id::text,'location_id',account.location_id::text,
        'material_id',account.material_id::text,'lot_id',account.lot_id::text,'target_condition',account.condition_code,
        'ledger_cursor',cutoff,'target_balance_quantity',holds->'balance_quantity','target_balance_version',holds->'balance_version',
        'frozen_holds_before',holds,'restored_line_id',root.line_id::text,'restored_frozen_quantity',to_char(line.quantity,'FM999999999999990.000'),
        'serials',serials,'serial_ids',ids,'policy_fingerprint',fingerprint);
    plan_hash:=encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex');
    IF inverse.plan_hash IS DISTINCT FROM plan_hash OR public.rsc_canonical_reconciliation_json_0026(inverse.plan_jsonb)
        IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected) THEN
        RAISE EXCEPTION '0165 complete historical recovery plan required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_scrap_inventory_edges_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_historical_plans_with_audit_0165(uuid,uuid[])
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

-- Only this private entry point creates the proof. Its children are read-only
-- validators: no cache, caller-supplied bypass, or reuse across SQL writes.
-- Lock inventory before audit to retain the posting serialization order.
CREATE FUNCTION public.rsc_check_scrap_historical_plans_0165(checked_scrap uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE audit_ids uuid[];
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 audit proof requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    audit_ids:=public.rsc_loss_inventory_audit_members_0159();
    PERFORM public.rsc_check_scrap_historical_plans_with_audit_0165(checked_scrap,audit_ids);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_check_scrap_historical_plans_0165(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

CREATE FUNCTION public.rsc_fence_scrap_historical_plans_0165()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE before_row jsonb; after_row jsonb; identifiers uuid[]; accounts uuid[]; transactions uuid[]; checked uuid;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0165 historical plans require read committed' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0165 inventory head required' USING ERRCODE='23514'; END IF;
    IF TG_OP<>'INSERT' THEN before_row:=to_jsonb(OLD); END IF;
    IF TG_OP<>'DELETE' THEN after_row:=to_jsonb(NEW); END IF;
    identifiers:=ARRAY[(before_row->>'id')::uuid,(after_row->>'id')::uuid];
    transactions:=ARRAY[(before_row->>'transaction_id')::uuid,(after_row->>'transaction_id')::uuid,
        (before_row->>'posting_transaction_id')::uuid,(after_row->>'posting_transaction_id')::uuid]||
        CASE WHEN TG_TABLE_NAME='inventory_transactions' THEN identifiers ELSE ARRAY[]::uuid[] END;
    accounts:=ARRAY[(before_row->>'from_account_id')::uuid,(after_row->>'from_account_id')::uuid,
        (before_row->>'to_account_id')::uuid,(after_row->>'to_account_id')::uuid,
        (before_row->>'frozen_account_id')::uuid,(after_row->>'frozen_account_id')::uuid,
        (before_row->>'reserved_account_id')::uuid,(after_row->>'reserved_account_id')::uuid,
        (before_row->>'source_account_id')::uuid,(after_row->>'source_account_id')::uuid,
        (before_row->>'target_account_id')::uuid,(after_row->>'target_account_id')::uuid]||
        CASE WHEN TG_TABLE_NAME='stock_accounts' THEN identifiers ELSE ARRAY[]::uuid[] END;
    SELECT accounts||COALESCE(array_agg(DISTINCT a),ARRAY[]::uuid[]) INTO accounts
        FROM public.inventory_movements m CROSS JOIN LATERAL unnest(ARRAY[m.from_account_id,m.to_account_id]) a
        WHERE m.transaction_id=ANY(transactions) OR m.id IN ((before_row->>'movement_id')::uuid,(after_row->>'movement_id')::uuid)
            OR m.id=ANY(identifiers);
    FOR checked IN SELECT DISTINCT s.id FROM public.stock_scrap_lines s JOIN public.stock_accounts a ON a.id=s.frozen_account_id
        WHERE s.id=ANY(identifiers) OR s.operation_id=ANY(identifiers) OR s.loss_line_id=ANY(identifiers)
            OR s.root_disposition_id=ANY(identifiers) OR s.correction_execution_id=ANY(identifiers)
            OR s.correction_decision_id=ANY(identifiers) OR s.original_decision_id=ANY(identifiers)
            OR s.predecessor_reversal_id=ANY(identifiers) OR s.frozen_account_id=ANY(accounts)
            OR s.root_disposition_id IN ((before_row->>'root_disposition_id')::uuid,(after_row->>'root_disposition_id')::uuid)
            OR s.id IN ((before_row->>'scrap_line_id')::uuid,(after_row->>'scrap_line_id')::uuid)
            OR (TG_TABLE_NAME='stock_operation_orders' AND EXISTS(SELECT 1 FROM public.stock_operation_lines l
                WHERE l.operation_id=ANY(identifiers) AND l.reserved_account_id=s.frozen_account_id))
            OR (TG_TABLE_NAME='stock_operation_serials' AND EXISTS(SELECT 1 FROM public.stock_operation_lines l
                WHERE l.id IN ((before_row->>'line_id')::uuid,(after_row->>'line_id')::uuid) AND l.reserved_account_id=s.frozen_account_id))
            OR (TG_TABLE_NAME='stock_loss_headquarters_reviews' AND EXISTS(SELECT 1 FROM public.stock_loss_headquarters_decisions d
                WHERE d.id=s.original_decision_id AND d.review_id=ANY(identifiers)))
            OR (TG_TABLE_NAME='material_inventory_policies' AND a.material_id IN (
                (before_row->>'material_id')::uuid,(after_row->>'material_id')::uuid))
            OR (TG_TABLE_NAME='custody_assignments' AND a.location_id IN (
                (before_row->>'location_id')::uuid,(after_row->>'location_id')::uuid))
            OR (TG_TABLE_NAME='files' AND EXISTS(SELECT 1 FROM public.stock_scrap_files f WHERE f.scrap_line_id=s.id AND f.file_id=ANY(identifiers)))
        ORDER BY s.id LOOP
        PERFORM public.rsc_check_scrap_historical_plans_0165(checked);
    END LOOP;
    RETURN COALESCE(NEW,OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_fence_scrap_historical_plans_0165()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;

DO $install$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['stock_scrap_lines','stock_scrap_serials','stock_scrap_files','stock_scrap_recovery_executions',
        'stock_loss_dispositions','stock_loss_correction_executions','stock_loss_disposition_reversals','stock_loss_correction_decisions',
        'stock_loss_headquarters_decisions','stock_loss_headquarters_reviews','stock_operation_orders','stock_operation_lines',
        'stock_operation_serials','inventory_transactions','inventory_movements','inventory_movement_serials',
        'stock_accounts','material_inventory_policies','custody_assignments','files'] LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER trg_scrap_historical_plans_0165 AFTER INSERT OR UPDATE OR DELETE ON public.%I '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_fence_scrap_historical_plans_0165()',name);
        EXECUTE format('ALTER TABLE public.%I ENABLE ALWAYS TRIGGER trg_scrap_historical_plans_0165',name);
    END LOOP;
END;
$install$;
