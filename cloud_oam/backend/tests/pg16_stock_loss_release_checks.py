"""Shared loss acceptance body for owned native and disposable CI PG16.

The caller owns cluster creation, isolation and cleanup. No supplied production
DSN is accepted here or by either entry point.
"""
import hashlib
import importlib.util
import json
import re

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as sources
from pg16_stock_loss_submit_gate import run as submit
from test_postgresql16_release_gate import (
    CLOUD_ROOT, HEAD_REVISION, _assert_shipment_retention_failure,
)


_RETAINED_GUARDS = {
    '20261125_0146': ('stock_loss_request_seals', '0146 immutable loss seal history requires retention'),
    '20261126_0147': ('stock_loss_regional_reviews', '0147 immutable regional review history requires retention'),
    '20261127_0148': ('stock_loss_headquarters_reviews', '0148 immutable headquarters review history requires retention'),
    '20261205_0156': ('stock_loss_review_request_seals', '0156 immutable approval seal history requires retention'),
}


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _retained_snapshot(owner):
    """Observe every public fact and catalog after all fixture writers joined.

    No plaintext row enters an assertion. The existing catalog probe retains
    functions, table/column ACLs, constraints, indexes, triggers and FK guards.
    Separate connections observe each committed post-refusal state.
    """
    probe = _module(CLOUD_ROOT / 'backend/alembic/contact_envelope_0181/catalog_probe.py',
        'loss_retention_catalog_probe')
    with owner.connect() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        assert tuple(db.execute(text("SELECT current_user, session_user, current_database(), "
            "current_setting('server_version_num')::int / 10000")).one()) == (
                'star_oam_migrator', 'star_oam_migrator', 'rsc_pg16_release_gate', 16)
        revision = db.scalars(text('SELECT version_num FROM public.alembic_version')).all()
        assert revision == [HEAD_REVISION]
        tables = tuple(db.scalars(text("SELECT relname FROM pg_class WHERE "
            "relnamespace='public'::regnamespace AND relkind IN ('r','p') ORDER BY relname")))
        assert tables and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', table) for table in tables)
        facts = {}
        for table in tables:
            rows = db.execute(text(f'SELECT to_jsonb(fact)::text FROM public.{table} AS fact'))
            digests = sorted(hashlib.sha256(row[0].encode()).digest() for row in rows)
            facts[table] = (len(digests), hashlib.sha256(b''.join(digests)).hexdigest())
        catalog = probe.snapshot(db)
        assert set(catalog['tables']) == set(tables)
        shipment_facts = db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.material_requests "
            "WHERE shipment_status <> 'not_started')"))
        return dict(revision=revision, facts=facts, shipment_facts=shipment_facts,
            catalog=hashlib.sha256(json.dumps(catalog, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest())


def _assert_retained_loss(owner, *, migrate, label, destination, blocking_revision):
    """Prove the observed 0168 fence and the original loss fence separately."""
    table, blocker = _RETAINED_GUARDS[blocking_revision]
    assert re.fullmatch(r'[0-9]{8}_[0-9]{4}', destination), 'numbered destination required'
    assert int(destination[-4:]) < int(blocking_revision[-4:]) < 168, (
        'both retention boundaries must be crossed')
    before = _retained_snapshot(owner)
    assert before['shipment_facts'], 'this loss fixture must retain proven shipment facts'
    assert before['facts'][table][0] > 0, 'original loss retention facts are required'
    completed = migrate(label, 'downgrade', destination,
        '0168 downgrade blocked: shipment projection facts exist')
    _assert_shipment_retention_failure(completed)
    assert _retained_snapshot(owner) == before, 'chain refusal changed facts or catalog'

    paths = tuple((CLOUD_ROOT / 'backend/alembic/versions').glob(f'{blocking_revision}_*.py'))
    assert len(paths) == 1, 'exact historical loss migration required'
    historical = _module(paths[0], 'loss_retention_' + blocking_revision)
    assert historical.revision == blocking_revision
    with owner.connect() as db:
        transaction = db.begin()
        try:
            assert tuple(db.execute(text('SELECT current_user, session_user')).one()) == (
                'star_oam_migrator', 'star_oam_migrator')
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            with EnvironmentContext(Config(), None) as environment:
                environment.configure(connection=db)
                with Operations.context(environment.get_context()):
                    with pytest.raises(DBAPIError) as caught:
                        historical.downgrade()
                    original = caught.value.orig
                    assert original.sqlstate == 'P0001', 'wrong independent retention SQLSTATE'
                    assert original.diag.message_primary == blocker, 'wrong independent retention guard'
        finally:
            transaction.rollback()
    assert _retained_snapshot(owner) == before, 'independent refusal changed facts or catalog'
    print(f'PG16 loss retention: exact 0168 and independent {blocking_revision} refusal; '
        'all public facts and catalogs unchanged PASS', flush=True)


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
    _assert_retained_loss(engines['star_oam_migrator'], migrate=migrate,
        label='retained-loss-downgrade', destination='20261124_0145',
        blocking_revision='20261125_0146')
    with engines["star_oam_migrator"].connect() as db:
        assert db.scalars(text("SELECT version_num FROM alembic_version")).all() == [HEAD_REVISION]
    from pg16_stock_loss_regional_review_gate import run as regional_reviews
    result['regionalReview'] = regional_reviews(engines,check_seals=check_review_seals)
    _assert_retained_loss(engines['star_oam_migrator'], migrate=migrate,
        label='retained-regional-downgrade', destination='20261125_0146',
        blocking_revision='20261205_0156' if check_review_seals else '20261126_0147')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalars(text('SELECT version_num FROM alembic_version')).all() == [HEAD_REVISION]
    from pg16_stock_loss_headquarters_review_gate import run as headquarters_reviews
    result['headquartersReview'] = headquarters_reviews(engines,check_seals=check_review_seals)
    _assert_retained_loss(engines['star_oam_migrator'], migrate=migrate,
        label='retained-headquarters-downgrade', destination='20261126_0147',
        blocking_revision='20261205_0156' if check_review_seals else '20261127_0148')
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalars(text('SELECT version_num FROM alembic_version')).all() == [HEAD_REVISION]
    security()
    result.update(
        emptyRoundtrip=True, retainedLossBlocksDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,
        retainedRegionalReviewBlocksDowngrade=not check_review_seals, retainedHeadquartersReviewBlocksDowngrade=not check_review_seals,
        retainedReviewSealsBlockDowngrade=check_review_seals,
    )
    assert result["passed"] and result["submission"]["passed"]
    return result
