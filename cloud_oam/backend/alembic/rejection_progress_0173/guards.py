"""Forward progress guard compiler for an owned PG16 development cluster.

No Alembic activation yet. Freeze observed catalog, active-registration claims
and readiness together before installing this revision in production.
"""
import json
from pathlib import Path

from sqlalchemy import text

PREDECESSOR = json.loads((Path(__file__).parents[1] / 'rejection_return_0172/catalog.json').read_text())
AUTHORITY = PREDECESSOR['functions']['rsc_assert_rejection_return_authority_0172(uuid, text, uuid, uuid, bigint)']['after']['prosrc']
assert AUTHORITY.count("p.action='submit_return'") == 3
AUTHORITY = AUTHORITY.replace("p.action='submit_return'", 'p.action=checked_action').replace('0172 rejection return', '0173 rejection progress')
AUTHORITY = AUTHORITY.replace('BEGIN\n', "BEGIN\n    IF checked_action NOT IN ('cancel_return','outbound_return','ship_return') THEN\n"
    "        RAISE EXCEPTION '0173 invalid progress permission' USING ERRCODE='42501'; END IF;\n", 1)

INSERT = """
DECLARE parent public.material_request_rejection_returns%ROWTYPE;
    r public.material_requests%ROWTYPE; prior public.material_request_rejection_progress%ROWTYPE;
    action_permission text; n bigint; input jsonb; expected jsonb; physical_json jsonb;
BEGIN
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=NEW.return_id;
    action_permission:=CASE NEW.action WHEN 'cancel_registration' THEN 'cancel_return'
        WHEN 'depart' THEN 'outbound_return' WHEN 'handover' THEN 'ship_return' END;
    IF action_permission IS NULL THEN RAISE EXCEPTION '0173 invalid action' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_rejection_progress_authority_0173(NEW.request_id,NEW.actor_user_id,
        NEW.actor_person_id,NEW.actor_role_assignment_id,NEW.authorization_version,action_permission);
    PERFORM public.rsc_validate_rejection_return_0172(parent.id);
    IF parent.request_id<>r.id OR parent.actor_user_id<>NEW.actor_user_id OR parent.actor_person_id<>NEW.actor_person_id
       OR NEW.registration_request_hash<>parent.request_hash OR NEW.request_version<>r.version
       OR NEW.request_version<parent.request_version OR r.status NOT IN ('approved','partially_approved')
       OR EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=r.id)
       OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
       OR NEW.recorded_at<>CURRENT_TIMESTAMP OR NEW.recorded_at<parent.occurred_at
       OR NEW.reason<>btrim(NEW.reason) OR NEW.reason ~ '[[:cntrl:]]'
       OR NEW.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$' THEN
        RAISE EXCEPTION '0173 progress origin state or coordinate mismatch' USING ERRCODE='23514'; END IF;
    SELECT count(*) INTO n FROM public.material_request_rejection_progress WHERE return_id=parent.id;
    IF NEW.action='handover' THEN
        IF n<>1 THEN RAISE EXCEPTION '0173 one departure required' USING ERRCODE='23514'; END IF;
        SELECT * INTO STRICT prior FROM public.material_request_rejection_progress WHERE return_id=parent.id;
        IF prior.action<>'depart' OR NEW.previous_event_id IS DISTINCT FROM prior.id
           OR NEW.previous_request_hash IS DISTINCT FROM prior.request_hash
           OR NEW.physical_at<prior.physical_at OR NEW.recorded_at<prior.recorded_at THEN
            RAISE EXCEPTION '0173 exact departure required' USING ERRCODE='23514'; END IF;
        IF NEW.carrier<>btrim(NEW.carrier) OR NEW.tracking_no<>btrim(NEW.tracking_no)
           OR NEW.carrier ~ '[[:cntrl:]]' OR NEW.tracking_no ~ '[[:cntrl:]]' THEN
            RAISE EXCEPTION '0173 carrier evidence malformed' USING ERRCODE='23514'; END IF;
    ELSIF n<>0 THEN
        RAISE EXCEPTION '0173 return already progressed' USING ERRCODE='23514';
    END IF;
    IF NEW.physical_at IS NOT NULL AND (NEW.physical_at<parent.occurred_at OR NEW.physical_at>NEW.recorded_at) THEN
        RAISE EXCEPTION '0173 physical time out of range' USING ERRCODE='23514'; END IF;
    physical_json:=CASE WHEN NEW.physical_at IS NULL THEN 'null'::jsonb ELSE to_jsonb(
        to_char(NEW.physical_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
        CASE WHEN mod(extract(microseconds FROM NEW.physical_at)::bigint,1000000)=0 THEN ''
            ELSE '.'||to_char(NEW.physical_at AT TIME ZONE 'UTC','US') END || 'Z') END;
    input:=jsonb_build_object('expected_request_version',NEW.request_version,'reason',NEW.reason,'action',NEW.action,
        'registration_request_hash',parent.request_hash,'previous_event_id',NEW.previous_event_id::text,
        'previous_request_hash',NEW.previous_request_hash,'physical_at',physical_json,'carrier',NEW.carrier,'tracking_no',NEW.tracking_no);
    expected:=jsonb_build_object('schema','rsc.material_request_rejection_progress.v1','input',input,
        'origin',jsonb_build_object('return_id',parent.id::text,'registration_request_hash',parent.request_hash,
          'registration_evidence_sha256',parent.evidence_sha256,'request_id',r.id::text,'receipt_id',parent.receipt_id::text,
          'receipt_line_id',parent.receipt_line_id::text,'quantity',parent.quantity::text,
          'serial_ids',parent.evidence_jsonb->'input'->'serial_ids'));
    IF NEW.evidence_jsonb IS DISTINCT FROM expected OR NEW.evidence_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
       OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
         jsonb_build_object('request_id',r.id::text,'return_id',parent.id::text,'actor_user_id',NEW.actor_user_id,
         'actor_person_id',NEW.actor_person_id::text,'input',input)),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0173 exact progress evidence required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

VALIDATE = """
DECLARE fact public.material_request_rejection_progress%ROWTYPE; expected jsonb; total bigint; matches bigint;
BEGIN
    SELECT * INTO STRICT fact FROM public.material_request_rejection_progress WHERE id=checked_id;
    expected:=jsonb_build_object('id',fact.id::text,'return_id',fact.return_id::text,'request_id',fact.request_id::text,
        'actor_person_id',fact.actor_person_id::text,'actor_role_assignment_id',fact.actor_role_assignment_id::text,
        'action',fact.action,'request_version',fact.request_version,'authorization_version',fact.authorization_version,
        'registration_request_hash',fact.registration_request_hash,'idempotency_key_hash',fact.idempotency_key_hash,
        'request_hash',fact.request_hash,'evidence_sha256',fact.evidence_sha256);
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request'
      AND action='material_request.rejection_return.'||fact.action
      AND actor_user_id=fact.actor_user_id AND request_id=fact.trace_request_id
      AND occurred_at=fact.recorded_at AND created_at=fact.recorded_at
      AND before_jsonb=jsonb_build_object('rejection_return',CASE WHEN fact.action='handover' THEN 'departed' ELSE 'registered' END)
      AND after_jsonb=expected) INTO total,matches FROM public.audit_events
      WHERE aggregate_type='material_request_rejection_progress' AND aggregate_id=fact.id::text;
    IF total<>1 OR matches<>1 THEN RAISE EXCEPTION '0173 exact progress audit required' USING ERRCODE='23514'; END IF;
