"""Immutable original-request fences; no business command or inventory replay."""
from pathlib import Path
import hashlib,runpy
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='20261113_0134'
down_revision='20261112_0133'
branch_labels=depends_on=None
FOLDER=Path(__file__).parent
previous=runpy.run_path(str(FOLDER/'20261112_0133_daily_review_evidence.py'))
base=runpy.run_path(str(FOLDER/'20261111_0132_daily_reconciliation_review.py'))
ready=runpy.run_path(str(FOLDER/'20261108_0129_notification_expansion_status.py'))
OLD_HASH=previous['NEW_HASH']
NEW_HASH=hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'],revision).encode()).hexdigest()
TABLE='daily_review_request_seals'

# Freeze the existing identity/session proof for READ authority, without
# inventing a business review event or requiring a revoked write permission.
LIVE=base['FUNCTIONS']['rsc_daily_review_live_0132'][2]
LIVE=LIVE[:LIVE.index(' FOR f IN SELECT value FROM jsonb_array_elements')]+"END\n"
LIVE=LIVE.replace("e.payload_jsonb->'actor'",'e.actor_snapshot').replace("e.payload_jsonb#>>'{request,operation}'","'read'")
start=LIVE.index(' action_name:=CASE');end=LIVE.index(' IF action_name IS NULL',start)
LIVE=LIVE[:start]+" action_name:='read';\n"+LIVE[end:]
LIVE=LIVE.replace("op IN ('open','explain')","op IN ('open','explain','read')").replace("WHERE op<>'open' OR", "WHERE op NOT IN ('open','read') OR")

