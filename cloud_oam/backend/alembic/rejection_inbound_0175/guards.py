"""Development compiler for caller-owned PG16; not a production migration.

Freeze a verified before/after catalog before registering runtime admission.
The predecessor authority and immutable ledger guards remain mandatory.
"""
from hashlib import sha256
from pathlib import Path
import runpy
from sqlalchemy import text

INSERT = """
DECLARE parent public.material_request_rejection_returns%ROWTYPE;
    receipt public.material_request_rejection_receipts%ROWTYPE; r public.material_requests%ROWTYPE;
BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.actor_user_id]::text[]);
    PERFORM public.rsc_require_request_open_0169(NEW.request_id);
    SELECT * INTO STRICT r FROM public.material_requests WHERE id=NEW.request_id;
    SELECT * INTO STRICT parent FROM public.material_request_rejection_returns WHERE id=NEW.return_id;
    SELECT * INTO STRICT receipt FROM public.material_request_rejection_receipts WHERE id=NEW.receipt_id;
    PERFORM public.rsc_lock_inventory_reference_graph_0027(ARRAY[parent.in_transit_account_id,parent.return_source_account_id],CURRENT_TIMESTAMP);
    PERFORM public.rsc_assert_rejection_receipt_authority_0174(parent.id,NEW.actor_user_id,NEW.actor_person_id,
        NEW.actor_role_assignment_id,NEW.authorization_version,NEW.custody_assignment_id,NEW.recorded_at);
    PERFORM public.rsc_validate_rejection_return_0172(parent.id);
    PERFORM public.rsc_validate_rejection_progress_0173(receipt.handover_id);
    PERFORM public.rsc_validate_rejection_receipt_0174(receipt.id);
    IF parent.request_id<>r.id OR receipt.return_id<>parent.id OR NEW.request_version<>r.version
       OR NEW.request_version<receipt.request_version OR r.status NOT IN ('approved','partially_approved')
       OR EXISTS(SELECT 1 FROM public.material_request_remaining_cancellations WHERE request_id=r.id)
       OR NEW.source_account_id<>parent.in_transit_account_id OR NEW.target_location_id<>receipt.target_location_id
       OR NEW.accepted_qty<>receipt.accepted_qty OR NEW.damaged_qty<>receipt.damaged_qty
       OR NEW.recorded_at<receipt.recorded_at OR NEW.recorded_at<>CURRENT_TIMESTAMP
       OR NEW.id='00000000-0000-0000-0000-000000000000'::uuid
       OR NEW.reason<>btrim(NEW.reason) OR NEW.reason ~ '[[:cntrl:]]'
       OR NEW.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR NEW.trace_request_id !~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}$'
       OR EXISTS(SELECT 1 FROM public.audit_events WHERE actor_user_id=NEW.actor_user_id
            AND request_id=NEW.trace_request_id) THEN
        RAISE EXCEPTION '0175 warehouse inbound origin or state mismatch' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
CHILD = """
DECLARE fact public.material_request_rejection_inbounds%ROWTYPE;
BEGIN
    SELECT * INTO STRICT fact FROM public.material_request_rejection_inbounds WHERE id=NEW.inbound_id;
    PERFORM public.rsc_require_request_open_0169(fact.request_id);
    IF fact.recorded_at<>CURRENT_TIMESTAMP OR EXISTS(SELECT 1 FROM public.audit_events
        WHERE aggregate_type='material_request_rejection_inbound' AND aggregate_id=fact.id::text) THEN
        RAISE EXCEPTION '0175 warehouse inbound children already sealed' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
