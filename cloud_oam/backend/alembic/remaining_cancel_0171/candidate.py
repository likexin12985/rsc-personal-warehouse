"""Forward PG16 candidate, not an activated Alembic revision.

The native gate records catalog before/after for subsequent frozen activation.
Never execute this compiler against an external or production database.
"""
from pathlib import Path
import runpy
from sqlalchemy import text

FOLDER = Path(__file__).parents[1] / 'request_closure_0169'
CLOSURE = runpy.run_path(str(FOLDER / 'insert_guard.py'))
BARRIER = runpy.run_path(str(FOLDER / 'write_barrier.py'))


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError('0171 reviewed predecessor anchor changed')
    return value.replace(old, new)


PROOF = CLOSURE['EVIDENCE_BODY']
PROOF = replace_once(PROOF, "('approved','partially_approved','cancelled')", "('approved','partially_approved')")
PROOF = replace_once(PROOF,
    "OR EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE item->>'remaining_qty'<>'0.000')",
    "OR EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE (item->>'remaining_qty')::numeric<0 OR (item->>'cancelled_qty')::numeric<>0)\n"
    "       OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(line_facts) item WHERE (item->>'remaining_qty')::numeric>0)")
PROOF = replace_once(PROOF, "    RETURN jsonb_build_object('schema','rsc.material_request_closure_evidence.v1','settled_fulfillment',settled,\n"
    "        'coverage',jsonb_build_object('schema_version','1.0','request_id',checked_request_id::text,\n"
    "          'request_version',request_row.version,'revision_id',revision_id::text,\n"
    "          'assessment','final_approved_quantity_coverage','quantity_coverage_complete',true,\n"
    "          'pending_inbound_orders',0,'lines',line_facts));",
    """    RETURN jsonb_build_object('settled_fulfillment',settled,
        'before',jsonb_build_object('schema_version','1.0','request_id',checked_request_id::text,
          'request_version',request_row.version,'revision_id',revision_id::text,
          'assessment','remaining_fulfillment_quantities','open_supply_tasks',0,'pending_substitutions',0,
          'lines',(SELECT jsonb_agg((item-'remaining_qty')||jsonb_build_object(
            'unreserved_qty',item->>'remaining_qty','reserved_unpicked_qty','0.000',
            'picked_unoutbound_qty','0.000','outbound_unshipped_qty','0.000',
            'shipped_unreceived_qty','0.000','accepted_unposted_qty','0.000','rejected_unsettled_qty','0.000')
            ORDER BY item->>'request_line_id') FROM jsonb_array_elements(line_facts) item)));
""")

