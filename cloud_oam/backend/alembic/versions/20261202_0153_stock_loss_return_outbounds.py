"""Prove physical departures of approved loss returns without inventing work orders.

Current engineer authority is checked at COMMIT; immutable origin and stock
history remain verifiable after grants change. No routes or permission seeds.
"""
from pathlib import Path
import hashlib
import runpy
from alembic import op

revision = '20261202_0153'
down_revision = '20261201_0152'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261201_0152_stock_loss_derived_returns.py'))
ready = previous['ready']
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()

AUTHORITY_SOURCE = runpy.run_path(str(FOLDER/'20261124_0145_stock_loss_submission_proof.py'))['AUTHORITY_BODY']
assert hashlib.sha256(AUTHORITY_SOURCE.encode()).hexdigest() == 'e8553433927bbee4ee59618552c5609ea61bcd0b9001a806e8e0df4dc164b84a'

def replace_once(value, old, new):
    assert value.count(old) == 1, old
    return value.replace(old, new)

AUTHORITY_BODY = AUTHORITY_SOURCE.replace('submit_loss', 'outbound_return').replace('0145', '0153 loss outbound')
AUTHORITY_BODY = replace_once(AUTHORITY_BODY,
    'SELECT * INTO location FROM public.stock_locations WHERE id=location_id FOR SHARE;',
    'SELECT * INTO location FROM public.stock_locations WHERE id=location_id FOR UPDATE;')
AUTHORITY_BODY = replace_once(AUTHORITY_BODY,
    "AND a.custodian_person_id=person_id AND a.valid_from<=checked_at\n        AND (a.valid_to IS NULL OR a.valid_to>checked_at))<>1 THEN",
    "AND a.valid_from<=checked_at\n        AND (a.valid_to IS NULL OR a.valid_to>checked_at))<>1\n"
    "       OR NOT EXISTS(SELECT 1 FROM public.custody_assignments a WHERE a.location_id=location.id\n"
    "           AND a.custodian_person_id=person_id AND a.valid_from<=checked_at\n"
    "           AND (a.valid_to IS NULL OR a.valid_to>checked_at)) THEN")


PREVIOUS = runpy.run_path(str(FOLDER / '20261015_0105_stock_return_receipts.py'))
SIGNATURE = 'public.rsc_check_stock_return_outbound_0103(uuid)'
BASE = PREVIOUS['_sources']()[SIGNATURE][1]
assert hashlib.sha256(BASE.encode()).hexdigest() == '934eda6927ccb46fe3ffd627334444006fbf6b989b491c0b18df6ea1d35b41f9'
NAME = 'rsc_check_loss_outbound_0153'

PROVENANCE = """
    SELECT * INTO disposition FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id;
    IF disposition.id IS NULL OR parent.operation_type<>'return' OR parent.oam_work_order_id IS NOT NULL
       OR parent.loss_headquarters_decision_id IS DISTINCT FROM disposition.headquarters_decision_id
       OR disposition.disposition<>'return_to_region'
       OR (SELECT count(*) FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id)<>1 THEN
        RAISE EXCEPTION 'loss outbound exact derived origin required' USING ERRCODE='23514'; END IF;
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
    IF departure.plan_jsonb->'origin' IS DISTINCT FROM wanted_origin
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(departure.plan_jsonb) key)
          IS DISTINCT FROM ARRAY['authorization_version','destination','intent','ledger_cursor','lines',
                'origin','original_request_hash','policies']::text[] THEN
        RAISE EXCEPTION 'loss outbound exact provenance snapshot required' USING ERRCODE='23514'; END IF;
    IF require_current THEN
        PERFORM public.rsc_assert_loss_outbound_authority_0153(departure.actor_user_id,departure.authorization_version,
            departure.operator_person_id,parent.source_location_id);
        PERFORM id FROM public.stock_locations WHERE id IN (parent.target_location_id,parent.transit_location_id)
            ORDER BY id FOR UPDATE;
        PERFORM id FROM public.custody_assignments WHERE location_id=parent.target_location_id ORDER BY id FOR SHARE;
        IF (SELECT count(*) FROM public.custody_assignments a WHERE a.location_id=parent.target_location_id
            AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp()))<>1
           OR NOT EXISTS(SELECT 1 FROM public.custody_assignments a WHERE a.id=departure.target_custody_assignment_id
            AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())) THEN
            RAISE EXCEPTION 'loss outbound current receiving custody required' USING ERRCODE='23514'; END IF;
    END IF;
"""