END;
"""

DISPATCH = """
BEGIN
    IF TG_TABLE_NAME='material_request_rejection_progress' THEN
        PERFORM public.rsc_validate_rejection_progress_0173(NEW.id);
    ELSE
        IF NEW.aggregate_type<>'material_request_rejection_progress'
           OR NEW.action NOT IN ('material_request.rejection_return.cancel_registration',
               'material_request.rejection_return.depart','material_request.rejection_return.handover') THEN
            RAISE EXCEPTION '0173 progress audit type mismatch' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_validate_rejection_progress_0173(NEW.aggregate_id::uuid);
    END IF;
    RETURN NULL;
END;
"""

FUNCTIONS = {
    'rsc_assert_rejection_progress_authority_0173(uuid,text,uuid,uuid,bigint,text)': (
        'checked_request_id uuid, actor_id text, checked_person_id uuid, assignment_id uuid, auth_version bigint, checked_action text', 'void', AUTHORITY),
    'rsc_guard_rejection_progress_insert_0173()': ('', 'trigger', INSERT),
    'rsc_validate_rejection_progress_0173(uuid)': ('checked_id uuid', 'void', VALIDATE),
    'rsc_dispatch_rejection_progress_0173()': ('', 'trigger', DISPATCH),
}

# Replace only the reviewed frozen 0172 bodies; historical migrations stay
# unchanged. Active claims are serialized by the same parent request lock.
REGISTRATION_INSERT = PREDECESSOR['functions']['rsc_guard_rejection_return_insert_0172()']['after']['prosrc']
old_budget = 'FROM public.material_request_rejection_returns WHERE receipt_line_id=line.id'
assert REGISTRATION_INSERT.count(old_budget) == 1
REGISTRATION_INSERT = REGISTRATION_INSERT.replace(old_budget,
    'FROM public.material_request_rejection_returns claim WHERE receipt_line_id=line.id '
    "AND NOT EXISTS(SELECT 1 FROM public.material_request_rejection_progress p WHERE p.return_id=claim.id AND p.action='cancel_registration')")
REGISTRATION_SERIAL = PREDECESSOR['functions']['rsc_guard_rejection_return_serial_0172()']['after']['prosrc']
assert REGISTRATION_SERIAL.count('    RETURN NEW;') == 1
REGISTRATION_SERIAL = REGISTRATION_SERIAL.replace('    RETURN NEW;', """
    IF EXISTS(SELECT 1 FROM public.material_request_rejection_progress WHERE return_id=parent.id AND action='cancel_registration')
       OR EXISTS(SELECT 1 FROM public.material_request_rejection_return_serials claim
         WHERE claim.receipt_line_id=NEW.receipt_line_id AND claim.serial_id=NEW.serial_id AND claim.return_id<>parent.id
           AND NOT EXISTS(SELECT 1 FROM public.material_request_rejection_progress p
             WHERE p.return_id=claim.return_id AND p.action='cancel_registration')) THEN
        RAISE EXCEPTION '0173 rejected serial already actively claimed' USING ERRCODE='23514'; END IF;
    RETURN NEW;""")
REPLACEMENTS = {
    'rsc_guard_rejection_return_insert_0172()': ('', 'trigger', REGISTRATION_INSERT),
    'rsc_guard_rejection_return_serial_0172()': ('', 'trigger', REGISTRATION_SERIAL),
}


def install(db):
    from app.material_request_rejection_progress_schema import progress
    from app import material_request_rejection_return_security as predecessor
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 or identity[3] != 'read committed':
        raise ValueError('0173 direct native PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != ['20261221_0172']:
        raise ValueError('0173 exact predecessor required')
    predecessor.verify(db)
    path = db.scalar(text("SELECT current_setting('search_path')"))
    db.execute(text("SELECT set_config('search_path','public',true)"))
    progress.create(db)
    db.execute(text('REVOKE ALL ON public.material_request_rejection_progress FROM PUBLIC,star_oam_api'))
    db.execute(text('GRANT SELECT,INSERT ON public.material_request_rejection_progress TO star_oam_api'))
    for operations, level, suffix in (('UPDATE OR DELETE', 'ROW', 'immutable'), ('TRUNCATE', 'STATEMENT', 'truncate')):
        db.execute(text(f'CREATE TRIGGER rsc_rejection_progress_{suffix}_0173 BEFORE {operations} '
            f'ON public.material_request_rejection_progress FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    db.execute(text('ALTER TABLE public.material_request_rejection_return_serials DROP CONSTRAINT uq_rejection_return_serial_origin'))
    for signature, (arguments, result, body) in (FUNCTIONS | REPLACEMENTS).items():
        db.execute(text(f'CREATE OR REPLACE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$' + body + '$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api'))
    db.execute(text('CREATE TRIGGER rsc_rejection_progress_insert_0173 BEFORE INSERT ON public.material_request_rejection_progress '
        'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_rejection_progress_insert_0173()'))
    for table in ('material_request_rejection_progress', 'audit_events'):
        when = ("WHEN (NEW.aggregate_type='material_request_rejection_progress' OR NEW.action IN "
            "('material_request.rejection_return.cancel_registration','material_request.rejection_return.depart',"
            "'material_request.rejection_return.handover')) ") if table == 'audit_events' else ''
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_rejection_progress_complete_0173 AFTER INSERT ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW ' + when + 'EXECUTE FUNCTION public.rsc_dispatch_rejection_progress_0173()'))
        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_rejection_progress_complete_0173'))
    db.execute(text("SELECT set_config('search_path',:path,true)"), {'path': path})
