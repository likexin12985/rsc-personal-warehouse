"""Original-cursor full-plan proof on actual two-generation service exports.

Uses the complete model and existing candidate guards. This is not the full
0164 migration catalog, nor Python business writes performed against PG.
"""
from copy import deepcopy
import hashlib
import json
from datetime import timedelta
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from pg16_scrap_inventory_edges_gate import CLOUD, decoded, install
from pg16_stock_scrap_bindings_gate import snapshot


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def run(engines,fixture):
    data=json.loads(fixture.read_text(),object_hook=decoded)
    owner,api=engines['star_oam_migrator'],engines['star_oam_api']
    metadata=install(owner,data)
    catalog=json.loads((CLOUD/'backend/alembic/stock_loss_corrections_0159/frozen-catalog.json').read_text())
    with owner.begin() as db:
        for name,args in [('rsc_loss_chain_projection_0159','uuid,bigint'),('rsc_loss_hold_projection_0159','uuid,bigint')]:
            db.execute(text(next(f['definition'] for f in catalog['newFunctions'] if f['proname']==name)))
            db.execute(text(f'REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC,star_oam_api,star_oam_projector,star_oam_edge,edge_inbox'))
        db.execute(text((CLOUD/'backend/alembic/stock_scrap_0165/historical_plans.sql').read_text()))
        triggers=db.execute(text("SELECT c.relname,t.tgdeferrable,t.tginitdeferred,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname='trg_scrap_historical_plans_0165'")).all()
        assert len(triggers)==20 and all(r.tgdeferrable and r.tginitdeferred and r.tgenabled=='A' for r in triggers)
        for signature in ['rsc_scrap_policy_basis_0165(uuid,timestamptz,jsonb)',
                'rsc_check_scrap_historical_plans_0165(uuid)','rsc_fence_scrap_historical_plans_0165()']:
            f=db.execute(text('SELECT prosecdef,proconfig,pg_get_userbyid(proowner) owner FROM pg_proc WHERE oid=CAST(:sig AS regprocedure)'),dict(sig='public.'+signature)).one()
            assert f.prosecdef and f.proconfig==['search_path=pg_catalog, public'] and f.owner=='star_oam_migrator'
            for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),dict(role=role,sig='public.'+signature))
    rows=data['rows'];lines=rows['stock_scrap_lines'];successful=[];rejected=[]
    byid=lambda name,id_:next(r for r in rows[name] if r['id']==id_)
    for line in lines:
        with owner.begin() as db:
            db.execute(text('SELECT public.rsc_check_scrap_historical_plans_0165(:id)'),dict(id=line['id']))
            tx=byid('inventory_transactions',line['posting_transaction_id'])
            projected=db.scalar(text('SELECT public.rsc_loss_hold_projection_0159(:account,:cursor)'),
                dict(account=line['frozen_account_id'],cursor=tx['ledger_cursor']-1))
            assert projected==line['plan_jsonb']['frozen_holds_before']
        successful.append(line['source_kind']+':scrap_and_recovery_plans')
    baseline=snapshot(owner,metadata)
    def update(db,name,id_,**values):
        t=metadata.tables[name];db.execute(t.update().where(t.c.id==id_).values(**values))
    def reject(label,action,*,message=None,state='23514',phase='commit',engine=owner):
        reached='statement'
        try:
            with engine.begin() as db:
                action(db);reached='commit'
        except DBAPIError as error:
            assert error.orig.sqlstate==state,(label,str(error.orig))
            assert phase==reached,(label,reached,str(error.orig))
            if message:assert message==error.orig.diag.message_primary,(label,str(error.orig))
            assert snapshot(owner,metadata)==baseline,label+': rollback altered ledger'
            rejected.append(dict(case=label,phase=reached,sqlstate=state,allRowsPreserved=True))
        else:raise AssertionError(label+': invalid historical plan accepted')
    for line in lines:
        kind=line['source_kind']
        name='stock_loss_dispositions' if kind=='original' else 'stock_loss_correction_executions'
        fact=byid(name,line['root_disposition_id'] if kind=='original' else line['correction_execution_id'])
        recovery=next(r for r in rows['stock_scrap_recovery_executions'] if r['scrap_line_id']==line['id'])
        inverse=byid('stock_loss_disposition_reversals',recovery['reversal_id'])
        # Rehash the parent plan; scrap also rehashes the command. Recovery
        # retains its request hash so the successor's exact inverse binding
        # cannot mask this proof. The immutable child remains untouched.
        # Require reconstruction, not child/pointer/append-only rejection.
        def forged(db,which,change):
            row=fact if which=='scrap' else inverse
            plan=deepcopy(row['plan_jsonb']);change(plan)
            command=deepcopy(row['command_jsonb']);command['expected_plan_hash']=digest(plan)
            values=dict(plan_jsonb=plan,plan_hash=digest(plan))
            if which=='scrap':values.update(command_jsonb=command,request_hash=digest(command))
            update(db,name if which=='scrap' else 'stock_loss_disposition_reversals',row['id'],**values)
            if which=='scrap':update(db,'stock_operation_orders',line['operation_id'],**values)
        for which in ('scrap','recovery'):
            balance='source_balance_quantity' if which=='scrap' else 'target_balance_quantity'
            version='source_balance_version' if which=='scrap' else 'target_balance_version'
            changes=[('balance',lambda p,k=balance:p.update({k:'999.000'})),
                ('version',lambda p,k=version:p.update({k:999})),
                ('cursor',lambda p:p.update(ledger_cursor=p['ledger_cursor']+1)),
                ('missing_holds',lambda p:p['frozen_holds_before'].update(lines=[])),
                ('frozen_share',lambda p:p['frozen_holds_before']['lines'][0].update(frozen_quantity='99.000')),
                ('extra_field',lambda p:p.update(unsafe_extra=True)),
                ('actor_version_type',lambda p:p.update(authorization_version=float(p['authorization_version'])))]
            if data['tracking']=='serial':
                changes.extend([('serial_predecessor',lambda p:p['serials'][0].update(previous_ledger_cursor=0)),
                    ('missing_serial',lambda p:p.update(serial_ids=[])),
                    ('lifecycle',lambda p:p['serials'][0].update(lifecycle_after='missing'))])
            for label,change in changes:
                def attack(db,w=which,c=change):
                    forged(db,w,c)
                    # First-generation hashes are referenced by the successor.
                    # Check the exact attacked generation before any deferred
                    # successor guard can mask the intended plan rejection.
                    if kind=='original':
                        db.execute(text('SELECT public.rsc_check_scrap_historical_plans_0165(:id)'),dict(id=line['id']))
                reject(kind+':'+which+':rehashed_'+label,attack,
                    phase='statement' if kind=='original' else 'commit',
                    message='0165 complete historical '+which+' plan required')
        reject(kind+':source_command_extra',lambda db:update(db,name,fact['id'],command_jsonb=fact['command_jsonb']|{'extra':True}),
            message='0165 canonical source-bound scrap command required')
    # Closing a policy after all retained operations must not invalidate the
    # originally open-ended fingerprint. Force deferred checks, then roll back
    # this hypothetical later closure without changing the exported baseline.
    account=byid('stock_accounts',lines[0]['frozen_account_id'])
    policy=next(p for p in rows['material_inventory_policies'] if p['material_id']==account['material_id'])
    assert policy['effective_to'] is None
    with owner.connect() as db:
        transaction=db.begin()
        try:
            update(db,'material_inventory_policies',policy['id'],effective_to=
                max(t['effective_at'] for t in rows['inventory_transactions'])+timedelta(days=1))
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            for line in lines:
                db.execute(text('SELECT public.rsc_check_scrap_historical_plans_0165(:id)'),dict(id=line['id']))
        finally:transaction.rollback()
    assert snapshot(owner,metadata)==baseline
    successful.append('later_policy_closure_preserves_historical_plans')
    recorded=lines[0]['plan_jsonb']['policy_fingerprint']
    for label,change in [('missing',lambda p:[]),('scale',lambda p:[p[0][:3]+[99]+p[0][4:]]),
            ('start',lambda p:[p[0][:5]+['2000-01-01T00:00:00+00:00']+p[0][6:]]),
            ('invented_end',lambda p:[p[0][:6]+['2099-01-01T00:00:00+00:00']])]:
        reject('policy_fingerprint:'+label,lambda db,c=change:db.execute(text(
            'SELECT public.rsc_scrap_policy_basis_0165(:account,:at,CAST(:recorded AS jsonb))'),
            dict(account=account['id'],at=lines[0]['created_at'],recorded=json.dumps(c(deepcopy(recorded))))),
            phase='statement',message='0165 exact historical scrap policy fingerprint required')
    # A later unrelated pending report must have its own exact share, visible
    # only at/after its freeze. The original scrap preceded this second freeze.
    if data.get('shared'):
        account=lines[0]['frozen_account_id']
        pending=next(l for l in rows['stock_operation_lines'] if l['operation_type']=='loss_report'
            and l['reserved_account_id']==account and l['id']!=lines[0]['loss_line_id'])
        order=byid('stock_operation_orders',pending['operation_id'])
        freeze=byid('inventory_transactions',order['posting_transaction_id'])
        with owner.begin() as db:
            for cursor in range(freeze['ledger_cursor']-1,max(t['ledger_cursor'] for t in rows['inventory_transactions'])+1):
                projection=db.scalar(text('SELECT public.rsc_loss_hold_projection_0159(:account,:cursor)'),dict(account=account,cursor=cursor))
                share=next((l for l in projection['lines'] if l['line_id']==str(pending['id'])),None)
                if cursor<freeze['ledger_cursor']:assert share is None
                else:
                    assert share['frozen_quantity']=='1.000' and share['active_execution_id'] is None
                    assert share['original_serial_ids']==share['frozen_serial_ids']
                    assert len(share['frozen_serial_ids'])==(1 if data['tracking']=='serial' else 0)
        successful.append('other_pending_share_at_every_later_cursor')
        reject('other_report_quantity',lambda db:update(db,'stock_operation_lines',pending['id'],quantity=pending['quantity']+1))
    reject('private_api_call',lambda db:db.execute(text('SELECT public.rsc_check_scrap_historical_plans_0165(:id)'),
        dict(id=lines[0]['id'])),state='42501',phase='statement',engine=api)
    return dict(status='passed',tracking=data['tracking'],shared=data.get('shared',False),successful=successful,rejected=rejected,
        exactSnapshotPreserved=snapshot(owner,metadata)==baseline,full0164Migration=False,postgresBusinessPosting=False,
        scope='complete historical plan reconstruction over full model synthetic two-generation exports')
