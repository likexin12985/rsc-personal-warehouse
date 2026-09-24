"""Deterministic synthetic catalog, stateless real-SDK transport, zero networking."""
from backup_test_support import CLOUD, WORKER, OPS
from datetime import datetime,timezone
from hashlib import sha256
from uuid import UUID
from urllib.parse import urlsplit
from formal_file_integrity import FileObject,StoredObjectHead,FileUploadIntentInput,FILE_METADATA_SCHEMA
from formal_file_integrity import _prepare_upload,_upload_request_hash,_storage_key,_head_manifest_sha256
from formal_object_backup import canonical,OssBackupReader,sdk_client
from backup_transport_fixture import Response
import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import HttpClient

BODY=b'synthetic scale fixture body; no operational data'
ETAG='"synthetic-scale-etag-3"'
DATE=datetime(2026,9,21,tzinfo=timezone.utc)
PREPARED=_prepare_upload(FileUploadIntentInput(purpose='request_attachment',original_filename='scale.png',
    size_bytes=len(BODY),mime_type='image/png',sha256=sha256(BODY).hexdigest()),maximum_size_bytes=120*1024*1024)


def row(index):
    key=UUID(int=index+1)
    result=FileObject(id=key,storage_key=_storage_key('request_attachment',key),sha256=sha256(BODY).hexdigest(),
        size_bytes=len(BODY),mime_type='image/png',original_filename='scale.png',uploaded_by='synthetic-scale-user',
        status='available' if index%5==0 else 'pending',created_at=DATE,metadata_jsonb={})
    result.metadata_jsonb=dict(schema=FILE_METADATA_SCHEMA,authorization_version=1,file_id=str(key),storage_key=result.storage_key,
        uploader_user_id=result.uploaded_by,uploader_person_id=str(UUID(int=999999999)),purpose='request_attachment',
        provider='aliyun_oss_v2',request_sha256=_upload_request_hash(PREPARED),idempotency_key_hash='a'*64)
    if result.status=='available':
        head=StoredObjectHead(result.storage_key,result.size_bytes,result.mime_type,{'file-id':str(key),'sha256':result.sha256},ETAG)
        result.metadata_jsonb['completion']=dict(etag_sha256=sha256(ETAG.encode()).hexdigest(),head_manifest_sha256=_head_manifest_sha256(head),verified_at=DATE.isoformat())
    return result


def fields(value):
    return dict(id=str(value.id),storage_key=value.storage_key,sha256=value.sha256,size_bytes=value.size_bytes,mime_type=value.mime_type,
        original_filename=value.original_filename,uploaded_by=value.uploaded_by,status=value.status,metadata_jsonb=value.metadata_jsonb,created_at=value.created_at.isoformat())


def header(count):
    return dict(schema='cloud_oam.formal_file_snapshot_candidate.v1',snapshot_id='00000004-00000019-1',database='synthetic_backup',role='star_oam_backup',
        isolation='repeatable read',read_only='on',migration_head='20261108_0129',file_count=count)


class Transport(HttpClient):
    def __init__(self):self.calls=0;self.downloads=0;self.closed_downloads=0;self.last=None
    def open(self):pass
    def close(self):pass
    def send(self,request,**kwargs):
        if self.last is not None:assert self.last.closed
        location=urlsplit(request.url);assert location.scheme=='https' and location.hostname=='synthetic-backup.oss-cn-hangzhou.aliyuncs.com'
        value=row(int(location.path.rsplit('/',1)[1],16)-1);assert value.status=='available'
        assert request.method in {'HEAD','GET'}
        if request.method=='GET':
            assert request.headers['if-match']==ETAG and request.headers['accept-encoding']=='identity'
            assert 'if-match' in request.headers['authorization'];self.downloads+=1
        headers={'Content-Length':str(value.size_bytes),'Content-Type':value.mime_type,'ETag':ETAG,
            'x-oss-meta-file-id':str(value.id),'x-oss-meta-sha256':value.sha256,'x-oss-version-id':'synthetic-scale-version'}
        response=Response(request,dict(headers=headers,body=BODY if request.method=='GET' else b''))
        close=response.close
        def mark():
            if not response.closed and request.method=='GET':self.closed_downloads+=1
            close()
        response.close=mark;self.calls+=1;self.last=response;return response


def reader():
    transport=Transport()
    config=oss.Config(region='cn-hangzhou',signature_version='v4',http_client=transport,retry_max_attempts=1,
        credentials_provider=oss.credentials.StaticCredentialsProvider('SYNTHETIC_TEST_ID','synthetic-not-a-live-secret'))
    return OssBackupReader(sdk_client(config),region='cn-hangzhou',bucket='synthetic-backup'),transport