BODY = replace_once(BASE, '    reference text;',
    '    reference text; disposition public.stock_loss_dispositions%ROWTYPE; wanted_origin jsonb;')
BODY = replace_once(BODY, '    SELECT * INTO tx FROM public.inventory_transactions WHERE id=departure.posting_transaction_id;',
    '    SELECT * INTO tx FROM public.inventory_transactions WHERE id=departure.posting_transaction_id;' + PROVENANCE)
BODY = replace_once(BODY, 'OR departure.actor_user_id <> parent.actor_user_id OR departure.operator_person_id <> parent.requester_id',
    'OR departure.operator_person_id <> parent.requester_id')
BODY = replace_once(BODY, "AND actor.authorization_version=departure.authorization_version AND actor.is_active\n              AND actor.account_status='active' AND person.employment_status='active'", 'AND person.id=parent.requester_id')
BODY = replace_once(BODY, "AND source_location.status='active' AND source_location.custodian_person_id=departure.operator_person_id\n          AND source_location.parent_id=destination.id AND source_location.owner_org_id=destination.owner_org_id\n          AND destination.location_type='region' AND destination.status='active'\n          AND transit.location_type='transit' AND transit.status='active' AND transit.parent_id=destination.id\n          AND transit.owner_org_id=destination.owner_org_id AND assignment.location_id=destination.id\n          AND assignment.custodian_person_id=destination.custodian_person_id", "AND assignment.location_id=destination.id\n          AND (NOT require_current OR (source_location.status='active'\n            AND source_location.custodian_person_id=departure.operator_person_id\n            AND source_location.parent_id=destination.id AND source_location.owner_org_id=destination.owner_org_id\n            AND destination.location_type='region' AND destination.status='active'\n            AND transit.location_type='transit' AND transit.status='active' AND transit.parent_id=destination.id\n            AND transit.owner_org_id=destination.owner_org_id\n            AND assignment.custodian_person_id=destination.custodian_person_id))")
BODY = replace_once(BODY, "OR view->>'source_recovery_line_id' IS DISTINCT FROM origin.source_recovery_line_id::text",
    "OR origin.source_recovery_line_id IS NOT NULL OR origin.source_loss_line_id IS DISTINCT FROM disposition.line_id\n"
    "           OR view ? 'source_recovery_line_id'\n"
    "           OR view->>'source_loss_line_id' IS DISTINCT FROM disposition.line_id::text")
BODY = replace_once(BODY, "'work_order_id',parent.oam_work_order_id::text,",
    "'origin_kind','loss_report','loss_operation_id',disposition.operation_id::text,\n"
    "        'loss_line_id',disposition.line_id::text,'headquarters_decision_id',disposition.headquarters_decision_id::text,\n"
    "        'loss_disposition_id',disposition.id::text,")
