"""Exercise queue fairness and transaction isolation with real local SQL reads."""
from uuid import UUID
import json
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app import opening_count_import_worker as worker
from app.formal_services import opening_count_import_termination as termination

@pytest.fixture
def queue():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE file_jobs (id CHAR(32) PRIMARY KEY, job_type TEXT, status TEXT)')
    def seed(count, status='queued', offset=0, job_type='import'):
        ids=[UUID(int=i+1+offset) for i in range(count)]
        with engine.begin() as db:
            db.execute(text('INSERT INTO file_jobs VALUES (:id,:kind,:status)'),
                       [{'id':i.hex,'kind':job_type,'status':status} for i in ids])
        return ids
    yield engine,lambda:Session(engine),seed
    engine.dispose()

class Stop:
    def __init__(self, pages=1): self.pages=pages;self.waits=[];self.requested=False
    def is_set(self): return self.requested or len(self.waits)>=self.pages
    def wait(self,seconds): self.waits.append(seconds)

def test_timeout_isolates_original_job_and_advances_healthy_job_without_leaking_exception(queue,monkeypatch):
    engine,factory,seed=queue;bad,good=seed(2);out=[];seen=[];sweeps=[];stop=Stop()
    def process(factory,*,storage,job_id):
        seen.append(job_id)
        if job_id==bad: raise TimeoutError('PRIVATE-URL-AND-CELL-SHOULD-NOT-APPEAR')
        return worker.OpeningImportWorkerResult(job_id,'awaiting_confirmation')
    def sweep(*a,**kw):
        sweeps.append(kw['after_id']);return termination.OpeningImportContextSweep(0,None)
    monkeypatch.setattr(worker,'process_one_opening_count_import',process)
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',sweep)
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=5,stop_event=stop,emit=out.append)
    assert seen==[bad,good] and sweeps==[None] and stop.waits==[5]
    assert out[0]=={'ok':False,'job_id':str(bad),'code':'opening_import_worker_retry_or_review_required'}
    assert out[1]['status']=='awaiting_confirmation' and 'PRIVATE' not in json.dumps(out)
    with factory() as db: assert db.execute(text('SELECT status FROM file_jobs ORDER BY id')).scalars().all()==['queued','queued']

def test_three_pages_do_not_starve_later_jobs_or_spin_on_failures(queue,monkeypatch):
    _,factory,seed=queue;ids=seed(205,status='prevalidating');seen=[];out=[];stop=Stop(4)
    def process(*a,job_id,**kw):
        seen.append(job_id);raise TimeoutError('unknown')
    monkeypatch.setattr(worker,'process_one_opening_count_import',process)
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',lambda *a,**kw:termination.OpeningImportContextSweep(0,None))
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=7,stop_event=stop,emit=out.append)
    assert seen[:205]==ids and seen[205:]==ids[:100]
    assert stop.waits==[7]*4 and len(out)==305

def test_queue_page_filters_out_terminal_and_nonimport_jobs(queue):
    _,factory,seed=queue;first=seed(1);second=seed(1,status='prevalidating',offset=1)
    seed(1,status='succeeded',offset=2);seed(1,status='awaiting_confirmation',offset=3);seed(1,offset=4,job_type='export')
    assert worker._opening_import_queue_page(factory)==tuple(first+second)
    assert worker._opening_import_queue_page(factory,after_id=first[0],limit=1)==tuple(second)

@pytest.mark.parametrize('limit,cursor',[(0,None),(101,None),(True,None),(1,'bad')])
def test_invalid_page_never_opens_database(limit,cursor):
    with pytest.raises(ValueError,match='queue_page_invalid'):
        worker._opening_import_queue_page(lambda:pytest.fail('database opened'),limit=limit,after_id=cursor)

def test_sweep_failure_rolls_back_only_bad_job_and_continues_exact_cursor(queue,monkeypatch):
    _,factory,seed=queue;bad,good=seed(2,status='awaiting_confirmation');seen=[]
    def terminate(db,*,job_id,request_id):
        seen.append(job_id)
        db.execute(text("UPDATE file_jobs SET status='failed' WHERE id=:id"),{'id':job_id.hex})
        if job_id==bad: raise TimeoutError('unknown')
    monkeypatch.setattr(termination,'terminate_ineligible_opening_import',terminate)
    result=termination.sweep_awaiting_opening_imports(factory,limit=2)
    assert result.checked==2 and result.next_after_id==good and result.failed_ids==(bad,) and seen==[bad,good]
    with factory() as db: assert db.execute(text('SELECT status FROM file_jobs ORDER BY id')).scalars().all()==['awaiting_confirmation','failed']
    tail=termination.sweep_awaiting_opening_imports(factory,after_id=result.next_after_id,limit=2)
    assert tail.checked==0 and tail.next_after_id is None and tail.failed_ids==()

