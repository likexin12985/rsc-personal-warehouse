"""Exact candidate tables with minimal external parents; no business proof.

Receipt quantities and accepted/damaged serial bindings are relational facts.
Old ledger/audit/authority guards are deliberately absent from this component
fixture. Passing it must never be reported as formal API or migration evidence.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Column, MetaData, Table, UniqueConstraint

from app.return_condition_schema import CASES, EVENTS, SERIALS, TABLE_NAMES, build_schema


def fixture_schema():
    full, facts, _ = build_schema()
    columns, keys = {}, {}
    for table in facts:
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table.name
            if target in TABLE_NAMES:
                continue
            names = tuple(e.column.name for e in fk.elements)
            columns.setdefault(target, set()).update(names)
            keys.setdefault(target, set()).add(names)
    extras = {
        'stock_operation_return_inbound_lines': ('receipt_line_id', 'accepted_qty'),
        'stock_operation_return_inbounds': ('receipt_id', 'plan_jsonb', 'posting_transaction_id'),
        'stock_operation_receipt_lines': ('id', 'receipt_id', 'accepted_qty', 'damaged_qty'),
        'stock_operation_return_inbound_serials': ('receipt_serial_id',),
        'stock_operation_receipt_serials': ('id', 'line_id', 'serial_id', 'result', 'damaged'),
    }
    for name, names in extras.items():
        columns.setdefault(name, set()).update(names)
    meta = MetaData()
    for name, names in columns.items():
        original = full.tables[name]
        Table(name, meta, *(Column(n, original.c[n].type, nullable=original.c[n].nullable,
            primary_key=n == 'id' or name == 'stock_operation_return_inbound_serials' and n in ('line_id', 'serial_id'))
            for n in sorted(names)),
            *(UniqueConstraint(*key) for key in sorted(keys.get(name, ())) if key != ('id',)))
    for table in facts:
        table.to_metadata(meta)
    return meta


class Source:
    def __init__(self, meta, mode):
        self.meta, self.mode = meta, mode
        self.tracked = mode in ('serial', 'lot_and_serial')
        self.ids = {key: uuid4() for key in ('root', 'inbound', 'line', 'receipt', 'receipt_line',
            'original_tx', 'original_move', 'source', 'frozen', 'damaged', 'custody', 'file')}
        self.actors = {role: (str(uuid4()), uuid4()) for role in ('requester', 'region', 'hq', 'other')}
        self.serials = [uuid4() for _ in range(3)]

    def insert(self, db, name, row):
        db.execute(self.meta.tables[name].insert(), row)

    def seed(self, db):
        i = self.ids
        rows = {
            'stock_loss_dispositions': [dict(id=i['root'])],
            'stock_operation_return_inbounds': [dict(id=i['inbound'], receipt_id=i['receipt'],
                plan_jsonb={'schema_version': '1.0'}, posting_transaction_id=i['original_tx'])],
            'stock_operation_return_inbound_lines': [dict(id=i['line'], inbound_id=i['inbound'],
                target_account_id=i['source'], condition_code='new', receipt_line_id=i['receipt_line'], accepted_qty=Decimal(3))],
            'stock_operation_receipt_lines': [dict(id=i['receipt_line'], receipt_id=i['receipt'],
                accepted_qty=Decimal(3), damaged_qty=Decimal(2))],
            'inventory_transactions': [dict(id=i['original_tx'])],
            'inventory_movements': [dict(id=i['original_move'], transaction_id=i['original_tx'],
                from_account_id=i['damaged'], to_account_id=i['source'], quantity=Decimal(3))],
            'stock_accounts': [dict(id=i[key]) for key in ('source', 'frozen', 'damaged')],
            'custody_assignments': [dict(id=i['custody'])],
            'people': [dict(id=p) for _, p in self.actors.values()],
            'users': [dict(id=u) for u, _ in self.actors.values()],
            'files': [dict(id=i['file'])],
            'inventory_serials': [dict(id=s) for s in self.serials],
        }
        for name, values in rows.items():
            for row in values:
                self.insert(db, name, row)
        if self.tracked:
            for index, serial in enumerate(self.serials):
                receipt_serial = uuid4()
                self.insert(db, 'stock_operation_receipt_serials', dict(id=receipt_serial,
                    line_id=i['receipt_line'], serial_id=serial, result='accepted', damaged=index<2))
                self.insert(db, 'stock_operation_return_inbound_serials', dict(line_id=i['line'],
                    serial_id=serial, receipt_serial_id=receipt_serial))

    def context(self, *, actor='requester'):
        user, person = self.actors[actor]
        return dict(id=uuid4(), created_at=datetime.now(timezone.utc), actor_user_id=user, actor_person_id=person,
            authorization_version=1, request_id='request-' + uuid4().hex,
            idempotency_key_hash=uuid4().hex*2, request_hash=uuid4().hex*2,
            reason='Synthetic invariant component', command_jsonb={}, plan_jsonb={}, plan_hash='c'*64)

    def claim(self, db, *, quantity='1', seq=1, serial_indices=(0,), case_changes=None, event_first=False):
        i, qty = self.ids, Decimal(quantity)
        case_id, line, tx, movement = (uuid4() for _ in range(4))
        event = self.context() | dict(case_id=case_id, inbound_line_id=i['line'],
            source_account_id=i['source'], frozen_account_id=i['frozen'], quantity=qty,
            event_sequence=seq, kind='submit', from_state='draft', to_state='awaiting_regional',
            previous_event_id=None, previous_sequence=None, decision_event_id=None, decision_kind=None,
            posting_transaction_id=tx, posting_movement_id=movement, movement_type='freeze',
            from_account_id=i['source'], to_account_id=i['frozen'])
        event['submit_event_id'] = event['id']
        case = dict(id=case_id, operation_type='condition_correction', line_id=line,
            root_disposition_id=i['root'], inbound_id=i['inbound'], inbound_line_id=i['line'],
            original_transaction_id=i['original_tx'], original_movement_id=i['original_move'], original_ledger_cursor=1,
            source_account_id=i['source'], frozen_account_id=i['frozen'], custody_assignment_id=i['custody'],
            recorded_condition='new', target_condition='damaged', quantity=qty, affected_quantity=Decimal(2),
            tracking_mode=self.mode, quantity_scale=0 if self.tracked else 3, allow_fraction=not self.tracked,
            history_hash='d'*64, source_hash='e'*64, source_jsonb={}, submit_event_id=event['id'], submit_kind='submit',
            freeze_transaction_id=tx, freeze_movement_id=movement) | (case_changes or {})
        self.insert(db, 'stock_operation_orders', dict(id=case_id, operation_type='condition_correction', posting_transaction_id=tx))
        self.insert(db, 'stock_operation_lines', dict(id=line, operation_id=case_id, operation_type='condition_correction',
            stock_account_id=i['source'], reserved_account_id=i['frozen'], quantity=qty, target_condition='damaged'))
        self.insert(db, 'inventory_transactions', dict(id=tx))
        self.insert(db, 'inventory_movements', dict(id=movement, transaction_id=tx,
            from_account_id=i['source'], to_account_id=i['frozen'], quantity=qty))
        for name, row in ((EVENTS, event), (CASES, case)) if event_first else ((CASES, case), (EVENTS, event)):
            self.insert(db, name, row)
        if self.tracked:
            for index in serial_indices:
                self.insert(db, SERIALS, dict(case_id=case_id, serial_id=self.serials[index], inbound_line_id=i['line']))
        return event

    def action(self, db, previous, kind, to_state, *, actor='requester', seq=None, changes=None):
        row = previous | self.context(actor=actor) | dict(event_sequence=seq or previous['event_sequence']+1,
            kind=kind, from_state=previous['to_state'], to_state=to_state,
            previous_event_id=previous['id'], previous_sequence=previous['event_sequence'],
            decision_event_id=None, decision_kind=None, posting_transaction_id=None, posting_movement_id=None,
            movement_type=None, from_account_id=None, to_account_id=None)
        if kind in ('execute', 'release'):
            tx, move = uuid4(), uuid4()
            target = self.ids['damaged' if kind == 'execute' else 'source']
            row.update(decision_event_id=previous['id'], decision_kind=previous['kind'],
                posting_transaction_id=tx, posting_movement_id=move,
                movement_type='status_change' if kind == 'execute' else 'unfreeze',
                from_account_id=self.ids['frozen'], to_account_id=target)
            self.insert(db, 'inventory_transactions', dict(id=tx))
            self.insert(db, 'inventory_movements', dict(id=move, transaction_id=tx,
                from_account_id=self.ids['frozen'], to_account_id=target, quantity=previous['quantity']))
        row.update(changes or {})
        self.insert(db, EVENTS, row)
        return row
