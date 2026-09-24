"""Bind new opening tasks to their actor version and recheck at commit.

Historical NULL versions remain unknown. No historical actor is inferred from
current users. The new proof is internal: runtime roles gain no capability.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = '20261105_0126'
down_revision = '20261104_0125'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261104_0125_opening_publication_admission.py'))
_directory = _previous['_previous']
OLD_HASH = _previous['NEW_HASH']
NEW_HASH = hashlib.sha256(_previous['_material']['_ready'].replace(
    _previous['_material']['_older']['revision'],revision).encode()).hexdigest()
COLUMN = 'opening_authorization_version'
CONSTRAINT = 'ck_opening_authorization_version_0126'
CHECK = "opening_authorization_version IS NULL OR (task_type = 'opening' AND opening_authorization_version > 0)"

# Freeze the already-reviewed role/scope and global-deny semantics. This private
# function has no directory query and samples time after all admission waits.
ACTOR_BODY = _directory['BODY'].split('    SELECT COALESCE(jsonb_agg(item ORDER BY id)',1)[0]
assert ACTOR_BODY.count('at_time timestamptz := statement_timestamp();') == 1
ACTOR_BODY = ACTOR_BODY.replace('at_time timestamptz := statement_timestamp();',
    'at_time timestamptz := clock_timestamp();\n    boundary timestamptz;')
old = """       OR p_region='00000000-0000-0000-0000-000000000000'::uuid
       OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
       OR p_after='00000000-0000-0000-0000-000000000000'::uuid THEN"""
assert ACTOR_BODY.count(old) == 1
ACTOR_BODY = ACTOR_BODY.replace(old,"       OR p_region='00000000-0000-0000-0000-000000000000'::uuid THEN")
ACTOR_BODY = ACTOR_BODY.replace('0124 directory','0126 opening actor').replace('0124 invalid directory','0126 invalid actor')
ACTOR_BODY += """    -- Reject any authorization time boundary crossed during the proof,
    -- including a scheduled global deny that was not effective at at_time.
    SELECT min(value) INTO boundary FROM (
        SELECT a.valid_from value FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
          WHERE a.user_id=p_user AND r.status='active' AND a.status IN ('active','scheduled')
            AND a.revoked_at IS NULL AND a.valid_from>at_time
        UNION ALL SELECT a.valid_to FROM public.role_assignments a JOIN public.roles r ON r.id=a.role_id
          WHERE a.user_id=p_user AND r.status='active' AND a.status IN ('active','scheduled')
            AND a.revoked_at IS NULL AND a.valid_to>at_time
    ) boundaries;
    IF boundary IS NOT NULL AND clock_timestamp()>=boundary THEN
        RAISE EXCEPTION '0126 opening actor changed during proof' USING ERRCODE='42501';
    END IF;
    RETURN;
END
"""
ACTOR_BODY = ACTOR_BODY.replace("USING ERRCODE='42501'",
    "USING ERRCODE='42501', CONSTRAINT='opening_actor_admission_0126'")

GUARD_BODY = """
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.opening_authorization_version IS DISTINCT FROM OLD.opening_authorization_version THEN
            RAISE EXCEPTION '0126 opening authorization evidence is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.task_type<>'opening' THEN
        IF NEW.opening_authorization_version IS NOT NULL THEN
            RAISE EXCEPTION '0126 opening authorization belongs to opening tasks only' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.opening_authorization_version IS NULL OR NEW.opening_authorization_version<1 THEN
        RAISE EXCEPTION '0126 new opening requires authorization evidence' USING ERRCODE='23514';
    END IF;
    -- Match the established principal -> control-publication lock order.
    -- The application already holds its entire actor/assignee principal set.
    PERFORM public.rsc_lock_formal_principal_graph_0026(ARRAY[NEW.created_by_user_id::text]);
    PERFORM public.rsc_assert_opening_publication_0125(NEW.control_source_system_id,NEW.control_sync_run_id,NEW.region_org_id);
    PERFORM public.rsc_assert_opening_actor_0126(NEW.created_by_user_id,NEW.opening_authorization_version,NEW.region_org_id);
    RETURN NEW;
