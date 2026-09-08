"""Add a distinct permission for fulfillment writes after approval."""
from alembic import op
import sqlalchemy as sa
import uuid
from datetime import datetime, timezone
revision = "20260916_0076"
down_revision = "20260915_0075"
branch_labels = depends_on = None
PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000059")
ROLE_PERMISSION_ROWS = ((uuid.UUID("21000000-0000-4000-8000-000000000103"), uuid.UUID("10000000-0000-4000-8000-000000000001")), (uuid.UUID("21000000-0000-4000-8000-000000000104"), uuid.UUID("10000000-0000-4000-8000-000000000002")), (uuid.UUID("21000000-0000-4000-8000-000000000105"), uuid.UUID("10000000-0000-4000-8000-000000000003")))
def upgrade():
    now = datetime.now(timezone.utc)
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()), sa.column("resource", sa.String()), sa.column("action", sa.String()), sa.column("field_code", sa.String()), sa.column("description", sa.String()), sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()))
    role_permissions = sa.table("role_permissions", sa.column("id", sa.Uuid()), sa.column("role_id", sa.Uuid()), sa.column("permission_id", sa.Uuid()), sa.column("effect", sa.String()), sa.column("created_at", sa.DateTime()))
    op.bulk_insert(permissions, [{"id": PERMISSION_ID, "resource": "material_request", "action": "fulfill", "field_code": "", "description": "Register shipment, receipt and personal inbound fulfillment facts", "created_at": now, "updated_at": now}])
    op.bulk_insert(role_permissions, [{"id": rid, "role_id": role, "permission_id": PERMISSION_ID, "effect": "allow", "created_at": now} for rid, role in ROLE_PERMISSION_ROWS])
def downgrade():
    role_permissions = sa.table("role_permissions", sa.column("id", sa.Uuid()))
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(role_permissions.delete().where(role_permissions.c.id.in_([rid for rid, _ in ROLE_PERMISSION_ROWS])))
    op.execute(permissions.delete().where(permissions.c.id == PERMISSION_ID))
