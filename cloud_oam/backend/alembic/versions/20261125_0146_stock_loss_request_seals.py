"""Immutable loss tombstones and bidirectional late-command exclusion.

Only an exact unresolved request can be sealed. No stock or notification is
created, no historical fact is rewritten, and no production permission seeded.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op
import sqlalchemy as sa

revision='20261125_0146'
down_revision='20261124_0145'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
previous=runpy.run_path(str(FOLDER/'20261124_0145_stock_loss_submission_proof.py'))
OLD_READY_HASH=previous['NEW_READY_HASH']
ready=previous['ready']
NEW_READY_HASH=hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
TABLE='stock_loss_request_seals'
REQUEST_TABLES=('stock_operation_orders','stock_operation_cancellations','stock_operation_outbounds',
    'stock_operation_shipments','stock_operation_receipts','stock_operation_return_inbounds',
    'stock_operation_command_seals','stock_operation_return_inbound_seals',TABLE)
CLAIMS=' UNION ALL '.join('SELECT actor_user_id,request_id FROM public.'+table for table in REQUEST_TABLES)

LOCK_BODY="""
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0146 loss seal requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0146 inventory ledger missing' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

CHECK_BODY="""
DECLARE seal public.stock_loss_request_seals%ROWTYPE; body jsonb;
BEGIN
    SELECT * INTO seal FROM public.stock_loss_request_seals WHERE id=checked_seal;
    IF seal.id IS NULL OR seal.authorization_version<1 OR seal.created_at>clock_timestamp()
       OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR seal.request_hash !~ '^[a-f0-9]{64}$' OR seal.plan_hash !~ '^[a-f0-9]{64}$'
       OR seal.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR seal.request_reference<>'inventory-request-' || encode(sha256(convert_to(
            'cloud_oam.inventory.request.v1','UTF8') || decode('00','hex') || convert_to(seal.request_id,'UTF8')),'hex') THEN
        RAISE EXCEPTION '0146 loss seal exact coordinates invalid' USING ERRCODE='23514'; END IF;
    PERFORM public.rsc_assert_loss_submit_authority_0145(seal.actor_user_id,seal.authorization_version,
        seal.operator_person_id,seal.source_location_id);
    -- Receipt/shipment acceptance does not acquire the inventory ledger. Its
    -- key fence uses this exact advisory lock; take it after principal locks.
    PERFORM pg_advisory_xact_lock(hashtextextended('cloud_oam.loss_request_key.v1:'||seal.idempotency_key_hash,0));
    IF (SELECT count(*) FROM (__CLAIMS__) fact
            WHERE fact.actor_user_id=seal.actor_user_id AND fact.request_id=seal.request_id)<>1
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=seal.idempotency_key_hash)
       OR EXISTS(SELECT 1 FROM public.receipts WHERE idempotency_key_hash=seal.idempotency_key_hash)
       OR EXISTS(SELECT 1 FROM public.shipments WHERE idempotency_key_hash=seal.idempotency_key_hash)
       OR EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE idempotency_key_hash=seal.idempotency_key_hash)
       OR EXISTS(SELECT 1 FROM public.audit_events e WHERE e.actor_user_id=seal.actor_user_id
            AND ((e.stream_key='inventory' AND e.request_id IN (seal.request_id,seal.request_reference))
              OR (e.stream_key='material_request' AND (e.request_id=seal.request_id
                OR (e.aggregate_type IN ('stock_operation_command_seal','stock_operation_return_inbound_seal')
                    AND e.after_jsonb->>'request_id'=seal.request_id)))))
       OR EXISTS(SELECT 1 FROM public.state_transition_events e WHERE e.actor_id=seal.actor_user_id
            AND e.aggregate_type='inventory_transaction' AND e.metadata_jsonb->>'request_reference'=seal.request_reference) THEN
        RAISE EXCEPTION '0146 executed or conflicting request cannot be sealed' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('operator_person_id',seal.operator_person_id::text,
        'source_location_id',seal.source_location_id::text,'authorization_version',seal.authorization_version,
        'request_id',seal.request_id,'idempotency_key_hash',seal.idempotency_key_hash,
        'request_hash',seal.request_hash,'plan_hash',seal.plan_hash);
    IF (SELECT count(*) FROM public.audit_events e WHERE e.stream_key='inventory'
            AND e.aggregate_type='stock_loss_request_seal' AND e.aggregate_id=seal.id::text)<>1
       OR (SELECT count(*) FROM public.audit_events e WHERE e.stream_key='inventory'
            AND e.aggregate_type='stock_loss_request_seal' AND e.actor_user_id=seal.actor_user_id
            AND e.after_jsonb->>'request_id'=seal.request_id)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events e WHERE e.stream_key='inventory'
            AND e.aggregate_type='stock_loss_request_seal' AND e.aggregate_id=seal.id::text
            AND e.actor_user_id=seal.actor_user_id AND e.action='stock_loss.request_sealed'
            AND e.request_id='stock-loss-seal:'||seal.id::text AND e.before_jsonb='{}'::jsonb
            AND e.after_jsonb=body AND e.occurred_at=seal.created_at AND e.created_at=seal.created_at) THEN
        RAISE EXCEPTION '0146 complete immutable loss seal audit required' USING ERRCODE='23514'; END IF;
END;
""".replace('__CLAIMS__',CLAIMS)

