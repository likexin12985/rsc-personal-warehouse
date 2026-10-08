"""Forward PG16 boundary compiler for isolated native verification only.

Not an activated revision. Never execute against external/production databases.
Formal activation must freeze the observed catalog and include closure rules.
"""
from pathlib import Path
import runpy
from sqlalchemy import text

ORIGIN = """
DECLARE i public.material_request_rejection_inbounds%ROWTYPE;
    p public.material_request_rejection_returns%ROWTYPE; o public.outbound_postings%ROWTYPE;
    r public.material_requests%ROWTYPE; l public.material_request_lines%ROWTYPE;
BEGIN
    SELECT * INTO STRICT i FROM public.material_request_rejection_inbounds WHERE id=checked_inbound_id;
    PERFORM public.rsc_validate_rejection_inbound_0175(i.id);
    SELECT * INTO STRICT p FROM public.material_request_rejection_returns WHERE id=i.return_id;
    SELECT * INTO STRICT o FROM public.outbound_postings WHERE id=p.outbound_posting_id;
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=i.request_id;
    SELECT * INTO STRICT l FROM public.material_request_lines WHERE id=o.request_line_id;
    PERFORM public.rsc_validate_material_request_approval_projection_0045(r.id);
    IF i.request_id<>checked_request_id OR p.request_id<>r.id OR o.request_id<>r.id OR l.request_id<>r.id
      OR l.revision_id<>p.revision_id OR o.revision_id<>p.revision_id
      OR NOT EXISTS(SELECT 1 FROM public.material_request_revisions v WHERE v.id=p.revision_id
          AND v.request_id=r.id AND v.revision_no=r.revision_no AND v.status='sealed')
      OR i.accepted_qty<=0 OR i.accepted_qty>l.final_approved_qty
      OR r.status NOT IN ('approved','partially_approved') THEN
        RAISE EXCEPTION '0176 exact approved returned source required' USING ERRCODE='23514'; END IF;
    RETURN jsonb_build_object('request_id',r.id::text,'revision_id',l.revision_id::text,'request_line_id',l.id::text,
        'return_id',p.id::text,'original_receipt_line_id',p.receipt_line_id::text,
        'warehouse_receipt_id',i.receipt_id::text,'inbound_id',i.id::text,'posting_transaction_id',i.posting_transaction_id::text,
        'inbound_request_hash',i.request_hash,'inbound_plan_hash',i.plan_hash,'accepted_qty',i.accepted_qty::numeric(18,3)::text);
END;
"""

INSERT = """
DECLARE r public.material_requests%ROWTYPE; i public.material_request_rejection_inbounds%ROWTYPE;
    origin jsonb; input jsonb; expected jsonb; payload jsonb;
BEGIN
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    PERFORM public.rsc_assert_remaining_cancel_authority_0171(NEW.request_id,NEW.actor_user_id,
        NEW.actor_person_id,NEW.actor_role_assignment_id,NEW.authorization_version);
    SELECT * INTO STRICT i FROM public.material_request_rejection_inbounds WHERE id=NEW.inbound_id;
    origin:=public.rsc_return_compensation_origin_0176(NEW.request_id,NEW.inbound_id);
    IF NEW.request_version<>r.version OR NEW.request_version<i.request_version
      OR NEW.recorded_at<>CURRENT_TIMESTAMP OR NEW.recorded_at<i.recorded_at OR NEW.recorded_at<r.updated_at
      OR NEW.reason<>btrim(NEW.reason) OR NEW.reason='' OR NEW.reason ~ '[[:cntrl:]]'
      OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
      OR NEW.idempotency_key_hash !~ '^[0-9a-f]{64}$'
      OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$'
      OR NEW.cancelled_qty<>i.accepted_qty OR NEW.revision_id::text<>origin->>'revision_id'
      OR NEW.request_line_id::text<>origin->>'request_line_id' THEN
        RAISE EXCEPTION '0176 compensation identity quantity or timestamp mismatch' USING ERRCODE='23514'; END IF;
    input:=jsonb_build_object('expected_request_version',NEW.request_version,'reason',NEW.reason,
        'inbound_id',i.id::text,'inbound_request_hash',i.request_hash,'inbound_plan_hash',i.plan_hash,
        'cancelled_qty',i.accepted_qty::numeric(18,3)::text);
    expected:=jsonb_build_object('schema','rsc.material_request_return_compensation.v1','input',input,'origin',origin);
    payload:=jsonb_build_object('request_id',r.id::text,'actor_user_id',NEW.actor_user_id,
        'actor_person_id',NEW.actor_person_id::text,'input',input);
    IF NEW.evidence_jsonb IS DISTINCT FROM expected
      OR NEW.evidence_sha256 IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
      OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(payload),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0176 exact compensation evidence required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

VALIDATE = """
DECLARE c public.material_request_return_compensations%ROWTYPE; expected jsonb; total bigint; matches bigint;
BEGIN
    SELECT * INTO STRICT c FROM public.material_request_return_compensations WHERE id=checked_id;
    IF c.evidence_jsonb->'origin' IS DISTINCT FROM public.rsc_return_compensation_origin_0176(c.request_id,c.inbound_id) THEN
        RAISE EXCEPTION '0176 historical compensation source mismatch' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('id',c.id::text,'inbound_id',c.inbound_id::text,'request_id',c.request_id::text,
        'revision_id',c.revision_id::text,'request_line_id',c.request_line_id::text,'request_version',c.request_version,
        'actor_person_id',c.actor_person_id::text,'actor_role_assignment_id',c.actor_role_assignment_id::text,
        'authorization_version',c.authorization_version,'idempotency_key_hash',c.idempotency_key_hash,
        'request_hash',c.request_hash,'evidence_sha256',c.evidence_sha256,'cancelled_qty',c.cancelled_qty::numeric(18,3)::text,
        'reason_sha256',encode(sha256(convert_to(c.reason,'UTF8')),'hex'));
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.cancel_returned'
        AND actor_user_id=c.actor_user_id AND request_id=c.trace_request_id
        AND occurred_at=c.recorded_at AND created_at=c.recorded_at
        AND before_jsonb=jsonb_build_object('return_compensation','not_recorded') AND after_jsonb=expected)
      INTO total,matches FROM public.audit_events WHERE aggregate_type='material_request_return_compensation' AND aggregate_id=c.id::text;
    IF total<>1 OR matches<>1 THEN
        RAISE EXCEPTION '0176 exact compensation audit required' USING ERRCODE='23514'; END IF;
