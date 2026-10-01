"""Observe two API connections racing permanent closure against execution."""
from concurrent.futures import ThreadPoolExecutor
from time import monotonic,sleep
import threading
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import bound_commands,sealed_inverse
from pg16_return_stop_seals import snapshot


def exercise(context,command,original,original_result,root_id,*,winner):
    assert winner in ('seal','execute')
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    before=snapshot(owner);ready=threading.Event();info={}
    loser='execute' if winner=='seal' else 'seal'
    def invoke(db,kind):
        function=bound_commands.seal_return_inverse if kind=='seal' else bound_commands.return_inverse
        return function(db,actor=load_formal_principal(db,context['admin_id']),request=command)
    def compete():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='30s'"))
            info['loser_pid']=db.scalar(text('SELECT pg_backend_pid()'));ready.set()
            try:
                result=invoke(db,loser);db.commit()
                return dict(status='committed',result=result)
            except InventoryReadError as error:
                db.rollback();return dict(status='rejected',code=error.code)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(api) as db:
            info['winner_pid']=db.scalar(text('SELECT pg_backend_pid()'))
            first=invoke(db,winner);future=pool.submit(compete)
            try:
                assert ready.wait(5),'competitor did not connect'
                deadline=monotonic()+10;blocked=False
                with owner.connect() as observe:
                    while monotonic()<deadline and not future.done():
                        blockers=observe.scalar(text('SELECT pg_blocking_pids(:pid)'),dict(pid=info['loser_pid']))
                        if info['winner_pid'] in blockers:
                            blocked=True;break
                        sleep(.05)
                assert blocked,'second API connection did not wait on winner'
                db.commit()
            except BaseException:
                db.rollback();raise
        second=future.result(timeout=35)
    after=snapshot(owner)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        result=sealed_inverse.lookup_unshipped_return(db,actor=load_formal_principal(db,context['admin_id']),request=command)
        db.commit()
    with owner.connect() as db:
        counts=tuple(db.scalar(text('SELECT count(*) FROM public.'+table+' WHERE root_disposition_id=:id'),dict(id=root_id))
            for table in ('stock_loss_return_stops','stock_loss_disposition_reversals','stock_loss_inverse_request_seals'))
    if winner=='seal':
        assert second==dict(status='rejected',code='loss_inverse_request_sealed'),second
        assert counts==(0,0,1) and result==first
        for table in ('inventory_transactions','inventory_movements','stock_balances','serial_current_positions'):
            assert after[table]==before[table],table
    else:
        assert second['status']=='committed' and second['result']['request_state']=='found',second
        assert counts==(1,1,0) and result==second['result'] and result['result']==first
        assert len(after['inventory_transactions'])==len(before['inventory_transactions'])+1
    assert snapshot(owner)==after
    print('Formal seal/execute race '+winner+' wins; exact recovery and exclusive facts PASS',flush=True)
    return dict(passed=True,tracking=context['tracking'],winner=winner,loser=loser,databaseBlockingObserved=True,
        exactRecovery=True,exclusiveFacts=True,stopCount=counts[0],inverseCount=counts[1],sealCount=counts[2],
        nativeSealExecuteRace=True,formalMigrationInstalled=True,productionAcceptance=False)
