"""Dedicated HTTP stop on actual PG16 API transactions; synthetic identity only."""
from uuid import uuid4,UUID
import json
from unittest.mock import patch
from fastapi import Depends,Request
from fastapi.testclient import TestClient
from sqlalchemy import event,select,text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission,Role,RolePermission
from app.main import app


def exercise(context,command,original,original_result,root_id):
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    commits=[];reads=[];statuses=[];lose_reply=False
    tables=('inventory_transactions','inventory_movements','inventory_movement_serials','stock_balances',
        'serial_current_positions','stock_loss_dispositions','stock_loss_return_stops',
        'stock_loss_disposition_reversals','stock_loss_inverse_request_seals','stock_loss_request_key_bindings',
        'audit_events','outbox_events','notification_events','state_transition_events')
    def snapshot():
        with owner.connect() as db:
            return {table:tuple(db.scalars(text('SELECT to_jsonb(t)::text FROM public.'+table+' t ORDER BY to_jsonb(t)::text'))) for table in tables}
    class HttpSession(Session):
        def commit(self):
            nonlocal lose_reply
            assert not self.info.get('readonly')
            super().commit();commits.append('committed')
            if lose_reply:
                lose_reply=False
                raise OperationalError('synthetic reply lost after real commit',{},Exception('unavailable'))
    def request_db(request:Request):
        with HttpSession(api) as db:
            readonly=request.method=='GET' or request.url.path.endswith('/request-lookup')
            preview_only=request.url.path.endswith('/preview')
            query_only=readonly or preview_only
            db.info['readonly']=query_only
            if readonly:
                db.execute(text('SET TRANSACTION READ ONLY'))
                assert db.scalar(text('SHOW transaction_read_only'))=='on';reads.append(request.url.path)
            assert db.scalar(text('SELECT current_user'))=='star_oam_api'
            connection=db.connection()
            def select_only(conn,cursor,statement,parameters,execution_context,executemany):
                assert statement.lstrip().upper().startswith('SELECT '),'read path emitted non-SELECT'
            if query_only:event.listen(connection,'before_cursor_execute',select_only)
            try:
                yield db
                if query_only:assert not db.new and not db.dirty and not db.deleted
            finally:
                if query_only:event.remove(connection,'before_cursor_execute',select_only)
    def actor(db:Session=Depends(get_db)):return load_formal_principal(db,context['admin_id'])
    with owner.connect() as db:
        grants={action:db.scalar(select(RolePermission.id).join(Permission,Permission.id==RolePermission.permission_id)
            .join(Role,Role.id==RolePermission.role_id).where(Role.code=='admin',Permission.resource=='stock_operation',
            Permission.action==action,Permission.field_code=='')) for action in ('read','reverse_loss')}
        assert all(grants.values())
    def permission(action,effect):
        with owner.begin() as db:db.execute(text('UPDATE role_permissions SET effect=:effect WHERE id=:id'),dict(id=grants[action],effect=effect))
    base='/api/v1/stock-operations/loss-reports/corrections/return-stops'
    def safe(response,status):
        assert response.status_code==status,(response.status_code,response.text)
        assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
        assert response.headers['referrer-policy']=='no-referrer'
        assert all(secret not in response.text for secret in ('command_jsonb','key_hash','plan_jsonb','PRIVATE-SQL'))
        statuses.append(status);return response.json()
    with patch.object(app,'dependency_overrides',{get_db:request_db,get_formal_principal:actor}):
        client=TestClient(app,raise_server_exceptions=False)
        try:
            def post(suffix,body,status=200):
                response=client.post(base+suffix,json=body)
                if 'idempotency_key' in body:assert body['idempotency_key'] not in response.text
                return safe(response,status)
            def source(state=None,status=200):
                before=snapshot();count=len(commits)
                result=safe(client.get(base+'/sources/'+str(root_id)),status)
                assert snapshot()==before and len(commits)==count
                if status==200:
                    assert result['state']==state and result['write_authorization_provided'] is False
                    assert result['current_stock_verified'] is False
                return result
            initial=snapshot();first=source('preview_required')
            history_url='/api/v1/stock-operations/loss-reports/corrections/return-history/'+str(root_id)
            history_before=safe(client.get(history_url),200)
            selection=dict(first['preview_reference'],reason='Explicit HTTP return stop')
            preview=post('/preview',selection)
            assert preview['return_operation_id']==first['return_operation_id']
            assert preview['return_line_id']==first['return_line_id']
            assert preview['quantity']==first['quantity'] and preview['stock_effect']=='none'
            assert snapshot()==initial
            body=dict(selection,expected_plan_hash=preview['plan_hash'],request_id=uuid4().hex,idempotency_key=uuid4().hex)
            sealed=post('/request-seal',body)
            assert sealed['request_state']=='sealed' and sealed['retry_allowed'] is False
            closed=snapshot()
            assert all(closed[t]==initial[t] for t in tables[:6])
            assert post('/request-lookup',body)==sealed
            for changes in ({},{'request_id':uuid4().hex},{'idempotency_key':uuid4().hex}):
                post('',dict(body,**changes),409)
                assert snapshot()==closed
            fresh=dict(body,request_id=uuid4().hex,idempotency_key=uuid4().hex)
            missing=post('/request-lookup',fresh)
            assert missing['request_state']=='not_found' and missing['retry_allowed'] is False
            assert snapshot()==closed
            count=len(commits);lose_reply=True
            unknown=post('',fresh,503)
            assert unknown['detail']['code']=='loss_correction_outcome_unconfirmed'
            assert len(commits)==count+1
            settled=snapshot();found=post('/request-lookup',fresh)
            assert found['request_state']=='found' and found['retry_allowed'] is False
            assert snapshot()==settled and len(commits)==count+1
            assert len(settled['stock_loss_return_stops'])==len(settled['stock_loss_disposition_reversals'])==1
            assert settled['stock_loss_dispositions']==initial['stock_loss_dispositions']
            stopped=source('stopped')
            assert stopped['stop']['reversal_id']==found['result']['reversal_id']
            assert stopped['serial_ids']==first['serial_ids'] and stopped['quantity']==first['quantity']
            assert stopped['preview_reference'] is None
            assert post('/request-lookup',body)==sealed
            history_after=safe(client.get(history_url),200)
            assert history_after['lines']==history_before['lines']
            assert history_after['coordinates']==history_before['coordinates']
            for action in ('reverse_loss','read'):
                permission(action,'deny')
                try:
                    if action=='reverse_loss':
                        assert post('/request-lookup',fresh)==found
                        assert post('/request-lookup',body)==sealed
                        source('stopped');post('',fresh,403);post('/request-seal',fresh,403)
                    else:
                        post('/request-lookup',fresh,403);source(status=403)
                finally:permission(action,'allow')
                assert snapshot()==settled
            # Exact original return recovery retains its own historical result.
            from app.formal_services import stock_loss_disposition_recovery
            with Session(api) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                recovered=stock_loss_disposition_recovery.lookup_disposition_request(db,actor=load_formal_principal(db,context['admin_id']),request=original,flow='return')
                assert recovered['lookup_status']=='found' and recovered['disposition']==original_result
            assert snapshot()==settled
        finally:client.close()
    from app.formal_services.stock_loss_corrections import binding_contracts as binding_sql,bound_recovery
    from app.formal_services.inventory_query import InventoryReadError
    from app.formal_services.stock_loss_corrections.request_contracts import ReversalExecute
    corruptions=[]
    for mode in ('missing','shared_token','other_action_alias'):
        before=snapshot()
        with Session(owner) as db:
            db.execute(text('ALTER TABLE public.'+binding_sql.TABLE+' DISABLE TRIGGER trg_loss_request_binding_immutable'))
            statement={
                'missing':'DELETE FROM public.'+binding_sql.TABLE+' WHERE fact_id=:id',
                'shared_token':'UPDATE public.'+binding_sql.TABLE+' SET key_token=:digest WHERE fact_id=:id',
                'other_action_alias':'UPDATE public.'+binding_sql.TABLE+' SET approval_key_hash=:digest WHERE fact_id=:id',
            }[mode]
            assert db.execute(text(statement),dict(id=UUID(found['result']['reversal_id']),digest=uuid4().hex+uuid4().hex)).rowcount==1
            try:bound_recovery.lookup_unshipped_return(db,actor=load_formal_principal(db,context['admin_id']),request=ReversalExecute(**fresh))
            except InventoryReadError as error:
                assert error.status_code==503 and error.code=='loss_request_binding_outcome_unknown'
            else:raise AssertionError('corrupt dedicated binding accepted: '+mode)
            db.rollback()
        assert snapshot()==before;corruptions.append(mode)
    fixture=dict(source=first,preview=preview,original=fresh,request_hash=found['request_hash'],missing=missing,found=found,
        sealed_command=body,sealed=sealed,after=stopped,history_before=history_before,history_after=history_after)
    if context.get('http_artifact_dir') is not None:
        (context['http_artifact_dir']/'http-fixture.json').write_text(json.dumps(fixture,indent=2)+'\n')
    print('Dedicated HTTP real COMMIT reply loss, READ ONLY bound recovery, permanent seal and ACL separation PASS',flush=True)
    return dict(passed=True,tracking=context['tracking'],httpStatusCodes=statuses,realPostCommitReplyLoss=True,
        committedTransactions=len(commits),readonlyRequests=len(reads),previewSelectOnlyWithEvidenceLocks=True,stockNeutralSeal=True,
        oldRequestAliasesRejected=3,sourceBeforeAfterVerified=True,originalReturnRecovery=True,
        exactDatabaseRequestBindings=True,writeAndReadRightsIndependent=True,
        corruptBindingCases=corruptions,corruptionUsesOwnerRollback=True,fulfillmentHistoryUnchanged=True,
        realLoginIdentity=False,productionAcceptance=False)
