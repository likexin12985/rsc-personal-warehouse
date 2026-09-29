"""Complete loss-origin acceptance, independent inbound and receipt recovery.

Ordinary work-order proofs remain intact. Loss facts bind exact provenance,
current COMMIT authority and immutable historical evidence independently.
"""
from pathlib import Path
import hashlib
import runpy
import sqlalchemy as sa
from alembic import op

revision = '20261204_0155'
down_revision = '20261203_0154'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261203_0154_stock_loss_return_shipments.py'))
ready = previous['ready']
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
receipts = previous['receipts']
replace = previous['replace']
BASE = receipts['CHECK_BODY']
assert hashlib.sha256(BASE.encode()).hexdigest() == '3aaed71163eb5cf6b8478dda95de8bee099b70206362af38167f6aee97e5538c'
NAME = 'rsc_check_loss_receipt_0155'
ORIGIN = """
    SELECT * INTO disposition FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id;
    IF disposition.id IS NULL OR parent.oam_work_order_id IS NOT NULL
       OR parent.loss_headquarters_decision_id IS DISTINCT FROM disposition.headquarters_decision_id
       OR disposition.disposition<>'return_to_region'
       OR (SELECT count(*) FROM public.stock_loss_dispositions WHERE return_operation_id=parent.id)<>1 THEN
        RAISE EXCEPTION '0155 exact loss receipt origin required' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_check_loss_shipment_0154(parcel.id,false);
    wanted_origin:=jsonb_build_object('origin_kind','loss_report','loss_operation_id',disposition.operation_id::text,
        'loss_line_id',disposition.line_id::text,'headquarters_decision_id',disposition.headquarters_decision_id::text,
        'disposition_id',disposition.id::text);
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(fact.plan_jsonb) key)
       IS DISTINCT FROM ARRAY['audit_cursor','authorization_version','evidence','intent','ledger_cursor','lines','package','policies']::text[] THEN
        RAISE EXCEPTION '0155 exact receipt snapshot fields required' USING ERRCODE='23514'; END IF;
    IF NOT EXISTS(SELECT 1 FROM public.custody_assignments custody
        WHERE custody.id=fact.target_custody_assignment_id AND custody.location_id=shipment.target_location_id
          AND custody.custodian_person_id=fact.operator_person_id AND shipment.target_person_id=fact.operator_person_id
          AND custody.valid_from<=header.received_at AND (custody.valid_to IS NULL OR custody.valid_to>fact.created_at)) THEN
        RAISE EXCEPTION '0155 receipt historical custody mismatch' USING ERRCODE='23514'; END IF;
"""
BODY = replace(BASE, '    fact public.stock_operation_receipts%ROWTYPE;',
    '    fact public.stock_operation_receipts%ROWTYPE; disposition public.stock_loss_dispositions%ROWTYPE; wanted_origin jsonb;')
BODY = replace(BODY,'    SELECT * INTO location FROM public.stock_locations WHERE id=shipment.target_location_id;',
    '    SELECT * INTO location FROM public.stock_locations WHERE id=shipment.target_location_id;'+ORIGIN)
BODY = replace(BODY,'    PERFORM public.rsc_check_return_receiver_0105(parcel.id,fact.actor_user_id,fact.operator_person_id,fact.authorization_version,fact.created_at);',
    '    IF require_current THEN PERFORM public.rsc_check_return_receiver_0105(parcel.id,fact.actor_user_id,fact.operator_person_id,fact.authorization_version,fact.created_at); END IF;')
BODY = replace(BODY,"OR cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')",
    "OR cursor_value>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory')\n       OR (require_current AND cursor_value<>(SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory'))")
