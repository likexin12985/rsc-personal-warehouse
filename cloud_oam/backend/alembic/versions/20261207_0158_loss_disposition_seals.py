"""Independent HQ execution seals; immutable, stock-neutral, current authority.

No predecessor namespace is retained:
readiness hashes are verified against the published 0052 body, and only small
historical transition helpers are loaded when an upgrade/downgrade executes.
"""
from pathlib import Path
import hashlib
import runpy
import sqlalchemy as sa
from alembic import op

revision='20261207_0158'
down_revision='20261206_0157'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
TABLE='stock_loss_disposition_request_seals'
OLD_READY_HASH='8e2f1d55ed6373bef7c342a8e4e919e551694d7405798587e7fd6ce3ee49b46a'
NEW_READY_HASH='6c18da9162afedf2f302f9db0474d7b64f235766515614acd814eb08e6e160a2'

OTHER_REQUEST_TABLES = (
    ('stock_operation_cancellations', True), ('stock_operation_outbounds', True),
    ('stock_operation_shipments', False), ('stock_operation_receipts', False),
    ('stock_operation_return_inbounds', True), ('stock_operation_command_seals', False),
    ('stock_operation_return_inbound_seals', False), ('stock_loss_request_seals', True),
    ('stock_loss_review_request_seals', True), ('stock_loss_regional_reviews', True),
    ('stock_loss_headquarters_reviews', True),
)

SCHEMA_SQL = """
CREATE TABLE public.stock_loss_disposition_request_seals (
    id uuid PRIMARY KEY,
    operation_id uuid NOT NULL,
    operation_type varchar(24) NOT NULL CHECK (operation_type='loss_report'),
    line_id uuid NOT NULL REFERENCES public.stock_operation_lines(id) ON DELETE RESTRICT,
    headquarters_decision_id uuid NOT NULL REFERENCES public.stock_loss_headquarters_decisions(id) ON DELETE RESTRICT,
    owner_org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    flow varchar(24) NOT NULL CHECK (flow IN ('disposition','return')),
    actor_user_id varchar(36) NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
    executor_person_id uuid NOT NULL REFERENCES public.people(id) ON DELETE RESTRICT,
    authorization_version bigint NOT NULL CHECK (authorization_version>0),
    request_id varchar(160) NOT NULL CHECK (request_id ~ '^[A-Za-z0-9._:-]{8,160}$'),
    request_reference varchar(100) NOT NULL,
    disposition_key_hash varchar(64) NOT NULL CHECK (disposition_key_hash ~ '^[a-f0-9]{64}$'),
    return_key_hash varchar(64) NOT NULL CHECK (return_key_hash ~ '^[a-f0-9]{64}$'),
    request_hash varchar(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    plan_hash varchar(64) NOT NULL CHECK (plan_hash ~ '^[a-f0-9]{64}$'),
    command_jsonb jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT fk_loss_disposition_seal_parent FOREIGN KEY (operation_id,operation_type)
        REFERENCES public.stock_operation_orders(id,operation_type) ON DELETE RESTRICT,
    CONSTRAINT uq_loss_disposition_seal_request UNIQUE(actor_user_id,request_id),
    CONSTRAINT uq_loss_disposition_seal_reference UNIQUE(actor_user_id,request_reference),
    CONSTRAINT uq_loss_disposition_seal_dkey UNIQUE(disposition_key_hash),
    CONSTRAINT uq_loss_disposition_seal_rkey UNIQUE(return_key_hash),
    CONSTRAINT ck_loss_disposition_seal_distinct_keys CHECK (disposition_key_hash<>return_key_hash)
);
CREATE INDEX ix_loss_disposition_seal_person_request ON public.stock_loss_disposition_request_seals(executor_person_id,request_id);
CREATE INDEX ix_loss_disposition_seal_person_hash ON public.stock_loss_disposition_request_seals(executor_person_id,request_hash);
"""

LOCK_BODY = """
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0158 execution seal requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0158 inventory ledger required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""

CHECK_BODY = """
DECLARE seal public.stock_loss_disposition_request_seals%ROWTYPE;
    parent public.stock_operation_orders%ROWTYPE; line public.stock_operation_lines%ROWTYPE;
    decision public.stock_loss_headquarters_decisions%ROWTYPE; review public.stock_loss_headquarters_reviews%ROWTYPE;
    intent jsonb; command jsonb; body jsonb; keys text[]; reference text;
