"""Opening-count sources have a separate, bounded, count-authorized file purpose."""

from pathlib import Path
import hashlib
import runpy
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from pglast import parser
import pytest
from sqlalchemy import create_engine, text

from app.formal_services.formal_files import _require_upload_permission
from formal_file_integrity import FileUploadIntentInput, FormalFileError, _prepare_upload


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / 'backend/alembic/versions/20261119_0140_opening_count_source_purpose.py'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _input(*, purpose='opening_count_import', size=1024, mime=MIME, name='期初实盘.xlsx'):
    return FileUploadIntentInput(
        purpose=purpose, original_filename=name, size_bytes=size,
        mime_type=mime, sha256='a' * 64,
    )


def test_source_purpose_requires_xlsx_and_eight_megabytes():
    prepared = _prepare_upload(_input(), maximum_size_bytes=120 * 1024 * 1024)
    assert prepared.purpose == 'opening_count_import'
    assert _prepare_upload(_input(size=8 * 1024 * 1024), maximum_size_bytes=120 * 1024 * 1024).size_bytes == 8 * 1024 * 1024
    for value in (
        _input(size=8 * 1024 * 1024 + 1),
        _input(mime='application/pdf', name='期初实盘.pdf'),
        _input(purpose='stocktake_evidence'),
    ):
        with pytest.raises(FormalFileError):
            _prepare_upload(value, maximum_size_bytes=120 * 1024 * 1024)


def test_source_upload_requires_stocktake_count_permission():
    principal = SimpleNamespace(
        allows=lambda _db, resource, action, **kwargs:
        (resource, action, kwargs.get('field_code')) == ('stocktake', 'count', ''),
    )
    _require_upload_permission(None, principal, 'opening_count_import')
    with pytest.raises(FormalFileError, match='当前账号不能上传该用途文件'):
        _require_upload_permission(None, SimpleNamespace(allows=lambda *_a, **_kw: False), 'opening_count_import')


def test_0140_sql_and_empty_sqlite_roundtrip(tmp_path, monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    assert migration['OLD_FILE_HASH'] == hashlib.sha256(migration['OLD_BODY'].encode()).hexdigest()
    assert migration['NEW_FILE_HASH'] == hashlib.sha256(migration['NEW_BODY'].encode()).hexdigest()
    parser.parse_plpgsql_json(
        'CREATE FUNCTION public.rsc_guard_formal_file_object_0036() RETURNS trigger LANGUAGE plpgsql AS $x$'
        + migration['NEW_BODY'] + '$x$'
    )
    url = f"sqlite+pysqlite:///{tmp_path / 'opening-source.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, migration['revision'])
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['revision']
            names = {row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_files_opening_count_import_%_0140'"
            )}
            assert names == {
                'trg_files_opening_count_import_insert_0140',
                'trg_files_opening_count_import_update_0140',
            }
        command.downgrade(config, migration['down_revision'])
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['down_revision']
            assert connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_files_opening_count_import_%_0140'")) == 0
    finally:
        engine.dispose()