# Both the package projection and receipt audit identify the exact loss source.
assert BODY.count("'work_order_id',parent.oam_work_order_id::text,") == 2
BODY = BODY.replace("'work_order_id',parent.oam_work_order_id::text,", "'origin',wanted_origin,")
BODY = replace(BODY,"'target_location_name',location.name", "'target_location_name',CASE WHEN require_current THEN location.name ELSE fact.plan_jsonb->'package'->>'target_location_name' END")
BODY = replace(BODY,"NOT sn.sku_verified OR NOT sn.qr_verified OR serial.lifecycle_status<>'active'\n                        OR position.stock_account_id IS DISTINCT FROM account.id",
    "NOT sn.sku_verified OR NOT sn.qr_verified\n                        OR (require_current AND (serial.lifecycle_status<>'active' OR position.stock_account_id IS DISTINCT FROM account.id))\n                        OR (SELECT movement.to_account_id FROM public.inventory_movements movement\n                            JOIN public.inventory_movement_serials selected ON selected.movement_id=movement.id\n                            JOIN public.inventory_transactions tx ON tx.id=movement.transaction_id\n                            WHERE selected.serial_id=sn.serial_id AND tx.ledger_cursor<=cursor_value\n                            ORDER BY tx.ledger_cursor DESC,movement.line_no DESC LIMIT 1) IS DISTINCT FROM account.id")
