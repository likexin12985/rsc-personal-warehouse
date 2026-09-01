"""Activate the additive V1.0 formal identity and stage-one RBAC schema.

Revision ID: 20260830_0003
Revises: 20260830_0002
Create Date: 2026-08-30

This revision is explicit and self-contained.  It preserves every legacy user
field, marks all pre-existing users ``pending_identity`` through the column
default, and deliberately performs no identity/person mapping.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_0003"
down_revision: Union[str, Sequence[str], None] = "20260830_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_IDS = {
    "admin": uuid.UUID("10000000-0000-4000-8000-000000000001"),
    "provincial_manager": uuid.UUID("10000000-0000-4000-8000-000000000002"),
    "technician": uuid.UUID("10000000-0000-4000-8000-000000000003"),
    "star_headquarters_approver": uuid.UUID(
        "10000000-0000-4000-8000-000000000004"
    ),
}

PERMISSION_DEFINITIONS = (
    (
        uuid.UUID("20000000-0000-4000-8000-000000000001"),
        "account",
        "read_self",
        "Read the current account only",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000002"),
        "access_context",
        "read",
        "Read the current effective access context",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000003"),
        "dashboard",
        "read",
        "Read the dashboard within assigned data scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000004"),
        "oam_data",
        "read",
        "Read validated OAM projections within assigned data scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000005"),
        "inventory",
        "read",
        "Read inventory projections within assigned data scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000006"),
        "legacy_transfer_history",
        "read",
        "Read quarantined transfer history within assigned data scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000007"),
        "audit",
        "read",
        "Read immutable audit events",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000008"),
        "system_settings",
        "read",
        "Read system settings",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000009"),
        "auth_session",
        "manage",
        "Manage authenticated device sessions",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000010"),
        "material_request_approval",
        "decide_level_1",
        "Decide regional level-one material request approval",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000011"),
        "material_request_approval",
        "decide_level_2",
        "Decide NIO headquarters level-two material request approval",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000012"),
        "material_request_approval",
        "decide_level_3",
        "Decide StarCharge headquarters level-three material request approval",
    ),
)

ROLE_PERMISSION_KEYS = {
    "admin": (1, 2, 3, 4, 5, 6, 7, 8, 9, 11),
    "provincial_manager": (1, 2, 3, 4, 5, 6, 10),
    "technician": (1, 2, 3, 4, 5, 6),
    "star_headquarters_approver": (1, 2, 12),
}


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("person_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "account_status",
                sa.String(length=32),
                server_default=sa.text("'pending_identity'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "authorization_version",
                sa.BigInteger(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_users_person_id_people", "people", ["person_id"], ["id"]
        )
        batch_op.create_unique_constraint("uq_users_person_id", ["person_id"])
        batch_op.create_check_constraint(
            "ck_users_account_status",
            "account_status IN ('pending_identity', 'active', "
            "'restricted_handover', 'suspended', 'disabled')",
        )
        batch_op.create_index("ix_users_person_id", ["person_id"], unique=False)
        batch_op.create_index(
            "ix_users_account_status", ["account_status"], unique=False
        )

    with op.batch_alter_table("auth_identities") as batch_op:
        batch_op.add_column(
            sa.Column(
                "hash_version",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_auth_identities_hash_version_positive", "hash_version > 0"
        )
        batch_op.create_check_constraint(
            "ck_auth_identities_status_timestamps",
            "(status = 'pending' AND verified_at IS NULL AND revoked_at IS NULL) "
            "OR (status = 'active' AND verified_at IS NOT NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_at IS NOT NULL)",
        )

    with op.batch_alter_table("login_challenges") as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider",
                sa.String(length=40),
                server_default=sa.text("'legacy_unknown'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("provider_reference", sa.String(length=160), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "client_type",
                sa.String(length=24),
                server_default=sa.text("'legacy_unknown'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_login_challenges_client_type",
            "client_type IN ('web', 'miniprogram', 'legacy_unknown')",
        )

    with op.batch_alter_table("role_assignments") as batch_op:
        batch_op.add_column(
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("revoked_by", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "reason",
                sa.Text(),
                server_default=sa.text("''"),
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_role_assignments_revoked_by_users",
            "users",
            ["revoked_by"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_role_assignments_revocation_state",
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL) "
            "OR (status <> 'revoked' AND revoked_at IS NULL AND revoked_by IS NULL)",
        )
        batch_op.create_index(
            "ix_role_assignments_revoked_by", ["revoked_by"], unique=False
        )

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="ck_auth_refresh_tokens_consumed_order",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_auth_refresh_tokens_revoked_order",
        ),
        sa.CheckConstraint(
            "replaced_by_id IS NULL OR (replaced_by_id <> id AND consumed_at IS NOT NULL)",
            name="ck_auth_refresh_tokens_replacement",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["auth_refresh_tokens.id"]
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["auth_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "replaced_by_id", name="uq_auth_refresh_tokens_replaced_by_id"
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_hash"),
    )
    op.create_index(
        "ix_auth_refresh_tokens_issued_at",
        "auth_refresh_tokens",
        ["issued_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_refresh_tokens_session_active",
        "auth_refresh_tokens",
        ["session_id", "consumed_at", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_refresh_tokens_session_id",
        "auth_refresh_tokens",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_auth_refresh_tokens_token_hash",
        "auth_refresh_tokens",
        ["token_hash"],
        unique=False,
    )

    op.create_table(
        "audit_chain_heads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stream_key", sa.String(length=160), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=True),
        sa.Column("last_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(last_event_id IS NULL AND last_hash IS NULL) OR "
            "(last_event_id IS NOT NULL AND last_hash IS NOT NULL)",
            name="ck_audit_chain_heads_last_event_pair",
        ),
        sa.CheckConstraint("version >= 0", name="ck_audit_chain_heads_version"),
        sa.ForeignKeyConstraint(["last_event_id"], ["audit_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "last_event_id", name="uq_audit_chain_heads_last_event_id"
        ),
    )
    op.create_index(
        "ix_audit_chain_heads_stream_key",
        "audit_chain_heads",
        ["stream_key"],
        unique=True,
    )

    _seed_permissions()


def _seed_permissions() -> None:
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(length=100)),
        sa.column("action", sa.String(length=80)),
        sa.column("field_code", sa.String(length=100)),
        sa.column("description", sa.String(length=300)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": permission_id,
                "resource": resource,
                "action": action,
                "field_code": "",
                "description": description,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
            for permission_id, resource, action, description in PERMISSION_DEFINITIONS
        ],
    )

    permission_by_number = {
        number: definition[0]
        for number, definition in enumerate(PERMISSION_DEFINITIONS, start=1)
    }
    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = []
    sequence = 1
    for role_code, permission_numbers in ROLE_PERMISSION_KEYS.items():
        for permission_number in permission_numbers:
            rows.append(
                {
                    "id": uuid.UUID(
                        f"21000000-0000-4000-8000-{sequence:012d}"
                    ),
                    "role_id": ROLE_IDS[role_code],
                    "permission_id": permission_by_number[permission_number],
                    "effect": "allow",
                    "created_at": seeded_at,
                }
            )
            sequence += 1
    op.bulk_insert(role_permission_table, rows)


def downgrade() -> None:
    role_permission_ids = ", ".join(
        f"'{uuid.UUID(f'21000000-0000-4000-8000-{sequence:012d}')}" + "'"
        for sequence in range(1, 27)
    )
    permission_ids = ", ".join(
        f"'{definition[0]}'" for definition in PERMISSION_DEFINITIONS
    )
    op.execute(
        sa.text(f"DELETE FROM role_permissions WHERE id IN ({role_permission_ids})")
    )
    op.execute(sa.text(f"DELETE FROM permissions WHERE id IN ({permission_ids})"))

    op.drop_index(
        "ix_audit_chain_heads_stream_key", table_name="audit_chain_heads"
    )
    op.drop_table("audit_chain_heads")

    op.drop_index(
        "ix_auth_refresh_tokens_token_hash", table_name="auth_refresh_tokens"
    )
    op.drop_index(
        "ix_auth_refresh_tokens_session_id", table_name="auth_refresh_tokens"
    )
    op.drop_index(
        "ix_auth_refresh_tokens_session_active", table_name="auth_refresh_tokens"
    )
    op.drop_index(
        "ix_auth_refresh_tokens_issued_at", table_name="auth_refresh_tokens"
    )
    op.drop_table("auth_refresh_tokens")

    with op.batch_alter_table("role_assignments") as batch_op:
        batch_op.drop_index("ix_role_assignments_revoked_by")
        batch_op.drop_constraint(
            "ck_role_assignments_revocation_state", type_="check"
        )
        batch_op.drop_constraint(
            "fk_role_assignments_revoked_by_users", type_="foreignkey"
        )
        batch_op.drop_column("reason")
        batch_op.drop_column("revoked_by")
        batch_op.drop_column("revoked_at")

    with op.batch_alter_table("login_challenges") as batch_op:
        batch_op.drop_constraint("ck_login_challenges_client_type", type_="check")
        batch_op.drop_column("client_type")
        batch_op.drop_column("provider_reference")
        batch_op.drop_column("provider")

    with op.batch_alter_table("auth_identities") as batch_op:
        batch_op.drop_constraint(
            "ck_auth_identities_status_timestamps", type_="check"
        )
        batch_op.drop_constraint(
            "ck_auth_identities_hash_version_positive", type_="check"
        )
        batch_op.drop_column("hash_version")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_account_status")
        batch_op.drop_index("ix_users_person_id")
        batch_op.drop_constraint("ck_users_account_status", type_="check")
        batch_op.drop_constraint("uq_users_person_id", type_="unique")
        batch_op.drop_constraint("fk_users_person_id_people", type_="foreignkey")
        batch_op.drop_column("authorization_version")
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("account_status")
        batch_op.drop_column("person_id")
