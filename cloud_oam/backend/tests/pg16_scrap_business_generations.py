"""Second scrap/recovery generation through actual API writes on full PG16."""
import json
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.stock_operation_models import StockLossDisposition, StockOperationOrder
from app.stock_scrap_schemas import ScrapPreview, ScrapExecute
from app.formal_services import stock_scrap_plan
from app.formal_services.inventory_posting import _storage_hash
from app.formal_services.stock_loss_corrections import bound_commands as legacy, bound_recovery
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionApprove
from app.formal_services.stock_scrap import execution, bound_commands
from pg16_scrap_business_recovery import coordinates, inventory, exercise as recover
from pg16_stock_scrap_structure_gate import original_columns, facts
from test_stock_scrap_plan import _upload


def retained(owner):
    names = ('inventory_transactions', 'inventory_movements', 'inventory_movement_serials',
        'stock_operation_orders', 'stock_loss_dispositions', 'stock_loss_disposition_reversals',
        'stock_loss_correction_decisions', 'stock_loss_correction_executions', 'stock_loss_request_key_bindings',
        'stock_scrap_lines', 'stock_scrap_serials', 'stock_scrap_files', 'stock_scrap_recovery_requests',
        'stock_scrap_recovery_files', 'stock_scrap_recovery_regional_reviews',
        'stock_scrap_recovery_headquarters_reviews', 'stock_scrap_recovery_executions', 'stock_scrap_request_key_bindings')
    with owner.connect() as db:
        return {name: {json.dumps(row, sort_keys=True) for row in db.execute(text(
            'SELECT to_jsonb(t) FROM public.' + name + ' t')).scalars()} for name in names}