BODY = replace_once(BODY, "        view := departure.plan_jsonb->'lines'->(ordinal-1);", """        view := departure.plan_jsonb->'lines'->(ordinal-1);
        IF jsonb_typeof(view) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'loss outbound line snapshot object required' USING ERRCODE='23514'; END IF;
        IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(view) key) IS DISTINCT FROM
            ARRAY['base_unit','condition_code','departed_quantity','held_quantity','lot_id','lot_no','material_id',
                'material_name','operation_line_id','remaining_quantity','return_quantity','selected_quantity',
                'selected_serials','sku_code','source_loss_line_id','source_stock_account_id']::text[] THEN
            RAISE EXCEPTION 'loss outbound exact line snapshot keys required' USING ERRCODE='23514'; END IF;
        IF require_current AND NOT EXISTS(SELECT 1 FROM public.materials m
            LEFT JOIN public.inventory_lots lot ON lot.id=source.lot_id
            WHERE m.id=source.material_id AND view->>'sku_code'=m.sku_code
              AND view->>'material_name'=m.name AND view->>'base_unit'=m.base_unit
              AND view->>'lot_no' IS NOT DISTINCT FROM lot.lot_no) THEN
            RAISE EXCEPTION 'loss outbound current material snapshot mismatch' USING ERRCODE='23514'; END IF;
""")
BODY = replace_once(BODY, '    IF require_current THEN\n', """    IF jsonb_typeof(departure.plan_jsonb->'destination') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'loss outbound route object required' USING ERRCODE='23514'; END IF;
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(departure.plan_jsonb->'destination') key)
        IS DISTINCT FROM ARRAY['custodian_person_id','custody_assignment_id','custody_effective_from','region_org_id',
            'source_location_id','target_location_code','target_location_id','target_location_name',
            'transit_location_code','transit_location_id','transit_location_name']::text[]
       OR NOT EXISTS(SELECT 1 FROM public.custody_assignments a WHERE a.id=departure.target_custody_assignment_id
            AND (departure.plan_jsonb->'destination'->>'custody_effective_from')::timestamptz=a.valid_from) THEN
        RAISE EXCEPTION 'loss outbound exact route snapshot required' USING ERRCODE='23514'; END IF;
    IF require_current THEN
        IF NOT EXISTS(SELECT 1 FROM public.stock_locations route_target
            JOIN public.stock_locations route_transit ON route_transit.id=parent.transit_location_id
            WHERE route_target.id=parent.target_location_id
              AND departure.plan_jsonb->'destination'->>'region_org_id'=route_target.owner_org_id::text
              AND departure.plan_jsonb->'destination'->>'target_location_code'=route_target.code
              AND departure.plan_jsonb->'destination'->>'target_location_name'=route_target.name
              AND departure.plan_jsonb->'destination'->>'transit_location_code'=route_transit.code
              AND departure.plan_jsonb->'destination'->>'transit_location_name'=route_transit.name) THEN
            RAISE EXCEPTION 'loss outbound current route snapshot mismatch' USING ERRCODE='23514'; END IF;
""")
# Validate mutable route labels after the route parent locks are acquired.
label_start = BODY.index("        IF NOT EXISTS(SELECT 1 FROM public.stock_locations route_target")
label_end_text = "RAISE EXCEPTION 'loss outbound current route snapshot mismatch' USING ERRCODE='23514'; END IF;"
label_end = BODY.index(label_end_text, label_start) + len(label_end_text)
route_labels = BODY[label_start:label_end]
BODY = BODY[:label_start] + BODY[label_end:]
BODY = replace_once(BODY,
    "PERFORM id FROM public.custody_assignments WHERE location_id=parent.target_location_id ORDER BY id FOR SHARE;",
    "PERFORM id FROM public.custody_assignments WHERE location_id=parent.target_location_id ORDER BY id FOR SHARE;\n" + route_labels)

NOTIFICATION = """
    IF (SELECT count(*) FROM public.notification_events WHERE business_type='stock_operation_outbound'
        AND business_id=departure.id::text)<>1
       OR NOT EXISTS(SELECT 1 FROM public.notification_events event
        JOIN public.custody_assignments assignment ON assignment.id=departure.target_custody_assignment_id
        WHERE event.business_type='stock_operation_outbound' AND event.business_id=departure.id::text
          AND event.event_type='stock_return_outbound'
          AND event.dedup_key='stock-return-notification:stock_return_outbound:'||departure.id::text
          AND event.payload_jsonb=body||jsonb_build_object('target_custody_assignment_id',assignment.id::text)
          AND event.occurred_at=departure.created_at AND event.created_at=departure.created_at
          AND event.target_manifest_sha256=encode(sha256(convert_to(
            'notification-person-targets.v1'||chr(10)||assignment.custodian_person_id::text,'UTF8')),'hex')
          AND (SELECT count(*) FROM public.notification_person_targets WHERE event_id=event.id)=1
          AND EXISTS(SELECT 1 FROM public.notification_person_targets
            WHERE event_id=event.id AND person_id=assignment.custodian_person_id)) THEN
        RAISE EXCEPTION 'loss outbound exact notification intent required' USING ERRCODE='23514'; END IF;
"""
BODY = replace_once(BODY, '\nEND;\n', NOTIFICATION + '\nEND;\n')

