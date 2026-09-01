"""Add single-owner SMS challenge dispatch evidence.

Revision ID: 20260902_0041
Revises: 20260901_0040
Create Date: 2026-09-02

The provider call is a paid, non-transactional side effect.  ``OutId`` is only
an external correlation value, so a pending login challenge is not sufficient
proof that another process may safely call the provider.  This revision adds a
separate, single-owner dispatch fact and narrows the production API role from
table-wide login-challenge UPDATE to the exact lifecycle columns it uses.

The upgrade never calls a provider and never guesses whether an unexpired
provider-managed challenge was sent.  Such rows, impossible verification
states, duplicate provider references, or missing/ambiguous legacy acceptance
audits stop the migration before DDL.  Proven legacy acceptances and expired
uncertain rows are copied with deterministic non-secret sentinels.
"""

from __future__ import annotations

import hashlib
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260902_0041"
down_revision: Union[str, Sequence[str], None] = "20260901_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "sms_challenge_dispatches"
PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
BACKUP_ROLE = "star_oam_backup"
EDGE_ROLE = "star_oam_edge"

PROVIDER_REFERENCE_INDEX = "uq_sms_challenge_dispatches_provider_reference"
UNRESOLVED_MOBILE_INDEX = "uq_sms_challenge_dispatches_unresolved_mobile"
UNRESOLVED_LEASE_INDEX = "ix_sms_challenge_dispatches_unresolved_lease"
STATUS_INDEX = "ix_sms_challenge_dispatches_status"

PG_GUARD_FUNCTION = "rsc_guard_sms_challenge_dispatch_0041"
PG_ROW_TRIGGER = "trg_sms_challenge_dispatches_guard_0041"
PG_TRUNCATE_TRIGGER = "trg_sms_challenge_dispatches_no_truncate_0041"
SQLITE_INSERT_TRIGGER = "trg_sms_challenge_dispatches_insert_0041"
SQLITE_UPDATE_TRIGGER = "trg_sms_challenge_dispatches_update_0041"
SQLITE_DELETE_TRIGGER = "trg_sms_challenge_dispatches_delete_0041"

LEGACY_ACCEPTED_REQUEST_SHA256 = hashlib.sha256(
    b"rsc-sms-dispatch-0041|legacy-accepted-request"
).hexdigest()
LEGACY_UNCERTAIN_REQUEST_SHA256 = hashlib.sha256(
    b"rsc-sms-dispatch-0041|legacy-expired-uncertain-request"
).hexdigest()
LEGACY_ACCEPTED_OWNER_SHA256 = hashlib.sha256(
    b"rsc-sms-dispatch-0041|legacy-accepted-owner-sentinel"
).hexdigest()
LEGACY_UNCERTAIN_OWNER_SHA256 = hashlib.sha256(
    b"rsc-sms-dispatch-0041|legacy-expired-uncertain-owner-sentinel"
).hexdigest()

NO_DISPATCH_REASONS = (
    "identity_unavailable",
    "mobile_hour_limit",
    "ip_hour_limit",
    "send_interval",
)
OLD_ACCEPTED_ACTION = "authentication.sms.challenge_sent"

UNEXPIRED_PREFLIGHT_ERROR = (
    "0041 preflight failed: an unexpired provider-managed challenge without "
    "an accepted provider reference exists; disable SMS and wait for expiry"
)
IMPOSSIBLE_VERIFICATION_ERROR = (
    "0041 preflight failed: a verified or consumed provider-managed challenge "
    "has no accepted provider reference"
)
DUPLICATE_REFERENCE_ERROR = (
    "0041 preflight failed: duplicate provider references exist"
)
ACCEPTANCE_AUDIT_ERROR = (
    "0041 preflight failed: every accepted legacy challenge must have exactly "
    "one well-ordered authentication.sms.challenge_sent audit"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0041 while non-legacy SMS dispatch facts exist"
)


def _lower_hex_remainder(expression: str) -> str:
    for character in "0123456789abcdef":
        expression = f"replace({expression}, '{character}', '')"
    return expression


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0041 supports only PostgreSQL and SQLite")
    return dialect


