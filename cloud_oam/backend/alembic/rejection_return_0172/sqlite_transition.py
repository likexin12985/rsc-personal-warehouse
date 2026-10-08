"""Schema tooling only; all rejection return writes require PostgreSQL 16."""
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_name('sqlite-schema.json').read_bytes()
if sha256(RAW).hexdigest() != '232956cbeb65ac40dec62fcc7400665356a502c98dd01d766874bbdb66095e41':
    raise ValueError('0172 frozen SQLite schema digest mismatch')
DATA = json.loads(RAW)
TABLES = ('material_request_rejection_returns', 'material_request_rejection_return_serials')


def guards(table):
    return {f'rsc_rejection_return_{index}_{action.lower()}_0172':
        f'CREATE TRIGGER rsc_rejection_return_{index}_{action.lower()}_0172 BEFORE {action} ON {table} '
        "BEGIN SELECT RAISE(ABORT, '0172 rejection return writes require PostgreSQL16'); END"
        for index in (TABLES.index(table),) for action in ('INSERT', 'UPDATE', 'DELETE')}


def objects(db, table):
    return dict(db.exec_driver_sql(
        'SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL', (table,)).all())


def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0172 SQLite schema tooling required')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = '20261220_0171' if up else '20261221_0172'
    if list(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != [expected]:
        raise ValueError('0172 exact single SQLite predecessor required')
    for table in TABLES:
        wanted = {table: DATA[table], **DATA['_indexes'][table], **guards(table)}
        if objects(db, table) != ({} if up else wanted):
            raise ValueError('0172 exact SQLite schema required: ' + table)
        if not up and db.exec_driver_sql(f'SELECT 1 FROM {table} LIMIT 1').first():
            raise ValueError('0172 immutable SQLite history requires retention')
    if up:
        for table in TABLES:
            db.exec_driver_sql(DATA[table])
            for sql in DATA['_indexes'][table].values():
                db.exec_driver_sql(sql)
            for sql in guards(table).values():
                db.exec_driver_sql(sql)
            if objects(db, table) != {table: DATA[table], **DATA['_indexes'][table], **guards(table)}:
                raise ValueError('0172 SQLite schema creation mismatch')
    else:
        for table in reversed(TABLES):
            db.exec_driver_sql(f'DROP TABLE {table}')
            if objects(db, table):
                raise ValueError('0172 SQLite removal incomplete')
