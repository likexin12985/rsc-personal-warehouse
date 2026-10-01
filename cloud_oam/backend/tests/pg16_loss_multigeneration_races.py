"""Actual two-API-session later-inverse contention; neither writer retries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4
import time
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting
from app.formal_services.inventory_query import InventoryReadError
from app.inventory_models import InventoryTransaction, StockBalance
from app.stock_operation_models import StockLossDisposition
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossDispositionReversal as Inverse, StockLossCorrectionExecution as Correction,
)
from app.formal_services.stock_loss_corrections import (
    bound_commands as commands, bound_recovery as recovery,
    reversal_stock as reversal_stock, correction_stock as correction_stock,
)
from app.formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove, CorrectionPreview, CorrectionExecute,
)


def race(owner, api, *, actor_id, request, distinct):
    ready = Barrier(3)
    pids = []
    requests = (request, request.model_copy(update={'request_id': uuid4().hex,
        'idempotency_key': uuid4().hex}) if distinct else request)
    assert request.reversed_correction_id is not None
    with Session(owner) as db:
        selected = db.get(Correction, request.reversed_correction_id)
        root = db.get(StockLossDisposition, request.root_disposition_id)
        selected_id = selected.id
        selected_tx, selected_target, frozen = selected.posting_transaction_id, selected.target_account_id, root.source_account_id
        source_before, frozen_before = db.get(StockBalance, selected_target).quantity, db.get(StockBalance, frozen).quantity
        amount = root.quantity
        binding_before = db.scalar(text('SELECT count(*) FROM public.stock_loss_request_key_bindings'))

    def worker(index):
        command = requests[index]
        with Session(api) as db:
            db.execute(text("SET LOCAL lock_timeout='600s'"))
            db.execute(text("SET LOCAL statement_timeout='600s'"))
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            actor = load_formal_principal(db, actor_id)
            pids.append(pid)
            ready.wait(timeout=30)
            try:
                result = commands.inverse(db, actor=actor, request=command)
                db.commit()
                return dict(state='committed', result=result, index=index)
            except InventoryReadError as error:
                db.rollback()
                expected = ('loss_reversal_execution_not_current', 412) if distinct else ('loss_inverse_request_requires_recovery', 409)
                assert (error.code, error.status_code) == expected, (error.code, error.status_code)
                db.execute(text('SET TRANSACTION READ ONLY'))
                found = recovery.lookup(db, actor=load_formal_principal(db, actor_id), request=command)
                assert found['retry_allowed'] is False
                if distinct:
                    assert found['request_state'] == 'not_found' and found['result'] is None
                else:
                    assert found['request_state'] == 'found'
                return dict(state='rejected', result=found['result'], index=index, code=error.code)

    pool = ThreadPoolExecutor(max_workers=2)
    with Session(owner) as blocker:
        inventory_posting._lock_inventory_ledger_head_for_atomic_batch(blocker)
        blocker_pid = blocker.scalar(text('SELECT pg_backend_pid()'))
        futures = [pool.submit(worker, index) for index in range(2)]
        try:
            ready.wait(timeout=30)
            assert len(set(pids)) == 2
            deadline = time.monotonic() + 30
            while True:
                graph = dict(blocker.execute(text('SELECT pid,pg_blocking_pids(pid) '
                    'FROM unnest(CAST(:pids AS integer[])) AS waits(pid)'), {'pids': pids}).all())
                def blocked_by_parent(pid):
                    pending, seen = list(graph[pid]), set()
                    while pending:
                        current = pending.pop()
                        if current == blocker_pid:
                            return True
                        if current not in seen:
                            seen.add(current); pending.extend(graph.get(current, ()))
                    return False
                if all(blocked_by_parent(pid) for pid in pids):
                    print('both later inverse API writers blocked on real ledger head PASS', flush=True)
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError('writers did not contend on the real ledger head')
                time.sleep(0.02)
        finally:
            blocker.rollback()
            pool.shutdown(wait=True, cancel_futures=True)
    outcomes = [future.result(timeout=1) for future in futures]
    assert sorted(row['state'] for row in outcomes) == ['committed', 'rejected']
    winner = next(row for row in outcomes if row['state'] == 'committed')
    loser = next(row for row in outcomes if row['state'] == 'rejected')
    if not distinct:
        assert loser['result'] == winner['result']
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        found = recovery.lookup(db, actor=load_formal_principal(db, actor_id), request=requests[winner['index']])
        assert found['request_state'] == 'found' and found['result'] == winner['result']
    with Session(owner) as db:
        rows = tuple(db.scalars(select(Inverse).where(Inverse.reversed_correction_id == selected_id)))
        assert len(rows) == 1 and str(rows[0].id) == winner['result']['reversal_id']
        assert db.scalar(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.reversed_transaction_id == selected_tx)) == 1
        binding = db.execute(text('SELECT fact_id,binding_kind FROM public.stock_loss_request_key_bindings '
            'WHERE fact_id=:id'), {'id': rows[0].id}).one()
        assert tuple(binding) == (rows[0].id, 'inverse')
        assert db.scalar(text('SELECT count(*) FROM public.stock_loss_request_key_bindings')) == binding_before + 1
        assert db.get(StockBalance, frozen).quantity == frozen_before + amount
        assert db.get(StockBalance, selected_target).quantity == source_before - amount
    return dict(result=winner['result'], losingRequest=requests[loser['index']],
        evidence=dict(distinctRequests=distinct, apiConnections=2, bothBlockedByInventoryHead=True,
            commits=1, conflicts=1, executionAttemptsPerWriter=1, duplicateInverseFacts=0,
            duplicateReverseTransactions=0, duplicateBindings=0,
            exactReadRecovery=True, losingRequestAbsent=distinct, losingRetryAllowed=False))


def run(context, original):
    owner, api = (context['engines'][key] for key in ('star_oam_migrator', 'star_oam_api'))
    results = []
    for distinct in (False, True):
        with Session(api) as db:
            actor = load_formal_principal(db, context['admin_id'])
            selected = db.scalars(select(Correction).where(Correction.root_disposition_id == original['root_id'])
                .join(InventoryTransaction, InventoryTransaction.id == Correction.posting_transaction_id)
                .order_by(InventoryTransaction.ledger_cursor.desc())).first()
            preview = ReversalPreview(**original['binding'], reversed_correction_id=selected.id,
                expected_execution_request_hash=selected.request_hash)
            prepared = reversal_stock.prepare(db, actor=actor, request=preview)
            command = ReversalExecute(**preview.model_dump(), expected_plan_hash=prepared.plan_hash,
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
        result = race(owner, api, actor_id=context['admin_id'], request=command, distinct=distinct)
        results.append(result['evidence'])
        inverse_id = UUID(result['result']['reversal_id'])
        inverse_hash = result['result']['request_hash']
        if distinct:
            with Session(api) as db:
                sealed = commands.seal(db, actor=load_formal_principal(db, context['admin_id']), request=result['losingRequest'])
                db.commit()
            assert sealed['request_state'] == 'sealed' and sealed['retry_allowed'] is False
            break
        # A separate independent approval and posting establishes the next
        # active execution before testing two distinct concurrent requests.
        with Session(api) as db:
            actor = load_formal_principal(db, context['admin_id'])
            approve = CorrectionApprove(**original['binding'], reversal_id=inverse_id,
                expected_reversal_hash=inverse_hash, disposition='restore_available',
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
            approved = commands.approve(db, actor=actor, request=approve); db.commit()
            preview = CorrectionPreview(**original['binding'], reversal_id=inverse_id,
                expected_reversal_hash=inverse_hash, correction_decision_id=UUID(approved['correction_decision_id']),
                expected_correction_decision_hash=approved['request_hash'])
            prepared = correction_stock.prepare(db, actor=load_formal_principal(db, context['admin_id']), request=preview)
            command = CorrectionExecute(**preview.model_dump(), expected_plan_hash=prepared.plan_hash,
                request_id=uuid4().hex, idempotency_key=uuid4().hex)
            commands.correct(db, actor=load_formal_principal(db, context['admin_id']), request=command); db.commit()
    return dict(passed=True, tracking=context['tracking'], races=results,
        separateApprovalBetweenRaces=True, losingDistinctRequestPermanentlySealed=True,
        candidateOnly=True, productionAcceptance=False)
