"""Prove loss-origin physical parcel handover as distinct from receipt/inbound.

All authority and freeze checks apply at COMMIT; historical facts remain
verifiable after mutable grants change. No routes or permission seeds.
"""
from pathlib import Path
import hashlib
import runpy
from alembic import op

revision = '20261203_0154'
down_revision = '20261202_0153'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261202_0153_stock_loss_return_outbounds.py'))
ready = previous['ready']
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
outbound = previous
receipts = runpy.run_path(str(FOLDER/'20261015_0105_stock_return_receipts.py'))
SIGNATURE = 'public.rsc_check_stock_return_shipment_0104(uuid)'
BASE = receipts['_sources']()[SIGNATURE][1]
assert hashlib.sha256(BASE.encode()).hexdigest() == '4f699ffd6a3e8917f9f2bd0aedf154b93e0b125ae1737b9307855310079fc5ab'


def replace(value, old, new):
    assert value.count(old) == 1, old
    return value.replace(old,new)


AUTHORITY_NAME = 'rsc_assert_loss_shipment_authority_0154'
AUTHORITY_BODY = outbound['AUTHORITY_BODY'].replace('outbound_return','ship_return').replace('0153 loss outbound','0154 loss shipment')
NAME = 'rsc_check_loss_shipment_0154'
ORIGIN = '''
    SELECT * INTO disposition FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id;
    IF disposition.id IS NULL OR parent.operation_type<>'return' OR parent.oam_work_order_id IS NOT NULL
       OR parent.loss_headquarters_decision_id IS DISTINCT FROM disposition.headquarters_decision_id
       OR disposition.disposition<>'return_to_region'
       OR (SELECT count(*) FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id)<>1 THEN
        RAISE EXCEPTION 'loss shipment exact derived origin required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_return_0152(disposition.id,false);
    wanted_origin:=jsonb_build_object('origin_kind','loss_report','operation_id',parent.id::text,
        'requester_id',parent.requester_id::text,'submitted_by_user_id',parent.actor_user_id,
        'request_hash',parent.request_hash,'plan_hash',parent.plan_hash,
        'posting_transaction_id',parent.posting_transaction_id::text,
        'submitted_at',CASE WHEN mod(extract(microseconds FROM parent.created_at)::bigint,1000000)=0
            THEN to_char(parent.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
            ELSE to_char(parent.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
        'loss_operation_id',disposition.operation_id::text,'loss_line_id',disposition.line_id::text,
        'headquarters_decision_id',disposition.headquarters_decision_id::text,'disposition_id',disposition.id::text);
    IF parcel.plan_jsonb->'origin' IS DISTINCT FROM wanted_origin
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(parcel.plan_jsonb) key)
          IS DISTINCT FROM ARRAY['audit_cursor','authorization_version','destination','intent','ledger_cursor',
            'lines','origin','original_request_hash','policies']::text[] THEN
        RAISE EXCEPTION 'loss shipment exact provenance snapshot required' USING ERRCODE='23514'; END IF;
    IF jsonb_typeof(parcel.plan_jsonb->'destination') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'loss shipment route object required' USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(parcel.plan_jsonb->'destination') key)
        IS DISTINCT FROM ARRAY['custodian_person_id','custody_assignment_id','custody_effective_from','region_org_id',
            'source_location_id','target_location_code','target_location_id','target_location_name',
            'transit_location_code','transit_location_id','transit_location_name']::text[] THEN
        RAISE EXCEPTION 'loss shipment exact route snapshot required' USING ERRCODE='23514'; END IF;
    IF require_current THEN
        PERFORM public.rsc_assert_loss_shipment_authority_0154(header.actor_user_id,header.authorization_version,
            header.actor_person_id,parent.source_location_id);
        PERFORM id FROM public.stock_locations WHERE id IN (parent.target_location_id,parent.transit_location_id)
            ORDER BY id FOR UPDATE;
        PERFORM id FROM public.custody_assignments WHERE location_id=parent.target_location_id ORDER BY id FOR SHARE;
        IF NOT EXISTS(SELECT 1 FROM public.stock_locations route_target
            JOIN public.stock_locations route_transit ON route_transit.id=parent.transit_location_id
            WHERE route_target.id=parent.target_location_id
              AND parcel.plan_jsonb->'destination'->>'region_org_id'=route_target.owner_org_id::text
              AND parcel.plan_jsonb->'destination'->>'target_location_code'=route_target.code
              AND parcel.plan_jsonb->'destination'->>'target_location_name'=route_target.name
              AND parcel.plan_jsonb->'destination'->>'transit_location_code'=route_transit.code
              AND parcel.plan_jsonb->'destination'->>'transit_location_name'=route_transit.name) THEN
            RAISE EXCEPTION 'loss shipment current route snapshot mismatch' USING ERRCODE='23514'; END IF;
        IF (SELECT count(*) FROM public.custody_assignments a WHERE a.location_id=parent.target_location_id
            AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp()))<>1
           OR NOT EXISTS(SELECT 1 FROM public.custody_assignments a WHERE a.id=parcel.target_custody_assignment_id
            AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())) THEN
            RAISE EXCEPTION 'loss shipment current receiving custody required' USING ERRCODE='23514'; END IF;
    END IF;
'''

