"""Frozen dedicated-purpose migration and shared API/backup purpose contract."""
from pathlib import Path
from io import StringIO
import hashlib,runpy
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security,oam_sync_scope_security as scope
from app.daily_reconciliation.evidence_security import FUNCTION_HASHES
from app.formal_file_schemas import FileUploadIntentIn
import formal_file_integrity as integrity

PATH=Path(__file__).parents[1]/'alembic/versions/20261112_0133_daily_review_evidence.py'

def test_api_backup_and_runtime_catalog_agree():
    m=runpy.run_path(str(PATH))
    report_file=runpy.run_path(str(PATH.with_name('20261116_0137_report_export_file_purpose.py')))
    opening_source=runpy.run_path(str(PATH.with_name('20261119_0140_opening_count_source_purpose.py')))
    assert m['PURPOSE'] in integrity.PURPOSES
    assert FileUploadIntentIn(purpose=m['PURPOSE'],original_filename='proof.pdf',size_bytes=5,mime_type='application/pdf',sha256='a'*64)
    for signature,(old,new) in m['SOURCES'].items():
        coordinate=next(c for c in FUNCTION_HASHES if signature.split('(')[0]=='public.'+c[0])
        stage_hash=hashlib.sha256(new.encode()).hexdigest()
        assert stage_hash==FUNCTION_HASHES[coordinate]
        if coordinate==('rsc_guard_formal_file_object_0036', ''):
            assert stage_hash==report_file['OLD_FILE_HASH']
            assert report_file['NEW_FILE_HASH']==opening_source['OLD_FILE_HASH']
            assert opening_source['NEW_FILE_HASH']==security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
        else:
            assert stage_hash==security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
        assert m['HASHES'][signature]==tuple(hashlib.sha256(x.encode()).hexdigest() for x in (old,new))
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0133['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0132['rsc_oam_runtime_binding_ready_0044()'][6]

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_sql_parses_and_preserves_old_history(direction):
    from pglast import parser
    m=runpy.run_path(str(PATH));output=StringIO()
    for signature,(_,new) in m['SOURCES'].items():
        args='e public.daily_review_events, wall_at timestamptz' if 'live' in signature else ''
        result='void' if args else 'trigger'
        parser.parse_plpgsql_json('CREATE FUNCTION probe('+args+') RETURNS '+result+' LANGUAGE plpgsql AS $b$'+new+'$b$')
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):m[direction]()
    sql=output.getvalue();parser.parse_sql(sql)
    assert 'direct owner required' in sql and 'ACCESS EXCLUSIVE' in sql
    assert 'DELETE FROM' not in sql and 'DROP TABLE' not in sql
    assert sql.index('no relabeling' if direction=='upgrade' else 'must be retained')<sql.index('daily_evidence_0133')