AUTHORITY = """
DECLARE r public.material_requests%ROWTYPE; checked_now timestamptz; eligible bigint;
    ancestors uuid[]; invalid_tree boolean; tree_before jsonb; tree_after jsonb;
BEGIN
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[actor_id]::text[]);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=checked_request_id;
    IF r.created_by_user_id<>actor_id OR r.requester_user_id<>actor_id OR r.requester_person_id<>checked_person_id OR NOT EXISTS(
        SELECT 1 FROM public.users u JOIN public.people p ON p.id=u.person_id
        JOIN public.organizations org ON org.id=p.organization_id
        WHERE u.id=actor_id AND p.id=checked_person_id AND u.authorization_version=auth_version
          AND u.account_status='active' AND p.employment_status='active' AND org.status='active'
          AND EXISTS(SELECT 1 FROM public.auth_identities i WHERE i.user_id=u.id
            AND i.verified_at IS NOT NULL AND i.revoked_at IS NULL)) THEN
        RAISE EXCEPTION '0171 current original requester required' USING ERRCODE='42501';
    END IF;
    WITH RECURSIVE chain AS (
        SELECT id,parent_id,status,ARRAY[id] path,false cycle FROM public.organizations WHERE id=(SELECT organization_id FROM public.people WHERE id=checked_person_id)
        UNION ALL SELECT o.id,o.parent_id,o.status,c.path||o.id,o.id=ANY(c.path)
        FROM chain c JOIN public.organizations o ON o.id=c.parent_id WHERE NOT c.cycle AND cardinality(c.path)<512
    ) SELECT array_agg(id),bool_or(cycle OR status<>'active' OR cardinality(path)>=512),
        jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id) INTO ancestors,invalid_tree,tree_before FROM chain;
    IF ancestors IS NULL OR invalid_tree THEN
        RAISE EXCEPTION '0171 requester organization invalid' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM public.organizations WHERE id=ANY(ancestors) ORDER BY id FOR SHARE;
    SELECT jsonb_agg(jsonb_build_array(id,parent_id,status) ORDER BY id) INTO tree_after FROM public.organizations WHERE id=ANY(ancestors);
    IF tree_before IS DISTINCT FROM tree_after THEN
        RAISE EXCEPTION '0171 requester organization changed' USING ERRCODE='40001';
    END IF;
    checked_now:=clock_timestamp();
    IF EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles role ON role.id=a.role_id
        JOIN public.role_permissions rp ON rp.role_id=role.id JOIN public.permissions p ON p.id=rp.permission_id
        WHERE a.user_id=actor_id AND a.status IN ('scheduled','active') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND role.status='active' AND p.resource='material_request' AND p.action IN ('read','cancel')
          AND p.field_code='' AND rp.effect='deny' AND ((a.scope_type='national' AND a.scope_id='*')
            OR (a.scope_type='person' AND lower(replace(a.scope_id,'-',''))=replace(checked_person_id::text,'-',''))
            OR (a.scope_type='organization' AND lower(replace(a.scope_id,'-','')) IN
              (SELECT replace(v::text,'-','') FROM unnest(ancestors) v)))) THEN
        RAISE EXCEPTION '0171 explicit requester deny' USING ERRCODE='42501';
    END IF;
    SELECT count(*) INTO eligible FROM public.role_assignments a JOIN public.roles role ON role.id=a.role_id
        WHERE a.user_id=actor_id AND a.status IN ('scheduled','active') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND role.status='active' AND NOT role.is_external AND role.code='technician'
          AND a.scope_type='person' AND lower(replace(a.scope_id,'-',''))=replace(checked_person_id::text,'-','')
          AND EXISTS(SELECT 1 FROM public.role_permissions rp JOIN public.permissions p ON p.id=rp.permission_id
            WHERE rp.role_id=role.id AND rp.effect='allow' AND p.resource='material_request' AND p.action='cancel' AND p.field_code='');
    IF eligible<>1 OR NOT EXISTS(SELECT 1 FROM public.role_assignments a JOIN public.roles role ON role.id=a.role_id
        WHERE a.id=assignment_id AND a.user_id=actor_id AND a.status IN ('scheduled','active') AND a.revoked_at IS NULL
          AND a.valid_from<=checked_now AND (a.valid_to IS NULL OR a.valid_to>checked_now)
          AND role.status='active' AND NOT role.is_external AND role.code='technician'
          AND a.scope_type='person' AND lower(replace(a.scope_id,'-',''))=replace(checked_person_id::text,'-','')
          AND EXISTS(SELECT 1 FROM public.role_permissions rp JOIN public.permissions p ON p.id=rp.permission_id
            WHERE rp.role_id=role.id AND rp.effect='allow' AND p.resource='material_request' AND p.action='cancel' AND p.field_code='')
          AND EXISTS(SELECT 1 FROM public.role_permissions rp JOIN public.permissions p ON p.id=rp.permission_id
            WHERE rp.role_id=role.id AND rp.effect='allow' AND p.resource='material_request' AND p.action='read' AND p.field_code='')) THEN
        RAISE EXCEPTION '0171 unique current requester grant required' USING ERRCODE='42501';
    END IF;
END;
"""