BODY = replace(BASE, '    fingerprint jsonb; reference text;',
    '    fingerprint jsonb; reference text; disposition public.stock_loss_dispositions%ROWTYPE; wanted_origin jsonb;')
BODY = replace(BODY,'    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=parcel.operation_id;',
    '    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=parcel.operation_id;'+ORIGIN)
BODY = replace(BODY,'OR parcel.actor_user_id<>parent.actor_user_id OR header.actor_person_id<>parent.requester_id',
    'OR header.actor_person_id<>parent.requester_id')
BODY = replace(BODY,"AND actor.authorization_version=header.authorization_version AND actor.is_active\n              AND actor.account_status='active' AND person.employment_status='active'",'AND person.id=parent.requester_id')
BODY = replace(BODY,"(SELECT count(*) FROM jsonb_object_keys(parcel.plan_jsonb))<>8", "(SELECT count(*) FROM jsonb_object_keys(parcel.plan_jsonb))<>9")
BODY = replace(BODY,"OR cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')",
    "OR cursor_value>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')\n"
    "       OR (require_current AND cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory'))")
BODY = replace(BODY,"WHERE source.id=parent.source_location_id AND source.status='active' AND source.location_type='personal'\n              AND source.custodian_person_id=header.actor_person_id AND source.parent_id=target.id\n              AND target.status='active' AND target.location_type='region' AND target.owner_org_id=source.owner_org_id\n              AND target.custodian_person_id=assignment.custodian_person_id\n              AND transit.status='active' AND transit.location_type='transit' AND transit.parent_id=target.id\n              AND transit.owner_org_id=target.owner_org_id", "WHERE source.id=parent.source_location_id\n              AND (NOT require_current OR (source.status='active' AND source.location_type='personal'\n                AND source.custodian_person_id=header.actor_person_id AND source.parent_id=target.id\n                AND target.status='active' AND target.location_type='region' AND target.owner_org_id=source.owner_org_id\n                AND target.custodian_person_id=assignment.custodian_person_id\n                AND transit.status='active' AND transit.location_type='transit' AND transit.parent_id=target.id\n                AND transit.owner_org_id=target.owner_org_id))")
# Region/route metadata are validated against current masters only on insertion.
BODY = replace(BODY,"AND parcel.plan_jsonb->'destination'->>'region_org_id'=target.owner_org_id::text", "AND (NOT require_current OR parcel.plan_jsonb->'destination'->>'region_org_id'=target.owner_org_id::text)")
BODY = replace(BODY,"OR account.custodian_person_id IS DISTINCT FROM header.actor_person_id OR account.condition_code NOT IN ('used','damaged')", "OR account.custodian_person_id IS DISTINCT FROM header.actor_person_id\n           OR account.condition_code IS DISTINCT FROM origin.target_condition\n           OR origin.source_loss_line_id IS DISTINCT FROM disposition.line_id OR origin.source_recovery_line_id IS NOT NULL")
BODY = replace(BODY,"OR account.material_id<>origin.material_id OR sku.status<>'active'", "OR account.material_id<>origin.material_id OR (require_current AND sku.status<>'active')")
BODY = replace(BODY,"        SELECT * INTO account FROM public.stock_accounts WHERE id=departure_line.transit_stock_account_id;", "        PERFORM public.rsc_check_loss_outbound_0153(departure.id,false);\n        SELECT * INTO account FROM public.stock_accounts WHERE id=departure_line.transit_stock_account_id;")
# Physical handover is stock-neutral but still subject to the established
# transit account's hard stocktake freeze. The inherited ledger lock serializes
# this check with stocktake issue/close commands; history does not acquire a new
# current operational prohibition after the original shipment has committed.
FREEZE_PROOF = """
        IF require_current AND EXISTS(
            SELECT 1 FROM public.inventory_freezes frozen
            JOIN public.stocktake_scopes scope ON scope.id=frozen.stocktake_scope_id AND scope.task_id=frozen.task_id
            WHERE frozen.freeze_mode='hard' AND frozen.status IN ('active','released','cancelled')
              AND ((frozen.valid_from<=clock_timestamp() AND (frozen.valid_to IS NULL OR frozen.valid_to>clock_timestamp()))
                OR (frozen.valid_from<=header.created_at AND (frozen.valid_to IS NULL OR frozen.valid_to>header.created_at)))
              AND scope.owner_org_id=account.owner_org_id AND scope.location_id=account.location_id
              AND (scope.scope_mode='location_all' OR (scope.scope_mode='filtered'
                AND (scope.material_id IS NULL OR scope.material_id=account.material_id)
                AND (scope.condition_code IS NULL OR scope.condition_code=account.condition_code)
                AND (scope.availability_bucket IS NULL OR scope.availability_bucket=account.availability_bucket)))) THEN
            RAISE EXCEPTION 'loss shipment transit scope is hard frozen' USING ERRCODE='23514'; END IF;
"""
BODY = replace(BODY, '        SELECT * INTO account FROM public.stock_accounts WHERE id=departure_line.transit_stock_account_id;',
    '        SELECT * INTO account FROM public.stock_accounts WHERE id=departure_line.transit_stock_account_id;'+FREEZE_PROOF)
