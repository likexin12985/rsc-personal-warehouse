"""Scoped inventory report jobs and an explicit export permission."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '20261114_0135'
down_revision = '20261113_0134'
branch_labels = depends_on = None

FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261113_0134_daily_review_request_seals.py'))
ready = runpy.run_path(str(FOLDER / '20261108_0129_notification_expansion_status.py'))
OLD_HASH = previous['NEW_HASH']
NEW_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
PERMISSION_ID = uuid.UUID('b9fdceca-45f1-4132-a97b-b2b799c0b135')
ADMIN_GRANT_ID = uuid.UUID('89b84a40-93f4-43dd-a4f7-0681a41e0135')
REGION_GRANT_ID = uuid.UUID('6c23fe59-6274-419d-b9a0-59ba532a0135')
ADMIN_ROLE_ID = uuid.UUID('10000000-0000-4000-8000-000000000001')
REGION_ROLE_ID = uuid.UUID('10000000-0000-4000-8000-000000000002')


def _permission_tables():
    permissions = sa.table('permissions', sa.column('id', sa.Uuid()),
        sa.column('resource', sa.String()), sa.column('action', sa.String()),
        sa.column('field_code', sa.String()), sa.column('description', sa.String()),
        sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('updated_at', sa.DateTime(timezone=True)))
    grants = sa.table('role_permissions', sa.column('id', sa.Uuid()),
        sa.column('role_id', sa.Uuid()), sa.column('permission_id', sa.Uuid()),
        sa.column('effect', sa.String()), sa.column('created_at', sa.DateTime(timezone=True)))
    return permissions, grants


def _add_columns():
    with op.batch_alter_table('file_jobs') as batch:
        batch.add_column(sa.Column('export_authorization_version', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('export_scope_jsonb', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=True))
        batch.add_column(sa.Column('export_ledger_cursor', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('result_sha256', sa.String(64), nullable=True))
        batch.add_column(sa.Column('result_size_bytes', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('download_count', sa.BigInteger(), nullable=False,
                                   server_default=sa.text('0')))
        batch.create_check_constraint('ck_file_jobs_export_version_positive',
                                      'export_authorization_version IS NULL OR export_authorization_version > 0')
        batch.create_check_constraint('ck_file_jobs_export_cursor_nonnegative',
                                      'export_ledger_cursor IS NULL OR export_ledger_cursor >= 0')
        batch.create_check_constraint('ck_file_jobs_result_sha256',
                                      'result_sha256 IS NULL OR length(result_sha256) = 64')
        batch.create_check_constraint('ck_file_jobs_result_size_nonnegative',
                                      'result_size_bytes IS NULL OR result_size_bytes >= 0')
        batch.create_check_constraint('ck_file_jobs_download_count_nonnegative',
                                      'download_count >= 0')
    op.create_index('ix_file_jobs_export_queue', 'file_jobs',
                    ['job_type', 'status', 'created_at', 'id'])


def _drop_columns():
    op.drop_index('ix_file_jobs_export_queue', table_name='file_jobs')
    with op.batch_alter_table('file_jobs') as batch:
        for name in ('ck_file_jobs_download_count_nonnegative',
                     'ck_file_jobs_result_size_nonnegative',
                     'ck_file_jobs_result_sha256',
                     'ck_file_jobs_export_cursor_nonnegative',
                     'ck_file_jobs_export_version_positive'):
            batch.drop_constraint(name, type_='check')
        for name in ('download_count', 'result_size_bytes', 'result_sha256',
                     'export_ledger_cursor', 'export_scope_jsonb',
                     'export_authorization_version'):
            batch.drop_column(name)


def _seed(up):
    permissions, grants = _permission_tables()
    if up:
        now = datetime.now(timezone.utc)
        op.bulk_insert(permissions, [dict(id=PERMISSION_ID, resource='report',
            action='export', field_code='', description='Generate and download scoped inventory reports',
            created_at=now, updated_at=now)])
        op.bulk_insert(grants, [dict(id=identifier, role_id=role,
            permission_id=PERMISSION_ID, effect='allow', created_at=now)
            for identifier, role in ((ADMIN_GRANT_ID, ADMIN_ROLE_ID),
                                     (REGION_GRANT_ID, REGION_ROLE_ID))])
    else:
        op.execute(grants.delete().where(grants.c.permission_id ==
            op.inline_literal(PERMISSION_ID, type_=sa.Uuid())))
        op.execute(permissions.delete().where(permissions.c.id ==
            op.inline_literal(PERMISSION_ID, type_=sa.Uuid())))


def _transition(up):
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0135 unsupported database')
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0135 direct owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version,public.file_jobs,public.permissions,public.role_permissions IN ACCESS EXCLUSIVE MODE')
    if up:
        _add_columns()
        _seed(True)
    else:
        helper['_preflight']("EXISTS(SELECT 1 FROM file_jobs WHERE export_authorization_version IS NOT NULL OR export_scope_jsonb IS NOT NULL OR export_ledger_cursor IS NOT NULL OR result_sha256 IS NOT NULL OR result_size_bytes IS NOT NULL OR download_count<>0)",
                             '0135 populated report jobs or downloads must be retained')
        permission_id = str(PERMISSION_ID) if dialect == 'postgresql' else PERMISSION_ID.hex
        helper['_preflight'](f"(SELECT count(*) FROM permissions WHERE id='{permission_id}' AND resource='report' AND action='export' AND field_code='')<>1 OR (SELECT count(*) FROM role_permissions WHERE permission_id='{permission_id}')<>2",
                             '0135 report permission catalog drift')
        admin_grant = str(ADMIN_GRANT_ID) if dialect == 'postgresql' else ADMIN_GRANT_ID.hex
        region_grant = str(REGION_GRANT_ID) if dialect == 'postgresql' else REGION_GRANT_ID.hex
        admin_role = str(ADMIN_ROLE_ID) if dialect == 'postgresql' else ADMIN_ROLE_ID.hex
        region_role = str(REGION_ROLE_ID) if dialect == 'postgresql' else REGION_ROLE_ID.hex
        helper['_preflight'](
            "(SELECT count(*) FROM role_permissions WHERE "
            f"permission_id='{permission_id}' AND effect='allow' AND ("
            f"(id='{admin_grant}' AND role_id='{admin_role}') OR "
            f"(id='{region_grant}' AND role_id='{region_role}')))<>2",
            '0135 report permission catalog drift',
        )
        _seed(False)
        _drop_columns()
    if dialect == 'postgresql':
        replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_HASH if up else NEW_HASH,
            replacement_hash=NEW_HASH if up else OLD_HASH,
            replacements=((down_revision, revision),) if up else ((revision, down_revision),),
            label='inventory_report_jobs_readiness_0135')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
