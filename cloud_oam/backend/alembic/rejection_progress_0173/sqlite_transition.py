"""Exact empty SQLite schema tooling; refusal-return business requires PG16."""
from hashlib import sha256
import json
from pathlib import Path
RAW = Path(__file__).with_name('sqlite-schema.json').read_bytes()
if sha256(RAW).hexdigest() != 'd74e3463272c7dada442b2338ed52d44a6308d0feaa7dc073cd367690689a98e':
    raise ValueError('0173 frozen SQLite schema digest mismatch')
DATA = json.loads(RAW)
SERIALS = 'material_request_rejection_return_serials'
PROGRESS = 'material_request_rejection_progress'

def objects(db, table):
    return dict(db.exec_driver_sql('SELECT name,sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL', (table,)).all())

def create(db, table, side):
    wanted = DATA[side][table]
    if wanted:
        db.exec_driver_sql(wanted[table])
        for name, sql in wanted.items():
            if name != table: db.exec_driver_sql(sql)

def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0173 SQLite schema tooling required')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = '20261221_0172' if up else '20261222_0173'
    if list(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != [expected]:
        raise ValueError('0173 exact single SQLite predecessor required')
    before, after = ('before', 'after') if up else ('after', 'before')
    for table in (SERIALS, PROGRESS):
        if objects(db, table) != DATA[before][table]:
            raise ValueError('0173 exact SQLite schema required: ' + table)
        if DATA[before][table] and db.exec_driver_sql(f'SELECT 1 FROM {table} LIMIT 1').first():
            raise ValueError('0173 immutable SQLite history requires retention')
    if not up: db.exec_driver_sql('DROP TABLE ' + PROGRESS)
    db.exec_driver_sql('DROP TABLE ' + SERIALS)
    create(db, SERIALS, after)
    if up: create(db, PROGRESS, after)
    for table in (SERIALS, PROGRESS):
        if objects(db, table) != DATA[after][table]:
            raise ValueError('0173 exact SQLite transition result required: ' + table)
