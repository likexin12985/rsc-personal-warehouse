-- Exact original submit input; does not grant replay or prove a full request lookup.
CREATE FUNCTION public.rsc_condition_check_request_input(checked_event uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE e public.stock_condition_events%ROWTYPE; c public.stock_condition_cases%ROWTYPE;
    r public.stock_condition_submission_requests%ROWTYPE; scans jsonb; files jsonb; expected jsonb;
BEGIN
    SELECT * INTO e FROM public.stock_condition_events WHERE id=checked_event;
    SELECT * INTO r FROM public.stock_condition_submission_requests WHERE event_id=checked_event;
    IF e.id IS NULL THEN RAISE EXCEPTION 'condition original input event missing' USING ERRCODE='23514'; END IF;
    IF e.kind<>'submit' THEN
        IF r.event_id IS NOT NULL THEN RAISE EXCEPTION 'condition original input kind invalid' USING ERRCODE='23514'; END IF;
        RETURN;
    END IF;
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=e.case_id;
    IF r.event_id IS NULL OR c.id IS NULL OR c.submit_event_id IS DISTINCT FROM e.id
       OR r.actor_user_id IS DISTINCT FROM e.actor_user_id OR r.actor_person_id IS DISTINCT FROM e.actor_person_id
       OR r.authorization_version IS DISTINCT FROM e.authorization_version OR r.created_at IS DISTINCT FROM e.created_at
       OR r.request_id IS DISTINCT FROM e.request_id OR r.idempotency_key_hash IS DISTINCT FROM e.idempotency_key_hash THEN
        RAISE EXCEPTION 'condition original input binding required' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(jsonb_build_object('serial_id',s.serial_id::text,'sku_code',r.material_sku_code,
        'serial_no',original->>'serial_no','qr_code',original->>'qr_code') ORDER BY s.serial_id::text),'[]'::jsonb)
        INTO scans FROM public.stock_condition_serials s
        JOIN LATERAL jsonb_array_elements(c.source_jsonb->'serials') original
            ON original->>'serial_id'=s.serial_id::text WHERE s.case_id=c.id;
    IF jsonb_array_length(scans)<>(SELECT count(*) FROM public.stock_condition_serials WHERE case_id=c.id)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(scans) item
            WHERE jsonb_typeof(item->'serial_no') IS DISTINCT FROM 'string'
               OR jsonb_typeof(item->'qr_code') IS DISTINCT FROM 'string') THEN
        RAISE EXCEPTION 'condition original scan source incomplete' USING ERRCODE='23514'; END IF;
    SELECT COALESCE(jsonb_agg(file_id::text ORDER BY file_id::text),'[]'::jsonb) INTO files
        FROM public.stock_condition_files WHERE event_id=e.id;
    expected:=jsonb_build_object('schema_version','condition_input/1','action','submit_return_condition',
        'inbound_line_id',c.inbound_line_id::text,'expected_source_hash',c.source_hash,
        'quantity',trim_scale(c.quantity)::text,'serial_verifications',scans,'evidence_file_ids',files,
        'reason',e.reason,'request_id',e.request_id,'idempotency_key_hash',e.idempotency_key_hash);
    IF public.rsc_canonical_reconciliation_json_0026(r.input_jsonb) IS DISTINCT FROM public.rsc_canonical_reconciliation_json_0026(expected)
       OR r.input_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'condition exact original input required' USING ERRCODE='23514'; END IF;
END $$;

-- Current SKU is checked only when capturing a new input. Later SKU changes
-- must not redefine what the user submitted or reauthorize historical facts.
CREATE FUNCTION public.rsc_condition_capture_request_input()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE current_sku text;
BEGIN
    SELECT m.sku_code INTO current_sku FROM public.stock_condition_events e
        JOIN public.stock_condition_cases c ON c.id=e.case_id AND c.submit_event_id=e.id
        JOIN public.stock_accounts a ON a.id=c.source_account_id
        JOIN public.materials m ON m.id=a.material_id
        WHERE e.id=NEW.event_id AND e.kind='submit' FOR SHARE OF m;
    IF current_sku IS NULL OR NEW.material_sku_code IS DISTINCT FROM current_sku THEN
        RAISE EXCEPTION 'condition original input current SKU required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION public.rsc_condition_request_input_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE event_id uuid;
BEGIN
    IF TG_TABLE_NAME='stock_condition_events' THEN event_id:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('stock_condition_submission_requests','stock_condition_files') THEN event_id:=NEW.event_id;
    ELSIF TG_TABLE_NAME='stock_condition_cases' THEN event_id:=NEW.submit_event_id;
    ELSE SELECT submit_event_id INTO event_id FROM public.stock_condition_cases WHERE id=NEW.case_id;
    END IF;
    PERFORM public.rsc_condition_check_request_input(event_id);
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_check_request_input(uuid),
    public.rsc_condition_capture_request_input(),public.rsc_condition_request_input_fence()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;

CREATE TRIGGER condition_input_immutable BEFORE UPDATE OR DELETE ON public.stock_condition_submission_requests
FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_reject_mutation();
ALTER TABLE public.stock_condition_submission_requests ENABLE ALWAYS TRIGGER condition_input_immutable;
CREATE TRIGGER condition_input_capture BEFORE INSERT ON public.stock_condition_submission_requests
FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_capture_request_input();
ALTER TABLE public.stock_condition_submission_requests ENABLE ALWAYS TRIGGER condition_input_capture;