def _uuid_text(expression: str, dialect: str) -> str:
    if dialect == "postgresql":
        return f"CAST({expression} AS text)"
    compact = f"replace(CAST({expression} AS text), '-', '')"
    return (
        f"substr({compact}, 1, 8) || '-' || substr({compact}, 9, 4) || '-' || "
        f"substr({compact}, 13, 4) || '-' || substr({compact}, 17, 4) || '-' || "
        f"substr({compact}, 21, 12)"
    )


def upgrade() -> None:
    dialect = _dialect_name()
    _lock_upgrade_evidence(dialect)
    _run_upgrade_preflight(dialect)
    _create_table()
    _backfill_legacy_dispatches(dialect)
    _create_indexes()
    if dialect == "postgresql":
        _create_postgresql_triggers()
        _apply_postgresql_acl()
    else:
        _create_sqlite_triggers()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0041 downgrade requires an online evidence check")
    dialect = _dialect_name()
    _lock_downgrade_evidence(dialect)
    if op.get_bind().exec_driver_sql(
        _unsafe_downgrade_fact_sql(dialect)
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_ROW_TRIGGER} ON public.{TABLE_NAME}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_TRUNCATE_TRIGGER} "
            f"ON public.{TABLE_NAME}"
        )
        op.execute(f"DROP FUNCTION IF EXISTS public.{PG_GUARD_FUNCTION}()")
        _revoke_postgresql_acl_and_restore_login_update()
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_INSERT_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")

    op.drop_index(STATUS_INDEX, table_name=TABLE_NAME)
    op.drop_index(UNRESOLVED_LEASE_INDEX, table_name=TABLE_NAME)
    op.drop_index(UNRESOLVED_MOBILE_INDEX, table_name=TABLE_NAME)
    op.drop_index(PROVIDER_REFERENCE_INDEX, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)


def _run_upgrade_preflight(dialect: str) -> None:
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError(
                "0041 offline upgrade is supported only for PostgreSQL SQL output"
            )
        op.execute(_postgresql_preflight_sql())
        return

    bind = op.get_bind()
    checks = (
        (_unexpired_preflight_sql(dialect), UNEXPIRED_PREFLIGHT_ERROR),
        (_impossible_verification_sql(), IMPOSSIBLE_VERIFICATION_ERROR),
        (_duplicate_reference_sql(), DUPLICATE_REFERENCE_ERROR),
        (_invalid_acceptance_audit_sql(dialect), ACCEPTANCE_AUDIT_ERROR),
    )
    for statement, message in checks:
        if bind.exec_driver_sql(statement).first() is not None:
            raise RuntimeError(message)


def _lock_upgrade_evidence(dialect: str) -> None:
    """Keep the preflight proof and its backfill in one serialized snapshot."""

    if dialect == "postgresql":
        op.execute(
            "LOCK TABLE public.login_challenges, public.audit_events, "
            "public.state_transition_events IN SHARE ROW EXCLUSIVE MODE"
        )
        return
    if context.is_offline_mode():
        return
    # Alembic owns the migration transaction.  A zero-row write obtains the
    # SQLite writer lock without changing evidence or firing row triggers.
    op.get_bind().exec_driver_sql(
        "UPDATE login_challenges SET status = status WHERE 0"
    )


def _unexpired_preflight_sql(dialect: str) -> str:
    expires_expression = (
        "datetime(expires_at)" if dialect == "sqlite" else "expires_at"
    )
    return f"""
SELECT 1
  FROM login_challenges
 WHERE verification_mode = 'provider_managed'
   AND provider_reference IS NULL
   AND {expires_expression} > CURRENT_TIMESTAMP
 LIMIT 1
"""


def _impossible_verification_sql() -> str:
    return """
SELECT 1
  FROM login_challenges
 WHERE verification_mode = 'provider_managed'
   AND provider_reference IS NULL
   AND status IN ('verified', 'consumed')
 LIMIT 1
"""


def _duplicate_reference_sql() -> str:
    return """
SELECT 1
  FROM login_challenges
 WHERE verification_mode = 'provider_managed'
   AND provider_reference IS NOT NULL
 GROUP BY provider, provider_reference
HAVING COUNT(*) > 1
 LIMIT 1
"""


