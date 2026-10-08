"""Forward close insert and deferred audit validation, independent of the API.

The frozen predecessor approval validator proves its projected approved and
cancelled quantities from immutable commands/decisions and ledger causality.
This layer adds terminal fulfillment, present authority and exact close proof.
"""
from sqlalchemy import text


AUTHORITY_BODY = """
DECLARE ancestors uuid[]; invalid_tree boolean; checked_now timestamptz; tree_before jsonb; tree_after jsonb;
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    IF NOT EXISTS(SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
        JOIN public.organizations org ON org.id=p.organization_id
        WHERE u.id=actor_id AND u.person_id=checked_person_id AND u.authorization_version=auth_version
          AND u.account_status='active' AND p.employment_status='active'
          AND org.status='active' AND org.org_type IN ('headquarters','region_company','department')
          AND EXISTS(SELECT 1 FROM public.auth_identities identity
            WHERE identity.user_id=u.id AND identity.verified_at IS NOT NULL AND identity.revoked_at IS NULL)) THEN
        RAISE EXCEPTION '0169 current close identity required' USING ERRCODE='42501';
    END IF;
    WITH RECURSIVE chain AS (
        SELECT id,parent_id,status,ARRAY[id] path,false cycle FROM public.organizations WHERE id=target_org
        UNION ALL
        SELECT o.id,o.parent_id,o.status,c.path||o.id,o.id=ANY(c.path)
        FROM chain c JOIN public.organizations o ON o.id=c.parent_id
        WHERE NOT c.cycle AND cardinality(c.path)<512
    ) SELECT array_agg(id),bool_or(cycle OR status<>'active' OR cardinality(path)>=512),
        jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id)
      INTO ancestors,invalid_tree,tree_before FROM chain;
    IF ancestors IS NULL OR invalid_tree THEN
        RAISE EXCEPTION '0169 close organization tree invalid' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM public.organizations WHERE id=ANY(ancestors)
      OR id=(SELECT organization_id FROM public.people WHERE id=checked_person_id) ORDER BY id FOR SHARE;
    SELECT jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id) INTO tree_after
      FROM public.organizations WHERE id=ANY(ancestors);
    IF tree_after IS DISTINCT FROM tree_before THEN
        RAISE EXCEPTION '0169 close organization changed while locking' USING ERRCODE='40001';
    END IF;
    checked_now := clock_timestamp();
    IF EXISTS(SELECT 1 FROM public.role_assignments a
        JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id
        JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor_id AND a.status IN ('scheduled','active') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND r.status='active' AND p.resource='material_request' AND p.action IN ('read','close')
          AND p.field_code='' AND rp.effect='deny'
          AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='organization'
            AND lower(replace(a.scope_id,'-','')) IN (SELECT replace(v::text,'-','') FROM unnest(ancestors) v)))) THEN
        RAISE EXCEPTION '0169 explicit close or read deny' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.role_assignments a
        JOIN public.roles r ON r.id=a.role_id JOIN public.people person ON person.id=checked_person_id
        JOIN public.organizations org ON org.id=person.organization_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.id=assignment_id AND a.user_id=actor_id AND a.status IN ('scheduled','active')
          AND a.revoked_at IS NULL AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND r.status='active' AND NOT r.is_external AND rp.effect='allow'
          AND p.resource='material_request' AND p.action='close' AND p.field_code=''
          AND ((r.code='admin' AND a.scope_type='national' AND a.scope_id='*' AND org.org_type='headquarters')
            OR (r.code='provincial_manager' AND a.scope_type='organization'
              AND EXISTS(SELECT 1 FROM public.organizations scope WHERE scope.org_type='region_company'
                AND scope.status='active' AND scope.id=ANY(ancestors)
                AND replace(scope.id::text,'-','')=lower(replace(a.scope_id,'-','')))))) THEN
        RAISE EXCEPTION '0169 current scoped close grant required' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=r.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor_id AND a.status IN ('scheduled','active') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND r.status='active' AND r.code IN ('admin','provincial_manager')
          AND rp.effect='allow' AND p.resource='material_request' AND p.action='read' AND p.field_code=''
          AND ((a.scope_type='national' AND a.scope_id='*') OR (a.scope_type='organization'
            AND lower(replace(a.scope_id,'-','')) IN (SELECT replace(v::text,'-','') FROM unnest(ancestors) v)))) THEN
        RAISE EXCEPTION '0169 current scoped read grant required' USING ERRCODE='42501';
    END IF;
END;
"""