DISPATCHED = replace_once(BASE, 'BEGIN\n', f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_outbounds o JOIN public.stock_operation_orders p
        ON p.id=o.operation_id WHERE o.id=checked_outbound AND p.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(checked_outbound,false);
        RETURN;
    END IF;
''')

INSERT_GUARD = f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE id=NEW.operation_id
        AND loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(NEW.id,true);
    END IF;
    RETURN NULL;
END;'''

NOTIFICATION_GUARD = f'''DECLARE event public.notification_events%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
    ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
    IF EXISTS(SELECT 1 FROM public.stock_operation_outbounds o JOIN public.stock_operation_orders p ON p.id=o.operation_id
        WHERE o.id::text=event.business_id AND p.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(event.business_id::uuid,false);
    END IF;
    RETURN NULL;
END;'''

FUNCTIONS = {
    ('rsc_assert_loss_outbound_authority_0153', 'text, bigint, uuid, uuid'):
        ('actor_id text, actor_version bigint, person_id uuid, location_id uuid', 'void', AUTHORITY_BODY),
    (NAME, 'uuid, boolean'): ('checked_outbound uuid, require_current boolean', 'void', BODY),
    ('rsc_guard_loss_outbound_insert_0153', ''): ('', 'trigger', INSERT_GUARD),
    ('rsc_guard_loss_outbound_notification_0153', ''): ('', 'trigger', NOTIFICATION_GUARD),
}
FUNCTION_HASHES = {key: hashlib.sha256(value[2].encode()).hexdigest() for key, value in FUNCTIONS.items()}
OLD_HASH = hashlib.sha256(BASE.encode()).hexdigest()
NEW_HASH = hashlib.sha256(DISPATCHED.encode()).hexdigest()
TRIGGERS = {
    'trg_loss_outbound_current_0153': ('stock_operation_outbounds', 'rsc_guard_loss_outbound_insert_0153'),
    'trg_loss_outbound_notification_0153': ('notification_events', 'rsc_guard_loss_outbound_notification_0153'),
    'trg_loss_outbound_target_0153': ('notification_person_targets', 'rsc_guard_loss_outbound_notification_0153'),
}
RETAINED = '''EXISTS(SELECT 1 FROM stock_operation_outbounds o
    JOIN stock_operation_orders p ON p.id=o.operation_id
    WHERE p.loss_headquarters_decision_id IS NOT NULL)'''


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
            THEN RAISE EXCEPTION '0153 loss outbound trigger catalog drift'; END IF; END $$""")


def _transition(up):
    helper = runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0153 PostgreSQL or SQLite required')
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0153 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_operation_orders,public.stock_operation_lines,public.stock_loss_dispositions,public.stock_operation_outbounds,public.stock_operation_outbound_lines,public.stock_operation_outbound_serials,public.stock_locations,public.custody_assignments,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
    if not up:
        helper['_preflight'](RETAINED, '0153 loss outbound history requires retention')
    if dialect == 'sqlite':
        if up:
            op.execute("""CREATE TRIGGER trg_loss_outbound_block_0153 BEFORE INSERT ON stock_operation_outbounds
                WHEN EXISTS(SELECT 1 FROM stock_operation_orders WHERE id=NEW.operation_id
                    AND loss_headquarters_decision_id IS NOT NULL)
                BEGIN SELECT RAISE(ABORT,'0153 PostgreSQL loss outbound proof required'); END""")
        else:
            op.execute('DROP TRIGGER trg_loss_outbound_block_0153')
        return
    verify = runpy.run_path(str(FOLDER/'20261125_0146_stock_loss_request_seals.py'))['_verify_function']
    verify('rsc_check_stock_return_outbound_0103', 'uuid', 'checked_outbound uuid', 'void', BASE if up else DISPATCHED)
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
        replacements=((BASE, DISPATCHED),) if up else ((DISPATCHED, BASE),), label='loss_outbound_0153')
    verify('rsc_check_stock_return_outbound_0103', 'uuid', 'checked_outbound uuid', 'void', DISPATCHED if up else BASE)
    if up:
        op.execute(f'''DO $$ DECLARE identifier uuid; BEGIN
            FOR identifier IN SELECT o.id FROM public.stock_operation_outbounds o
                JOIN public.stock_operation_orders p ON p.id=o.operation_id
                WHERE p.loss_headquarters_decision_id IS NOT NULL LOOP
                PERFORM public.{NAME}(identifier,false);
            END LOOP; END $$''')
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
        expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
        replacements=((down_revision, revision),) if up else ((revision, down_revision),), label='loss_outbound_ready_0153')
    if not up:
        for name, (table, _) in TRIGGERS.items():
            op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name, signature in reversed(FUNCTIONS):
            op.execute(f'DROP FUNCTION public.{name}({signature})')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
