"""Add the formal authentication runtime audit boundary.

Revision ID: 20260830_0005
Revises: 20260830_0004
Create Date: 2026-08-30

This revision seeds only the empty authentication audit-chain head required by
the append-only runtime writer, adds the hashed-IP challenge rate-limit index,
and makes challenge verification evidence explicit.  Existing non-empty code
hashes are classified as local hashes; empty legacy material remains explicitly
unknown and is never guessed to be provider-managed.  It creates no user,
authentication identity, role assignment, session, challenge, or audit event.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "20260830_0005"
down_revision: Union[str, Sequence[str], None] = "20260830_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUTHENTICATION_AUDIT_CHAIN_HEAD_ID = uuid.UUID(
    "30000000-0000-4000-8000-000000000002"
)
AUTHENTICATION_AUDIT_STREAM_KEY = "authentication"
LOGIN_CHALLENGE_IP_CREATED_INDEX = (
    "ix_login_challenges_requested_ip_created"
)
LOGIN_CHALLENGE_VERIFICATION_CHECK = (
    "ck_login_challenges_verification_material"
)


def upgrade() -> None:
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    audit_chain_head_table = _audit_chain_head_table()

    # Pre-create the stream so authentication code never races to create a
    # security-critical chain head on its first challenge or login event.
    op.bulk_insert(
        audit_chain_head_table,
        [
            {
                "id": AUTHENTICATION_AUDIT_CHAIN_HEAD_ID,
                "stream_key": AUTHENTICATION_AUDIT_STREAM_KEY,
                "last_event_id": None,
                "last_hash": None,
                "version": 0,
                "updated_at": seeded_at,
                "created_at": seeded_at,
            }
        ],
    )
    op.create_index(
        LOGIN_CHALLENGE_IP_CREATED_INDEX,
        "login_challenges",
        ["requested_ip_hash", "created_at"],
        unique=False,
    )

    # Add the discriminator as nullable while the exact existing rows are
    # classified.  The migration never infers provider-managed verification:
    # only a future runtime can choose that mode explicitly when it receives a
    # provider-owned challenge reference and stores no local code hash.
    op.add_column(
        "login_challenges",
        sa.Column("verification_mode", sa.String(length=24), nullable=True),
    )
    with op.batch_alter_table("login_challenges") as batch_op:
        batch_op.alter_column(
            "code_hash",
            existing_type=sa.String(length=128),
            nullable=True,
        )

    challenge_table = sa.table(
        "login_challenges",
        sa.column("code_hash", sa.String(length=128)),
        sa.column("verification_mode", sa.String(length=24)),
    )
    op.execute(
        challenge_table.update().values(
            verification_mode=sa.case(
                (
                    sa.and_(
                        challenge_table.c.code_hash.is_not(None),
                        sa.func.length(challenge_table.c.code_hash) > 0,
                    ),
                    "local_hash",
                ),
                else_="legacy_unknown",
            )
        )
    )
    with op.batch_alter_table("login_challenges") as batch_op:
        batch_op.alter_column(
            "verification_mode",
            existing_type=sa.String(length=24),
            nullable=False,
        )
        batch_op.create_check_constraint(
            LOGIN_CHALLENGE_VERIFICATION_CHECK,
            "(verification_mode = 'local_hash' AND code_hash IS NOT NULL "
            "AND length(code_hash) > 0) OR "
            "(verification_mode = 'provider_managed' AND code_hash IS NULL) OR "
            "verification_mode = 'legacy_unknown'",
        )


def downgrade() -> None:
    # Inspect the exact seed before changing any 0005-owned object.  Missing,
    # renamed, duplicated, advanced, or otherwise altered heads are all unsafe:
    # removing the index first and failing later would leave a partial downgrade.
    audit_chain_head_table = _audit_chain_head_table()
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
                == AUTHENTICATION_AUDIT_CHAIN_HEAD_ID,
                audit_chain_head_table.c.stream_key
                == AUTHENTICATION_AUDIT_STREAM_KEY,
            )
        )
    ).all()
    if len(matching_heads) != 1:
        raise RuntimeError(
            "cannot downgrade 0005: authentication audit head identity is "
            "missing or ambiguous"
        )

    authentication_head = matching_heads[0]
    expected_empty_seed = (
        authentication_head.id == AUTHENTICATION_AUDIT_CHAIN_HEAD_ID
        and authentication_head.stream_key
        == AUTHENTICATION_AUDIT_STREAM_KEY
        and authentication_head.last_event_id is None
        and authentication_head.last_hash is None
        and authentication_head.version == 0
    )
    if not expected_empty_seed:
        raise RuntimeError(
            "cannot downgrade 0005: authentication audit chain has been used "
            "or its seeded identity changed"
        )

    # Revision 0004 requires a non-null code_hash.  Provider-managed or other
    # null-hash evidence cannot be represented there, so stop rather than
    # inventing a value or partially removing the 0005 index/head first.
    login_challenge_table = sa.table(
        "login_challenges",
        sa.column("code_hash", sa.String(length=128)),
    )
    null_hash_exists = op.get_bind().execute(
        sa.select(sa.literal(1))
        .select_from(login_challenge_table)
        .where(login_challenge_table.c.code_hash.is_(None))
        .limit(1)
    ).first()
    if null_hash_exists is not None:
        raise RuntimeError(
            "cannot downgrade 0005: a null challenge code_hash cannot be "
            "represented by revision 0004"
        )

    op.drop_index(
        LOGIN_CHALLENGE_IP_CREATED_INDEX,
        table_name="login_challenges",
    )
    op.execute(
        audit_chain_head_table.delete().where(
            audit_chain_head_table.c.id
            == AUTHENTICATION_AUDIT_CHAIN_HEAD_ID
        )
    )
    with op.batch_alter_table("login_challenges") as batch_op:
        batch_op.drop_constraint(
            LOGIN_CHALLENGE_VERIFICATION_CHECK,
            type_="check",
        )
        batch_op.alter_column(
            "code_hash",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch_op.drop_column("verification_mode")


def _audit_chain_head_table():
    return sa.table(
        "audit_chain_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