END
"""
FUNCTIONS = {
    'rsc_assert_opening_actor_0126': ('text, bigint, uuid','p_user text,p_version bigint,p_region uuid','void',ACTOR_BODY),
    'rsc_guard_opening_actor_0126': ('','','trigger',GUARD_BODY),
}
BODY_HASHES = {name:hashlib.sha256(row[3].encode()).hexdigest() for name,row in FUNCTIONS.items()}
TRIGGERS = {'trg_opening_actor_insert_0126':(7,False),
            'trg_opening_actor_commit_0126':(5,True),
            'trg_opening_actor_immutable_0126':(19,False)}


def _verify():
    for name,(args,_,returns,body) in FUNCTIONS.items():
        op.execute(f"""DO $verify_0126$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='public.{name}({args})'::regprocedure
            AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
            AND p.prokind='f' AND p.prorettype='{returns}'::regtype AND NOT p.proretset
            AND p.prosecdef AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u'
            AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
            AND p.proconfig=ARRAY['search_path=pg_catalog, public']
            AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{BODY_HASHES[name]}'
            AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                WHERE a.grantee<>p.proowner)) THEN
            RAISE EXCEPTION '0126 actor function source or ACL drift'; END IF;
          END $verify_0126$""")
    for name,(kind,deferred) in TRIGGERS.items():
        flag=str(deferred).lower()
        op.execute(f"""DO $trigger_0126$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.stocktake_tasks'::regclass
            AND t.tgname='{name}' AND t.tgfoid='public.rsc_guard_opening_actor_0126()'::regprocedure
            AND t.tgtype={kind} AND t.tgenabled='A' AND NOT t.tgisinternal
            AND t.tgdeferrable={flag} AND t.tginitdeferred={flag} AND t.tgqual IS NULL AND t.tgnargs=0) THEN
            RAISE EXCEPTION '0126 actor trigger drift'; END IF;
          END $trigger_0126$""")
    op.execute(f"""DO $column_0126$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.stocktake_tasks'::regclass
        AND attname='{COLUMN}' AND atttypid='bigint'::regtype AND NOT attnotnull AND NOT atthasdef AND NOT attisdropped)
        OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.stocktake_tasks'::regclass
          AND conname='{CONSTRAINT}' AND contype='c' AND convalidated AND NOT connoinherit
          AND regexp_replace(pg_get_constraintdef(oid), '[[:space:]()]', '', 'g')=
              'CHECKopening_authorization_versionISNULLORtask_type::text=''opening''::textANDopening_authorization_version>0') THEN
        RAISE EXCEPTION '0126 authorization column drift'; END IF;
      END $column_0126$""")


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'postgresql','sqlite'}:raise RuntimeError('0126 unsupported database')
    if dialect=='sqlite':
        runpy.run_path(str(_folder/'20260927_0087_inbound_fulfillment_boundary.py'))['_begin_sqlite']()
    if dialect=='postgresql':
        op.execute("""DO $owner_0126$ BEGIN
          IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN
            RAISE EXCEPTION '0126 direct schema owner required'; END IF; END $owner_0126$""")
        op.execute('LOCK TABLE public.alembic_version, public.stocktake_tasks IN ACCESS EXCLUSIVE MODE')
    if not up:
        if dialect=='postgresql':_verify()
        # Refuse before any DDL. Dropping a stored version would erase evidence.
        legacy=runpy.run_path(str(_folder/'20260927_0087_inbound_fulfillment_boundary.py'))
        legacy['_preflight']('EXISTS (SELECT 1 FROM stocktake_tasks WHERE opening_authorization_version IS NOT NULL)',
                            '0126 downgrade blocked: opening authorization evidence must be retained')
    if up:
        op.add_column('stocktake_tasks',sa.Column(COLUMN,sa.BigInteger(),sa.CheckConstraint(CHECK,name=CONSTRAINT),nullable=True))
    if dialect=='sqlite':
        # SQLite is only a domain-algorithm reference; PG16 proves deferred
        # current authorization. Still protect stored/unknown version identity.
        if up:
            op.execute("""CREATE TRIGGER trg_opening_actor_immutable_0126 BEFORE UPDATE OF opening_authorization_version
              ON stocktake_tasks WHEN NEW.opening_authorization_version IS NOT OLD.opening_authorization_version
              BEGIN SELECT RAISE(ABORT, '0126 opening authorization evidence is immutable'); END""")
        else:
            op.execute('DROP TRIGGER trg_opening_actor_immutable_0126')
            op.drop_column('stocktake_tasks',COLUMN)
        return
    if up:
        for name,(args,params,returns,body) in FUNCTIONS.items():
            op.execute(f'CREATE FUNCTION public.{name}({params}) RETURNS {returns} LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog, public AS $body$'+body+'$body$')
            op.execute(f'REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC')
        op.execute('CREATE TRIGGER trg_opening_actor_insert_0126 BEFORE INSERT ON public.stocktake_tasks FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_opening_actor_0126()')
        op.execute('CREATE CONSTRAINT TRIGGER trg_opening_actor_commit_0126 AFTER INSERT ON public.stocktake_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_opening_actor_0126()')
        op.execute('CREATE TRIGGER trg_opening_actor_immutable_0126 BEFORE UPDATE ON public.stocktake_tasks FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_opening_actor_0126()')
        for name in TRIGGERS:op.execute('ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER '+name)
        _verify()
    else:
        for name in TRIGGERS:op.execute('DROP TRIGGER '+name+' ON public.stocktake_tasks')
        for name,(args,*_) in reversed(tuple(FUNCTIONS.items())):op.execute(f'DROP FUNCTION public.{name}({args})')
        op.drop_column('stocktake_tasks',COLUMN)
    replace=runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),label='opening_actor_readiness_0126')


def upgrade():_transition(True)
def downgrade():_transition(False)
