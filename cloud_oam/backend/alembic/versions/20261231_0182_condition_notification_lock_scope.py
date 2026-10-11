"""Keep ordinary notification projections out of the condition lock pair.

The 0167 condition fences intentionally remain broad: they must still observe
late condition evidence. Their lock triggers, however, used to take the shared
inventory ledger-head row lock for every relevant notification. That made an
ordinary inventory notification wait behind an unrelated transaction and fail
under the PostgreSQL 16 statement timeout. This successor changes only the
trigger function bodies and keeps the frozen 0167 catalog immutable.
"""

from hashlib import sha256
import json
from pathlib import Path
import runpy

from alembic import op


revision = '20261231_0182'
down_revision = '20261230_0181'
branch_labels = depends_on = None

FOLDER = Path(__file__).parents[1] / 'return_condition_0167'
DATA = json.loads((FOLDER / 'catalog.json').read_text())
ROW = DATA['functions']['rsc_condition_decision_seal_lock()']['after']
SIGNATURE = 'public.rsc_condition_decision_seal_lock()'
LEGACY_SOURCE = ROW['prosrc']
LEGACY_HASH = '3e954671d1f03deb037d6cf7c70ce7f171e12a44c2402cb44e2c30c1d2a44d7c'
if sha256(LEGACY_SOURCE.encode()).hexdigest() != LEGACY_HASH:
    raise ValueError('0182 frozen 0167 condition lock source drift')

READ_COMMITTED_ANCHOR = (
    "    IF current_setting('transaction_isolation')<>'read committed' THEN\n"
    "        RAISE EXCEPTION 'condition decision seal fence requires read committed' USING ERRCODE='23514'; END IF;\n"
)
LOCK_SCOPE_GUARD = """    -- Notification projections remain under the deferred condition-decision
    -- fence, but ordinary inventory notifications must not serialize
    -- unrelated transactions on the shared ledger head. Only condition
    -- evidence (or the reserved decision-seal namespace) participates in the
    -- global decision lock. Any condition reference evidence, including
    -- payload request IDs, is retained; generic transaction fields bypass.
    IF TG_TABLE_NAME='notification_events'
       AND NOT (
          COALESCE(COALESCE(next_row->>'business_type',prior->>'business_type') IN ('stock_condition_decision_seal','stock_condition_event'),false)
          OR COALESCE(COALESCE(next_row->>'event_type',prior->>'event_type') LIKE 'stock_condition.%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'schema_version',prior->'payload_jsonb'->>'schema_version')='condition_result/1',false)
          OR COALESCE(COALESCE(next_row->>'action',prior->>'action')='seal_condition_decision_request',false)
          OR COALESCE(COALESCE(next_row->>'request_id',prior->>'request_id') LIKE 'condition-decision-seal:%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'aggregate_type',prior->'payload_jsonb'->>'aggregate_type')='stock_condition_decision_seal',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'action',prior->'payload_jsonb'->>'action')='seal_condition_decision_request',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id') LIKE 'condition-decision-seal:%',false)

          OR COALESCE(COALESCE(next_row->>'key_token',prior->>'key_token') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'raw_key_hash',prior->>'raw_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'regional_key_hash',prior->>'regional_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'headquarters_key_hash',prior->>'headquarters_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'disposition_key_hash',prior->>'disposition_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'return_key_hash',prior->>'return_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'reversal_key_hash',prior->>'reversal_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'approval_key_hash',prior->>'approval_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'correction_key_hash',prior->>'correction_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'recovery_key_hash',prior->>'recovery_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'scrap_key_hash',prior->>'scrap_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'condition_key_hash',prior->>'condition_key_hash') IS NOT NULL,false)
          OR COALESCE(next_row->'payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id',next_row->'metadata_jsonb'->>'request_id',prior->'metadata_jsonb'->>'request_id',next_row->'after_jsonb'->>'request_id',prior->'after_jsonb'->>'request_id') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'request_reference',prior->'payload_jsonb'->>'request_reference',next_row->'metadata_jsonb'->>'request_reference',prior->'metadata_jsonb'->>'request_reference',next_row->'after_jsonb'->>'request_reference',prior->'after_jsonb'->>'request_reference') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'actor_user_id',prior->'payload_jsonb'->>'actor_user_id',next_row->'metadata_jsonb'->>'actor_user_id',prior->'metadata_jsonb'->>'actor_user_id',next_row->'after_jsonb'->>'actor_user_id',prior->'after_jsonb'->>'actor_user_id') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'actor_person_id',prior->'payload_jsonb'->>'actor_person_id',next_row->'metadata_jsonb'->>'actor_person_id',prior->'metadata_jsonb'->>'actor_person_id',next_row->'after_jsonb'->>'actor_person_id',prior->'after_jsonb'->>'actor_person_id') IS NOT NULL
       ) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
"""
if LEGACY_SOURCE.count(READ_COMMITTED_ANCHOR) != 1:
    raise ValueError('0182 condition lock anchor drift')
