"""Full API startup after downgrade plus live schema/column ACL negatives."""
from sqlalchemy import text

from app.database_security import _loss_correction_catalog
from pg16_scrap_transition_gate import empty_roundtrip, runtime_admission, catalog


def release(engines, *, tracking, migrate, provision):
    migrate('predecessor-0164', 'upgrade', '20261213_0164')
    provision()
    runtime_admission(engines)
    proof = empty_roundtrip(engines)
    print('two committed empty downgrades followed by full API startup admission PASS', flush=True)
    owner = engines['star_oam_migrator']
    with owner.connect() as db:
        dropped = dict(db.execute(text("SELECT c.relname,count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "WHERE c.relnamespace='public'::regnamespace AND a.attisdropped GROUP BY c.relname ORDER BY c.relname")).all())
    assert dropped == dict(stock_loss_correction_executions=2, stock_loss_disposition_reversals=6,
        stock_loss_dispositions=2, stock_loss_request_key_bindings=4, stock_operation_orders=2), dropped
    before = catalog(owner)
    rejected = []
    cases = (
        ('missing_required_column', 'ALTER TABLE stock_loss_request_key_bindings DROP COLUMN created_at',
            '0159 exact table catalog mismatch'),
        ('unexpected_visible_column', 'ALTER TABLE stock_loss_request_key_bindings ADD COLUMN unexpected text',
            '0159 exact table catalog mismatch'),
        ('column_acl', 'GRANT SELECT(created_at) ON stock_loss_request_key_bindings TO star_oam_api',
            '0159 table security metadata mismatch'),
    )
    for name, mutation, message in cases:
        # The exact verifier reads uncommitted DDL in its owner transaction.
        # A second API connection would wait on that DDL lock. After rollback,
        # run the complete startup boundary through the real API role again.
        with owner.connect() as db:
            db.execute(text(mutation))
            try:
                _loss_correction_catalog.verify(db)
            except ValueError as error:
                assert message in str(error), (name, str(error))
                rejected.append(name)
            else:
                raise AssertionError('startup catalog accepted invalid schema: ' + name)
            finally:
                db.rollback()
        assert catalog(owner) == before
        runtime_admission(engines)
    print('missing/extra visible columns and explicit column ACL still rejected; API startup restored PASS', flush=True)
    return dict(passed=True, scope='transition-runtime-readiness', frozenTransition=proof,
        canonicalDroppedSlots=dropped, runtimeCatalogRejections=rejected,
        postDowngradeRuntimeAdmission=True, formalRevisionActivated=False,
        fullBusinessRerun=False, productionAcceptance=False)
