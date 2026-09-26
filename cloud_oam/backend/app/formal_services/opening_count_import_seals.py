"""Explicit permanent non-admission, including unknown source-intent outcomes.

This retains possible private objects. It never claims that a signed PUT was
revoked, cancels an accepted job, or grants authority to replay a command.
"""
from dataclasses import dataclass
from hashlib import sha256
import hmac
import re
from uuid import UUID, uuid4
from sqlalchemy import select, text
from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import OpeningImportCommandSeal as Seal, FileObject, FileJob, AuditEvent
from ..models import User
from ..stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeRound
from ..inventory_models import StockLocation
from . import formal_files, opening_stocktake as opening
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot
from .opening_count_import_jobs import OpeningCountImportJobError, _clean
from .opening_count_import_intake import opening_import_request_key
from .opening_import_admission import lock_import_admission
from .opening_observation_disposition import _task_principal_user_ids
from .opening_stocktake_count import lock_opening_count_coordinates


@dataclass(frozen=True)
class ImportSealCommand:
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    actor_person_id: UUID
    authorization_version: int
    source_sha256: str
    size_bytes: int
    upload_key: str
    import_key: str
    source_file_id: UUID | None = None


def _fail(code='opening_import_seal_evidence_invalid', status=412):
    raise OpeningCountImportJobError(code, status, '原导入终结证据尚未核实，请保留原记录')


def _coordinates(db, command, secret):
    if not isinstance(command, ImportSealCommand) or any(type(v) is not UUID or not v.int
        for v in (command.task_id,command.round_id,command.scope_id,command.actor_person_id)):
        _fail('opening_import_seal_coordinates_invalid',422)
    if (type(command.authorization_version) is not int or command.authorization_version<1
        or type(command.size_bytes) is not int or not 1<=command.size_bytes<=8388608
        or not isinstance(command.source_sha256,str) or not re.fullmatch('[0-9a-f]{64}',command.source_sha256)
        or command.source_file_id is not None and (type(command.source_file_id) is not UUID or not command.source_file_id.int)):
        _fail('opening_import_seal_coordinates_invalid',422)
    raw = formal_files._require_idempotency_key(command.upload_key)
    secret = formal_files._require_hmac_secret(secret)
    user = db.scalar(select(User).where(User.person_id == command.actor_person_id))
    if user is None: _fail()
    key = opening_import_request_key(user.id, command.import_key)
    source_id = formal_files._uuid_from_digest(hmac.new(secret, ('formal-file-upload-id-v1\0'+user.id+'\0'+raw).encode(), sha256).digest())
    upload_hash = formal_files._domain_hmac(secret, 'formal-file-upload-idempotency-v1', user.id, raw)
    if command.source_file_id is not None and source_id != command.source_file_id: _fail()
    return user.id,source_id,upload_hash,key


