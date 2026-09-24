"""Permanent original-opening request seals with a shared ledger commit fence.

No historical outcome is inferred. Positive start evidence wins; a negative
fact is immutable and forbids a later start even through the legacy raw API.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = '20261107_0128'
down_revision = '20261106_0127'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder / '20261106_0127_reconciliation_event_binding.py'))
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_previous['_ready'].replace(_previous['_ready_parent'], revision).encode()).hexdigest()
TABLE = 'opening_start_command_seals'
FUNCTION = 'rsc_guard_opening_start_seal_0128'
BODY = """
DECLARE
    item public.opening_start_command_seals%ROWTYPE;
    observed_user text;
    observed_reference text;
    identifier uuid;
    payload jsonb;
BEGIN
    -- Unrelated audit/state events return before taking the inventory lock.
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type='opening_start_command_seal' THEN
            identifier:=NEW.aggregate_id::uuid;
            SELECT * INTO item FROM public.opening_start_command_seals WHERE id=identifier;
            IF NOT FOUND THEN RAISE EXCEPTION '0128 detached seal audit' USING ERRCODE='23514'; END IF;
            observed_user:=item.actor_user_id; observed_reference:=item.request_reference;
        ELSIF NEW.stream_key='inventory' AND NEW.aggregate_type='stocktake_task' AND NEW.action='stocktake.opening.started' THEN
            observed_user:=NEW.actor_user_id; observed_reference:=NEW.request_id;
        ELSE RETURN NULL; END IF;
    ELSIF TG_TABLE_NAME='state_transition_events' THEN
        IF NEW.aggregate_type<>'stocktake_task' OR COALESCE(NEW.metadata_jsonb->>'request_reference','') NOT LIKE 'opening-request-%' THEN RETURN NULL; END IF;
        observed_user:=NEW.actor_id; observed_reference:=NEW.metadata_jsonb->>'request_reference';
    ELSE
        observed_user:=NEW.actor_user_id; observed_reference:=NEW.request_reference;
    END IF;
    IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION '0128 READ COMMITTED required' USING ERRCODE='25001';
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION '0128 inventory ledger missing' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='stocktake_task'
          AND action='stocktake.opening.started' AND actor_user_id=observed_user AND request_id=observed_reference)>1 THEN
        RAISE EXCEPTION '0128 original opening request is ambiguous' USING ERRCODE='23514';
    END IF;
    -- BEFORE INSERT cannot select NEW from its table yet.
    FOR item IN SELECT * FROM public.opening_start_command_seals
        WHERE actor_user_id=observed_user AND request_reference=observed_reference
        UNION ALL SELECT (jsonb_populate_record(NULL::public.opening_start_command_seals,to_jsonb(NEW))).*
          WHERE TG_TABLE_NAME='opening_start_command_seals' AND TG_WHEN='BEFORE'
    LOOP
        IF item.request_id !~ '^[A-Za-z0-9._:-]{8,160}$' OR item.authorization_version<1
           OR item.request_reference<>'opening-request-' || encode(sha256(convert_to('cloud_oam.opening_stocktake.request.v1','UTF8') || decode('00','hex') || convert_to(item.request_id,'UTF8')),'hex')
           OR NOT isfinite(item.created_at) OR item.created_at>clock_timestamp()
           OR NOT EXISTS (SELECT 1 FROM public.users WHERE id=item.actor_user_id AND person_id=item.actor_person_id)
           OR NOT EXISTS (SELECT 1 FROM public.control_projection_publications WHERE id=item.publication_id AND region_org_id=item.region_org_id) THEN
            RAISE EXCEPTION '0128 original coordinate mismatch' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='stocktake_task'
              AND action='stocktake.opening.started' AND actor_user_id=item.actor_user_id AND request_id=item.request_reference)
           OR EXISTS (SELECT 1 FROM public.state_transition_events WHERE aggregate_type='stocktake_task'
              AND actor_id=item.actor_user_id AND metadata_jsonb->>'request_reference'=item.request_reference) THEN
            RAISE EXCEPTION '0128 sealed request has execution evidence' USING ERRCODE='23514', CONSTRAINT='opening_start_sealed_0128';
        END IF;
        IF TG_TABLE_NAME='opening_start_command_seals' THEN
            IF item.created_at<transaction_timestamp() THEN RAISE EXCEPTION '0128 seal cannot be backdated' USING ERRCODE='23514'; END IF;
            PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[item.actor_user_id::text]);
            PERFORM public.rsc_assert_opening_actor_0126(item.actor_user_id,item.authorization_version,item.region_org_id);
        END IF;
        IF TG_WHEN='AFTER' THEN
            payload:=jsonb_build_object('actor_person_id',item.actor_person_id::text,'authorization_version',item.authorization_version,
                'region_org_id',item.region_org_id::text,'publication_id',item.publication_id::text,'trace_request_id',item.request_id);
            IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type='opening_start_command_seal' AND aggregate_id=item.id::text)<>1
               OR NOT EXISTS (SELECT 1 FROM public.audit_events WHERE stream_key='inventory' AND aggregate_type='opening_start_command_seal'
                    AND aggregate_id=item.id::text AND action='opening_start.sealed' AND actor_user_id=item.actor_user_id
                    AND request_id='opening-start-seal:'||item.id::text AND before_jsonb='{}'::jsonb AND after_jsonb=payload
                    AND occurred_at=item.created_at AND created_at=item.created_at) THEN
                RAISE EXCEPTION '0128 complete immutable seal audit required' USING ERRCODE='23514';
            END IF;
        END IF;
    END LOOP;
    IF TG_WHEN='BEFORE' THEN RETURN NEW; END IF;
    RETURN NULL;
