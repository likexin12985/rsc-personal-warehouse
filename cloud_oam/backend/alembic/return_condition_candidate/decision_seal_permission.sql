-- Private current permission component. The source helper must derive owner_id
-- and independently bind requester/reviewer identity to historical events.
-- No stock/custody/latest-state authorization and no write entry point.
CREATE FUNCTION public.rsc_condition_decision_seal_permission(
    actor_id text, actor_version bigint, actor_person uuid, owner_id uuid, required_kind text, require_write boolean)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.users%ROWTYPE; person public.people%ROWTYPE;
    actor_org public.organizations%ROWTYPE; ancestors uuid[]; latest uuid[]; invalid_tree boolean;
    at timestamptz; required_resource text; required_action text;
    stage_action text; required_role text; required_scope text; required_scope_id text;
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' OR require_write IS NULL OR owner_id IS NULL
       OR required_kind IS NULL OR required_kind NOT IN ('supplement','withdraw','verify_region','return_evidence',
            'reject_region','return_region','reject_hq','approve_hq','cancel_approved','execute','release') THEN
        RAISE EXCEPTION 'condition decision seal read committed and explicit authority required' USING ERRCODE='23514'; END IF;
    IF required_kind IN ('supplement','withdraw','execute','release','verify_region','return_evidence','reject_region') THEN
        required_role:='provincial_manager'; required_scope:='organization'; required_scope_id:=owner_id::text;
        stage_action:=CASE WHEN required_kind IN ('supplement','withdraw','execute','release') THEN required_kind||'_return_condition'
            ELSE 'review_return_condition_regional' END;
    ELSE
        required_role:='admin'; required_scope:='national'; required_scope_id:='*';
        stage_action:=CASE required_kind WHEN 'cancel_approved' THEN 'cancel_return_condition_approval'
            ELSE 'review_return_condition_headquarters' END;
    END IF;
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
        RAISE EXCEPTION 'condition decision seal current identity required' USING ERRCODE='23514'; END IF;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=owner_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active'
        OR (parent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.organizations o WHERE o.id=tree.parent_id)))
        INTO ancestors,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) THEN
        RAISE EXCEPTION 'condition decision seal current owner hierarchy invalid' USING ERRCODE='23514'; END IF;
    PERFORM id FROM public.organizations WHERE id=ANY(ancestors)
       OR id::text IN (SELECT scope_id FROM public.role_assignments WHERE user_id=actor_id AND scope_type='organization')
       ORDER BY id FOR SHARE;
    WITH RECURSIVE tree AS (
        SELECT id,parent_id,status,ARRAY[id] visited,false cycle FROM public.organizations WHERE id=owner_id
        UNION ALL SELECT o.id,o.parent_id,o.status,t.visited||o.id,o.id=ANY(t.visited)
            FROM public.organizations o JOIN tree t ON o.id=t.parent_id WHERE NOT t.cycle
    ) SELECT array_agg(id ORDER BY id),bool_or(cycle OR status<>'active'
        OR (parent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.organizations o WHERE o.id=tree.parent_id)))
        INTO latest,invalid_tree FROM tree;
    IF COALESCE(invalid_tree,true) OR ancestors IS DISTINCT FROM latest
       OR NOT EXISTS(SELECT 1 FROM public.organizations WHERE id=owner_id AND org_type='region_company' AND status='active') THEN
        RAISE EXCEPTION 'condition decision seal current owner hierarchy changed' USING ERRCODE='23514'; END IF;
    -- Historical custody is proved by the source. No current warehouse,
    -- custody assignment, SKU policy or executable balance is required here.
    at:=clock_timestamp();
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
        RAISE EXCEPTION 'condition decision seal current role graph invalid' USING ERRCODE='23514'; END IF;
    FOR required_resource,required_action IN SELECT * FROM (VALUES ('inventory','read'),('stock_operation','read'),
        ('stock_operation',stage_action)) required(resource,action)
        WHERE require_write OR required.action='read'
    LOOP
        IF NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
            JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
            WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=at AND (a.valid_to IS NULL OR a.valid_to>at) AND r.status='active' AND NOT r.is_external
              AND rp.effect='allow' AND p.resource=required_resource AND p.action=required_action AND p.field_code=''
              AND CASE WHEN required_action='read' THEN
                (a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='organization' AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value))
                ELSE r.code=required_role AND a.scope_type=required_scope AND a.scope_id=required_scope_id END)
           OR EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
            JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
            WHERE a.user_id=actor.id AND a.status IN ('active','scheduled') AND a.revoked_at IS NULL
              AND a.valid_from<=at AND (a.valid_to IS NULL OR a.valid_to>at) AND r.status='active'
              AND rp.effect='deny' AND p.resource=required_resource AND p.action=required_action AND p.field_code=''
              AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='organization'
                AND a.scope_id IN (SELECT value::text FROM unnest(ancestors) value)))) THEN
            RAISE EXCEPTION 'condition decision seal current read and action permission required' USING ERRCODE='23514'; END IF;
    END LOOP;
    RETURN;
END $$;
REVOKE ALL ON FUNCTION public.rsc_condition_decision_seal_permission(text,bigint,uuid,uuid,text,boolean)
FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,star_oam_edge,edge_inbox;
