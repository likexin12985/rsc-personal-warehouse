"""Seed permissions for formal work-order material operations."""
from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa

revision = "20260918_0078"
down_revision = "20260917_0077"
branch_labels = depends_on = None

READ_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000060")
OPERATE_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000061")
ROLE_PERMISSION_ROWS = (
    (uuid.UUID("21000000-0000-4000-8000-000000000106"), uuid.UUID("10000000-0000-4000-8000-000000000001"), READ_PERMISSION_ID),
    (uuid.UUID("21000000-0000-4000-8000-000000000107"), uuid.UUID("10000000-0000-4000-8000-000000000001"), OPERATE_PERMISSION_ID),
    (uuid.UUID("21000000-0000-4000-8000-000000000108"), uuid.UUID("10000000-0000-4000-8000-000000000002"), READ_PERMISSION_ID),
    (uuid.UUID("21000000-0000-4000-8000-000000000109"), uuid.UUID("10000000-0000-4000-8000-000000000002"), OPERATE_PERMISSION_ID),
    (uuid.UUID("21000000-0000-4000-8000-000000000110"), uuid.UUID("10000000-0000-4000-8000-000000000003"), READ_PERMISSION_ID),
    (uuid.UUID("21000000-0000-4000-8000-000000000111"), uuid.UUID("10000000-0000-4000-8000-000000000003"), OPERATE_PERMISSION_ID),
)


def upgrade():
    now = datetime.now(timezone.utc)
    permissions = sa.table(
        "permissions", sa.column("id", sa.Uuid()), sa.column("resource", sa.String()),
        sa.column("action", sa.String()), sa.column("field_code", sa.String()),
        sa.column("description", sa.String()), sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    role_permissions = sa.table(
        "role_permissions", sa.column("id", sa.Uuid()), sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()), sa.column("effect", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )
    op.bulk_insert(permissions, [
        {"id": READ_PERMISSION_ID, "resource": "work_order_material", "action": "read", "field_code": "", "description": "Read formal work-order material facts and history", "created_at": now, "updated_at": now},
        {"id": OPERATE_PERMISSION_ID, "resource": "work_order_material", "action": "operate", "field_code": "", "description": "Post formal work-order material operations", "created_at": now, "updated_at": now},
    ])
    op.bulk_insert(role_permissions, [
        {"id": rid, "role_id": role, "permission_id": permission, "effect": "allow", "created_at": now}
        for rid, role, permission in ROLE_PERMISSION_ROWS
    ])


def downgrade():
    role_permissions = sa.table("role_permissions", sa.column("id", sa.Uuid()))
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(role_permissions.delete().where(role_permissions.c.id.in_([rid for rid, _, _ in ROLE_PERMISSION_ROWS])))
    op.execute(permissions.delete().where(permissions.c.id.in_([READ_PERMISSION_ID, OPERATE_PERMISSION_ID])))
