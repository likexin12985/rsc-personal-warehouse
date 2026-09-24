"""Keep unrelated events outside stock-return ledger locks.

0100/0101/0103 already ignore events of other aggregates, but only after acquiring the
inventory ledger. That late no-op lock in a publisher's deferred COMMIT forms a
cycle with an opening starter waiting for the publisher's principal lock. Move
the same no-op decision ahead of the lock; all seal and command checks remain.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = '20261102_0123'
down_revision = '20261101_0122'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261101_0122_zero_control_opening.py'))
OLD_HASH = _previous['NEW_HASH']
_material = _previous['_material']
NEW_HASH = hashlib.sha256(_material['_ready'].replace(_material['_older']['revision'], revision).encode()).hexdigest()
SIGNATURE = 'public.rsc_guard_stock_operation_seal_0101()'
LEGACY_HASH = '6a2f63d9156030fd2336d4a19a1a3600d9d9fafcd9f942ee42690f56a540dd07'
LEGACY_FRAGMENT = """BEGIN
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"""
FIXED_FRAGMENT = """BEGIN
    -- Match the existing no-op audit branch before taking any ledger lock.
    -- Nested IF avoids reading audit-only fields on command/seal records.
    IF TG_TABLE_NAME='audit_events' THEN
        IF NEW.aggregate_type <> 'stock_operation_command_seal' THEN RETURN NULL; END IF;
    END IF;
    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"""
_legacy = runpy.run_path(str(_folder/'20261015_0105_stock_return_receipts.py'))['_sources']()[SIGNATURE][1]
if hashlib.sha256(_legacy.encode()).hexdigest() != LEGACY_HASH or _legacy.count(LEGACY_FRAGMENT) != 1:
    raise RuntimeError('0123 frozen seal function source drift')
FIXED_HASH = hashlib.sha256(_legacy.replace(LEGACY_FRAGMENT,FIXED_FRAGMENT).encode()).hexdigest()
DISPATCH_PATCHES = {}
for signature, migration_file, legacy_hash, aggregates in (
    ('public.rsc_dispatch_stock_return_0100()', '20261013_0103_stock_return_outbounds.py',
     'f1c3ec8f8f697e8cb1612dd6226f440b0cfb68711e38ad2b402f726a2280d15d',
     "'stock_operation_order','stock_operation_cancellation','inventory_transaction'"),
    ('public.rsc_dispatch_stock_return_outbound_0103()', '20261021_0111_stock_return_inbound_proof.py',
     '67e655a9400244d55405402119171e23a77db65e5ec77e15318aeb212bf5504f',
     "'stock_operation_outbound','inventory_transaction'"),
):
    body = runpy.run_path(str(_folder/migration_file))['_sources']()[signature][1]
    anchor = "BEGIN\n"+body.split("BEGIN\n",1)[1].splitlines(keepends=True)[0]
    fragment = "BEGIN\n" + f"""    -- Unrelated event aggregates are an existing no-op, before any lock.
    IF TG_TABLE_NAME IN ('audit_events','outbox_events','state_transition_events') THEN
        IF NEW.aggregate_type NOT IN ({aggregates}) THEN RETURN NULL; END IF;
    END IF;
"""
    if hashlib.sha256(body.encode()).hexdigest() != legacy_hash or body.count(anchor) != 1:
        raise RuntimeError('0123 frozen return dispatcher source drift')
    fragment += anchor.removeprefix("BEGIN\n")
    DISPATCH_PATCHES[signature] = (body,body.replace(anchor,fragment),anchor,fragment)
PATCHES = {SIGNATURE: (_legacy,_legacy.replace(LEGACY_FRAGMENT,FIXED_FRAGMENT),LEGACY_FRAGMENT,FIXED_FRAGMENT),
           **DISPATCH_PATCHES}
AUDIT_TRIGGERS = {
    SIGNATURE: 'trg_audit_events_stock_operation_seal_0101',
    'public.rsc_dispatch_stock_return_0100()': 'trg_audit_events_return_0100',
    'public.rsc_dispatch_stock_return_outbound_0103()': 'trg_audit_events_return_outbound_0103',
}
# Drain every table that invokes a patched function, including deferred event
# checks, before validating and replacing the three bodies in one transaction.
LOCK_TABLES = {'alembic_version','inventory_ledger_heads'}
for migration_file in ('20261010_0100_stock_return_orders.py','20261011_0101_stock_return_request_seals.py',
                       '20261013_0103_stock_return_outbounds.py','20261014_0104_stock_return_shipments.py',
                       '20261015_0105_stock_return_receipts.py'):
    for table,_,function,*_ in runpy.run_path(str(_folder/migration_file))['TRIGGERS'].values():
        if 'public.'+function+'()' in PATCHES: LOCK_TABLES.add(table)
LOCK_TABLES = tuple(sorted(LOCK_TABLES))


def _sources():
    return {signature: values[:2] for signature,values in PATCHES.items()}


def _verify(signature, expected_hash):
    op.execute(f"""DO $verify_0123$ BEGIN
        IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator'
           OR NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='{signature}'::regprocedure
              AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
              AND p.prosecdef AND p.prokind='f' AND p.prorettype='trigger'::regtype
              AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u'
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.proconfig=ARRAY['search_path=pg_catalog, public']
              AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected_hash}'
              AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee<>p.proowner))
           OR NOT EXISTS (SELECT 1 FROM pg_trigger t
              WHERE t.tgrelid='public.audit_events'::regclass
                AND t.tgname='{AUDIT_TRIGGERS[signature]}'
                AND t.tgfoid='{signature}'::regprocedure AND t.tgtype=5
                AND t.tgenabled='A' AND t.tgdeferrable AND t.tginitdeferred
                AND NOT t.tgisinternal AND t.tgqual IS NULL AND t.tgnargs=0) THEN
            RAISE EXCEPTION '0123 return function source, ownership, ACL or trigger drift';
        END IF;
    END $verify_0123$""")


def _transition(up):
    dialect = op.get_bind().dialect.name
    if dialect not in {'sqlite','postgresql'}:
        raise RuntimeError('0123 supports PostgreSQL and SQLite only')
    # No data or schema change on SQLite. Downgrade restores only the former
    # lock behavior; it never removes a fact or loosens the retained seal proof.
    if dialect != 'postgresql':
        return
    op.execute('LOCK TABLE '+','.join('public.'+table for table in LOCK_TABLES)+' IN ACCESS EXCLUSIVE MODE')
    for signature,(old,new,*_) in PATCHES.items():
        _verify(signature,hashlib.sha256((old if up else new).encode()).hexdigest())
    replace = runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    for signature,(old,new,anchor,fragment) in PATCHES.items():
        replace(signature=signature,
                expected_hash=hashlib.sha256((old if up else new).encode()).hexdigest(),
                replacement_hash=hashlib.sha256((new if up else old).encode()).hexdigest(),
                replacements=((anchor,fragment),) if up else ((fragment,anchor),),
                label='return_audit_lock_scope_0123')
        _verify(signature,hashlib.sha256((new if up else old).encode()).hexdigest())
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_HASH if up else NEW_HASH,replacement_hash=NEW_HASH if up else OLD_HASH,
            replacements=((down_revision,revision),) if up else ((revision,down_revision),),
            label='seal_audit_lock_scope_readiness_0123')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