BEGIN
    SELECT * INTO seal FROM public.stock_loss_disposition_request_seals WHERE id=checked_seal;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=seal.operation_id;
    SELECT * INTO line FROM public.stock_operation_lines WHERE id=seal.line_id;
    SELECT * INTO decision FROM public.stock_loss_headquarters_decisions WHERE id=seal.headquarters_decision_id;
    SELECT * INTO review FROM public.stock_loss_headquarters_reviews WHERE id=decision.review_id;
    IF seal.id IS NULL OR parent.id IS NULL OR line.id IS NULL OR decision.id IS NULL OR review.id IS NULL
       OR seal.operation_type<>'loss_report' OR parent.operation_type<>'loss_report' OR parent.status<>'submitted'
       OR line.operation_type<>'loss_report' OR line.operation_id<>parent.id OR decision.line_id<>line.id
       OR review.operation_id<>parent.id OR review.decision<>'approved' OR review.operation_type<>'loss_report'
       OR review.submission_plan_hash<>parent.plan_hash OR review.owner_org_id<>seal.owner_org_id
       OR seal.owner_org_id IS DISTINCT FROM (SELECT owner_org_id FROM public.stock_locations WHERE id=parent.source_location_id)
       OR seal.flow NOT IN ('disposition','return')
       OR (seal.flow='return' AND decision.disposition<>'return_to_region')
       OR (seal.flow='disposition' AND decision.disposition NOT IN ('restore_available','convert_used','convert_damaged'))
       OR seal.authorization_version<1 OR seal.created_at<review.created_at OR seal.created_at>clock_timestamp()
       OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR seal.disposition_key_hash !~ '^[a-f0-9]{64}$' OR seal.return_key_hash !~ '^[a-f0-9]{64}$'
       OR seal.request_hash !~ '^[a-f0-9]{64}$' OR seal.plan_hash !~ '^[a-f0-9]{64}$'
       OR seal.disposition_key_hash=seal.return_key_hash
       OR NOT EXISTS(SELECT 1 FROM public.inventory_transactions t WHERE t.id=parent.posting_transaction_id
            AND t.status='posted' AND t.movement_type='freeze' AND t.source_document_type='stock_operation_loss'
            AND t.source_document_id=parent.id::text)
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE reversed_transaction_id=parent.posting_transaction_id) THEN
        RAISE EXCEPTION '0158 exact approved original execution required' USING ERRCODE='23514'; END IF;
    -- The approval is immutable history. Only today's executor is authorized;
    -- do not re-require the original applicant/reviewer's former permissions.
    PERFORM public.rsc_assert_loss_disposition_authority_0150(seal.actor_user_id,seal.authorization_version,
        seal.executor_person_id,seal.owner_org_id);
    intent:=jsonb_build_object('headquarters_decision_id',decision.id::text,
        'expected_headquarters_review_hash',review.request_hash,'expected_submission_plan_hash',parent.plan_hash);
    IF seal.flow='return' THEN
        IF jsonb_typeof(seal.command_jsonb->'intent'->'target_location_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(seal.command_jsonb->'intent'->'transit_location_id') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION '0158 original return route required' USING ERRCODE='23514'; END IF;
        intent:=intent||jsonb_build_object(
            'target_location_id',(seal.command_jsonb->'intent'->>'target_location_id')::uuid::text,
            'transit_location_id',(seal.command_jsonb->'intent'->>'transit_location_id')::uuid::text);
    END IF;
    command:=jsonb_build_object('intent',intent,'request_id',seal.request_id,'expected_plan_hash',seal.plan_hash);
    IF seal.command_jsonb IS DISTINCT FROM command
       OR seal.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex') THEN
        RAISE EXCEPTION '0158 canonical original command required' USING ERRCODE='23514'; END IF;
    keys:=ARRAY[seal.disposition_key_hash,seal.return_key_hash];
    reference:='inventory-request-'||encode(sha256(convert_to('cloud_oam.inventory.request.v1','UTF8')||decode('00','hex')||convert_to(seal.request_id,'UTF8')),'hex');
    IF seal.request_reference IS DISTINCT FROM reference
       OR EXISTS(SELECT 1 FROM public.stock_loss_disposition_request_seals other WHERE other.id<>seal.id
            AND (other.disposition_key_hash=ANY(keys) OR other.return_key_hash=ANY(keys)
                OR (other.actor_user_id=seal.actor_user_id AND other.request_id=seal.request_id)))
       OR EXISTS(SELECT 1 FROM public.stock_loss_dispositions WHERE idempotency_key_hash=ANY(keys)
            OR (actor_user_id=seal.actor_user_id AND request_id=seal.request_id))
       OR EXISTS(SELECT 1 FROM public.stock_operation_orders WHERE idempotency_key_hash=ANY(keys)
            OR (actor_user_id=seal.actor_user_id AND request_id=seal.request_id))
       OR EXISTS(SELECT 1 FROM public.inventory_transactions WHERE idempotency_key_hash=ANY(keys))
       OR EXISTS(SELECT 1 FROM public.shipments WHERE idempotency_key_hash=ANY(keys))
       OR EXISTS(SELECT 1 FROM public.receipts WHERE idempotency_key_hash=ANY(keys))
       $other_request_checks$
       OR EXISTS(SELECT 1 FROM public.audit_events WHERE actor_user_id=seal.actor_user_id
            AND stream_key IN ('inventory','material_request') AND request_id IN (seal.request_id,reference))
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE actor_id=seal.actor_user_id
            AND aggregate_type='inventory_transaction' AND metadata_jsonb->>'request_reference'=reference)
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_loss_disposition'
            AND payload_jsonb->>'executor_person_id'=seal.executor_person_id::text AND payload_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_loss_disposition'
            AND metadata_jsonb->>'executor_person_id'=seal.executor_person_id::text AND metadata_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE business_type='stock_loss_disposition'
            AND payload_jsonb->>'executor_person_id'=seal.executor_person_id::text AND payload_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_operation_order'
            AND payload_jsonb->>'executor_person_id'=seal.executor_person_id::text AND payload_jsonb->>'request_hash'=seal.request_hash)
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_operation_order'
            AND metadata_jsonb->>'executor_person_id'=seal.executor_person_id::text AND metadata_jsonb->>'request_hash'=seal.request_hash) THEN
        RAISE EXCEPTION '0158 executed or unknown original request cannot be sealed' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('flow',seal.flow,'operation_id',seal.operation_id::text,'line_id',seal.line_id::text,
        'headquarters_decision_id',seal.headquarters_decision_id::text,'owner_org_id',seal.owner_org_id::text,
        'executor_person_id',seal.executor_person_id::text,'authorization_version',seal.authorization_version,
        'request_id',seal.request_id,'request_reference',seal.request_reference,
        'disposition_key_hash',seal.disposition_key_hash,'return_key_hash',seal.return_key_hash,
        'request_hash',seal.request_hash,'plan_hash',seal.plan_hash,'command',command);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_disposition_request_seal' AND aggregate_id=seal.id::text)<>1
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_disposition_request_seal' AND actor_user_id=seal.actor_user_id
            AND after_jsonb->>'request_id'=seal.request_id)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_disposition_request_seal' AND aggregate_id=seal.id::text
            AND actor_user_id=seal.actor_user_id AND action='stock_loss.disposition_request_sealed'
            AND request_id='stock-loss-disposition-seal:'||seal.id::text AND before_jsonb='{}'::jsonb
            AND after_jsonb=body AND created_at=seal.created_at AND occurred_at=seal.created_at)
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_type='stock_loss_disposition_request_seal' AND aggregate_id=seal.id::text)
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stock_loss_disposition_request_seal' AND aggregate_id=seal.id::text)
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE business_type='stock_loss_disposition_request_seal' AND business_id=seal.id::text) THEN
        RAISE EXCEPTION '0158 exact stock-neutral seal audit required' USING ERRCODE='23514'; END IF;
