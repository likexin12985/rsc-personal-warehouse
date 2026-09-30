"""Independent approval seals and bidirectional late-request exclusion.

No stock writes, notification delivery or production permission seeds.
"""
from pathlib import Path
import hashlib
import runpy
import sqlalchemy as sa
from alembic import op

revision = '20261205_0156'
down_revision = '20261204_0155'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER/'20261204_0155_stock_loss_return_receipts.py'))
ready = previous['ready']
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
TABLE = 'stock_loss_review_request_seals'
LOCK_BODY = """
BEGIN
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0156 approval seal requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0156 inventory ledger required' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END;
"""
# Match the original Python strict command trim rule, including Unicode spaces.
APPROVAL_TRIM_SQL = "U&'\\0009\\000a\\000b\\000c\\000d\\001c\\001d\\001e\\001f\\0020\\0085\\00a0\\1680\\2000\\2001\\2002\\2003\\2004\\2005\\2006\\2007\\2008\\2009\\200a\\2028\\2029\\202f\\205f\\3000'"
CHECK_BODY = r"""
DECLARE seal public.stock_loss_review_request_seals%ROWTYPE; parent public.stock_operation_orders%ROWTYPE;
    regional public.stock_loss_regional_reviews%ROWTYPE; command jsonb; body jsonb; aggregate text;
    decision jsonb; canonical_decisions jsonb; expected_fields text[];
BEGIN
    SELECT * INTO seal FROM public.stock_loss_review_request_seals WHERE id=checked_seal;
    SELECT * INTO parent FROM public.stock_operation_orders WHERE id=seal.operation_id;
    command:=seal.command_intent_jsonb;
    IF seal.id IS NULL OR parent.id IS NULL OR seal.operation_type<>'loss_report'
       OR parent.operation_type<>'loss_report' OR parent.status<>'submitted'
       OR seal.stage NOT IN ('regional','headquarters') OR seal.authorization_version<1
       OR seal.actor_user_id=parent.actor_user_id OR seal.reviewer_person_id=parent.requester_id
       OR seal.submission_plan_hash<>parent.plan_hash
       OR seal.owner_org_id IS DISTINCT FROM (SELECT owner_org_id FROM public.stock_locations WHERE id=parent.source_location_id)
       OR seal.created_at<parent.created_at OR seal.created_at>clock_timestamp()
       OR seal.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
       OR seal.request_hash !~ '^[a-f0-9]{64}$' OR seal.submission_plan_hash !~ '^[a-f0-9]{64}$'
       OR seal.idempotency_key_hash !~ '^[a-f0-9]{64}$'
       OR jsonb_typeof(command) IS DISTINCT FROM 'object'
       OR command->>'operation_id' IS DISTINCT FROM parent.id::text
       OR command->>'expected_submission_plan_hash' IS DISTINCT FROM parent.plan_hash
       OR jsonb_typeof(command->'comment') IS DISTINCT FROM 'string'
       OR length(command->>'comment') NOT BETWEEN 1 AND 1000
       OR command->>'comment'<>btrim(command->>'comment',$approval_trim_chars$)
       OR seal.request_hash<>encode(sha256(convert_to(public.rsc_canonical_reconciliation_json_0026(command),'UTF8')),'hex')
       OR NOT EXISTS(SELECT 1 FROM public.inventory_transactions WHERE id=parent.posting_transaction_id
           AND status='posted' AND movement_type='freeze' AND source_document_type='stock_operation_loss'
           AND source_document_id=parent.id::text)
       OR NOT EXISTS(SELECT 1 FROM public.stock_operation_lines WHERE operation_id=parent.id) THEN
        RAISE EXCEPTION '0156 exact original approval intent required' USING ERRCODE='23514'; END IF;
    aggregate:='stock_loss_'||seal.stage||'_review';
    expected_fields:=ARRAY['comment','expected_submission_plan_hash','operation_id'];
    IF seal.stage='regional' THEN
        PERFORM public.rsc_assert_loss_regional_authority_0147(seal.actor_user_id,seal.authorization_version,
            seal.reviewer_person_id,seal.owner_org_id);
        IF EXISTS(SELECT 1 FROM public.stock_loss_regional_reviews WHERE operation_id=parent.id
            OR idempotency_key_hash=seal.idempotency_key_hash OR (actor_user_id=seal.actor_user_id AND request_id=seal.request_id)) THEN
            RAISE EXCEPTION '0156 executed regional request cannot be sealed' USING ERRCODE='23514'; END IF;
    ELSE
        PERFORM public.rsc_assert_loss_headquarters_authority_0148(seal.actor_user_id,seal.authorization_version,
            seal.reviewer_person_id,seal.owner_org_id);
        expected_fields:=ARRAY['comment','decisions','expected_regional_review_hash','expected_submission_plan_hash','operation_id','regional_review_id'];
        SELECT * INTO regional FROM public.stock_loss_regional_reviews WHERE id::text=command->>'regional_review_id';
        IF regional.id IS NULL OR regional.operation_id<>parent.id OR regional.owner_org_id<>seal.owner_org_id
           OR regional.decision<>'verified' OR regional.submission_plan_hash<>seal.submission_plan_hash
           OR regional.request_hash IS DISTINCT FROM command->>'expected_regional_review_hash'
           OR regional.created_at>seal.created_at OR jsonb_typeof(command->'decisions') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION '0156 exact regional approval and decisions required' USING ERRCODE='23514'; END IF;
        IF jsonb_array_length(command->'decisions') NOT BETWEEN 1 AND 100 THEN
            RAISE EXCEPTION '0156 nonempty decisions required' USING ERRCODE='23514'; END IF;
        FOR decision IN SELECT value FROM jsonb_array_elements(command->'decisions') LOOP
            IF jsonb_typeof(decision) IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION '0156 decision object required' USING ERRCODE='23514'; END IF;
            IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(decision) key)
                 IS DISTINCT FROM ARRAY['disposition','line_id','reason']::text[]
               OR jsonb_typeof(decision->'reason') IS DISTINCT FROM 'string'
               OR length(decision->>'reason') NOT BETWEEN 1 AND 500
               OR decision->>'reason'<>btrim(decision->>'reason',$approval_trim_chars$)
               OR decision->>'reason' ~ '[\x01-\x08\x0b-\x1f]'
               OR COALESCE(decision->>'disposition','') NOT IN ('restore_available','convert_used','convert_damaged','return_to_region','scrap')
               OR NOT EXISTS(SELECT 1 FROM public.stock_operation_lines WHERE operation_id=parent.id AND id::text=decision->>'line_id') THEN
                RAISE EXCEPTION '0156 invalid original line decision' USING ERRCODE='23514'; END IF;
        END LOOP;
        IF (SELECT count(DISTINCT value->>'line_id') FROM jsonb_array_elements(command->'decisions'))<>jsonb_array_length(command->'decisions')
           OR jsonb_array_length(command->'decisions')<>(SELECT count(*) FROM public.stock_operation_lines WHERE operation_id=parent.id) THEN
            RAISE EXCEPTION '0156 complete unique original lines required' USING ERRCODE='23514'; END IF;
        SELECT jsonb_agg(value ORDER BY value->>'line_id') INTO canonical_decisions FROM jsonb_array_elements(command->'decisions');
        IF canonical_decisions IS DISTINCT FROM command->'decisions' THEN
            RAISE EXCEPTION '0156 canonical decision order required' USING ERRCODE='23514'; END IF;
        IF EXISTS(SELECT 1 FROM public.stock_loss_headquarters_reviews WHERE operation_id=parent.id
            OR idempotency_key_hash=seal.idempotency_key_hash OR (actor_user_id=seal.actor_user_id AND request_id=seal.request_id)) THEN
            RAISE EXCEPTION '0156 executed headquarters request cannot be sealed' USING ERRCODE='23514'; END IF;
    END IF;
    IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(command) key) IS DISTINCT FROM expected_fields
       OR EXISTS(SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type=aggregate
            AND actor_user_id=seal.actor_user_id AND after_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.outbox_events WHERE aggregate_type=aggregate
            AND payload_jsonb->>'reviewer_person_id'=seal.reviewer_person_id::text AND payload_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.state_transition_events WHERE aggregate_type=aggregate
            AND metadata_jsonb->>'reviewer_person_id'=seal.reviewer_person_id::text AND metadata_jsonb->>'request_id'=seal.request_id)
       OR EXISTS(SELECT 1 FROM public.notification_events WHERE business_type=aggregate
            AND payload_jsonb->>'reviewer_person_id'=seal.reviewer_person_id::text AND payload_jsonb->>'request_id'=seal.request_id) THEN
        RAISE EXCEPTION '0156 conflicting approval evidence' USING ERRCODE='23514'; END IF;
    body:=jsonb_build_object('stage',seal.stage,'operation_id',seal.operation_id::text,'owner_org_id',seal.owner_org_id::text,
        'reviewer_person_id',seal.reviewer_person_id::text,'authorization_version',seal.authorization_version,
        'request_id',seal.request_id,'idempotency_key_hash',seal.idempotency_key_hash,'request_hash',seal.request_hash,
        'submission_plan_hash',seal.submission_plan_hash,'command_intent',command);
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_review_request_seal' AND aggregate_id=seal.id::text)<>1
       OR (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_review_request_seal' AND actor_user_id=seal.actor_user_id
            AND after_jsonb->>'stage'=seal.stage AND after_jsonb->>'request_id'=seal.request_id)<>1
       OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE stream_key='inventory'
            AND aggregate_type='stock_loss_review_request_seal' AND aggregate_id=seal.id::text
            AND actor_user_id=seal.actor_user_id AND action='stock_loss.review_request_sealed'
            AND request_id='stock-loss-review-seal:'||seal.id::text AND before_jsonb='{}'::jsonb
            AND after_jsonb=body AND created_at=seal.created_at AND occurred_at=seal.created_at) THEN
        RAISE EXCEPTION '0156 exact immutable approval seal audit required' USING ERRCODE='23514'; END IF;
END;
""".replace("$approval_trim_chars$", APPROVAL_TRIM_SQL)
FENCE_BODY = """
DECLARE identifier uuid; observed_stage text; observed_actor text; observed_person text;
    observed_request text; observed_key text; aggregate text; body jsonb;
BEGIN
    IF TG_TABLE_NAME='stock_loss_review_request_seals' THEN identifier:=NEW.id;
    ELSIF TG_TABLE_NAME IN ('stock_loss_regional_reviews','stock_loss_headquarters_reviews') THEN
        observed_stage:=CASE TG_TABLE_NAME WHEN 'stock_loss_regional_reviews' THEN 'regional' ELSE 'headquarters' END;
        observed_actor:=NEW.actor_user_id; observed_person:=NEW.reviewer_person_id::text;
        observed_request:=NEW.request_id; observed_key:=NEW.idempotency_key_hash;
    ELSE
        IF TG_TABLE_NAME='audit_events' THEN
            aggregate:=NEW.aggregate_type; body:=NEW.after_jsonb; observed_actor:=NEW.actor_user_id;
            IF aggregate='stock_loss_review_request_seal' THEN identifier:=NEW.aggregate_id::uuid; END IF;
        ELSIF TG_TABLE_NAME='outbox_events' THEN aggregate:=NEW.aggregate_type; body:=NEW.payload_jsonb;
        ELSIF TG_TABLE_NAME='state_transition_events' THEN aggregate:=NEW.aggregate_type; body:=NEW.metadata_jsonb; observed_actor:=NEW.actor_id;
        ELSE aggregate:=NEW.business_type; body:=NEW.payload_jsonb;
        END IF;
        IF identifier IS NULL THEN
            IF aggregate NOT IN ('stock_loss_regional_review','stock_loss_headquarters_review') THEN RETURN NULL; END IF;
            observed_stage:=CASE aggregate WHEN 'stock_loss_regional_review' THEN 'regional' ELSE 'headquarters' END;
            observed_person:=body->>'reviewer_person_id'; observed_request:=body->>'request_id';
        END IF;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0156 approval fence requires read committed' USING ERRCODE='23514'; END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0156 inventory ledger required' USING ERRCODE='23514'; END IF;
    IF identifier IS NOT NULL THEN PERFORM public.rsc_check_loss_review_seal_0156(identifier); RETURN NULL; END IF;
    IF EXISTS(SELECT 1 FROM public.stock_loss_review_request_seals WHERE stage=observed_stage
        AND (idempotency_key_hash=observed_key OR (request_id=observed_request
            AND (actor_user_id=observed_actor OR reviewer_person_id::text=observed_person)))) THEN
        RAISE EXCEPTION '0156 sealed approval request cannot execute' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END;
"""
FUNCTIONS = {
    ('rsc_lock_loss_review_seal_0156',''): ('','trigger',LOCK_BODY),
    ('rsc_check_loss_review_seal_0156','uuid'): ('checked_seal uuid','void',CHECK_BODY),
    ('rsc_guard_loss_review_seal_0156',''): ('','trigger',FENCE_BODY),
}
FUNCTION_HASHES = {key:hashlib.sha256(value[2].encode()).hexdigest() for key,value in FUNCTIONS.items()}
TRIGGERS = {f'trg_{table}_review_seal_0156':(table,'INSERT','rsc_guard_loss_review_seal_0156',5,True)
    for table in (TABLE,'stock_loss_regional_reviews','stock_loss_headquarters_reviews','audit_events',
        'outbox_events','state_transition_events','notification_events')}