def exercise(context, original_scrap, first_recovery):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    historical = retained(owner)
    before_approval = inventory(owner)
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        root = db.get(StockLossDisposition, UUID(original_scrap['root_disposition_id']))
        order = db.get(StockOperationOrder, root.operation_id)
        source = dict(root_disposition_id=root.id, expected_root_request_hash=root.request_hash,
            expected_submission_plan_hash=order.plan_hash, reversal_id=first_recovery['reversal_id'],
            expected_reversal_hash=first_recovery['request_hash'])
        approval_command = CorrectionApprove(**source, disposition='scrap',
            reason='Synthetic independent reapproval after first recovery', **coordinates())
    with owner.connect() as db:
        columns = original_columns(db)
        before_reused = facts(db, columns)
    with Session(api) as db:
        phase = 'service'
        try:
            reused = approval_command.model_copy(update={
                'idempotency_key': context['scrap_recovery_lookup_cases'][0][1].idempotency_key})
            legacy.approve(db, actor=load_formal_principal(db, context['admin_id']), request=reused)
            phase = 'commit'
            db.commit()
        except DBAPIError as error:
            assert phase == 'commit' and error.orig.sqlstate == '23514', (phase, str(error.orig))
            assert 'raw request key reused across registries' in error.orig.diag.message_primary
            db.rollback()
        else:
            raise AssertionError('old registry reused new review raw key')
    with owner.connect() as db:
        assert facts(db, columns) == before_reused, 'cross-registry rejection changed retained facts'
    from pg16_scrap_cross_registry_races import approve_with_cross_registry_races
    approved = approve_with_cross_registry_races(context, approval_command)
    context['legacy_approval_key'] = approval_command.idempotency_key
    context['legacy_approval_lookup_case'] = (approval_command, approved)
    assert inventory(owner) == before_approval
    print('actual corrected scrap approval API COMMIT; inventory unchanged PASS', flush=True)
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        file = _upload(db, actor)
        preview = ScrapPreview(source=dict(kind='correction', **source,
            correction_decision_id=approved['correction_decision_id'], expected_correction_decision_hash=approved['request_hash']),
            execution_reason='Synthetic second approved scrap generation', evidence_file_ids=(file.id,))
        prepared = stock_scrap_plan.prepare(db, actor=actor, request=preview)
        command = ScrapExecute(**preview.model_dump(), expected_plan_hash=prepared.plan_hash, **coordinates())
    from pg16_scrap_closed_execution import before_stage
    before_stage(context, actor_id=context['admin_id'], command=command)
    with owner.connect() as db:
        columns = original_columns(db)
        before_execution = facts(db, columns)
    rejected = []
    for case in ('missing_binding', 'wrong_client_key', 'approval_key_reuse'):
        phase = 'service'
        with Session(api) as db:
            try:
                value = command.model_copy(update={'idempotency_key': approval_command.idempotency_key}) if case == 'approval_key_reuse' else command
                result = execution.execute(db, actor=load_formal_principal(db, context['admin_id']), request=value)
                if case != 'missing_binding':
                    phase = 'register'
                    key = coordinates()['idempotency_key'] if case == 'wrong_client_key' else value.idempotency_key
                    legacy.register(db, kind='correction', identifier=result['correction_execution_id'], client_key=key)
                phase = 'commit'
                db.commit()
            except DBAPIError as error:
                if case == 'approval_key_reuse':
                    assert phase == 'register' and error.orig.sqlstate == '23505', (case, str(error.orig))
                    assert error.orig.diag.constraint_name in {
                        'stock_loss_request_key_bindings_' + column + '_key'
                        for column in ('key_token', 'reversal_key_hash', 'approval_key_hash', 'correction_key_hash')}
                else:
                    expected = 'exact database-owned request binding required' if case == 'missing_binding' else 'client key does not prove stored action hashes'
                    assert error.orig.sqlstate == '23514' and expected in error.orig.diag.message_primary, (case, str(error.orig))
                    assert phase == ('commit' if case == 'missing_binding' else 'register')
                db.rollback()
                rejected.append(dict(case=case, phase=phase, sqlstate=error.orig.sqlstate))
            else:
                raise AssertionError('invalid corrected scrap binding accepted: ' + case)
        with owner.connect() as db:
            assert facts(db, columns) == before_execution, 'failed corrected scrap changed retained facts'
    print('corrected scrap missing/wrong/cross-action binding rejection and full rollback PASS', flush=True)
    from pg16_scrap_seal_races import commit_write
    result = commit_write(context, actor_id=context['admin_id'], command=command)
    assert result['source_kind'] == 'correction'
    context.setdefault('scrap_lookup_cases', []).append((context['admin_id'], command, result))
    with owner.begin() as db:
        row = db.execute(text('SELECT * FROM stock_loss_request_key_bindings WHERE fact_id=:id'),
            dict(id=UUID(result['correction_execution_id']))).mappings().one()
        assert row['scrap_key_hash'] == _storage_hash('stock-scrap:' + command.idempotency_key)
        assert row['recovery_key_hash'] is None
        for column, action in (('reversal_key_hash', 'reverse_loss'), ('approval_key_hash', 'approve_loss_correction'),
                ('correction_key_hash', 'correct_loss')):
            assert row[column] == _storage_hash('stock-loss:' + action + ':' + command.idempotency_key)
        assert db.scalar(text('SELECT quantity FROM stock_balances WHERE stock_account_id=:id'),
            dict(id=UUID(result['source_account_id']))) == 0
        db.execute(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'), dict(id=UUID(result['root_disposition_id'])))
        for value in prepared.document['serial_ids']:
            db.execute(text('SELECT public.rsc_check_serial_lifecycle_0092(:id)'), dict(id=UUID(value)))
    print('actual corrected scrap API COMMIT and full history PASS', flush=True)
    second = recover(context, result)
    after = retained(owner)
    assert all(rows <= after[name] for name, rows in historical.items()), 'second generation rewrote historical facts'
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        found = bound_recovery.lookup(db, actor=load_formal_principal(db, context['admin_id']), request=approval_command)
        assert found['request_state'] == 'found' and found['result'] == approved and found['retry_allowed'] is False
    with owner.begin() as db:
        proof = db.scalar(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'), dict(id=UUID(result['root_disposition_id'])))
        assert len(proof['verified_inverse_ids']) == 2 and len(proof['verified_correction_ids']) == 1
    print('two actual scrap/recovery generations; retained facts and approval lookup PASS', flush=True)
    return dict(correctedScrapApiCommitVerified=True, correctedScrapResult=result,
        correctedScrapBindingRejections=rejected, secondRecovery=second,
        completedScrapRecoveryGenerations=2, priorImmutableFactsRetained=True,
        legacyReuseOfNewReviewRejectedAtCommit=True,
        correctionApprovalReadOnlyLookupAfterSuccessors=True)
