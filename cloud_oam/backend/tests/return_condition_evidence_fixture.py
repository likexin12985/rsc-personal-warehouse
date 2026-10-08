"""Full file metadata over minimal synthetic stock/identity parent records.

Actual upload service and authority are verified by the separate old-history
gate. Here metadata is deliberately constructed so forged variants can reach
the event/file SQL guard independently of upload-time protection.
"""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import Column, Table

from app.foundation_models import FileObject, UUID_TYPE, JSON_DOCUMENT
from app.return_condition_schema import build_schema
from formal_file_integrity import _canonical_hash, _storage_key
from return_condition_identity_fixture import IdentitySource, identity_schema


def evidence_schema(references):
    meta=identity_schema(); files=meta.tables['files']
    for column in FileObject.__table__.c:
        if column.name not in files.c:
            files.append_column(Column(column.name,column.type,nullable=True))
    full,_,_=build_schema()
    for name, columns in references.items():
        assert name not in meta.tables
        Table(name,meta,Column('id',UUID_TYPE,primary_key=True),
            *(Column(c,full.tables[name].c[c].type,nullable=True) for c in columns))
    Table('daily_review_events',meta,Column('id',UUID_TYPE,primary_key=True),Column('payload_jsonb',JSON_DOCUMENT))
    return meta


class EvidenceSource(IdentitySource):
    def __init__(self,meta,mode):
        super().__init__(meta,mode)
        self.file_transform=lambda row:row
        self.files_by_event={}
        self.file_count=1

    def evidence_files(self,db,event):
        result=[]
        if event['kind'] not in ('submit','verify_region','supplement'):
            return result
        for _ in range(self.file_count):
            file_id=uuid4(); purpose='return_condition_evidence'; created=event['created_at']-timedelta(minutes=1)
            value=dict(id=file_id,status='available',uploaded_by=event['actor_user_id'],created_at=created,
                storage_key=_storage_key(purpose,file_id),original_filename='核对.jpg',size_bytes=128,mime_type='image/jpeg',sha256='a'*64)
            metadata=dict(schema='cloud_oam.formal_file_upload_intent.v1',purpose=purpose,
                provider='aliyun_oss_v2',file_id=str(file_id),storage_key=value['storage_key'],
                uploader_user_id=event['actor_user_id'],uploader_person_id=str(event['actor_person_id']),
                authorization_version=event['authorization_version'],idempotency_key_hash='b'*64,
                request_sha256=_canonical_hash({k:value[k] for k in ('original_filename','size_bytes','mime_type','sha256')}|dict(purpose=purpose)),
                completion=dict(etag_sha256='c'*64,head_manifest_sha256='d'*64,verified_at=created.isoformat()))
            value['metadata_jsonb']=metadata
            value=self.file_transform(deepcopy(value))
            db.execute(self.meta.tables['files'].insert(),value)
            result.append(dict(file_id=file_id,metadata_sha256=_canonical_hash(value['metadata_jsonb'])))
        self.files_by_event[event['id']]=[r['file_id'] for r in result]
        return result
