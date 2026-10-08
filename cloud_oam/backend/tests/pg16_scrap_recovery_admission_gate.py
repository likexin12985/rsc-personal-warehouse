"""Compose real recovery fact/approval/event/authority guards on native PG16.

Identity, scope and custody tables use full model schemas. Stock parents remain
explicit column fixtures: this is not the full migration or stock/SN posting
proof. No old business guard is disabled, replaced or dropped.
"""
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import uuid4

from sqlalchemy import Column, MetaData, String, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.stock_scrap_recovery_schemas import ScrapRecoveryExecute
from app.formal_services.stock_scrap.recovery_facts import intent
from pg16_scrap_recovery_approval_gate import CLOUD, install as install_approvals, row_for, insert, digest
from pg16_scrap_recovery_authority_gate import seed as seed_authority
from pg16_stock_scrap_bindings_gate import snapshot
from scrap_recovery_evidence_fixture import seed_file
from stock_scrap_relational_fixture import fixture_schema, scenario, insert_scrap
from test_formal_access import make_role

STAGES = ('apply', 'regional', 'headquarters', 'execute')
TABLES = dict(zip(STAGES, ('stock_scrap_recovery_requests', 'stock_scrap_recovery_regional_reviews',
    'stock_scrap_recovery_headquarters_reviews', 'stock_scrap_recovery_executions')))


def schema(metadata, full):
    names = {'users', 'people', 'organizations', 'auth_identities', 'roles', 'permissions',
        'role_assignments', 'role_permissions', 'stock_locations', 'custody_assignments'}
    while extra := {f.column.table.name for n in names for f in full.tables[n].foreign_keys} - names:
        names |= extra
    result = MetaData()
    for name in sorted(names):
        full.tables[name].to_metadata(result)
    for table in metadata.tables.values():
        if table.name not in names:
            table.to_metadata(result)
    additions = {
        'stock_operation_orders': ('created_at', 'status', 'source_location_id', 'oam_work_order_id',
            'target_location_id', 'transit_location_id', 'target_custody_assignment_id',
            'loss_headquarters_decision_id', 'loss_correction_decision_id', 'request_hash'),
        'stock_operation_lines': ('operation_id', 'operation_type', 'reserved_account_id', 'material_id',
            'target_condition', 'quantity'),
        'stock_loss_dispositions': ('target_account_id', 'custody_assignment_id', 'return_operation_id', 'created_at'),
        'stock_loss_correction_executions': ('target_account_id', 'custody_assignment_id', 'return_operation_id', 'created_at'),
        'stock_accounts': ('owner_org_id', 'location_id', 'custodian_person_id', 'material_id',
            'condition_code', 'availability_bucket'),
    }
    for name, columns in additions.items():
        for key in columns:
            if key not in result.tables[name].c:
                source = full.tables[name].c[key]
                result.tables[name].append_column(Column(key, source.type, nullable=source.nullable))
    result.tables['inventory_ledger_heads'].append_column(Column('stream_key', String(80),
        nullable=False, server_default='inventory', unique=True))
    return result


