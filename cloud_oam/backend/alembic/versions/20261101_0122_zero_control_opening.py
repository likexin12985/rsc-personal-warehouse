"""Admit a sealed, proven zero control publication to the opening lock graph.

The old lock graph rejected every run without inbox rows, including an actual
0121 zero publication. Keep 0027 frozen and replace only its inbox-count guard;
nonempty imports retain their exact existing lock order and row-count limits.
This is a locking prerequisite, not freshness or business authorization.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = '20261101_0122'
down_revision = '20261031_0121'
branch_labels = depends_on = None
_folder = Path(__file__).parent
_previous = runpy.run_path(str(_folder/'20261031_0121_control_publications.py'))
OLD_HASH = _previous['NEW_HASH']
_material = _previous['_material']
NEW_HASH = hashlib.sha256(_material['_ready'].replace(_material['_older']['revision'], revision).encode()).hexdigest()
SIGNATURE = 'public.rsc_lock_opening_control_import_0027(uuid, uuid)'
LEGACY_HASH = 'de1a1ada12225d2c38d695448a475ebf16f404fdefb7a438a1f8fb8f50d996d1'
FIXED_HASH = 'c2004a551b55bba574cf26779e59ef04ca11caa0c9ec653183c57363c19b405a'

LEGACY_FRAGMENT = """    IF graph_count < 1 OR graph_count > 100000 THEN
        RAISE EXCEPTION 'formal PostgreSQL lock graph invariant violated';
    END IF;
    PERFORM event.id"""
FIXED_FRAGMENT = """    IF graph_count > 100000 OR (graph_count = 0 AND NOT EXISTS (
        SELECT 1
          FROM public.control_projection_publications publication
          JOIN public.sync_runs run ON run.id=publication.sync_run_id
          JOIN public.sync_batches batch ON batch.id=publication.sync_batch_id
         WHERE publication.source_system_id=requested_source_system_id
           AND publication.sync_run_id=requested_sync_run_id
           AND publication.record_count=0 AND publication.origin_count=0
           AND run.source_system_id=publication.source_system_id
           AND run.run_key='control-publication:'||publication.id::text
           AND run.scope_key='oam_inventory_control:region:'||publication.region_org_id::text
           AND run.status='completed' AND run.completed_at=publication.created_at
           AND run.manifest_sha256=publication.payload_jsonb->>'control_manifest_sha256'
           AND batch.run_id=run.id AND batch.entity_type='oam_inventory_control'
           AND batch.sequence=1 AND batch.status='applied' AND batch.record_count=0
           AND (SELECT count(*) FROM public.sync_batches WHERE run_id=run.id)=1
           AND NOT EXISTS (SELECT 1 FROM public.sync_inbox_events WHERE batch_id=batch.id)
           AND NOT EXISTS (SELECT 1 FROM public.control_projection_lines WHERE publication_id=publication.id)
           AND NOT EXISTS (SELECT 1 FROM public.control_projection_origins WHERE publication_id=publication.id)
    )) THEN
        RAISE EXCEPTION 'formal PostgreSQL lock graph invariant violated';
    END IF;
    PERFORM event.id"""

# Source/run locks drain users of the replaced function. The publication and
# opening fact locks also serialize retention checks against concurrent writers.
LOCK_TABLES = ('alembic_version', 'control_projection_lines', 'control_projection_origins',
               'control_projection_publications', 'source_systems', 'stocktake_tasks',
               'sync_batches', 'sync_inbox_events', 'sync_runs')
RETENTION = """EXISTS (SELECT 1 FROM stocktake_tasks task
    JOIN control_projection_publications publication ON publication.sync_run_id=task.control_sync_run_id
    WHERE task.task_type='opening' AND publication.record_count=0)"""


def _verify(expected_hash):
    op.execute(f"""DO $verify_0122$ BEGIN
        IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator'
           OR NOT EXISTS (SELECT 1 FROM pg_proc p WHERE p.oid='{SIGNATURE}'::regprocedure
              AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname='star_oam_migrator')
              AND p.prosecdef AND p.prokind='f' AND p.prorettype='void'::regtype
              AND p.provolatile='v' AND NOT p.proisstrict AND p.proparallel='u'
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.proconfig=ARRAY['search_path=pg_catalog, public']
              AND encode(sha256(convert_to(p.prosrc,'UTF8')),'hex')='{expected_hash}'
              AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee=(SELECT oid FROM pg_roles WHERE rolname='star_oam_api')
                    AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
              AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                  WHERE a.grantee<>p.proowner AND (a.grantee<>(SELECT oid FROM pg_roles WHERE rolname='star_oam_api')
                    OR a.privilege_type<>'EXECUTE' OR a.is_grantable))) THEN
            RAISE EXCEPTION '0122 control import function source, ownership or ACL drift';
        END IF;
    END $verify_0122$""")


def _transition(up):
    dialect = op.get_bind().dialect.name
    if dialect not in {'sqlite', 'postgresql'}:
        raise RuntimeError('0122 supports PostgreSQL and SQLite only')
    helper = runpy.run_path(str(_folder/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect == 'postgresql':
        op.execute('LOCK TABLE '+','.join('public.'+table for table in LOCK_TABLES)+' IN ACCESS EXCLUSIVE MODE')
    if not up:
        helper['_preflight'](RETENTION, '0122 downgrade blocked: zero-control opening history must be retained')
    if dialect != 'postgresql':
        return
    _verify(LEGACY_HASH if up else FIXED_HASH)
    replace = runpy.run_path(str(_folder/'20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
    replace(signature=SIGNATURE, expected_hash=LEGACY_HASH if up else FIXED_HASH,
            replacement_hash=FIXED_HASH if up else LEGACY_HASH,
            replacements=((LEGACY_FRAGMENT, FIXED_FRAGMENT),) if up else ((FIXED_FRAGMENT, LEGACY_FRAGMENT),),
            label='zero_control_import_0122')
    _verify(FIXED_HASH if up else LEGACY_HASH)
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_HASH if up else NEW_HASH, replacement_hash=NEW_HASH if up else OLD_HASH,
            replacements=((down_revision, revision),) if up else ((revision, down_revision),),
            label='zero_control_opening_readiness_0122')


def upgrade(): _transition(True)
def downgrade(): _transition(False)
