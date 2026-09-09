"""Grant the runtime API its append-only access to inbound postings."""

from pathlib import Path
import runpy

from alembic import op


revision = "20260920_0080"
down_revision = "20260919_0079"
branch_labels = depends_on = None
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0079 = (
    "9dbb698d8c5f6f01883c8692a8a1e87204137a326948d840f3a191d26f40e3c0"
)
RUNTIME_READY_BODY_SHA256_0080 = (
    "39c99f7b25ea5cbb22befa6176e8ea6b0237ace130e3848792d2aa864edd3583"
)


def _migration_0072():
    return runpy.run_path(
        str(Path(__file__).with_name("20260912_0072_outbound_postings.py"))
    )


def _replace_readiness(*, upgrade: bool) -> None:
    migration = _migration_0072()
    replace = (
        migration["_previous"]()["_previous"]()["_previous"]()[
            "_replace_function_source"
        ]
    )
    old_revision, new_revision = (
        (down_revision, revision) if upgrade else (revision, down_revision)
    )
    expected_hash, replacement_hash = (
        (RUNTIME_READY_BODY_SHA256_0079, RUNTIME_READY_BODY_SHA256_0080)
        if upgrade
        else (RUNTIME_READY_BODY_SHA256_0080, RUNTIME_READY_BODY_SHA256_0079)
    )
    replace(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=expected_hash,
        replacement_hash=replacement_hash,
        replacements=((old_revision, new_revision),),
        label="runtime_readiness_0080",
    )


def _verify_readiness(expected_hash: str) -> None:
    migration = runpy.run_path(
        str(Path(__file__).with_name("20260919_0079_runtime_readiness_head.py"))
    )
    migration["_verify_runtime_ready"](expected_hash)


def _lock_boundary() -> None:
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0080 supports only PostgreSQL and SQLite")
    _lock_boundary()
    _verify_readiness(RUNTIME_READY_BODY_SHA256_0079)
    _replace_readiness(upgrade=True)
    op.execute(
        "GRANT SELECT, INSERT ON TABLE public.inbound_postings TO star_oam_api"
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0080 supports only PostgreSQL and SQLite")
    _lock_boundary()
    op.execute(
        "REVOKE SELECT, INSERT ON TABLE public.inbound_postings FROM star_oam_api"
    )
    _replace_readiness(upgrade=False)
    _verify_readiness(RUNTIME_READY_BODY_SHA256_0079)
