-- Current-authority component. The eventual admission trigger must derive
-- owner/location/requester from the exact scrap source, never client input.
-- Historical graph and delivery checks must NOT call this function: retained
-- approvals remain facts after an old approver's permission expires.
CREATE FUNCTION public.rsc_assert_scrap_recovery_authority_0165(
    actor_id text, actor_version bigint, actor_person uuid, owner_id uuid,
    location_id uuid, requester_id uuid, required_stage text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, public
AS $function$
DECLARE actor public.users%ROWTYPE; person public.people%ROWTYPE; actor_org public.organizations%ROWTYPE;
    location public.stock_locations%ROWTYPE; at timestamptz; ancestors uuid[]; latest uuid[];
    invalid_tree boolean; target_org uuid; required_role text; required_scope text; required_scope_id text;
    required_action text;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed'
       OR required_stage IS NULL OR required_stage NOT IN ('apply','regional','headquarters','execute') THEN
        RAISE EXCEPTION '0165 explicit recovery stage and read committed required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    SELECT * INTO actor FROM public.users WHERE id=actor_id;
    SELECT * INTO person FROM public.people WHERE id=actor.person_id;
    SELECT * INTO actor_org FROM public.organizations WHERE id=person.organization_id FOR SHARE;
    IF actor.id IS NULL OR person.id IS NULL OR actor_org.id IS NULL
       OR NOT actor.is_active OR actor.account_status IS DISTINCT FROM 'active'
       OR person.employment_status IS DISTINCT FROM 'active'
       OR actor.person_id IS DISTINCT FROM actor_person OR actor.authorization_version IS DISTINCT FROM actor_version
       OR actor_org.status IS DISTINCT FROM 'active' OR actor_org.org_type NOT IN ('headquarters','region_company','department')
       OR NOT EXISTS(SELECT 1 FROM public.auth_identities WHERE user_id=actor.id
            AND status='active' AND verified_at IS NOT NULL AND revoked_at IS NULL) THEN
        RAISE EXCEPTION '0165 current recovery identity invalid' USING ERRCODE='23514'; END IF;
    IF required_stage='apply' THEN
        IF actor_person IS DISTINCT FROM requester_id THEN
            RAISE EXCEPTION '0165 recovery applicant must be original custodian' USING ERRCODE='23514'; END IF;
        required_role:='technician'; required_scope:='person'; required_scope_id:=requester_id::text;
        required_action:='apply_scrap_recovery'; target_org:=person.organization_id;
    ELSIF required_stage='regional' THEN
        required_role:='provincial_manager'; required_scope:='organization'; required_scope_id:=owner_id::text;
        required_action:='review_scrap_recovery_regional'; target_org:=owner_id;
    ELSE
        required_role:='admin'; required_scope:='national'; required_scope_id:='*'; target_org:=owner_id;
        required_action:=CASE required_stage WHEN 'headquarters' THEN 'review_scrap_recovery_headquarters'
            ELSE 'execute_scrap_recovery' END;
    END IF;
    -- Organization-deny semantics follow the target: the applicant's person
    -- scope uses their organization; regional/HQ actions use the asset owner.
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=target_org
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO ancestors,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) THEN
        RAISE EXCEPTION '0165 recovery scope hierarchy invalid' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.organizations WHERE id=ANY(ancestors) OR id=owner_id
       OR id::text IN (SELECT scope_id FROM public.role_assignments WHERE user_id=actor_id AND scope_type='organization')
       ORDER BY id FOR SHARE;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=target_org
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active') INTO latest,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) OR ancestors IS DISTINCT FROM latest
       OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=owner_id AND org_type='region_company' AND status='active') THEN
        RAISE EXCEPTION '0165 recovery owner or scope changed' USING ERRCODE='23514'; END IF;
    SELECT * INTO location FROM public.stock_locations WHERE id=location_id FOR UPDATE;
    IF location.id IS NULL OR location.status IS DISTINCT FROM 'active' OR location.location_type IS DISTINCT FROM 'personal'
       OR location.owner_org_id IS DISTINCT FROM owner_id OR location.custodian_person_id IS DISTINCT FROM requester_id THEN
        RAISE EXCEPTION '0165 exact current recovery personal location required' USING ERRCODE='23514'; END IF;
    -- The location lock also prevents a new FK-bound assignment from entering
    -- while the current assignment rows are held through the transaction.
    PERFORM id FROM public.custody_assignments WHERE custody_assignments.location_id=location.id ORDER BY id FOR SHARE;
    at:=clock_timestamp();
    IF (SELECT count(*) FROM public.custody_assignments WHERE custody_assignments.location_id=location.id
            AND valid_from<=at AND (valid_to IS NULL OR valid_to>at))<>1
       OR NOT EXISTS(SELECT 1 FROM public.custody_assignments WHERE custody_assignments.location_id=location.id
            AND custodian_person_id=requester_id AND valid_from<=at AND (valid_to IS NULL OR valid_to>at)) THEN
        RAISE EXCEPTION '0165 unique current recovery custody required' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        LEFT JOIN public.organizations scope ON scope.id::text=a.scope_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=at AND (a.valid_to IS NULL OR a.valid_to>at) AND r.status='active'
          AND NOT CASE r.code
            WHEN 'admin' THEN NOT r.is_external AND a.scope_type='national' AND a.scope_id='*' AND actor_org.org_type='headquarters'
            WHEN 'provincial_manager' THEN NOT r.is_external AND a.scope_type='organization' AND scope.id IS NOT NULL
                AND scope.org_type='region_company' AND scope.status='active'
            WHEN 'technician' THEN NOT r.is_external AND a.scope_type='person' AND a.scope_id=person.id::text
            ELSE false END) THEN
        RAISE EXCEPTION '0165 recovery role graph invalid' USING ERRCODE='23514'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=at AND (a.valid_to IS NULL OR a.valid_to>at)
          AND r.status='active' AND r.code=required_role AND NOT r.is_external
          AND a.scope_type=required_scope AND a.scope_id=required_scope_id
          AND rp.effect='allow' AND p.resource='stock_operation' AND p.action=required_action AND p.field_code='')
       OR EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
          AND a.valid_from<=at AND (a.valid_to IS NULL OR a.valid_to>at) AND r.status='active'
          AND rp.effect='deny' AND p.resource='stock_operation' AND p.action=required_action AND p.field_code=''
          AND ((a.scope_type='national' AND a.scope_id='*')
            OR (a.scope_type='organization' AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value))
            OR (required_stage='apply' AND a.scope_type='person' AND a.scope_id=requester_id::text))) THEN
        RAISE EXCEPTION '0165 current stage-specific recovery permission required' USING ERRCODE='23514'; END IF;
END;
$function$;
REVOKE ALL ON FUNCTION public.rsc_assert_scrap_recovery_authority_0165(text,bigint,uuid,uuid,uuid,uuid,text)
FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox;