GUARD="""
DECLARE item public.daily_review_request_seals%ROWTYPE; expected jsonb; a jsonb;
BEGIN
 IF TG_TABLE_NAME='audit_events' THEN
  IF NEW.aggregate_type<>'daily_review_request_seal' THEN
   IF NEW.action='daily_reconciliation.request_sealed' THEN RAISE EXCEPTION '0134 wrong seal aggregate' USING ERRCODE='23514'; END IF;
   IF TG_WHEN='BEFORE' THEN RETURN NEW; ELSE RETURN NULL; END IF;
  END IF;
  SELECT * INTO STRICT item FROM public.daily_review_request_seals WHERE id=NEW.aggregate_id::uuid;
 ELSE item:=NEW; END IF;
 IF session_user NOT IN ('star_oam_api','star_oam_migrator') OR current_setting('transaction_isolation')<>'read committed'
 THEN RAISE EXCEPTION '0134 runtime or isolation invalid' USING ERRCODE='42501'; END IF;
 IF item.transaction_id<>txid_current() THEN RAISE EXCEPTION '0134 seal audit must be in original transaction' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='daily_review_request_seals' AND TG_WHEN='BEFORE' THEN
  PERFORM 1 FROM public.auth_sessions WHERE id=item.auth_session_id FOR UPDATE;
  PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[item.actor_user_id]::text[]);
  PERFORM pg_advisory_xact_lock(hashtextextended('daily-review:'||item.cutoff_id::text,0));
  PERFORM 1 FROM public.daily_review_bindings WHERE cutoff_id=item.cutoff_id FOR UPDATE;
  PERFORM pg_advisory_xact_lock(hashtextextended('daily-review-request:'||item.actor_user_id||':'||item.request_id,0));
 END IF;
 a:=item.actor_snapshot;
 IF item.id='00000000-0000-0000-0000-000000000000'::uuid
  OR NOT isfinite(item.created_at) OR item.created_at<transaction_timestamp() OR item.created_at>clock_timestamp()
  OR item.request_id !~ '^[A-Za-z0-9._:-]{8,160}$'
  OR jsonb_typeof(a) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(a))<>9
  OR NOT a ?& ARRAY['user_id','person_id','authorization_version','assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']
  OR a->>'user_id' IS DISTINCT FROM item.actor_user_id OR a->>'person_id' IS DISTINCT FROM item.actor_person_id::text
  OR jsonb_typeof(a->'authorization_version') IS DISTINCT FROM 'number'
  OR item.original_authorization_version>(a->>'authorization_version')::bigint
  OR EXISTS(SELECT 1 FROM public.daily_review_events WHERE actor_user_id=item.actor_user_id AND request_id=item.request_id)
 THEN RAISE EXCEPTION '0134 seal coordinates or execution evidence conflict' USING ERRCODE='23514'; END IF;
 PERFORM public.rsc_daily_review_seal_live_0134(item,clock_timestamp());
 expected:=jsonb_build_object('reference',jsonb_build_object('cutoff_id',item.cutoff_id,'actor_person_id',item.actor_person_id,
  'original_authorization_version',item.original_authorization_version,'original_review_version',item.original_review_version,
  'operation',item.operation,'trace_request_id',item.request_id),'seal_id',item.id,'permanent_nonexecution',true);
 IF TG_TABLE_NAME='audit_events' THEN
  IF NEW.stream_key<>'authorization' OR NEW.action<>'daily_reconciliation.request_sealed'
   OR NEW.actor_user_id IS DISTINCT FROM item.actor_user_id OR NEW.request_id<>'daily-review-seal:'||item.id::text
   OR NEW.before_jsonb IS DISTINCT FROM '{}'::jsonb OR NEW.after_jsonb IS DISTINCT FROM expected
   OR NEW.created_at<>item.created_at OR NEW.occurred_at<>item.created_at
  THEN RAISE EXCEPTION '0134 seal audit binding invalid' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_WHEN='AFTER' THEN
  IF (SELECT count(*) FROM public.audit_events WHERE aggregate_type='daily_review_request_seal' AND aggregate_id=item.id::text)<>1
   OR NOT EXISTS(SELECT 1 FROM public.audit_events WHERE aggregate_type='daily_review_request_seal' AND aggregate_id=item.id::text
    AND stream_key='authorization' AND action='daily_reconciliation.request_sealed' AND actor_user_id=item.actor_user_id
    AND request_id='daily-review-seal:'||item.id::text AND before_jsonb='{}'::jsonb AND after_jsonb=expected
    AND created_at=item.created_at AND occurred_at=item.created_at)
  THEN RAISE EXCEPTION '0134 complete seal audit required' USING ERRCODE='23514'; END IF;
  RETURN NULL;
 END IF;
 RETURN NEW;
END
"""
EVENT="""
BEGIN
 -- The earlier 0132 event guard already holds session, principal and cutoff.
 PERFORM pg_advisory_xact_lock(hashtextextended('daily-review-request:'||NEW.actor_user_id||':'||NEW.request_id,0));
 IF EXISTS(SELECT 1 FROM public.daily_review_request_seals WHERE actor_user_id=NEW.actor_user_id AND request_id=NEW.request_id)
 THEN RAISE EXCEPTION '0134 original review request permanently sealed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END
"""
IMMUTABLE="BEGIN RAISE EXCEPTION '0134 original review seals immutable' USING ERRCODE='23514'; END"
FUNCTIONS={
 'rsc_daily_review_seal_live_0134':('e public.daily_review_request_seals, at_time timestamptz','void',LIVE,False),
 'rsc_guard_daily_review_seal_0134':('','trigger',GUARD,False),
 'rsc_guard_daily_review_trace_0134':('','trigger',EVENT,False),
 'rsc_guard_daily_review_seal_immutable_0134':('','trigger',IMMUTABLE,False),
}
FUNCTION_HASHES={k:hashlib.sha256(v[2].encode()).hexdigest() for k,v in FUNCTIONS.items()}
TRIGGERS={
 'trg_daily_review_seal_insert_0134':(TABLE,'rsc_guard_daily_review_seal_0134','INSERT',7,False),
 'trg_daily_review_seal_commit_0134':(TABLE,'rsc_guard_daily_review_seal_0134','INSERT',5,True),
 'trg_daily_review_seal_immutable_0134':(TABLE,'rsc_guard_daily_review_seal_immutable_0134','UPDATE OR DELETE',27,False),
 'trg_daily_review_seal_truncate_0134':(TABLE,'rsc_guard_daily_review_seal_immutable_0134','TRUNCATE',34,False),
 'trg_daily_review_events_trace_0134':('daily_review_events','rsc_guard_daily_review_trace_0134','INSERT',7,False),
 'trg_daily_review_seal_audit_0134':('audit_events','rsc_guard_daily_review_seal_0134','INSERT',7,False),
 'trg_daily_review_seal_audit_commit_0134':('audit_events','rsc_guard_daily_review_seal_0134','INSERT',5,True),
}

