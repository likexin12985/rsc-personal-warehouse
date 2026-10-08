"""Unactivated 0172 registration boundary for an owned native PG16 test only.

Formal Alembic activation must freeze the observed catalog and readiness delta.
This does not provide physical dispatch, return receipt or inventory settlement.
"""
from pathlib import Path
import runpy
from sqlalchemy import text

BASE=runpy.run_path(str(Path(__file__).parents[1]/'remaining_cancel_0171/candidate.py'))['AUTHORITY']
AUTHORITY=BASE.replace('0171','0172 rejection return')
old="p.resource='material_request' AND p.action IN ('read','cancel')"
assert AUTHORITY.count(old)==1
AUTHORITY=AUTHORITY.replace(old,"((p.resource='material_request' AND p.action='read') OR (p.resource='stock_operation' AND p.action='submit_return'))")
old="p.resource='material_request' AND p.action='cancel'"
assert AUTHORITY.count(old)==2
AUTHORITY=AUTHORITY.replace(old,"p.resource='stock_operation' AND p.action='submit_return'")

INSERT="""
DECLARE r public.material_requests%ROWTYPE; receipt public.receipts%ROWTYPE;
    line public.receipt_lines%ROWTYPE; ship public.shipment_lines%ROWTYPE;
    outbound public.outbound_postings%ROWTYPE; reservation public.stock_reservations%ROWTYPE;
    source public.stock_accounts%ROWTYPE; transit public.stock_accounts%ROWTYPE;
    policy public.material_inventory_policies%ROWTYPE; expected_origin jsonb; input jsonb; expected jsonb;
    selected_serials jsonb; original_serials jsonb; tracked boolean; origin_count bigint;
BEGIN
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    PERFORM public.rsc_assert_rejection_return_authority_0172(NEW.request_id,NEW.actor_user_id,
        NEW.actor_person_id,NEW.actor_role_assignment_id,NEW.authorization_version);
    IF r.status NOT IN ('approved','partially_approved') OR NEW.request_version<>r.version
       OR EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=r.id)
       OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
       OR NEW.occurred_at<>CURRENT_TIMESTAMP OR NEW.created_at<>NEW.occurred_at
       OR NEW.return_no !~ '^RJR-[A-F0-9]{32}$' OR NEW.reason<>btrim(NEW.reason) OR NEW.reason ~ '[[:cntrl:]]'
       OR NEW.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$' THEN
        RAISE EXCEPTION '0172 return request state or coordinate mismatch' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT receipt FROM public.receipts WHERE id=NEW.receipt_id;
    SELECT * INTO STRICT line FROM public.receipt_lines WHERE id=NEW.receipt_line_id FOR UPDATE;
    SELECT * INTO STRICT ship FROM public.shipment_lines WHERE id=line.shipment_line_id;
    SELECT * INTO STRICT outbound FROM public.outbound_postings WHERE id=ship.outbound_posting_id;
    SELECT * INTO STRICT reservation FROM public.stock_reservations WHERE id=outbound.reservation_id;
    SELECT * INTO STRICT source FROM public.stock_accounts WHERE id=reservation.source_stock_account_id;
    SELECT * INTO STRICT transit FROM public.stock_accounts WHERE id=outbound.target_stock_account_id;
    IF receipt.receiver_person_id<>NEW.actor_person_id OR receipt.id<>line.receipt_id
       OR receipt.shipment_id<>ship.shipment_id OR receipt.request_hash<>NEW.receipt_request_hash
       OR line.condition='normal' OR line.rejected_qty<=0 OR NEW.quantity>line.rejected_qty
       OR outbound.request_id<>r.id OR NEW.revision_id<>outbound.revision_id
       OR NEW.shipment_line_id<>ship.id OR NEW.outbound_posting_id<>outbound.id
       OR NEW.reservation_id<>reservation.id OR NEW.in_transit_account_id<>transit.id
       OR NEW.return_source_account_id<>source.id OR source.availability_bucket<>'available'
       OR transit.availability_bucket<>'in_transit' OR source.material_id<>transit.material_id
       OR source.owner_org_id<>transit.owner_org_id
       OR NOT EXISTS(SELECT 1 FROM public.shipments s WHERE s.id=receipt.shipment_id AND s.target_person_id=NEW.actor_person_id) THEN
        RAISE EXCEPTION '0172 original rejection provenance mismatch' USING ERRCODE='23514'; END IF;
    SELECT count(*) INTO origin_count FROM public.material_inventory_policies p
      WHERE p.material_id=source.material_id AND p.effective_from<=receipt.received_at
        AND (p.effective_to IS NULL OR p.effective_to>receipt.received_at);
    IF origin_count<>1 THEN RAISE EXCEPTION '0172 original policy unavailable' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT policy FROM public.material_inventory_policies p
      WHERE p.material_id=source.material_id AND p.effective_from<=receipt.received_at
        AND (p.effective_to IS NULL OR p.effective_to>receipt.received_at);
    tracked:=policy.tracking_mode IN ('serial','lot_and_serial');
    IF EXISTS(SELECT 1 FROM unnest(ARRAY[line.accepted_qty,line.rejected_qty,NEW.quantity]) q
      WHERE q<>round(q,policy.quantity_scale) OR (NOT policy.allow_fraction AND q<>trunc(q))) THEN
        RAISE EXCEPTION '0172 original quantity precision required' USING ERRCODE='23514'; END IF;
    SELECT coalesce(jsonb_agg(serial_id::text ORDER BY serial_id),'[]') INTO original_serials
      FROM public.receipt_serials WHERE receipt_line_id=line.id AND NOT accepted;
    IF (tracked AND jsonb_array_length(original_serials)<>line.rejected_qty)
       OR (NOT tracked AND original_serials<>'[]') THEN
        RAISE EXCEPTION '0172 original rejected serial count mismatch' USING ERRCODE='23514'; END IF;
    IF (SELECT coalesce(sum(quantity),0) FROM public.material_request_rejection_returns WHERE receipt_line_id=line.id)+NEW.quantity>line.rejected_qty THEN
        RAISE EXCEPTION '0172 cumulative return exceeds rejection' USING ERRCODE='23514'; END IF;
    input:=NEW.evidence_jsonb->'input';
    selected_serials:=input->'serial_ids';
    IF jsonb_typeof(selected_serials) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION '0172 selected serial array required' USING ERRCODE='23514'; END IF;
    IF jsonb_array_length(selected_serials)>1000 OR (tracked AND jsonb_array_length(selected_serials)<>NEW.quantity)
       OR (NOT tracked AND selected_serials<>'[]')
       OR selected_serials IS DISTINCT FROM (SELECT coalesce(jsonb_agg(v ORDER BY v),'[]') FROM (SELECT DISTINCT value v FROM jsonb_array_elements(selected_serials)) s)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(selected_serials) v WHERE NOT original_serials @> jsonb_build_array(v)) THEN
        RAISE EXCEPTION '0172 selected return serials mismatch' USING ERRCODE='23514'; END IF;
    IF input IS DISTINCT FROM jsonb_build_object('expected_request_version',NEW.request_version,'receipt_id',receipt.id::text,
        'receipt_line_id',line.id::text,'receipt_request_hash',receipt.request_hash,'quantity',NEW.quantity::text,
        'serial_ids',selected_serials,'reason',NEW.reason) THEN
        RAISE EXCEPTION '0172 exact input required' USING ERRCODE='23514'; END IF;
    expected_origin:=jsonb_build_object('request_id',r.id::text,'revision_id',outbound.revision_id::text,
        'receipt_id',receipt.id::text,'receipt_line_id',line.id::text,'receipt_request_hash',receipt.request_hash,
        'shipment_line_id',ship.id::text,'outbound_posting_id',outbound.id::text,'reservation_id',reservation.id::text,
        'in_transit_account_id',transit.id::text,'return_source_account_id',source.id::text,'rejected_qty',line.rejected_qty::text,
        'condition',line.condition,'tracking_mode',policy.tracking_mode,
        'quantity_scale',policy.quantity_scale,'allow_fraction',policy.allow_fraction,'rejected_serial_ids',original_serials);
    expected:=jsonb_build_object('schema','rsc.material_request_rejection_return.v1','input',input,'origin',expected_origin);
    IF NEW.evidence_jsonb IS DISTINCT FROM expected OR NEW.evidence_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(expected),'UTF8')),'hex')
       OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(
         jsonb_build_object('request_id',r.id::text,'actor_user_id',NEW.actor_user_id,'actor_person_id',NEW.actor_person_id::text,'input',input)),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0172 rejection return evidence mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
SERIAL="""
DECLARE parent public.material_request_rejection_returns%ROWTYPE;
BEGIN
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=NEW.return_id;
    PERFORM 1 FROM public.material_requests WHERE id=parent.request_id FOR UPDATE;
    IF NEW.receipt_line_id<>parent.receipt_line_id OR NOT parent.evidence_jsonb->'input'->'serial_ids' @> to_jsonb(ARRAY[NEW.serial_id::text])
       OR NOT EXISTS(SELECT 1 FROM public.receipt_serials WHERE receipt_line_id=NEW.receipt_line_id AND serial_id=NEW.serial_id AND NOT accepted) THEN
        RAISE EXCEPTION '0172 return serial origin mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
