"""Historical attachment reader with real completed file service facts.

Event tables intentionally contain only minimal context: no claim of a full
condition stock transaction, migration or current scoped download authority.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, select, text

from app.foundation_models import FileObject
from app.formal_services.stock_loss_corrections import return_condition_event_files as subject
from formal_file_integrity import FormalFileError
from test_return_condition_evidence import db, world, create, finish, proof


@pytest.fixture
def event_world(db, world):
    metadata=MetaData(); events, files=subject._tables()
    Table('files',metadata,Column('id',FileObject.__table__.c.id.type,primary_key=True))
    event_copy=Table(events.name,metadata,*(Column(c.name,c.type,primary_key=c.name=='id',
        nullable=c.name!='id') for c in events.c))
    file_copy=files.to_metadata(metadata)
    metadata.create_all(db.get_bind(),tables=[event_copy,file_copy])
    row=db.get(FileObject,create(db,world).file_id); finish(db,world,row)
    evidence=proof(world,row); event_id=uuid4(); at=datetime.now(timezone.utc)
    event=dict(id=event_id,created_at=at,kind='submit',actor_user_id=world.actor.user_id,
        actor_person_id=world.actor.person_id,authorization_version=world.actor.authorization_version,
        command_jsonb=dict(evidence=[dict(file_id=str(row.id),metadata_sha256=evidence.metadata_sha256)]))
    db.execute(events.insert(),event)
    db.execute(files.insert(),dict(event_id=event_id,file_id=row.id,created_at=at,metadata_sha256=evidence.metadata_sha256))
    db.commit()
    return world,row,event,evidence,events,files


def test_reads_exact_persisted_completed_evidence_without_business_write(db,event_world):
    _,_,event,evidence,_,_=event_world
    db.execute(text('PRAGMA query_only=ON'))
    try:
        assert subject.read_event_evidence(db,event_id=event['id'])==(evidence,)
        assert not db.new and not db.dirty and not db.deleted
    finally: db.execute(text('PRAGMA query_only=OFF'))


@pytest.mark.parametrize('change',['user','person','version','manifest','digest','binding_time','missing','event_time'])
def test_refuses_event_file_binding_tampering(db,event_world,change):
    world,row,event,evidence,events,files=event_world
    if change in ('user','person','version'):
        column,value={'user':('actor_user_id','wrong-user'),'person':('actor_person_id',uuid4()),
            'version':('authorization_version',world.actor.authorization_version+1)}[change]
        db.execute(events.update().where(events.c.id==event['id']).values(**{column:value}))
    elif change=='manifest':
        db.execute(events.update().where(events.c.id==event['id']).values(command_jsonb=dict(evidence=[])))
    elif change=='digest':
        forged='b'*64
        db.execute(files.update().where(files.c.event_id==event['id']).values(metadata_sha256=forged))
        db.execute(events.update().where(events.c.id==event['id']).values(command_jsonb=dict(
            evidence=[dict(file_id=str(row.id),metadata_sha256=forged)])))
    elif change=='binding_time':
        db.execute(files.update().where(files.c.event_id==event['id']).values(created_at=event['created_at']+timedelta(seconds=1)))
    elif change=='missing': db.execute(files.delete().where(files.c.event_id==event['id']))
    else:
        at=event['created_at']-timedelta(days=1)
        db.execute(events.update().where(events.c.id==event['id']).values(created_at=at))
        db.execute(files.update().where(files.c.event_id==event['id']).values(created_at=at))
    db.commit()
    with pytest.raises(FormalFileError): subject.read_event_evidence(db,event_id=event['id'])


def test_late_file_change_cannot_reuse_the_old_event_snapshot(db,event_world):
    _,row,event,_,_,_=event_world
    row.metadata_jsonb=dict(row.metadata_jsonb,completion=dict(row.metadata_jsonb['completion'],etag_sha256='b'*64))
    db.commit()
    with pytest.raises(FormalFileError): subject.read_event_evidence(db,event_id=event['id'])


def test_later_uploader_disable_does_not_revoke_historical_upload(db,event_world):
    world,_,event,evidence,_,_=event_world
    world.user.is_active=False; world.user.authorization_version+=1; db.commit()
    assert subject.read_event_evidence(db,event_id=event['id'])==(evidence,)


def test_unknown_event_is_not_an_empty_evidence_list(db,event_world):
    with pytest.raises(FormalFileError): subject.read_event_evidence(db,event_id=uuid4())