def _create_table():
    op.create_table(TABLE,
      sa.Column('id',sa.Uuid(),primary_key=True),
      sa.Column('cutoff_id',sa.Uuid(),sa.ForeignKey('daily_reconciliation_cutoffs.id'),nullable=False),
      sa.Column('actor_user_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
      sa.Column('actor_person_id',sa.Uuid(),sa.ForeignKey('people.id'),nullable=False),
      sa.Column('original_authorization_version',sa.BigInteger(),nullable=False),
      sa.Column('original_review_version',sa.BigInteger(),nullable=False),
      sa.Column('operation',sa.String(24),nullable=False),sa.Column('request_id',sa.String(160),nullable=False),
      sa.Column('actor_snapshot',sa.JSON().with_variant(JSONB(),'postgresql'),nullable=False),
      sa.Column('auth_session_id',sa.String(36),sa.ForeignKey('auth_sessions.id'),nullable=False),
      sa.Column('access_issued_at',sa.BigInteger(),nullable=False),sa.Column('access_expires_at',sa.BigInteger(),nullable=False),
      sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('transaction_id',sa.BigInteger(),nullable=False),
      sa.UniqueConstraint('actor_user_id','request_id',name='uq_daily_review_seal_request'),
      sa.CheckConstraint('original_authorization_version>0 AND original_review_version>=0',name='ck_daily_review_seal_versions'),
      sa.CheckConstraint("operation IN ('open','explain','approve','request_changes')",name='ck_daily_review_seal_operation'),
      sa.CheckConstraint('length(request_id) BETWEEN 8 AND 160',name='ck_daily_review_seal_request'),
      sa.CheckConstraint('access_issued_at>0 AND access_expires_at>access_issued_at',name='ck_daily_review_seal_token'))

def _verify():
    # Reuse the frozen function/trigger/ACL verifier, with this migration's
    # coordinates; earlier binding and cyclic-FK checks also remain enforced.
    checker=runpy.run_path(str(FOLDER/'20261111_0132_daily_reconciliation_review.py'))['_verify']
    checker.__globals__.update(FUNCTIONS=FUNCTIONS,FUNCTION_HASHES=FUNCTION_HASHES,TRIGGERS=TRIGGERS,TABLES=(TABLE,))
    checker()
    op.execute(f"""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
      WHERE i.indexrelid='public.uq_daily_review_seal_request'::regclass AND i.indrelid='public.{TABLE}'::regclass
       AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
       AND i.indnkeyatts=2 AND i.indnatts=2 AND i.indpred IS NULL AND i.indexprs IS NULL AND a.amname='btree'
       AND pg_get_indexdef(i.indexrelid,1,true)='actor_user_id' AND pg_get_indexdef(i.indexrelid,2,true)='request_id')
     THEN RAISE EXCEPTION '0134 original request uniqueness drift'; END IF; END $$""")

def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}:raise RuntimeError('0134 unsupported database')
    helper=runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'));helper['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0134 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.daily_review_events,public.daily_review_bindings,public.audit_events IN ACCESS EXCLUSIVE MODE')
    if up:_create_table()
    else:
        if dialect=='postgresql':op.execute('LOCK TABLE public.'+TABLE+' IN ACCESS EXCLUSIVE MODE');_verify()
        helper['_preflight']("EXISTS(SELECT 1 FROM "+TABLE+") OR EXISTS(SELECT 1 FROM audit_events WHERE aggregate_type='daily_review_request_seal' OR action='daily_reconciliation.request_sealed')",'0134 populated downgrade refused: original request seals must be retained')
    if dialect=='sqlite':
        if up:
            for action in ('UPDATE','DELETE'):op.execute(f"CREATE TRIGGER trg_daily_review_seal_{action.lower()}_0134 BEFORE {action} ON {TABLE} BEGIN SELECT RAISE(ABORT,'0134 original request seal immutable'); END")
        else:op.drop_table(TABLE)
        return
    if up:
        for name,(args,result,body,_) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $b$'+body+'$b$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({base["_types"](args)}) FROM PUBLIC,star_oam_api')
        for name,(table,function,events,_,deferred) in TRIGGERS.items():
            clause=f'CONSTRAINT TRIGGER {name} AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW' if deferred else f"TRIGGER {name} BEFORE {events} ON public.{table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}"
            op.execute(f'CREATE {clause} EXECUTE FUNCTION public.{function}()');op.execute(f'ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}')
        op.execute('REVOKE ALL ON public.'+TABLE+' FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox')
        op.execute('GRANT SELECT,INSERT ON public.'+TABLE+' TO star_oam_api');op.execute('GRANT SELECT ON public.'+TABLE+' TO star_oam_backup')
        _verify()
    else:
        for name,(table,*_) in TRIGGERS.items():op.execute(f'DROP TRIGGER {name} ON public.{table}')
        for name,(args,*_) in reversed(FUNCTIONS.items()):op.execute(f'DROP FUNCTION public.{name}({base["_types"](args)})')
        op.drop_table(TABLE)
    replace=runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='daily_review_seals_readiness_0134')

def upgrade():_transition(True)
def downgrade():_transition(False)
