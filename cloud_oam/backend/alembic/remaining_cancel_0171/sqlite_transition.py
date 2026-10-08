"""Schema tooling only; all cancellation writes require PostgreSQL 16."""
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_name('sqlite-schema.json').read_bytes()
if sha256(RAW).hexdigest() != 'b62b7cef4848efb64f2b972ba5c17b0ada1e3cfe0a0f3f1820a687f631556b39':
    raise ValueError('0171 frozen SQLite schema digest mismatch')
DATA = json.loads(RAW)
TABLES = ('material_request_remaining_cancellations', 'material_request_remaining_cancellation_lines')


def guards(table):
    return {f'rsc_remaining_cancel_{index}_{action.lower()}_0171':
        f'CREATE TRIGGER rsc_remaining_cancel_{index}_{action.lower()}_0171 BEFORE {action} ON {table} '
        "BEGIN SELECT RAISE(ABORT, '0171 cancellation writes require PostgreSQL16'); END"
        for index in (TABLES.index(table),) for action in ('INSERT', 'UPDATE', 'DELETE')}


def objects(db, table):
    return dict(db.exec_driver_sql(
        'SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL', (table,)).all())


def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0171 SQLite schema tooling required')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = '20261219_0170' if up else '20261220_0171'
    if list(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != [expected]:
        raise ValueError('0171 exact single SQLite predecessor required')
    for table in TABLES:
        wanted = {table: DATA[table], **guards(table)}
        if objects(db, table) != ({} if up else wanted):
            raise ValueError('0171 exact SQLite schema required: ' + table)
        if not up and db.exec_driver_sql(f'SELECT 1 FROM {table} LIMIT 1').first():
            raise ValueError('0171 immutable SQLite history requires retention')
    if up:
        for table in TABLES:
            db.exec_driver_sql(DATA[table])
            for sql in guards(table).values():
                db.exec_driver_sql(sql)
            if objects(db, table) != {table: DATA[table], **guards(table)}:
                raise ValueError('0171 SQLite schema creation mismatch')
    else:
        for table in reversed(TABLES):
            db.exec_driver_sql(f'DROP TABLE {table}')
            if objects(db, table):
                raise ValueError('0171 SQLite removal incomplete')
