"""Frozen tooling-only SQLite transition; native correction writes forbidden."""
from pathlib import Path
import hashlib,json

RAW=(Path(__file__).resolve().parent/'frozen-sqlite.json').read_bytes()
if hashlib.sha256(RAW).hexdigest()!='ba2bed93323e554c6ab5be6ab60e43fef953041d7885c31c2d8f1f53490067d1':
    raise ValueError('0159 SQLite artifact digest mismatch')
DATA=json.loads(RAW)
TABLES=tuple(row['name'] for row in DATA['tables'])


def prepare(db,expected_revision='20261207_0158'):
    if db.dialect.name!='sqlite':raise ValueError('0159 SQLite tooling connection required')
    # The immutable 0118 tooling migration rebuilds referenced tables with
    # foreign_keys OFF. Do not impose a contradictory connection-wide mode
    # on the full graph. All 15 new INSERT/UPDATE/DELETE guards are unconditional
    # in either mode; SQLite never admits native correction business writes.
    if db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()!=expected_revision:
        raise ValueError('0159 candidate requires exact0158 predecessor')
    # Python sqlite legacy transaction mode does not BEGIN for SELECT or DDL.
    # Explicitly begin so any failure rolls back the whole schema transition.
    if not db.connection.driver_connection.in_transaction:db.exec_driver_sql('BEGIN IMMEDIATE')


def verify(db):
    expected={row['name']:('table',row['name'],row['create_sql']) for row in DATA['tables']}
    expected.update({name:('index',None,sql) for name,sql in DATA['indexes'].items()})
    expected.update({name:('trigger',row['table'],row['sql']) for name,row in DATA['triggers'].items()})
    actual={row[0]:(row[1],row[2],row[3]) for row in db.exec_driver_sql(
        'SELECT name,type,tbl_name,sql FROM sqlite_master WHERE tbl_name IN ('+','.join('?' for _ in TABLES)+') AND sql IS NOT NULL',TABLES)}
    if actual.keys()!=expected.keys():raise ValueError('0159 SQLite object set mismatch')
    for name,(kind,table,sql) in expected.items():
        row=actual[name]
        if row[0]!=kind or (table is not None and row[1]!=table) or row[2]!=sql:
            raise ValueError('0159 SQLite exact schema mismatch: '+name)


def install(db):
    prepare(db)
    for name in TABLES:
        if db.exec_driver_sql('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).first():
            raise ValueError('0159 SQLite table already exists: '+name)
    for statement in DATA['statements']:db.exec_driver_sql(statement)
    verify(db)
    for name in TABLES:
        if db.exec_driver_sql('PRAGMA foreign_key_check("'+name+'")').first():
            raise ValueError('0159 SQLite new-table foreign key violation: '+name)


def remove_empty(db,*,expected_revision='20261207_0158'):
    prepare(db,expected_revision);verify(db)
    for name in (*TABLES,'stock_loss_dispositions'):
        if db.exec_driver_sql('SELECT 1 FROM "'+name+'" LIMIT 1').first():
            raise ValueError('0159 immutable business history requires retention: '+name)
    for name in reversed(TABLES):db.exec_driver_sql('DROP TABLE "'+name+'"')
