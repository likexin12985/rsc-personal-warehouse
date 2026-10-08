"""Real scrap/recovery services after formal Alembic 0168 on owned PG16.

No extra candidate DDL or grants are installed. Full current API admission and
real failed Alembic downgrades bound the business and retained-history proofs.
"""
from uuid import UUID, uuid4
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.database_security import validate_production_database_security
from app.formal_access import load_formal_principal
from app.stock_scrap_schemas import ScrapPreview, ScrapExecute
from app.formal_services import stock_scrap_plan
from app.formal_services.stock_scrap import execution, bound_commands
from pg16_scrap_raw_bindings import reject_unbound, privileges, immutable
from pg16_stock_loss_sources_gate import run as opening_and_sources
from pg16_loss_multigeneration_fixture import exercise
from pg16_scrap_formal_retention import refuse_history
from pg16_stock_scrap_structure_gate import original_columns, facts
from test_stock_scrap_plan import _upload

def release(engines, *, tracking, migrate, provision):
    migrate('upgrade-complete-0170', 'upgrade', '20261229_0180')
    provision()
    owner, api = (engines[name] for name in ('star_oam_migrator', 'star_oam_api'))
    validate_production_database_security(api, expected_runtime_role='star_oam_api',
        expected_migration_role='star_oam_migrator')
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261229_0180'
        from pg16_stock_operation_permission_policy import assert_fresh_defaults
        permission_proof = assert_fresh_defaults(db)
    transition_proof = dict(formalRevision='20261229_0180', defaultApiAdmission=True,
        formalPermissionDefaults=permission_proof)
    print('formal complete 0168 Alembic and default API admission PASS', flush=True)
    captured = {}

    def approved(context):
        captured['context'] = context
        captured['source'] = exercise(context, approved_disposition='scrap', after_approval=lambda source: source)
        return dict(passed=True, independentScrapApprovalCommitted=True)

    opening_and_sources(engines, tracking=tracking, after_preview=approved)
    print('actual API opening/report/independent scrap approval COMMIT PASS', flush=True)
    binding_privileges = privileges(engines)
    from pg16_scrap_seal_request_contract import run as seal_contract
    seal_request_contract = seal_contract(owner,api)
    with Session(api) as db:
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        actor = load_formal_principal(db, captured['context']['admin_id'])
        file = _upload(db, actor)
        preview = ScrapPreview(source=dict(kind='original', **captured['source']),
            execution_reason='Synthetic native approved scrap execution', evidence_file_ids=(file.id,))
        preparation = stock_scrap_plan.prepare(db, actor=actor, request=preview)
        command = ScrapExecute(**preview.model_dump(), expected_plan_hash=preparation.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
    from pg16_scrap_seal_sources import before_original, after_history
    original_seal_source = before_original(captured['context'], command)
    print('actual approved original seal source without fabricated root PASS', flush=True)
    from pg16_scrap_seal_persistence import before_original as close_original, after_history as close_history
    original_seal_persistence = close_original(captured['context'], command)
    transition_proof['sealOnlyRetention'] = refuse_history(engines, migrate=migrate, seal_only=True)
    print('actual root-free seal and unique audit COMMIT; late physical execution rolled back PASS', flush=True)
    with owner.connect() as db:
        columns = original_columns(db)
        before = facts(db, columns)
    rejected = []
    for case in ('actor_version', 'custody_ends'):
        reached = 'service'
        with Session(owner) as db:
            actor = load_formal_principal(db, captured['context']['admin_id'])
            try:
                bound_commands.original(db, actor=actor, request=command)
                if case == 'actor_version':
                    db.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'), dict(id=actor.user_id))
                else:
                    db.execute(text('UPDATE custody_assignments SET valid_to=clock_timestamp() WHERE id=:id'),
                        dict(id=UUID(preparation.document['custody_assignment_id'])))
                reached = 'commit'
                db.commit()
            except DBAPIError as error:
                assert reached == 'commit' and error.orig.sqlstate == '23514', (case, reached, str(error.orig))
                message = error.orig.diag.message_primary
                assert 'rsc_check_scrap_current_0165' in (error.orig.diag.context or ''), (case, str(error.orig))
                db.rollback()
                rejected.append(dict(case=case, sqlstate=error.orig.sqlstate, message=message, phase=reached))
            else:
                raise AssertionError('late scrap authority change committed: ' + case)
        with owner.connect() as db:
            assert facts(db, columns) == before, case + ': failed transaction changed retained facts'
    print('actual new scrap late version/custody COMMIT rejection and full rollback PASS', flush=True)
    binding_rejections = reject_unbound(captured['context'], actor_id=captured['context']['admin_id'],
        kind='original', command=command, service=execution.execute)
    from pg16_scrap_seal_races import commit_write
    result = commit_write(captured['context'], actor_id=captured['context']['admin_id'], command=command)
    print('actual new scrap API COMMIT PASS', flush=True)
    captured['context'].setdefault('scrap_lookup_cases', []).append((captured['context']['admin_id'], command, result))
    with owner.begin() as db:
        roots = db.execute(text("SELECT id FROM stock_loss_dispositions WHERE disposition='scrap'")).scalars().all()
        assert len(roots) == 1
        db.execute(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'), dict(id=roots[0]))
        assert db.scalar(text('SELECT count(*) FROM stock_scrap_lines')) == 1
        account = UUID(preparation.document['source_account_id'])
        assert db.scalar(text('SELECT quantity FROM stock_balances WHERE stock_account_id=:id'), dict(id=account)) == Decimal('0')
        assert Decimal(result['quantity']) == Decimal('1' if tracking == 'serial' else '0.250')
        for identifier in preparation.document['serial_ids']:
            state = db.execute(text('SELECT s.lifecycle_status,p.stock_account_id,p.last_movement_id FROM inventory_serials s '
                'JOIN serial_current_positions p ON p.serial_id=s.id WHERE s.id=:id'), dict(id=UUID(identifier))).one()
            assert tuple(state) == ('scrapped', None, UUID(result['posting_movement_id']))
            db.execute(text('SELECT public.rsc_check_serial_lifecycle_0092(:id)'), dict(id=UUID(identifier)))
    from pg16_scrap_business_recovery import exercise as recover
    recovered = recover(captured['context'], result)
    from pg16_scrap_business_generations import exercise as next_generation
    generations = next_generation(captured['context'], result, recovered['recoveryResult'])
    binding_immutability = immutable(engines)
    from pg16_scrap_seal_authority import exercise as check_seal_authority
    seal_authority = check_seal_authority(captured['context'])
    print('six actual seal source current read/write grants and custody separation PASS', flush=True)
    from pg16_scrap_seal_expiry import exercise as check_seal_expiry
    seal_expiry = check_seal_expiry(captured['context'])
    print('six real API seal transactions rejected at COMMIT after natural assignment expiry PASS', flush=True)
    seal_persistence = close_history(captured['context'])
    print('six actual seal kinds COMMIT; repeat/late-version/direct-write fences PASS', flush=True)
    from pg16_scrap_seal_lookup import create as compose_seals, read as read_seals
    seal_composition=compose_seals(captured['context'])
    print('six composed seal service API transactions COMMIT PASS',flush=True)
    from pg16_scrap_recovery_lookup import exercise as lookup_recovery
    lookup_results = lookup_recovery(captured['context'])
    from pg16_scrap_request_lookup import exercise as lookup_scrap
    scrap_lookup_results = lookup_scrap(captured['context'])
    from pg16_scrap_legacy_lookup import exercise as lookup_legacy
    legacy_lookup_results = lookup_legacy(captured['context'])
    seal_lookup=read_seals(captured['context'])
    print('all seals native READ ONLY; executed/sealed closure repeats without write grants PASS',flush=True)
    seal_sources = after_history(captured['context'])
    closed_stage_proofs=captured['context']['closed_stage_write_proofs']
    assert sorted(closed_stage_proofs)==['apply','correction','execute','headquarters','original','regional']
    races=captured['context']['seal_race_proofs']
    assert set(races)=={kind+':'+direction for kind in closed_stage_proofs for direction in ('seal_first','execute_first')}
    cross_registry_races=captured['context']['cross_registry_seal_races']
    assert set(cross_registry_races)=={'new_seal_first','legacy_approval_first'}
    revocation_races=captured['context']['seal_revocation_races']
    assert set(revocation_races)=={kind+':'+direction for kind in closed_stage_proofs for direction in ('revoke_first','seal_first')}
    transition_proof['businessHistoryRetention'] = refuse_history(engines, migrate=migrate)
    return dict(passed=True, tracking=tracking, formalMigration=transition_proof, originalScrapApiCommitVerified=True,
        originalScrapResult=result, **recovered, **generations,
        recoveryRequestLookup=lookup_results,
        scrapRequestLookup=scrap_lookup_results,
        legacyRequestLookup=legacy_lookup_results,
        originalSealSource=original_seal_source, historicalSealSources=seal_sources,
        sealCurrentAuthority=seal_authority,
        sealNaturalExpiry=seal_expiry,
        originalSealPersistence=original_seal_persistence, sealPersistence=seal_persistence,
        sealRequestContract=seal_request_contract,
        sealComposition=seal_composition,sealLookup=seal_lookup,
        closedStageWriteProofs=closed_stage_proofs,
        sealConcurrency=races,
        crossRegistrySealConcurrency=cross_registry_races,
        sealRevocationConcurrency=revocation_races,
        originalBindingRejections=binding_rejections, bindingPrivileges=binding_privileges,
        bindingImmutability=binding_immutability,
        lateAuthorityCommitRollbacks=rejected, exactFrozenShareRemoved=True, serialScrapProved=tracking == 'serial',
        candidateGrantsOnly=False, formalMigrationAndReadinessPending=False, productionAcceptance=False)