EVIDENCE_BODY = """
DECLARE request_row public.material_requests%ROWTYPE; revision_id uuid; line_facts jsonb; settled jsonb;
BEGIN
    SELECT * INTO STRICT request_row FROM public.material_requests WHERE id=checked_request_id;
    IF request_row.status NOT IN ('approved','partially_approved','cancelled') OR request_row.decided_at IS NULL THEN
        RAISE EXCEPTION '0169 final approval required' USING ERRCODE='23514';
    END IF;
    PERFORM public.rsc_validate_material_request_approval_projection_0045(checked_request_id);
    SELECT id INTO STRICT revision_id FROM public.material_request_revisions
      WHERE request_id=checked_request_id AND revision_no=request_row.revision_no AND status='sealed';
    IF NOT EXISTS(SELECT 1 FROM public.material_request_lines WHERE request_id=checked_request_id
        AND revision_no=request_row.revision_no AND final_approved_qty>0) THEN
        RAISE EXCEPTION '0169 positive approved quantity required' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.inbound_orders i JOIN public.receipts r ON r.id=i.receipt_id
        LEFT JOIN public.inbound_postings ip ON ip.inbound_order_id=i.id
        LEFT JOIN public.inventory_transactions t ON t.id=ip.inventory_transaction_id
        WHERE EXISTS(SELECT 1 FROM public.shipment_lines s JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
            WHERE s.shipment_id=r.shipment_id AND o.request_id=checked_request_id)
          AND (ip.id IS NULL OR t.id IS NULL OR i.status<>'pending' OR t.status<>'posted'
            OR i.posting_transaction_id IS NOT NULL OR t.source_document_type<>'personal_inbound'
            OR t.reversed_transaction_id IS NOT NULL OR t.posted_at IS NULL OR t.ledger_cursor<=0
            OR t.source_document_id<>i.id::text OR EXISTS(SELECT 1 FROM public.inventory_transactions reversal
              WHERE reversal.reversed_transaction_id=t.id))) THEN
        RAISE EXCEPTION '0169 unposted or reversed inbound exists' USING ERRCODE='23514';
    END IF;
    WITH posted AS (
        SELECT o.request_line_id,sum(r.accepted_qty) qty FROM public.outbound_postings o
        JOIN public.shipment_lines s ON s.outbound_posting_id=o.id
        JOIN public.receipt_lines r ON r.shipment_line_id=s.id
        JOIN public.inbound_orders i ON i.receipt_id=r.receipt_id
        JOIN public.inbound_postings ip ON ip.inbound_order_id=i.id
        WHERE o.request_id=checked_request_id GROUP BY o.request_line_id
    ) SELECT jsonb_agg(jsonb_build_object('request_line_id',l.id::text,
        'approved_qty',l.final_approved_qty::text,'cancelled_qty',l.cancelled_qty::text,
        'posted_qty',coalesce(p.qty,0)::numeric(18,3)::text,
        'remaining_qty',(l.final_approved_qty-l.cancelled_qty-coalesce(p.qty,0))::numeric(18,3)::text) ORDER BY l.id)
        INTO line_facts FROM public.material_request_lines l LEFT JOIN posted p ON p.request_line_id=l.id
        WHERE l.request_id=checked_request_id AND l.revision_no=request_row.revision_no;
    IF jsonb_array_length(line_facts) NOT BETWEEN 1 AND 200
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE item->>'remaining_qty'<>'0.000') THEN
        RAISE EXCEPTION '0169 approved lines not completely covered' USING ERRCODE='23514';
    END IF;
    IF EXISTS(SELECT 1 FROM public.stock_reservations r WHERE r.request_id=checked_request_id
        AND r.reserved_qty <> coalesce((SELECT sum(x.released_qty) FROM public.stock_reservation_releases x WHERE x.reservation_id=r.id),0)
            + coalesce((SELECT sum(x.picked_qty) FROM public.stock_reservation_picks x WHERE x.reservation_id=r.id),0))
      OR EXISTS(SELECT 1 FROM public.stock_reservation_picks p WHERE p.request_id=checked_request_id
        AND p.picked_qty<>coalesce((SELECT sum(o.outbound_qty) FROM public.outbound_postings o WHERE o.pick_id=p.id),0))
      OR EXISTS(SELECT 1 FROM public.outbound_postings o WHERE o.request_id=checked_request_id
        AND o.outbound_qty<>coalesce((SELECT sum(s.shipped_qty) FROM public.shipment_lines s WHERE s.outbound_posting_id=o.id),0))
      OR EXISTS(SELECT 1 FROM public.shipment_lines s JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
        WHERE o.request_id=checked_request_id AND (s.shipped_qty<>coalesce((SELECT sum(r.accepted_qty)
          FROM public.receipt_lines r WHERE r.shipment_line_id=s.id),0)
          OR EXISTS(SELECT 1 FROM public.receipt_lines r WHERE r.shipment_line_id=s.id AND r.rejected_qty<>0)))
      OR EXISTS(SELECT 1 FROM public.supply_tasks t JOIN public.material_request_lines l ON l.id=t.request_line_id
        WHERE l.request_id=checked_request_id AND t.status NOT IN ('cancelled','closed_no_supply'))
      OR EXISTS(SELECT 1 FROM public.substitution_decisions s JOIN public.material_request_lines l ON l.id=s.request_line_id
        WHERE l.request_id=checked_request_id AND s.status='proposed') THEN
        RAISE EXCEPTION '0169 unfinished fulfillment exists' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_build_object(
        'reservations',coalesce((SELECT jsonb_agg(id::text ORDER BY id) FROM public.stock_reservations WHERE request_id=checked_request_id),'[]'),
        'releases',coalesce((SELECT jsonb_agg(id::text ORDER BY id) FROM public.stock_reservation_releases WHERE request_id=checked_request_id),'[]'),
        'picks',coalesce((SELECT jsonb_agg(id::text ORDER BY id) FROM public.stock_reservation_picks WHERE request_id=checked_request_id),'[]'),
        'outbound',coalesce((SELECT jsonb_agg(id::text ORDER BY id) FROM public.outbound_postings WHERE request_id=checked_request_id),'[]'),
        'shipment_lines',coalesce((SELECT jsonb_agg(s.id::text ORDER BY s.id) FROM public.shipment_lines s
          JOIN public.outbound_postings o ON o.id=s.outbound_posting_id WHERE o.request_id=checked_request_id),'[]'),
        'receipt_lines',coalesce((SELECT jsonb_agg(r.id::text ORDER BY r.id) FROM public.receipt_lines r
          JOIN public.shipment_lines s ON s.id=r.shipment_line_id JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
          WHERE o.request_id=checked_request_id),'[]')) INTO settled;
    RETURN jsonb_build_object('schema','rsc.material_request_closure_evidence.v1','settled_fulfillment',settled,
        'coverage',jsonb_build_object('schema_version','1.0','request_id',checked_request_id::text,
          'request_version',request_row.version,'revision_id',revision_id::text,
          'assessment','final_approved_quantity_coverage','quantity_coverage_complete',true,
          'pending_inbound_orders',0,'lines',line_facts));
END;
"""

