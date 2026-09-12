"""Replace the technician fulfillment seed with recipient-only acceptance."""
from datetime import datetime, timezone
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa

revision = "20260923_0083"
down_revision = "20260922_0082"
branch_labels = depends_on = None

PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000062")
FULFILL_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000059")
TECHNICIAN_SEED_ID = uuid.UUID("21000000-0000-4000-8000-000000000105")
TECHNICIAN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000003")
OLD_HASH = "c6318534d12f800c067ad8ced6c2700018545c95c50e8b5078c56f3037b06210"
NEW_HASH = "88d751f169702542808c0d9def764b608b93774821e54bfbcaba177c236da838"


def _readiness(upgrade):
    if op.get_bind().dialect.name == "sqlite":
        return
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("0083 supports only PostgreSQL and SQLite")
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    older = runpy.run_path(str(Path(__file__).with_name("20260920_0080_inbound_posting_acl.py")))
    replace = older["_migration_0072"]()["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()", expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
            label="runtime_readiness_0083")


def _tables():
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()), sa.column("resource", sa.String()), sa.column("action", sa.String()), sa.column("field_code", sa.String()), sa.column("description", sa.String()), sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()))
    grants = sa.table("role_permissions", sa.column("id", sa.Uuid()), sa.column("role_id", sa.Uuid()), sa.column("permission_id", sa.Uuid()), sa.column("effect", sa.String()), sa.column("created_at", sa.DateTime()))
    return permissions, grants


def _swap_seed(grants, old, new):
    db = op.get_bind()
    if db.dialect.name == "postgresql":
        op.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM public.role_permissions
                WHERE id = '{TECHNICIAN_SEED_ID}' AND role_id = '{TECHNICIAN_ROLE_ID}'
                  AND permission_id = '{old}') THEN
                RAISE EXCEPTION '0083 technician permission seed changed; review custom authorization';
            END IF;
        END $$""")
    else:
        seed = db.execute(sa.select(grants).where(grants.c.id == TECHNICIAN_SEED_ID)).mappings().one_or_none()
        if seed is None or seed["role_id"] != TECHNICIAN_ROLE_ID or seed["permission_id"] != old:
            raise RuntimeError("0083 technician permission seed changed; review the custom authorization before migration")
    # Preserve an explicit deny; a migration must never turn it into allow.
    db.execute(grants.update().where(grants.c.id == TECHNICIAN_SEED_ID).values(permission_id=new))


def _invalidate_technician_sessions():
    users = sa.table("users", sa.column("id", sa.String()), sa.column("authorization_version", sa.Integer()))
    assignments = sa.table("role_assignments", sa.column("user_id", sa.String()), sa.column("role_id", sa.Uuid()))
    op.execute(users.update().where(users.c.id.in_(sa.select(assignments.c.user_id).where(assignments.c.role_id == TECHNICIAN_ROLE_ID))).values(authorization_version=users.c.authorization_version + 1))


def upgrade():
    _readiness(True)
    permissions, grants = _tables()
    now = datetime.now(timezone.utc)
    op.bulk_insert(permissions, [{"id": PERMISSION_ID, "resource": "material_request", "action": "receive", "field_code": "", "description": "Accept only packages explicitly bound to the current recipient and personal warehouse", "created_at": now, "updated_at": now}])
    _swap_seed(grants, FULFILL_PERMISSION_ID, PERMISSION_ID)
    _invalidate_technician_sessions()


def downgrade():
    # Retain the dedicated recovery boundary once new recipient facts exist.
    audit = sa.table("audit_events", sa.column("action", sa.String()))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.audit_events WHERE action = 'my_receipt_registered') THEN
                RAISE EXCEPTION '0083 downgrade blocked: recipient receipt facts exist';
            END IF;
        END $$""")
    elif op.get_bind().scalar(sa.select(sa.func.count()).select_from(audit).where(audit.c.action == "my_receipt_registered")):
        raise RuntimeError("0083 downgrade blocked: recipient receipt facts exist")
    _readiness(False)
    permissions, grants = _tables()
    _swap_seed(grants, PERMISSION_ID, FULFILL_PERMISSION_ID)
    op.execute(permissions.delete().where(permissions.c.id == PERMISSION_ID))
    _invalidate_technician_sessions()