def _invalid_acceptance_audit_sql(dialect: str) -> str:
    challenge_text = _uuid_text("challenge.id", dialect)
    accepted_details = _legacy_acceptance_event_details_sql(dialect)
    return f"""
SELECT 1
  FROM login_challenges AS challenge
 WHERE challenge.verification_mode = 'provider_managed'
   AND challenge.provider_reference IS NOT NULL
   AND (
       (SELECT COUNT(*)
          FROM audit_events AS event
         WHERE event.stream_key = 'authentication'
           AND event.action = '{OLD_ACCEPTED_ACTION}'
           AND event.aggregate_type = 'login_challenge'
           AND event.aggregate_id = {challenge_text}) <> 1
       OR (SELECT MIN(event.occurred_at)
             FROM audit_events AS event
            WHERE event.stream_key = 'authentication'
              AND event.action = '{OLD_ACCEPTED_ACTION}'
              AND event.aggregate_type = 'login_challenge'
              AND event.aggregate_id = {challenge_text}) < challenge.created_at
       OR EXISTS (
           SELECT 1
             FROM audit_events AS event
            WHERE event.stream_key = 'authentication'
              AND event.action = '{OLD_ACCEPTED_ACTION}'
              AND event.aggregate_type = 'login_challenge'
              AND event.aggregate_id = {challenge_text}
              AND NOT ({accepted_details})
       )
   )
 LIMIT 1
"""


def _legacy_acceptance_event_details_sql(dialect: str) -> str:
    if dialect == "postgresql":
        return """
(event.before_jsonb IS NULL OR event.before_jsonb = 'null'::jsonb)
AND COALESCE(event.after_jsonb ->> 'outcome', '') = 'accepted'
AND COALESCE(event.after_jsonb ->> 'reason_code', '') = 'provider_accepted'
AND COALESCE(event.after_jsonb ->> 'status', '') = 'pending'
AND COALESCE(event.after_jsonb ->> 'client_type', '') = challenge.client_type
""".strip()
    safe_after = (
        "CASE WHEN json_valid(event.after_jsonb) "
        "THEN event.after_jsonb ELSE '{}' END"
    )
    safe_before = (
        "CASE WHEN json_valid(event.before_jsonb) "
        "THEN event.before_jsonb ELSE '{}' END"
    )
    return f"""
(event.before_jsonb IS NULL OR json_type({safe_before}, '$') = 'null')
AND COALESCE(json_extract({safe_after}, '$.outcome'), '') = 'accepted'
AND COALESCE(json_extract({safe_after}, '$.reason_code'), '') = 'provider_accepted'
AND COALESCE(json_extract({safe_after}, '$.status'), '') = 'pending'
AND COALESCE(json_extract({safe_after}, '$.client_type'), '') = challenge.client_type
""".strip()


def _postgresql_preflight_sql() -> str:
    return f"""
DO $migration$
BEGIN
    IF EXISTS ({_unexpired_preflight_sql('postgresql').strip().removesuffix('LIMIT 1')}) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = '{UNEXPIRED_PREFLIGHT_ERROR}';
    END IF;
    IF EXISTS ({_impossible_verification_sql().strip().removesuffix('LIMIT 1')}) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = '{IMPOSSIBLE_VERIFICATION_ERROR}';
    END IF;
    IF EXISTS ({_duplicate_reference_exists_sql()}) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = '{DUPLICATE_REFERENCE_ERROR}';
    END IF;
    IF EXISTS ({_invalid_acceptance_audit_sql('postgresql').strip().removesuffix('LIMIT 1')}) THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = '{ACCEPTANCE_AUDIT_ERROR}';
    END IF;
END
$migration$
"""


def _duplicate_reference_exists_sql() -> str:
    return """
SELECT 1 FROM (
    SELECT provider, provider_reference
      FROM login_challenges
     WHERE verification_mode = 'provider_managed'
       AND provider_reference IS NOT NULL
     GROUP BY provider, provider_reference
    HAVING COUNT(*) > 1
) AS duplicate_reference
""".strip()


