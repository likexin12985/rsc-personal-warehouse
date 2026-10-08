"""PG16 command fingerprints and common-header bindings, not live authority."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.formal_services import inventory_posting as posting
from app.return_condition_guards import statements
from pg16_return_condition_invariants_gate import snapshot
from return_condition_identity_fixture import IdentitySource, identity_schema


def run(owner,api):
    meta=identity_schema(); meta.create_all(owner)
    migration=Path(__file__).resolve().parents[1]/'alembic/versions/20260831_0026_opening_control_reconciliation.py'
    canonical=re.search(r'CREATE FUNCTION public\.\{PG_CANONICAL_JSON_FUNCTION\}.*?\n\$\$',migration.read_text(),re.S).group()
    canonical=canonical.replace('{PG_CANONICAL_JSON_FUNCTION}','rsc_canonical_reconciliation_json_0026').replace('{{','{').replace('}}','}')
    ddl=statements(identity=True)
    with owner.begin() as db:
        db.execute(text(canonical))
        for sql in ddl: db.execute(text(sql))
        for name in meta.tables:
            db.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON public.'+name+' TO star_oam_api')
    rejected=[]; positive=[]

    def source(mode):
        s=IdentitySource(meta,mode)
        with owner.begin() as db: s.seed(db)
        return s

    def refuse(name,action,*,messages=('condition',),engine=None):
        before=snapshot(owner,meta); completed=False
        try:
            with (engine or api).begin() as db:
                action(db); completed=True
        except DBAPIError as error:
            assert completed and error.orig.sqlstate=='23514',(name,str(error.orig))
            assert any(message in str(error.orig) for message in messages),(name,str(error.orig))
            assert snapshot(owner,meta)==before,name+': rollback changed facts'
            rejected.append(dict(name=name,atCommit=True,sqlstate=error.orig.sqlstate,allRowsPreserved=True))
        else: raise AssertionError(name+': forged context committed')

    for mode in ('none','lot','serial','lot_and_serial'):
        s=source(mode)
        with api.begin() as db:
            initial=s.claim(db,quantity='1' if s.tracked else '0.375')
            back=s.action(db,initial,'return_evidence','needs_evidence',actor='region')
            supplement=s.action(db,back,'supplement','awaiting_regional')
            region=s.action(db,supplement,'verify_region','awaiting_headquarters',actor='region')
            hq=s.action(db,region,'approve_hq','approved',actor='hq')
            s.action(db,hq,'execute','executed')
        positive.append(mode+':evidence_rework_approval_execute')
        s=source(mode)
        with api.begin() as db:
            initial=s.claim(db)
            withdrawn=s.action(db,initial,'withdraw','cancelled_pending_release')
            s.action(db,withdrawn,'release','released_cancelled')
            s.claim(db,seq=4)
        positive.append(mode+':release_reclaim')
        s=source(mode)
        header_changes=dict(operation_no='wrong',requester_id=s.actors['other'][1],actor_user_id=s.actors['other'][0],
            authorization_version=9,request_id='another-request',request_hash='a'*64,plan_hash='a'*64,
            source_location_id=uuid4(),reason='Another reason',created_at=s.origin_time)
        for field,value in header_changes.items():
            s.rewrite=lambda name,row,f=field,v=value: row|{f:v} if name=='stock_operation_orders' else row
            refuse(mode+':header_'+field,lambda db:s.claim(db))
        for field,value in dict(line_no=2,material_id=uuid4(),reason='Another reason',created_at=s.origin_time).items():
            s.rewrite=lambda name,row,f=field,v=value: row|{f:v} if name=='stock_operation_lines' else row
            refuse(mode+':line_'+field,lambda db:s.claim(db),messages=('common operation identity',))
        for field,value in dict(transaction_no='wrong',posting_key='stock-condition:wrong',
            idempotency_key_hash='a'*64,request_hash='a'*64).items():
            s.rewrite=lambda name,row,f=field,v=value: row|{f:v} if name=='inventory_transactions' else row
            refuse(mode+':inventory_'+field,lambda db:s.claim(db),messages=('canonical inventory request',))
        for variant in ('plan_quantity','plan_source','command_actor','command_reason','command_extra','previous_hash'):
            def rehash(name,row,variant=variant):
                if name not in ('stock_operation_orders','stock_condition_events'): return row
                row=deepcopy(row)
                if variant=='plan_quantity': row['plan_jsonb']['quantity']='999'
                elif variant=='plan_source': row['plan_jsonb']['source_account_id']=str(uuid4())
                elif variant=='command_actor': row['command_jsonb']['actor']['person_id']=str(s.actors['other'][1])
                elif variant=='command_reason': row['command_jsonb']['reason']='A forged reason'
                elif variant=='command_extra': row['command_jsonb']['permission_override']=True
                else: row['command_jsonb']['previous_request_hash']='a'*64
                row['plan_hash']=posting._canonical_hash(row['plan_jsonb'])
                row['command_jsonb']['expected_plan_hash']=row['plan_hash']
                row['request_hash']=posting._canonical_hash(row['command_jsonb'])
                return row
            s.rewrite=rehash
            refuse(mode+':rehash_'+variant,lambda db:s.claim(db),messages=('canonical command identity',))
        s.rewrite=lambda name,row: None if name=='stock_condition_files' else row
        refuse(mode+':missing_evidence_link',lambda db:s.claim(db),messages=('evidence references',))
        s.rewrite=lambda name,row: row
        with api.begin() as db: initial=s.claim(db)
        def late_header(db):
            t=meta.tables['stock_operation_orders']
            db.execute(t.update().where(t.c.id==initial['case_id']).values(reason='tampered later'))
        refuse(mode+':late_header_only',late_header,messages=('common operation identity','common submission context'))
        def check_then_change(db):
            db.execute(text('SELECT public.rsc_condition_check_identity(:id)'),{'id':initial['case_id']})
            late_header(db)
        refuse(mode+':proof_then_late_header',check_then_change,engine=owner)
        if s.tracked:
            def late_common_sn(db):
                t=meta.tables['stock_operation_serials']
                db.execute(t.update().where(t.c.line_id==s.cases[initial['case_id']]['line_id']).values(sku_verified=False))
            refuse(mode+':late_common_sn_proof',late_common_sn,messages=('common operation serial proof',))
        with owner.connect() as db:
            tx=db.execute(select(meta.tables['inventory_transactions']).where(
                meta.tables['inventory_transactions'].c.id==initial['posting_transaction_id'])).mappings().one()
            assert tx['idempotency_key_hash']!=initial['idempotency_key_hash']
            assert tx['request_hash']!=initial['request_hash']
    return dict(passed=True,rejected=rejected,positive=positive,ddlSha256=hashlib.sha256('\n'.join(ddl).encode()).hexdigest(),
        canonicalFunctionSource=str(migration),canonicalFunctionSha256=hashlib.sha256(canonical.encode()).hexdigest(),
        externalParents='minimal synthetic inventory/common headers/files; completed uploads and authority not proved',
        realApiRoleSql=True,rehashingDoesNotBypassFacts=True,liveAuthority=False,auditOutboxRequestRecovery=False,
        formalMigration=False,productionAcceptance=False)