INSERT_BODY = """
DECLARE request_row public.material_requests%ROWTYPE; expected jsonb; payload jsonb;
BEGIN
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT request_row FROM public.material_requests WHERE id=NEW.request_id;
    PERFORM public.rsc_assert_closure_authority_0169(NEW.actor_user_id,NEW.actor_person_id,
        NEW.actor_role_assignment_id,NEW.authorization_version,request_row.requester_org_id);
    IF NEW.request_version<>request_row.version OR NOT EXISTS(SELECT 1 FROM public.material_request_revisions
        WHERE id=NEW.revision_id AND request_id=NEW.request_id AND revision_no=request_row.revision_no)
      OR NEW.created_at<>NEW.occurred_at OR NEW.occurred_at<>CURRENT_TIMESTAMP
      OR NEW.occurred_at<request_row.updated_at OR NEW.reason<>btrim(NEW.reason)
      OR NEW.reason ~ '[[:cntrl:]]' OR NEW.reason=''
      OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$'
      OR NEW.idempotency_key_hash !~ '^[0-9a-f]{64}$'
      OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid THEN
        RAISE EXCEPTION '0169 closure identity version or timestamp mismatch' USING ERRCODE='23514';
    END IF;
    payload := jsonb_build_object('request_id',NEW.request_id::text,'actor_user_id',NEW.actor_user_id,
        'actor_person_id',NEW.actor_person_id::text,'expected_request_version',NEW.request_version,'reason',NEW.reason);
    expected := public.rsc_closure_evidence_0169(NEW.request_id);
    PERFORM public.rsc_assert_closure_authority_0169(NEW.actor_user_id,NEW.actor_person_id,
        NEW.actor_role_assignment_id,NEW.authorization_version,request_row.requester_org_id);
    IF NEW.evidence_jsonb IS DISTINCT FROM expected
       OR NEW.evidence_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
       OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(payload),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0169 closure proof or request hash mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""

AUDIT_BODY = """
DECLARE c public.material_request_closures%ROWTYPE; expected jsonb; matches bigint; total bigint;
BEGIN
    SELECT * INTO STRICT c FROM public.material_request_closures WHERE id=closure_id;
    PERFORM 1 FROM public.material_requests WHERE id=c.request_id FOR UPDATE;
    expected := jsonb_build_object('business_status','closed','closure_id',c.id::text,'request_id',c.request_id::text,
        'revision_id',c.revision_id::text,'request_version',c.request_version,
        'actor_person_id',c.actor_person_id::text,'actor_role_assignment_id',c.actor_role_assignment_id::text,
        'authorization_version',c.authorization_version,'idempotency_key_hash',c.idempotency_key_hash,
        'request_hash',c.request_hash,'evidence_sha256',c.evidence_sha256,
        'reason_sha256',encode(sha256(convert_to(c.reason,'UTF8')),'hex'));
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.close'
        AND actor_user_id=c.actor_user_id AND request_id=c.trace_request_id AND occurred_at=c.occurred_at
        AND created_at=c.created_at AND before_jsonb=jsonb_build_object('business_status','open')
        AND after_jsonb=expected) INTO total,matches FROM public.audit_events
        WHERE aggregate_type='material_request_closure' AND aggregate_id=c.id::text;
    IF total<>1 OR matches<>1 THEN
        RAISE EXCEPTION '0169 closure requires one exact committed audit' USING ERRCODE='23514';
    END IF;
