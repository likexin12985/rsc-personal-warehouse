"""Schema tooling only; all rejection return writes require PostgreSQL 16."""
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_name('sqlite-schema.json').read_bytes()
if sha256(RAW).hexdigest() != '50333782bcaf56c8c708f74e81260f4c3a61275216effda7f81f1ce557f44763':
    raise ValueError('0174 frozen SQLite schema digest mismatch')
DATA = json.loads(RAW)
TABLES = ('material_request_rejection_receipts', 'material_request_rejection_receipt_serials', 'material_request_rejection_receipt_exceptions')


def guards(table):
    return {f'rsc_rejection_receipt_{index}_{action.lower()}_0174':
        f'CREATE TRIGGER rsc_rejection_receipt_{index}_{action.lower()}_0174 BEFORE {action} ON {table} '
        "BEGIN SELECT RAISE(ABORT, '0174 rejection return writes require PostgreSQL16'); END"
        for index in (TABLES.index(table),) for action in ('INSERT', 'UPDATE', 'DELETE')}


def objects(db, table):
    return dict(db.exec_driver_sql(
        'SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL', (table,)).all())


def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0174 SQLite schema tooling required')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = '20261222_0173' if up else '20261223_0174'
    if list(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != [expected]:
        raise ValueError('0174 exact single SQLite predecessor required')
    for table in TABLES:
        wanted = {table: DATA[table], **DATA['_indexes'][table], **guards(table)}
        if objects(db, table) != ({} if up else wanted):
            raise ValueError('0174 exact SQLite schema required: ' + table)
        if not up and db.exec_driver_sql(f'SELECT 1 FROM {table} LIMIT 1').first():
            raise ValueError('0174 immutable SQLite history requires retention')
    if up:
        for table in TABLES:
            db.exec_driver_sql(DATA[table])
            for sql in DATA['_indexes'][table].values():
                db.exec_driver_sql(sql)
            for sql in guards(table).values():
                db.exec_driver_sql(sql)
            if objects(db, table) != {table: DATA[table], **DATA['_indexes'][table], **guards(table)}:
                raise ValueError('0174 SQLite schema creation mismatch')
    else:
        for table in reversed(TABLES):
            db.exec_driver_sql(f'DROP TABLE {table}')
            if objects(db, table):
                raise ValueError('0174 SQLite removal incomplete')
