"""Scoped publication directory through one redacted PostgreSQL capability.

Historical/expired batches remain visible. A publication ID from this directory
does not authorize a new opening. No owner connection or raw evidence is used
by the API, and missing database capability fails closed without a fallback.
"""
from datetime import datetime,timezone
from uuid import UUID

from sqlalchemy import exists,select,text
from sqlalchemy.orm import aliased

from ..foundation_models import SourceSystem
from ..inventory_control_projection_models import ControlProjectionPublication as Publication
from ..opening_control_directory_schemas import OpeningControlBatchOut,OpeningControlBatchPageOut
from . import opening_start_options as options

SQL = 'SELECT public.rsc_opening_control_directory_0124(:user,CAST(:version AS bigint),CAST(:region AS uuid),CAST(:limit AS integer),CAST(:after AS uuid))'


def _summaries(db,*,actor,region,limit,after):
    if db.get_bind().dialect.name=='postgresql':
        document=db.scalar(text(SQL),dict(user=actor.user_id,version=actor.authorization_version,
            region=region,limit=limit,after=after))
    elif db.get_bind().dialect.name=='sqlite':
        # Development/test equivalent only. Production never SELECTs private tables.
        successor=aliased(Publication)
        query=select(Publication.id,Publication.source_system_id,SourceSystem.name,
            Publication.captured_at,Publication.created_at,Publication.valid_until,Publication.record_count,
            ~exists(select(successor.id).where(successor.previous_publication_id==Publication.id))) \
            .join(SourceSystem,SourceSystem.id==Publication.source_system_id).where(Publication.region_org_id==region)
        if after is not None: query=query.where(Publication.id>after)
        rows=db.execute(query.order_by(Publication.id).limit(limit+1)).all()
        document=dict(actor_person_id=str(actor.person_id),authorization_version=actor.authorization_version,
            region_org_id=str(region),items=[dict(publication_id=str(row[0]),source_system_id=str(row[1]),source_name=row[2],
                captured_at=options._aware(row[3]),published_at=options._aware(row[4]),valid_until=options._aware(row[5]),
                record_count=row[6],is_latest=row[7]) for row in rows])
    else:
        options._projection_invalid()
    if not isinstance(document,dict) or set(document)!={'actor_person_id','authorization_version','region_org_id','items'} \
            or document['actor_person_id']!=str(actor.person_id) or document['authorization_version']!=actor.authorization_version \
            or type(document['authorization_version']) is not int or document['region_org_id']!=str(region) \
            or not isinstance(document['items'],list) or len(document['items'])>limit+1:
        options._projection_invalid()
    result=tuple(OpeningControlBatchOut.model_validate(row) for row in document['items'])
    previous=after
    for item in result:
        if previous is not None and item.publication_id<=previous: options._projection_invalid()
        previous=item.publication_id
    return result


def list_control_batches(db,*,actor,region_org_id:UUID,limit:int,after_id:UUID|None=None,now:datetime|None=None):
    region=options._required_uuid(region_org_id)
    limit=options._limit(limit)
    after=None if after_id is None else options._required_uuid(after_id)
    clock=lambda: options._aware(now if now is not None else datetime.now(timezone.utc))
    with options._read_boundary(db):
        original=options._require_current_actor(db,actor,now=clock())
        selection=options._Selection(region)
        target=options._selection_signature(db,original.principal,selection,clock())
        rows=_summaries(db,actor=original.principal,region=region,limit=limit,after=after)
        # Compare immutable primitive summaries, including private lookahead;
        # replacement or source-label drift while reading invalidates the page.
        if rows!=_summaries(db,actor=original.principal,region=region,limit=limit,after=after):
            options._read_conflict()
        current=options._require_current_actor(db,original.principal,now=clock())
        current_target=options._selection_signature(db,current.principal,selection,clock())
        if original.signature!=current.signature or target!=current_target: options._read_conflict()
        options._validate_horizons((original.signature,current.signature,target,current_target),now=clock())
        page=rows[:limit]
        return OpeningControlBatchPageOut(actor_person_id=current.principal.person_id,
            authorization_version=current.principal.authorization_version,region_org_id=region,items=page,
            next_after_id=page[-1].publication_id if len(rows)>limit else None)