END;
""".replace('$other_request_checks$', '\n'.join(
    'OR EXISTS(SELECT 1 FROM public.' + table + ' WHERE '
    + ('idempotency_key_hash=ANY(keys) OR ' if keyed else '')
    + '(actor_user_id=seal.actor_user_id AND request_id=seal.request_id))'
    for table, keyed in OTHER_REQUEST_TABLES))

FENCE_BODY = """
DECLARE identifier uuid; observed_actor text; observed_person text; observed_request text;
    observed_key text; observed_hash text; observed_reference text; aggregate text; body jsonb;
BEGIN
    IF TG_TABLE_NAME='stock_loss_disposition_request_seals' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('stock_loss_dispositions','stock_operation_orders') THEN
        observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id; observed_key:=NEW.idempotency_key_hash;
    ELSIF TG_TABLE_NAME IN ($other_keyed_tables$) THEN
        observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id; observed_key:=NEW.idempotency_key_hash;
    ELSIF TG_TABLE_NAME IN ($other_unkeyed_tables$) THEN
        observed_actor:=NEW.actor_user_id; observed_request:=NEW.request_id;
    ELSIF TG_TABLE_NAME IN ('inventory_transactions','shipments','receipts') THEN observed_key:=NEW.idempotency_key_hash;
    ELSE
        IF TG_TABLE_NAME='audit_events' THEN
            IF NEW.stream_key NOT IN ('inventory','material_request') THEN RETURN NULL; END IF;
            aggregate:=NEW.aggregate_type; body:=NEW.after_jsonb; observed_actor:=NEW.actor_user_id;
            IF aggregate='stock_loss_disposition_request_seal' THEN identifier:=NEW.aggregate_id::uuid;
            ELSIF aggregate='inventory_transaction' THEN observed_reference:=NEW.request_id;
            ELSE observed_request:=NEW.request_id; END IF;
        ELSIF TG_TABLE_NAME='outbox_events' THEN aggregate:=NEW.aggregate_type; body:=NEW.payload_jsonb;
            IF aggregate='stock_loss_disposition_request_seal' THEN identifier:=NEW.aggregate_id::uuid; END IF;
        ELSIF TG_TABLE_NAME='state_transition_events' THEN
            aggregate:=NEW.aggregate_type; body:=NEW.metadata_jsonb; observed_actor:=NEW.actor_id;
            IF aggregate='stock_loss_disposition_request_seal' THEN identifier:=NEW.aggregate_id::uuid;
            ELSIF aggregate='inventory_transaction' THEN observed_reference:=body->>'request_reference'; END IF;
        ELSE aggregate:=NEW.business_type; body:=NEW.payload_jsonb;
            IF aggregate='stock_loss_disposition_request_seal' THEN identifier:=NEW.business_id::uuid; END IF;
        END IF;
        IF aggregate IN ('stock_loss_disposition','stock_operation_order') THEN
            observed_person:=body->>'executor_person_id';
            observed_request:=COALESCE(observed_request,body->>'request_id');
            observed_hash:=body->>'request_hash';
        END IF;
    END IF;
    IF identifier IS NULL AND observed_key IS NULL
       AND (observed_actor IS NULL OR (observed_request IS NULL AND observed_reference IS NULL))
       AND (observed_person IS NULL OR (observed_request IS NULL AND observed_hash IS NULL)) THEN RETURN NULL; END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0158 execution fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0158 inventory ledger required' USING ERRCODE='23514'; END IF;
    IF identifier IS NOT NULL THEN PERFORM public.rsc_check_loss_disposition_seal_0158(identifier); RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM public.stock_loss_disposition_request_seals WHERE
        observed_key IN (disposition_key_hash,return_key_hash)
        OR (request_id=observed_request AND (actor_user_id=observed_actor OR executor_person_id::text=observed_person))
        OR (request_hash=observed_hash AND executor_person_id::text=observed_person)
        OR (actor_user_id=observed_actor AND request_reference=observed_reference)) THEN
        RAISE EXCEPTION '0158 sealed execution request cannot execute' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END;
