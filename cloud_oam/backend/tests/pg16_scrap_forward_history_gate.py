"""Compose history patches on the full migrated catalog and real API facts.

This keeps new writes closed. Passing proves predecessor compatibility, not
new scrap/recovery COMMIT or the final 0165 migration/readiness contract.
"""
from pathlib import Path
import runpy
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.database_security import validate_production_database_security
from pg16_stock_loss_sources_gate import run as opening_and_sources
from pg16_loss_multigeneration_fixture import exercise
from pg16_loss_multigeneration_business import run as normal_generations
from pg16_stock_scrap_schema import compile_structure
from pg16_stock_scrap_structure_gate import original_columns, facts, functions, existing_foreign_keys

CLOUD = Path(__file__).resolve().parents[2]
FOLDER = CLOUD / 'backend/alembic/stock_scrap_0165'


def release(engines, *, tracking, migrate, provision, scenario='generations', components='all'):
    if components not in ('history', 'all') or scenario not in ('generations', 'seal_retention'):
        raise ValueError('explicit forward components and business scenario required')
    migrate('upgrade-complete-0164', 'upgrade', 'head')
    provision()
    owner, api = (engines[name] for name in ('star_oam_migrator', 'star_oam_api'))
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')
    captured = {}

    def original(context):
        captured.update(context=context, original=exercise(context))
        return dict(passed=True, originalApiPostingCommitted=True)

    baseline = opening_and_sources(engines, tracking=tracking, after_preview=original)
    module = runpy.run_path(str(FOLDER / 'forward_history.py'))
    modules = [module]
    if components == 'all':
        modules.extend(runpy.run_path(str(FOLDER / ('forward_' + name + '.py')))
            for name in ('business', 'serial', 'recovery', 'recovery_bindings', 'scrap_bindings'))
    stages = [(part, part['patches']() if 'patches' in part else [part['patch']()]) for part in modules]
    final, predecessors = {}, {}
    for _, patches in stages:
        for patch in patches:
            name = patch['before']['proname']
            if name in final:
                assert final[name]['definition'] == patch['before']['definition'], 'discontinuous forward replacement'
            predecessors.setdefault(name, patch['before'])
            final[name] = patch['after']
    _, tables, _, statements = compile_structure()
    private_rejected = []
    transition = {}

    def install():
        assert not transition, 'candidate installed more than once'
        with owner.connect() as db:
            columns = original_columns(db)
            before = facts(db, columns)
            old_functions = functions(db)
            old_fks = existing_foreign_keys(db, columns)
            old_triggers = db.execute(text("SELECT oid,pg_get_triggerdef(oid),tgenabled FROM pg_trigger ORDER BY oid")).all()
            retained_bindings = db.scalar(text('SELECT count(*) FROM stock_loss_request_key_bindings'))
        with owner.begin() as db:
            for statement in statements:
                db.execute(text(statement))
            for name in ('recovery_evidence.sql', 'recovery_approval.sql', 'recovery_authority.sql',
                         'recovery_admission.sql', 'inventory_edges.sql', 'historical_plans.sql', 'execution_evidence.sql'):
                db.execute(text((FOLDER / name).read_text()))
            # Every stage verifies its exact predecessor. An intermediate
            # dispatcher replacement is checked before the subsequent patch.
            for part, patches in stages:
                for patch in patches:
                    savepoint = db.begin_nested()
                    try:
                        db.execute(text('ALTER FUNCTION public.' + patch['before']['signature'] + ' SECURITY INVOKER'))
                        try:
                            part['install'](db)
                        except ValueError as error:
                            assert 'exact history function catalog mismatch' in str(error)
                        else:
                            raise AssertionError('altered predecessor was overwritten')
                    finally:
                        savepoint.rollback()
                part['install'](db)
            assert facts(db, columns) == before
            assert set(old_fks) <= set(existing_foreign_keys(db, columns))
            assert set(old_triggers) <= set(db.execute(text(
                'SELECT oid,pg_get_triggerdef(oid),tgenabled FROM pg_trigger ORDER BY oid')).all())
            actual = dict(functions(db))
            changed = {predecessors[name]['definition']: record['definition'] for name, record in final.items()}
            for oid, definition in old_functions:
                assert actual[oid] == changed.get(definition, definition), 'unexpected existing function change'
            assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
            if components == 'all':
                assert retained_bindings == (4 if scenario == 'seal_retention' else 3)
                assert db.scalar(text('SELECT count(*) FROM stock_loss_request_key_bindings WHERE recovery_key_hash IS NOT NULL')) == 0
                assert db.scalar(text('SELECT count(*) FROM stock_loss_request_key_bindings WHERE scrap_key_hash IS NOT NULL')) == 0
        signatures = ['rsc_check_scrap_history_0165']
        if components == 'all':
            signatures += ['rsc_check_scrap_current_0165', 'rsc_check_scrap_coordinates_0165']
        for signature in signatures:
            with api.connect() as db:
                try:
                    db.execute(text('SELECT public.' + signature + "('original',NULL::uuid)"))
                except DBAPIError as error:
                    assert error.orig.sqlstate == '42501'
                    db.rollback()
                    private_rejected.append(signature)
                else:
                    raise AssertionError('API invoked a private proof')
        transition.update(existingFunctionOidsPreserved=len(old_functions), oldTriggersPreserved=len(old_triggers),
            oldForeignKeysPreserved=len(old_fks), historicalRowsPreservedAtTransition=True,
            retainedLegacyBindingsAtTransition=retained_bindings,
            alteredPredecessorsRejected=sum(len(patches) for _, patches in stages))
        if components == 'all':
            captured['context']['scrap_candidate_request_lookup'] = True
        print('complete candidate installed; retained rows, aliases, OIDs, triggers, FKs and private ACL PASS', flush=True)

    def after_first_generation(generation, command, result):
        if generation == 0:
            install()

    if components == 'history':
        install()
    # Real API inverse/independent approval/correction, retained exact requests,
    # and late-authority rejection run with every old trigger still installed.
    business = normal_generations(captured['context'], captured['original'],
        seal_only=scenario == 'seal_retention',
        after_correction=after_first_generation if components == 'all' and scenario == 'generations' else None,
        after_seal=install if components == 'all' and scenario == 'seal_retention' else None)
    assert transition
    with owner.begin() as db:
        proof = db.scalar(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'),
            dict(id=captured['original']['root_id']))
        assert len(proof['verified_inverse_ids']) == business['rounds']
        assert len(proof['verified_correction_ids']) == business['rounds']
        for record in final.values():
            module['verify'](db, record)
        if components == 'all':
            assert db.scalar(text('SELECT count(*) FROM stock_loss_request_key_bindings WHERE recovery_key_hash IS NOT NULL')) == 0
            assert db.scalar(text('SELECT count(*) FROM stock_loss_request_key_bindings WHERE scrap_key_hash IS NOT NULL')) == 0
            assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_key_bindings')) == 0
            assert not db.scalar(text("SELECT has_table_privilege('star_oam_api','public.stock_scrap_request_key_bindings','INSERT')"))
        for table in tables:
            assert db.scalar(text('SELECT count(*) FROM public.' + table.name)) == 0
            assert not db.scalar(text("SELECT has_table_privilege('star_oam_api',:table,'INSERT')"),
                dict(table='public.' + table.name))
    return dict(passed=True, tracking=tracking, full0164Migrated=True,
        originalApiPosting=baseline['submission'], existingApiGenerations=business,
        historyProof=proof, forwardComponents=components, changedFunctions=len(final), **transition,
        legacyRoundsAfterUpgrade=business['rounds'] - (1 if components == 'all' else 0),
        privateApiCallsRejected=private_rejected,
        newTablesEmptyAndInsertDenied=True, newScrapApiCommitVerified=False,
        preUpgradeSealRetained=components == 'all' and scenario == 'seal_retention',
        finalMigrationAndReadinessPending=True, productionAcceptance=False)
