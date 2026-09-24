"""Represent unknown material source time without inventing a synchronization time."""
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = '20261028_0118'
down_revision = '20261027_0117'
branch_labels = depends_on = None
OLD_HASH = 'cfa7f20da8cc4dfecf6d5aa2d146afc333c90b00366e29ed14811a2a042140ab'
NEW_HASH = '35088a5ce50b2eaa912a873d69ae86fec16eb24e230be756069de3e1994ddb1a'


def _transition(upgrade):
    connection = op.get_bind()
    dialect = connection.dialect.name
    if dialect not in {'postgresql', 'sqlite'}:
        raise RuntimeError('0118 supports PostgreSQL and SQLite only')
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect == 'postgresql':
        op.execute('LOCK TABLE public.alembic_version, public.materials IN ACCESS EXCLUSIVE MODE')
        helper['_preflight'](
            "NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.materials'::regclass "
            "AND attname='source_updated_at' AND NOT attisdropped AND attnotnull="
            + ('true)' if upgrade else 'false)'),
            '0118 material source-time column drift')
    else:
        # Rebuilding a referenced SQLite parent with FK enforcement can cascade
        # deletes. Only the explicit offline test-migration connection may rebuild.
        if connection.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0118 SQLite migration requires foreign_keys OFF before the migration transaction')
        column = next(row for row in sa.inspect(connection).get_columns('materials') if row['name'] == 'source_updated_at')
        if column['nullable'] == upgrade:
            raise RuntimeError('0118 material source-time column drift')
    if not upgrade:
        helper['_preflight']('EXISTS (SELECT 1 FROM materials WHERE source_updated_at IS NULL)',
            '0118 downgrade blocked: unknown material source times must be retained')
    if dialect == 'postgresql':
        op.alter_column('materials', 'source_updated_at', existing_type=sa.DateTime(timezone=True), nullable=upgrade)
        replace = runpy.run_path(str(folder / '20260912_0072_outbound_postings.py'))['_previous']()['_previous']()['_previous']()['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
            label='material_source_time_readiness_0118')
    else:
        triggers = tuple(connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='materials' ORDER BY name").scalars())
        legacy_alter = connection.exec_driver_sql('PRAGMA legacy_alter_table').scalar()
        try:
            # Leave referring triggers/views bound to the final parent name while
            # Alembic replaces the table. Restore the connection option even on error.
            connection.exec_driver_sql('PRAGMA legacy_alter_table=ON')
            with op.batch_alter_table('materials', recreate='always') as batch:
                batch.alter_column('source_updated_at', existing_type=sa.DateTime(timezone=True), nullable=upgrade)
        finally:
            connection.exec_driver_sql('PRAGMA legacy_alter_table=' + ('ON' if legacy_alter else 'OFF'))
        for statement in triggers:
            op.execute(statement)
        if connection.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
            raise RuntimeError('0118 SQLite foreign-key integrity check failed')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
