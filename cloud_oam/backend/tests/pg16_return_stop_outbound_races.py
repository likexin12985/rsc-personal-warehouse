"""Two actual API connections, observed database blocking, one stock winner."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from time import monotonic,sleep
from uuid import uuid4
import threading
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockLossDisposition,StockOperationLine,StockOperationOutbound
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.inventory_models import StockBalance
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn,StockReturnOutboundSubmitIn
from app.formal_services import stock_return_outbound_plan,stock_return_outbound_commands
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import inverse_posting,bound_commands


def exercise(context,command,root_id,*,winner):
    assert winner in ('stop','outbound')
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    with Session(api) as db:
        root=db.get(StockLossDisposition,root_id);operation_id=root.return_operation_id
        line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==operation_id)).one()
        amount=root.quantity if context['tracking']=='serial' else root.quantity/2
        pending_id,frozen_id=root.target_account_id,root.source_account_id
        pending_before=db.get(StockBalance,pending_id).quantity;frozen_before=db.get(StockBalance,frozen_id).quantity
        root_quantity=root.quantity
        transaction_count=db.scalar(text('SELECT count(*) FROM public.inventory_transactions'))
        actor=load_formal_principal(db,context['engineer_id'])
        request=StockReturnOutboundPreviewIn(operator_person_id=actor.person_id,outbound_at=datetime.now(timezone.utc),
            reason='Synthetic competing physical departure',lines=(dict(operation_line_id=line.id,quantity=amount,
                serial_verifications=context['request'].lines[0].serial_verifications),))
        prepared,_=stock_return_outbound_plan.preview_outbound(db,actor=actor,work_order_id=None,operation_id=operation_id,request=request)
        outbound=StockReturnOutboundSubmitIn(**request.model_dump(),expected_plan_hash=prepared.plan_hash,
            request_id=uuid4().hex,idempotency_key=uuid4().hex)
        db.commit()
    def execute(db,kind):
        if kind=='stop':
            result=inverse_posting.execute_unshipped_return_inverse(db,actor=load_formal_principal(db,context['admin_id']),request=command)
            bound_commands.register(db,kind='inverse',identifier=result['reversal_id'],client_key=command.idempotency_key)
            return result
        return stock_return_outbound_commands.execute_outbound(db,actor=load_formal_principal(db,context['engineer_id']),
            work_order_id=None,operation_id=operation_id,request=outbound)
    ready=threading.Event();info={};loser='outbound' if winner=='stop' else 'stop'
    def compete():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='30s'"))
            info['loser_pid']=db.scalar(text('SELECT pg_backend_pid()'));ready.set()
            try:
                execute(db,loser);db.commit()
                return dict(status='unexpected_commit')
            except InventoryReadError as error:
                db.rollback();return dict(status='rejected',code=error.code)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(api) as db:
            info['winner_pid']=db.scalar(text('SELECT pg_backend_pid()'))
            execute(db,winner)
            future=pool.submit(compete)
            try:
                assert ready.wait(5),'competitor did not connect'
                deadline=monotonic()+10;blocked=False
                with owner.connect() as observe:
                    while monotonic()<deadline and not future.done():
                        blockers=observe.scalar(text('SELECT pg_blocking_pids(:pid)'),dict(pid=info['loser_pid']))
                        if info['winner_pid'] in blockers:
                            blocked=True;break
                        sleep(.05)
                assert blocked,'second API connection never observed waiting on winner'
                db.commit()
            except BaseException:
                db.rollback();raise
        answer=future.result(timeout=35)
    expected='loss_return_stopped' if winner=='stop' else 'loss_reversal_requires_return_compensation'
    assert answer==dict(status='rejected',code=expected),answer
    with owner.connect() as db:
        stops=db.scalar(text('SELECT count(*) FROM public.stock_loss_return_stops WHERE root_disposition_id=:id'),dict(id=root_id))
        outbounds=db.scalar(text('SELECT count(*) FROM public.stock_operation_outbounds WHERE operation_id=:id'),dict(id=operation_id))
        inverses=db.scalar(text('SELECT count(*) FROM public.stock_loss_disposition_reversals WHERE root_disposition_id=:id'),dict(id=root_id))
        assert (stops,outbounds,inverses)==((1,0,1) if winner=='stop' else (0,1,0))
        pending_after=db.scalar(text('SELECT quantity FROM public.stock_balances WHERE stock_account_id=:id'),dict(id=pending_id))
        frozen_after=db.scalar(text('SELECT quantity FROM public.stock_balances WHERE stock_account_id=:id'),dict(id=frozen_id))
        assert pending_after==pending_before-(root_quantity if winner=='stop' else amount)
        assert frozen_after==frozen_before+(root_quantity if winner=='stop' else 0)
        assert db.scalar(text('SELECT count(*) FROM public.inventory_transactions'))==transaction_count+1
        target=db.scalar(text("SELECT m.to_account_id FROM public.inventory_movements m JOIN public.inventory_transactions t ON t.id=m.transaction_id ORDER BY t.ledger_cursor DESC LIMIT 1"))
        serials=[proof.serial_id for proof in context['request'].lines[0].serial_verifications]
        for identifier in serials:
            assert db.scalar(text('SELECT stock_account_id FROM public.serial_current_positions WHERE serial_id=:id'),dict(id=identifier))==target
    print('Observed native API race '+winner+' commits; '+loser+' rejected with '+expected,flush=True)
    return dict(passed=True,tracking=context['tracking'],winner=winner,loser=loser,loserCode=expected,
        databaseBlockingObserved=True,oneCommittedMovement=True,stopCount=stops,outboundCount=outbounds,
        inverseCount=inverses,exactBalanceDelta=True,exactSerialDestination=True,
        departureQuantity=format(amount,".3f"),partialDeparture=context["tracking"]=="quantity",formalMigrationInstalled=True,productionAcceptance=False)
