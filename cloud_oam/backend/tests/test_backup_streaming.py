from backup_test_support import CLOUD, WORKER, OPS
from copy import deepcopy
import io,json,os,tarfile
from pathlib import Path
import pytest
import streaming_backup as stream
from joint_backup import JointBackupError,publish_joint,verify_joint,unpack_exact
from formal_object_backup import ObjectBackupError,canonical
from backup_scale_fixture import row,fields,header,reader
from test_backup_joint import setup_case,tar_bytes,members
from test_backup_objects import no_network


@pytest.mark.parametrize('bad',[b'{"catalog":[{},]}',b'{"objects":[],}',b'{"catalog":[],"catalog":[]}',b'{"catalog":[] }garbage',b'{"catalog":["unterminated]}',b'{"catalog":[{"x":1,"x":2}]}',b'{"catalog":['+b'['*70+b'0'+b']'*70+b']}'])
def test_incremental_json_refuses_malformed_duplicate_or_deep_input(bad):
    with pytest.raises(JointBackupError):stream.JsonRecords(io.BytesIO(bad)).manifest(lambda *args:None)


def test_json_escape_unicode_and_chunk_boundaries_match_standard_decoder():
    value={'catalog':[{'x':'中文 \\" \n , : ] } '*7000},{'y':True}],'objects':[{'x':None}],'schema':'test'}
    encoded=canonical(value);found={'catalog':[],'objects':[]}
    class TinyReader(io.BytesIO):
        def read(self,size):return super().read(min(size,17))
    scalars=stream.JsonRecords(TinyReader(encoded)).manifest(lambda key,value:found[key].append(value))
    assert {**scalars,**found}==json.loads(encoded)


def test_record_limit_stops_before_reading_unbounded_value(monkeypatch):
    monkeypatch.setattr(stream,'RECORD_LIMIT',128)
    body=io.BytesIO(b'{"catalog":[{"value":"'+b'x'*100000+b'"}]}')
    with pytest.raises(JointBackupError,match='manifest_record_too_large'):stream.JsonRecords(body).manifest(lambda *a:None)
    assert body.tell()<=65536


@pytest.mark.parametrize('kind',[tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK,tarfile.GNUTYPE_SPARSE])
def test_extended_headers_refused_before_payload_read(tmp_path,kind):
    item=tarfile.TarInfo('manifest.json');item.type=kind;item.size=2**30
    source=tmp_path/'evil.tar';source.write_bytes(item.tobuf(format=tarfile.GNU_FORMAT));out=tmp_path/'out';out.mkdir()
    with pytest.raises(JointBackupError,match='invalid_archive_member'):unpack_exact(source,out,{'manifest.json'},maximum_bytes=2**31)
    assert list(out.iterdir())==[]


@pytest.mark.parametrize('mutation',['duplicate','bad-final','wrong-count','oversized-line'])
def test_late_invalid_catalog_never_contacts_storage(tmp_path,mutation):
    work=tmp_path/'index';work.mkdir();path=tmp_path/'files.ndjson';h=header(300)
    with path.open('wb') as out:
        out.write(canonical(h)+b'\n')
        for i in range(300):
            value=fields(row(i))
            if i==299:
                if mutation=='duplicate':value=fields(row(0))
                if mutation=='bad-final':value['metadata_jsonb']['provider']='foreign'
                if mutation=='wrong-count':continue
                if mutation=='oversized-line':value['padding']='x'*(stream.RECORD_LIMIT+1)
            out.write(canonical(value)+b'\n')
    provider,transport=reader()
    with pytest.raises((JointBackupError,ObjectBackupError)):
        with stream.catalog_at(work,16*1024*1024) as index:
            index.read(path,{'snapshot_id':h['snapshot_id']})
            stream.capture(index,provider,tmp_path/'objects.tar',work)
    assert transport.calls==0 and list(work.iterdir())==[] and not (tmp_path/'objects.tar').exists()


def test_v1_objects_and_joint_layout_are_preserved(tmp_path):
    export,legacy,provider,_=setup_case(tmp_path);final=tmp_path/'joint.tar'
    manifest=publish_joint(export,legacy,provider,final,maximum_bytes=1048576)
    assert manifest==verify_joint(final,tmp_path/'restored',maximum_bytes=1048576)
    nested=tmp_path/'restored/objects/manifest.json';content=json.loads(nested.read_text())
    assert content['schema']=='cloud_oam.formal_object_backup_candidate.v1' and len(content['catalog'])==2 and len(content['objects'])==1
    assert not list(tmp_path.rglob('catalog-index.sqlite'))


def test_disk_index_budget_refusal_cleans_private_state(tmp_path):
    work=tmp_path/'work';work.mkdir();h=header(400)
    path=tmp_path/'files.ndjson'
    with path.open('wb') as out:
        out.write(canonical(h)+b'\n')
        for i in range(400):out.write(canonical(fields(row(i)))+b'\n')
    with pytest.raises(JointBackupError,match='catalog_index_refused'):
        with stream.catalog_at(work,65536) as index:index.read(path,{'snapshot_id':h['snapshot_id']})
    assert list(work.iterdir())==[]


def test_index_initialization_error_closes_connection_and_removes_only_owned_file(tmp_path,monkeypatch):
    import sqlite3
    original=sqlite3.connect;connections=[]
    class FailingConnection(sqlite3.Connection):
        def execute(self,sql,*args):
            if sql.startswith('CREATE TABLE'):raise sqlite3.OperationalError('synthetic initialization failure')
            return super().execute(sql,*args)
    def connect(path):
        db=original(path,factory=FailingConnection);connections.append(db);return db
    monkeypatch.setattr(stream.sqlite3,'connect',connect)
    sentinel=tmp_path/'keep';sentinel.write_bytes(b'previous backup')
    with pytest.raises(JointBackupError,match='catalog_index_refused'):
        with stream.catalog_at(tmp_path,1048576):raise AssertionError('must not enter')
    assert list(tmp_path.iterdir())==[sentinel] and sentinel.read_bytes()==b'previous backup'
    with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')
