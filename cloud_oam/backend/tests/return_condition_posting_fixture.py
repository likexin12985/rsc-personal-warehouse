"""Inventory-edge component fixtures; these are not posted business histories."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Table

from app.database import Base
from app.return_condition_schema import EVENTS, SERIALS
from return_condition_guard_fixture import Source, fixture_schema


def posting_schema():
    meta = fixture_schema()
    extra = {
        'stock_accounts': ('owner_org_id','custodian_person_id','location_id','material_id','lot_id','condition_code','availability_bucket'),
        'stock_operation_return_inbound_lines': ('source_account_id','material_id','lot_id','line_no'),
        'inventory_transactions': ('movement_type','source_document_type','source_document_id','posting_key','actor_user_id',
            'status','effective_at','posted_at','created_at','ledger_cursor','reversed_transaction_id'),
        'inventory_movements': ('line_no','external_boundary_code','created_at'),
        'inventory_serials': ('material_id','lot_id'),
        'inventory_movement_serials': ('movement_id','transaction_id','serial_id','created_at'),
    }
    for name, columns in extra.items():
        if name not in meta.tables:
            Table(name, meta)
        target, original = meta.tables[name], Base.metadata.tables[name]
        for key in columns:
            if key not in target.c:
                col = original.c[key]
                target.append_column(Column(key,col.type,nullable=col.nullable,
                    primary_key=name=='inventory_movement_serials' and key in ('movement_id','serial_id')))
    return meta


class PostingSource(Source):
    def __init__(self, meta, mode):
        super().__init__(meta, mode)
        from uuid import uuid4
        self.dimensions = dict(owner_org_id=uuid4(),location_id=uuid4(),material_id=uuid4(),
            custodian_person_id=self.actors['requester'][1],lot_id=uuid4() if 'lot' in mode else None)
        self.transactions, self.submissions, self.selected = {}, {}, {}
        self.cursor = 10
        self.origin_time = datetime.now(timezone.utc)-timedelta(hours=1)

    def seed(self, db):
        super().seed(db)
        if self.tracked:
            for serial in self.serials:
                super().insert(db,'inventory_movement_serials',dict(movement_id=self.ids['original_move'],
                    transaction_id=self.ids['original_tx'],serial_id=serial,created_at=self.origin_time))

    def context(self, *, actor='requester'):
        self.pending_context = super().context(actor=actor)
        return self.pending_context

    def claim(self, db, **kwargs):
        self.kind = 'submit'
        return super().claim(db, **kwargs)

    def action(self, db, previous, kind, to_state, **kwargs):
        self.kind = kind
        return super().action(db, previous, kind, to_state, **kwargs)

    def insert(self, db, name, row):
        i = self.ids
        if name=='stock_accounts':
            row = self.dimensions | dict(condition_code='damaged' if row['id']==i['damaged'] else 'new',
                availability_bucket='frozen' if row['id']==i['frozen'] else 'available') | row
        elif name=='inventory_serials':
            row = dict(material_id=self.dimensions['material_id'],lot_id=self.dimensions['lot_id']) | row
        elif name=='stock_operation_return_inbound_lines':
            row = dict(source_account_id=i['damaged'],material_id=self.dimensions['material_id'],
                lot_id=self.dimensions['lot_id'],line_no=1) | row
        elif name=='inventory_transactions':
            original = row['id']==i['original_tx']
            context = self.pending_context if not original else None
            at = self.origin_time if original else context['created_at']
            row = dict(movement_type='transfer' if original else {'submit':'freeze','execute':'status_change','release':'unfreeze'}[self.kind],
                source_document_type='stock_return_receipt_inbound' if original else 'stock_condition_event',
                source_document_id=str(i['inbound'] if original else context['id']),
                posting_key='original:'+str(row['id']) if original else 'stock-condition:'+str(context['id']),
                actor_user_id=self.actors['requester'][0] if original else context['actor_user_id'],status='posted',
                effective_at=at,created_at=at,posted_at=at,ledger_cursor=1 if original else self.cursor,
                reversed_transaction_id=None) | row
            self.cursor += 10
            self.transactions[row['id']] = row
        elif name=='inventory_movements':
            row = dict(line_no=1,external_boundary_code=None,
                created_at=self.transactions[row['transaction_id']]['posted_at']) | row
        super().insert(db,name,row)
        if name==EVENTS:
            if row['kind']=='submit':
                self.submissions[row['case_id']] = row
                self.selected[row['case_id']] = []
            elif row['kind'] in ('execute','release'):
                for serial in self.selected[row['case_id']]:
                    super().insert(db,'inventory_movement_serials',dict(movement_id=row['posting_movement_id'],
                        transaction_id=row['posting_transaction_id'],serial_id=serial,created_at=row['created_at']))
        elif name==SERIALS:
            event = self.submissions[row['case_id']]
            self.selected[row['case_id']].append(row['serial_id'])
            super().insert(db,'inventory_movement_serials',dict(movement_id=event['posting_movement_id'],
                transaction_id=event['posting_transaction_id'],serial_id=row['serial_id'],created_at=event['created_at']))