INSERT = """
DECLARE r public.material_requests%ROWTYPE; proof jsonb; input jsonb; expected jsonb; supplied_lines jsonb; expected_lines jsonb; payload jsonb;
BEGIN
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    PERFORM public.rsc_assert_remaining_cancel_authority_0171(NEW.request_id,NEW.actor_user_id,NEW.actor_person_id,NEW.actor_role_assignment_id,NEW.authorization_version);
    IF NEW.request_version<>r.version OR NEW.created_at<>NEW.occurred_at OR NEW.occurred_at<>CURRENT_TIMESTAMP
      OR NEW.occurred_at<r.updated_at OR NEW.reason<>btrim(NEW.reason) OR NEW.reason='' OR NEW.reason ~ '[[:cntrl:]]'
      OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
      OR NEW.idempotency_key_hash !~ '^[0-9a-f]{64}$'
      OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$' THEN
        RAISE EXCEPTION '0171 cancellation identity version or timestamp mismatch' USING ERRCODE='23514';
    END IF;
    proof:=public.rsc_remaining_cancel_proof_0171(NEW.request_id);
    input:=NEW.evidence_jsonb->'input';
    IF input IS NULL OR jsonb_typeof(input)<>'object' OR NOT (input ?& ARRAY['expected_request_version','reason','lines'])
      OR input-'expected_request_version'-'reason'-'lines'<>'{}'
      OR input->'expected_request_version' IS DISTINCT FROM to_jsonb(NEW.request_version) OR input->>'reason' IS DISTINCT FROM NEW.reason
      OR jsonb_typeof(input->'lines')<>'array' OR jsonb_array_length(input->'lines') NOT BETWEEN 1 AND 200
      OR proof->'before'->>'revision_id'<>NEW.revision_id::text THEN
        RAISE EXCEPTION '0171 cancellation input mismatch' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_agg(item ORDER BY item->>'request_line_id') INTO supplied_lines FROM jsonb_array_elements(input->'lines') item;
    SELECT jsonb_agg(jsonb_build_object('request_line_id',item->>'request_line_id','cancelled_qty',item->>'unreserved_qty')
      ORDER BY item->>'request_line_id') INTO expected_lines FROM jsonb_array_elements(proof->'before'->'lines') item
      WHERE (item->>'unreserved_qty')::numeric>0;
    IF supplied_lines IS DISTINCT FROM expected_lines THEN
        RAISE EXCEPTION '0171 exact remaining cancellation lines required' USING ERRCODE='23514';
    END IF;
    expected:=proof||jsonb_build_object('schema','rsc.material_request_remaining_cancellation.v1','input',input);
    payload:=input||jsonb_build_object('request_id',NEW.request_id::text,'actor_user_id',NEW.actor_user_id,'actor_person_id',NEW.actor_person_id::text);
    IF NEW.evidence_jsonb IS DISTINCT FROM expected
      OR NEW.evidence_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
      OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(payload),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0171 cancellation evidence or input hash mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""

LINE = """
DECLARE c public.material_request_remaining_cancellations%ROWTYPE;
BEGIN
    SELECT * INTO STRICT c FROM public.material_request_remaining_cancellations WHERE id=NEW.cancellation_id;
    PERFORM 1 FROM public.material_requests WHERE id=c.request_id FOR UPDATE;
    IF NEW.request_id<>c.request_id OR NEW.revision_id<>c.revision_id OR NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(c.evidence_jsonb->'input'->'lines') item
        WHERE item->>'request_line_id'=NEW.request_line_id::text AND (item->>'cancelled_qty')::numeric=NEW.cancelled_qty) THEN
        RAISE EXCEPTION '0171 cancellation line binding mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
