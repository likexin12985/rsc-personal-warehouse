"""Verify actual Alembic refuses to remove committed 0165 business history."""
from sqlalchemy import text

from app.database_security import validate_production_database_security
from pg16_scrap_transition_gate import catalog
from pg16_stock_scrap_structure_gate import original_columns, facts, functions


def refuse_history(engines, *, migrate, seal_only=False):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        function_definitions = functions(db)
        version = db.scalar(text('SELECT version_num FROM alembic_version'))
        assert version == '20261230_0181'
        assert before['stock_scrap_request_seals'], 'real committed seal required'
        if seal_only:
            assert not before['stock_scrap_lines'] and not before['stock_scrap_recovery_executions']
        else:
            assert before['stock_scrap_lines'] and before['stock_scrap_recovery_executions']
            assert before['stock_loss_request_key_bindings'], 'legacy aliases required'
    before_catalog = catalog(owner)
    label = 'reject-seal-only-downgrade' if seal_only else 'reject-business-history-downgrade'
    migrate(label, 'downgrade', '20261213_0164', expected='immutable history requires retention')
    assert catalog(owner) == before_catalog, 'refused Alembic downgrade changed catalog'
    with owner.connect() as db:
        assert original_columns(db) == columns, 'refused downgrade changed visible columns'
        assert facts(db, columns) == before, 'refused downgrade changed business history'
        assert functions(db) == function_definitions, 'refused downgrade changed function body or OID'
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == version
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
                                         expected_migration_role='star_oam_migrator')
    return dict(formalAlembicDowngradeRejected=True, allFactsUnchanged=True,
        catalogAndFunctionsUnchanged=True, revisionUnchanged=True, defaultApiAdmissionAfterFailure=True,
        sealOnly=seal_only, retainedSeals=len(before['stock_scrap_request_seals']),
        retainedLegacyBindings=len(before['stock_loss_request_key_bindings']),
        retainedScrapLines=len(before['stock_scrap_lines']),
        retainedRecoveryExecutions=len(before['stock_scrap_recovery_executions']))
