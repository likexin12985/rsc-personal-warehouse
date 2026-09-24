"""XLSX reports have one private file purpose and no generic upload path."""

from pathlib import Path
import hashlib
import runpy

from alembic import command
from alembic.config import Config
from pglast import parser
import pytest
from sqlalchemy import create_engine, text

from app.formal_services.formal_files import _require_upload_permission
from formal_file_integrity import FileUploadIntentInput, FormalFileError, _prepare_upload


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / 'backend/alembic/versions/20261116_0137_report_export_file_purpose.py'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def test_report_purpose_is_xlsx_only_and_generic_upload_is_closed():
    prepared = _prepare_upload(FileUploadIntentInput(
        purpose='inventory_report_export', original_filename='库存.xlsx',
        size_bytes=1024, mime_type=MIME, sha256='a'*64,
    ), maximum_size_bytes=20*1024*1024)
    assert prepared.purpose == 'inventory_report_export'
    for purpose, mime, name in (
        ('inventory_report_export','application/pdf','report.pdf'),
        ('stocktake_evidence',MIME,'report.xlsx'),
    ):
        with pytest.raises(FormalFileError, match='文件用途与类型不一致'):
            _prepare_upload(FileUploadIntentInput(
                purpose=purpose, original_filename=name,
                size_bytes=1024, mime_type=mime, sha256='a'*64,
            ), maximum_size_bytes=20*1024*1024)
    with pytest.raises(FormalFileError, match='报表文件只能由后台任务生成'):
        _require_upload_permission(None, None, 'inventory_report_export')


def test_0137_sql_and_empty_sqlite_roundtrip(tmp_path, monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    assert migration['OLD_FILE_HASH'] == hashlib.sha256(migration['OLD_BODY'].encode()).hexdigest()
    assert migration['NEW_FILE_HASH'] == hashlib.sha256(migration['NEW_BODY'].encode()).hexdigest()
    parser.parse_plpgsql_json('CREATE FUNCTION public.rsc_guard_formal_file_object_0036() RETURNS trigger LANGUAGE plpgsql AS $x$'
                              + migration['NEW_BODY'] + '$x$')
    url = f"sqlite+pysqlite:///{tmp_path / 'report-purpose.db'}"
    monkeypatch.setenv('OAM_DATABASE_URL', url)
    monkeypatch.setenv('OAM_ENVIRONMENT', 'development')
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, migration['revision'])
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['revision']
            names = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_files_report_export_%_0137'")}
            assert names == {'trg_files_report_export_insert_0137', 'trg_files_report_export_update_0137'}
        command.downgrade(config, migration['down_revision'])
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == migration['down_revision']
            assert connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_files_report_export_%_0137'")) == 0
    finally:
        engine.dispose()
