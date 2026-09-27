"""Shared loss acceptance body for owned native and disposable CI PG16.

The caller owns cluster creation, isolation and cleanup. No supplied production
DSN is accepted here or by either entry point.
"""
from sqlalchemy import text

from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as sources
from pg16_stock_loss_submit_gate import run as submit


def run(engines, *, tracking, migrate, provision):
    if tracking not in ("quantity", "serial"):
        raise ValueError("tracking must be quantity or serial")
    migrate("initial-upgrade", "upgrade", "head")
    migrate("empty-downgrade", "downgrade", "20261123_0144")
    migrate("empty-reupgrade", "upgrade", "head")
    provision()

    def security():
        validate_production_database_security(
            engines["star_oam_api"], expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )

    security()
    result = sources(engines, tracking=tracking, after_preview=submit)
    migrate(
        "retained-loss-downgrade", "downgrade", "20261124_0145",
        "0146 immutable loss seal history requires retention",
    )
    with engines["star_oam_migrator"].connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261129_0150"
    from pg16_stock_loss_regional_review_gate import run as regional_reviews
    result['regionalReview'] = regional_reviews(engines)
    migrate('retained-regional-downgrade', 'downgrade', '20261125_0146',
        '0147 immutable regional review history requires retention')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261129_0150'
    from pg16_stock_loss_headquarters_review_gate import run as headquarters_reviews
    result['headquartersReview'] = headquarters_reviews(engines)
    migrate('retained-headquarters-downgrade', 'downgrade', '20261126_0147',
        '0148 immutable headquarters review history requires retention')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261129_0150'
    security()
    result.update(
        emptyRoundtrip=True, retainedLossBlocksDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,
        retainedRegionalReviewBlocksDowngrade=True, retainedHeadquartersReviewBlocksDowngrade=True,
    )
    assert result["passed"] and result["submission"]["passed"]
    return result