FENCE_BODY="""
DECLARE observed_actor text; observed_request text; observed_reference text; observed_key text;
    identifier uuid; seal public.stock_loss_request_seals%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME IN ('receipts','shipments') THEN
        IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION '0146 loss key fence requires read committed' USING ERRCODE='23514'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('cloud_oam.loss_request_key.v1:'||NEW.idempotency_key_hash,0));
        IF EXISTS(SELECT 1 FROM public.stock_loss_request_seals WHERE idempotency_key_hash=NEW.idempotency_key_hash) THEN
            RAISE EXCEPTION '0146 sealed loss key cannot execute' USING ERRCODE='23514'; END IF;
        RETURN NULL;
    END IF;
    -- Unrelated audit/state facts leave before taking the inventory ledger.
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type='stock_loss_request_seal' THEN
            identifier:=NEW.aggregate_id::uuid;
        ELSIF NEW.aggregate_type IN ('stock_operation_order','stock_operation_cancellation',
                'stock_operation_outbound','stock_operation_shipment','stock_operation_receipt',
                'stock_operation_return_inbound','stock_operation_command_seal',
                'stock_operation_return_inbound_seal','inventory_transaction') THEN
            observed_actor:=NEW.actor_user_id; observed_reference:=NEW.request_id;
            observed_request:=CASE WHEN NEW.aggregate_type IN ('stock_operation_command_seal',
                'stock_operation_return_inbound_seal') THEN NEW.after_jsonb->>'request_id' ELSE NEW.request_id END;
        ELSE RETURN NULL;
        END IF;
    ELSIF TG_TABLE_NAME='state_transition_events' THEN
        IF NEW.aggregate_type<>'inventory_transaction' THEN RETURN NULL; END IF;
        observed_actor:=NEW.actor_id; observed_reference:=NEW.metadata_jsonb->>'request_reference';
    ELSIF TG_TABLE_NAME='inventory_transactions' THEN
        observed_key:=NEW.idempotency_key_hash;
    ELSIF TG_TABLE_NAME='stock_loss_request_seals' THEN
        identifier:=NEW.id;
    ELSE
        observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id;
        IF TG_TABLE_NAME='stock_operation_orders' THEN observed_key:=NEW.idempotency_key_hash; END IF;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0146 loss fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0146 inventory ledger missing' USING ERRCODE='23514'; END IF;
    IF identifier IS NOT NULL THEN
        PERFORM public.rsc_check_loss_seal_0146(identifier);
        RETURN NULL;
    END IF;
    FOR seal IN SELECT * FROM public.stock_loss_request_seals s
        WHERE s.idempotency_key_hash=observed_key OR (s.actor_user_id=observed_actor
            AND (s.request_id=observed_request OR s.request_reference=observed_reference))
    LOOP
        RAISE EXCEPTION '0146 sealed loss request cannot execute' USING ERRCODE='23514';
    END LOOP;
    RETURN NULL;
END;
"""

