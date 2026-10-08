-- Private input proof only. No authority, absence, audit, or persistent seal
-- is established here. The registrar must compose those independently.
CREATE FUNCTION public.rsc_condition_decision_seal_canonical(command jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE expected jsonb; files jsonb; item jsonb; seen text[]:='{}'; reason text;
    reference_name text; reference_value text; kind text; scans jsonb; scan_ids text[]:='{}'; expected_scan jsonb;
BEGIN
    IF command IS NULL OR jsonb_typeof(command) IS DISTINCT FROM 'object'
       OR NOT COALESCE(command->>'request_id' ~ '^[A-Za-z0-9._:-]{8,160}$'
          AND command->>'expected_event_hash' ~ '^[a-f0-9]{64}$'
          AND command->>'idempotency_key_hash' ~ '^[a-f0-9]{64}$',false) THEN
        RAISE EXCEPTION 'decision seal complete original input required' USING ERRCODE='23514'; END IF;
    kind:=command->>'action'; reason:=command->>'reason';
    IF kind IS NULL OR kind NOT IN ('supplement','withdraw','verify_region','return_evidence',
          'reject_region','return_region','reject_hq','approve_hq','cancel_approved','execute','release')
       OR jsonb_typeof(command->'reason') IS DISTINCT FROM 'string'
       OR NOT COALESCE(length(reason) BETWEEN 1 AND 500,false)
       OR reason IS DISTINCT FROM btrim(reason,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288))
       OR EXISTS(SELECT 1 FROM generate_series(1,length(reason)) p
          WHERE ascii(substr(reason,p,1))<32 AND ascii(substr(reason,p,1)) NOT IN (9,10)) THEN
        RAISE EXCEPTION 'decision seal exact action and reason required' USING ERRCODE='23514'; END IF;
    FOREACH reference_name IN ARRAY ARRAY['case_id','expected_event_id'] LOOP
        reference_value:=command->>reference_name;
        IF jsonb_typeof(command->reference_name) IS DISTINCT FROM 'string'
           OR reference_value::uuid='00000000-0000-0000-0000-000000000000'::uuid
           OR reference_value::uuid::text IS DISTINCT FROM reference_value THEN
            RAISE EXCEPTION 'decision seal canonical nonzero reference required' USING ERRCODE='23514'; END IF;
    END LOOP;
    files:=command->'evidence_file_ids';
    IF jsonb_typeof(files) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'decision seal original evidence array required' USING ERRCODE='23514'; END IF;
    IF jsonb_array_length(files)>20 OR (kind IN ('supplement','verify_region') AND jsonb_array_length(files)=0) THEN
        RAISE EXCEPTION 'decision seal evidence bounds required' USING ERRCODE='23514'; END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(files) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'string'
           OR (item#>>'{}')::uuid='00000000-0000-0000-0000-000000000000'::uuid
           OR (item#>>'{}')::uuid::text IS DISTINCT FROM item#>>'{}' OR item#>>'{}'=ANY(seen) THEN
            RAISE EXCEPTION 'decision seal canonical unique files required' USING ERRCODE='23514'; END IF;
        seen:=seen||(item#>>'{}');
    END LOOP;
    IF files IS DISTINCT FROM COALESCE((SELECT jsonb_agg(v ORDER BY v COLLATE "C") FROM unnest(seen) v),'[]'::jsonb) THEN
        RAISE EXCEPTION 'decision seal sorted original files required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('schema_version','condition_decision_input/1','action',kind,
        'case_id',command->>'case_id','expected_event_id',command->>'expected_event_id',
        'expected_event_hash',command->>'expected_event_hash','evidence_file_ids',files,
        'reason',reason,'request_id',command->>'request_id','idempotency_key_hash',command->>'idempotency_key_hash');
    IF kind IN ('execute','release') THEN
        scans:=command->'serial_verifications';
        IF jsonb_typeof(scans) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'settlement seal original scan array required' USING ERRCODE='23514'; END IF;
        IF jsonb_array_length(scans)>1000 THEN
            RAISE EXCEPTION 'settlement seal original scan bounds required' USING ERRCODE='23514'; END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(scans) LOOP
            IF jsonb_typeof(item) IS DISTINCT FROM 'object'
               OR jsonb_typeof(item->'serial_id') IS DISTINCT FROM 'string'
               OR (item->>'serial_id')::uuid::text IS DISTINCT FROM item->>'serial_id'
               OR item->>'serial_id'=ANY(scan_ids) THEN
                RAISE EXCEPTION 'settlement seal canonical unique scan required' USING ERRCODE='23514'; END IF;
            FOREACH reference_name IN ARRAY ARRAY['sku_code','serial_no','qr_code'] LOOP
                IF jsonb_typeof(item->reference_name) IS DISTINCT FROM 'string'
                   OR length(item->>reference_name) NOT BETWEEN 1 AND
                      (CASE reference_name WHEN 'sku_code' THEN 80 WHEN 'serial_no' THEN 200 ELSE 250 END) THEN
                    RAISE EXCEPTION 'settlement seal original scan text invalid' USING ERRCODE='23514'; END IF;
            END LOOP;
            expected_scan:=jsonb_build_object('serial_id',item->>'serial_id','sku_code',item->>'sku_code',
                'serial_no',item->>'serial_no','qr_code',item->>'qr_code');
            IF item IS DISTINCT FROM expected_scan THEN
                RAISE EXCEPTION 'settlement seal exact original scan fields required' USING ERRCODE='23514'; END IF;
            scan_ids:=scan_ids||(item->>'serial_id');
        END LOOP;
        IF scan_ids IS DISTINCT FROM ARRAY(SELECT v FROM unnest(scan_ids) v ORDER BY v COLLATE "C") THEN
            RAISE EXCEPTION 'settlement seal sorted original scans required' USING ERRCODE='23514'; END IF;
        -- Preserve the supplied physical input without claiming it passed
        -- preflight or still matches today's material/serial labels.
        expected:=expected||jsonb_build_object('schema_version','condition_settlement_input/1','serial_verifications',scans);
    END IF;
    IF command IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'decision seal exact canonical input required' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('kind',kind,'case_id',command->>'case_id',
        'expected_event_id',command->>'expected_event_id','claimed_event_hash',command->>'expected_event_hash',
        'command_jsonb',expected,'request_id',command->>'request_id','reason',reason,
        'idempotency_key_hash',command->>'idempotency_key_hash',
        'request_hash',encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex'));
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RAISE EXCEPTION 'decision seal original input syntax invalid' USING ERRCODE='23514';
END $$;

CREATE FUNCTION public.rsc_condition_decision_seal_input(command jsonb, client_key text)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE prepared jsonb; aliases jsonb; token text;
BEGIN
    IF client_key IS NULL OR client_key !~ '^[A-Za-z0-9._:-]{8,200}$' THEN
        RAISE EXCEPTION 'decision seal exact original key required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_condition_decision_seal_canonical(command);
    SELECT jsonb_object_agg(name,encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')
        ||convert_to(prefix||client_key,'UTF8')),'hex')) INTO aliases FROM (VALUES __KEY_DOMAINS__) d(name,prefix);
    token:=encode(sha256(convert_to('cloud_oam.loss.correction.key.v1','UTF8')||decode('00','hex')||convert_to(client_key,'UTF8')),'hex');
    IF prepared->>'idempotency_key_hash' IS DISTINCT FROM aliases->>'condition_key_hash' THEN
        RAISE EXCEPTION 'decision seal original key and input differ' USING ERRCODE='23514'; END IF;
    RETURN prepared||aliases||jsonb_build_object('key_token',token);
END $$;

-- Row integrity remains separate from live authority, source history, global
-- absence and audit proof. Those are mandatory before granting a registrar.
CREATE FUNCTION public.rsc_condition_decision_seal_input_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE prepared jsonb; raw jsonb; field_name text;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'decision seal rows are immutable' USING ERRCODE='23514'; END IF;
    raw:=to_jsonb(NEW);
    prepared:=public.rsc_condition_decision_seal_canonical(raw->'command_jsonb');
    FOREACH field_name IN ARRAY ARRAY['kind','case_id','expected_event_id','claimed_event_hash',
        'command_jsonb','request_id','reason','idempotency_key_hash','request_hash'] LOOP
        IF raw->field_name IS DISTINCT FROM prepared->field_name THEN
            RAISE EXCEPTION 'decision seal row differs from original command' USING ERRCODE='23514'; END IF;
    END LOOP;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_decision_seal_canonical(jsonb),
    public.rsc_condition_decision_seal_input(jsonb,text),
    public.rsc_condition_decision_seal_input_guard()
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
