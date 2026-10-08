"""Frozen SQLite schema tooling; scrap business writes require PostgreSQL 16.

Rebuild only the five extended parents, retaining all old columns and every
predecessor index/trigger/view. The caller owns commit/rollback. No application
metadata or historical migration is imported to determine installed DDL.
"""
from hashlib import sha256
import json
from pathlib import Path

RAW = Path(__file__).with_name('sqlite-catalog.json').read_bytes()
if sha256(RAW).hexdigest() != 'af96dc9d761715e43b11a1069f5c4c7158cfe7f9a118b46d52a2f78cc1da0df1':
    raise ValueError('0165 frozen SQLite catalog digest mismatch')
DATA = json.loads(RAW)
TABLES = (*DATA['parentTables'], *DATA['newTables'])


def quote(name):
    if not name or not name.replace('_', '').isalnum() or name[0].isdigit():
        raise ValueError('0165 unexpected SQLite identifier')
    return '"' + name + '"'


def objects(db):
    sql = ('SELECT name,type,tbl_name,sql FROM sqlite_master WHERE tbl_name IN (' +
           ','.join('?' for _ in TABLES) + ') AND sql IS NOT NULL ORDER BY name')
    return {name: dict(type=kind, table=table, sql=ddl)
            for name, kind, table, ddl in db.exec_driver_sql(sql, TABLES)}


def table_identity(sql):
    """Ignore only table-constraint order from old SQLAlchemy set iteration.

    Retain column order, every fragment byte (including quoted literals),
    duplicate constraints, table options and identifier quoting. Never apply
    this normalization to indexes, triggers, PostgreSQL or business records.
    """
    start, end = sql.index('('), sql.rindex(')')
    body = sql[start + 1:end]
    fragments, offset, depth, quoted, i = [], 0, 0, None, 0
    while i < len(body):
        char = body[i]
        if quoted:
            if char == quoted:
                if i + 1 < len(body) and body[i + 1] == quoted:
                    i += 1
                else:
                    quoted = None
        elif char in ('"', "'", '`', '['):
            quoted = ']' if char == '[' else char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and not depth:
            fragments.append(body[offset:i].strip())
            offset = i + 1
        if depth < 0:
            raise ValueError('0165 invalid SQLite table expression')
        i += 1
    if depth or quoted:
        raise ValueError('0165 unbalanced SQLite table expression')
    fragments.append(body[offset:].strip())
    constraints = ('CONSTRAINT ', 'PRIMARY KEY ', 'PRIMARY KEY(', 'FOREIGN KEY', 'UNIQUE ', 'UNIQUE(', 'CHECK ', 'CHECK(')
    return (sql[:start].strip(),
            tuple(fragment for fragment in fragments if not fragment.startswith(constraints)),
            tuple(sorted(fragment for fragment in fragments if fragment.startswith(constraints))),
            sql[end + 1:].strip())


def object_identity(row):
    if row is None or row['type'] != 'table':
        return row
    return dict(row, sql=table_identity(row['sql']))


def verify(db, side):
    actual, expected = objects(db), DATA[side]
    changed = sorted(name for name in actual.keys() | expected.keys()
                     if object_identity(actual.get(name)) != object_identity(expected.get(name)))
    if changed:
        raise ValueError('0165 exact SQLite ' + side + ' catalog mismatch: ' + ','.join(changed))


def facts(db):
    """Bounded-memory fingerprints of all retained parent columns and rows."""
    result = {}
    for name, columns in DATA['oldColumns'].items():
        keys = [row[1] for row in sorted(db.exec_driver_sql('PRAGMA table_info(' + quote(name) + ')'),
                                        key=lambda row: row[5]) if row[5]]
        if not keys:
            raise ValueError('0165 predecessor primary key required')
        sql = ('SELECT ' + ','.join(map(quote, columns)) + ' FROM ' + quote(name) +
               ' ORDER BY ' + ','.join(map(quote, keys)))
        digest, count = sha256(), 0
        for row in db.exec_driver_sql(sql):
            encoded = json.dumps(list(row), ensure_ascii=False, separators=(',', ':'),
                                 default=lambda value: {'bytes': value.hex()}).encode()
            digest.update(str(len(encoded)).encode() + b':' + encoded)
            count += 1
        result[name] = (count, digest.hexdigest())
    return result