FIXED_SOURCE = LEGACY_SOURCE.replace(
    READ_COMMITTED_ANCHOR, READ_COMMITTED_ANCHOR + LOCK_SCOPE_GUARD, 1)
FIXED_HASH = sha256(FIXED_SOURCE.encode()).hexdigest()

SEAL_ROW = DATA['functions']['rsc_condition_seal_lock()']['after']
SEAL_SIGNATURE = 'public.rsc_condition_seal_lock()'
SEAL_LEGACY_SOURCE = SEAL_ROW['prosrc']
SEAL_LEGACY_HASH = 'dc92d427506ff729adb759f3adcc2ed772cede1a3cfe9715e6a158c17cb6fb90'
if sha256(SEAL_LEGACY_SOURCE.encode()).hexdigest() != SEAL_LEGACY_HASH:
    raise ValueError('0182 frozen 0167 condition seal lock source drift')
SEAL_LOCK_SCOPE_GUARD = """    -- Notification projections remain under the deferred condition-seal
    -- fence, but ordinary inventory notifications must not serialize
    -- unrelated transactions on the shared ledger head. Only condition
    -- evidence (or the reserved condition-seal namespace) participates in
    -- the global decision lock. A payload request_id/request_reference alone
    -- is insufficient because shipment and receipt notifications carry them.
    IF TG_TABLE_NAME='notification_events'
       AND NOT (
          COALESCE(COALESCE(next_row->>'business_type',prior->>'business_type') IN ('stock_condition_request_seal','stock_condition_event'),false)
          OR COALESCE(COALESCE(next_row->>'event_type',prior->>'event_type') LIKE 'stock_condition.%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'schema_version',prior->'payload_jsonb'->>'schema_version')='condition_result/1',false)
          OR COALESCE(COALESCE(next_row->>'action',prior->>'action')='seal_condition_request',false)
          OR COALESCE(COALESCE(next_row->>'request_id',prior->>'request_id') LIKE 'condition-seal:%',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'aggregate_type',prior->'payload_jsonb'->>'aggregate_type') IN ('stock_condition_request_seal','stock_condition_event'),false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'action',prior->'payload_jsonb'->>'action')='seal_condition_request',false)
          OR COALESCE(COALESCE(next_row->'payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id') LIKE 'condition-seal:%',false)

          OR COALESCE(COALESCE(next_row->>'key_token',prior->>'key_token') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'raw_key_hash',prior->>'raw_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'regional_key_hash',prior->>'regional_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'headquarters_key_hash',prior->>'headquarters_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'disposition_key_hash',prior->>'disposition_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'return_key_hash',prior->>'return_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'reversal_key_hash',prior->>'reversal_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'approval_key_hash',prior->>'approval_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'correction_key_hash',prior->>'correction_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'recovery_key_hash',prior->>'recovery_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'scrap_key_hash',prior->>'scrap_key_hash') IS NOT NULL,false)
          OR COALESCE(COALESCE(next_row->>'condition_key_hash',prior->>'condition_key_hash') IS NOT NULL,false)
          OR COALESCE(next_row->'payload_jsonb'->>'request_id',prior->'payload_jsonb'->>'request_id',next_row->'metadata_jsonb'->>'request_id',prior->'metadata_jsonb'->>'request_id',next_row->'after_jsonb'->>'request_id',prior->'after_jsonb'->>'request_id') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'request_reference',prior->'payload_jsonb'->>'request_reference',next_row->'metadata_jsonb'->>'request_reference',prior->'metadata_jsonb'->>'request_reference',next_row->'after_jsonb'->>'request_reference',prior->'after_jsonb'->>'request_reference') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'actor_user_id',prior->'payload_jsonb'->>'actor_user_id',next_row->'metadata_jsonb'->>'actor_user_id',prior->'metadata_jsonb'->>'actor_user_id',next_row->'after_jsonb'->>'actor_user_id',prior->'after_jsonb'->>'actor_user_id') IS NOT NULL
          OR COALESCE(next_row->'payload_jsonb'->>'actor_person_id',prior->'payload_jsonb'->>'actor_person_id',next_row->'metadata_jsonb'->>'actor_person_id',prior->'metadata_jsonb'->>'actor_person_id',next_row->'after_jsonb'->>'actor_person_id',prior->'after_jsonb'->>'actor_person_id') IS NOT NULL
       ) THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
"""
SEAL_LOCK_ANCHOR = "    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"
if SEAL_LEGACY_SOURCE.count(SEAL_LOCK_ANCHOR) != 1:
    raise ValueError('0182 condition seal lock anchor drift')
SEAL_FIXED_SOURCE = SEAL_LEGACY_SOURCE.replace(
    SEAL_LOCK_ANCHOR, SEAL_LOCK_SCOPE_GUARD + SEAL_LOCK_ANCHOR, 1)
