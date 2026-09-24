from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import event
from app.formal_access import load_formal_principal
from app.formal_services import opening_start_recovery as recovery,opening_stocktake as opening
from test_opening_stocktake_service import db,world,_start,NOW

@pytest.fixture(autouse=True)
def clock(monkeypatch):monkeypatch.setattr(opening,'_database_now',lambda db:NOW)

@pytest.fixture
def saved(world,monkeypatch):
    result=_start(world);world.db.commit()
    # SQLite checks only the historical domain graph. The actual scoped
    # publication capability is exercised on native PG16, without this stub.
    monkeypatch.setattr(recovery,'selection',lambda db,**kw:(dict(sync_run_id=str(world.command.control_sync_run_id),
        source_system_id=str(world.command.control_source_system_id),sync_scope_key=world.command.control_sync_scope_key),world.command.control_lines))
    return world,result

def read(world,**changes):
    args=dict(actor=world.principals['manager_x'],region_org_id=world.command.region_org_id,publication_id=uuid4(),trace_request_id='opening-request-0001')
    return recovery.recover_start(world.db,**(args|changes))

def test_recovery_verifies_original_graph_without_locks_or_writes(saved,monkeypatch):
    world,result=saved;statements=[]
    monkeypatch.setattr(opening,'verify_audit_event_in_stream',lambda *a,**k:pytest.fail('locking audit verifier called'))
    def record(*args):statements.append(args[2])
    event.listen(world.db.bind,'before_cursor_execute',record)
    try:found=read(world)
    finally:event.remove(world.db.bind,'before_cursor_execute',record)
    assert found['outcome']=='found' and found['result']['task_id']==result.task_id and found['result']['replayed']
    assert found['automatic_retry_allowed'] is False
    assert statements and all(sql.lstrip().startswith('SELECT') and 'FOR UPDATE' not in sql for sql in statements)
    assert not world.db.new and not world.db.dirty and not world.db.deleted

def test_missing_is_not_permission_to_repeat(saved):
    result=read(saved[0],trace_request_id=uuid4().hex)
    assert result['outcome']=='not_observed' and result['result'] is None and result['automatic_retry_allowed'] is False

def test_same_person_new_current_grant_can_recover_but_stale_principal_cannot(saved):
    world,result=saved
    original=world.principals['manager_x']
    world.manager_x.user.authorization_version += 1
    world.db.commit()
    current=load_formal_principal(world.db,world.manager_x.user.id,now=NOW)
    assert current.person_id == original.person_id
    assert current.authorization_version == original.authorization_version + 1
    with pytest.raises(opening.OpeningStocktakeError):read(world,actor=original)
    world.db.rollback()
    found=read(world,actor=current)
    assert found['outcome']=='found' and found['result']['task_id']==result.task_id
    assert found['authorization_version']==current.authorization_version
    assert not world.db.new and not world.db.dirty and not world.db.deleted

def test_other_region_actor_cannot_recover(saved):
    world,_=saved
    with pytest.raises(opening.OpeningStocktakeError):read(world,actor=world.principals['manager_y'])

def test_cross_publication_run_refused(saved,monkeypatch):
    world,_=saved
    monkeypatch.setattr(recovery,'selection',lambda db,**kw:(dict(sync_run_id=str(uuid4()),source_system_id=str(world.command.control_source_system_id),sync_scope_key=world.command.control_sync_scope_key),world.command.control_lines))
    with pytest.raises(opening.OpeningStocktakeError) as error:read(world)
    assert error.value.code=='opening_recovery_request_conflict'

def test_read_drift_is_not_a_stable_success(saved,monkeypatch):
    world,_=saved;values=iter([None,{'task_id':str(uuid4())}])
    monkeypatch.setattr(recovery,'_read',lambda *a,**k:next(values))
    with pytest.raises(opening.OpeningStocktakeError) as error:read(world)
    assert error.value.code=='opening_recovery_changed'