def transition(db, *, up):
    if db.dialect.name != 'sqlite':
        raise ValueError('0165 SQLite tooling connection required')
    # Existing tooling migrations rebuild referenced parents with enforcement
    # off. Do not silently change a caller's connection-wide enforcement mode.
    if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise ValueError('0165 SQLite table rebuild requires explicit foreign_keys OFF')
    if not db.connection.driver_connection.in_transaction:
        db.exec_driver_sql('BEGIN IMMEDIATE')
    expected = DATA['previousRevision' if up else 'revision']
    if tuple(db.exec_driver_sql('SELECT version_num FROM alembic_version').scalars()) != (expected,):
        raise ValueError('0165 exact single SQLite predecessor revision required')
    before, after = ('before', 'after') if up else ('after', 'before')
    verify(db, before)
    if not up:
        for name in DATA['newTables']:
            if db.exec_driver_sql('SELECT 1 FROM ' + quote(name) + ' LIMIT 1').first():
                raise ValueError('0165 immutable SQLite history requires retention: ' + name)
        for name, columns in DATA['addedColumns'].items():
            condition = ' OR '.join(quote(column) + ' IS NOT NULL' for column in columns)
            if db.exec_driver_sql('SELECT 1 FROM ' + quote(name) + ' WHERE ' + condition + ' LIMIT 1').first():
                raise ValueError('0165 immutable SQLite binding requires retention: ' + name)
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise ValueError('0165 predecessor SQLite foreign key violation')
    original = facts(db)
    # A trigger/view on an unrelated table may reference a rebuilt parent.
    # Retain its exact text, temporarily remove it within this transaction,
    # and restore it only after all original table names exist again.
    retained = list(db.exec_driver_sql("SELECT type,name,sql FROM sqlite_master "
                                      "WHERE type IN ('trigger','view') ORDER BY type,name"))
    for kind, name, sql in retained:
        db.exec_driver_sql('DROP ' + kind.upper() + ' ' + quote(name))
    if up:
        for name in DATA['newTables']:
            db.exec_driver_sql(DATA['create'][name])
    for name in DATA['parentTables']:
        columns = ','.join(map(quote, DATA['oldColumns'][name]))
        temporary = '_rsc_0165_' + name
        db.exec_driver_sql('CREATE TEMP TABLE ' + quote(temporary) + ' AS SELECT ' + columns + ' FROM ' + quote(name))
        db.exec_driver_sql('DROP TABLE ' + quote(name))
        db.exec_driver_sql(DATA[after][name]['sql'])
        db.exec_driver_sql('INSERT INTO ' + quote(name) + ' (' + columns + ') SELECT ' + columns + ' FROM ' + quote(temporary))
        db.exec_driver_sql('DROP TABLE temp.' + quote(temporary))
    if not up:
        for name in reversed(DATA['newTables']):
            db.exec_driver_sql('DROP TABLE ' + quote(name))
    for row in DATA[after].values():
        if row['type'] == 'index':
            db.exec_driver_sql(row['sql'])
    for kind, name, sql in retained:
        if up or name not in DATA['guards']:
            db.exec_driver_sql(sql)
    if up:
        for sql in DATA['guards'].values():
            db.exec_driver_sql(sql)
    verify(db, after)
    if facts(db) != original:
        raise ValueError('0165 immutable SQLite predecessor rows changed')
    expected_retained = {(kind, name, sql) for kind, name, sql in retained if name not in DATA['guards']}
    actual_retained = {(kind, name, sql) for kind, name, sql in db.exec_driver_sql(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','view')") if name not in DATA['guards']}
    if actual_retained != expected_retained:
        raise ValueError('0165 unrelated SQLite triggers or views changed')
    if db.exec_driver_sql('PRAGMA foreign_key_check').first():
        raise ValueError('0165 SQLite foreign key violation')
    if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise ValueError('0165 SQLite foreign key enforcement changed')