def _create_table() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("mobile_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("owner_token_hash", sa.String(length=64), nullable=True),
        sa.Column("provider_reference", sa.String(length=160), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uncertain_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared', 'sending', 'accepted', 'uncertain', 'expired')",
            name="ck_sms_challenge_dispatches_status",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64 AND "
            f"length({_lower_hex_remainder('request_sha256')}) = 0",
            name="ck_sms_challenge_dispatches_request_sha256",
        ),
        sa.CheckConstraint(
            "length(mobile_hash) = 64 AND "
            f"length({_lower_hex_remainder('mobile_hash')}) = 0",
            name="ck_sms_challenge_dispatches_mobile_hash",
        ),
        sa.CheckConstraint(
            "owner_token_hash IS NULL OR (length(owner_token_hash) = 64 AND "
            f"length({_lower_hex_remainder('owner_token_hash')}) = 0)",
            name="ck_sms_challenge_dispatches_owner_hash",
        ),
        sa.CheckConstraint(
            "(status = 'prepared' AND owner_token_hash IS NULL "
            "AND claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'sending' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'uncertain' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NOT NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'accepted' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NOT NULL AND provider_reference IS NOT NULL) OR "
            "(status = 'expired' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NOT NULL "
            "AND expired_at IS NOT NULL AND provider_reference IS NULL)",
            name="ck_sms_challenge_dispatches_state_evidence",
        ),
        sa.CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at >= claimed_at",
            name="ck_sms_challenge_dispatches_lease_order",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR accepted_at >= claimed_at",
            name="ck_sms_challenge_dispatches_accepted_order",
        ),
        sa.CheckConstraint(
            "uncertain_at IS NULL OR uncertain_at >= claimed_at",
            name="ck_sms_challenge_dispatches_uncertain_order",
        ),
        sa.CheckConstraint(
            "expired_at IS NULL OR expired_at >= claimed_at",
            name="ck_sms_challenge_dispatches_expired_order",
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["login_challenges.id"],
            name="fk_sms_challenge_dispatches_challenge_0041",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "challenge_id", name="pk_sms_challenge_dispatches_0041"
        ),
    )


def _backfill_legacy_dispatches(dialect: str) -> None:
    challenge_text = _uuid_text("challenge.id", dialect)
    accepted_details = _legacy_acceptance_event_details_sql(dialect)
    expired_expression = (
        "datetime(challenge.expires_at)" if dialect == "sqlite"
        else "challenge.expires_at"
    )
    no_dispatch_reasons = ", ".join(f"'{reason}'" for reason in NO_DISPATCH_REASONS)
    op.execute(
        f"""
INSERT INTO {TABLE_NAME} (
    challenge_id, provider, mobile_hash, status, request_sha256,
    owner_token_hash, provider_reference, claimed_at, lease_expires_at,
    accepted_at, uncertain_at, expired_at, created_at
)
SELECT challenge.id, challenge.provider, challenge.mobile_hash, 'accepted',
       '{LEGACY_ACCEPTED_REQUEST_SHA256}', '{LEGACY_ACCEPTED_OWNER_SHA256}',
       challenge.provider_reference, challenge.created_at, event.occurred_at,
       event.occurred_at, NULL, NULL, challenge.created_at
  FROM login_challenges AS challenge
  JOIN audit_events AS event
    ON event.stream_key = 'authentication'
   AND event.action = '{OLD_ACCEPTED_ACTION}'
   AND event.aggregate_type = 'login_challenge'
   AND event.aggregate_id = {challenge_text}
   AND {accepted_details}
 WHERE challenge.verification_mode = 'provider_managed'
   AND challenge.provider_reference IS NOT NULL
"""
    )
    op.execute(
        f"""
INSERT INTO {TABLE_NAME} (
    challenge_id, provider, mobile_hash, status, request_sha256,
    owner_token_hash, provider_reference, claimed_at, lease_expires_at,
    accepted_at, uncertain_at, expired_at, created_at
)
SELECT challenge.id, challenge.provider, challenge.mobile_hash, 'expired',
       '{LEGACY_UNCERTAIN_REQUEST_SHA256}',
       '{LEGACY_UNCERTAIN_OWNER_SHA256}', NULL, challenge.created_at,
       challenge.expires_at, NULL, challenge.expires_at,
       challenge.expires_at, challenge.created_at
  FROM login_challenges AS challenge
 WHERE challenge.verification_mode = 'provider_managed'
   AND challenge.provider_reference IS NULL
   AND {expired_expression} <= CURRENT_TIMESTAMP
   AND NOT EXISTS (
       SELECT 1
         FROM state_transition_events AS transition
        WHERE transition.aggregate_type = 'login_challenge'
          AND transition.aggregate_id = {challenge_text}
          AND transition.from_status IS NULL
          AND transition.to_status = 'cancelled'
          AND transition.reason IN ({no_dispatch_reasons})
   )
"""
    )


