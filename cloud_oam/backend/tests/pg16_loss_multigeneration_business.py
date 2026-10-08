"""Three native account correction rounds, permanent binding and seal checks."""
from uuid import UUID, uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.inventory_models import InventoryTransaction, StockBalance, SerialCurrentPosition
from app.stock_operation_models import StockLossDisposition
from app.models import User
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_disposition_recovery as original_recovery
from app.formal_services.stock_loss_corrections import (
    bound_commands as commands, bound_recovery as recovery,
    reversal_stock as reversal_stock, correction_stock as correction_stock,
    inverse_posting as inverse_posting,
)
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossDispositionReversal as Inverse, StockLossCorrectionExecution as Correction,
)
from app.formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove, CorrectionPreview, CorrectionExecute,
)
from pg16_loss_correction_gate import stock_snapshot


def readonly(api, context, request):
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
        actor = load_formal_principal(db, context['admin_id'])
        reader = recovery
        if context.get('scrap_candidate_request_lookup'):
            from app.formal_services.stock_scrap import legacy_request_lookup as reader
        result = reader.lookup(db, actor=actor, request=request)
        assert not db.new and not db.dirty and not db.deleted
        return result


def stock_only(owner):
    return {key: value for key, value in stock_snapshot(owner).items() if key != 'audit_chain_heads'}


