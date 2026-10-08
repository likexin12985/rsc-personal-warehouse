-- Private input proof for the controlled seal writer. This does not prove
-- source existence, authority, absence or audit, and creates no seal rows.
-- Never grant this component as a business endpoint or treat its result as
-- a posting/closure permit. The eventual registrar must compose all proofs.
CREATE FUNCTION public.rsc_scrap_seal_canonical_0165(checked_kind text, command jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE source jsonb; expected_source jsonb; body jsonb; expected jsonb; evidence jsonb;
    reason text; request text; plan text; action text; value text; field text;
    ids text[]; digests text[]; files jsonb;
BEGIN
    IF checked_kind IS NULL OR checked_kind NOT IN ('original','correction','apply','regional','headquarters','execute')
       OR command IS NULL OR jsonb_typeof(command)<>'object' THEN
        RAISE EXCEPTION '0165 explicit seal kind and command required' USING ERRCODE='23514';
    END IF;
    request:=command->>'request_id';
    IF checked_kind IN ('original','correction') THEN
        body:=command->'intent';
        reason:=body->>'execution_reason';
        source:=body->'source';
        files:=body->'evidence_file_ids';
        plan:=command->>'expected_plan_hash';
        IF checked_kind='original' THEN
            ids:=ARRAY['headquarters_decision_id'];
            digests:=ARRAY['expected_headquarters_review_hash','expected_submission_plan_hash'];
        ELSE
            ids:=ARRAY['root_disposition_id','reversal_id','correction_decision_id'];
            digests:=ARRAY['expected_root_request_hash','expected_submission_plan_hash',
                'expected_reversal_hash','expected_correction_decision_hash'];
        END IF;
        expected_source:=jsonb_build_object('kind',checked_kind);
    ELSE
        body:=command;
        reason:=command->>'reason';
        source:=command->'source';
        ids:=ARRAY['scrap_line_id']; digests:=ARRAY['expected_scrap_request_hash'];
        expected_source:='{}'::jsonb;
        IF checked_kind='apply' THEN files:=command->'evidence_file_ids'; END IF;
        IF checked_kind='execute' THEN plan:=command->>'expected_plan_hash'; END IF;
    END IF;
    IF NOT COALESCE(request ~ '^[A-Za-z0-9._:-]{8,160}$'
        AND length(reason) BETWEEN 1 AND 500
        AND btrim(reason,chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(28)||chr(29)||chr(30)||chr(31)||chr(32)||chr(133)||chr(160)||chr(5760)||chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288))=reason
        AND jsonb_typeof(source)='object',false)
       OR EXISTS(SELECT 1 FROM generate_series(1,length(reason)) p
           WHERE ascii(substr(reason,p,1))<32 AND ascii(substr(reason,p,1)) NOT IN (9,10)) THEN
        RAISE EXCEPTION '0165 exact canonical seal request identity required' USING ERRCODE='23514';
    END IF;
    FOREACH field IN ARRAY ids LOOP
        value:=source->>field;
        IF value IS NULL OR value::uuid='00000000-0000-0000-0000-000000000000'::uuid THEN
            RAISE EXCEPTION '0165 nonzero seal source identifier required' USING ERRCODE='23514'; END IF;
        expected_source:=expected_source||jsonb_build_object(field,value::uuid::text);
    END LOOP;
    FOREACH field IN ARRAY digests LOOP
        value:=source->>field;
        IF value IS NULL OR value !~ '^[a-f0-9]{64}$' THEN
            RAISE EXCEPTION '0165 exact seal source hash required' USING ERRCODE='23514'; END IF;
        expected_source:=expected_source||jsonb_build_object(field,value);
    END LOOP;
    IF checked_kind IN ('original','correction','execute') AND
        (plan IS NULL OR plan !~ '^[a-f0-9]{64}$') THEN
        RAISE EXCEPTION '0165 retain the original seal plan hash' USING ERRCODE='23514'; END IF;
    IF checked_kind IN ('original','correction','apply') THEN
        IF files IS NULL OR jsonb_typeof(files)<>'array' THEN
            RAISE EXCEPTION '0165 canonical seal evidence identifiers required' USING ERRCODE='23514'; END IF;
        IF jsonb_array_length(files) NOT BETWEEN 1 AND 20
           OR EXISTS(SELECT 1 FROM jsonb_array_elements(files) f WHERE jsonb_typeof(f)<>'string') THEN
            RAISE EXCEPTION '0165 canonical seal evidence list required' USING ERRCODE='23514'; END IF;
        SELECT jsonb_agg(f.identifier::uuid::text ORDER BY f.identifier::uuid::text COLLATE "C") INTO evidence
            FROM jsonb_array_elements_text(files) AS f(identifier);
        IF evidence IS DISTINCT FROM files
           OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(files) AS f(identifier)
               WHERE f.identifier::uuid='00000000-0000-0000-0000-000000000000'::uuid)
           OR (SELECT count(DISTINCT f.identifier) FROM jsonb_array_elements_text(files) AS f(identifier))<>jsonb_array_length(files) THEN
            RAISE EXCEPTION '0165 sorted unique nonzero seal evidence required' USING ERRCODE='23514'; END IF;
    END IF;
    IF checked_kind IN ('original','correction') THEN
        expected:=jsonb_build_object('schema_version',1,'action','scrap',
            'intent',jsonb_build_object('source',expected_source,'execution_reason',reason,'evidence_file_ids',evidence),
            'request_id',request,'expected_plan_hash',plan);
    ELSE
        action:=CASE checked_kind WHEN 'apply' THEN 'apply_scrap_recovery'
            WHEN 'regional' THEN 'review_scrap_recovery_region'
            WHEN 'headquarters' THEN 'review_scrap_recovery_headquarters' ELSE 'execute_scrap_recovery' END;
        expected:=jsonb_build_object('action',action,'source',expected_source,'reason',reason,'request_id',request);
        IF checked_kind='apply' THEN
            expected:=expected||jsonb_build_object('evidence_file_ids',evidence);
        ELSE
            ids:=ARRAY['recovery_request_id']; digests:=ARRAY['expected_request_hash'];
            IF checked_kind='headquarters' THEN
                ids:=ids||'regional_review_id'::text; digests:=digests||'expected_regional_hash'::text;
            ELSIF checked_kind='execute' THEN
                ids:=ids||'headquarters_review_id'::text; digests:=digests||'expected_headquarters_hash'::text;
            END IF;
            FOREACH field IN ARRAY ids LOOP
                value:=command->>field;
                IF value IS NULL OR value::uuid='00000000-0000-0000-0000-000000000000'::uuid THEN
                    RAISE EXCEPTION '0165 exact seal review ancestor required' USING ERRCODE='23514'; END IF;
                expected:=expected||jsonb_build_object(field,value::uuid::text);
            END LOOP;
            FOREACH field IN ARRAY digests LOOP
                value:=command->>field;
                IF value IS NULL OR value !~ '^[a-f0-9]{64}$' THEN
                    RAISE EXCEPTION '0165 exact seal review hash required' USING ERRCODE='23514'; END IF;
                expected:=expected||jsonb_build_object(field,value);
            END LOOP;
            IF checked_kind='execute' THEN
                expected:=expected||jsonb_build_object('expected_plan_hash',plan);
            ELSE
                value:=command->>'decision';
                IF value IS NULL OR NOT (checked_kind='regional' AND value IN ('verified','needs_evidence')
                    OR checked_kind='headquarters' AND value IN ('approve','request_regional_review')) THEN
                    RAISE EXCEPTION '0165 exact seal review decision required' USING ERRCODE='23514'; END IF;
                expected:=expected||jsonb_build_object('decision',value);
            END IF;
        END IF;
    END IF;
    IF command IS DISTINCT FROM expected
       OR public.rsc_canonical_reconciliation_json_0026(command) IS DISTINCT FROM
          public.rsc_canonical_reconciliation_json_0026(expected) THEN
        RAISE EXCEPTION '0165 complete canonical seal command required' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('kind',checked_kind,'command_jsonb',expected,'request_id',request,'reason',reason,
        'plan_hash',plan,'request_hash',encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex'));
EXCEPTION WHEN invalid_text_representation THEN
    RAISE EXCEPTION '0165 canonical seal identifier invalid' USING ERRCODE='23514';
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_scrap_seal_canonical_0165(text,jsonb)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;

-- Real key derivation belongs only to the write boundary. Retained proof does
-- not store, invent, or recover plaintext client keys.
CREATE FUNCTION public.rsc_prepare_scrap_seal_request_0165(checked_kind text, command jsonb, client_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE prepared jsonb; hashes text[]; token text;
BEGIN
    IF client_key IS NULL OR client_key !~ '^[A-Za-z0-9._:-]{8,200}$' THEN
        RAISE EXCEPTION '0165 real client key required' USING ERRCODE='23514'; END IF;
    prepared:=public.rsc_scrap_seal_canonical_0165(checked_kind,command);
    SELECT array_agg(encode(sha256(convert_to('cloud_oam.inventory.idempotency.v1','UTF8')||decode('00','hex')
        ||convert_to(a.prefix||client_key,'UTF8')),'hex') ORDER BY a.ordinal) INTO hashes
        FROM (VALUES ('stock-loss:reverse_loss:',1),('stock-loss:approve_loss_correction:',2),
            ('stock-loss:correct_loss:',3),('stock-scrap-recovery:',4),('stock-scrap:',5)) a(prefix,ordinal);
    token:=encode(sha256(convert_to('cloud_oam.loss.correction.key.v1','UTF8')||decode('00','hex')||convert_to(client_key,'UTF8')),'hex');
    RETURN prepared||jsonb_build_object('key_token',token,'reversal_key_hash',hashes[1],'approval_key_hash',hashes[2],
        'correction_key_hash',hashes[3],'recovery_key_hash',hashes[4],'scrap_key_hash',hashes[5],
        'idempotency_key_hash',CASE WHEN checked_kind IN ('original','correction') THEN hashes[5] ELSE hashes[4] END);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_prepare_scrap_seal_request_0165(text,jsonb,text)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
