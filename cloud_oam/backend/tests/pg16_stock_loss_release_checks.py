"""Shared loss acceptance body for owned native and disposable CI PG16.

The caller owns cluster creation, isolation and cleanup. No supplied production
DSN is accepted here or by either entry point.
"""
from sqlalchemy import text

from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as sources
from pg16_stock_loss_submit_gate import run as submit


def run(engines, *, tracking, migrate, provision, check_review_seals=False):
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
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == '20261213_0164'
    from pg16_stock_loss_regional_review_gate import run as regional_reviews
    result['regionalReview'] = regional_reviews(engines,check_seals=check_review_seals)
    migrate('retained-regional-downgrade', 'downgrade', '20261125_0146',
        '0156 immutable approval seal history requires retention' if check_review_seals else
        '0147 immutable regional review history requires retention')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
    from pg16_stock_loss_headquarters_review_gate import run as headquarters_reviews
    result['headquartersReview'] = headquarters_reviews(engines,check_seals=check_review_seals)
    migrate('retained-headquarters-downgrade', 'downgrade', '20261126_0147',
        '0156 immutable approval seal history requires retention' if check_review_seals else
        '0148 immutable headquarters review history requires retention')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
    security()
    result.update(
        emptyRoundtrip=True, retainedLossBlocksDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,
        retainedRegionalReviewBlocksDowngrade=not check_review_seals, retainedHeadquartersReviewBlocksDowngrade=not check_review_seals,
        retainedReviewSealsBlockDowngrade=check_review_seals,
    )
    assert result["passed"] and result["submission"]["passed"]
    return result