VALIDATE="""
DECLARE parent public.material_request_rejection_returns%ROWTYPE; actual jsonb; expected jsonb; total bigint; matches bigint;
BEGIN
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=checked_id;
    SELECT coalesce(jsonb_agg(serial_id::text ORDER BY serial_id),'[]') INTO actual
      FROM public.material_request_rejection_return_serials WHERE return_id=parent.id;
    IF actual IS DISTINCT FROM parent.evidence_jsonb->'input'->'serial_ids' THEN
        RAISE EXCEPTION '0172 complete return serial facts required' USING ERRCODE='23514'; END IF;
    expected:=jsonb_build_object('id',parent.id::text,'request_id',parent.request_id::text,'receipt_id',parent.receipt_id::text,
        'receipt_line_id',parent.receipt_line_id::text,'actor_person_id',parent.actor_person_id::text,
        'actor_role_assignment_id',parent.actor_role_assignment_id::text,'return_no',parent.return_no,
        'request_version',parent.request_version,'authorization_version',parent.authorization_version,
        'idempotency_key_hash',parent.idempotency_key_hash,'request_hash',parent.request_hash,
        'evidence_sha256',parent.evidence_sha256,'quantity',parent.quantity::text);
    SELECT count(*),count(*) FILTER(WHERE stream_key='material_request' AND action='material_request.register_rejection_return'
      AND actor_user_id=parent.actor_user_id AND request_id=parent.trace_request_id
      AND occurred_at=parent.occurred_at AND created_at=parent.created_at
      AND before_jsonb=jsonb_build_object('rejection_return','not_registered') AND after_jsonb=expected)
      INTO total,matches FROM public.audit_events WHERE aggregate_type='material_request_rejection_return' AND aggregate_id=parent.id::text;
    IF total<>1 OR matches<>1 THEN RAISE EXCEPTION '0172 exact return audit required' USING ERRCODE='23514'; END IF;
END;
"""
DISPATCH="""
BEGIN
    IF TG_TABLE_NAME='material_request_rejection_returns' THEN
        PERFORM public.rsc_validate_rejection_return_0172(NEW.id);
    ELSIF TG_TABLE_NAME='material_request_rejection_return_serials' THEN
        PERFORM public.rsc_validate_rejection_return_0172(NEW.return_id);
    ELSE
        IF NEW.action<>'material_request.register_rejection_return' OR NEW.aggregate_type<>'material_request_rejection_return' THEN
            RAISE EXCEPTION '0172 return audit type mismatch' USING ERRCODE='23514'; END IF;
        PERFORM public.rsc_validate_rejection_return_0172(NEW.aggregate_id::uuid);
    END IF;
    RETURN NULL;
END;
"""
FUNCTIONS={
    'rsc_assert_rejection_return_authority_0172(uuid,text,uuid,uuid,bigint)':('checked_request_id uuid, actor_id text, checked_person_id uuid, assignment_id uuid, auth_version bigint','void',AUTHORITY),
    'rsc_guard_rejection_return_insert_0172()':('','trigger',INSERT),
    'rsc_guard_rejection_return_serial_0172()':('','trigger',SERIAL),
    'rsc_validate_rejection_return_0172(uuid)':('checked_id uuid','void',VALIDATE),
    'rsc_dispatch_rejection_return_0172()':('','trigger',DISPATCH),
}


