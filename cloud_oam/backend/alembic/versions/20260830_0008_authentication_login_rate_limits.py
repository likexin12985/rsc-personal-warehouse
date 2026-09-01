"""Add pseudonymous database buckets for formal login admission control.

Revision ID: 20260830_0008
Revises: 20260830_0007
Create Date: 2026-08-30

The migration creates only empty, short-lived counter infrastructure.  It
does not infer identities, migrate authentication attempts, call a provider,
or copy any raw IP address, mobile, WeChat code, openid or unionid.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_0008"
down_revision: Union[str, Sequence[str], None] = "20260830_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "auth_login_rate_limit_buckets"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("hash_version", sa.Integer(), nullable=False),
        sa.Column("scope_hmac", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('sms_login', 'wechat_login')",
            name="ck_auth_login_rate_limit_buckets_operation",
        ),
        sa.CheckConstraint(
            "scope_type IN ('global', 'ip', 'identity')",
            name="ck_auth_login_rate_limit_buckets_scope",
        ),
        sa.CheckConstraint(
            "length(scope_hmac) = 64",
            name="ck_auth_login_rate_limit_buckets_scope_hmac",
        ),
        sa.CheckConstraint(
            "hash_version > 0",
            name="ck_auth_login_rate_limit_buckets_hash_version",
        ),
        sa.CheckConstraint(
            "window_seconds BETWEEN 10 AND 3600",
            name="ck_auth_login_rate_limit_buckets_window_seconds",
        ),
        sa.CheckConstraint(
            "request_count > 0",
            name="ck_auth_login_rate_limit_buckets_request_count",
        ),
        sa.CheckConstraint(
            "expires_at > window_started_at AND cleanup_after > expires_at",
            name="ck_auth_login_rate_limit_buckets_expiry",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_type",
            "scope_type",
            "hash_version",
            "scope_hmac",
            "window_started_at",
            "window_seconds",
            name="uq_auth_login_rate_limit_bucket_window",
        ),
    )
    op.create_index(
        "ix_auth_login_rate_limit_buckets_cleanup_after",
        TABLE_NAME,
        ["cleanup_after"],
        unique=False,
    )
    op.create_index(
        "ix_auth_login_rate_limit_buckets_lookup",
        TABLE_NAME,
        [
            "operation_type",
            "scope_type",
            "hash_version",
            "scope_hmac",
            "window_started_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The lock closes the check/drop race.  Operators must also stop all
        # formal login writers before a production downgrade.
        bind.exec_driver_sql(
            "LOCK TABLE auth_login_rate_limit_buckets IN ACCESS EXCLUSIVE MODE"
        )
    bucket_table = sa.table(TABLE_NAME, sa.column("id", sa.Uuid()))
    if bind.execute(
        sa.select(sa.literal(1)).select_from(bucket_table).limit(1)
    ).first() is not None:
        raise RuntimeError(
            "cannot downgrade 0008: authentication login rate-limit buckets "
            "must be safely expired and cleaned before downgrade"
        )

    op.drop_index(
        "ix_auth_login_rate_limit_buckets_lookup",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_auth_login_rate_limit_buckets_cleanup_after",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
