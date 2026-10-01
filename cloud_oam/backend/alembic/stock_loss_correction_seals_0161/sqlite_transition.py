"""Frozen SQLite tooling transition; never admits native correction writes."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json


def verifier(root,data,*,upgraded):
    path=Path(root)/'stock_loss_corrections_0159/sqlite_install.py'
    if hashlib.sha256(path.with_name('frozen-sqlite.json').read_bytes()).hexdigest()!=data['sourceFrozenCatalogSha256']:
        raise ValueError('0161 frozen SQLite predecessor drift')
    spec=importlib.util.spec_from_file_location('_loss_0161_sqlite_predecessor',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if upgraded:
        module.DATA=copy.deepcopy(data['after'])
        module.TABLES=tuple(row['name'] for row in module.DATA['tables'])
    return module


def transition(db,*,up,catalog_path,expected_sha,migration_root):
    raw=Path(catalog_path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:raise ValueError('0161 frozen SQLite catalog digest mismatch')
    data=json.loads(raw)
    if (data['previousRevision'],data['revision'])!=('20261209_0160','20261210_0161'):
        raise ValueError('0161 exact SQLite revision pair required')
    before=verifier(migration_root,data,upgraded=not up)
    expected=data['previousRevision' if up else 'revision']
    before.prepare(db,expected)
    if tuple(row[0] for row in db.exec_driver_sql('SELECT version_num FROM alembic_version'))!=(expected,):
        raise ValueError('0161 exactly one SQLite revision required')
    before.verify(db)
    foreign_keys=db.exec_driver_sql('PRAGMA foreign_keys').scalar()
    columns=','.join('"'+name+'"' for name in data['bindingOldColumns'])
    binding=data['bindingTable']
    original=db.exec_driver_sql('SELECT '+columns+' FROM '+binding+' ORDER BY fact_id').all()
    if up:
        for table in data['addedTables']:
            if db.exec_driver_sql('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).first():
                raise ValueError('0161 SQLite target already exists')
    else:
        for table in data['addedTables']:
            if db.exec_driver_sql('SELECT 1 FROM "'+table+'" LIMIT 1').first():
                raise ValueError('0161 immutable correction request history requires retention')
        if db.exec_driver_sql('SELECT 1 FROM '+binding+' WHERE approval_seal_id IS NOT NULL OR correction_seal_id IS NOT NULL LIMIT 1').first():
            raise ValueError('0161 immutable correction binding history requires retention')
    for statement in data['upgradeStatements' if up else 'downgradeStatements']:db.exec_driver_sql(statement)
    if db.exec_driver_sql('SELECT '+columns+' FROM '+binding+' ORDER BY fact_id').all()!=original:
        raise ValueError('0161 immutable SQLite binding history changed')
    verifier(migration_root,data,upgraded=up).verify(db)
    if db.exec_driver_sql('PRAGMA foreign_key_check').all():raise ValueError('0161 SQLite foreign key violation')
    if db.exec_driver_sql('PRAGMA foreign_keys').scalar()!=foreign_keys:
        raise ValueError('0161 SQLite foreign key enforcement changed')