BALANCE = """
DECLARE result jsonb;
BEGIN
    SELECT jsonb_build_object('quantity',COALESCE(sum(CASE WHEN m.to_account_id=account_id THEN m.quantity ELSE -m.quantity END),0)::numeric(18,3)::text,
        'version',count(DISTINCT t.id),'ledger_cursor',COALESCE(max(t.ledger_cursor),0)) INTO result
      FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id
      WHERE t.status='posted' AND t.ledger_cursor<before_cursor AND (m.from_account_id=account_id OR m.to_account_id=account_id);
    RETURN result;
END;
"""
DIMENSIONS = """
DECLARE result jsonb;
BEGIN
    SELECT jsonb_build_object('owner_org_id',a.owner_org_id::text,'custodian_person_id',a.custodian_person_id::text,
        'location_id',a.location_id::text,'material_id',a.material_id::text,'condition_code',a.condition_code,
        'availability_bucket',a.availability_bucket,'lot_id',a.lot_id::text) INTO STRICT result
      FROM public.stock_accounts a WHERE a.id=account_id;
    RETURN result;
END;
"""
DISPATCH = """
DECLARE identifier uuid; original_source text;
BEGIN
    IF TG_TABLE_NAME='inventory_transactions' THEN
        IF NEW.reversed_transaction_id IS NOT NULL THEN
            SELECT source_document_type INTO original_source FROM public.inventory_transactions WHERE id=NEW.reversed_transaction_id;
            IF original_source='material_request_rejection_inbound' THEN
                RAISE EXCEPTION '0175 inbound reversal requires independent command' USING ERRCODE='23514'; END IF;
        END IF;
        IF NEW.source_document_type='material_request_rejection_inbound' OR NEW.posting_key LIKE 'rejection-inbound:%' THEN
            SELECT id INTO identifier FROM public.material_request_rejection_inbounds WHERE posting_transaction_id=NEW.id;
            IF identifier IS NULL THEN RAISE EXCEPTION '0175 inventory posting requires warehouse inbound' USING ERRCODE='23514'; END IF;
        ELSE RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type<>'material_request_rejection_inbound' OR NEW.action<>'material_request.rejection_return.inbound' THEN
            RAISE EXCEPTION '0175 inbound audit target mismatch' USING ERRCODE='23514'; END IF;
        identifier:=NEW.aggregate_id::uuid;
    ELSIF TG_TABLE_NAME='outbox_events' THEN
        IF NEW.aggregate_type<>'material_request_rejection_inbound' OR NEW.event_type<>'material_request.rejection_return.inbound' THEN
            RAISE EXCEPTION '0175 inbound outbox target mismatch' USING ERRCODE='23514'; END IF;
        identifier:=NEW.aggregate_id::uuid;
    ELSIF TG_TABLE_NAME='notification_events' THEN
        IF NEW.business_type<>'material_request_rejection_inbound' OR NEW.event_type<>'material_request.rejection_return.inbound' THEN
            RAISE EXCEPTION '0175 inbound notification target mismatch' USING ERRCODE='23514'; END IF;
        identifier:=NEW.business_id::uuid;
    ELSIF TG_TABLE_NAME='notification_person_targets' THEN
        SELECT business_id::uuid INTO identifier FROM public.notification_events
            WHERE id=NEW.event_id AND business_type='material_request_rejection_inbound';
        IF identifier IS NULL THEN RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME='material_request_rejection_inbounds' THEN identifier:=NEW.id;
    ELSE identifier:=NEW.inbound_id;
    END IF;
    PERFORM public.rsc_validate_rejection_inbound_0175(identifier);
    RETURN NULL;
END;
"""

ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.material_request_rejection_inbound_parts part
        JOIN public.material_request_rejection_inbounds inbound ON inbound.id=part.inbound_id
        JOIN public.material_request_rejection_receipts receipt ON receipt.id=inbound.receipt_id
        JOIN public.inventory_transactions tx ON tx.id=inbound.posting_transaction_id
        JOIN public.inventory_movements m ON m.transaction_id=tx.id AND m.to_account_id=NEW.id
            AND m.from_account_id=inbound.source_account_id AND m.quantity=part.quantity
        JOIN public.inventory_opening_establishments established ON established.owner_org_id=NEW.owner_org_id
            AND established.location_id=NEW.location_id
        JOIN public.stock_balances balance ON balance.stock_account_id=NEW.id
        WHERE part.target_account_id=NEW.id AND NEW.created_at=inbound.recorded_at AND NEW.updated_at=NEW.created_at
            AND NEW.created_at=tx.effective_at AND NEW.created_at>=receipt.recorded_at
            AND NEW.created_at>=established.established_at AND balance.version=1 AND balance.ledger_cursor=tx.ledger_cursor
            AND balance.quantity=part.quantity AND tx.status='posted' AND tx.reversed_transaction_id IS NULL
            AND tx.source_document_type='material_request_rejection_inbound' AND tx.source_document_id=inbound.id::text
            AND NOT EXISTS(SELECT 1 FROM public.inventory_movements other JOIN public.inventory_transactions prior ON prior.id=other.transaction_id
                WHERE (other.from_account_id=NEW.id OR other.to_account_id=NEW.id)
                  AND (prior.ledger_cursor<tx.ledger_cursor OR other.from_account_id=NEW.id))
    ) THEN
        PERFORM public.rsc_validate_rejection_inbound_0175(inbound.id)
          FROM public.material_request_rejection_inbounds inbound JOIN public.material_request_rejection_inbound_parts part
            ON part.inbound_id=inbound.id WHERE part.target_account_id=NEW.id;
        RETURN NEW;
    END IF;