END
"""
BODY_HASH = hashlib.sha256(BODY.encode()).hexdigest()
IMMUTABLE_FUNCTION = 'rsc_guard_opening_start_seal_immutable_0128'
IMMUTABLE_BODY = "BEGIN RAISE EXCEPTION '0128 original request seals are immutable' USING ERRCODE='23514'; END"
IMMUTABLE_HASH = hashlib.sha256(IMMUTABLE_BODY.encode()).hexdigest()
TRIGGERS = {
    'trg_opening_start_seal_insert_0128': (TABLE, 'INSERT', FUNCTION, 7, False),
    'trg_opening_start_seal_commit_0128': (TABLE, 'INSERT', FUNCTION, 5, True),
    'trg_opening_start_seal_audit_0128': ('audit_events', 'INSERT', FUNCTION, 5, True),
    'trg_opening_start_seal_state_0128': ('state_transition_events', 'INSERT', FUNCTION, 5, True),
    'trg_opening_start_seal_immutable_0128': (TABLE, 'UPDATE OR DELETE', IMMUTABLE_FUNCTION, 27, False),
    'trg_opening_start_seal_truncate_0128': (TABLE, 'TRUNCATE', IMMUTABLE_FUNCTION, 34, False),
}


def _create_table():
    op.create_table(TABLE,
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('actor_user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_person_id', sa.Uuid(), sa.ForeignKey('people.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('authorization_version', sa.BigInteger(), nullable=False),
        sa.Column('region_org_id', sa.Uuid(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('publication_id', sa.Uuid(), sa.ForeignKey('control_projection_publications.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('request_id', sa.String(160), nullable=False),
        sa.Column('request_reference', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('actor_user_id','request_id',name='uq_opening_start_seal_request'),
        sa.UniqueConstraint('actor_user_id','request_reference',name='uq_opening_start_seal_reference'),
        sa.CheckConstraint('authorization_version > 0 AND length(request_id) BETWEEN 8 AND 160 AND length(request_reference)=80',name='ck_opening_start_seal_context'))


def _verify():
    for function,expected_hash in [(FUNCTION,BODY_HASH),(IMMUTABLE_FUNCTION,IMMUTABLE_HASH)]:
        op.execute(f"""DO $verify_0128$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{function}()'::regprocedure
              AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator') AND p.prokind='f'
              AND p.prorettype='trigger'::regtype AND NOT p.proretset AND p.prosecdef AND NOT p.proleakproof
              AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u' AND p.pronargs=0 AND p.pronargdefaults=0
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql') AND p.proargmodes IS NULL
              AND p.proconfig=ARRAY['search_path=pg_catalog, public']
              AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected_hash}'
              AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee<>p.proowner)) THEN
            RAISE EXCEPTION '0128 seal function or private ACL drift'; END IF;
          END $verify_0128$""")
    for name,(table,_,function,kind,deferred) in TRIGGERS.items():
        flag=str(deferred).lower()
        op.execute(f"""DO $trigger_0128$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.{table}'::regclass AND tgname='{name}'
            AND tgfoid='public.{function}()'::regprocedure AND tgtype={kind} AND tgenabled='A' AND NOT tgisinternal
            AND tgdeferrable={flag} AND tginitdeferred={flag} AND tgqual IS NULL AND tgnargs=0 AND tgattr=''::int2vector) THEN
            RAISE EXCEPTION '0128 seal trigger drift'; END IF; END $trigger_0128$""")
    for index,column in [('uq_opening_start_seal_request','request_id'),('uq_opening_start_seal_reference','request_reference')]:
        op.execute(f"""DO $index_0128$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
            WHERE i.indexrelid='public.{index}'::regclass AND i.indrelid='public.{TABLE}'::regclass
            AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
            AND i.indnkeyatts=2 AND i.indnatts=2 AND i.indpred IS NULL AND i.indexprs IS NULL AND a.amname='btree'
            AND pg_get_indexdef(i.indexrelid,1,true)='actor_user_id' AND pg_get_indexdef(i.indexrelid,2,true)='{column}') THEN
            RAISE EXCEPTION '0128 original request uniqueness drift'; END IF; END $index_0128$""")


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}: raise RuntimeError('0128 unsupported database')
    helper=runpy.run_path(str(_folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $owner$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0128 direct schema owner required'; END IF; END $owner$")
        op.execute('LOCK TABLE public.alembic_version, public.stocktake_tasks, public.audit_events, public.state_transition_events IN ACCESS EXCLUSIVE MODE')
    if up:
        helper['_preflight']("EXISTS (SELECT 1 FROM audit_events WHERE stream_key='inventory' AND aggregate_type='stocktake_task' AND action='stocktake.opening.started' GROUP BY actor_user_id,request_id HAVING count(*)>1)", '0128 ambiguous historical opening requests require investigation')
        _create_table()
    else:
        if dialect=='postgresql':
            op.execute('LOCK TABLE public.'+TABLE+' IN ACCESS EXCLUSIVE MODE'); _verify()
        helper['_preflight']('EXISTS (SELECT 1 FROM '+TABLE+')', '0128 downgrade blocked: original request seals must be retained')
    if dialect=='sqlite':
        if up:
            for action in ['UPDATE','DELETE']:
                op.execute(f"CREATE TRIGGER trg_opening_start_seal_{action.lower()}_0128 BEFORE {action} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0128 original request seal is immutable'); END")
        else: op.drop_table(TABLE)
        return
    if up:
        for function,body in [(FUNCTION,BODY),(IMMUTABLE_FUNCTION,IMMUTABLE_BODY)]:
            op.execute(f'CREATE FUNCTION public.{function}() RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{function}() FROM PUBLIC, star_oam_api')
        for name,(table,events,function,_,deferred) in TRIGGERS.items():
            statement=(f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred else
                f"CREATE TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
            op.execute(statement+f' EXECUTE FUNCTION public.{function}()')
            op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        op.execute('REVOKE ALL ON TABLE public.'+TABLE+' FROM PUBLIC')
        op.execute('GRANT SELECT, INSERT ON TABLE public.'+TABLE+' TO star_oam_api')
        _verify()
    else:
        for name,(table,*_) in TRIGGERS.items(): op.execute(f'DROP TRIGGER {name} ON public.{table}')
        op.execute(f'DROP FUNCTION public.{FUNCTION}()')
        op.execute(f'DROP FUNCTION public.{IMMUTABLE_FUNCTION}()')
        op.drop_table(TABLE)
    replace=runpy.run_path(str(_folder/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='opening_seal_readiness_0128')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
