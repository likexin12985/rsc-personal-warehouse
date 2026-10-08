"""Owned PG16 transition proofs; not an Alembic/runtime activation claim."""
from pathlib import Path
import runpy

from sqlalchemy import text

from pg16_stock_scrap_structure_gate import original_columns, facts

FOLDER = Path(__file__).resolve().parents[1] / 'alembic/stock_scrap_0165'
TRANSITION = runpy.run_path(str(FOLDER / 'transition.py'))
DATA = TRANSITION['DATA']
probe = TRANSITION['probe']


def catalog(owner):
    with owner.connect() as db:
        return probe.snapshot(db)


def runtime_admission(engines):
    from app.database_security import validate_production_database_security
    validate_production_database_security(engines['star_oam_api'],
        expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')


def rejected(owner, *, mutation, operation, message):
    before = catalog(owner)
    try:
        with owner.begin() as db:
            if mutation:
                db.execute(text(mutation))
            operation(db)
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError('unsafe transition unexpectedly succeeded')
    assert catalog(owner) == before, 'rejected migration changed retained catalog'


def empty_roundtrip(engines):
    owner = engines['star_oam_migrator']
    before = catalog(owner)
    function = next(row['before'] for row in DATA['functions'].values() if row['before'])
    signature = TRANSITION['function_name'](function)
    cases = [
        ('predecessor_acl', 'GRANT EXECUTE ON FUNCTION ' + signature + ' TO PUBLIC', 'before functions catalog mismatch'),
        ('predecessor_body', function['definition'].replace(function['prosrc'], function['prosrc'] + '\n'), 'before functions catalog mismatch'),
        ('wrong_revision', "UPDATE alembic_version SET version_num='incorrect_predecessor'", 'exact single predecessor'),
    ]
    for _, mutation, message in cases:
        rejected(owner, mutation=mutation, operation=TRANSITION['install'], message=message)
    with owner.begin() as db:
        TRANSITION['install'](db)
    after = catalog(owner)
    changed_function = next(row['after'] for row in DATA['functions'].values() if row['before'] is None)
    public_function = TRANSITION['function_name'](changed_function)
    seal = 'public.stock_scrap_request_seals'
    cases_after = [
        ('new_function_acl', 'GRANT EXECUTE ON FUNCTION ' + public_function + ' TO PUBLIC', 'after functions catalog mismatch'),
        ('registry_write_acl', 'GRANT INSERT ON public.stock_scrap_request_key_bindings TO star_oam_api', 'after tables catalog mismatch'),
        ('disabled_seal_guard', 'ALTER TABLE ' + seal + ' DISABLE TRIGGER trg_scrap_seal_immutable_0165', 'after tables catalog mismatch'),
        ('extra_column', 'ALTER TABLE ' + seal + ' ADD COLUMN unexpected text', 'after tables catalog mismatch'),
    ]
    for _, mutation, message in cases_after:
        rejected(owner, mutation=mutation, operation=TRANSITION['remove_empty'], message=message)
    with owner.begin() as db:
        TRANSITION['remove_empty'](db)
    assert catalog(owner) == before, 'committed empty downgrade did not restore predecessor'
    runtime_admission(engines)
    with owner.begin() as db:
        TRANSITION['install'](db)
    assert catalog(owner) == after, 'reupgrade differs from first upgrade'
    with owner.begin() as db:
        TRANSITION['remove_empty'](db)
    assert catalog(owner) == before
    runtime_admission(engines)
    return dict(passed=True, emptyUpgradeDowngradeReupgrade=True, predecessorRestored=True,
        postDowngradeRuntimeAdmissionChecks=2,
        rejectedCatalogMutations=[case[0] for case in cases + cases_after],
        catalogSha256=TRANSITION['CATALOG_SHA'], alembicAdvanced=False)


def install_preserving_history(owner):
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        functions = db.execute(text("SELECT oid,proname FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY oid")).all()
    assert before['stock_loss_headquarters_reviews'], 'genuine predecessor approval required'
    with owner.begin() as db:
        TRANSITION['install'](db)
    with owner.connect() as db:
        assert facts(db, columns) == before, 'upgrade changed old facts'
        current = set(db.execute(text("SELECT oid,proname FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY oid")).all())
        assert set(functions) <= current, 'upgrade recreated a predecessor function'
    return dict(oldColumnFactsUnchanged=True, oldFunctionOidsRetained=len(functions),
        predecessorFactRows=sum(len(rows) for rows in before.values()),
        predecessorApprovalRows=len(before['stock_loss_headquarters_reviews']))


def refuse_history(owner, *, seal_only=False):
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
        version = db.scalar(text('SELECT version_num FROM alembic_version'))
        assert before['stock_scrap_request_seals'], 'real committed seal required'
        if seal_only:
            assert not before['stock_scrap_lines'] and not before['stock_scrap_recovery_executions']
        else:
            assert before['stock_scrap_lines'] and before['stock_scrap_recovery_executions']
            assert before['stock_loss_request_key_bindings'], 'legacy aliases required'
    rejected(owner, mutation=None, operation=TRANSITION['remove_empty'], message='immutable history requires retention')
    with owner.connect() as db:
        assert facts(db, columns) == before, 'refused downgrade changed business history'
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == version
    return dict(downgradeRejected=True, allFactsUnchanged=True, revisionUnchanged=True,
        sealOnly=seal_only, retainedSeals=len(before['stock_scrap_request_seals']),
        retainedLegacyBindings=len(before['stock_loss_request_key_bindings']),
        retainedScrapLines=len(before['stock_scrap_lines']),
        retainedRecoveryExecutions=len(before['stock_scrap_recovery_executions']))