END;
"""

DISPATCH = """
BEGIN
    IF TG_TABLE_NAME='material_request_return_compensations' THEN
        PERFORM public.rsc_validate_return_compensation_0176(NEW.id);
    ELSE
        IF NEW.action<>'material_request.cancel_returned' OR NEW.aggregate_type<>'material_request_return_compensation' THEN
            RAISE EXCEPTION '0176 compensation audit type mismatch' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_validate_return_compensation_0176(NEW.aggregate_id::uuid);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    'rsc_return_compensation_origin_0176(uuid,uuid)': ('checked_request_id uuid, checked_inbound_id uuid', 'jsonb', ORIGIN),
    'rsc_guard_return_compensation_insert_0176()': ('', 'trigger', INSERT),
    'rsc_validate_return_compensation_0176(uuid)': ('checked_id uuid', 'void', VALIDATE),
    'rsc_dispatch_return_compensation_0176()': ('', 'trigger', DISPATCH),
}


def install(db):
    from app.material_request_return_compensation_schema import compensations
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 or identity[3] != 'read committed':
        raise ValueError('0176 direct native PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != ['20261224_0175']:
        raise ValueError('0176 exact formal predecessor required')
    predecessor = runpy.run_path(str(Path(__file__).parents[1] / 'rejection_inbound_0175/transition.py'))
    predecessor['verify'](db, 'after')
    path = db.scalar(text("SELECT current_setting('search_path')"))
    db.execute(text("SELECT set_config('search_path','public',true)"))
    compensations.create(db)
    db.execute(text('REVOKE ALL ON public.material_request_return_compensations FROM PUBLIC,star_oam_api'))
    db.execute(text('GRANT SELECT,INSERT ON public.material_request_return_compensations TO star_oam_api'))
    for operations, level, suffix in (('UPDATE OR DELETE', 'ROW', 'immutable'), ('TRUNCATE', 'STATEMENT', 'truncate')):
        db.execute(text(f'CREATE TRIGGER rsc_return_compensation_{suffix}_0176 BEFORE {operations} ON public.material_request_return_compensations '
            f'FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    for signature, (arguments, result, body) in FUNCTIONS.items():
        db.execute(text(f'CREATE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$' + body + '$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api'))
    db.execute(text('CREATE TRIGGER rsc_return_compensation_insert_0176 BEFORE INSERT ON public.material_request_return_compensations '
        'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_return_compensation_insert_0176()'))
    for table, condition in (('material_request_return_compensations', ''), ('audit_events',
            "WHEN (NEW.aggregate_type='material_request_return_compensation' OR NEW.action='material_request.cancel_returned')")):
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_return_compensation_complete_0176 AFTER INSERT ON public.{table} '
            f'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW {condition} EXECUTE FUNCTION public.rsc_dispatch_return_compensation_0176()'))
        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_return_compensation_complete_0176'))
    runpy.run_path(str(Path(__file__).with_name('closure.py')))['install'](db)
    runpy.run_path(str(Path(__file__).with_name('remaining.py')))['install'](db)
    db.execute(text("SELECT set_config('search_path',:path,true)"), {'path': path})