def install(owner, api):
    metadata = install_approvals(owner, api, schema_transform=schema)
    migration = (CLOUD/'backend/alembic/versions/20260831_0026_opening_control_reconciliation.py').read_text()
    lock = re.search(r'CREATE FUNCTION public\.\{PG_PRINCIPAL_GRAPH_LOCK_FUNCTION\}.*?\n\$\$', migration, re.S).group()
    lock = lock.replace('{PG_PRINCIPAL_GRAPH_LOCK_FUNCTION}', 'rsc_lock_formal_principal_graph_0026')
    lock = lock.replace('{GUARD_ERROR}', re.search(r'GUARD_ERROR = "([^"]+)"', migration).group(1))
    with owner.begin() as db:
        db.execute(text(lock))
        db.execute(text('REVOKE ALL ON FUNCTION public.rsc_lock_formal_principal_graph_0026(text[]) FROM PUBLIC'))
        for name in ('recovery_authority.sql', 'recovery_admission.sql'):
            db.execute(text((CLOUD/'backend/alembic/stock_scrap_0165'/name).read_text()))
        # Test-only capability to insert the reciprocal inverse and execution.
        # The minimal parent inverse is NOT the real unified stock writer.
        db.execute(text('GRANT SELECT,INSERT ON public.stock_scrap_recovery_executions,'
            'public.stock_loss_disposition_reversals TO star_oam_api'))
        rows = db.execute(text("SELECT c.relname,t.tgname,t.tgdeferrable,t.tginitdeferred,t.tgenabled "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname LIKE 'trg_recovery_admission_%_0165'")).all()
        assert {(r.relname,r.tgname,r.tgdeferrable,r.tginitdeferred,r.tgenabled) for r in rows} == {
            (table,'trg_recovery_admission_'+phase+'_0165',phase=='commit',phase=='commit','A')
            for table in TABLES.values() for phase in ('before','commit')}
        for signature in ('rsc_scrap_recovery_source_0165(uuid)', 'rsc_admit_scrap_recovery_0165()',
                'rsc_assert_scrap_recovery_authority_0165(text,bigint,uuid,uuid,uuid,uuid,text)'):
            f = db.execute(text('SELECT prosecdef,proconfig,pg_get_userbyid(proowner) owner '
                'FROM pg_proc WHERE oid=CAST(:sig AS regprocedure)'),dict(sig='public.'+signature)).one()
            assert f.prosecdef and f.owner=='star_oam_migrator' and f.proconfig==['search_path=pg_catalog, public']
            for role in ('star_oam_api','star_oam_projector','star_oam_edge','edge_inbox'):
                assert not db.scalar(text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),
                    dict(role=role,sig='public.'+signature))
    with Session(owner) as db:
        for role in ('technician','provincial_manager','admin'):
            make_role(db,role)
        db.commit()
    return metadata, seed_authority(owner)


def base(owner, metadata, identity, kind):
    data = scenario(fixture_schema(),kind)
    now = datetime.now(timezone.utc)-timedelta(hours=1)
    person, user = identity['people']['apply'], identity['users']['apply']
    data['ids'].update(users=user,people=person,custody_assignments=identity['custody'])
    line = data['line']
    line.update(created_at=now,custody_assignment_id=identity['custody'])
    loss_id, material = uuid4(), uuid4()
    source_hash = digest(dict(scrap=str(line['id'])))
    parents = []
    for name,row in data['parents']:
        if name in ('users','people','custody_assignments','files'):
            continue
        if name=='stock_accounts':
            row.update(owner_org_id=identity['owner'],location_id=identity['location'],custodian_person_id=person,
                material_id=material,condition_code='new',availability_bucket='frozen')
        if name=='stock_operation_lines':
            row.update(operation_id=loss_id,operation_type='loss_report',reserved_account_id=line['frozen_account_id'],
                material_id=material,target_condition='new',quantity=line['quantity'])
        if name in ('stock_loss_dispositions','stock_loss_correction_executions'):
            row.update(request_hash=source_hash,created_at=now,custody_assignment_id=identity['custody'],
                target_account_id=None,return_operation_id=None)
        if name=='stock_loss_dispositions':
            row['operation_id']=loss_id
        if name=='stock_operation_orders':
            row.update(created_at=now,status='posted',requester_id=person,source_location_id=identity['location'],
                oam_work_order_id=None,target_location_id=None,transit_location_id=None,target_custody_assignment_id=None,
                loss_headquarters_decision_id=line['original_decision_id'],loss_correction_decision_id=line['correction_decision_id'],
                request_hash=source_hash)
            parents.append((name,row|dict(id=loss_id,operation_type='loss_report',status='submitted',
                loss_headquarters_decision_id=None,loss_correction_decision_id=None)))
        parents.append((name,row))
    data['parents']=parents
    with owner.begin() as db:
        insert_scrap(db,metadata,data)
        seed_file(db,metadata,identifier=data['ids']['files'],user=user,person=person,at=now)
        actual = db.execute(text('SELECT * FROM public.rsc_scrap_recovery_source_0165(:id)'),dict(id=line['id'])).one()
        assert tuple(actual)==(identity['owner'],identity['location'],person,source_hash)
    return dict(data=data,time=now,actors=[(identity['users'][s],identity['people'][s]) for s in STAGES],
        source=dict(scrap_line_id=str(line['id']),expected_scrap_request_hash=source_hash),loss_id=loss_id)


def chain(c):
    app=row_for(c,'apply')
    region=row_for(c,'regional',application=app,offset=2)
    hq=row_for(c,'headquarters',application=app,regional=region,offset=3)
    req=ScrapRecoveryExecute(action='execute_scrap_recovery',source=c['source'],request_id=uuid4().hex,
        idempotency_key=uuid4().hex,reason='按准确批准恢复原冻结账户',recovery_request_id=app['id'],
        expected_request_hash=app['request_hash'],headquarters_review_id=hq['id'],
        expected_headquarters_hash=hq['request_hash'],expected_plan_hash='b'*64)
    body=intent(req)
    execution=c['data']['recovery']|dict(created_at=c['time']+timedelta(seconds=4),
        actor_user_id=c['actors'][3][0],actor_person_id=c['actors'][3][1],request_id=req.request_id,
        idempotency_key_hash=digest(req.idempotency_key),request_hash=digest(body),reason=req.reason,command_jsonb=body,
        recovery_request_id=app['id'],headquarters_review_id=hq['id'],expected_headquarters_hash=hq['request_hash'])
    return dict(apply=app,regional=region,headquarters=hq,execute=execution)


def write(db,metadata,c,stage,row):
    if stage!='execute':
        insert(db,metadata,stage,row)
    else:
        db.execute(metadata.tables['stock_loss_disposition_reversals'].insert(),c['data']['inverse'])
        db.execute(metadata.tables[TABLES[stage]].insert(),row)


def run(engines):
    owner,api=engines['star_oam_migrator'],engines['star_oam_api']
    metadata,identity=install(owner,api)
    success,rejected=[],[]

    def prepared(kind,stage):
        c=base(owner,metadata,identity,kind)
        rows=chain(c)
        for earlier in STAGES[:STAGES.index(stage)]:
            with api.begin() as db: write(db,metadata,c,earlier,rows[earlier])
        return c,rows[stage]

    def deny(label,action,*,engine=api,phase='statement',code='23514',message=None):
        before=snapshot(owner,metadata)
        reached='statement'
        try:
            with engine.begin() as db:
                action(db)
                reached='commit'
        except DBAPIError as error:
            assert error.orig.sqlstate==code,(label,str(error.orig))
            assert reached==phase,(label,reached)
            if message: assert message in str(error.orig),(label,str(error.orig))
            assert snapshot(owner,metadata)==before,label
            rejected.append(dict(case=label,phase=phase,sqlstate=code,rollbackPreserved=True))
        else: raise AssertionError(label+': invalid admission accepted')

    for kind in ('original','correction'):
        c=base(owner,metadata,identity,kind)
        rows=chain(c)
        for stage in STAGES:
            with api.begin() as db: write(db,metadata,c,stage,rows[stage])
            success.append(kind+':'+stage)
        for stage in STAGES:
            for attack in ('source_hash','source_id','extra_owner','actor_person','authorization_version'):
                c,row=prepared(kind,stage)
                if attack=='source_hash': row['command_jsonb']['source']['expected_scrap_request_hash']='f'*64
                elif attack=='source_id': row['command_jsonb']['source']['scrap_line_id']=str(uuid4())
                elif attack=='extra_owner': row['command_jsonb']['source']['owner_org_id']=str(identity['other'])
                elif attack=='actor_person': row['actor_person_id']=identity['people']['regional' if stage=='apply' else 'apply']
                else: row['authorization_version']=2
                row['request_hash']=digest(row['command_jsonb'])
                deny(kind+':'+stage+':'+attack,lambda db:write(db,metadata,c,stage,row))
            # COMMIT must reauthorize the new stage, including its exact fact
            # table. Earlier approval actors are not reconsidered.
            c,row=prepared(kind,stage)
            def late(db):
                write(db,metadata,c,stage,row)
                users=metadata.tables['users']
                db.execute(users.update().where(users.c.id==row['actor_user_id']).values(authorization_version=2))
            deny(kind+':'+stage+':late_revocation',late,engine=owner,phase='commit',message='current recovery identity invalid')
        for attack in ('loss_order_person','loss_order_location','loss_line_order','loss_line_account','loss_line_material',
                'loss_line_condition','loss_line_quantity','scrap_order_person','scrap_order_location','scrap_order_status',
                'scrap_order_hash','scrap_order_decision','fact_custody','fact_target','fact_return','fact_time',
                'account_custodian','account_location','account_owner','account_bucket'):
            c,row=prepared(kind,'apply')
            data=c['data']; line=data['line']
            fact='stock_loss_dispositions' if kind=='original' else 'stock_loss_correction_executions'
            fields={
                'loss_order_person':('stock_operation_orders',c['loss_id'],'requester_id',identity['people']['regional']),
                'loss_order_location':('stock_operation_orders',c['loss_id'],'source_location_id',uuid4()),
                'loss_line_order':('stock_operation_lines',line['loss_line_id'],'operation_id',line['operation_id']),
                'loss_line_account':('stock_operation_lines',line['loss_line_id'],'reserved_account_id',data['spares']['stock_accounts']),
                'loss_line_material':('stock_operation_lines',line['loss_line_id'],'material_id',uuid4()),
                'loss_line_condition':('stock_operation_lines',line['loss_line_id'],'target_condition','damaged'),
                'loss_line_quantity':('stock_operation_lines',line['loss_line_id'],'quantity',2),
                'scrap_order_person':('stock_operation_orders',line['operation_id'],'requester_id',identity['people']['regional']),
                'scrap_order_location':('stock_operation_orders',line['operation_id'],'source_location_id',uuid4()),
                'scrap_order_status':('stock_operation_orders',line['operation_id'],'status','submitted'),
                'scrap_order_hash':('stock_operation_orders',line['operation_id'],'request_hash','f'*64),
                'scrap_order_decision':('stock_operation_orders',line['operation_id'],
                    'loss_headquarters_decision_id' if kind=='original' else 'loss_correction_decision_id',uuid4()),
                'fact_custody':(fact,data['ids'][fact],'custody_assignment_id',identity['second_custody']),
                'fact_target':(fact,data['ids'][fact],'target_account_id',line['frozen_account_id']),
                'fact_return':(fact,data['ids'][fact],'return_operation_id',line['operation_id']),
                'fact_time':(fact,data['ids'][fact],'created_at',c['time']-timedelta(seconds=1)),
                'account_custodian':('stock_accounts',line['frozen_account_id'],'custodian_person_id',identity['people']['regional']),
                'account_location':('stock_accounts',line['frozen_account_id'],'location_id',uuid4()),
                'account_owner':('stock_accounts',line['frozen_account_id'],'owner_org_id',identity['other']),
                'account_bucket':('stock_accounts',line['frozen_account_id'],'availability_bucket','available'),
            }
            table,id_,column,value=fields[attack]; table=metadata.tables[table]
            # Corrupt only this synthetic minimal parent in the failing
            # transaction; the whole snapshot must roll back, including edits.
            def corrupt(db):
                db.execute(table.update().where(table.c.id==id_).values(**{column:value}))
                write(db,metadata,c,'apply',row)
            deny(kind+':'+attack,corrupt,engine=owner)
        # Revoked original applicant cannot submit anew, but a still-authorized
        # region may review their previously committed application.
        c,row=prepared(kind,'regional')
        with owner.begin() as db:
            users=metadata.tables['users']
            db.execute(users.update().where(users.c.id==identity['users']['apply']).values(authorization_version=2))
        try:
            with api.begin() as db:write(db,metadata,c,'regional',row)
            success.append(kind+':historical_applicant_not_reauthorized')
        finally:
            with owner.begin() as db:
                db.execute(users.update().where(users.c.id==identity['users']['apply']).values(authorization_version=1))
    deny('api_cannot_call_private_source',lambda db:db.execute(text(
        'SELECT * FROM public.rsc_scrap_recovery_source_0165(:id)'),dict(id=uuid4())),code='42501')
    return dict(status='passed',positive=success,rejected=rejected,admissionTriggers=8,
        scope='real recovery tables and composed approval/evidence/authority; minimal stock parents; no stock/SN posting proof',
        productionMigration=False,productionGrants=False)
