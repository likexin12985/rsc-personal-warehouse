"""Schema-only SQLite transition; closure business writes require PostgreSQL16.

The explicit rejection guard prevents this tooling path from being mistaken for
production authority, quantity and audit enforcement. No predecessor rows change.
"""
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_name('sqlite-schema.json').read_bytes()
if sha256(RAW).hexdigest() != 'e56170d5d2abb626f907e4474fd1cc963870020659f7848fb2b4c8cdc177f0a5':
    raise ValueError('0169 frozen SQLite schema digest mismatch')
DATA = json.loads(RAW)
TABLE = 'material_request_closures'
GUARDS = {f'rsc_closure_sqlite_{operation.lower()}_0169':
    f'CREATE TRIGGER rsc_closure_sqlite_{operation.lower()}_0169 BEFORE {operation} ON {TABLE} '
    "BEGIN SELECT RAISE(ABORT, '0169 closure writes require PostgreSQL16'); END"
    for operation in ('INSERT', 'UPDATE', 'DELETE')}


def objects(db):
    return {name: sql for name, sql in db.exec_driver_sql(
        "SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL", (TABLE,))}


def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0169 SQLite schema tooling connection required')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = '20261217_0168' if up else '20261218_0169'
    if list(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != [expected]:
        raise ValueError('0169 exact single SQLite predecessor required')
    wanted = {TABLE: DATA['create'], **GUARDS}
    if up:
        if objects(db):
            raise ValueError('0169 predecessor must not contain closure objects')
        db.exec_driver_sql(DATA['create'])
        for sql in GUARDS.values():
            db.exec_driver_sql(sql)
        if objects(db) != wanted:
            raise ValueError('0169 exact SQLite closure schema required')
    else:
        if objects(db) != wanted:
            raise ValueError('0169 exact SQLite closure schema required')
        if db.exec_driver_sql(f'SELECT 1 FROM {TABLE} LIMIT 1').first():
            raise ValueError('0169 immutable SQLite history requires retention')
        db.exec_driver_sql(f'DROP TABLE {TABLE}')
        if objects(db):
            raise ValueError('0169 SQLite closure removal incomplete')
