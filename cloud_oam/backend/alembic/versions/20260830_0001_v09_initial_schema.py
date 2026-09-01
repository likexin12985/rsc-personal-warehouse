"""Freeze the explicit cloud_oam v0.9.0 schema.

Revision ID: 20260830_0001
Revises:
Create Date: 2026-08-30

This revision is intentionally self-contained.  Do not replace these operations
with ORM metadata or ``create_all``: an applied revision must not change when a
later model is edited.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_sync_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("batch_id", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_instance", "batch_id", name="uq_external_sync_source_batch"
        ),
    )
    op.create_index(
        "ix_external_sync_batch_received",
        "external_sync_batches",
        ["received_at"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_batch_id",
        "external_sync_batches",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_entity_type",
        "external_sync_batches",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_received_at",
        "external_sync_batches",
        ["received_at"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_snapshot_at",
        "external_sync_batches",
        ["snapshot_at"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_source_instance",
        "external_sync_batches",
        ["source_instance"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_source_system",
        "external_sync_batches",
        ["source_system"],
        unique=False,
    )
    op.create_index(
        "ix_external_sync_batches_status",
        "external_sync_batches",
        ["status"],
        unique=False,
    )

    op.create_table(
        "external_sync_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("sync_mode", sa.String(length=20), nullable=False),
        sa.Column("company_id", sa.String(length=80), nullable=False),
        sa.Column("org_code", sa.String(length=80), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_instance",
            "snapshot_id",
            name="uq_external_sync_snapshot_source_id",
        ),
    )
    op.create_index(
        "ix_external_sync_snapshot_scope_status",
        "external_sync_snapshots",
        ["source_instance", "scope_key", "status"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_external_sync_snapshots_company_id", ["company_id"]),
        ("ix_external_sync_snapshots_completed_at", ["completed_at"]),
        ("ix_external_sync_snapshots_org_code", ["org_code"]),
        ("ix_external_sync_snapshots_received_at", ["received_at"]),
        ("ix_external_sync_snapshots_scope_key", ["scope_key"]),
        ("ix_external_sync_snapshots_snapshot_at", ["snapshot_at"]),
        ("ix_external_sync_snapshots_snapshot_id", ["snapshot_id"]),
        ("ix_external_sync_snapshots_source_instance", ["source_instance"]),
        ("ix_external_sync_snapshots_source_system", ["source_system"]),
        ("ix_external_sync_snapshots_status", ["status"]),
        ("ix_external_sync_snapshots_sync_mode", ["sync_mode"]),
    ):
        op.create_index(
            index_name, "external_sync_snapshots", columns, unique=False
        )

    op.create_table(
        "materials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("specification", sa.String(length=240), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_materials_code", "materials", ["code"], unique=True)
    op.create_index("ix_materials_name", "materials", ["name"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("province", sa.String(length=40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("require_password_change", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_mobile", "users", ["mobile"], unique=True)
    op.create_index("ix_users_province", "users", ["province"], unique=False)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_audit_logs_action", ["action"]),
        ("ix_audit_logs_created_at", ["created_at"]),
        ("ix_audit_logs_entity_id", ["entity_id"]),
        ("ix_audit_logs_entity_type", ["entity_type"]),
    ):
        op.create_index(index_name, "audit_logs", columns, unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_type", sa.String(length=24), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("device_name", sa.String(length=160), nullable=False),
        sa.Column("ip_address", sa.String(length=80), nullable=False),
        sa.Column("user_agent", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["revoked_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_session_user_active",
        "auth_sessions",
        ["user_id", "revoked_at", "expires_at"],
        unique=False,
    )
    for index_name, columns, unique in (
        ("ix_auth_sessions_client_type", ["client_type"], False),
        ("ix_auth_sessions_created_at", ["created_at"], False),
        ("ix_auth_sessions_device_id", ["device_id"], False),
        ("ix_auth_sessions_expires_at", ["expires_at"], False),
        ("ix_auth_sessions_last_seen_at", ["last_seen_at"], False),
        ("ix_auth_sessions_refresh_token_hash", ["refresh_token_hash"], True),
        ("ix_auth_sessions_revoked_at", ["revoked_at"], False),
        ("ix_auth_sessions_user_id", ["user_id"], False),
    ):
        op.create_index(index_name, "auth_sessions", columns, unique=unique)

    op.create_table(
        "external_sync_current_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("business_key", sa.String(length=200), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_snapshot_id"], ["external_sync_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_instance",
            "scope_key",
            "entity_type",
            "business_key",
            name="uq_external_sync_current_record_key",
        ),
    )
    op.create_index(
        "ix_external_sync_current_scope_entity",
        "external_sync_current_records",
        ["source_instance", "scope_key", "entity_type"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_external_sync_current_records_entity_type", ["entity_type"]),
        (
            "ix_external_sync_current_records_last_snapshot_id",
            ["last_snapshot_id"],
        ),
        ("ix_external_sync_current_records_payload_sha256", ["payload_sha256"]),
        ("ix_external_sync_current_records_scope_key", ["scope_key"]),
        ("ix_external_sync_current_records_source_instance", ["source_instance"]),
        ("ix_external_sync_current_records_source_system", ["source_system"]),
    ):
        op.create_index(
            index_name, "external_sync_current_records", columns, unique=False
        )

    op.create_table(
        "external_sync_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("business_key", sa.String(length=200), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("last_batch_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_batch_id"], ["external_sync_batches.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_instance",
            "entity_type",
            "business_key",
            name="uq_external_sync_record_business_key",
        ),
    )
    op.create_index(
        "ix_external_sync_record_source_entity",
        "external_sync_records",
        ["source_instance", "entity_type"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_external_sync_records_entity_type", ["entity_type"]),
        ("ix_external_sync_records_last_batch_id", ["last_batch_id"]),
        ("ix_external_sync_records_payload_sha256", ["payload_sha256"]),
        ("ix_external_sync_records_source_instance", ["source_instance"]),
        ("ix_external_sync_records_source_system", ["source_system"]),
    ):
        op.create_index(index_name, "external_sync_records", columns, unique=False)

    op.create_table(
        "external_sync_snapshot_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_ref_id", sa.String(length=36), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("batch_id", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("total_sequences", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_ref_id"], ["external_sync_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_ref_id",
            "entity_type",
            "sequence",
            name="uq_external_sync_snapshot_batch_sequence",
        ),
        sa.UniqueConstraint(
            "source_instance",
            "batch_id",
            name="uq_external_sync_snapshot_batch_source_id",
        ),
    )
    for index_name, columns in (
        ("ix_external_sync_snapshot_batches_batch_id", ["batch_id"]),
        ("ix_external_sync_snapshot_batches_entity_type", ["entity_type"]),
        ("ix_external_sync_snapshot_batches_received_at", ["received_at"]),
        (
            "ix_external_sync_snapshot_batches_snapshot_ref_id",
            ["snapshot_ref_id"],
        ),
        (
            "ix_external_sync_snapshot_batches_source_instance",
            ["source_instance"],
        ),
    ):
        op.create_index(
            index_name, "external_sync_snapshot_batches", columns, unique=False
        )

    op.create_table(
        "external_sync_snapshot_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_ref_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("business_key", sa.String(length=200), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_ref_id"], ["external_sync_snapshots.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_ref_id",
            "entity_type",
            "business_key",
            name="uq_external_sync_snapshot_record_key",
        ),
    )
    op.create_index(
        "ix_external_sync_snapshot_record_entity",
        "external_sync_snapshot_records",
        ["snapshot_ref_id", "entity_type"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_external_sync_snapshot_records_entity_type", ["entity_type"]),
        (
            "ix_external_sync_snapshot_records_payload_sha256",
            ["payload_sha256"],
        ),
        (
            "ix_external_sync_snapshot_records_snapshot_ref_id",
            ["snapshot_ref_id"],
        ),
    ):
        op.create_index(
            index_name, "external_sync_snapshot_records", columns, unique=False
        )

    op.create_table(
        "media_attachments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_path"),
    )
    for index_name, columns in (
        ("ix_media_attachments_entity_id", ["entity_id"]),
        ("ix_media_attachments_entity_type", ["entity_type"]),
        ("ix_media_attachments_sha256", ["sha256"]),
    ):
        op.create_index(index_name, "media_attachments", columns, unique=False)

    op.create_table(
        "oam_personnel_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_instance", sa.String(length=128), nullable=False),
        sa.Column("oam_account_id", sa.String(length=100), nullable=False),
        sa.Column("oam_employee_id", sa.String(length=100), nullable=False),
        sa.Column("account", sa.String(length=100), nullable=False),
        sa.Column("job_no", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("oam_status", sa.String(length=24), nullable=False),
        sa.Column("source_present", sa.Boolean(), nullable=False),
        sa.Column("source_active", sa.Boolean(), nullable=False),
        sa.Column("login_eligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_reason", sa.String(length=160), nullable=False),
        sa.Column("login_enabled", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("last_seen_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_seen_snapshot_id"], ["external_sync_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_instance",
            "oam_account_id",
            name="uq_oam_personnel_source_account",
        ),
        sa.UniqueConstraint("user_id", name="uq_oam_personnel_user"),
    )
    op.create_index(
        "ix_oam_personnel_login_state",
        "oam_personnel_bindings",
        ["source_present", "source_active", "login_enabled"],
        unique=False,
    )
    for index_name, columns in (
        (
            "ix_oam_personnel_bindings_last_seen_snapshot_id",
            ["last_seen_snapshot_id"],
        ),
        ("ix_oam_personnel_bindings_login_eligible", ["login_eligible"]),
        ("ix_oam_personnel_bindings_login_enabled", ["login_enabled"]),
        ("ix_oam_personnel_bindings_name", ["name"]),
        ("ix_oam_personnel_bindings_oam_account_id", ["oam_account_id"]),
        ("ix_oam_personnel_bindings_source_active", ["source_active"]),
        ("ix_oam_personnel_bindings_source_instance", ["source_instance"]),
        ("ix_oam_personnel_bindings_source_present", ["source_present"]),
        ("ix_oam_personnel_bindings_user_id", ["user_id"]),
        ("ix_oam_personnel_mobile", ["mobile"]),
    ):
        op.create_index(
            index_name, "oam_personnel_bindings", columns, unique=False
        )

    op.create_table(
        "sms_login_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_biz_id", sa.String(length=160), nullable=False),
        sa.Column("requested_ip", sa.String(length=80), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sms_challenge_ip_created",
        "sms_login_challenges",
        ["requested_ip", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_sms_challenge_mobile_created",
        "sms_login_challenges",
        ["mobile", "created_at"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_sms_login_challenges_created_at", ["created_at"]),
        ("ix_sms_login_challenges_expires_at", ["expires_at"]),
        ("ix_sms_login_challenges_mobile", ["mobile"]),
        ("ix_sms_login_challenges_requested_ip", ["requested_ip"]),
        ("ix_sms_login_challenges_status", ["status"]),
    ):
        op.create_index(index_name, "sms_login_challenges", columns, unique=False)

    op.create_table(
        "warehouses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("province", sa.String(length=40), nullable=False),
        sa.Column("city", sa.String(length=40), nullable=False),
        sa.Column("warehouse_type", sa.String(length=32), nullable=False),
        sa.Column("condition_scope", sa.String(length=20), nullable=False),
        sa.Column("warehouse_level", sa.String(length=20), nullable=False),
        sa.Column("ownership_type", sa.String(length=24), nullable=False),
        sa.Column("position_scope", sa.String(length=24), nullable=False),
        sa.Column("parent_warehouse_id", sa.String(length=36), nullable=True),
        sa.Column("manager_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["manager_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["parent_warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warehouses_code", "warehouses", ["code"], unique=True)
    op.create_index("ix_warehouses_name", "warehouses", ["name"], unique=False)
    op.create_index(
        "ix_warehouses_province", "warehouses", ["province"], unique=False
    )

    op.create_table(
        "wechat_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("app_id", sa.String(length=80), nullable=False),
        sa.Column("openid", sa.String(length=160), nullable=False),
        sa.Column("unionid", sa.String(length=160), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "openid", name="uq_wechat_app_openid"),
        sa.UniqueConstraint("app_id", "user_id", name="uq_wechat_app_user"),
    )
    for index_name, columns in (
        ("ix_wechat_identities_app_id", ["app_id"]),
        ("ix_wechat_identities_openid", ["openid"]),
        ("ix_wechat_identities_unionid", ["unionid"]),
        ("ix_wechat_identities_user_id", ["user_id"]),
    ):
        op.create_index(index_name, "wechat_identities", columns, unique=False)

    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("warehouse_id", sa.String(length=36), nullable=False),
        sa.Column("holder_user_id", sa.String(length=36), nullable=True),
        sa.Column("holder_key", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False),
        sa.Column("quantity_occupied", sa.Integer(), nullable=False),
        sa.Column("quantity_in_transit", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity_occupied >= 0", name="ck_inventory_occupied_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_on_hand_nonnegative"
        ),
        sa.CheckConstraint(
            "quantity_in_transit >= 0", name="ck_inventory_transit_nonnegative"
        ),
        sa.ForeignKeyConstraint(["holder_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "warehouse_id",
            "holder_key",
            "material_id",
            "condition",
            name="uq_inventory_owner_material_condition",
        ),
    )
    op.create_index(
        "ix_inventory_warehouse_material",
        "inventory_balances",
        ["warehouse_id", "material_id"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_inventory_balances_condition", ["condition"]),
        ("ix_inventory_balances_material_id", ["material_id"]),
        ("ix_inventory_balances_warehouse_id", ["warehouse_id"]),
    ):
        op.create_index(index_name, "inventory_balances", columns, unique=False)

    op.create_table(
        "stocktake_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("warehouse_id", sa.String(length=36), nullable=False),
        sa.Column("assignee_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stocktake_tasks_number", "stocktake_tasks", ["number"], unique=True
    )
    op.create_index(
        "ix_stocktake_tasks_status", "stocktake_tasks", ["status"], unique=False
    )

    op.create_table(
        "transfers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("transfer_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("source_warehouse_id", sa.String(length=36), nullable=True),
        sa.Column("source_holder_user_id", sa.String(length=36), nullable=True),
        sa.Column("target_warehouse_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=True),
        sa.Column("requester_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by_id", sa.String(length=36), nullable=True),
        sa.Column("work_order_number", sa.String(length=80), nullable=False),
        sa.Column("logistics_company", sa.String(length=80), nullable=False),
        sa.Column("tracking_number", sa.String(length=100), nullable=False),
        sa.Column("external_reference", sa.String(length=100), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_holder_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["source_warehouse_id"], ["warehouses.id"]
        ),
        sa.ForeignKeyConstraint(
            ["target_warehouse_id"], ["warehouses.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns, unique in (
        ("ix_transfers_external_reference", ["external_reference"], False),
        ("ix_transfers_number", ["number"], True),
        ("ix_transfers_status", ["status"], False),
        ("ix_transfers_tracking_number", ["tracking_number"], False),
        ("ix_transfers_work_order_number", ["work_order_number"], False),
    ):
        op.create_index(index_name, "transfers", columns, unique=unique)

    op.create_table(
        "work_order_materials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("work_order_number", sa.String(length=80), nullable=False),
        sa.Column("warehouse_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("recovery_condition", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name="ck_work_order_material_positive"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_order_material_order_status",
        "work_order_materials",
        ["work_order_number", "status"],
        unique=False,
    )
    for index_name, columns in (
        ("ix_work_order_materials_material_id", ["material_id"]),
        ("ix_work_order_materials_status", ["status"]),
        ("ix_work_order_materials_user_id", ["user_id"]),
        ("ix_work_order_materials_warehouse_id", ["warehouse_id"]),
        ("ix_work_order_materials_work_order_number", ["work_order_number"]),
    ):
        op.create_index(index_name, "work_order_materials", columns, unique=False)

    op.create_table(
        "stocktake_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column("counted_quantity", sa.Integer(), nullable=True),
        sa.Column("remark", sa.String(length=240), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(
            ["task_id"], ["stocktake_tasks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "transfer_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transfer_id", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=20), nullable=False),
        sa.Column("remark", sa.String(length=240), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_transfer_item_positive"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.ForeignKeyConstraint(
            ["transfer_id"], ["transfers.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # Reverse dependency order keeps PostgreSQL foreign-key drops deterministic.
    op.drop_table("transfer_items")
    op.drop_table("stocktake_items")
    op.drop_table("work_order_materials")
    op.drop_table("transfers")
    op.drop_table("stocktake_tasks")
    op.drop_table("inventory_balances")
    op.drop_table("wechat_identities")
    op.drop_table("warehouses")
    op.drop_table("sms_login_challenges")
    op.drop_table("oam_personnel_bindings")
    op.drop_table("media_attachments")
    op.drop_table("external_sync_snapshot_records")
    op.drop_table("external_sync_snapshot_batches")
    op.drop_table("external_sync_records")
    op.drop_table("external_sync_current_records")
    op.drop_table("auth_sessions")
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_table("materials")
    op.drop_table("external_sync_snapshots")
    op.drop_table("external_sync_batches")