def install(db):
    from app.material_request_rejection_return_schema import returns,return_serials
    identity=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2]!=('star_oam_migrator','star_oam_migrator') or identity[2]//10000!=16 or identity[3]!='read committed':
        raise ValueError('0172 direct native PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()!=['20261220_0171']:
        raise ValueError('0172 exact predecessor required')
    before_path=db.scalar(text("SELECT current_setting('search_path')"))
    db.execute(text("SELECT set_config('search_path','public',true)"))
    for table in (returns,return_serials):
        table.create(db)
        db.execute(text(f'REVOKE ALL ON public.{table.name} FROM PUBLIC,star_oam_api'))
        db.execute(text(f'GRANT SELECT,INSERT ON public.{table.name} TO star_oam_api'))
        for ops,level,suffix in (('UPDATE OR DELETE','ROW','immutable'),('TRUNCATE','STATEMENT','truncate')):
            db.execute(text(f'CREATE TRIGGER rsc_rejection_return_{suffix}_0172 BEFORE {ops} ON public.{table.name} '
                f'FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    for signature,(arguments,result,body) in FUNCTIONS.items():
        db.execute(text(f'CREATE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$'+body+'$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api'))
    for table,kind in ((returns,'insert'),(return_serials,'serial')):
        db.execute(text(f'CREATE TRIGGER rsc_rejection_return_insert_0172 BEFORE INSERT ON public.{table.name} '
            f'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_rejection_return_{kind}_0172()'))
    for table in (returns.name,return_serials.name,'audit_events'):
        when="WHEN (NEW.action='material_request.register_rejection_return' OR NEW.aggregate_type='material_request_rejection_return') " if table=='audit_events' else ''
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_rejection_return_complete_0172 AFTER INSERT ON public.{table} '
            'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '+when+'EXECUTE FUNCTION public.rsc_dispatch_rejection_return_0172()'))
        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_rejection_return_complete_0172'))
    db.execute(text("SELECT set_config('search_path',:path,true)"),{'path':before_path})
