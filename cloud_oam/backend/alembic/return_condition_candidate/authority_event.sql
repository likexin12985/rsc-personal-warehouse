-- Bind current authority to the persisted event, never caller-selected scope.
CREATE FUNCTION public.rsc_condition_check_current_event(event_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE e public.stock_condition_events%ROWTYPE; c public.stock_condition_cases%ROWTYPE;
    submitted public.stock_condition_events%ROWTYPE; regional public.stock_condition_events%ROWTYPE;
    line public.stock_operation_return_inbound_lines%ROWTYPE;
    header public.stock_operation_return_inbounds%ROWTYPE; source public.stock_accounts%ROWTYPE;
BEGIN
    SELECT * INTO e FROM public.stock_condition_events WHERE id=event_id;
    SELECT * INTO c FROM public.stock_condition_cases WHERE id=e.case_id;
    IF e.id IS NULL OR c.id IS NULL THEN
        RAISE EXCEPTION 'condition exact current event required' USING ERRCODE='23514'; END IF;
    -- Same per-source lock as the history budget guard. Does not serialize
    -- unrelated inbound lines. Historical immutable guards prove the rest.
    SELECT * INTO line FROM public.stock_operation_return_inbound_lines WHERE id=c.inbound_line_id FOR NO KEY UPDATE;
    SELECT * INTO header FROM public.stock_operation_return_inbounds WHERE id=line.inbound_id FOR SHARE;
    SELECT * INTO source FROM public.stock_accounts WHERE id=line.target_account_id FOR SHARE;
    IF line.id IS NULL OR header.id IS NULL OR source.id IS NULL
       OR header.plan_jsonb->>'schema_version' IS DISTINCT FROM '1.0'
       OR line.condition_code NOT IN ('new','used') OR source.condition_code IS DISTINCT FROM line.condition_code
       OR source.availability_bucket IS DISTINCT FROM 'available'
       OR source.material_id IS DISTINCT FROM line.material_id OR source.lot_id IS DISTINCT FROM line.lot_id
       OR source.location_id IS DISTINCT FROM header.target_location_id
       OR source.custodian_person_id IS DISTINCT FROM header.operator_person_id
       OR c.operation_type IS DISTINCT FROM 'condition_correction' OR c.inbound_id IS DISTINCT FROM header.id
       OR c.source_account_id IS DISTINCT FROM source.id OR c.recorded_condition IS DISTINCT FROM source.condition_code
       OR e.inbound_line_id IS DISTINCT FROM line.id OR e.source_account_id IS DISTINCT FROM source.id
       OR e.submit_event_id IS DISTINCT FROM c.submit_event_id THEN
        RAISE EXCEPTION 'condition exact current source binding required' USING ERRCODE='23514'; END IF;
    SELECT * INTO submitted FROM public.stock_condition_events
        WHERE id=c.submit_event_id AND case_id=c.id AND kind='submit';
    IF submitted.id IS NULL OR submitted.actor_person_id IS DISTINCT FROM source.custodian_person_id THEN
        RAISE EXCEPTION 'condition original custodian submission required' USING ERRCODE='23514'; END IF;
    IF e.kind IN ('submit','supplement','withdraw','execute','release') THEN
        IF e.actor_user_id IS DISTINCT FROM submitted.actor_user_id
           OR e.actor_person_id IS DISTINCT FROM submitted.actor_person_id THEN
            RAISE EXCEPTION 'condition only original applicant may act' USING ERRCODE='23514'; END IF;
    ELSE
        IF e.actor_user_id=submitted.actor_user_id OR e.actor_person_id=submitted.actor_person_id THEN
            RAISE EXCEPTION 'condition applicant cannot review own case' USING ERRCODE='23514'; END IF;
        IF e.kind IN ('return_region','reject_hq','approve_hq','cancel_approved') THEN
            SELECT * INTO regional FROM public.stock_condition_events
                WHERE case_id=c.id AND kind='verify_region' AND event_sequence<e.event_sequence
                ORDER BY event_sequence DESC LIMIT 1;
            IF regional.id IS NULL OR e.actor_user_id=regional.actor_user_id OR e.actor_person_id=regional.actor_person_id THEN
                RAISE EXCEPTION 'condition headquarters reviewer must be independent' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    PERFORM public.rsc_condition_assert_current_authority(e.actor_user_id,e.authorization_version,
        e.actor_person_id,source.owner_org_id,source.location_id,source.custodian_person_id,c.custody_assignment_id,e.kind);
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_condition_check_current_event(uuid)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;

CREATE FUNCTION public.rsc_condition_current_event_fence()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
BEGIN
    PERFORM public.rsc_condition_check_current_event(NEW.id);
    RETURN NULL;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_condition_current_event_fence()
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox,star_oam_backup;
CREATE CONSTRAINT TRIGGER condition_current_authority AFTER INSERT ON public.stock_condition_events
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_condition_current_event_fence();
ALTER TABLE public.stock_condition_events ENABLE ALWAYS TRIGGER condition_current_authority;