NOTIFICATION = previous['outbound']['NOTIFICATION'].replace('stock_operation_outbound','stock_operation_receipt').replace('stock_return_outbound','stock_return_received').replace('departure.','fact.').replace('loss outbound','loss receipt')
BODY = replace(BODY,'\nEND;\n',NOTIFICATION+'\nEND;\n')
DISPATCHED = replace(BASE,'BEGIN\n',f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_receipts r
        JOIN public.stock_operation_shipments p ON p.id=r.shipment_id
        JOIN public.stock_operation_orders o ON o.id=p.operation_id
        WHERE r.id=checked_receipt AND o.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(checked_receipt,false); RETURN;
    END IF;
''')

# Keep all existing seal checks; add an exclusive, exact loss-source alternative.
SEAL_BASE = runpy.run_path(str(FOLDER/'20261102_0123_seal_audit_lock_scope.py'))['_sources']()['public.rsc_guard_stock_operation_seal_0101()'][1]
SEAL_BODY = replace(SEAL_BASE,
    '       OR NOT EXISTS (SELECT 1 FROM public.oam_work_orders wo WHERE wo.id=seal.oam_work_order_id)',
    "       OR NOT ((seal.source_loss_disposition_id IS NULL AND seal.oam_work_order_id IS NOT NULL\n            AND EXISTS(SELECT 1 FROM public.oam_work_orders wo WHERE wo.id=seal.oam_work_order_id))\n          OR (seal.source_loss_disposition_id IS NOT NULL AND seal.oam_work_order_id IS NULL AND seal.operation_type='receive_return'))")
SEAL_BODY = replace(SEAL_BODY,'AND parent.oam_work_order_id=seal.oam_work_order_id) THEN',
    "AND ((seal.source_loss_disposition_id IS NULL AND parent.oam_work_order_id=seal.oam_work_order_id)\n              OR (parent.oam_work_order_id IS NULL AND EXISTS(SELECT 1 FROM public.stock_loss_dispositions d\n                  WHERE d.id=seal.source_loss_disposition_id AND d.return_operation_id=parent.id\n                    AND d.headquarters_decision_id=parent.loss_headquarters_decision_id AND d.disposition='return_to_region')))) THEN")
SEAL_BODY = replace(SEAL_BODY,"    IF seal.operation_type='receive_return' THEN\n", "    IF seal.operation_type='receive_return' THEN\n        IF seal.source_loss_disposition_id IS NOT NULL THEN\n            PERFORM public.rsc_check_loss_shipment_0154(seal.shipment_id,false);\n        END IF;\n")
SEAL_BODY = replace(SEAL_BODY,"    IF seal.operation_type='receive_return' THEN body:=body || jsonb_build_object('shipment_id',seal.shipment_id::text); END IF;",
    "    IF seal.operation_type='receive_return' THEN body:=body || jsonb_build_object('shipment_id',seal.shipment_id::text); END IF;\n    IF seal.source_loss_disposition_id IS NOT NULL THEN\n        body:=(body-'work_order_id')||jsonb_build_object('source_loss_disposition_id',seal.source_loss_disposition_id::text);\n    END IF;")

# Existing inbound proof already binds every accepted line and exact posting.
# Prove the loss receipt history first; ordinary inbounds cannot use new stock.
inbound = runpy.run_path(str(FOLDER/'20261021_0111_stock_return_inbound_proof.py'))
INBOUND_BASE = inbound['CHECK_BODY']
INBOUND_BODY = replace(INBOUND_BASE,'    SELECT * INTO receipt FROM public.stock_operation_receipts WHERE id=fact.receipt_id;',
    '''    SELECT * INTO receipt FROM public.stock_operation_receipts WHERE id=fact.receipt_id;
    IF EXISTS(SELECT 1 FROM public.stock_operation_shipments parcel
        JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
        WHERE parcel.id=fact.shipment_id AND parent.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.rsc_check_loss_receipt_0155(fact.receipt_id,false);
    ELSIF EXISTS(SELECT 1 FROM public.stock_operation_return_inbound_lines
        WHERE inbound_id=fact.id AND condition_code='new') THEN
        RAISE EXCEPTION '0155 new-condition inbound requires exact loss receipt' USING ERRCODE='23514';
    END IF;''')
INBOUND_NOTIFICATION = NOTIFICATION.replace('stock_operation_receipt','stock_operation_return_inbound').replace('stock_return_received','stock_return_inbound_posted').replace('loss receipt','loss inbound')
INBOUND_BODY = replace(INBOUND_BODY,'\nEND;\n', "\n    IF receipt.plan_jsonb->'package'->'origin'->>'origin_kind'='loss_report' THEN\n"+INBOUND_NOTIFICATION+"\n    END IF;\nEND;\n")
INSERT_GUARD = f'''BEGIN
    IF EXISTS(SELECT 1 FROM public.stock_operation_shipments parcel
        JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
        WHERE parcel.id=NEW.shipment_id AND parent.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(NEW.id,true);
    END IF;
    RETURN NULL;
END;'''
NOTIFICATION_GUARD = f'''DECLARE event public.notification_events%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='notification_events' THEN event:=NEW;
    ELSE SELECT * INTO event FROM public.notification_events WHERE id=NEW.event_id; END IF;
    IF event.business_type='stock_operation_receipt' AND EXISTS(
        SELECT 1 FROM public.stock_operation_receipts receipt
        JOIN public.stock_operation_shipments parcel ON parcel.id=receipt.shipment_id
        JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
        WHERE receipt.id::text=event.business_id AND parent.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.{NAME}(event.business_id::uuid,false);
    ELSIF event.business_type='stock_operation_return_inbound' AND EXISTS(
        SELECT 1 FROM public.stock_operation_return_inbounds inbound
        JOIN public.stock_operation_shipments parcel ON parcel.id=inbound.shipment_id
        JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
        WHERE inbound.id::text=event.business_id AND parent.loss_headquarters_decision_id IS NOT NULL) THEN
        PERFORM public.rsc_check_stock_return_inbound_0111(event.business_id::uuid);
    END IF;
    RETURN NULL;
END;'''
FUNCTIONS = {
    (NAME,'uuid, boolean'):('checked_receipt uuid, require_current boolean','void',BODY),
    ('rsc_guard_loss_receipt_insert_0155',''):('','trigger',INSERT_GUARD),
    ('rsc_guard_loss_receipt_notification_0155',''):('','trigger',NOTIFICATION_GUARD),
}
FUNCTION_HASHES = {key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
PATCHES = {
    ('rsc_check_stock_return_receipt_0105','uuid'):('checked_receipt uuid','void',BASE,DISPATCHED),
    ('rsc_guard_stock_operation_seal_0101',''):('','trigger',SEAL_BASE,SEAL_BODY),
    ('rsc_check_stock_return_inbound_0111','uuid'):('checked_inbound uuid','void',INBOUND_BASE,INBOUND_BODY),
}
# Admit a first new-condition regional account only for the exact loss parcel.
ACCOUNT_KEY = ('rsc_require_opening_observation_account_0023','')
ACCOUNT_BASE = runpy.run_path(str(FOLDER/'20261201_0152_stock_loss_derived_returns.py'))['_sources']()[
    'public.rsc_require_opening_observation_account_0023()'][1]
assert hashlib.sha256(ACCOUNT_BASE.encode()).hexdigest() == 'b8ae67ff22df17c599eb72c9d7cf584bc348ed6cfc678085d4983c347761113b'
ACCOUNT_BODY = replace(ACCOUNT_BASE, "          AND NEW.condition_code IN ('used', 'damaged')", """          AND (NEW.condition_code IN ('used', 'damaged') OR (NEW.condition_code='new' AND EXISTS(
              SELECT 1 FROM public.stock_operation_shipments loss_parcel
              JOIN public.stock_operation_orders loss_order ON loss_order.id=loss_parcel.operation_id
              JOIN public.stock_loss_dispositions loss_source ON loss_source.return_operation_id=loss_order.id
                AND loss_source.headquarters_decision_id=loss_order.loss_headquarters_decision_id
              WHERE loss_parcel.id=receipt.shipment_id AND loss_order.oam_work_order_id IS NULL
                AND loss_source.disposition='return_to_region'
                AND receipt.plan_jsonb->'package'->'origin'=jsonb_build_object(
                    'origin_kind','loss_report','loss_operation_id',loss_source.operation_id::text,
                    'loss_line_id',loss_source.line_id::text,'headquarters_decision_id',loss_source.headquarters_decision_id::text,
                    'disposition_id',loss_source.id::text))))""")
PATCHES[ACCOUNT_KEY] = ('','trigger',ACCOUNT_BASE,ACCOUNT_BODY)
TRIGGERS = {
    'trg_loss_receipt_current_0155':('stock_operation_receipts','rsc_guard_loss_receipt_insert_0155'),
    'trg_loss_receipt_notification_0155':('notification_events','rsc_guard_loss_receipt_notification_0155'),
    'trg_loss_receipt_target_0155':('notification_person_targets','rsc_guard_loss_receipt_notification_0155'),
}
RETAINED = """EXISTS(SELECT 1 FROM stock_operation_receipts receipt
    JOIN stock_operation_shipments parcel ON parcel.id=receipt.shipment_id
    JOIN stock_operation_orders parent ON parent.id=parcel.operation_id
    WHERE parent.loss_headquarters_decision_id IS NOT NULL)
    OR EXISTS(SELECT 1 FROM stock_operation_command_seals WHERE source_loss_disposition_id IS NOT NULL)"""
SOURCE_CHECK = "(oam_work_order_id IS NOT NULL AND source_loss_disposition_id IS NULL) OR (oam_work_order_id IS NULL AND source_loss_disposition_id IS NOT NULL AND operation_type='receive_return')"


def _sources():
    return {f'public.{name}({signature})': (old,new)
        for (name,signature),(_,_,old,new) in PATCHES.items()}


def _schema(up):
    db=op.get_bind()
    saved=()
    if db.dialect.name=='sqlite':
        if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0155 SQLite migration requires foreign_keys OFF before the migration transaction')
        # Preserve guards on rebuilt tables and guards on other tables that
        # refer to them; SQLite validates those references during table rename.
        saved=tuple(db.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND "
            "(instr(lower(sql),'stock_operation_command_seals')>0 OR "
            "instr(lower(sql),'stock_operation_return_inbound_lines')>0) ORDER BY name")))
        for name,_ in saved:db.exec_driver_sql('DROP TRIGGER '+db.dialect.identifier_preparer.quote(name))
    with op.batch_alter_table('stock_operation_command_seals') as batch:
        if up:
            batch.add_column(sa.Column('source_loss_disposition_id',sa.Uuid(),nullable=True))
            batch.create_foreign_key('fk_stock_operation_seals_loss','stock_loss_dispositions',['source_loss_disposition_id'],['id'],ondelete='RESTRICT')
            batch.alter_column('oam_work_order_id',existing_type=sa.Uuid(),nullable=True)
            batch.create_check_constraint('ck_stock_operation_seals_source',SOURCE_CHECK)
        else:
            batch.drop_constraint('ck_stock_operation_seals_source',type_='check')
            batch.drop_constraint('fk_stock_operation_seals_loss',type_='foreignkey')
            batch.drop_column('source_loss_disposition_id')
            batch.alter_column('oam_work_order_id',existing_type=sa.Uuid(),nullable=False)
    with op.batch_alter_table('stock_operation_return_inbound_lines') as batch:
        batch.drop_constraint('ck_stock_operation_return_inbound_lines_context',type_='check')
        kinds="'new','used','damaged'" if up else "'used','damaged'"
        batch.create_check_constraint('ck_stock_operation_return_inbound_lines_context',f'line_no > 0 AND accepted_qty > 0 AND condition_code IN ({kinds})')
    for _,sql in saved:db.exec_driver_sql(sql)


def _verify_triggers():
    for name,(table,function) in TRIGGERS.items():
        op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint
            WHERE t.tgname='{name}' AND t.tgrelid='public.{table}'::regclass AND t.tgfoid='public.{function}()'::regprocedure
              AND t.tgtype=5 AND t.tgenabled='A' AND t.tgdeferrable AND t.tginitdeferred AND NOT t.tgisinternal
              AND t.tgnargs=0 AND t.tgqual IS NULL AND t.tgattr=''::int2vector AND t.tgconstrrelid=0
              AND c.contype='t' AND c.condeferrable AND c.condeferred) THEN
            RAISE EXCEPTION '0155 loss receipt trigger catalog drift'; END IF; END $$""")


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'): raise RuntimeError('0155 PostgreSQL or SQLite required')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0155 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_accounts,public.stock_balances,public.inventory_transactions,public.inventory_movements,public.stock_operation_return_inbound_postings,public.stock_operation_receipts,public.stock_operation_receipt_lines,public.stock_operation_receipt_serials,public.stock_operation_receipt_exceptions,public.stock_operation_command_seals,public.stock_operation_return_inbounds,public.stock_operation_return_inbound_lines,public.stock_operation_shipments,public.stock_loss_dispositions,public.audit_events,public.notification_events,public.notification_person_targets IN SHARE ROW EXCLUSIVE MODE')
    if not up: helper['_preflight'](RETAINED,'0155 loss receiving history requires retention')
    if dialect=='sqlite':
        if not up:
            op.execute('DROP TRIGGER trg_loss_receipt_block_0155')
            op.execute('DROP TRIGGER trg_loss_receipt_seal_block_0155')
            op.execute('DROP TRIGGER trg_loss_inbound_line_block_0155')
        _schema(up)
        if up:
            op.execute("""CREATE TRIGGER trg_loss_receipt_block_0155 BEFORE INSERT ON stock_operation_receipts
                WHEN EXISTS(SELECT 1 FROM stock_operation_shipments parcel JOIN stock_operation_orders parent ON parent.id=parcel.operation_id
                    WHERE parcel.id=NEW.shipment_id AND parent.loss_headquarters_decision_id IS NOT NULL)
                BEGIN SELECT RAISE(ABORT,'0155 PostgreSQL loss receipt proof required'); END""")
            op.execute("""CREATE TRIGGER trg_loss_receipt_seal_block_0155 BEFORE INSERT ON stock_operation_command_seals
                WHEN NEW.source_loss_disposition_id IS NOT NULL
                BEGIN SELECT RAISE(ABORT,'0155 PostgreSQL loss receipt seal proof required'); END""")
            op.execute("""CREATE TRIGGER trg_loss_inbound_line_block_0155 BEFORE INSERT ON stock_operation_return_inbound_lines
                WHEN NEW.condition_code='new'
                BEGIN SELECT RAISE(ABORT,'0155 PostgreSQL new-condition return inbound proof required'); END""")
        return
    verify=runpy.run_path(str(FOLDER/'20261125_0146_stock_loss_request_seals.py'))['_verify_function']
    for (name,sig),(args,result,old,new) in PATCHES.items(): verify(name,sig,args,result,old if up else new)
    if up:
        _schema(True)
        for (name,sig),(args,result,body) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({sig}) FROM PUBLIC,star_oam_api')
        for name,(table,function) in TRIGGERS.items():
            op.execute(f'CREATE CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{function}()')
            op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
    for (name,sig),(args,result,body) in FUNCTIONS.items(): verify(name,sig,args,result,body)
    _verify_triggers()
    patch=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    for (name,sig),(args,result,old,new) in PATCHES.items():
        patch(signature=f'public.{name}({sig})',expected_hash=hashlib.sha256((old if up else new).encode()).hexdigest(),
            replacement_hash=hashlib.sha256((new if up else old).encode()).hexdigest(),
            replacements=((old,new),) if up else ((new,old),),label='loss_receiving_0155')
        verify(name,sig,args,result,new if up else old)
    patch(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_receiving_ready_0155')
    if not up:
        for name,(table,_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name,sig in reversed(FUNCTIONS): op.execute(f'DROP FUNCTION public.{name}({sig})')
        _schema(False)


def upgrade(): _transition(True)
def downgrade(): _transition(False)