def _create_indexes() -> None:
    op.create_index(
        PROVIDER_REFERENCE_INDEX,
        TABLE_NAME,
        ["provider", "provider_reference"],
        unique=True,
        postgresql_where=sa.text("provider_reference IS NOT NULL"),
        sqlite_where=sa.text("provider_reference IS NOT NULL"),
    )
    op.create_index(
        UNRESOLVED_MOBILE_INDEX,
        TABLE_NAME,
        ["provider", "mobile_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('sending', 'uncertain')"),
        sqlite_where=sa.text("status IN ('sending', 'uncertain')"),
    )
    op.create_index(
        UNRESOLVED_LEASE_INDEX,
        TABLE_NAME,
        ["status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(STATUS_INDEX, TABLE_NAME, ["status"], unique=False)


def _create_postgresql_triggers() -> None:
    op.execute(_postgresql_guard_function_sql())
    op.execute(
        f"CREATE TRIGGER {PG_ROW_TRIGGER} BEFORE INSERT OR UPDATE OR DELETE "
        f"ON public.{TABLE_NAME} FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {PG_TRUNCATE_TRIGGER} BEFORE TRUNCATE "
        f"ON public.{TABLE_NAME} FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_GUARD_FUNCTION}()"
    )
    op.execute(
        f"ALTER TABLE public.{TABLE_NAME} ENABLE ALWAYS TRIGGER {PG_ROW_TRIGGER}"
    )
    op.execute(
        f"ALTER TABLE public.{TABLE_NAME} ENABLE ALWAYS TRIGGER "
        f"{PG_TRUNCATE_TRIGGER}"
    )


def _postgresql_guard_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_matches boolean;
    claim_transition boolean;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'SMS challenge dispatch evidence cannot be truncated';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'SMS challenge dispatch evidence cannot be deleted';
    END IF;

    IF NEW.mobile_hash IS NULL
       OR NEW.mobile_hash !~ '^[0-9a-f]{{64}}$'
       OR NEW.request_sha256 IS NULL
       OR NEW.request_sha256 !~ '^[0-9a-f]{{64}}$'
       OR (NEW.owner_token_hash IS NOT NULL
           AND NEW.owner_token_hash !~ '^[0-9a-f]{{64}}$') THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'invalid SMS dispatch digest evidence';
    END IF;

    IF NEW.status IS NULL OR NOT (
        (NEW.status = 'prepared'
         AND NEW.owner_token_hash IS NULL
         AND NEW.claimed_at IS NULL
         AND NEW.lease_expires_at IS NULL
         AND NEW.accepted_at IS NULL
         AND NEW.uncertain_at IS NULL
         AND NEW.expired_at IS NULL
         AND NEW.provider_reference IS NULL)
        OR
        (NEW.status = 'sending'
         AND NEW.owner_token_hash IS NOT NULL
         AND NEW.claimed_at IS NOT NULL
         AND NEW.lease_expires_at IS NOT NULL
         AND NEW.accepted_at IS NULL
         AND NEW.uncertain_at IS NULL
         AND NEW.expired_at IS NULL
         AND NEW.provider_reference IS NULL)
        OR
        (NEW.status = 'uncertain'
         AND NEW.owner_token_hash IS NOT NULL
         AND NEW.claimed_at IS NOT NULL
         AND NEW.lease_expires_at IS NOT NULL
         AND NEW.accepted_at IS NULL
         AND NEW.uncertain_at IS NOT NULL
         AND NEW.expired_at IS NULL
         AND NEW.provider_reference IS NULL)
        OR
        (NEW.status = 'accepted'
         AND NEW.owner_token_hash IS NOT NULL
         AND NEW.claimed_at IS NOT NULL
         AND NEW.lease_expires_at IS NOT NULL
         AND NEW.accepted_at IS NOT NULL
         AND NEW.provider_reference IS NOT NULL)
        OR
        (NEW.status = 'expired'
         AND NEW.owner_token_hash IS NOT NULL
         AND NEW.claimed_at IS NOT NULL
         AND NEW.lease_expires_at IS NOT NULL
         AND NEW.accepted_at IS NULL
         AND NEW.uncertain_at IS NOT NULL
         AND NEW.expired_at IS NOT NULL
         AND NEW.provider_reference IS NULL)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'invalid SMS dispatch state evidence';
    END IF;

    IF (NEW.claimed_at IS NOT NULL
        AND (NEW.created_at IS NULL OR NEW.claimed_at < NEW.created_at))
       OR (NEW.lease_expires_at IS NOT NULL
        AND (NEW.claimed_at IS NULL
             OR NEW.lease_expires_at < NEW.claimed_at))
       OR (NEW.accepted_at IS NOT NULL
           AND (NEW.claimed_at IS NULL OR NEW.accepted_at < NEW.claimed_at))
       OR (NEW.uncertain_at IS NOT NULL
           AND (NEW.claimed_at IS NULL OR NEW.uncertain_at < NEW.claimed_at))
       OR (NEW.expired_at IS NOT NULL
           AND (NEW.claimed_at IS NULL OR NEW.expired_at < NEW.claimed_at))
       OR (NEW.expired_at IS NOT NULL AND NEW.uncertain_at IS NOT NULL
           AND NEW.expired_at < NEW.uncertain_at)
       OR (NEW.accepted_at IS NOT NULL AND NEW.uncertain_at IS NOT NULL
           AND NEW.accepted_at < NEW.uncertain_at)
       OR (NEW.accepted_at IS NOT NULL AND NEW.expired_at IS NOT NULL
           AND NEW.accepted_at < NEW.expired_at) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'invalid SMS dispatch timestamp order';
    END IF;

    IF TG_OP = 'INSERT' THEN
        PERFORM 1
          FROM public.login_challenges AS challenge
         WHERE challenge.id = NEW.challenge_id
           AND challenge.verification_mode = 'provider_managed'
           AND challenge.provider = NEW.provider
           AND challenge.mobile_hash = NEW.mobile_hash
           AND challenge.status = 'pending'
           AND challenge.provider_reference IS NULL
         FOR KEY SHARE;
        parent_matches := FOUND;
        IF NEW.status <> 'prepared' OR NOT parent_matches THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'invalid prepared SMS dispatch parent';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.challenge_id IS DISTINCT FROM OLD.challenge_id
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.mobile_hash IS DISTINCT FROM OLD.mobile_hash
       OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'SMS dispatch identity and request evidence are immutable';
    END IF;

    claim_transition := OLD.status = 'prepared' AND NEW.status = 'sending'
        AND OLD.owner_token_hash IS NULL AND NEW.owner_token_hash IS NOT NULL
        AND OLD.claimed_at IS NULL AND NEW.claimed_at IS NOT NULL
        AND OLD.lease_expires_at IS NULL AND NEW.lease_expires_at IS NOT NULL;

    IF NOT (
        (OLD.status = 'prepared' AND NEW.status = 'sending')
        OR (OLD.status = 'sending' AND NEW.status IN ('accepted', 'uncertain', 'expired'))
        OR (OLD.status = 'uncertain' AND NEW.status IN ('accepted', 'expired'))
        OR (OLD.status = 'expired' AND NEW.status = 'accepted')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'invalid SMS dispatch state transition';
    END IF;

    IF (NEW.owner_token_hash IS DISTINCT FROM OLD.owner_token_hash
        OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
        OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at)
       AND NOT claim_transition THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'SMS dispatch ownership is immutable after claim';
    END IF;
    IF NEW.provider_reference IS DISTINCT FROM OLD.provider_reference
       AND NOT (NEW.status = 'accepted' AND OLD.provider_reference IS NULL
                AND NEW.provider_reference IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'invalid SMS dispatch provider reference mutation';
    END IF;
    IF NEW.accepted_at IS DISTINCT FROM OLD.accepted_at
       AND NOT (NEW.status = 'accepted' AND OLD.accepted_at IS NULL
                AND NEW.accepted_at IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'invalid SMS dispatch accepted timestamp mutation';
    END IF;
    IF NEW.uncertain_at IS DISTINCT FROM OLD.uncertain_at
       AND NOT (OLD.status = 'sending' AND NEW.status IN ('uncertain', 'expired')
                AND OLD.uncertain_at IS NULL AND NEW.uncertain_at IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'invalid SMS dispatch uncertain timestamp mutation';
    END IF;
    IF NEW.expired_at IS DISTINCT FROM OLD.expired_at
       AND NOT (OLD.status IN ('sending', 'uncertain') AND NEW.status = 'expired'
                AND OLD.expired_at IS NULL AND NEW.expired_at IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE = '55000',
            MESSAGE = 'invalid SMS dispatch expiry timestamp mutation';
    END IF;
    RETURN NEW;
END
$$
"""


def _create_sqlite_triggers() -> None:
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_INSERT_TRIGGER}
BEFORE INSERT ON {TABLE_NAME}
FOR EACH ROW
WHEN NEW.status <> 'prepared' OR NOT EXISTS (
    SELECT 1 FROM login_challenges AS challenge
     WHERE challenge.id = NEW.challenge_id
       AND challenge.verification_mode = 'provider_managed'
       AND challenge.provider = NEW.provider
       AND challenge.mobile_hash = NEW.mobile_hash
       AND challenge.status = 'pending'
       AND challenge.provider_reference IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'invalid prepared SMS dispatch parent');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_UPDATE_TRIGGER}
BEFORE UPDATE ON {TABLE_NAME}
FOR EACH ROW
WHEN NEW.challenge_id IS NOT OLD.challenge_id
  OR NEW.provider IS NOT OLD.provider
  OR NEW.mobile_hash IS NOT OLD.mobile_hash
  OR NEW.request_sha256 IS NOT OLD.request_sha256
  OR NEW.created_at IS NOT OLD.created_at
  OR NOT (
      (OLD.status = 'prepared' AND NEW.status = 'sending')
      OR (OLD.status = 'sending' AND NEW.status IN ('accepted', 'uncertain', 'expired'))
      OR (OLD.status = 'uncertain' AND NEW.status IN ('accepted', 'expired'))
      OR (OLD.status = 'expired' AND NEW.status = 'accepted')
  )
  OR ((NEW.owner_token_hash IS NOT OLD.owner_token_hash
       OR NEW.claimed_at IS NOT OLD.claimed_at
       OR NEW.lease_expires_at IS NOT OLD.lease_expires_at)
      AND NOT (
          OLD.status = 'prepared' AND NEW.status = 'sending'
          AND OLD.owner_token_hash IS NULL AND NEW.owner_token_hash IS NOT NULL
          AND OLD.claimed_at IS NULL AND NEW.claimed_at IS NOT NULL
          AND OLD.lease_expires_at IS NULL AND NEW.lease_expires_at IS NOT NULL
      ))
  OR (NEW.provider_reference IS NOT OLD.provider_reference
      AND NOT (NEW.status = 'accepted' AND OLD.provider_reference IS NULL
               AND NEW.provider_reference IS NOT NULL))
  OR (NEW.accepted_at IS NOT OLD.accepted_at
      AND NOT (NEW.status = 'accepted' AND OLD.accepted_at IS NULL
               AND NEW.accepted_at IS NOT NULL))
  OR (NEW.uncertain_at IS NOT OLD.uncertain_at
      AND NOT (OLD.status = 'sending' AND NEW.status IN ('uncertain', 'expired')
               AND OLD.uncertain_at IS NULL AND NEW.uncertain_at IS NOT NULL))
  OR (NEW.expired_at IS NOT OLD.expired_at
      AND NOT (OLD.status IN ('sending', 'uncertain') AND NEW.status = 'expired'
               AND OLD.expired_at IS NULL AND NEW.expired_at IS NOT NULL))
BEGIN
    SELECT RAISE(ABORT, 'invalid SMS challenge dispatch mutation');
END
"""
    )
    op.execute(
        f"CREATE TRIGGER {SQLITE_DELETE_TRIGGER} BEFORE DELETE ON {TABLE_NAME} "
        "FOR EACH ROW BEGIN SELECT RAISE(ABORT, "
        "'SMS challenge dispatch evidence cannot be deleted'); END"
    )


def _apply_postgresql_acl() -> None:
    function_signature = f"public.{PG_GUARD_FUNCTION}()"
    dispatch_update_columns = (
        "status, owner_token_hash, provider_reference, claimed_at, "
        "lease_expires_at, accepted_at, uncertain_at, expired_at"
    )
    login_update_columns = (
        "provider_reference, attempts, status, verified_at, consumed_at"
    )
    op.execute(f"REVOKE ALL ON TABLE public.{TABLE_NAME} FROM PUBLIC")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {function_signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER TABLE public.{TABLE_NAME} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {function_signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'GRANT SELECT, INSERT ON TABLE public.{TABLE_NAME} TO {PRODUCTION_API_ROLE}';
        EXECUTE 'GRANT UPDATE ({dispatch_update_columns}) ON TABLE public.{TABLE_NAME} TO {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE UPDATE ON TABLE public.login_challenges FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'GRANT UPDATE ({login_update_columns}) ON TABLE public.login_challenges TO {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {PRODUCTION_API_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {BACKUP_ROLE}';
        EXECUTE 'GRANT SELECT ON TABLE public.{TABLE_NAME} TO {BACKUP_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {BACKUP_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{EDGE_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {EDGE_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {EDGE_ROLE}';
    END IF;
END
$$
"""
    )


def _lock_downgrade_evidence(dialect: str) -> None:
    bind = op.get_bind()
    if dialect == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE public.audit_events, public.login_challenges, "
            "public.sms_challenge_dispatches IN ACCESS EXCLUSIVE MODE"
        )
        return
    bind.exec_driver_sql(
        f"UPDATE {TABLE_NAME} SET status = status WHERE 0"
    )


def _unsafe_downgrade_fact_sql(dialect: str) -> str:
    prefix = "public." if dialect == "postgresql" else ""
    challenge_text = _uuid_text("challenge.id", dialect)
    dispatch_challenge_text = _uuid_text("dispatch.challenge_id", dialect)
    accepted_details = _legacy_acceptance_event_details_sql(dialect)
    return f"""
SELECT 1
  FROM {prefix}{TABLE_NAME} AS dispatch
  JOIN {prefix}login_challenges AS challenge
    ON challenge.id = dispatch.challenge_id
 WHERE NOT (
     (dispatch.status = 'accepted'
      AND dispatch.request_sha256 = '{LEGACY_ACCEPTED_REQUEST_SHA256}'
      AND dispatch.owner_token_hash = '{LEGACY_ACCEPTED_OWNER_SHA256}'
      AND dispatch.provider = challenge.provider
      AND dispatch.mobile_hash = challenge.mobile_hash
      AND dispatch.provider_reference = challenge.provider_reference
      AND dispatch.claimed_at = challenge.created_at
      AND dispatch.accepted_at = dispatch.lease_expires_at
      AND dispatch.uncertain_at IS NULL
      AND dispatch.expired_at IS NULL
      AND (SELECT COUNT(*) FROM {prefix}audit_events AS event
            WHERE event.stream_key = 'authentication'
              AND event.action = '{OLD_ACCEPTED_ACTION}'
              AND event.aggregate_type = 'login_challenge'
              AND event.aggregate_id = {challenge_text}
              AND event.aggregate_id = {dispatch_challenge_text}
              AND {accepted_details}
              AND event.occurred_at = dispatch.accepted_at) = 1)
     OR
     (dispatch.status = 'expired'
      AND dispatch.request_sha256 = '{LEGACY_UNCERTAIN_REQUEST_SHA256}'
      AND dispatch.owner_token_hash = '{LEGACY_UNCERTAIN_OWNER_SHA256}'
      AND dispatch.provider = challenge.provider
      AND dispatch.mobile_hash = challenge.mobile_hash
      AND dispatch.provider_reference IS NULL
      AND challenge.provider_reference IS NULL
      AND dispatch.claimed_at = challenge.created_at
      AND dispatch.lease_expires_at = challenge.expires_at
      AND dispatch.accepted_at IS NULL
      AND dispatch.uncertain_at = challenge.expires_at
      AND dispatch.expired_at = challenge.expires_at)
 )
 LIMIT 1
"""


def _revoke_postgresql_acl_and_restore_login_update() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE UPDATE (provider_reference, attempts, status, verified_at, consumed_at) ON TABLE public.login_challenges FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'GRANT UPDATE ON TABLE public.login_challenges TO {PRODUCTION_API_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {BACKUP_ROLE}';
    END IF;
END
$$
"""
    )
