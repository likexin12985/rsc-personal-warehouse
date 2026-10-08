"""Run the reviewed Alembic graph with the bounded discovery cache enabled."""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from migration_script_cache import cache_migration_compilation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="alembic.ini")
    parser.add_argument("revision", nargs="?", default="head")
    parser.add_argument("--sql", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config).resolve(strict=True)
    config = Config(str(config_path))
    script_location = Path(config.get_main_option("script_location"))
    if not script_location.is_absolute():
        script_location = (config_path.parent / script_location).resolve()
    versions = script_location / "versions"
    with cache_migration_compilation(versions):
        command.upgrade(config, args.revision, sql=args.sql)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the image entrypoint
    raise SystemExit(main())