"""

VALIDATE = runpy.run_path(str(Path(__file__).with_name('validation.py')))['VALIDATE']
FUNCTIONS = {
    'rsc_guard_rejection_inbound_insert_0175()': ('', 'trigger', INSERT),
    'rsc_guard_rejection_inbound_child_0175()': ('', 'trigger', CHILD),
    'rsc_rejection_inbound_balance_0175(uuid,bigint)': ('account_id uuid, before_cursor bigint', 'jsonb', BALANCE),
    'rsc_rejection_inbound_dimensions_0175(uuid)': ('account_id uuid', 'jsonb', DIMENSIONS),
    'rsc_validate_rejection_inbound_0175(uuid)': ('checked_id uuid', 'void', VALIDATE),
    'rsc_dispatch_rejection_inbound_0175()': ('', 'trigger', DISPATCH),
}


def install(db):
    from app.material_request_rejection_inbound_schema import inbounds, parts, serials
    from app import material_request_rejection_receipt_security as predecessor
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 or identity[3] != 'read committed':
        raise ValueError('0175 direct native PG16 read-committed migrator required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != ['20261223_0174']:
        raise ValueError('0175 exact predecessor required')
    predecessor.verify(db)
    signature='public.rsc_require_opening_observation_account_0023()'
    definition, source=db.execute(text('SELECT pg_get_functiondef(oid),prosrc FROM pg_proc WHERE oid=to_regprocedure(:s)'), {'s':signature}).one()
    if sha256(source.encode()).hexdigest()!='77669b88e8ab76374732cba30978158103fdff857a40ac214b9484d5b1582036':
        raise ValueError('0175 exact predecessor account admission required')
    anchor="    IF NOT EXISTS (\n        SELECT 1\n          FROM public.inventory_movements AS movement"
    if source.count(anchor)!=1 or definition.count(source)!=1:
        raise ValueError('0175 predecessor account anchor drift')
    path=db.scalar(text("SELECT current_setting('search_path')"))
    db.execute(text("SELECT set_config('search_path','public',true)"))
    tables=(inbounds,parts,serials)
    for table in tables:
        table.create(db)
        db.execute(text(f'REVOKE ALL ON public.{table.name} FROM PUBLIC,star_oam_api'))
        db.execute(text(f'GRANT SELECT,INSERT ON public.{table.name} TO star_oam_api'))
        for operations,level,suffix in (('UPDATE OR DELETE','ROW','immutable'),('TRUNCATE','STATEMENT','truncate')):
            db.execute(text(f'CREATE TRIGGER rsc_rejection_inbound_{suffix}_0175 BEFORE {operations} ON public.{table.name} '
                f'FOR EACH {level} EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    for signature,(arguments,result,body) in FUNCTIONS.items():
        db.execute(text(f'CREATE FUNCTION public.{signature.split("(")[0]}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body$'+body+'$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api'))
    db.execute(text(definition.replace(source,source.replace(anchor,ACCOUNT_BRANCH+anchor))))
    for table in tables:
        function='insert' if table is inbounds else 'child'
        db.execute(text(f'CREATE TRIGGER rsc_rejection_inbound_insert_0175 BEFORE INSERT ON public.{table.name} '
            f'FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_rejection_inbound_{function}_0175()'))
    conditions={table.name:('INSERT','') for table in tables}
    conditions.update(
        inventory_transactions=('INSERT OR UPDATE',"WHEN (NEW.source_document_type='material_request_rejection_inbound' OR NEW.posting_key LIKE 'rejection-inbound:%' OR NEW.reversed_transaction_id IS NOT NULL)"),
        audit_events=('INSERT',"WHEN (NEW.aggregate_type='material_request_rejection_inbound' OR NEW.action='material_request.rejection_return.inbound')"),
        outbox_events=('INSERT OR UPDATE',"WHEN (NEW.aggregate_type='material_request_rejection_inbound' OR NEW.event_type='material_request.rejection_return.inbound')"),
        notification_events=('INSERT',"WHEN (NEW.business_type='material_request_rejection_inbound' OR NEW.event_type='material_request.rejection_return.inbound')"),
        notification_person_targets=('INSERT',''))
    for table,(operations,condition) in conditions.items():
        db.execute(text(f'CREATE CONSTRAINT TRIGGER rsc_rejection_inbound_complete_0175 AFTER {operations} ON public.{table} '
            f'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW {condition} EXECUTE FUNCTION public.rsc_dispatch_rejection_inbound_0175()'))
        db.execute(text(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER rsc_rejection_inbound_complete_0175'))
    db.execute(text("SELECT set_config('search_path',:path,true)"),{'path':path})
