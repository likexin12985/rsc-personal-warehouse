"""Add the formal authentication idempotency operation ledger.

Revision ID: 20260830_0006
Revises: 20260830_0005
Create Date: 2026-08-30

The ledger persists only domain-separated hashes/HMACs and an encrypted replay
envelope for the four formal authentication writes.  It stores no plaintext
request or response payload and creates no operation row during migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_0006"
down_revision: Union[str, Sequence[str], None] = "20260830_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "auth_idempotency_operations"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("client_type", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hmac", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("response_nonce", sa.LargeBinary(length=12), nullable=True),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("encryption_key_version", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("auth_session_id", sa.String(length=36), nullable=True),
        sa.Column("input_refresh_token_id", sa.Uuid(), nullable=True),
        sa.Column("output_refresh_token_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('sms_login', 'wechat_login', "
            "'session_refresh', 'session_logout')",
            name="ck_auth_idempotency_operations_type",
        ),
        sa.CheckConstraint(
            "client_type IN ('web', 'miniprogram')",
            name="ck_auth_idempotency_operations_client_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_auth_idempotency_operations_status",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "length(scope_hash) = 64 AND length(request_hmac) = 64",
            name="ck_auth_idempotency_operations_request_hashes",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND response_ciphertext IS NULL AND "
            "response_nonce IS NULL AND response_sha256 IS NULL AND "
            "encryption_key_version IS NULL AND http_status IS NULL AND "
            "completed_at IS NULL) OR "
            "(status IN ('completed', 'failed') AND "
            "response_ciphertext IS NOT NULL AND "
            "length(response_ciphertext) > 0 AND response_nonce IS NOT NULL "
            "AND length(response_nonce) = 12 AND response_sha256 IS NOT NULL "
            "AND length(response_sha256) = 64 AND "
            "encryption_key_version IS NOT NULL AND "
            "encryption_key_version > 0 AND http_status IS NOT NULL AND "
            "((status = 'completed' AND http_status BETWEEN 200 AND 299) OR "
            "(status = 'failed' AND http_status BETWEEN 400 AND 599)) AND "
            "completed_at IS NOT NULL)",
            name="ck_auth_idempotency_operations_response_evidence",
        ),
        sa.CheckConstraint(
            "status = 'completed' OR output_refresh_token_id IS NULL",
            name="ck_auth_idempotency_operations_output_token_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND "
            "(completed_at IS NULL OR completed_at >= created_at)",
            name="ck_auth_idempotency_operations_time_order",
        ),
        sa.ForeignKeyConstraint(["auth_session_id"], ["auth_sessions.id"]),
        sa.ForeignKeyConstraint(
            ["input_refresh_token_id"], ["auth_refresh_tokens.id"]
        ),
        sa.ForeignKeyConstraint(
            ["output_refresh_token_id"], ["auth_refresh_tokens.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_auth_idempotency_operations_key_hash",
        ),
    )
    op.create_index(
        "ix_auth_idempotency_operations_scope_status_expires",
        TABLE_NAME,
        ["scope_hash", "status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_idempotency_operations_status_expires",
        TABLE_NAME,
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Production downgrades run only after writers are stopped, but the
        # database must still close the check/drop race against a stray writer.
        bind.exec_driver_sql(
            "LOCK TABLE auth_idempotency_operations IN ACCESS EXCLUSIVE MODE"
        )
    operation_table = sa.table(
        TABLE_NAME,
        sa.column("id", sa.Uuid()),
    )
    existing_operation = bind.execute(
        sa.select(sa.literal(1)).select_from(operation_table).limit(1)
    ).first()
    if existing_operation is not None:
        raise RuntimeError(
            "cannot downgrade 0006: authentication idempotency operation "
            "ledger is not empty"
        )

    op.drop_index(
        "ix_auth_idempotency_operations_status_expires",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_auth_idempotency_operations_scope_status_expires",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)