FUNCTIONS={
    ('rsc_lock_loss_seal_0146',''):('','trigger',LOCK_BODY),
    ('rsc_check_loss_seal_0146','uuid'):('checked_seal uuid','void',CHECK_BODY),
    ('rsc_guard_loss_seal_0146',''):('','trigger',FENCE_BODY),
}
FUNCTION_HASHES={key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS={f'trg_{table}_loss_seal_0146':(table,'INSERT','rsc_guard_loss_seal_0146',5,True)
    for table in (*REQUEST_TABLES,'inventory_transactions','receipts','shipments','audit_events','state_transition_events')}
TRIGGERS.update({
    'trg_loss_seals_lock_0146':(TABLE,'INSERT','rsc_lock_loss_seal_0146',7,False),
    'trg_loss_seals_immutable_0146':(TABLE,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
    'trg_loss_seals_truncate_0146':(TABLE,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
})


def _schema():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('operator_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_location_id',sa.Uuid(),sa.ForeignKey('stock_locations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('request_reference',sa.String(100),nullable=False),
        sa.Column('idempotency_key_hash',sa.String(64),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('plan_hash',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_stock_loss_seals_request'),
        sa.UniqueConstraint('idempotency_key_hash',name='uq_stock_loss_seals_key'),
        sa.CheckConstraint('authorization_version > 0 AND length(request_hash)=64 AND length(plan_hash)=64 AND length(idempotency_key_hash)=64',name='ck_stock_loss_seals_context'),
        sa.CheckConstraint('length(request_id) BETWEEN 8 AND 160',name='ck_stock_loss_seals_request'))


def _verify_function(name,signature,args,result,body):
    digest=hashlib.sha256(body.encode()).hexdigest()
    op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({signature})'::regprocedure
        AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
        AND p.prosecdef AND p.provolatile='v' AND p.prokind='f' AND NOT p.proleakproof AND NOT p.proisstrict
        AND p.proparallel='u' AND p.prorettype='{result}'::regtype
        AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
        AND p.proconfig=ARRAY['search_path=pg_catalog, public']
        AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{digest}'
        AND NOT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner))
        THEN RAISE EXCEPTION '0146 loss seal function source ownership or ACL drift'; END IF; END $$""")


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0146 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0146 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        tables=tuple(table for table in dict.fromkeys(t[0] for t in TRIGGERS.values()) if table!=TABLE)
        op.execute('LOCK TABLE public.inventory_ledger_heads,'+','.join('public.'+t for t in tables)+' IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.stock_loss_request_seals IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM stock_loss_request_seals)',
            '0146 immutable loss seal history requires retention')
    if up:_schema()
    if dialect=='postgresql':
        if up:
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'GRANT SELECT,INSERT ON public.{TABLE} TO star_oam_api')
        for (name,signature),(args,result,body) in FUNCTIONS.items():
            _verify_function(name,signature,args,result,body)
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_seal_ready_0146')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    elif up:
        op.execute("CREATE TRIGGER trg_loss_seals_insert_0146 BEFORE INSERT ON stock_loss_request_seals BEGIN SELECT RAISE(ABORT,'0146 PostgreSQL loss seal proof required'); END")
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_loss_seals_{event.lower()}_0146 BEFORE {event} ON stock_loss_request_seals BEGIN SELECT RAISE(ABORT,'0146 immutable loss seals'); END")
    if not up:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
