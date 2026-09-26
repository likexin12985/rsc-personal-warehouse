"""Shared original-source and original-import admission lock ordering."""
from hashlib import sha256
from sqlalchemy import select, text
from ..foundation_models import OpeningImportCommandSeal
from .formal_files import _take_file_advisory_lock
from .opening_count_import_jobs import OpeningCountImportJobError


def lock_import_admission(db, source_file_id, key):
    _take_file_advisory_lock(db, source_file_id)
    if db.get_bind().dialect.name == 'postgresql':
        coordinate = int.from_bytes(sha256(('opening-import-admission:'+key).encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:coordinate)'), {'coordinate':coordinate})


def require_unsealed_import(db, source_file_id, key):
    if db.scalar(select(OpeningImportCommandSeal.id).where(
        (OpeningImportCommandSeal.source_file_id == source_file_id) | (OpeningImportCommandSeal.import_key_hash == key))) is not None:
        raise OpeningCountImportJobError('opening_import_permanently_sealed', 409, '原导入已永久终结，请查询原终结记录')
