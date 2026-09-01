"""Add the reviewed role-assignment administration boundary.

Revision ID: 20260830_0004
Revises: 20260830_0003
Create Date: 2026-08-30

This revision adds two fixed administrator permissions, a database guard
against concurrent duplicate current role assignments, and the empty
authorization audit-chain head required by the runtime append-only writer.  It
deliberately creates no user, person, authentication identity, or role
assignment: those records require a separately reviewed provisioning input.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260830_0004"
down_revision: Union[str, Sequence[str], None] = "20260830_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
AUTHORIZATION_AUDIT_CHAIN_HEAD_ID = uuid.UUID(
    "30000000-0000-4000-8000-000000000001"
)

PEOPLE_READ_MINIMAL_PERMISSION_ID = uuid.UUID(
    "20000000-0000-4000-8000-000000000013"
)
ROLE_ASSIGNMENT_MANAGE_PROVINCIAL_PERMISSION_ID = uuid.UUID(
    "20000000-0000-4000-8000-000000000014"
)

ADMIN_PEOPLE_READ_MINIMAL_ROLE_PERMISSION_ID = uuid.UUID(
    "21000000-0000-4000-8000-000000000027"
)
ADMIN_ROLE_ASSIGNMENT_MANAGE_PROVINCIAL_ROLE_PERMISSION_ID = uuid.UUID(
    "21000000-0000-4000-8000-000000000028"
)

PERMISSION_DEFINITIONS = (
    (
        PEOPLE_READ_MINIMAL_PERMISSION_ID,
        "people",
        "read_minimal",
        "Read the minimum person fields required for reviewed role assignment",
    ),
    (
        ROLE_ASSIGNMENT_MANAGE_PROVINCIAL_PERMISSION_ID,
        "role_assignment",
        "manage_provincial",
        "Manage provincial-manager assignments with an explicit organization scope",
    ),
)

ROLE_PERMISSION_DEFINITIONS = (
    (
        ADMIN_PEOPLE_READ_MINIMAL_ROLE_PERMISSION_ID,
        PEOPLE_READ_MINIMAL_PERMISSION_ID,
    ),
    (
        ADMIN_ROLE_ASSIGNMENT_MANAGE_PROVINCIAL_ROLE_PERMISSION_ID,
        ROLE_ASSIGNMENT_MANAGE_PROVINCIAL_PERMISSION_ID,
    ),
)


def upgrade() -> None:
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    _assert_no_duplicate_current_role_assignments()

    # Pre-create the authorization stream so the runtime never races to create
    # a security-critical audit chain head on its first role change.
    audit_chain_head_table = sa.table(
        "audit_chain_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        audit_chain_head_table,
        [
            {
                "id": AUTHORIZATION_AUDIT_CHAIN_HEAD_ID,
                "stream_key": "authorization",
                "last_event_id": None,
                "last_hash": None,
                "version": 0,
                "updated_at": seeded_at,
                "created_at": seeded_at,
            }
        ],
    )

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

    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "id": role_permission_id,
                "role_id": ADMIN_ROLE_ID,
                "permission_id": permission_id,
                "effect": "allow",
                "created_at": seeded_at,
            }
            for role_permission_id, permission_id in ROLE_PERMISSION_DEFINITIONS
        ],
    )

    # Deliberately key only on status, not valid_to.  A stale active/scheduled
    # row must continue to block a replacement until an explicit expiry or
    # revocation transition is recorded; otherwise a missed status update could
    # silently create two current grants for the same scope.
    op.create_index(
        "uq_role_assignments_current_scope",
        "role_assignments",
        ["user_id", "role_id", "scope_type", "scope_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
        sqlite_where=sa.text("status IN ('scheduled', 'active')"),
    )


def _assert_no_duplicate_current_role_assignments() -> None:
    """Fail before seeding 0004 when prototype data violates the new guard."""

    if context.is_offline_mode():
        # The only supported production database is PostgreSQL.  Keep the
        # generated review SQL honest by including the same fail-closed gate
        # used by online upgrades instead of silently emitting only the index.
        op.execute(
            sa.text(
                """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM role_assignments
        WHERE status IN ('scheduled', 'active')
        GROUP BY user_id, role_id, scope_type, scope_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION '0004 preflight failed: duplicate current role assignments';
    END IF;
END $$
"""
            )
        )
        return

    role_assignment_table = sa.table(
        "role_assignments",
        sa.column("user_id", sa.String(length=36)),
        sa.column("role_id", sa.Uuid()),
        sa.column("scope_type", sa.String(length=24)),
        sa.column("scope_id", sa.String(length=80)),
        sa.column("status", sa.String(length=20)),
    )
    duplicate = op.get_bind().execute(
        sa.select(sa.literal(1))
        .select_from(role_assignment_table)
        .where(role_assignment_table.c.status.in_(("scheduled", "active")))
        .group_by(
            role_assignment_table.c.user_id,
            role_assignment_table.c.role_id,
            role_assignment_table.c.scope_type,
            role_assignment_table.c.scope_id,
        )
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "0004 preflight failed: duplicate current role assignments must "
            "be reviewed and explicitly closed before migration"
        )


def downgrade() -> None:
    # An authorization chain is append-only evidence.  Once the seeded head
    # has advanced, removing it would sever the link to the existing audit
    # events and a later re-upgrade could incorrectly start a second genesis
    # chain.  Refuse the downgrade before changing any 0004-owned object.
    audit_chain_head_table = sa.table(
        "audit_chain_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
    )
    matching_heads = op.get_bind().execute(
        sa.select(
            audit_chain_head_table.c.id,
            audit_chain_head_table.c.stream_key,
            audit_chain_head_table.c.last_event_id,
            audit_chain_head_table.c.last_hash,
            audit_chain_head_table.c.version,
        ).where(
            sa.or_(
                audit_chain_head_table.c.id
                == AUTHORIZATION_AUDIT_CHAIN_HEAD_ID,
                audit_chain_head_table.c.stream_key == "authorization",
            )
        )
    ).all()
    if len(matching_heads) > 1:
        raise RuntimeError(
            "cannot downgrade 0004: authorization audit head identity is ambiguous"
        )
    authorization_head = matching_heads[0] if matching_heads else None
    if authorization_head is not None:
        expected_empty_seed = (
            authorization_head.id == AUTHORIZATION_AUDIT_CHAIN_HEAD_ID
            and authorization_head.stream_key == "authorization"
            and authorization_head.last_event_id is None
            and authorization_head.last_hash is None
            and authorization_head.version == 0
        )
        if not expected_empty_seed:
            raise RuntimeError(
                "cannot downgrade 0004: authorization audit chain has been used "
                "or its seeded identity changed"
            )

    op.drop_index(
        "uq_role_assignments_current_scope",
        table_name="role_assignments",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
        sqlite_where=sa.text("status IN ('scheduled', 'active')"),
    )

    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
    )
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id.in_(
                [
                    role_permission_id
                    for role_permission_id, _ in ROLE_PERMISSION_DEFINITIONS
                ]
            )
        )
    )
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
    )
    op.execute(
        permission_table.delete().where(
            permission_table.c.id.in_(
                [
                    permission_id
                    for permission_id, _, _, _ in PERMISSION_DEFINITIONS
                ]
            )
        )
    )
    if authorization_head is not None:
        op.execute(
            audit_chain_head_table.delete().where(
                audit_chain_head_table.c.id
                == AUTHORIZATION_AUDIT_CHAIN_HEAD_ID
            )
        )