"""

AUDIT = """
DECLARE c public.material_request_remaining_cancellations%ROWTYPE; expected jsonb; matches bigint; total bigint; actual_lines jsonb; expected_lines jsonb;
BEGIN
    SELECT * INTO STRICT c FROM public.material_request_remaining_cancellations WHERE id=checked_cancellation_id;
    PERFORM 1 FROM public.material_requests WHERE id=c.request_id FOR UPDATE;
    SELECT jsonb_agg(jsonb_build_object('request_line_id',request_line_id::text,'cancelled_qty',cancelled_qty::text)
        ORDER BY request_line_id) INTO actual_lines FROM public.material_request_remaining_cancellation_lines WHERE cancellation_id=c.id;
    SELECT jsonb_agg(item ORDER BY item->>'request_line_id') INTO expected_lines FROM jsonb_array_elements(c.evidence_jsonb->'input'->'lines') item;
    IF actual_lines IS DISTINCT FROM expected_lines THEN
        RAISE EXCEPTION '0171 complete cancellation line facts required' USING ERRCODE='23514';
    END IF;
    expected:=jsonb_build_object('id',c.id::text,'request_id',c.request_id::text,'revision_id',c.revision_id::text,
        'request_version',c.request_version,'actor_person_id',c.actor_person_id::text,'actor_role_assignment_id',c.actor_role_assignment_id::text,
        'authorization_version',c.authorization_version,'idempotency_key_hash',c.idempotency_key_hash,'request_hash',c.request_hash,
        'evidence_sha256',c.evidence_sha256,'reason_sha256',encode(sha256(convert_to(c.reason,'UTF8')),'hex'));
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.cancel_remaining'
        AND actor_user_id=c.actor_user_id AND request_id=c.trace_request_id AND occurred_at=c.occurred_at AND created_at=c.created_at
        AND before_jsonb=jsonb_build_object('remaining_cancellation','not_recorded') AND after_jsonb=expected)
      INTO total,matches FROM public.audit_events WHERE aggregate_type='material_request_remaining_cancellation' AND aggregate_id=c.id::text;
    IF total<>1 OR matches<>1 THEN
        RAISE EXCEPTION '0171 one exact cancellation audit required' USING ERRCODE='23514';
    END IF;
END;
"""
DISPATCH = """
BEGIN
    IF TG_TABLE_NAME='material_request_remaining_cancellations' THEN
        PERFORM public.rsc_validate_remaining_cancel_0171(NEW.id);
    ELSIF TG_TABLE_NAME='material_request_remaining_cancellation_lines' THEN
        PERFORM public.rsc_validate_remaining_cancel_0171(NEW.cancellation_id);
    ELSE
        IF NEW.action<>'material_request.cancel_remaining' OR NEW.aggregate_type<>'material_request_remaining_cancellation' THEN
            RAISE EXCEPTION '0171 cancellation audit type mismatch' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_validate_remaining_cancel_0171(NEW.aggregate_id::uuid);
    END IF;
    RETURN NULL;
