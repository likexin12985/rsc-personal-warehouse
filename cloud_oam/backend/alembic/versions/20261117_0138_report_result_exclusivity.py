"""Prevent one private result file from completing more than one file job."""

import hashlib
from pathlib import Path
import runpy

from alembic import op


revision = '20261117_0138'
down_revision = '20261116_0137'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261116_0137_report_export_file_purpose.py'))
ready = runpy.run_path(str(FOLDER / '20261108_0129_notification_expansion_status.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
NEW_READY_HASH = hashlib.sha256(
    ready['_ready'].replace(ready['_ready_parent'], revision).encode()
).hexdigest()
INDEX = 'uq_file_jobs_result_file_id'


def _transition(up: bool):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0138 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.file_jobs IN ACCESS EXCLUSIVE MODE')
    elif dialect != 'sqlite':
        raise RuntimeError('0138 PostgreSQL or SQLite required')
    if up:
        helper['_preflight'](
            'EXISTS (SELECT 1 FROM file_jobs WHERE result_file_id IS NOT NULL '
            'GROUP BY result_file_id HAVING count(*)>1)',
            '0138 result file is referenced by multiple jobs',
        )
        op.create_index(INDEX, 'file_jobs', ['result_file_id'], unique=True)
    else:
        helper['_preflight'](
            "EXISTS (SELECT 1 FROM file_jobs WHERE job_type='export' "
            "AND status='succeeded' AND result_file_id IS NOT NULL)",
            '0138 completed report files must remain exclusive',
        )
        op.drop_index(INDEX, table_name='file_jobs')
    if dialect == 'postgresql':
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(
            signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
            replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
            replacements=((down_revision, revision),) if up else ((revision, down_revision),),
            label='report_result_exclusivity_0138',
        )


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
