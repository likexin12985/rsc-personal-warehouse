"""Forward closure barrier SQL; installed only by the reviewed 0169 migration.

Kept independent of live ORM and application code. Until the complete closure
insert validator and runtime contract are activated, this is a candidate only.
"""
from sqlalchemy import text


DIRECT = (
    'material_request_commands', 'material_request_revisions', 'material_request_lines',
    'stock_allocations', 'stock_reservations', 'stock_reservation_releases',
    'stock_reservation_picks', 'outbound_postings',
)
MODES = {name: 'request' for name in DIRECT}
MODES.update(material_requests='self', supply_tasks='line', substitution_decisions='line',
    shipment_lines='outbound', receipts='shipment', receipt_lines='shipment_line',
    inbound_orders='receipt', inbound_postings='inbound')

LOCK_BODY = """
BEGIN
    -- A snapshot taken before a concurrent closure must not authorize a new
    -- write after waiting for its lock. READ COMMITTED gives each statement
    -- below a fresh snapshot; stricter snapshot modes are rejected explicitly.
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION '0169 fulfillment requires read committed isolation' USING ERRCODE='25001';
    END IF;
    PERFORM 1 FROM public.material_requests WHERE id=checked_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '0169 fulfillment request missing' USING ERRCODE='23503';
    END IF;
    IF EXISTS(SELECT 1 FROM public.material_request_closures WHERE request_id=checked_request_id) THEN
        RAISE EXCEPTION '0169 business request is closed' USING ERRCODE='55000';
    END IF;
END;
"""

ROOTS_BODY = """
DECLARE roots uuid[];
BEGIN
    CASE reference_kind
    WHEN 'request' THEN roots := ARRAY[(document->>'request_id')::uuid];
    WHEN 'self' THEN roots := ARRAY[(document->>'id')::uuid];
    WHEN 'line' THEN
        SELECT array_agg(request_id) INTO roots FROM public.material_request_lines
        WHERE id=(document->>'request_line_id')::uuid;
    WHEN 'outbound' THEN
        SELECT array_agg(request_id) INTO roots FROM public.outbound_postings
        WHERE id=(document->>'outbound_posting_id')::uuid;
    WHEN 'shipment' THEN
        SELECT array_agg(DISTINCT o.request_id) INTO roots FROM public.shipment_lines s
        JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
        WHERE s.shipment_id=(document->>'shipment_id')::uuid;
    WHEN 'shipment_line' THEN
        SELECT array_agg(o.request_id) INTO roots FROM public.shipment_lines s
        JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
        WHERE s.id=(document->>'shipment_line_id')::uuid;
    WHEN 'receipt' THEN
        SELECT array_agg(DISTINCT o.request_id) INTO roots FROM public.receipts r
        JOIN public.shipment_lines s ON s.shipment_id=r.shipment_id
        JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
        WHERE r.id=(document->>'receipt_id')::uuid;
    WHEN 'inbound' THEN
        SELECT array_agg(DISTINCT o.request_id) INTO roots FROM public.inbound_orders i
        JOIN public.receipts r ON r.id=i.receipt_id
        JOIN public.shipment_lines s ON s.shipment_id=r.shipment_id
        JOIN public.outbound_postings o ON o.id=s.outbound_posting_id
        WHERE i.id=(document->>'inbound_order_id')::uuid;
    ELSE
        RAISE EXCEPTION '0169 unsupported fulfillment reference' USING ERRCODE='23514';
    END CASE;
    IF roots IS NULL OR cardinality(roots)=0 OR array_position(roots,NULL) IS NOT NULL THEN
        RAISE EXCEPTION '0169 fulfillment request reference missing' USING ERRCODE='23503';
    END IF;
    RETURN roots;
END;
"""

BARRIER_BODY = """
DECLARE roots uuid[]; parent_id uuid;
BEGIN
    IF TG_OP='UPDATE' AND TG_TABLE_NAME='material_requests'
       AND (to_jsonb(NEW) - ARRAY['logistics_signature_status','oam_receipt_status',
            'notification_status','reconciliation_status','updated_at'])
         = (to_jsonb(OLD) - ARRAY['logistics_signature_status','oam_receipt_status',
            'notification_status','reconciliation_status','updated_at']) THEN
        RETURN NEW;
    END IF;
    roots := public.rsc_request_roots_0169(TG_ARGV[0],to_jsonb(NEW));
    IF TG_OP='UPDATE' THEN
        roots := roots || public.rsc_request_roots_0169(TG_ARGV[0],to_jsonb(OLD));
    END IF;
    FOR parent_id IN SELECT DISTINCT value FROM unnest(roots) value ORDER BY value LOOP
        PERFORM public.rsc_require_request_open_0169(parent_id);
    END LOOP;
    RETURN NEW;
END;
"""

IMMUTABLE_BODY = """
BEGIN
    RAISE EXCEPTION '0169 business closure facts are append-only' USING ERRCODE='55000';
END;
"""

FUNCTIONS = {
    'rsc_require_request_open_0169(uuid)': ('checked_request_id uuid', 'void', LOCK_BODY),
    'rsc_request_roots_0169(text,jsonb)': ('reference_kind text, document jsonb', 'uuid[]', ROOTS_BODY),
    'rsc_guard_request_open_0169()': ('', 'trigger', BARRIER_BODY),
    'rsc_guard_closure_immutable_0169()': ('', 'trigger', IMMUTABLE_BODY),
}


def install(db):
    """Caller owns the migration transaction, table creation and admission."""
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0169 direct PostgreSQL16 migrator required')
    for signature, (arguments, result, body) in FUNCTIONS.items():
        name = signature.split('(', 1)[0]
        db.execute(text(f'CREATE FUNCTION public.{name}({arguments}) RETURNS {result} '
            'LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public '
            'AS $body$' + body + '$body$'))
        db.execute(text(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, star_oam_api'))
    for name, mode in MODES.items():
        operations = 'UPDATE' if name == 'material_requests' else 'INSERT OR UPDATE'
        db.execute(text(f'CREATE TRIGGER aaa_rsc_request_open_0169 BEFORE {operations} '
            f'ON public.{name} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_request_open_0169(\'{mode}\')'))
    db.execute(text('CREATE TRIGGER rsc_closure_immutable_0169 BEFORE UPDATE OR DELETE '
        'ON public.material_request_closures FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
    db.execute(text('CREATE TRIGGER rsc_closure_no_truncate_0169 BEFORE TRUNCATE '
        'ON public.material_request_closures FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_guard_closure_immutable_0169()'))