SEAL_FIXED_HASH = sha256(SEAL_FIXED_SOURCE.encode()).hexdigest()

READY_SIGNATURE = 'public.rsc_oam_runtime_binding_ready_0044()'
READY_ROW = json.loads((Path(__file__).parents[2] /
                        'app/material_request_contact_envelope_security.json').read_text())['readiness']['after']
READY_LEGACY_SOURCE = READY_ROW['prosrc']
READY_LEGACY_HASH = 'beaeca9daa8c2f3d9407358d08d28a28554b7abf1a63a27fed622833fee7554f'
if sha256(READY_LEGACY_SOURCE.encode()).hexdigest() != READY_LEGACY_HASH \
        or READY_LEGACY_SOURCE.count('20261230_0181') != 1:
    raise ValueError('0182 frozen readiness source drift')
READY_SOURCE = READY_LEGACY_SOURCE.replace('20261230_0181', revision, 1)
READY_HASH = sha256(READY_SOURCE.encode()).hexdigest()

# All current trigger consumers are drained before the function is replaced.
# The deferred triggers remain attached and immutable; only their function
# source changes.  Keep this list derived from the frozen catalog.
LOCK_TABLES = {'alembic_version', 'inventory_ledger_heads'}
for table, change in DATA['tables'].items():
    for trigger in change['after'].get('triggers', []):
        if trigger.get('function_signature') in {
                'rsc_condition_decision_seal_lock()', 'rsc_condition_seal_lock()'}:
            LOCK_TABLES.add(table)
LOCK_TABLES = tuple(sorted(LOCK_TABLES))


def _sources():
    return {SIGNATURE: (LEGACY_SOURCE, FIXED_SOURCE),
            SEAL_SIGNATURE: (SEAL_LEGACY_SOURCE, SEAL_FIXED_SOURCE),
            READY_SIGNATURE: (READY_LEGACY_SOURCE, READY_SOURCE)}


def _verify(expected_hash, signature=SIGNATURE):
    op.execute(f"""DO $verify_0182$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{signature}');
    function_source text;
BEGIN
    SELECT p.prosrc INTO function_source
      FROM pg_catalog.pg_proc AS p
     WHERE p.oid = function_oid
       AND p.proowner = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname='star_oam_migrator')
       AND p.prosecdef AND p.prokind='f' AND p.prorettype='trigger'::regtype
       AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u'
       AND p.prolang=(SELECT oid FROM pg_catalog.pg_language WHERE lanname='plpgsql')
       AND p.proconfig=ARRAY['search_path=pg_catalog, public']
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')='{expected_hash}'
       AND NOT EXISTS (
           SELECT 1 FROM pg_catalog.aclexplode(
               COALESCE(p.proacl, pg_catalog.acldefault('f', p.proowner))) a
            WHERE a.grantee <> p.proowner);
    IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator'
       OR function_source IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_trigger t
            WHERE t.tgfoid=function_oid AND t.tgenabled='A'
              AND NOT t.tgisinternal AND t.tgnargs=0) THEN
        RAISE EXCEPTION '0182 condition notification lock source, ownership, ACL or trigger drift';
    END IF;
END
$verify_0182$""")


def _transition(up):
    db = op.get_bind()
    if db.dialect.name == 'sqlite':
        return
    if db.dialect.name != 'postgresql':
        raise ValueError('0182 requires PostgreSQL16 or SQLite schema tooling')
    op.execute('LOCK TABLE ' + ','.join('public.' + table for table in LOCK_TABLES)
               + ' IN ACCESS EXCLUSIVE MODE')
    helper = runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    if up:
        transitions = (
            (SIGNATURE, LEGACY_SOURCE, FIXED_SOURCE),
            (SEAL_SIGNATURE, SEAL_LEGACY_SOURCE, SEAL_FIXED_SOURCE),
        )
    else:
        transitions = (
            (SIGNATURE, FIXED_SOURCE, LEGACY_SOURCE),
            (SEAL_SIGNATURE, SEAL_FIXED_SOURCE, SEAL_LEGACY_SOURCE),
        )
    for signature, old, new in transitions:
        _verify(sha256(old.encode()).hexdigest(), signature)
        helper['_replace_function_source'](
            signature=signature,
            expected_hash=sha256(old.encode()).hexdigest(),
            replacement_hash=sha256(new.encode()).hexdigest(),
            replacements=((old, new),),
            label='condition_notification_lock_scope_0182')
        _verify(sha256(new.encode()).hexdigest(), signature)
    ready_old, ready_new = ((READY_LEGACY_SOURCE, READY_SOURCE) if up
                            else (READY_SOURCE, READY_LEGACY_SOURCE))
    helper['_replace_function_source'](
        signature=READY_SIGNATURE,
        expected_hash=sha256(ready_old.encode()).hexdigest(),
        replacement_hash=sha256(ready_new.encode()).hexdigest(),
        replacements=((ready_old, ready_new),),
        label='condition_notification_readiness_0182')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