END;
"""
TERMINAL_BARRIER = replace_once(BARRIER['BARRIER_BODY'],
    '        PERFORM public.rsc_require_request_open_0169(parent_id);',
    """        PERFORM public.rsc_require_request_open_0169(parent_id);
        IF EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=parent_id) THEN
            RAISE EXCEPTION '0171 remaining demand cancelled' USING ERRCODE='55000';
        END IF;""")
CLOSURE_EVIDENCE = replace_once(CLOSURE['EVIDENCE_BODY'],
    '    PERFORM public.rsc_validate_material_request_approval_projection_0045(checked_request_id);',
    '''    PERFORM public.rsc_validate_material_request_approval_projection_0045(checked_request_id);
    PERFORM public.rsc_validate_remaining_cancel_0171(c.id)
      FROM public.material_request_remaining_cancellations c WHERE c.request_id=checked_request_id;''')
if CLOSURE_EVIDENCE.count('l.cancelled_qty') != 2:
    raise ValueError('0171 closure quantity predecessor changed')
CLOSURE_EVIDENCE = CLOSURE_EVIDENCE.replace('l.cancelled_qty',
    '(l.cancelled_qty+coalesce((SELECT c.cancelled_qty FROM public.material_request_remaining_cancellation_lines c '
    'WHERE c.request_line_id=l.id),0))')
FUNCTIONS = {
    'rsc_remaining_cancel_proof_0171(uuid)': ('checked_request_id uuid', 'jsonb', PROOF),
    'rsc_assert_remaining_cancel_authority_0171(uuid,text,uuid,uuid,bigint)': (
        'checked_request_id uuid, actor_id text, checked_person_id uuid, assignment_id uuid, auth_version bigint', 'void', AUTHORITY),
    'rsc_guard_remaining_cancel_insert_0171()': ('', 'trigger', INSERT),
    'rsc_guard_remaining_cancel_line_0171()': ('', 'trigger', LINE),
    'rsc_validate_remaining_cancel_0171(uuid)': ('checked_cancellation_id uuid', 'void', AUDIT),
    'rsc_dispatch_remaining_cancel_0171()': ('', 'trigger', DISPATCH),
}


def install(db):
    from app.material_request_remaining_cancel_schema import cancellations, cancellation_lines
    identity=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2]!=('star_oam_migrator','star_oam_migrator') or identity[2]//10000!=16 or identity[3]!='read committed':
        raise ValueError('0171 direct PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()!=['20261219_0170']:
        raise ValueError('0171 exact predecessor required')
    if db.scalar(text("SELECT prosrc FROM pg_proc WHERE oid='public.rsc_guard_request_open_0169()'::regprocedure")) != BARRIER['BARRIER_BODY']:
        raise ValueError('0171 predecessor barrier drift')
    for table in (cancellations,cancellation_lines):
        table.create(db)
        db.execute(text(f'REVOKE ALL ON public.{table.name} FROM PUBLIC, star_oam_api'))
        db.execute(text(f'GRANT SELECT, INSERT ON public.{table.name} TO star_oam_api'))
    for signature,(arguments,result,body) in FUNCTIONS.items():
        db.execute(text(f'CREATE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, star_oam_api'))
    db.execute(text('CREATE OR REPLACE FUNCTION public.rsc_guard_request_open_0169() RETURNS trigger '
        'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+TERMINAL_BARRIER+'$body$'))
    if db.scalar(text("SELECT prosrc FROM pg_proc WHERE oid='public.rsc_closure_evidence_0169(uuid)'::regprocedure")) != CLOSURE['EVIDENCE_BODY']:
        raise ValueError('0171 predecessor closure evidence drift')
    db.execute(text('CREATE OR REPLACE FUNCTION public.rsc_closure_evidence_0169(checked_request_id uuid) RETURNS jsonb '
        'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+CLOSURE_EVIDENCE+'$body$'))
    for table,guard in ((cancellations,'insert'),(cancellation_lines,'line')):
        db.execute(text(f'CREATE TRIGGER rsc_remaining_cancel_insert_0171 BEFORE INSERT ON public.{table.name} '
            f'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_remaining_cancel_{guard}_0171()'))
        for operations,level,suffix in (('UPDATE OR DELETE','ROW','immutable'),('TRUNCATE','STATEMENT','truncate')):
            db.execute(text(f'CREATE TRIGGER rsc_remaining_cancel_{suffix}_0171 BEFORE {operations} ON public.{table.name} '
                f'FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    for table in (cancellations.name,cancellation_lines.name,'audit_events'):
        when="WHEN (NEW.action='material_request.cancel_remaining' OR NEW.aggregate_type='material_request_remaining_cancellation') " if table=='audit_events' else ''
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_remaining_cancel_audit_0171 AFTER INSERT ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '+when+'EXECUTE FUNCTION public.rsc_dispatch_remaining_cancel_0171()'))
        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_remaining_cancel_audit_0171'))
