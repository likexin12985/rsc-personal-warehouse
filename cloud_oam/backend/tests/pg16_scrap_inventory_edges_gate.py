"""Native edge proof on full-schema ledgers exported by real local services.

All columns, rows and model constraints are retained, without minimal stock
parents. This does not install the complete 0164 migration/old trigger catalog;
the export uses SQLite services and cannot prove a new PG posting service.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from app.database import Base
from app.stock_scrap_persistence_schema import build_schema
from pg16_stock_scrap_bindings_gate import snapshot

CLOUD=Path(__file__).resolve().parents[2]


def decoded(value):
    if set(value)=={'__rsc_type__','value'}:
        return {'UUID':UUID,'Decimal':Decimal,'datetime':datetime.fromisoformat}[value['__rsc_type__']](value['value'])
    return value


def schema():
    metadata,_,_=build_schema()
    for name,original in Base.metadata.tables.items():
        for constraint in original.constraints:
            rule=constraint._ddl_if
            if rule is None:continue
            matches=[c for c in metadata.tables[name].constraints if type(c) is type(constraint)
                and c.name==constraint.name and str(getattr(c,'sqltext',''))==str(getattr(constraint,'sqltext',''))]
            assert len(matches)==1
            matches[0].ddl_if(dialect=rule.dialect,callable_=rule.callable_,state=rule.state)
    return metadata


def install(owner, data):
    metadata=schema()
    metadata.create_all(owner)
    # Preserve every exported row; defer only constraints declared deferrable
    # in the actual schema. No constraint is disabled or rewritten for import.
    imported=deepcopy(data['rows'])
    # A real audit stream starts with an empty head, then advances to its
    # retained event. Materialize this same cycle within the import transaction;
    # the complete final rows are compared against the untouched export below.
    for row in imported['audit_chain_heads']:
        row.update(last_event_id=None,last_hash=None,version=0)
    nodes={(name,index):row for name,rows in imported.items() for index,row in enumerate(rows)}
    dependencies={}
    for node,row in nodes.items():
        required=set()
        for fk in metadata.tables[node[0]].foreign_key_constraints:
            if fk.deferrable:continue
            local=tuple(row[e.parent.name] for e in fk.elements)
            if any(v is None for v in local):continue
            target=next(iter(fk.elements)).column.table.name
            matches=[(target,index) for index,value in enumerate(imported.get(target,[]))
                if tuple(value[e.column.name] for e in fk.elements)==local]
            assert len(matches)==1,(node[0],fk.name,'exact referenced export row required')
            required.update(matches)
        dependencies[node]=required-{node}
    with owner.begin() as db:
        db.execute(text('SET CONSTRAINTS ALL DEFERRED'))
        pending=dict(nodes); inserted=set()
        while pending:
            ready=sorted(node for node in pending if dependencies[node]<=inserted)
            assert ready,('unresolved exported row cycle',sorted({n[0] for n in pending}))
            for node in ready:
                db.execute(metadata.tables[node[0]].insert(),pending.pop(node));inserted.add(node)
        head=metadata.tables['audit_chain_heads']
        for row in data['rows']['audit_chain_heads']:
            db.execute(head.update().where(head.c.id==row['id']).values(**row))
    with owner.connect() as db:
        def canonical(row):
            return json.dumps(row,sort_keys=True,default=lambda value:
                value.astimezone(timezone.utc).isoformat() if isinstance(value,datetime) else str(value))
        for name,rows in data['rows'].items():
            actual=[dict(r) for r in db.execute(select(metadata.tables[name])).mappings()]
            assert sorted(map(canonical,actual))==sorted(map(canonical,rows)),name+': import changed source rows'
    migration=(CLOUD/'backend/alembic/versions/20260831_0026_opening_control_reconciliation.py').read_text()
    with owner.begin() as db:
        for symbol,name in [('PG_CANONICAL_JSON_FUNCTION','rsc_canonical_reconciliation_json_0026'),
                ('PG_PRINCIPAL_GRAPH_LOCK_FUNCTION','rsc_lock_formal_principal_graph_0026')]:
            body=re.search(r'CREATE FUNCTION public\.\{'+symbol+r'\}.*?\n\$\$',migration,re.S).group()
            body=body.replace('{'+symbol+'}',name).replace('{{','{').replace('}}','}')
            body=body.replace('{GUARD_ERROR}',re.search(r'GUARD_ERROR = "([^"]+)"',migration).group(1))
            db.execute(text(body))
        db.execute(text('REVOKE ALL ON FUNCTION public.rsc_lock_formal_principal_graph_0026(text[]) FROM PUBLIC'))
        catalog=json.loads((CLOUD/'backend/alembic/stock_loss_corrections_0159/frozen-catalog.json').read_text())
        db.execute(text(next(f['definition'] for f in catalog['newFunctions']
            if f['proname']=='rsc_loss_inventory_audit_members_0159')))
        for name in ('recovery_evidence.sql','recovery_approval.sql','recovery_authority.sql',
                'recovery_admission.sql','inventory_edges.sql'):
            db.execute(text((CLOUD/'backend/alembic/stock_scrap_0165'/name).read_text()))
        rows=db.execute(text("SELECT c.relname,t.tgdeferrable,t.tginitdeferred,t.tgenabled FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname='trg_scrap_inventory_edges_0165'")).all()
        assert {r.relname for r in rows}=={'stock_scrap_lines','stock_scrap_serials','stock_scrap_recovery_executions',
            'stock_loss_dispositions','stock_loss_correction_executions','stock_loss_disposition_reversals',
            'inventory_transactions','inventory_movements','inventory_movement_serials','stock_accounts',
            'stock_operation_serials','material_inventory_policies','inventory_serials'}
        assert all(r.tgdeferrable and r.tginitdeferred and r.tgenabled=='A' for r in rows)
        for signature in ('rsc_check_scrap_inventory_edges_0165(uuid)','rsc_fence_scrap_inventory_edges_0165()',
                'rsc_assert_scrap_tracking_0165(uuid,numeric,integer,timestamptz)'):
            f=db.execute(text('SELECT prosecdef,proconfig,pg_get_userbyid(proowner) owner FROM pg_proc '
                'WHERE oid=CAST(:sig AS regprocedure)'),dict(sig='public.'+signature)).one()
            assert f.prosecdef and f.proconfig==['search_path=pg_catalog, public'] and f.owner=='star_oam_migrator'
            for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),
                    dict(role=role,sig='public.'+signature))
    return metadata


def run(engines,fixture):
    data=json.loads(fixture.read_text(),object_hook=decoded)
    assert data['provenance']=='synthetic SQLite real services; two scrap/recovery generations'
    owner,api=engines['star_oam_migrator'],engines['star_oam_api']
    metadata=install(owner,data)
    lines=data['rows']['stock_scrap_lines']
    assert {line['source_kind'] for line in lines}=={'original','correction'}
    rows=data['rows']; successful=[]; rejected=[]
    byid=lambda table,id_:next(r for r in rows[table] if r['id']==id_)
    def update(db,table,id_,**changes):
        t=metadata.tables[table];db.execute(t.update().where(t.c.id==id_).values(**changes))
    for line in lines:
        with owner.begin() as db:
            db.execute(text('SELECT public.rsc_check_scrap_inventory_edges_0165(:id)'),dict(id=line['id']))
        successful.append(line['source_kind'])
    baseline=snapshot(owner,metadata)
    def reject(label,action,*,phase='commit',state='23514',engine=owner):
        reached='statement'
        try:
            with engine.begin() as db:
                action(db);reached='commit'
        except DBAPIError as error:
            assert error.orig.sqlstate==state,(label,str(error.orig))
            assert reached==phase,(label,reached)
            if state=='23514':
                assert '0165' in str(error.orig) and 'rsc_' in (error.orig.diag.context or ''),(label,str(error.orig))
            assert snapshot(owner,metadata)==baseline,label+': rollback changed retained ledger'
            rejected.append(dict(case=label,phase=reached,sqlstate=state,rollbackPreserved=True))
        else:raise AssertionError(label+': invalid ledger edge accepted')
    for line in lines:
        kind=line['source_kind'];tx=byid('inventory_transactions',line['posting_transaction_id'])
        move=byid('inventory_movements',line['posting_movement_id'])
        recovery=next(r for r in rows['stock_scrap_recovery_executions'] if r['scrap_line_id']==line['id'])
        inverse=byid('stock_loss_disposition_reversals',recovery['reversal_id'])
        itx=byid('inventory_transactions',inverse['posting_transaction_id'])
        imove=byid('inventory_movements',inverse['posting_movement_id'])
        other=next(a['id'] for a in rows['stock_accounts'] if a['id']!=line['frozen_account_id'])
        account=byid('stock_accounts',line['frozen_account_id'])
        policy=next(p for p in rows['material_inventory_policies'] if p['material_id']==account['material_id'])
        for label,table,id_,change in [
            ('scrap_quantity','inventory_movements',move['id'],dict(quantity=move['quantity']+1)),
            ('scrap_account','inventory_movements',move['id'],dict(from_account_id=other)),
            ('scrap_internal','inventory_movements',move['id'],dict(to_account_id=other,external_boundary_code=None)),
            ('scrap_boundary','inventory_movements',move['id'],dict(external_boundary_code='unrelated_boundary')),
            ('scrap_line_number','inventory_movements',move['id'],dict(line_no=2)),
            ('scrap_request','inventory_transactions',tx['id'],dict(request_hash='f'*64)),
            ('scrap_document','inventory_transactions',tx['id'],dict(source_document_id=str(uuid4()))),
            ('scrap_actor','inventory_transactions',tx['id'],dict(actor_user_id=next(u['id'] for u in rows['users'] if u['id']!=tx['actor_user_id']))),
            ('scrap_effective_time','inventory_transactions',tx['id'],dict(effective_at=tx['effective_at']-timedelta(seconds=1))),
            ('inverse_quantity','inventory_movements',imove['id'],dict(quantity=imove['quantity']+1)),
            ('inverse_account','inventory_movements',imove['id'],dict(to_account_id=other)),
            ('inverse_internal','inventory_movements',imove['id'],dict(from_account_id=other,external_boundary_code=None)),
            ('inverse_boundary','inventory_movements',imove['id'],dict(external_boundary_code='unrelated_boundary')),
            ('inverse_request','inventory_transactions',itx['id'],dict(request_hash='f'*64)),
            ('inverse_document','inventory_transactions',itx['id'],dict(source_document_id=str(uuid4()))),
            ('inverse_effective_time','inventory_transactions',itx['id'],dict(effective_at=itx['effective_at']-timedelta(seconds=1))),
            ('inverse_reason','stock_loss_disposition_reversals',inverse['id'],dict(reason='forged context')),
            ('inverse_plan','stock_loss_disposition_reversals',inverse['id'],dict(plan_jsonb={})),
            ('historical_policy_missing','material_inventory_policies',policy['id'],dict(effective_from=itx['effective_at']+timedelta(seconds=1))),
            ('historical_policy_changed','material_inventory_policies',policy['id'],dict(tracking_mode='none' if data['tracking']=='serial' else 'serial')),
            ('policy_requires_lot','material_inventory_policies',policy['id'],dict(tracking_mode='lot_and_serial' if data['tracking']=='serial' else 'lot')),
        ]:
            reject(kind+':'+label,lambda db,t=table,i=id_,v=change:update(db,t,i,**v))
        def extra_move(db):
            db.execute(metadata.tables['inventory_movements'].insert(),move|dict(id=uuid4(),line_no=2))
        reject(kind+':extra_scrap_movement',extra_move)
        def orphan(db):
            key=uuid4().hex*2;identifier=uuid4()
            db.execute(metadata.tables['inventory_transactions'].insert(),tx|dict(id=identifier,transaction_no=key,
                posting_key=key,idempotency_key_hash=key,source_document_id=str(uuid4()),
                ledger_cursor=max(t['ledger_cursor'] for t in rows['inventory_transactions'])+1))
            db.execute(metadata.tables['inventory_movements'].insert(),move|dict(id=uuid4(),transaction_id=identifier))
        reject(kind+':orphan_scrap_transaction',orphan)
        # Test policy precision directly as well: otherwise an earlier hash or
        # quantity binding could mask this guard's own rejection.
        reject(kind+':policy_precision',lambda db:db.execute(text(
            'SELECT public.rsc_assert_scrap_tracking_0165(:account,1.0001,:count,:at)'),
            dict(account=account['id'],count=1 if data['tracking']=='serial' else 0,at=tx['effective_at'])),phase='statement')
        if data['tracking']=='serial':
            sn=next(s for s in rows['stock_scrap_serials'] if s['scrap_line_id']==line['id'])
            for label,movement in [('scrap',move['id']),('inverse',imove['id']),('previous',sn['previous_movement_id']),
                    ('admission',sn['admission_movement_id'])]:
                def remove(db,m=movement):
                    t=metadata.tables['inventory_movement_serials']
                    db.execute(t.delete().where(t.c.movement_id==m,t.c.serial_id==sn['serial_id']))
                reject(kind+':missing_'+label+'_serial',remove)
            def remove_source(db):
                t=metadata.tables['stock_operation_serials']
                db.execute(t.delete().where(t.c.line_id==line['loss_line_id'],t.c.serial_id==sn['serial_id']))
            reject(kind+':missing_loss_line_serial',remove_source)
            prev=byid('inventory_movements',sn['previous_movement_id'])
            ptx=byid('inventory_transactions',prev['transaction_id'])
            reject(kind+':predecessor_cursor_swap',lambda db:update(db,'inventory_transactions',ptx['id'],
                ledger_cursor=max(t['ledger_cursor'] for t in rows['inventory_transactions'])+1))
            def extra_serial(db):
                t=metadata.tables['inventory_movement_serials']
                other_sn=next(s['id'] for s in rows['inventory_serials'] if s['id']!=sn['serial_id'])
                db.execute(t.insert(),dict(movement_id=imove['id'],transaction_id=itx['id'],
                    serial_id=other_sn,created_at=itx['posted_at']))
            reject(kind+':extra_inverse_serial',extra_serial)
    reject('api_private_function',lambda db:db.execute(text('SELECT public.rsc_check_scrap_inventory_edges_0165(:id)'),
        dict(id=lines[0]['id'])),phase='statement',state='42501',engine=api)
    return dict(status='passed',tracking=data['tracking'],successful=successful,rejected=rejected,
        retainedTables=len(rows),retainedRows=sum(map(len,rows.values())),exactSnapshotPreserved=snapshot(owner,metadata)==baseline,
        scope='native candidate edge guards over complete real-service synthetic ledger exports and full model constraints',
        full0164Migration=False,postgresBusinessPosting=False)
