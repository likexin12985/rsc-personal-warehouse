"""Add the V1.0 stage-one organization, sync, and platform foundation.

Revision ID: 20260830_0002
Revises: 20260830_0001
Create Date: 2026-08-30

This revision is intentionally additive and self-contained.  It does not
import ORM metadata, rebuild the legacy v0.9 users/auth_sessions/materials
tables, connect to an external system, or execute any business migration.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql


revision: str = '20260830_0002'
down_revision: Union[str, Sequence[str], None] = '20260830_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('login_challenges',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('mobile_hash', sa.String(length=64), nullable=False),
    sa.Column('code_hash', sa.String(length=128), nullable=False),
    sa.Column('purpose', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('requested_ip_hash', sa.String(length=64), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("purpose IN ('login', 'bind_identity', 'step_up')", name='ck_login_challenges_purpose'),
    sa.CheckConstraint("status IN ('pending', 'verified', 'consumed', 'expired', 'locked', 'cancelled')", name='ck_login_challenges_status'),
    sa.CheckConstraint('attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts', name='ck_login_challenges_attempts'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_login_challenges_idempotency')
    )
    op.create_index(op.f('ix_login_challenges_expires_at'), 'login_challenges', ['expires_at'], unique=False)
    op.create_index('ix_login_challenges_mobile_created', 'login_challenges', ['mobile_hash', 'created_at'], unique=False)
    op.create_index(op.f('ix_login_challenges_mobile_hash'), 'login_challenges', ['mobile_hash'], unique=False)
    op.create_index(op.f('ix_login_challenges_status'), 'login_challenges', ['status'], unique=False)
    op.create_table('migration_batches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_no', sa.String(length=100), nullable=False),
    sa.Column('cutoff_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cutoff_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('source_count', sa.BigInteger(), nullable=False),
    sa.Column('target_count', sa.BigInteger(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('planned', 'validating', 'validated', 'importing', 'completed', 'failed', 'rolled_back')", name='ck_migration_batches_status'),
    sa.CheckConstraint('attempt_no > 0', name='ck_migration_batches_attempt_positive'),
    sa.CheckConstraint('cutoff_to IS NULL OR cutoff_from IS NULL OR cutoff_to >= cutoff_from', name='ck_migration_batches_cutoff_order'),
    sa.CheckConstraint('source_count >= 0 AND target_count >= 0', name='ck_migration_batches_counts_nonnegative'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_no', name='uq_migration_batches_batch_no')
    )
    op.create_index(op.f('ix_migration_batches_batch_no'), 'migration_batches', ['batch_no'], unique=False)
    op.create_index(op.f('ix_migration_batches_entity_type'), 'migration_batches', ['entity_type'], unique=False)
    op.create_index(op.f('ix_migration_batches_status'), 'migration_batches', ['status'], unique=False)
    op.create_table('notification_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('business_type', sa.String(length=80), nullable=False),
    sa.Column('business_id', sa.String(length=64), nullable=False),
    sa.Column('dedup_key', sa.String(length=200), nullable=False),
    sa.Column('payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'expanded', 'cancelled')", name='ck_notification_events_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key', name='uq_notification_events_dedup_key')
    )
    op.create_index('ix_notification_events_business', 'notification_events', ['business_type', 'business_id'], unique=False)
    op.create_index(op.f('ix_notification_events_event_type'), 'notification_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_notification_events_status'), 'notification_events', ['status'], unique=False)
    op.create_table('outbox_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('aggregate_type', sa.String(length=80), nullable=False),
    sa.Column('aggregate_id', sa.String(length=64), nullable=False),
    sa.Column('payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_by', sa.String(length=160), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'processing', 'published', 'failed', 'dead_letter')", name='ck_outbox_events_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_outbox_events_attempts'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_outbox_events_idempotency')
    )
    op.create_index('ix_outbox_events_aggregate', 'outbox_events', ['aggregate_type', 'aggregate_id'], unique=False)
    op.create_index(op.f('ix_outbox_events_available_at'), 'outbox_events', ['available_at'], unique=False)
    op.create_index('ix_outbox_events_dispatch', 'outbox_events', ['status', 'available_at', 'created_at'], unique=False)
    op.create_index(op.f('ix_outbox_events_event_type'), 'outbox_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_outbox_events_status'), 'outbox_events', ['status'], unique=False)
    op.create_table('permissions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('resource', sa.String(length=100), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('field_code', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('length(action) > 0', name='ck_permissions_action_nonempty'),
    sa.CheckConstraint('length(resource) > 0', name='ck_permissions_resource_nonempty'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resource', 'action', 'field_code', name='uq_permissions_definition')
    )
    op.create_index(op.f('ix_permissions_action'), 'permissions', ['action'], unique=False)
    op.create_index(op.f('ix_permissions_resource'), 'permissions', ['resource'], unique=False)
    op.create_table('robot_inbound_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('provider_event_id', sa.String(length=250), nullable=False),
    sa.Column('sender_id', sa.String(length=250), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('payload_hash', sa.String(length=64), nullable=False),
    sa.Column('payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('response_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('received', 'verified', 'processed', 'rejected', 'failed')", name='ck_robot_inbound_events_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_event_id', name='uq_robot_inbound_events_provider_id')
    )
    op.create_index(op.f('ix_robot_inbound_events_provider_event_id'), 'robot_inbound_events', ['provider_event_id'], unique=False)
    op.create_index(op.f('ix_robot_inbound_events_received_at'), 'robot_inbound_events', ['received_at'], unique=False)
    op.create_index(op.f('ix_robot_inbound_events_sender_id'), 'robot_inbound_events', ['sender_id'], unique=False)
    op.create_index(op.f('ix_robot_inbound_events_status'), 'robot_inbound_events', ['status'], unique=False)
    op.create_table('roles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_external', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("code IN ('admin', 'provincial_manager', 'technician', 'star_headquarters_approver')", name='ck_roles_fixed_code'),
    sa.CheckConstraint("status IN ('active', 'inactive')", name='ck_roles_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_roles_code'), 'roles', ['code'], unique=True)
    role_table = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String(length=80)),
        sa.column("name", sa.String(length=120)),
        sa.column("is_external", sa.Boolean()),
        sa.column("status", sa.String(length=20)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    op.bulk_insert(
        role_table,
        [
            {
                "id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
                "code": "admin",
                "name": "蔚来总部管理员",
                "is_external": False,
                "status": "active",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
            {
                "id": uuid.UUID("10000000-0000-4000-8000-000000000002"),
                "code": "provincial_manager",
                "name": "区域公司负责人",
                "is_external": False,
                "status": "active",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
            {
                "id": uuid.UUID("10000000-0000-4000-8000-000000000003"),
                "code": "technician",
                "name": "工程师",
                "is_external": False,
                "status": "active",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
            {
                "id": uuid.UUID("10000000-0000-4000-8000-000000000004"),
                "code": "star_headquarters_approver",
                "name": "星星总部审批人",
                "is_external": True,
                "status": "active",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
        ],
    )
    op.create_table('source_systems',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('mode', sa.String(length=24), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('configuration_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("mode IN ('read_only', 'mirror_only')", name='ck_source_systems_mode'),
    sa.CheckConstraint('length(code) > 0', name='ck_source_systems_code_nonempty'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_source_systems_code'), 'source_systems', ['code'], unique=True)
    op.create_table('audit_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('actor_user_id', sa.String(length=36), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('aggregate_type', sa.String(length=80), nullable=False),
    sa.Column('aggregate_id', sa.String(length=64), nullable=False),
    sa.Column('before_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('after_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('request_id', sa.String(length=160), nullable=False),
    sa.Column('previous_hash', sa.String(length=64), nullable=True),
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_hash', name='uq_audit_events_event_hash')
    )
    op.create_index(op.f('ix_audit_events_action'), 'audit_events', ['action'], unique=False)
    op.create_index(op.f('ix_audit_events_actor_user_id'), 'audit_events', ['actor_user_id'], unique=False)
    op.create_index('ix_audit_events_aggregate', 'audit_events', ['aggregate_type', 'aggregate_id'], unique=False)
    op.create_index('ix_audit_events_occurred_at', 'audit_events', ['occurred_at'], unique=False)
    op.create_index('ix_audit_events_request_id', 'audit_events', ['request_id'], unique=False)
    op.create_table('auth_identities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('identity_type', sa.String(length=24), nullable=False),
    sa.Column('provider_key', sa.String(length=100), nullable=False),
    sa.Column('identifier_hash', sa.String(length=64), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("identity_type IN ('mobile', 'wechat_openid', 'wechat_unionid')", name='ck_auth_identities_type'),
    sa.CheckConstraint("status IN ('pending', 'active', 'revoked')", name='ck_auth_identities_status'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_type', 'provider_key', 'identifier_hash', name='uq_auth_identities_identifier'),
    sa.UniqueConstraint('user_id', 'identity_type', 'provider_key', name='uq_auth_identities_user_type_provider')
    )
    op.create_index(op.f('ix_auth_identities_identifier_hash'), 'auth_identities', ['identifier_hash'], unique=False)
    op.create_index(op.f('ix_auth_identities_identity_type'), 'auth_identities', ['identity_type'], unique=False)
    op.create_index(op.f('ix_auth_identities_status'), 'auth_identities', ['status'], unique=False)
    op.create_index(op.f('ix_auth_identities_user_id'), 'auth_identities', ['user_id'], unique=False)
    op.create_table('external_objects',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_system_id', sa.Uuid(), nullable=False),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('external_id', sa.String(length=250), nullable=False),
    sa.Column('current_version_id', sa.Uuid(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_systems.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_system_id', 'entity_type', 'external_id', name='uq_external_objects_source_identity')
    )
    op.create_index(op.f('ix_external_objects_current_version_id'), 'external_objects', ['current_version_id'], unique=False)
    op.create_index('ix_external_objects_entity_type', 'external_objects', ['entity_type'], unique=False)
    op.create_index(op.f('ix_external_objects_source_system_id'), 'external_objects', ['source_system_id'], unique=False)
    op.create_table('files',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('storage_key', sa.String(length=500), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('mime_type', sa.String(length=160), nullable=False),
    sa.Column('original_filename', sa.String(length=300), nullable=True),
    sa.Column('uploaded_by', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('metadata_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'available', 'quarantined', 'deleted')", name='ck_files_status'),
    sa.CheckConstraint('size_bytes >= 0', name='ck_files_size_nonnegative'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key')
    )
    op.create_index('ix_files_sha256', 'files', ['sha256'], unique=False)
    op.create_index(op.f('ix_files_status'), 'files', ['status'], unique=False)
    op.create_index(op.f('ix_files_uploaded_by'), 'files', ['uploaded_by'], unique=False)
    op.create_table('migration_errors',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('source_key', sa.String(length=300), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('source_payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('resolution_status', sa.String(length=20), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=False),
    sa.Column('resolved_by', sa.String(length=36), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("resolution_status IN ('open', 'resolved', 'ignored')", name='ck_migration_errors_resolution_status'),
    sa.ForeignKeyConstraint(['batch_id'], ['migration_batches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'source_key', 'error_code', name='uq_migration_errors_source_error')
    )
    op.create_index(op.f('ix_migration_errors_batch_id'), 'migration_errors', ['batch_id'], unique=False)
    op.create_index('ix_migration_errors_batch_status', 'migration_errors', ['batch_id', 'resolution_status'], unique=False)
    op.create_index(op.f('ix_migration_errors_error_code'), 'migration_errors', ['error_code'], unique=False)
    op.create_index(op.f('ix_migration_errors_resolution_status'), 'migration_errors', ['resolution_status'], unique=False)
    op.create_table('notification_recipients',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('recipient_key', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("channel IN ('wechat', 'sms', 'feishu')", name='ck_notification_recipients_channel'),
    sa.CheckConstraint("status IN ('active', 'suppressed')", name='ck_notification_recipients_status'),
    sa.ForeignKeyConstraint(['event_id'], ['notification_events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('event_id', 'channel', 'recipient_key', name='uq_notification_recipients_event_channel_key')
    )
    op.create_index(op.f('ix_notification_recipients_channel'), 'notification_recipients', ['channel'], unique=False)
    op.create_index(op.f('ix_notification_recipients_event_id'), 'notification_recipients', ['event_id'], unique=False)
    op.create_index(op.f('ix_notification_recipients_user_id'), 'notification_recipients', ['user_id'], unique=False)
    op.create_table('reconciliation_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_key', sa.String(length=160), nullable=False),
    sa.Column('source_system_id', sa.Uuid(), nullable=False),
    sa.Column('scope', sa.String(length=200), nullable=False),
    sa.Column('external_snapshot_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('local_ledger_cursor', sa.String(length=160), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('summary_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'running', 'matched', 'differences', 'failed', 'approved')", name='ck_reconciliation_runs_status'),
    sa.CheckConstraint('completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at', name='ck_reconciliation_runs_time_order'),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_systems.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_key', name='uq_reconciliation_runs_run_key')
    )
    op.create_index('ix_reconciliation_runs_source_status', 'reconciliation_runs', ['source_system_id', 'status'], unique=False)
    op.create_index(op.f('ix_reconciliation_runs_source_system_id'), 'reconciliation_runs', ['source_system_id'], unique=False)
    op.create_index(op.f('ix_reconciliation_runs_status'), 'reconciliation_runs', ['status'], unique=False)
    op.create_table('role_assignments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('scope_type', sa.String(length=24), nullable=False),
    sa.Column('scope_id', sa.String(length=80), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('assigned_by', sa.String(length=36), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(scope_type = 'national' AND scope_id = '*') OR (scope_type <> 'national' AND length(scope_id) > 0)", name='ck_role_assignments_scope_binding'),
    sa.CheckConstraint("scope_type IN ('national', 'organization', 'warehouse', 'person', 'document')", name='ck_role_assignments_scope_type'),
    sa.CheckConstraint("status IN ('scheduled', 'active', 'revoked', 'expired')", name='ck_role_assignments_status'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name='ck_role_assignments_validity'),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'role_id', 'scope_type', 'scope_id', 'valid_from', name='uq_role_assignments_identity_scope_start')
    )
    op.create_index(op.f('ix_role_assignments_assigned_by'), 'role_assignments', ['assigned_by'], unique=False)
    op.create_index(op.f('ix_role_assignments_role_id'), 'role_assignments', ['role_id'], unique=False)
    op.create_index(op.f('ix_role_assignments_status'), 'role_assignments', ['status'], unique=False)
    op.create_index('ix_role_assignments_user_active', 'role_assignments', ['user_id', 'status', 'valid_to'], unique=False)
    op.create_index(op.f('ix_role_assignments_user_id'), 'role_assignments', ['user_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('role_id', sa.Uuid(), nullable=False),
    sa.Column('permission_id', sa.Uuid(), nullable=False),
    sa.Column('effect', sa.String(length=12), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("effect IN ('allow', 'deny')", name='ck_role_permissions_effect'),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('role_id', 'permission_id', name='uq_role_permissions_pair')
    )
    op.create_index(op.f('ix_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False)
    op.create_index(op.f('ix_role_permissions_role_id'), 'role_permissions', ['role_id'], unique=False)
    op.create_table('state_transition_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('aggregate_type', sa.String(length=80), nullable=False),
    sa.Column('aggregate_id', sa.String(length=64), nullable=False),
    sa.Column('from_status', sa.String(length=40), nullable=True),
    sa.Column('to_status', sa.String(length=40), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=True),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('metadata_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('from_status IS NULL OR from_status <> to_status', name='ck_state_transition_events_changed'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_state_transition_events_idempotency')
    )
    op.create_index('ix_state_transition_events_aggregate', 'state_transition_events', ['aggregate_type', 'aggregate_id', 'occurred_at'], unique=False)
    op.create_table('sync_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('source_system_id', sa.Uuid(), nullable=False),
    sa.Column('run_key', sa.String(length=160), nullable=False),
    sa.Column('scope_key', sa.String(length=200), nullable=False),
    sa.Column('mode', sa.String(length=24), nullable=False),
    sa.Column('watermark_from', sa.String(length=250), nullable=True),
    sa.Column('watermark_to', sa.String(length=250), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('manifest_sha256', sa.String(length=64), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_code', sa.String(length=80), nullable=True),
    sa.Column('failure_detail', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("mode IN ('full', 'incremental', 'replay')", name='ck_sync_runs_mode'),
    sa.CheckConstraint("status IN ('pending', 'receiving', 'validating', 'validated', 'projecting', 'completed', 'failed', 'conflict', 'cancelled')", name='ck_sync_runs_status'),
    sa.CheckConstraint('completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at', name='ck_sync_runs_time_order'),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_systems.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_key', name='uq_sync_runs_run_key')
    )
    op.create_index(op.f('ix_sync_runs_scope_key'), 'sync_runs', ['scope_key'], unique=False)
    op.create_index('ix_sync_runs_source_status', 'sync_runs', ['source_system_id', 'status'], unique=False)
    op.create_index(op.f('ix_sync_runs_source_system_id'), 'sync_runs', ['source_system_id'], unique=False)
    op.create_index(op.f('ix_sync_runs_status'), 'sync_runs', ['status'], unique=False)
    op.create_table('system_parameters',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('parameter_key', sa.String(length=160), nullable=False),
    sa.Column('value_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'active', 'superseded', 'revoked')", name='ck_system_parameters_status'),
    sa.CheckConstraint('effective_to IS NULL OR effective_to > effective_from', name='ck_system_parameters_validity'),
    sa.CheckConstraint('version > 0', name='ck_system_parameters_version_positive'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('parameter_key', 'version', name='uq_system_parameters_key_version')
    )
    op.create_index(op.f('ix_system_parameters_parameter_key'), 'system_parameters', ['parameter_key'], unique=False)
    op.create_index(op.f('ix_system_parameters_status'), 'system_parameters', ['status'], unique=False)
    op.create_index('uq_system_parameters_one_active', 'system_parameters', ['parameter_key'], unique=True, postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.create_table('approval_delegations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('from_user_id', sa.String(length=36), nullable=False),
    sa.Column('to_user_id', sa.String(length=36), nullable=False),
    sa.Column('scope_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('scope_hash', sa.String(length=64), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=False),
    sa.Column('evidence_file_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=False),
    sa.Column('revoked_by', sa.String(length=36), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('scheduled', 'active', 'revoked', 'expired')", name='ck_approval_delegations_status'),
    sa.CheckConstraint('from_user_id <> to_user_id', name='ck_approval_delegations_distinct_users'),
    sa.CheckConstraint('valid_to > valid_from', name='ck_approval_delegations_validity'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['evidence_file_id'], ['files.id'], ),
    sa.ForeignKeyConstraint(['from_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['to_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('from_user_id', 'to_user_id', 'scope_hash', 'valid_from', name='uq_approval_delegations_scope_start')
    )
    op.create_index('ix_approval_delegations_from_active', 'approval_delegations', ['from_user_id', 'status', 'valid_to'], unique=False)
    op.create_index(op.f('ix_approval_delegations_from_user_id'), 'approval_delegations', ['from_user_id'], unique=False)
    op.create_index(op.f('ix_approval_delegations_status'), 'approval_delegations', ['status'], unique=False)
    op.create_index(op.f('ix_approval_delegations_to_user_id'), 'approval_delegations', ['to_user_id'], unique=False)
    op.create_table('document_attachments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_type', sa.String(length=80), nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('file_id', sa.Uuid(), nullable=False),
    sa.Column('attachment_type', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('uploaded_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active', 'removed')", name='ck_document_attachments_status'),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_type', 'document_id', 'file_id', 'attachment_type', name='uq_document_attachments_binding')
    )
    op.create_index('ix_document_attachments_document', 'document_attachments', ['document_type', 'document_id'], unique=False)
    op.create_index(op.f('ix_document_attachments_file_id'), 'document_attachments', ['file_id'], unique=False)
    op.create_table('external_object_mappings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.Uuid(), nullable=False),
    sa.Column('local_object_type', sa.String(length=80), nullable=False),
    sa.Column('local_object_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'superseded')", name='ck_external_object_mappings_status'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['external_object_id'], ['external_objects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_object_id', 'local_object_type', 'local_object_id', name='uq_external_object_mappings_target')
    )
    op.create_index(op.f('ix_external_object_mappings_external_object_id'), 'external_object_mappings', ['external_object_id'], unique=False)
    op.create_index(op.f('ix_external_object_mappings_status'), 'external_object_mappings', ['status'], unique=False)
    op.create_table('external_object_versions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.Uuid(), nullable=False),
    sa.Column('source_version', sa.String(length=160), nullable=False),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name='ck_external_object_versions_validity'),
    sa.ForeignKeyConstraint(['external_object_id'], ['external_objects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('external_object_id', 'source_version', 'payload_sha256', name='uq_external_object_versions_version_hash')
    )
    op.create_index(op.f('ix_external_object_versions_external_object_id'), 'external_object_versions', ['external_object_id'], unique=False)
    op.create_index(op.f('ix_external_object_versions_is_current'), 'external_object_versions', ['is_current'], unique=False)
    op.create_index('ix_external_object_versions_source_time', 'external_object_versions', ['external_object_id', 'source_updated_at'], unique=False)
    op.create_index('uq_external_object_versions_current', 'external_object_versions', ['external_object_id'], unique=True, postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.create_table('file_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('job_type', sa.String(length=20), nullable=False),
    sa.Column('requested_by', sa.String(length=36), nullable=False),
    sa.Column('parameters_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('parameters_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=28), nullable=False),
    sa.Column('result_file_id', sa.Uuid(), nullable=True),
    sa.Column('error_file_id', sa.Uuid(), nullable=True),
    sa.Column('confirmed_by', sa.String(length=36), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("job_type IN ('import', 'export', 'print')", name='ck_file_jobs_type'),
    sa.CheckConstraint("status IN ('queued', 'prevalidating', 'awaiting_confirmation', 'running', 'succeeded', 'failed', 'cancelled')", name='ck_file_jobs_status'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['error_file_id'], ['files.id'], ),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['result_file_id'], ['files.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_file_jobs_idempotency')
    )
    op.create_index(op.f('ix_file_jobs_job_type'), 'file_jobs', ['job_type'], unique=False)
    op.create_index(op.f('ix_file_jobs_requested_by'), 'file_jobs', ['requested_by'], unique=False)
    op.create_index('ix_file_jobs_requester_status', 'file_jobs', ['requested_by', 'status'], unique=False)
    op.create_index(op.f('ix_file_jobs_status'), 'file_jobs', ['status'], unique=False)
    op.create_table('notification_deliveries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('recipient_id', sa.Uuid(), nullable=False),
    sa.Column('delivery_key', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('provider_message_id', sa.String(length=250), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('queued', 'sending', 'sent', 'delivered', 'read', 'failed', 'cancelled')", name='ck_notification_deliveries_status'),
    sa.CheckConstraint('attempts >= 0', name='ck_notification_deliveries_attempts'),
    sa.ForeignKeyConstraint(['recipient_id'], ['notification_recipients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('delivery_key', name='uq_notification_deliveries_key'),
    sa.UniqueConstraint('recipient_id', name='uq_notification_deliveries_recipient')
    )
    op.create_index(op.f('ix_notification_deliveries_provider_message_id'), 'notification_deliveries', ['provider_message_id'], unique=False)
    op.create_index(op.f('ix_notification_deliveries_status'), 'notification_deliveries', ['status'], unique=False)
    op.create_index('ix_notification_deliveries_status_created', 'notification_deliveries', ['status', 'created_at'], unique=False)
    op.create_table('organizations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.Uuid(), nullable=True),
    sa.Column('code', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('parent_id', sa.Uuid(), nullable=True),
    sa.Column('org_type', sa.String(length=32), nullable=False),
    sa.Column('province_code', sa.String(length=12), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("org_type IN ('headquarters', 'region_company', 'department', 'external_approval_org')", name='ck_organizations_org_type'),
    sa.CheckConstraint("status IN ('active', 'inactive')", name='ck_organizations_status'),
    sa.CheckConstraint('parent_id IS NULL OR parent_id <> id', name='ck_organizations_parent'),
    sa.ForeignKeyConstraint(['external_object_id'], ['external_objects.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_organizations_code'), 'organizations', ['code'], unique=True)
    op.create_index(op.f('ix_organizations_external_object_id'), 'organizations', ['external_object_id'], unique=True)
    op.create_index(op.f('ix_organizations_name'), 'organizations', ['name'], unique=False)
    op.create_index(op.f('ix_organizations_org_type'), 'organizations', ['org_type'], unique=False)
    op.create_index(op.f('ix_organizations_parent_id'), 'organizations', ['parent_id'], unique=False)
    op.create_index(op.f('ix_organizations_province_code'), 'organizations', ['province_code'], unique=False)
    op.create_index(op.f('ix_organizations_status'), 'organizations', ['status'], unique=False)
    op.create_table('reconciliation_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('business_key', sa.String(length=300), nullable=False),
    sa.Column('external_qty', sa.Numeric(precision=18, scale=3), nullable=False),
    sa.Column('local_qty', sa.Numeric(precision=18, scale=3), nullable=False),
    sa.Column('difference', sa.Numeric(precision=18, scale=3), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('evidence_file_id', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('matched', 'difference', 'explained', 'resolved')", name='ck_reconciliation_items_status'),
    sa.CheckConstraint('difference = external_qty - local_qty', name='ck_reconciliation_items_difference'),
    sa.CheckConstraint('external_qty >= 0 AND local_qty >= 0', name='ck_reconciliation_items_quantities_nonnegative'),
    sa.ForeignKeyConstraint(['evidence_file_id'], ['files.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['reconciliation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'business_key', name='uq_reconciliation_items_business_key')
    )
    op.create_index(op.f('ix_reconciliation_items_run_id'), 'reconciliation_items', ['run_id'], unique=False)
    op.create_index('ix_reconciliation_items_run_status', 'reconciliation_items', ['run_id', 'status'], unique=False)
    op.create_index(op.f('ix_reconciliation_items_status'), 'reconciliation_items', ['status'], unique=False)
    op.create_table('sync_batches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('record_count', sa.Integer(), nullable=False),
    sa.Column('body_sha256', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('receiving', 'received', 'validated', 'rejected', 'applied')", name='ck_sync_batches_status'),
    sa.CheckConstraint('record_count >= 0', name='ck_sync_batches_record_count_nonnegative'),
    sa.CheckConstraint('sequence > 0', name='ck_sync_batches_sequence_positive'),
    sa.ForeignKeyConstraint(['run_id'], ['sync_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'entity_type', 'sequence', name='uq_sync_batches_sequence')
    )
    op.create_index(op.f('ix_sync_batches_entity_type'), 'sync_batches', ['entity_type'], unique=False)
    op.create_index(op.f('ix_sync_batches_run_id'), 'sync_batches', ['run_id'], unique=False)
    op.create_index('ix_sync_batches_run_status', 'sync_batches', ['run_id', 'status'], unique=False)
    op.create_index(op.f('ix_sync_batches_status'), 'sync_batches', ['status'], unique=False)
    op.create_table('notification_attempts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('delivery_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('response_code', sa.String(length=80), nullable=True),
    sa.Column('response_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('attempt_no > 0', name='ck_notification_attempts_positive'),
    sa.ForeignKeyConstraint(['delivery_id'], ['notification_deliveries.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('delivery_id', 'attempt_no', name='uq_notification_attempts_number')
    )
    op.create_index(op.f('ix_notification_attempts_attempted_at'), 'notification_attempts', ['attempted_at'], unique=False)
    op.create_index(op.f('ix_notification_attempts_delivery_id'), 'notification_attempts', ['delivery_id'], unique=False)
    op.create_table('people',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('external_object_id', sa.Uuid(), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('employee_no', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('mobile_encrypted', sa.Text(), nullable=True),
    sa.Column('mobile_hash', sa.String(length=64), nullable=True),
    sa.Column('employment_status', sa.String(length=24), nullable=False),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("employment_status IN ('active', 'inactive', 'left', 'restricted')", name='ck_people_employment_status'),
    sa.ForeignKeyConstraint(['external_object_id'], ['external_objects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'employee_no', name='uq_people_org_employee_no')
    )
    op.create_index(op.f('ix_people_employee_no'), 'people', ['employee_no'], unique=False)
    op.create_index(op.f('ix_people_employment_status'), 'people', ['employment_status'], unique=False)
    op.create_index(op.f('ix_people_external_object_id'), 'people', ['external_object_id'], unique=True)
    op.create_index(op.f('ix_people_mobile_hash'), 'people', ['mobile_hash'], unique=False)
    op.create_index(op.f('ix_people_name'), 'people', ['name'], unique=False)
    op.create_index('ix_people_org_status', 'people', ['organization_id', 'employment_status'], unique=False)
    op.create_index(op.f('ix_people_organization_id'), 'people', ['organization_id'], unique=False)
    op.create_table('sync_inbox_events',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('batch_id', sa.Uuid(), nullable=False),
    sa.Column('source_system_id', sa.Uuid(), nullable=False),
    sa.Column('external_event_id', sa.String(length=200), nullable=False),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('external_id', sa.String(length=250), nullable=False),
    sa.Column('source_version', sa.String(length=160), nullable=True),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payload_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('payload_sha256', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('staged', 'validated', 'applied', 'conflict', 'rejected')", name='ck_sync_inbox_events_status'),
    sa.ForeignKeyConstraint(['batch_id'], ['sync_batches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_system_id'], ['source_systems.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_system_id', 'external_event_id', 'payload_sha256', name='uq_sync_inbox_events_id_hash')
    )
    op.create_index(op.f('ix_sync_inbox_events_batch_id'), 'sync_inbox_events', ['batch_id'], unique=False)
    op.create_index(op.f('ix_sync_inbox_events_external_event_id'), 'sync_inbox_events', ['external_event_id'], unique=False)
    op.create_index('ix_sync_inbox_events_external_object', 'sync_inbox_events', ['source_system_id', 'entity_type', 'external_id'], unique=False)
    op.create_index(op.f('ix_sync_inbox_events_source_system_id'), 'sync_inbox_events', ['source_system_id'], unique=False)
    op.create_index(op.f('ix_sync_inbox_events_status'), 'sync_inbox_events', ['status'], unique=False)
    op.create_table('sync_conflicts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=True),
    sa.Column('inbox_event_id', sa.Uuid(), nullable=True),
    sa.Column('external_object_id', sa.Uuid(), nullable=True),
    sa.Column('dedup_key', sa.String(length=160), nullable=False),
    sa.Column('conflict_type', sa.String(length=80), nullable=False),
    sa.Column('external_value_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('local_value_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolution_jsonb', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('resolved_by', sa.String(length=36), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('open', 'resolved', 'ignored')", name='ck_sync_conflicts_status'),
    sa.CheckConstraint('inbox_event_id IS NOT NULL OR external_object_id IS NOT NULL', name='ck_sync_conflicts_object_reference'),
    sa.ForeignKeyConstraint(['external_object_id'], ['external_objects.id'], ),
    sa.ForeignKeyConstraint(['inbox_event_id'], ['sync_inbox_events.id'], ),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['sync_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key', name='uq_sync_conflicts_dedup_key')
    )
    op.create_index(op.f('ix_sync_conflicts_conflict_type'), 'sync_conflicts', ['conflict_type'], unique=False)
    op.create_index(op.f('ix_sync_conflicts_external_object_id'), 'sync_conflicts', ['external_object_id'], unique=False)
    op.create_index(op.f('ix_sync_conflicts_inbox_event_id'), 'sync_conflicts', ['inbox_event_id'], unique=False)
    op.create_index(op.f('ix_sync_conflicts_run_id'), 'sync_conflicts', ['run_id'], unique=False)
    op.create_index(op.f('ix_sync_conflicts_status'), 'sync_conflicts', ['status'], unique=False)
    op.create_index('ix_sync_conflicts_status_type', 'sync_conflicts', ['status', 'conflict_type'], unique=False)
def downgrade() -> None:
    op.drop_index('ix_sync_conflicts_status_type', table_name='sync_conflicts')
    op.drop_index(op.f('ix_sync_conflicts_status'), table_name='sync_conflicts')
    op.drop_index(op.f('ix_sync_conflicts_run_id'), table_name='sync_conflicts')
    op.drop_index(op.f('ix_sync_conflicts_inbox_event_id'), table_name='sync_conflicts')
    op.drop_index(op.f('ix_sync_conflicts_external_object_id'), table_name='sync_conflicts')
    op.drop_index(op.f('ix_sync_conflicts_conflict_type'), table_name='sync_conflicts')
    op.drop_table('sync_conflicts')
    op.drop_index(op.f('ix_sync_inbox_events_status'), table_name='sync_inbox_events')
    op.drop_index(op.f('ix_sync_inbox_events_source_system_id'), table_name='sync_inbox_events')
    op.drop_index('ix_sync_inbox_events_external_object', table_name='sync_inbox_events')
    op.drop_index(op.f('ix_sync_inbox_events_external_event_id'), table_name='sync_inbox_events')
    op.drop_index(op.f('ix_sync_inbox_events_batch_id'), table_name='sync_inbox_events')
    op.drop_table('sync_inbox_events')
    op.drop_index(op.f('ix_people_organization_id'), table_name='people')
    op.drop_index('ix_people_org_status', table_name='people')
    op.drop_index(op.f('ix_people_name'), table_name='people')
    op.drop_index(op.f('ix_people_mobile_hash'), table_name='people')
    op.drop_index(op.f('ix_people_external_object_id'), table_name='people')
    op.drop_index(op.f('ix_people_employment_status'), table_name='people')
    op.drop_index(op.f('ix_people_employee_no'), table_name='people')
    op.drop_table('people')
    op.drop_index(op.f('ix_notification_attempts_delivery_id'), table_name='notification_attempts')
    op.drop_index(op.f('ix_notification_attempts_attempted_at'), table_name='notification_attempts')
    op.drop_table('notification_attempts')
    op.drop_index(op.f('ix_sync_batches_status'), table_name='sync_batches')
    op.drop_index('ix_sync_batches_run_status', table_name='sync_batches')
    op.drop_index(op.f('ix_sync_batches_run_id'), table_name='sync_batches')
    op.drop_index(op.f('ix_sync_batches_entity_type'), table_name='sync_batches')
    op.drop_table('sync_batches')
    op.drop_index(op.f('ix_reconciliation_items_status'), table_name='reconciliation_items')
    op.drop_index('ix_reconciliation_items_run_status', table_name='reconciliation_items')
    op.drop_index(op.f('ix_reconciliation_items_run_id'), table_name='reconciliation_items')
    op.drop_table('reconciliation_items')
    op.drop_index(op.f('ix_organizations_status'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_province_code'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_parent_id'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_org_type'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_name'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_external_object_id'), table_name='organizations')
    op.drop_index(op.f('ix_organizations_code'), table_name='organizations')
    op.drop_table('organizations')
    op.drop_index('ix_notification_deliveries_status_created', table_name='notification_deliveries')
    op.drop_index(op.f('ix_notification_deliveries_status'), table_name='notification_deliveries')
    op.drop_index(op.f('ix_notification_deliveries_provider_message_id'), table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    op.drop_index(op.f('ix_file_jobs_status'), table_name='file_jobs')
    op.drop_index('ix_file_jobs_requester_status', table_name='file_jobs')
    op.drop_index(op.f('ix_file_jobs_requested_by'), table_name='file_jobs')
    op.drop_index(op.f('ix_file_jobs_job_type'), table_name='file_jobs')
    op.drop_table('file_jobs')
    op.drop_index('uq_external_object_versions_current', table_name='external_object_versions', postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.drop_index('ix_external_object_versions_source_time', table_name='external_object_versions')
    op.drop_index(op.f('ix_external_object_versions_is_current'), table_name='external_object_versions')
    op.drop_index(op.f('ix_external_object_versions_external_object_id'), table_name='external_object_versions')
    op.drop_table('external_object_versions')
    op.drop_index(op.f('ix_external_object_mappings_status'), table_name='external_object_mappings')
    op.drop_index(op.f('ix_external_object_mappings_external_object_id'), table_name='external_object_mappings')
    op.drop_table('external_object_mappings')
    op.drop_index(op.f('ix_document_attachments_file_id'), table_name='document_attachments')
    op.drop_index('ix_document_attachments_document', table_name='document_attachments')
    op.drop_table('document_attachments')
    op.drop_index(op.f('ix_approval_delegations_to_user_id'), table_name='approval_delegations')
    op.drop_index(op.f('ix_approval_delegations_status'), table_name='approval_delegations')
    op.drop_index(op.f('ix_approval_delegations_from_user_id'), table_name='approval_delegations')
    op.drop_index('ix_approval_delegations_from_active', table_name='approval_delegations')
    op.drop_table('approval_delegations')
    op.drop_index('uq_system_parameters_one_active', table_name='system_parameters', postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_system_parameters_status'), table_name='system_parameters')
    op.drop_index(op.f('ix_system_parameters_parameter_key'), table_name='system_parameters')
    op.drop_table('system_parameters')
    op.drop_index(op.f('ix_sync_runs_status'), table_name='sync_runs')
    op.drop_index(op.f('ix_sync_runs_source_system_id'), table_name='sync_runs')
    op.drop_index('ix_sync_runs_source_status', table_name='sync_runs')
    op.drop_index(op.f('ix_sync_runs_scope_key'), table_name='sync_runs')
    op.drop_table('sync_runs')
    op.drop_index('ix_state_transition_events_aggregate', table_name='state_transition_events')
    op.drop_table('state_transition_events')
    op.drop_index(op.f('ix_role_permissions_role_id'), table_name='role_permissions')
    op.drop_index(op.f('ix_role_permissions_permission_id'), table_name='role_permissions')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_role_assignments_user_id'), table_name='role_assignments')
    op.drop_index('ix_role_assignments_user_active', table_name='role_assignments')
    op.drop_index(op.f('ix_role_assignments_status'), table_name='role_assignments')
    op.drop_index(op.f('ix_role_assignments_role_id'), table_name='role_assignments')
    op.drop_index(op.f('ix_role_assignments_assigned_by'), table_name='role_assignments')
    op.drop_table('role_assignments')
    op.drop_index(op.f('ix_reconciliation_runs_status'), table_name='reconciliation_runs')
    op.drop_index(op.f('ix_reconciliation_runs_source_system_id'), table_name='reconciliation_runs')
    op.drop_index('ix_reconciliation_runs_source_status', table_name='reconciliation_runs')
    op.drop_table('reconciliation_runs')
    op.drop_index(op.f('ix_notification_recipients_user_id'), table_name='notification_recipients')
    op.drop_index(op.f('ix_notification_recipients_event_id'), table_name='notification_recipients')
    op.drop_index(op.f('ix_notification_recipients_channel'), table_name='notification_recipients')
    op.drop_table('notification_recipients')
    op.drop_index(op.f('ix_migration_errors_resolution_status'), table_name='migration_errors')
    op.drop_index(op.f('ix_migration_errors_error_code'), table_name='migration_errors')
    op.drop_index('ix_migration_errors_batch_status', table_name='migration_errors')
    op.drop_index(op.f('ix_migration_errors_batch_id'), table_name='migration_errors')
    op.drop_table('migration_errors')
    op.drop_index(op.f('ix_files_uploaded_by'), table_name='files')
    op.drop_index(op.f('ix_files_status'), table_name='files')
    op.drop_index('ix_files_sha256', table_name='files')
    op.drop_table('files')
    op.drop_index(op.f('ix_external_objects_source_system_id'), table_name='external_objects')
    op.drop_index('ix_external_objects_entity_type', table_name='external_objects')
    op.drop_index(op.f('ix_external_objects_current_version_id'), table_name='external_objects')
    op.drop_table('external_objects')
    op.drop_index(op.f('ix_auth_identities_user_id'), table_name='auth_identities')
    op.drop_index(op.f('ix_auth_identities_status'), table_name='auth_identities')
    op.drop_index(op.f('ix_auth_identities_identity_type'), table_name='auth_identities')
    op.drop_index(op.f('ix_auth_identities_identifier_hash'), table_name='auth_identities')
    op.drop_table('auth_identities')
    op.drop_index('ix_audit_events_request_id', table_name='audit_events')
    op.drop_index('ix_audit_events_occurred_at', table_name='audit_events')
    op.drop_index('ix_audit_events_aggregate', table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_actor_user_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_action'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_source_systems_code'), table_name='source_systems')
    op.drop_table('source_systems')
    op.drop_index(op.f('ix_roles_code'), table_name='roles')
    op.drop_table('roles')
    op.drop_index(op.f('ix_robot_inbound_events_status'), table_name='robot_inbound_events')
    op.drop_index(op.f('ix_robot_inbound_events_sender_id'), table_name='robot_inbound_events')
    op.drop_index(op.f('ix_robot_inbound_events_received_at'), table_name='robot_inbound_events')
    op.drop_index(op.f('ix_robot_inbound_events_provider_event_id'), table_name='robot_inbound_events')
    op.drop_table('robot_inbound_events')
    op.drop_index(op.f('ix_permissions_resource'), table_name='permissions')
    op.drop_index(op.f('ix_permissions_action'), table_name='permissions')
    op.drop_table('permissions')
    op.drop_index(op.f('ix_outbox_events_status'), table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_event_type'), table_name='outbox_events')
    op.drop_index('ix_outbox_events_dispatch', table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_available_at'), table_name='outbox_events')
    op.drop_index('ix_outbox_events_aggregate', table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_index(op.f('ix_notification_events_status'), table_name='notification_events')
    op.drop_index(op.f('ix_notification_events_event_type'), table_name='notification_events')
    op.drop_index('ix_notification_events_business', table_name='notification_events')
    op.drop_table('notification_events')
    op.drop_index(op.f('ix_migration_batches_status'), table_name='migration_batches')
    op.drop_index(op.f('ix_migration_batches_entity_type'), table_name='migration_batches')
    op.drop_index(op.f('ix_migration_batches_batch_no'), table_name='migration_batches')
    op.drop_table('migration_batches')
    op.drop_index(op.f('ix_login_challenges_status'), table_name='login_challenges')
    op.drop_index(op.f('ix_login_challenges_mobile_hash'), table_name='login_challenges')
    op.drop_index('ix_login_challenges_mobile_created', table_name='login_challenges')
    op.drop_index(op.f('ix_login_challenges_expires_at'), table_name='login_challenges')
    op.drop_table('login_challenges')
