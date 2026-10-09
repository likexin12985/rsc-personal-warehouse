"""Real pre-0165 history upgrade; no old requests are replayed as writes."""
import json
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database_security import validate_production_database_security
from app.formal_access import load_formal_principal
from app.stock_loss_schemas import StockLossDispositionExecuteIn
from app.formal_services import stock_loss_disposition_recovery as original_recovery
from app.formal_services.stock_loss_corrections import bound_recovery
from app.formal_services.stock_loss_corrections.request_contracts import ReversalExecute, CorrectionApprove, CorrectionExecute
from pg16_stock_scrap_structure_gate import original_columns, facts, functions
from pg16_stock_operation_permission_policy import assert_authorization_transition


def read_all(api, history):
    types = {cls.__name__: cls for cls in (StockLossDispositionExecuteIn, ReversalExecute, CorrectionApprove, CorrectionExecute)}
    for row in history['committedCommands']:
        request = types[row['commandType']].model_validate(row['command'])
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            actor = load_formal_principal(db, row['actor'])
            outcome = (original_recovery.lookup_disposition_request(db, actor=actor, request=request, flow='disposition')
                       if row['kind'] == 'original' else bound_recovery.lookup(db, actor=actor, request=request))
            assert json.loads(json.dumps(outcome, default=str, sort_keys=True)) == row['lookup'], row['kind']
            assert not db.new and not db.dirty and not db.deleted
    return len(history['committedCommands'])


def release(engines, *, tracking, migrate, provision, historical_admission, populate_history,
            historical_baseline_admission=None):
    migrate('predecessor-0164', 'upgrade', '20261213_0164')
    provision()
    old_baseline = historical_baseline_admission() if historical_baseline_admission else None
    predecessor = populate_history()
    history = predecessor['history']
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.connect() as db:
        columns = original_columns(db)
        # The version row is the explicit migration result, not a retained
        # business fact. Every column of every other old table is compared.
        del columns['alembic_version']
        before = facts(db, columns)
        old_function_oids = {row[0] for row in functions(db)}
    assert history['factCounts']['stock_loss_disposition_reversals'] == 3
    assert history['factCounts']['stock_loss_correction_executions'] == 3
    assert history['factCounts']['stock_loss_request_key_bindings'] == 10
    migrate('upgrade-populated-0166', 'upgrade', '20261215_0166')
    with owner.connect() as db:
        after_policy = facts(db, columns)
    permission_delta = assert_authorization_transition(before, after_policy)
    # Check the independent additive deltas separately: the new correction
    # permissions must never mask a changed old fact or a widened scrap grant.
    before_condition = after_policy
    migrate('upgrade-populated-0167', 'upgrade', '20261216_0167')
    with owner.connect() as db:
        after_policy = facts(db, columns)
    condition_permission_delta = assert_authorization_transition(before_condition, after_policy, revision='0167')
    # 0168 changes only a derived shipment axis; this old loss fixture has no
    # shipments, so all retained rows and additive permission data must match.
    migrate('upgrade-populated-0168', 'upgrade', '20261217_0168')
    with owner.connect() as db:
        assert facts(db, columns) == after_policy
    before_closure = after_policy
    migrate('upgrade-populated-0169', 'upgrade', '20261218_0169')
    with owner.connect() as db:
        after_policy = facts(db, columns)
    closure_permission_delta = assert_authorization_transition(before_closure, after_policy, revision='0169')
    migrate('upgrade-populated-0170', 'upgrade', '20261219_0170')
    with owner.connect() as db:
        assert facts(db, columns) == after_policy
    migrate('upgrade-populated-0171', 'upgrade', '20261230_0181')
    with owner.connect() as db:
        assert facts(db, columns) == after_policy

    def current():
        validate_production_database_security(api, expected_runtime_role='star_oam_api',
                                             expected_migration_role='star_oam_migrator')
        with owner.connect() as db:
            assert facts(db, columns) == after_policy
            assert old_function_oids <= {row[0] for row in functions(db)}
            all_columns = original_columns(db)
            snapshot = facts(db, all_columns)
        count = read_all(api, history)
        with owner.connect() as db:
            assert facts(db, all_columns) == snapshot, 'read-only request recovery changed database facts'
        return count
    reads = current()
    print('real old 0164 inverse/correction/key/seal facts unchanged; eleven original request lookups PASS', flush=True)
    # Only OLD facts exist: no new 0165 table or alias may obstruct legitimate
    # rollback. New scrap fact retention is checked by the full business gate.
    migrate('downgrade-old-history-0164', 'downgrade', '20261213_0164')
    old_runtime = historical_admission()
    with owner.connect() as db:
        assert facts(db, columns) == after_policy
    migrate('reupgrade-old-history-0171', 'upgrade', '20261230_0181')
    assert current() == reads
    return dict(passed=True, tracking=tracking, scope='formal-populated-predecessor-upgrade', migrationHead='20261230_0181',
        oldFactCounts=history['factCounts'], retainedRows=sum(map(len, before.values())),
        retainedFunctionOids=len(old_function_oids), exactOldRequestsFound=reads,
        predecessorSource=predecessor['source'], predecessorRequestsSha256=predecessor['outputSha256'],
        closurePermissionMigration=closure_permission_delta, permissionMigration=permission_delta, conditionPermissionMigration=condition_permission_delta,
        authorizationDataRetainedOnDowngrade=True,
        fullReadOnlySnapshotUnchanged=True, formalOldHistoryRoundtrip=True,
        oldApplicationAfterPopulatedDowngrade=old_runtime,
        oldApplicationBeforeUpgrade=old_baseline,
        newScrapBusinessRerun=False, productionAcceptance=False)
