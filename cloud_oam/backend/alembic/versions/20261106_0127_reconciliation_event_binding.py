"""Disambiguate the private reconciliation event variable without changing facts.

A real posted opening with a control difference reaches the 0026 consumption
trigger: its unqualified event_type collides with outbox_events.event_type.
Rename only the PL/pgSQL variable. Preserve function OID, invoker mode, ACL,
all exact event comparisons and existing command/seal/history relationships.
"""
import hashlib
from pathlib import Path
import re
import runpy
from types import FunctionType, SimpleNamespace
from alembic import op

revision='20261106_0127'
down_revision='20261105_0126'
branch_labels=depends_on=None
_folder=Path(__file__).parent
_previous=runpy.run_path(str(_folder/'20261105_0126_opening_actor_commit.py'))
OLD_HASH=_previous['NEW_HASH']
_ready=_previous['_previous']['_material']['_ready']
_ready_parent=_previous['_previous']['_material']['_older']['revision']
NEW_HASH=hashlib.sha256(_ready.replace(_ready_parent,revision).encode()).hexdigest()
FUNCTION='rsc_guard_opening_reconciliation_command_consumption_0026'
SIGNATURE='public.'+FUNCTION+'()'
LEGACY_HASH='16e5644cb12edf6e940f670a7546a3208d7c697f07c145a880457f9cc7c7b4cd'


def _frozen_body():
    legacy=runpy.run_path(str(_folder/'20260831_0026_opening_control_reconciliation.py'))
    statements=[];create=legacy['_create_postgresql_guards']
    # Render frozen DDL into a private collector. Never replace Alembic's global
    # operations object and never execute this historical DDL against the DB.
    FunctionType(create.__code__,dict(create.__globals__,op=SimpleNamespace(execute=statements.append)))()
    statement=next(s for s in statements if 'CREATE FUNCTION '+SIGNATURE in s)
    body=statement.split('AS $$',1)[1].split('$$',1)[0]
    if hashlib.sha256(body.encode()).hexdigest()!=LEGACY_HASH:
        raise RuntimeError('0127 frozen reconciliation source drift')
    return body


LEGACY_BODY=_frozen_body()
FIXED_BODY,replacement_count=re.subn(r'(?<![A-Za-z0-9_.])event_type\b','expected_reconciliation_event',LEGACY_BODY)
if replacement_count!=12 or FIXED_BODY.count('outbox.event_type')!=2:
    raise RuntimeError('0127 exact variable rename mismatch')
FIXED_HASH=hashlib.sha256(FIXED_BODY.encode()).hexdigest()
TRIGGERS={'trg_opening_reconciliation_consumptions_guard_0026':31,
          'trg_opening_reconciliation_consumptions_no_truncate_0026':34}


def _verify(expected_hash):
    op.execute(f"""DO $verify_0127$ BEGIN
        IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator'
           OR NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='{SIGNATURE}'::regprocedure
              AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
              AND NOT p.prosecdef AND p.prokind='f' AND p.prorettype='trigger'::regtype
              AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u' AND NOT p.proleakproof
              AND p.pronargs=0 AND p.pronargdefaults=0 AND p.proargmodes IS NULL
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.proconfig=ARRAY['search_path=pg_catalog, public']
              AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected_hash}'
              AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee<>p.proowner)) THEN
            RAISE EXCEPTION '0127 reconciliation source, owner or private ACL drift';
        END IF;
        IF (SELECT count(*) FROM pg_trigger WHERE tgfoid='{SIGNATURE}'::regprocedure)<>2 THEN
            RAISE EXCEPTION '0127 reconciliation trigger closure drift';
        END IF;
    END $verify_0127$""")
    for name,kind in TRIGGERS.items():
        op.execute(f"""DO $trigger_0127$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgname='{name}'
                AND t.tgrelid='public.opening_control_reconciliation_command_consumptions'::regclass
                AND t.tgfoid='{SIGNATURE}'::regprocedure AND t.tgtype={kind} AND t.tgenabled='A'
                AND NOT t.tgdeferrable AND NOT t.tginitdeferred AND NOT t.tgisinternal
                AND t.tgconstraint=0 AND t.tgqual IS NULL AND t.tgnargs=0 AND t.tgattr=''::int2vector) THEN
                RAISE EXCEPTION '0127 reconciliation trigger configuration drift';
            END IF;
        END $trigger_0127$""")


def _transition(up):
    dialect=op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:raise RuntimeError('0127 unsupported database')
    if dialect=='sqlite':return
    op.execute('LOCK TABLE public.alembic_version,public.opening_control_reconciliation_command_consumptions IN ACCESS EXCLUSIVE MODE')
    _verify(LEGACY_HASH if up else FIXED_HASH)
    replace=runpy.run_path(str(_folder/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature=SIGNATURE,expected_hash=LEGACY_HASH if up else FIXED_HASH,
        replacement_hash=FIXED_HASH if up else LEGACY_HASH,
        replacements=((LEGACY_BODY,FIXED_BODY),) if up else ((FIXED_BODY,LEGACY_BODY),),label='reconciliation_event_0127')
    _verify(FIXED_HASH if up else LEGACY_HASH)
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',expected_hash=OLD_HASH if up else NEW_HASH,
        replacement_hash=NEW_HASH if up else OLD_HASH,replacements=((down_revision,revision),) if up else ((revision,down_revision),),
        label='reconciliation_readiness_0127')


def upgrade():_transition(True)
def downgrade():_transition(False)
