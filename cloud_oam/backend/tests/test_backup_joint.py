from backup_test_support import CLOUD, WORKER, OPS
from copy import deepcopy
import io,json,tarfile
from pathlib import Path
import pytest

from formal_object_backup import ObjectBackupError
from joint_backup import publish_joint,verify_joint,JointBackupError,canonical,digest
from test_backup_objects import fixture_row,reader_for,no_network


def tar_bytes(members):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name,body in members.items():
            item=tarfile.TarInfo(name);item.size=len(body);item.mode=0o600
            archive.addfile(item,io.BytesIO(body))
    return stream.getvalue()


def members(path):
    with tarfile.open(path) as archive:return {item.name:archive.extractfile(item).read() for item in archive}


def setup_case(tmp_path,*,change=None,corrupt=False):
    row,body,headers=fixture_row()
    pending,_,_=fixture_row(pending=True)
    def fields(row):
        return dict(id=str(row.id),storage_key=row.storage_key,sha256=row.sha256,size_bytes=row.size_bytes,
            mime_type=row.mime_type,original_filename=row.original_filename,uploaded_by=row.uploaded_by,
            status=row.status,metadata_jsonb=deepcopy(row.metadata_jsonb),created_at=row.created_at.isoformat())
    header=dict(schema='cloud_oam.formal_file_snapshot_candidate.v1',snapshot_id='00000004-00000019-1',
        database='synthetic_backup',role='star_oam_backup',isolation='repeatable read',read_only='on',
        migration_head='20261108_0129',file_count=2)
    rows=[fields(row),fields(pending)]
    snapshot=dict(schema='cloud_oam.database_object_snapshot_candidate.v1',snapshot_id=header['snapshot_id'])
    if change:change(header,rows,snapshot)
    snapshot_export=tmp_path/'export.tar'
    snapshot_export.write_bytes(tar_bytes({'database.sql':b'-- synthetic unit fixture, no database executed\n',
        'files.ndjson':b'\n'.join(canonical(item) for item in [header,*rows])+b'\n','snapshot.json':canonical(snapshot)}))
    legacy=tmp_path/'uploads.tar.gz'
    with tarfile.open(legacy,'w:gz') as archive:
        item=tarfile.TarInfo('legacy.txt');item.size=6
        archive.addfile(item,io.BytesIO(b'legacy'))
    reader,transport=reader_for(row,body,headers,get_change=(lambda s:s.update(body=b'corrupt')) if corrupt else None)
    return snapshot_export,legacy,reader,transport


def test_joint_catalog_objects_and_legacy_archive_verified(tmp_path):
    export,legacy,reader,transport=setup_case(tmp_path)
    final=tmp_path/'joint.tar'
    manifest=publish_joint(export,legacy,reader,final,maximum_bytes=1_000_000)
    assert manifest['formal_object_count']==1 and manifest['pending_intent_count']==1
    assert manifest['cloud_restore_verified'] is False
    verified=verify_joint(final,tmp_path/'restore',maximum_bytes=1_000_000)
    assert verified==manifest
    assert digest(tmp_path/'restore/uploads.tar.gz')==digest(legacy)
    assert len(transport.calls)==2
    assert final.stat().st_mode&0o777==0o600
    assert not list(tmp_path.glob('.joint-*'))


@pytest.mark.parametrize('change',[
    lambda h,r,s:h.update(file_count=3),
    lambda h,r,s:h.update(role='star_oam_api'),
    lambda h,r,s:h.update(isolation='read committed'),
    lambda h,r,s:h.update(read_only='off'),
    lambda h,r,s:h.update(snapshot_id='00000009-00000020-1'),
    lambda h,r,s:s.update(snapshot_id='$(not-a-snapshot)'),
    lambda h,r,s:r[0].update(extra_column='silently-ignore'),
    lambda h,r,s:r[0]['metadata_jsonb'].update(provider='foreign-provider'),
    lambda h,r,s:(r.append(deepcopy(r[0])),h.update(file_count=3)),
])
def test_bad_snapshot_catalog_never_fetches_or_publishes(tmp_path,change):
    export,legacy,reader,transport=setup_case(tmp_path,change=change)
    with pytest.raises((JointBackupError,ObjectBackupError)):
        publish_joint(export,legacy,reader,tmp_path/'joint.tar',maximum_bytes=1_000_000)
    assert transport.calls==[] and not (tmp_path/'joint.tar').exists()
    assert not list(tmp_path.glob('.joint-*'))


