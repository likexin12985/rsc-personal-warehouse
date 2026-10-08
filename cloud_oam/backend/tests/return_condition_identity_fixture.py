"""Canonical candidate records; external parents/evidence remain synthetic."""
from uuid import uuid4

from sqlalchemy import Column, Table

from app.database import Base
from app.formal_services.stock_loss_corrections.return_condition_identity import identity, inventory_identity
from return_condition_posting_fixture import PostingSource, posting_schema


def identity_schema():
    meta=posting_schema()
    extra={
        'inventory_transactions': ('transaction_no','idempotency_key_hash','request_hash'),
        'stock_operation_orders': ('created_at','operation_no','status','source_location_id','requester_id',
            'actor_user_id','authorization_version','reason','request_id','idempotency_key_hash','request_hash',
            'plan_hash','command_jsonb','plan_jsonb'),
        'stock_operation_lines': ('line_no','material_id','reason','created_at'),
        'stock_operation_serials': ('id','line_id','serial_id','sku_verified','qr_verified','created_at'),
    }
    for name,columns in extra.items():
        if name not in meta.tables: Table(name,meta)
        for key in columns:
            if key not in meta.tables[name].c:
                col=Base.metadata.tables[name].c[key]
                meta.tables[name].append_column(Column(key,col.type,nullable=col.nullable,primary_key=key=='id'))
    return meta


class Collector:
    def __init__(self): self.rows=[]
    def execute(self, statement, row): self.rows.append((statement.table.name,dict(row)))


class IdentitySource(PostingSource):
    def __init__(self,meta,mode):
        super().__init__(meta,mode)
        self.cases={}
        self.rewrite=lambda name,row: row

    def seed(self,db):
        collected=Collector()
        super().seed(collected)
        for name,row in collected.rows:
            if name=='inventory_transactions':
                row.update(transaction_no='SYNTHETIC-'+str(row['id']),idempotency_key_hash='0'*64,request_hash='0'*64)
            db.execute(self.meta.tables[name].insert(),row)

    def claim(self,db,**kwargs):
        collected=Collector()
        event=super().claim(collected,**kwargs)
        case=next(row for name,row in collected.rows if name=='stock_condition_cases')
        self.cases[case['id']]=case
        self.complete(db,collected,event,previous_hash=None)
        return event

    def action(self,db,previous,kind,to_state,**kwargs):
        collected=Collector()
        event=super().action(collected,previous,kind,to_state,**kwargs)
        self.complete(db,collected,event,previous_hash=previous['request_hash'])
        return event

    def evidence_files(self,db,event):
        files=[]
        if event['kind'] in ('submit','verify_region','supplement'):
            file_id=uuid4()
            db.execute(self.meta.tables['files'].insert(),{'id':file_id})
            files=[dict(file_id=file_id,metadata_sha256='f'*64)]
        return files

    def complete(self,db,collected,event,*,previous_hash):
        case=self.cases[event['case_id']]
        serials=self.selected[event['case_id']]
        files=self.evidence_files(db,event)
        binding=identity(case,event,serial_ids=serials,evidence=files,previous_request_hash=previous_hash)
        event.update(plan_jsonb=binding.plan,plan_hash=binding.plan_hash,command_jsonb=binding.command,request_hash=binding.request_hash)
        if event['kind'] in ('submit','execute','release'):
            command,key_hash,request_hash=inventory_identity(event,serials)
            self.transactions[event['posting_transaction_id']].update(transaction_no=command.transaction_no,
                posting_key=command.posting_key,idempotency_key_hash=key_hash,request_hash=request_hash)
        for name,row in collected.rows:
            if name=='stock_condition_events': row=event
            elif name=='inventory_transactions': row=self.transactions[row['id']]
            elif name=='stock_operation_orders':
                keys=('created_at','actor_user_id','authorization_version','reason','request_id','idempotency_key_hash',
                    'request_hash','plan_hash','command_jsonb','plan_jsonb','posting_transaction_id')
                row=row|{k:event[k] for k in keys}|dict(operation_no='COND-'+case['id'].hex.upper(),status='submitted',
                    source_location_id=self.dimensions['location_id'],requester_id=event['actor_person_id'])
            elif name=='stock_operation_lines':
                row=row|dict(line_no=1,material_id=self.dimensions['material_id'],reason=event['reason'],created_at=event['created_at'])
            rewritten=self.rewrite(name,row)
            if rewritten is not None:
                db.execute(self.meta.tables[name].insert(),rewritten)
        if event['kind']=='submit':
            for serial in serials:
                row=self.rewrite('stock_operation_serials',dict(id=uuid4(),line_id=case['line_id'],
                    serial_id=serial,sku_verified=True,qr_verified=True,created_at=event['created_at']))
                if row is not None: db.execute(self.meta.tables['stock_operation_serials'].insert(),row)
        for file in files:
            row=self.rewrite('stock_condition_files',file|dict(event_id=event['id'],created_at=event['created_at']))
            if row is not None: db.execute(self.meta.tables['stock_condition_files'].insert(),row)