def run(context, original, *, seal_only=False, after_correction=None, after_seal=None):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    requests = []
    with owner.connect() as db:
        # A continuation may install a forward schema between generations.
        # Compare every retained old column, without treating newly appended
        # nullable columns as a rewrite of the original business fact.
        original_columns = db.execute(text("SELECT quote_ident(attname) FROM pg_attribute "
            "WHERE attrelid='public.stock_loss_dispositions'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum")).scalars().all()
        original_query = text('SELECT ' + ','.join(original_columns) + ' FROM public.stock_loss_dispositions ORDER BY id')
        originals = db.execute(original_query).all()
    with Session(api) as db:
        root = db.get(StockLossDisposition, original['root_id'])
        selected_id, selected_hash = root.id, root.request_hash
        frozen_id, amount = root.source_account_id, root.quantity
    kinds = ('convert_used',) if seal_only else ('convert_used', 'convert_damaged', 'restore_available')
    for generation, kind in enumerate(kinds):
        with Session(api) as db:
            actor = load_formal_principal(db, context['admin_id'])
            selection = ReversalPreview(**original['binding'],
                reversed_correction_id=selected_id if generation else None,
                expected_execution_request_hash=selected_hash)
            prepared = reversal_stock.prepare(db, actor=actor, request=selection)
            command = ReversalExecute(**selection.model_dump(), expected_plan_hash=prepared.plan_hash,
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
            selected = db.get(Correction if generation else StockLossDisposition, selected_id)
            selected_tx, selected_movement, selected_target = selected.posting_transaction_id, selected.posting_movement_id, selected.target_account_id
            frozen_before = db.get(StockBalance, frozen_id).quantity
            target_before = db.get(StockBalance, selected_target).quantity
        if generation == 1:
            before = stock_snapshot(owner)
            with Session(owner) as db:
                commands.inverse(db, actor=load_formal_principal(db, context['admin_id']), request=command)
                db.get(User, context['admin_id']).authorization_version += 1
                try:
                    db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate == '23514'; db.rollback()
                else:
                    raise AssertionError('late authority change committed a later inverse')
            assert stock_snapshot(owner) == before
            print('later inverse late authority COMMIT rollback PASS', flush=True)
        with Session(api) as db:
            result = commands.inverse(db, actor=load_formal_principal(db, context['admin_id']), request=command)
            db.commit()
            inverse = db.get(Inverse, UUID(result['reversal_id']))
            inverse_id, inverse_hash = inverse.id, inverse.request_hash
            assert inverse.original_transaction_id == selected_tx and inverse.original_movement_id == selected_movement
            assert inverse.reversed_correction_id == (selected_id if generation else None)
            assert db.get(InventoryTransaction, inverse.posting_transaction_id).reversed_transaction_id == selected_tx
            assert db.get(StockBalance, frozen_id).quantity == frozen_before + amount
            assert db.get(StockBalance, selected_target).quantity == target_before - amount
        requests.append((command, result))
        answer = readonly(api, context, command)
        assert answer['request_state'] == 'found' and answer['retry_allowed'] is False and answer['result'] == result
        print('native generation ' + str(generation + 1) + ' inverse COMMIT and bound recovery PASS', flush=True)
        before = stock_only(owner)
        with Session(api) as db:
            approve = CorrectionApprove(**original['binding'], reversal_id=inverse_id,
                expected_reversal_hash=inverse_hash, disposition=kind,
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
            result = commands.approve(db, actor=load_formal_principal(db, context['admin_id']), request=approve)
            db.commit()
        # A decision fact is allowed; ledger, balances and SN must not change.
        after = stock_only(owner)
        assert all(after[key] == before[key] for key in ('inventory_transactions', 'inventory_movements',
            'stock_balances', 'serial_current_positions', 'stock_loss_disposition_reversals', 'stock_loss_correction_executions'))
        requests.append((approve, result))
        assert readonly(api, context, approve)['result'] == result
        with Session(api) as db:
            selection = CorrectionPreview(**original['binding'], reversal_id=inverse_id,
                expected_reversal_hash=inverse_hash, correction_decision_id=UUID(result['correction_decision_id']),
                expected_correction_decision_hash=result['request_hash'])
            actor = load_formal_principal(db, context['admin_id'])
            prepared = correction_stock.prepare(db, actor=actor, request=selection)
            command = CorrectionExecute(**selection.model_dump(), expected_plan_hash=prepared.plan_hash,
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
            result = commands.correct(db, actor=actor, request=command); db.commit()
            selected = db.get(Correction, UUID(result['correction_execution_id']))
            selected_id, selected_hash = selected.id, selected.request_hash
            assert db.get(StockBalance, frozen_id).quantity == frozen_before
            for serial in prepared.document['serial_ids']:
                position = db.get(SerialCurrentPosition, UUID(serial))
                assert position.stock_account_id == selected.target_account_id
                assert position.last_movement_id == selected.posting_movement_id
        requests.append((command, result))
        assert readonly(api, context, command)['result'] == result
        if after_correction is not None:
            after_correction(generation, command, result)
        with owner.connect() as db:
            assert db.execute(original_query).all() == originals
        print('native generation ' + str(generation + 1) + ' approval/correction COMMIT and bound recovery PASS', flush=True)
    before = stock_snapshot(owner)
    for command, expected in requests:
        found = readonly(api, context, command)
        assert found['request_state'] == 'found' and found['retry_allowed'] is False and found['result'] == expected
    # Recovery of the original disposition uses its complete retained command.
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor = load_formal_principal(db, context['admin_id'])
        found = original_recovery.lookup_disposition_request(db, actor=actor,
            request=original['original_command'], flow='disposition')
        assert found == dict(lookup_status='found', retry_permitted=False, disposition=original['original_result'])
    assert stock_snapshot(owner) == before
    with owner.connect() as db:
        graph = db.scalar(text('SELECT public.rsc_check_loss_history_graph_0159(:id)'), {'id': original['root_id']})
        assert len(graph['verified_inverse_ids']) == len(kinds) and len(graph['verified_correction_ids']) == len(kinds)
        assert db.scalar(text('SELECT count(*) FROM public.stock_loss_request_key_bindings')) == 3 * len(kinds)
    print(f'all {len(requests)} native historical bound requests and original outcome PASS', flush=True)
    with Session(api) as db:
        actor = load_formal_principal(db, context['admin_id'])
        selection = ReversalPreview(**original['binding'], reversed_correction_id=selected_id,
            expected_execution_request_hash=selected_hash)
        prepared = reversal_stock.prepare(db, actor=actor, request=selection)
        command = ReversalExecute(**selection.model_dump(), expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
    stable = stock_only(owner)
    with Session(api) as db:
        sealed = commands.seal(db, actor=load_formal_principal(db, context['admin_id']), request=command); db.commit()
    assert sealed['request_state'] == 'sealed' and sealed['retry_allowed'] is False
    if after_seal is not None:
        after_seal()
        current = stock_only(owner)
        assert current.keys() == stable.keys()
        for table, rows in stable.items():
            assert len(rows) == len(current[table])
            for before_row, after_row in zip(rows, current[table]):
                assert before_row.keys() <= after_row.keys()
                assert {k: after_row[k] for k in before_row} == before_row
    else:
        assert stock_only(owner) == stable
    assert readonly(api, context, command) == sealed
    before = stock_snapshot(owner)
    with Session(api) as db:
        try:
            commands.inverse(db, actor=load_formal_principal(db, context['admin_id']), request=command)
        except InventoryReadError as error:
            assert error.code == 'loss_inverse_request_sealed'; db.rollback()
        else:
            raise AssertionError('service allowed a sealed later inverse')
    assert stock_snapshot(owner) == before
    # Call the low-level service without the service seal check. The database
    # must still reject; no trigger or constraint is disabled for this test.
    with Session(api) as db:
        try:
            result = inverse_posting.execute_account_inverse(db, actor=load_formal_principal(db, context['admin_id']), request=command)
            commands.register(db, kind='inverse', identifier=result['reversal_id'], client_key=command.idempotency_key)
            db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate in ('23514', '23505'); db.rollback()
        else:
            raise AssertionError('database allowed a sealed later inverse')
    assert stock_snapshot(owner) == before and readonly(api, context, command) == sealed
    print('native later inverse permanent seal and late write rollback PASS', flush=True)
    if seal_only:
        with owner.connect() as db:
            assert db.scalar(text('SELECT count(*) FROM public.stock_loss_disposition_reversals WHERE reversed_correction_id IS NOT NULL')) == 0
            assert db.scalar(text("SELECT count(*) FROM public.stock_loss_inverse_request_seals WHERE command_jsonb->'intent'->>'reversed_correction_id' IS NOT NULL")) == 1
    return dict(passed=True, tracking=context['tracking'], rounds=len(kinds), historicalRequests=len(requests),
        originalDispositionUnchanged=True, databaseOwnedKeyBindings=True,
        apiRoleCommits=True, nativeReadOnlyRecovery=True, lateAuthorityRollback=not seal_only,
        permanentLaterInverseSeal=True, lateWriteRollback=True,
        laterSealWithoutLaterInverse=seal_only, productionAcceptance=False)