def test_corrupt_download_has_no_joint_completion_marker(tmp_path):
    export,legacy,reader,transport=setup_case(tmp_path,corrupt=True)
    with pytest.raises(ObjectBackupError):publish_joint(export,legacy,reader,tmp_path/'joint.tar',maximum_bytes=1_000_000)
    assert not (tmp_path/'joint.tar').exists() and all(r.closed for r in transport.responses)
    assert not list(tmp_path.glob('.joint-*'))


@pytest.mark.parametrize('name',['database.sql.gz','files.ndjson','objects.tar','uploads.tar.gz'])
def test_each_corrupt_joint_component_refuses_before_restore(tmp_path,name):
    export,legacy,reader,_=setup_case(tmp_path)
    final=tmp_path/'joint.tar';publish_joint(export,legacy,reader,final,maximum_bytes=1_000_000)
    values=members(final);values[name]+=b'corrupt';bad=tmp_path/'bad.tar';bad.write_bytes(tar_bytes(values))
    with pytest.raises(JointBackupError,match='joint_file_integrity_mismatch'):
        verify_joint(bad,tmp_path/'restore',maximum_bytes=1_000_000)
    assert not (tmp_path/'restore').exists() and not list(tmp_path.glob('.joint-*'))


@pytest.mark.parametrize('change',[
    lambda m:m['objects'][0].update(etag='"different-restored-etag"'),
    lambda m:m['objects'][0]['metadata'].update(extra='different-restored-metadata'),
    lambda m:m['objects'].append(deepcopy(m['objects'][0])),
    lambda m:m['catalog'][0].update(uploaded_by='foreign-user'),
])
def test_recomputed_outer_checksum_cannot_hide_inner_completion_mismatch(tmp_path,change):
    import hashlib
    export,legacy,reader,_=setup_case(tmp_path)
    final=tmp_path/'joint.tar';publish_joint(export,legacy,reader,final,maximum_bytes=1_000_000)
    values=members(final)
    with tarfile.open(fileobj=io.BytesIO(values['objects.tar'])) as archive:
        objects={item.name:archive.extractfile(item).read() for item in archive}
    manifest=json.loads(objects['manifest.json']);change(manifest);objects['manifest.json']=canonical(manifest)
    values['objects.tar']=tar_bytes(objects)
    outer=json.loads(values['manifest.json'])
    outer['files']['objects.tar']=dict(sha256=hashlib.sha256(values['objects.tar']).hexdigest(),size_bytes=len(values['objects.tar']))
    values['manifest.json']=canonical(outer)
    bad=tmp_path/'bad.tar';bad.write_bytes(tar_bytes(values))
    with pytest.raises(JointBackupError):verify_joint(bad,tmp_path/'restore',maximum_bytes=1_000_000)
    assert not (tmp_path/'restore').exists()


def test_source_tar_extra_member_and_existing_backup_are_retained(tmp_path):
    export,legacy,reader,transport=setup_case(tmp_path)
    values=members(export);values['../escape']=b'data';export.write_bytes(tar_bytes(values))
    with pytest.raises(JointBackupError,match='unexpected_archive_members'):
        publish_joint(export,legacy,reader,tmp_path/'joint.tar',maximum_bytes=1_000_000)
    assert transport.calls==[] and not (tmp_path/'escape').exists()
    final=tmp_path/'joint.tar';final.write_bytes(b'previous completed backup')
    with pytest.raises(JointBackupError,match='backup_already_exists'):
        publish_joint(export,legacy,reader,final,maximum_bytes=1_000_000)
    assert final.read_bytes()==b'previous completed backup'