""".replace('$other_keyed_tables$', ','.join("'"+name+"'" for name,keyed in OTHER_REQUEST_TABLES if keyed)
).replace('$other_unkeyed_tables$', ','.join("'"+name+"'" for name,keyed in OTHER_REQUEST_TABLES if not keyed))

FUNCTIONS = {
    ('rsc_lock_loss_disposition_seal_0158',''): ('','trigger',LOCK_BODY),
    ('rsc_check_loss_disposition_seal_0158','uuid'): ('checked_seal uuid','void',CHECK_BODY),
    ('rsc_guard_loss_disposition_seal_0158',''): ('','trigger',FENCE_BODY),
}

FENCE_TABLES = ('stock_loss_disposition_request_seals', 'stock_loss_dispositions',
    'stock_operation_orders', 'inventory_transactions', 'shipments', 'receipts',
    'audit_events', 'outbox_events', 'state_transition_events', 'notification_events',
    *(name for name, _ in OTHER_REQUEST_TABLES))

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
        THEN RAISE EXCEPTION '0158 execution seal function source ownership or ACL drift'; END IF; END $$""")

FUNCTION_HASHES={key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS={f'trg_{table}_execution_seal_0158':(table,'INSERT','rsc_guard_loss_disposition_seal_0158',5,True)
    for table in FENCE_TABLES}
TRIGGERS.update({
    'trg_loss_disposition_seal_lock_0158':(TABLE,'INSERT','rsc_lock_loss_disposition_seal_0158',7,False),
    'trg_loss_disposition_seal_immutable_0158':(TABLE,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
    'trg_loss_disposition_seal_truncate_0158':(TABLE,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
})
assert all(len(name)<=63 for name in TRIGGERS)


def _sqlite_schema():
    # SQLite cannot prove concurrent closures; all inserts are blocked below.
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('operation_id',sa.Uuid(),nullable=False),
        sa.Column('operation_type',sa.String(24),nullable=False),
        sa.Column('line_id',sa.Uuid(),sa.ForeignKey('stock_operation_lines.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('headquarters_decision_id',sa.Uuid(),sa.ForeignKey('stock_loss_headquarters_decisions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('owner_org_id',sa.Uuid(),sa.ForeignKey('organizations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('flow',sa.String(24),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('executor_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('request_reference',sa.String(100),nullable=False),
        sa.Column('disposition_key_hash',sa.String(64),nullable=False),
        sa.Column('return_key_hash',sa.String(64),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('plan_hash',sa.String(64),nullable=False),
        sa.Column('command_jsonb',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['operation_id','operation_type'],['stock_operation_orders.id','stock_operation_orders.operation_type'],
            name='fk_loss_disposition_seal_parent',ondelete='RESTRICT'),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_loss_disposition_seal_request'),
        sa.UniqueConstraint('actor_user_id','request_reference',name='uq_loss_disposition_seal_reference'),
        sa.UniqueConstraint('disposition_key_hash',name='uq_loss_disposition_seal_dkey'),
        sa.UniqueConstraint('return_key_hash',name='uq_loss_disposition_seal_rkey'),
        sa.CheckConstraint("operation_type='loss_report' AND flow IN ('disposition','return')",name='ck_loss_disposition_seal_kind'),
        sa.CheckConstraint('authorization_version>0 AND length(request_id) BETWEEN 8 AND 160',name='ck_loss_disposition_seal_context'),
        sa.CheckConstraint('length(request_hash)=64 AND length(plan_hash)=64 AND length(disposition_key_hash)=64 AND length(return_key_hash)=64',name='ck_loss_disposition_seal_hashes'),
        sa.CheckConstraint('disposition_key_hash<>return_key_hash',name='ck_loss_disposition_seal_distinct_keys'))
    op.create_index('ix_loss_disposition_seal_person_request',TABLE,['executor_person_id','request_id'])
    op.create_index('ix_loss_disposition_seal_person_hash',TABLE,['executor_person_id','request_hash'])


def _verify_catalog():
    for (name,signature),(args,result,body) in FUNCTIONS.items():
        _verify_function(name,signature,args,result,body)
    for name,(table,events,function,kind,deferred) in TRIGGERS.items():
        boolean='true' if deferred else 'false'
        op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger t
            JOIN pg_proc f ON f.oid=t.tgfoid JOIN pg_namespace n ON n.oid=f.pronamespace
            WHERE t.tgrelid='public.{table}'::regclass AND t.tgname='{name}'
              AND t.tgtype={kind} AND t.tgenabled='A' AND NOT t.tgisinternal
              AND t.tgdeferrable={boolean} AND t.tginitdeferred={boolean}
              AND (t.tgconstraint<>0)={boolean} AND t.tgqual IS NULL
              AND t.tgnargs=0 AND t.tgattr=''::int2vector
              AND f.proname='{function}' AND n.nspname='public')
            THEN RAISE EXCEPTION '0158 execution seal trigger drift'; END IF; END $$""")


def _transition(up):
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect=op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0158 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0158 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,'+','.join('public.'+t for t in FENCE_TABLES if t!=TABLE)+' IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.'+TABLE+' IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM '+TABLE+')','0158 immutable execution seal history requires retention')
    if dialect=='postgresql':
        if up:
            op.execute(SCHEMA_SQL)
            for (name,signature),(args,result,body) in FUNCTIONS.items():
                op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public AS $body${body}$body$')
                op.execute(f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api')
            for name,(table,events,function,_,deferred) in TRIGGERS.items():
                statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                    if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
                op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
                op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
            op.execute(f'REVOKE ALL ON public.{TABLE} FROM PUBLIC,star_oam_api')
            op.execute(f'GRANT SELECT,INSERT ON public.{TABLE} TO star_oam_api')
        _verify_catalog()
        replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_execution_seal_ready_0158')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    elif up:
        _sqlite_schema()
        op.execute(f"CREATE TRIGGER trg_loss_disposition_seal_insert_0158 BEFORE INSERT ON {TABLE} BEGIN SELECT RAISE(ABORT,'0158 PostgreSQL execution seal proof required'); END")
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_loss_disposition_seal_{event.lower()}_0158 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0158 immutable execution seals'); END")
    if not up:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
