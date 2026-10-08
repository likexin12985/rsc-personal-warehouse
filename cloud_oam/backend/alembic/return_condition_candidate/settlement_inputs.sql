-- Original settlement input and scan retention only. Historical stock, current
-- authority, posting, unique effects and request-key fences are independent.
CREATE FUNCTION public.rsc_condition_check_settlement_input(checked_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.stock_condition_events%ROWTYPE; c public.stock_condition_cases%ROWTYPE;
    p public.stock_condition_events%ROWTYPE; r public.stock_condition_settlement_requests%ROWTYPE;
    selected uuid[]; captured uuid[]; scans jsonb; files jsonb; expected jsonb;
BEGIN
    SELECT * INTO e FROM public.stock_condition_events WHERE id=checked_event;
    SELECT * INTO r FROM public.stock_condition_settlement_requests WHERE event_id=checked_event;
    IF e.id IS NULL THEN RAISE EXCEPTION 'settlement input event missing' USING ERRCODE='23514'; END IF;
    IF e.kind NOT IN ('execute','release') THEN
        IF r.event_id IS NOT NULL OR EXISTS(SELECT 1 FROM public.stock_condition_settlement_scans WHERE event_id=e.id) THEN
            RAISE EXCEPTION 'settlement input kind invalid' USING ERRCODE='23514'; END IF;
        RETURN;
    END IF;
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=e.case_id;
    SELECT * INTO p FROM public.stock_condition_events WHERE id=e.previous_event_id;
    IF r.event_id IS NULL OR c.id IS NULL OR p.id IS NULL
        OR ROW(r.case_id,r.kind,r.previous_event_id,r.previous_kind,r.previous_request_hash)
            IS DISTINCT FROM ROW(c.id,e.kind,p.id,p.kind,p.request_hash)
        OR ROW(r.actor_user_id,r.actor_person_id,r.authorization_version,r.created_at,r.request_id,r.idempotency_key_hash)
            IS DISTINCT FROM ROW(e.actor_user_id,e.actor_person_id,e.authorization_version,e.created_at,e.request_id,e.idempotency_key_hash)
        OR p.case_id IS DISTINCT FROM c.id OR e.decision_event_id IS DISTINCT FROM p.id
        OR e.decision_kind IS DISTINCT FROM p.kind THEN
        RAISE EXCEPTION 'settlement exact input binding required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(array_agg(serial_id ORDER BY serial_id),'{}'::uuid[]) INTO selected
        FROM public.stock_condition_serials WHERE case_id=c.id;
    SELECT COALESCE(array_agg(serial_id ORDER BY serial_id),'{}'::uuid[]) INTO captured
        FROM public.stock_condition_settlement_scans WHERE event_id=e.id;
    IF selected IS DISTINCT FROM captured OR EXISTS (
        SELECT 1 FROM public.stock_condition_settlement_scans s WHERE s.event_id=e.id AND
            (s.case_id IS DISTINCT FROM c.id OR s.created_at IS DISTINCT FROM e.created_at
                OR s.sku_code IS DISTINCT FROM r.material_sku_code)) THEN
        RAISE EXCEPTION 'settlement exact scan selection required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',serial_id::text,'sku_code',sku_code,
        'serial_no',serial_no,'qr_code',qr_code) ORDER BY serial_id::text),'[]'::jsonb) INTO scans
        FROM public.stock_condition_settlement_scans WHERE event_id=e.id;
    SELECT COALESCE(jsonb_agg(file_id::text ORDER BY file_id::text),'[]'::jsonb) INTO files
        FROM public.stock_condition_files WHERE event_id=e.id;
    expected:=jsonb_build_object('schema_version','condition_settlement_input/1','action',e.kind,
        'case_id',c.id::text,'expected_event_id',p.id::text,'expected_event_hash',p.request_hash,
        'reason',e.reason,'request_id',e.request_id,'idempotency_key_hash',e.idempotency_key_hash,
        'serial_verifications',scans,'evidence_file_ids',files);
    IF public.rsc_canonical_reconciliation_json_0026(r.input_jsonb)
            IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected)
        OR r.input_hash IS DISTINCT FROM encode(sha256(convert_to(
            public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'settlement exact original input required' USING ERRCODE='23514'; END IF;
END $$;

-- Snapshot physical identity under actual master row locks. Current labels
-- may change later; readback must use immutable captured identity thereafter.
CREATE FUNCTION public.rsc_condition_capture_settlement_input()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE current_sku text; current_serial text; current_qr text;
BEGIN
    IF TG_TABLE_NAME='stock_condition_settlement_requests' THEN
        SELECT m.sku_code INTO current_sku FROM public.stock_condition_events e
            JOIN public.stock_condition_cases c ON c.id=e.case_id
            JOIN public.stock_accounts a ON a.id=c.source_account_id
            JOIN public.materials m ON m.id=a.material_id
            WHERE e.id=NEW.event_id AND e.kind IN ('execute','release') FOR SHARE OF m;
        IF current_sku IS NULL OR NEW.material_sku_code IS DISTINCT FROM current_sku THEN
            RAISE EXCEPTION 'settlement input current SKU required' USING ERRCODE='23514'; END IF;
    ELSE
        SELECT m.sku_code,i.serial_no,i.qr_code INTO current_sku,current_serial,current_qr
            FROM public.stock_condition_events e
            JOIN public.stock_condition_cases c ON c.id=e.case_id
            JOIN public.stock_accounts a ON a.id=c.source_account_id
            JOIN public.materials m ON m.id=a.material_id
            JOIN public.stock_condition_serials chosen ON chosen.case_id=c.id AND chosen.serial_id=NEW.serial_id
            JOIN public.inventory_serials i ON i.id=chosen.serial_id AND i.material_id=a.material_id
                AND i.lot_id IS NOT DISTINCT FROM a.lot_id
            WHERE e.id=NEW.event_id AND c.id=NEW.case_id AND e.kind IN ('execute','release') FOR SHARE OF m,i;
        IF current_sku IS NULL OR ROW(NEW.sku_code,NEW.serial_no,NEW.qr_code)
                IS DISTINCT FROM ROW(current_sku,current_serial,current_qr) THEN
            RAISE EXCEPTION 'settlement current physical scan required' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_settlement_input_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE checked uuid; checked_case uuid;
BEGIN
    IF TG_TABLE_NAME='stock_condition_events' THEN
        PERFORM public.rsc_condition_check_settlement_input(NEW.id);
    ELSIF TG_TABLE_NAME IN ('stock_condition_settlement_requests','stock_condition_settlement_scans','stock_condition_files') THEN
        PERFORM public.rsc_condition_check_settlement_input(NEW.event_id);
    ELSE
        IF TG_TABLE_NAME='stock_condition_cases' THEN checked_case:=NEW.id; ELSE checked_case:=NEW.case_id; END IF;
        FOR checked IN SELECT id FROM public.stock_condition_events
            WHERE case_id=checked_case
                AND kind IN ('execute','release') LOOP
            PERFORM public.rsc_condition_check_settlement_input(checked);
        END LOOP;
    END IF;
    RETURN NULL;
END $$;

REVOKE ALL ON FUNCTION public.rsc_condition_check_settlement_input(uuid),
    public.rsc_condition_capture_settlement_input(),public.rsc_condition_settlement_input_fence()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
