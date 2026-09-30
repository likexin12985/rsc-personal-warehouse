"""Exact loss-derived outbound/shipment request seals; no new business writes."""
from pathlib import Path
import hashlib
import runpy
import sqlalchemy as sa
from alembic import op

revision='20261206_0157'
down_revision='20261205_0156'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
previous=runpy.run_path(str(FOLDER/'20261205_0156_stock_loss_review_request_seals.py'))
ready=previous['ready']
OLD_READY_HASH=previous['NEW_READY_HASH']
NEW_READY_HASH=hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
BASE=previous['previous']['SEAL_BODY']
assert hashlib.sha256(BASE.encode()).hexdigest()=='484989dddfd93a1b473f5418b03d7f5e55626ef535c953d96f6cb54a2307e226'
NAME='rsc_guard_stock_operation_seal_0101'
SIGNATURE='public.'+NAME+'()'
OLD_SOURCE_CHECK=previous['previous']['SOURCE_CHECK']
SOURCE_CHECK="(oam_work_order_id IS NOT NULL AND source_loss_disposition_id IS NULL) OR (oam_work_order_id IS NULL AND source_loss_disposition_id IS NOT NULL AND operation_type IN ('outbound_return','ship_return','receive_return'))"
RETAINED="EXISTS(SELECT 1 FROM stock_operation_command_seals WHERE source_loss_disposition_id IS NOT NULL AND operation_type IN ('outbound_return','ship_return'))"
BODY="""
DECLARE
    seal public.stock_operation_command_seals%ROWTYPE;
    observed_actor text;
    observed_request text;
    identifier uuid;
    body jsonb;
    loss_source_location uuid;
BEGIN
    -- Match the existing no-op audit branch before taking any ledger lock.
    -- Nested IF avoids reading audit-only fields on command/seal records.
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type <> 'stock_operation_command_seal' THEN RETURN NULL; END IF;
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0101 inventory ledger head missing' USING ERRCODE='23514'; END IF;
    IF TG_TABLE_NAME IN ('stock_operation_orders','stock_operation_cancellations','stock_operation_outbounds','stock_operation_shipments','stock_operation_receipts') THEN
        observed_actor := NEW.actor_user_id; observed_request := NEW.request_id;
        IF EXISTS (SELECT 1 FROM public.stock_operation_command_seals tombstone
            WHERE tombstone.actor_user_id=observed_actor AND tombstone.request_id=observed_request) THEN
            RAISE EXCEPTION '0101 sealed return request cannot execute' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    ELSIF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type <> 'stock_operation_command_seal' THEN RETURN NULL; END IF;
        identifier := NEW.aggregate_id::uuid;
    ELSE identifier := NEW.id;
    END IF;
    SELECT * INTO seal FROM public.stock_operation_command_seals WHERE id=identifier;
    IF NOT FOUND THEN RAISE EXCEPTION '0101 seal audit is detached' USING ERRCODE='23514'; END IF;
    IF seal.operation_type NOT IN ('submit_return','cancel_return','outbound_return','ship_return','receive_return')
       OR (seal.operation_type='receive_return') IS DISTINCT FROM (seal.shipment_id IS NOT NULL)
       OR (seal.operation_type='submit_return') IS DISTINCT FROM (seal.operation_id IS NULL)
       OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR seal.request_hash !~ '^[0-9a-f]{64}$'
       OR seal.request_reference <> 'inventory-request-' || encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(seal.request_id,'UTF8')),'hex')
       OR seal.authorization_version < 1 OR seal.created_at > clock_timestamp()
       OR NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.id=seal.actor_user_id
            AND actor.person_id=seal.operator_person_id AND actor.authorization_version=seal.authorization_version)
       OR NOT ((seal.source_loss_disposition_id IS NULL AND seal.oam_work_order_id IS NOT NULL
            AND EXISTS(SELECT 1 FROM public.oam_work_orders wo WHERE wo.id=seal.oam_work_order_id))
          OR (seal.source_loss_disposition_id IS NOT NULL AND seal.oam_work_order_id IS NULL AND seal.operation_type IN ('outbound_return','ship_return','receive_return')))
       OR (seal.operation_type IN ('cancel_return','outbound_return','ship_return') AND NOT EXISTS (SELECT 1 FROM public.stock_operation_orders parent
            WHERE parent.id=seal.operation_id AND parent.requester_id=seal.operator_person_id
              AND ((seal.source_loss_disposition_id IS NULL
                    AND parent.oam_work_order_id=seal.oam_work_order_id AND parent.actor_user_id=seal.actor_user_id)
                OR (seal.source_loss_disposition_id IS NOT NULL AND parent.oam_work_order_id IS NULL
                    AND EXISTS(SELECT 1 FROM public.stock_loss_dispositions disposition
                        WHERE disposition.id=seal.source_loss_disposition_id
                          AND disposition.return_operation_id=parent.id
                          AND disposition.headquarters_decision_id=parent.loss_headquarters_decision_id
                          AND disposition.disposition='return_to_region'))))) THEN
        RAISE EXCEPTION '0101 seal identity or original coordinates invalid' USING ERRCODE='23514';
    END IF;
    IF seal.source_loss_disposition_id IS NOT NULL
       AND seal.operation_type IN ('outbound_return','ship_return') THEN
        -- Re-prove historical HQ derivation, then require the current engineer's grant.
        PERFORM public.rsc_check_loss_return_0152(seal.source_loss_disposition_id,false);
        SELECT source_location_id INTO loss_source_location
            FROM public.stock_operation_orders WHERE id=seal.operation_id;
        IF seal.operation_type='outbound_return' THEN
            PERFORM public.rsc_assert_loss_outbound_authority_0153(seal.actor_user_id,seal.authorization_version,
                seal.operator_person_id,loss_source_location);
        ELSE
            PERFORM public.rsc_assert_loss_shipment_authority_0154(seal.actor_user_id,seal.authorization_version,
                seal.operator_person_id,loss_source_location);
        END IF;
    END IF;
    IF seal.operation_type='receive_return' THEN
        IF seal.source_loss_disposition_id IS NOT NULL THEN
            PERFORM public.rsc_check_loss_shipment_0154(seal.shipment_id,false);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.stock_operation_shipments parcel JOIN public.stock_operation_orders parent ON parent.id=parcel.operation_id
            WHERE parcel.id=seal.shipment_id AND parent.id=seal.operation_id AND ((seal.source_loss_disposition_id IS NULL AND parent.oam_work_order_id=seal.oam_work_order_id)
              OR (parent.oam_work_order_id IS NULL AND EXISTS(SELECT 1 FROM public.stock_loss_dispositions d
                  WHERE d.id=seal.source_loss_disposition_id AND d.return_operation_id=parent.id
                    AND d.headquarters_decision_id=parent.loss_headquarters_decision_id AND d.disposition='return_to_region')))) THEN
            RAISE EXCEPTION '0105 receiver seal original parcel mismatch' USING ERRCODE='23514';
        END IF;
        PERFORM public.rsc_check_return_receiver_0105(seal.shipment_id,seal.actor_user_id,seal.operator_person_id,seal.authorization_version,seal.created_at);
        IF EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND actor_user_id=seal.actor_user_id AND request_id=seal.request_reference)
           OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='inventory_transaction' AND actor_id=seal.actor_user_id
                AND metadata_jsonb->>'request_reference'=seal.request_reference) THEN
            RAISE EXCEPTION '0105 receipt seal has inventory evidence' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.stock_operation_orders parent
            WHERE parent.actor_user_id=seal.actor_user_id AND parent.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_cancellations cancellation
            WHERE cancellation.actor_user_id=seal.actor_user_id AND cancellation.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_outbounds departure
            WHERE departure.actor_user_id=seal.actor_user_id AND departure.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_shipments parcel
            WHERE parcel.actor_user_id=seal.actor_user_id AND parcel.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.stock_operation_receipts receipt
            WHERE receipt.actor_user_id=seal.actor_user_id AND receipt.request_id=seal.request_id)
       OR EXISTS (SELECT 1 FROM public.audit_events event WHERE event.actor_user_id=seal.actor_user_id
            AND event.stream_key='material_request' AND event.request_id=seal.request_id
            AND event.aggregate_type IN ('stock_operation_order','stock_operation_cancellation','stock_operation_outbound','stock_operation_shipment','stock_operation_receipt'))
       OR EXISTS (SELECT 1 FROM public.audit_events event JOIN public.inventory_transactions tx ON tx.id::text=event.aggregate_id
            WHERE event.stream_key='inventory' AND event.actor_user_id=seal.actor_user_id AND event.request_id=seal.request_reference
              AND event.aggregate_type='inventory_transaction' AND tx.source_document_type IN ('stock_operation_return','stock_operation_return_outbound'))
       OR EXISTS (SELECT 1 FROM public.state_transition_events event JOIN public.inventory_transactions tx ON tx.id::text=event.aggregate_id
            WHERE event.actor_id=seal.actor_user_id AND event.aggregate_type='inventory_transaction'
              AND event.metadata_jsonb->>'request_reference'=seal.request_reference AND tx.source_document_type IN ('stock_operation_return','stock_operation_return_outbound')) THEN
        RAISE EXCEPTION '0101 executed return request cannot be sealed' USING ERRCODE='23514';
    END IF;
    body := jsonb_build_object('work_order_id',seal.oam_work_order_id::text,'operator_person_id',seal.operator_person_id::text,
        'operation_id',seal.operation_id::text,'operation_type',seal.operation_type,'authorization_version',seal.authorization_version,
        'request_id',seal.request_id,'request_hash',seal.request_hash);
    IF seal.operation_type='receive_return' THEN body:=body || jsonb_build_object('shipment_id',seal.shipment_id::text); END IF;
    IF seal.source_loss_disposition_id IS NOT NULL THEN
        body:=(body-'work_order_id')||jsonb_build_object('source_loss_disposition_id',seal.source_loss_disposition_id::text);
    END IF;
    IF (SELECT count(*) FROM public.audit_events event WHERE event.stream_key='material_request'
            AND event.aggregate_type='stock_operation_command_seal' AND event.aggregate_id=seal.id::text) <> 1
       OR NOT EXISTS (SELECT 1 FROM public.audit_events event WHERE event.stream_key='material_request'
            AND event.aggregate_type='stock_operation_command_seal' AND event.aggregate_id=seal.id::text
            AND event.actor_user_id=seal.actor_user_id AND event.action='stock_return.command_sealed'
            AND event.request_id='stock-return-seal:' || seal.id::text AND event.before_jsonb='{}'::jsonb
            AND event.after_jsonb=body AND event.occurred_at=seal.created_at AND event.created_at=seal.created_at) THEN
        RAISE EXCEPTION '0101 complete seal audit required' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
"""