END;
"""

DISPATCH_BODY = """
BEGIN
    IF TG_TABLE_NAME='material_request_closures' THEN
        PERFORM public.rsc_validate_closure_audit_0169(NEW.id);
    ELSIF NEW.action='material_request.close' OR NEW.aggregate_type='material_request_closure' THEN
        IF NEW.action<>'material_request.close' OR NEW.aggregate_type<>'material_request_closure' THEN
            RAISE EXCEPTION '0169 closure audit type mismatch' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_validate_closure_audit_0169(NEW.aggregate_id::uuid);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    'rsc_assert_closure_authority_0169(text,uuid,uuid,bigint,uuid)': (
        'actor_id text, checked_person_id uuid, assignment_id uuid, auth_version bigint, target_org uuid','void',AUTHORITY_BODY),
    'rsc_closure_evidence_0169(uuid)': ('checked_request_id uuid','jsonb',EVIDENCE_BODY),
    'rsc_guard_closure_insert_0169()': ('','trigger',INSERT_BODY),
    'rsc_validate_closure_audit_0169(uuid)': ('closure_id uuid','void',AUDIT_BODY),
    'rsc_dispatch_closure_audit_0169()': ('','trigger',DISPATCH_BODY),
}


def install(db):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator','star_oam_migrator') or identity[2]//10000 != 16:
        raise ValueError('0169 direct PostgreSQL16 migrator required')
    for signature,(arguments,result,body) in FUNCTIONS.items():
        name=signature.split('(',1)[0]
        db.execute(text(f'CREATE FUNCTION public.{name}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, star_oam_api'))
    db.execute(text('CREATE TRIGGER rsc_closure_insert_0169 BEFORE INSERT ON public.material_request_closures '
        'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_closure_insert_0169()'))
    for table in ('material_request_closures','audit_events'):
        condition = ("WHEN (NEW.action='material_request.close' OR NEW.aggregate_type='material_request_closure') "
                     if table=='audit_events' else '')
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_closure_audit_0169 AFTER INSERT ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW ' + condition +
            'EXECUTE FUNCTION public.rsc_dispatch_closure_audit_0169()'))

        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_closure_audit_0169'))