BODY = replace(BODY,"OR serial.lifecycle_status<>'active' OR position.stock_account_id IS DISTINCT FROM account.id", "OR (require_current AND (serial.lifecycle_status<>'active' OR position.stock_account_id IS DISTINCT FROM account.id))")
BODY = replace(BODY,"'source_recovery_line_id',origin.source_recovery_line_id::text", "'source_loss_line_id',origin.source_loss_line_id::text")
for field,column in (('sku_code','sku_code'),('material_name','name'),('base_unit','base_unit')):
    BODY = replace(BODY, f"'{field}',sku.{column}", f"'{field}',CASE WHEN require_current THEN sku.{column} ELSE view->>'{field}' END")
BODY = replace(BODY,"'lot_no',(SELECT lot_no FROM public.inventory_lots WHERE id=account.lot_id)", "'lot_no',CASE WHEN require_current THEN (SELECT lot_no FROM public.inventory_lots WHERE id=account.lot_id) ELSE view->>'lot_no' END")
BODY = replace(BODY,"'work_order_id',parent.oam_work_order_id::text,", "'origin_kind','loss_report','loss_operation_id',disposition.operation_id::text,\n        'loss_line_id',disposition.line_id::text,'headquarters_decision_id',disposition.headquarters_decision_id::text,\n        'loss_disposition_id',disposition.id::text,")
NOTIFICATION = outbound['NOTIFICATION'].replace('stock_operation_outbound','stock_operation_shipment').replace('stock_return_outbound','stock_return_shipped').replace('departure.','parcel.').replace('loss outbound','loss shipment')
NOTIFICATION = NOTIFICATION.replace('assignments assignment ON', 'assignments notification_custody ON').replace('assignment.', 'notification_custody.')
BODY = replace(BODY,'\nEND;\n',NOTIFICATION+'\nEND;\n')
DISPATCHED = replace(BASE,'BEGIN\n',f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_shipments s JOIN public.stock_operation_orders p ON p.id=s.operation_id
        WHERE s.id=checked_shipment AND p.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(checked_shipment,false);
        RETURN;
    END IF;
''')
INSERT_GUARD = f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=NEW.operation_id
        AND loss_headquarters_decision_id IS NOT NULL) THEN PERFORM public.{NAME}(NEW.id,true); END IF;
    RETURN NULL;
END;'''
NOTIFICATION_GUARD = f'''DECLARE event public.notification_events%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
    ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
    IF EXISTS(SELECT 1 FROM public.stock_operation_shipments s JOIN public.stock_operation_orders p ON p.id=s.operation_id
        WHERE s.id::text=event.business_id AND p.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(event.business_id::uuid,false);
    END IF;
    RETURN NULL;
END;'''


FUNCTIONS = {
    (AUTHORITY_NAME, 'text, bigint, uuid, uuid'):
        ('actor_id text, actor_version bigint, person_id uuid, location_id uuid', 'void', AUTHORITY_BODY),
    (NAME, 'uuid, boolean'): ('checked_shipment uuid, require_current boolean', 'void', BODY),
    ('rsc_guard_loss_shipment_insert_0154', ''): ('', 'trigger', INSERT_GUARD),
    ('rsc_guard_loss_shipment_notification_0154', ''): ('', 'trigger', NOTIFICATION_GUARD),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
OLD_HASH = hashlib.sha256(BASE.encode()).hexdigest()
NEW_HASH = hashlib.sha256(DISPATCHED.encode()).hexdigest()
TRIGGERS = {
    'trg_loss_shipment_current_0154': ('stock_operation_shipments', 'rsc_guard_loss_shipment_insert_0154'),
    'trg_loss_shipment_notification_0154': ('notification_events', 'rsc_guard_loss_shipment_notification_0154'),
    'trg_loss_shipment_target_0154': ('notification_person_targets', 'rsc_guard_loss_shipment_notification_0154'),
}
RETAINED = """EXISTS(SELECT 1 FROM stock_operation_shipments s
    JOIN stock_operation_orders p ON p.id=s.operation_id
    WHERE p.loss_headquarters_decision_id IS NOT NULL)"""

def _sources():
    return {SIGNATURE: (BASE, DISPATCHED)}


def _verify_triggers():
    for name, (table, function) in TRIGGERS.items():
        op.execute(f"""DO $$ BEGIN IF NOT EXISTS(
            SELECT 1 FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint
            WHERE t.tgname='{name}' AND t.tgrelid='public.{table}'::regclass
              AND t.tgfoid='public.{function}()'::regprocedure AND t.tgtype=5
              AND t.tgenabled='A' AND t.tgdeferrable AND t.tginitdeferred
              AND NOT t.tgisinternal AND t.tgnargs=0 AND t.tgqual IS NULL
              AND t.tgattr=''::int2vector AND t.tgconstrrelid=0
              AND c.contype='t' AND c.condeferrable AND c.condeferred)
            THEN RAISE EXCEPTION '0154 loss shipment trigger catalog drift'; END IF; END $$""")


def _transition(up):
    helper = runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0154 PostgreSQL or SQLite required')
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0154 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.stock_operation_lines,public.stock_loss_dispositions,public.stock_operation_outbounds,public.stock_operation_outbound_lines,public.stock_operation_outbound_serials,public.shipments,public.stock_operation_shipments,public.stock_operation_shipment_lines,public.stock_operation_shipment_serials,public.stock_locations,public.custody_assignments,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
    if not up:
        helper['_preflight'](RETAINED, '0154 loss shipment history requires retention')
    if dialect == 'sqlite':
        if up:
            op.execute("""CREATE TRIGGER trg_loss_shipment_block_0154 BEFORE INSERT ON stock_operation_shipments
                WHEN EXISTS(SELECT 1 FROM stock_operation_orders WHERE id=NEW.operation_id
                    AND loss_headquarters_decision_id IS NOT NULL)
                BEGIN SELECT RAISE(ABORT,'0154 PostgreSQL loss shipment proof required'); END""")
        else:
            op.execute('DROP TRIGGER trg_loss_shipment_block_0154')
        return
    verify = runpy.run_path(str(FOLDER/'20261125_0146_stock_loss_request_seals.py'))['_verify_function']
    verify('rsc_check_stock_return_shipment_0104', 'uuid', 'checked_shipment uuid', 'void', BASE if up else DISPATCHED)
    if up:
        for (name, signature), (args, result, body) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
        for name, (table, function) in TRIGGERS.items():
            op.execute(f'CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()')
            op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
    for (name, signature), (args, result, body) in FUNCTIONS.items():
        verify(name, signature, args, result, body)
    _verify_triggers()
    replace = runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature=SIGNATURE, expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,
        replacements=((BASE, DISPATCHED),) if up else ((DISPATCHED, BASE),), label='loss_shipment_0154')
    verify('rsc_check_stock_return_shipment_0104', 'uuid', 'checked_shipment uuid', 'void', DISPATCHED if up else BASE)
    if up:
        op.execute(f'''DO $$ DECLARE identifier uuid; BEGIN
            FOR identifier IN SELECT o.id FROM public.stock_operation_shipments o
                JOIN public.stock_operation_orders p ON p.id=o.operation_id
                WHERE p.loss_headquarters_decision_id IS NOT NULL LOOP
                PERFORM public.{NAME}(identifier,false);
            END LOOP; END $$''')
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
        expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
        replacements=((down_revision, revision),) if up else ((revision, down_revision),), label='loss_shipment_ready_0154')
    if not up:
        for name, (table, _) in TRIGGERS.items():
            op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name, signature in reversed(FUNCTIONS):
            op.execute(f'DROP FUNCTION public.{name}({signature})')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
