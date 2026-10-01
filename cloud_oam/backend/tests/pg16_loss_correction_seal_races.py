"""Native API concurrency checks to compose after the first overlay gate.

No database target creation and no external writes. The caller supplies the
same owned fixture engines and an exact pending approval/execution command.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import bound_commands
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionApprove
from app.stock_loss_correction_models import StockLossCorrectionApprovalSeal, StockLossCorrectionExecutionSeal
from pg16_loss_correction_recovery import readonly


def run(owner, api, context, original):
    from pg16_loss_correction_seal_business import complete_snapshot
    model = StockLossCorrectionApprovalSeal if type(original) is CorrectionApprove else StockLossCorrectionExecutionSeal
    results = []
    for variant in ('identical', 'same_key_different_request', 'same_request_different_key'):
        command = original.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
        other = command if variant == 'identical' else command.model_copy(update={
            'request_id' if variant == 'same_key_different_request' else 'idempotency_key': uuid4().hex})
        before = complete_snapshot(owner)
        barrier = Barrier(2)

        def close(request):
            with Session(api) as db:
                db.execute(text("SET LOCAL lock_timeout='20s'"))
                actor = load_formal_principal(db, context['admin_id'])
                barrier.wait(timeout=30)
                try:
                    result = bound_commands.seal(db, actor=actor, request=request)
                    db.commit()
                    return dict(state='sealed', result=result, request=request)
                except InventoryReadError as error:
                    db.rollback()
                    assert error.status_code == 409, str(error)
                    return dict(state='conflict', request=request)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(close, request) for request in (command, other)]
            attempts = [future.result(timeout=90) for future in futures]
        winners = [result for result in attempts if result['state'] == 'sealed']
        if variant == 'identical':
            assert len(winners) == 2 and winners[0]['result'] == winners[1]['result']
        else:
            assert len(winners) == 1 and sum(result['state'] == 'conflict' for result in attempts) == 1
        winner = winners[0]
        assert readonly(api, context, winner['request']) == winner['result']
        with owner.connect() as db:
            rows = db.execute(select(model.id).where(model.request_id.in_((command.request_id, other.request_id)))).all()
            assert len(rows) == 1
        after = complete_snapshot(owner)
        for table in before:
            expected_delta = 1 if table in (model.__tablename__, 'stock_loss_request_key_bindings', 'audit_events') else 0
            if table == 'audit_chain_heads':
                continue
            if expected_delta:
                assert len(after[table]) == len(before[table]) + 1, table
            else:
                assert after[table] == before[table], table
        results.append(dict(variant=variant,committedSealFacts=1,stockUnchanged=True,
            originalRequestRecovered=True,successfulResponses=len(winners)))
    return results