def _context(db, actor, command, secret):
    _clean(db)
    if not isinstance(actor,FormalPrincipal): _fail('opening_import_seal_forbidden',403)
    original,source_id,upload_hash,key=_coordinates(db,command,secret)
    lock_import_admission(db,source_id,key)
    lock_opening_count_coordinates(db,task_id=command.task_id,round_id=command.round_id,idempotency_key=key)
    task=db.scalar(select(FormalStocktakeTask).where(FormalStocktakeTask.id==command.task_id).with_for_update().execution_options(populate_existing=True))
    if task is None or task.task_type!='opening': _fail()
    users=_task_principal_user_ids(db,task_id=task.id,supplied_user_ids=(original,actor.user_id))
    lock_formal_principal_graph(db,tuple(sorted(users)))
    scope=db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.task_id==task.id,FormalStocktakeScope.id==command.scope_id))
    round_=db.scalar(select(StocktakeRound.id).where(StocktakeRound.task_id==task.id,StocktakeRound.id==command.round_id))
    location=db.get(StockLocation,scope.location_id) if scope else None
    if round_ is None or location is None: _fail()
    try:
        current=opening._require_current_actor(db,actor,now=opening._database_now(db))
        grant=opening._authorize_scope_dimensions(db,actor=current,task_region_org_id=task.region_org_id,
            owner_org_id=scope.owner_org_id,location_owner_org_id=location.owner_org_id)
        if not all(opening._grant_allows(db,current,grant,'stocktake','read',target_scope_type='organization',target_scope_id=str(v))
                   for v in {task.region_org_id,scope.owner_org_id,location.owner_org_id}): _fail('opening_import_seal_forbidden',403)
    except opening.OpeningStocktakeError as error:
        raise OpeningCountImportJobError('opening_import_seal_forbidden',error.http_status_code,'只有当前有权的总部或本区域负责人可终结原导入') from None
    user=db.scalar(select(User).where(User.id==original).execution_options(populate_existing=True))
    if user.person_id!=command.actor_person_id or command.authorization_version>user.authorization_version: _fail()
    expected=dict(actor_user_id=original,actor_person_id=command.actor_person_id,authorization_version=command.authorization_version,
        task_id=command.task_id,round_id=command.round_id,scope_id=command.scope_id,source_file_id=source_id,
        source_sha256=command.source_sha256,size_bytes=command.size_bytes,upload_key_hash=upload_hash,import_key_hash=key)
    source=db.scalar(select(FileObject).where(FileObject.id==source_id).with_for_update().execution_options(populate_existing=True))
    if source is not None:
        metadata=formal_files._validate_intent_metadata(source,allow_completed=True)
        if (source.uploaded_by!=original or source.sha256!=command.source_sha256 or source.size_bytes!=command.size_bytes
            or metadata['purpose']!='opening_count_import' or metadata['idempotency_key_hash']!=upload_hash
            or metadata['uploader_person_id']!=str(command.actor_person_id) or metadata['authorization_version']!=command.authorization_version): _fail()
    if db.scalar(select(FileJob.id).where(FileJob.job_type=='import',
        (FileJob.idempotency_key==key)|(FileJob.import_binding_jsonb['source_file_id'].as_string()==str(source_id))).limit(1)) is not None:
        _fail('opening_import_already_accepted',409)
    row=db.scalar(select(Seal).where((Seal.source_file_id==source_id)|(Seal.import_key_hash==key)))
    if row is not None and any(getattr(row,k)!=v for k,v in expected.items()): _fail()
    return current,grant,expected,row


def _payload(row):
    return {column.name:(str(getattr(row,column.name)) if isinstance(getattr(row,column.name),UUID) else getattr(row,column.name))
            for column in Seal.__table__.columns if column.name not in {'id','created_at','actor_user_id','reviewer_user_id'}}


def _verified(db,current,row):
    events=list(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type=='opening_import_command_seal',AuditEvent.aggregate_id==str(row.id)).limit(2)))
    if len(events)!=1: _fail()
    audit=events[0]
    if (audit.stream_key!='inventory' or audit.actor_user_id!=row.reviewer_user_id or audit.action!='opening_import.sealed'
        or audit.request_id!='opening-import-seal:'+str(row.id) or audit.before_jsonb!={} or audit.after_jsonb!=_payload(row)
        or audit.occurred_at!=row.created_at or audit.created_at!=row.created_at): _fail()
    verify_audit_event_in_read_snapshot(db,stream_key='inventory',event_id=audit.id)
    return dict(schema_version='rsc.opening_import_seal.v1',seal_id=row.id,terminal_audit_id=audit.id,
        actor_person_id=row.actor_person_id,authorization_version=row.authorization_version,
        reviewer_person_id=current.person_id,reviewer_authorization_version=current.authorization_version,
        task_id=row.task_id,round_id=row.round_id,scope_id=row.scope_id,source_file_id=row.source_file_id,
        source_sha256=row.source_sha256,size_bytes=row.size_bytes,permanent_nonexecution=True,
        automatic_retry_allowed=False,source_object_may_exist=True)


def read_opening_import_seal(db,*,actor,command,idempotency_hmac_secret):
    current,_,_,row=_context(db,actor,command,idempotency_hmac_secret)
    if row is None: _fail('opening_import_seal_not_found',404)
    return _verified(db,current,row)


def seal_opening_import(db,*,actor,command,idempotency_hmac_secret):
    current,grant,expected,row=_context(db,actor,command,idempotency_hmac_secret)
    if row is not None: return _verified(db,current,row)
    row=Seal(id=uuid4(),**expected,reviewer_user_id=current.user_id,reviewer_person_id=current.person_id,
        reviewer_authorization_version=current.authorization_version,reviewer_assignment_id=grant.assignment_id,
        created_at=db.scalar(text('SELECT clock_timestamp()')))
    db.add(row);db.flush()
    append_audit_event(db,stream_key='inventory',actor_user_id=current.user_id,action='opening_import.sealed',
        aggregate_type='opening_import_command_seal',aggregate_id=str(row.id),before_jsonb={},after_jsonb=_payload(row),
        request_id='opening-import-seal:'+str(row.id),occurred_at=row.created_at,created_at=row.created_at)
    return _verified(db,current,row)
