"""Authorize the notification delivery worker and operator retry surface.

The notification tables already exist in the V1 foundation and 0107 added the
worker lease columns.  This migration closes the production ACL gap: the API
role may read and append delivery evidence, update only the delivery state
columns, and may not delete or alter notification attempts.  The operator
permissions are deliberately granted to the national admin role only.

This migration advances the OAM readiness marker to this head.  The function
body has no new database dependency here, so the catalog hash remains
unchanged while its embedded migration revision is advanced together with the
table ACL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import runpy
import uuid

from alembic import op
import sqlalchemy as sa


revision = "20261018_0108"
down_revision = "20261017_0107"
branch_labels = depends_on = None

RUNTIME_READY_BODY_SHA256_0107 = (
    "fabc93a9f0066f158ba54952bd4417436189f08f92b03686d5ac646f95f1dcb6"
)
RUNTIME_READY_BODY_SHA256_0108 = RUNTIME_READY_BODY_SHA256_0107
OLD_HASH = RUNTIME_READY_BODY_SHA256_0107
NEW_HASH = RUNTIME_READY_BODY_SHA256_0108

READ_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000069")
RETRY_PERMISSION_ID = uuid.UUID("20000000-0000-4000-8000-000000000070")
ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
READ_ROLE_PERMISSION_ID = uuid.UUID("21000000-0000-4000-8000-000000000132")
RETRY_ROLE_PERMISSION_ID = uuid.UUID("21000000-0000-4000-8000-000000000133")

DELIVERY_UPDATE_COLUMNS = (
    "status",
    "provider_message_id",
    "attempts",
    "sent_at",
    "delivered_at",
    "read_at",
    "last_error",
    "locked_at",
    "locked_by",
    "updated_at",
)


def _permission_tables():
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String()),
        sa.column("action", sa.String()),
        sa.column("field_code", sa.String()),
        sa.column("description", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    return permissions, role_permissions


def _seed_permissions() -> None:
    permissions, role_permissions = _permission_tables()
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        permissions,
        [
            {
                "id": READ_PERMISSION_ID,
                "resource": "notification_delivery",
                "action": "read",
                "field_code": "",
                "description": "Read redacted notification delivery records",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": RETRY_PERMISSION_ID,
                "resource": "notification_delivery",
                "action": "retry",
                "field_code": "",
                "description": "Explicitly retry a definitively failed notification delivery",
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    op.bulk_insert(
        role_permissions,
        [
            {
                "id": READ_ROLE_PERMISSION_ID,
                "role_id": ADMIN_ROLE_ID,
                "permission_id": READ_PERMISSION_ID,
                "effect": "allow",
                "created_at": now,
            },
            {
                "id": RETRY_ROLE_PERMISSION_ID,
                "role_id": ADMIN_ROLE_ID,
                "permission_id": RETRY_PERMISSION_ID,
                "effect": "allow",
                "created_at": now,
            },
        ],
    )


def _delete_permissions() -> None:
    permissions, role_permissions = _permission_tables()
    op.execute(
        role_permissions.delete().where(
            role_permissions.c.id.in_(
                (READ_ROLE_PERMISSION_ID, RETRY_ROLE_PERMISSION_ID)
            )
        )
    )
    op.execute(
        permissions.delete().where(
            permissions.c.id.in_((READ_PERMISSION_ID, RETRY_PERMISSION_ID))
        )
    )


def _create_retry_idempotency_index() -> None:
    predicate = sa.text(
        "stream_key = 'material_request' "
        "AND action = 'notification_delivery.retry'"
    )
    op.create_index(
        "uq_audit_events_notification_retry_request_id_0108",
        "audit_events",
        ["request_id"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def _drop_retry_idempotency_index() -> None:
    op.drop_index(
        "uq_audit_events_notification_retry_request_id_0108",
        table_name="audit_events",
    )


def _grant_runtime_acl() -> None:
    op.execute(
        "REVOKE ALL ON TABLE public.notification_deliveries, "
        "public.notification_attempts FROM PUBLIC"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.notification_deliveries, "
        "public.notification_attempts TO star_oam_api"
    )
    op.execute(
        "GRANT UPDATE ("
        + ", ".join(DELIVERY_UPDATE_COLUMNS)
        + ") ON TABLE public.notification_deliveries TO star_oam_api"
    )


def _replace_readiness(*, upgrade: bool) -> None:
    """Move the immutable readiness marker with this migration head."""

    source = runpy.run_path(
        str(Path(__file__).with_name("20260912_0072_outbound_postings.py"))
    )
    replace = source["_previous"]()["_previous"]()["_previous"]()[
        "_replace_function_source"
    ]
    old_revision, new_revision = (
        (down_revision, revision) if upgrade else (revision, down_revision)
    )
    replace(
        signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=RUNTIME_READY_BODY_SHA256_0107,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0108,
        replacements=((old_revision, new_revision),),
        label="notification_delivery_readiness_0108",
    )


def _revoke_runtime_acl() -> None:
    op.execute(
        "REVOKE UPDATE ("
        + ", ".join(DELIVERY_UPDATE_COLUMNS)
        + ") ON TABLE public.notification_deliveries FROM star_oam_api"
    )
    op.execute(
        "REVOKE SELECT, INSERT ON TABLE public.notification_deliveries, "
        "public.notification_attempts FROM star_oam_api"
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0108 supports PostgreSQL and SQLite only")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        _replace_readiness(upgrade=True)
        _grant_runtime_acl()
    _create_retry_idempotency_index()
    _seed_permissions()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0108 supports PostgreSQL and SQLite only")
    _delete_permissions()
    _drop_retry_idempotency_index()
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        _replace_readiness(upgrade=False)
        _revoke_runtime_acl()


__all__ = [
    "ADMIN_ROLE_ID",
    "DELIVERY_UPDATE_COLUMNS",
    "NEW_HASH",
    "OLD_HASH",
    "READ_PERMISSION_ID",
    "RETRY_PERMISSION_ID",
    "READ_ROLE_PERMISSION_ID",
    "RETRY_ROLE_PERMISSION_ID",
    "RUNTIME_READY_BODY_SHA256_0107",
    "RUNTIME_READY_BODY_SHA256_0108",
    "down_revision",
    "revision",
]