TRIGGERS.update({
    'trg_loss_review_seal_lock_0156':(TABLE,'INSERT','rsc_lock_loss_review_seal_0156',7,False),
    'trg_loss_review_seal_immutable_0156':(TABLE,'UPDATE OR DELETE','rsc_guard_work_order_facts_0090',27,False),
    'trg_loss_review_seal_truncate_0156':(TABLE,'TRUNCATE','rsc_guard_work_order_facts_0090',34,False),
})


def _schema():
    op.create_table(TABLE,
        sa.Column('id',sa.Uuid(),primary_key=True,nullable=False),
        sa.Column('operation_id',sa.Uuid(),nullable=False),
        sa.Column('operation_type',sa.String(24),nullable=False),
        sa.Column('stage',sa.String(24),nullable=False),
        sa.Column('owner_org_id',sa.Uuid(),sa.ForeignKey('organizations.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('reviewer_person_id',sa.Uuid(),sa.ForeignKey('people.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('authorization_version',sa.BigInteger(),nullable=False),
        sa.Column('request_id',sa.String(160),nullable=False),
        sa.Column('idempotency_key_hash',sa.String(64),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),
        sa.Column('submission_plan_hash',sa.String(64),nullable=False),
        sa.Column('command_intent_jsonb',sa.JSON().with_variant(sa.dialects.postgresql.JSONB(),'postgresql'),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(['operation_id','operation_type'],['stock_operation_orders.id','stock_operation_orders.operation_type'],
            name='fk_loss_review_seal_parent',ondelete='RESTRICT'),
        sa.UniqueConstraint('stage','actor_user_id','request_id',name='uq_loss_review_seal_request'),
        sa.UniqueConstraint('stage','idempotency_key_hash',name='uq_loss_review_seal_key'),
        sa.CheckConstraint("operation_type='loss_report' AND stage IN ('regional','headquarters')",name='ck_loss_review_seal_kind'),
        sa.CheckConstraint('authorization_version>0 AND length(request_id) BETWEEN 8 AND 160',name='ck_loss_review_seal_context'),
        sa.CheckConstraint('length(request_hash)=64 AND length(submission_plan_hash)=64 AND length(idempotency_key_hash)=64',name='ck_loss_review_seal_hashes'))


def _transition(up):
    helper = runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql','sqlite'):raise RuntimeError('0156 requires PostgreSQL or SQLite')
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0156 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        tables = tuple(dict.fromkeys(t[0] for t in TRIGGERS.values() if t[0]!=TABLE))
        op.execute('LOCK TABLE public.inventory_ledger_heads,'+','.join('public.'+t for t in tables)+' IN SHARE ROW EXCLUSIVE MODE')
        if not up:op.execute('LOCK TABLE public.'+TABLE+' IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM '+TABLE+')', '0156 immutable approval seal history requires retention')
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
        verify = runpy.run_path(str(FOLDER/'20261125_0146_stock_loss_request_seals.py'))['_verify_function']
        for (name,signature),(args,result,body) in FUNCTIONS.items():verify(name,signature,args,result,body)
        replace = runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='loss_review_seal_ready_0156')
        if not up:
            for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
            for name,signature in reversed(FUNCTIONS):op.execute(f'DROP FUNCTION public.{name}({signature})')
    elif up:
        op.execute(f"CREATE TRIGGER trg_loss_review_seal_insert_0156 BEFORE INSERT ON {TABLE} BEGIN SELECT RAISE(ABORT,'0156 PostgreSQL approval seal proof required'); END")
        for event in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER trg_loss_review_seal_{event.lower()}_0156 BEFORE {event} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0156 immutable approval seals'); END")
    if not up:op.drop_table(TABLE)


def upgrade():_transition(True)
def downgrade():_transition(False)
