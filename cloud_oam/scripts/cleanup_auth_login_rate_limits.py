#!/usr/bin/env python3
"""Bounded cleanup entrypoint for expired formal-login rate buckets.

No database URL is read from application settings or the environment.  The
operator must supply an exact target and cutoff.  PostgreSQL additionally
requires an explicit acknowledgement so a local invocation cannot silently
fall through to a configured production database.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os

from sqlalchemy import create_engine


def _aware_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--as-of", required=True, type=_aware_timestamp)
    parser.add_argument("--batch-size", required=True, type=int)
    parser.add_argument(
        "--allow-postgresql",
        action="store_true",
        help="explicitly acknowledge a PostgreSQL cleanup target",
    )
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 10_000:
        parser.error("--batch-size must be between 1 and 10000")
    if args.database_url.startswith("postgresql+") and not args.allow_postgresql:
        parser.error("PostgreSQL cleanup requires --allow-postgresql")
    if not args.database_url.startswith(("sqlite+", "postgresql+psycopg://")):
        parser.error("database URL must be explicit sqlite+ or postgresql+psycopg")

    # The application model module constructs settings at import time.  Bind
    # that import to this exact CLI target and a non-serving runtime; no .env or
    # historical default target participates in cleanup selection.
    os.environ["OAM_ENVIRONMENT"] = "test"
    os.environ["OAM_DATABASE_URL"] = args.database_url
    from app.formal_services.authentication_rate_limit import (
        cleanup_expired_authentication_login_rate_limit_buckets,
    )

    engine = create_engine(args.database_url, pool_pre_ping=True, future=True)
    try:
        deleted = cleanup_expired_authentication_login_rate_limit_buckets(
            engine,
            now=args.as_of,
            batch_size=args.batch_size,
        )
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "cleanup": "auth_login_rate_limit_buckets",
                "deleted": deleted,
                "as_of": args.as_of.isoformat(),
                "batch_size": args.batch_size,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