def test_stop_between_jobs_does_not_process_next_or_sweep(queue,monkeypatch):
    _,factory,seed=queue;ids=seed(2);stop=Stop();seen=[]
    def process(*a,job_id,**kw):
        seen.append(job_id);stop.requested=True;return worker.OpeningImportWorkerResult(job_id,'failed')
    monkeypatch.setattr(worker,'process_one_opening_count_import',process)
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',lambda *a,**kw:pytest.fail('sweep after stop'))
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=5,stop_event=stop,emit=lambda _:None)
    assert seen==ids[:1] and not stop.waits

def test_idle_still_sweeps_and_waits_and_reports_sweep_failure(queue,monkeypatch):
    _,factory,_=queue;identifier=UUID(int=10);out=[];stop=Stop()
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',lambda *a,**kw:termination.OpeningImportContextSweep(1,None,(identifier,)))
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=9,stop_event=stop,emit=out.append)
    assert out[0]['status']=='idle' and stop.waits==[9]
    assert out[1]=={'ok':False,'job_id':str(identifier),'code':'opening_import_context_review_required'}

def test_discovery_failure_propagates_to_supervisor_without_busy_retry(monkeypatch):
    stop=Stop();out=[]
    def bad(*a,**kw): raise ConnectionError('database unavailable')
    monkeypatch.setattr(worker,'_opening_import_queue_page',bad)
    with pytest.raises(ConnectionError):
        worker._poll_opening_imports(lambda:None,storage=object(),poll_seconds=5,stop_event=stop,emit=out.append)
    assert not stop.waits and not out

def test_unknown_job_commit_is_reobserved_without_repeating_terminal_work(queue,monkeypatch):
    _,factory,seed=queue;first,second=seed(2);calls=[];out=[];stop=Stop(2)
    def process(*args,job_id,**kwargs):
        calls.append(job_id)
        with factory() as db:
            db.execute(text("UPDATE file_jobs SET status='awaiting_confirmation' WHERE id=:id"),{'id':job_id.hex})
            db.commit()
        if job_id==first: raise TimeoutError('commit acknowledgement lost')
        return worker.OpeningImportWorkerResult(job_id,'awaiting_confirmation')
    monkeypatch.setattr(worker,'process_one_opening_count_import',process)
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',lambda *a,**kw:termination.OpeningImportContextSweep(0,None))
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=5,stop_event=stop,emit=out.append)
    assert calls==[first,second] and out[-1]['status']=='idle' and stop.waits==[5,5]
    with factory() as db: assert set(db.execute(text('SELECT status FROM file_jobs')).scalars())=={'awaiting_confirmation'}

def test_sweep_unknown_commit_keeps_committed_fact_and_continues_next_job(queue,monkeypatch):
    engine,_,seed=queue;first,second=seed(2,status='awaiting_confirmation');commits=[]
    class AckLost(Session):
        def commit(self):
            super().commit();commits.append(True)
            if len(commits)==1: raise TimeoutError('commit acknowledgement lost')
    factory=lambda:AckLost(engine)
    def terminate(db,*,job_id,request_id):
        db.execute(text("UPDATE file_jobs SET status='failed' WHERE id=:id"),{'id':job_id.hex})
    monkeypatch.setattr(termination,'terminate_ineligible_opening_import',terminate)
    result=termination.sweep_awaiting_opening_imports(factory)
    assert result.failed_ids==(first,) and result.checked==2 and len(commits)==2
    with factory() as db: assert set(db.execute(text('SELECT status FROM file_jobs')).scalars())=={'failed'}
    assert termination.sweep_awaiting_opening_imports(factory).checked==0

def test_slow_batch_runs_due_context_sweeps_between_jobs(queue,monkeypatch):
    import time
    _,factory,seed=queue;ids=seed(3);now=[10.0];seen=[];sweeps=[];stop=Stop()
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    def process(*args,job_id,**kwargs):
        seen.append(job_id);now[0]+=6
        return worker.OpeningImportWorkerResult(job_id,'awaiting_confirmation')
    def sweep(*args,after_id):
        sweeps.append((len(seen),after_id))
        return termination.OpeningImportContextSweep(100,ids[len(sweeps)-1])
    monkeypatch.setattr(worker,'process_one_opening_count_import',process)
    monkeypatch.setattr(termination,'sweep_awaiting_opening_imports',sweep)
    worker._poll_opening_imports(factory,storage=object(),poll_seconds=5,stop_event=stop,emit=lambda _:None)
    assert sweeps==[(1,None),(2,ids[0]),(3,ids[1])] and stop.waits==[5]