GUARD_TRIGGERS={
    'trg_stock_operation_seals_proof_0101':'stock_operation_command_seals',
    'trg_stock_operation_orders_seal_0101':'stock_operation_orders',
    'trg_stock_operation_cancellations_seal_0101':'stock_operation_cancellations',
    'trg_audit_events_stock_operation_seal_0101':'audit_events',
    'trg_stock_operation_outbounds_seal_0103':'stock_operation_outbounds',
    'trg_stock_operation_shipments_seal_0104':'stock_operation_shipments',
    'trg_stock_operation_receipts_seal_0105':'stock_operation_receipts',
}


def _sources():
    return {SIGNATURE:(BASE,BODY)}


def _schema(up):
    db=op.get_bind();saved=()
    if db.dialect.name=='sqlite':
        if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0157 SQLite requires foreign_keys OFF before migration')
        saved=tuple(db.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND instr(lower(sql),'stock_operation_command_seals')>0 ORDER BY name")))
        if 'trg_loss_receipt_seal_block_0155' not in {row[0] for row in saved}:
            raise RuntimeError('0157 SQLite loss-origin seal block guard missing')
        for name,_ in saved:db.exec_driver_sql('DROP TRIGGER '+db.dialect.identifier_preparer.quote(name))
    with op.batch_alter_table('stock_operation_command_seals') as batch:
        batch.drop_constraint('ck_stock_operation_seals_source',type_='check')
        batch.create_check_constraint('ck_stock_operation_seals_source',SOURCE_CHECK if up else OLD_SOURCE_CHECK)
    for _,sql in saved:db.exec_driver_sql(sql)


def _verify_triggers():
    for name,table in GUARD_TRIGGERS.items():
        op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint
            WHERE t.tgname='{name}' AND t.tgrelid='public.{table}'::regclass AND t.tgfoid='{SIGNATURE}'::regprocedure
              AND t.tgtype=5 AND t.tgenabled='A' AND t.tgdeferrable AND t.tginitdeferred AND NOT t.tgisinternal
              AND t.tgnargs=0 AND t.tgqual IS NULL AND t.tgattr=''::int2vector AND t.tgconstrrelid=0
              AND c.contype='t' AND c.condeferrable AND c.condeferred) THEN
            RAISE EXCEPTION '0157 return seal trigger catalog drift'; END IF; END $$""")


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0157 PostgreSQL or SQLite required')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0157 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        tables=tuple(sorted(set(GUARD_TRIGGERS.values())|{'inventory_ledger_heads','stock_loss_dispositions','stock_operation_lines','stock_operation_serials','inventory_transactions','inventory_movements'}))
        op.execute('LOCK TABLE '+','.join('public.'+table for table in tables)+' IN SHARE ROW EXCLUSIVE MODE')
    if not up:helper['_preflight'](RETAINED,'0157 immutable loss sender seals require retention')
    if dialect=='sqlite':
        _schema(up)
        return
    verify=runpy.run_path(str(FOLDER/'20261125_0146_stock_loss_request_seals.py'))['_verify_function']
    verify(NAME,'','','trigger',BASE if up else BODY)
    _verify_triggers()
    _schema(up)
    patch=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    old,new=(BASE,BODY) if up else (BODY,BASE)
    patch(signature=SIGNATURE,expected_hash=hashlib.sha256(old.encode()).hexdigest(),
        replacement_hash=hashlib.sha256(new.encode()).hexdigest(),replacements=((old,new),),label='loss_sender_seals_0157')
    verify(NAME,'','','trigger',new)
    _verify_triggers()
    patch(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
        replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_sender_seals_ready_0157')


def upgrade():_transition(True)
def downgrade():_transition(False)
