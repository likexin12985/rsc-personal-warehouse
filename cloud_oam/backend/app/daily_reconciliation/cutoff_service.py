"""Internal owner-side capture and immutable archive candidate.

The caller supplies authenticated command coordinates, plus server-owned reader
connections. Raw documents and computed quantities are never request fields.
Caller owns commit/rollback. This is not a final reconciliation or approval API.
"""
from dataclasses import asdict
from datetime import date, timedelta
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,or_,text
from app import inventory_control_admission as admission
from app import inventory_control_configuration as configuration
from app import inventory_control_authority as authority
from app import inventory_control_preparation as preparation
from app import inventory_control_projection as publication
from app import inventory_control_mapping as normalization_mapping
from app.inventory_control_projection_models import ControlProjectionPublication as Publication
from app.inventory_control_models import InventoryControlPreparation as Preparation,InventoryControlSourceBinding as Binding
from app.formal_services.audit_chain import append_audit_event,verify_audit_event_in_read_snapshot
from .mapping_models import DailyMappingDecision as MappingDecision
from . import mapping as mapping_service
from .cutoff_models import DailyCutoff
from .control_source import capture_published_control,_read as read_source_graph
from .ledger_capture import capture_ledger
from .capture_bridge import compare_captures,at
from .kernel import digest

class CutoffError(RuntimeError):pass

def require(ok,code):
 if not ok:raise CutoffError('daily_cutoff_'+code)

class CaptureCommand(BaseModel):
 model_config=ConfigDict(extra='forbid',frozen=True)
 business_date:date
 source_publication_id:UUID
 source_publication_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
 mapping_decision_id:UUID
 mapping_decision_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
 idempotency_key:str=Field(min_length=16,max_length=128,pattern=r'^[A-Za-z0-9._:-]+$')
 request_id:str=Field(min_length=8,max_length=160,pattern=r'^[A-Za-z0-9._:-]+$')
 @model_validator(mode='after')
 def identities(self):
  if self.source_publication_id.int==0 or self.mapping_decision_id.int==0:raise ValueError('explicit source and mapping required')
  return self

def request_hash(principal,command):return digest(dict(actor_user_id=principal.user_id,command=command.model_dump(mode='json')))

def context(db,access_token,expected_authorization_version,command):
 admission._owner(db);require(isinstance(command,CaptureCommand),'command_invalid')
 command=CaptureCommand.model_validate(command.model_dump())
 preparation._begin_outer(db)
 claims=configuration._claims(access_token,configuration.get_settings())
 principal,session,claims,settings=configuration._operator_context(db,claims,expected_authorization_version)
 return command,principal,session,claims,settings

def audit_payload(row):return dict(cutoff_id=str(row.id),source_publication_id=str(row.source_publication_id),
 mapping_decision_id=str(row.mapping_decision_id),business_date=row.business_date.isoformat(),
 local_ledger_cursor=row.local_ledger_cursor,payload_sha256=row.payload_sha256)

def receipt(row):return dict(recorded=True,cutoff_id=str(row.id),payload_sha256=row.payload_sha256,
 business_date=row.business_date.isoformat(),local_ledger_cursor=row.local_ledger_cursor,
 comparison_status=row.payload_jsonb['comparison']['comparison']['status'],created_at=preparation._aware(row.created_at).isoformat(),
 daily_reconciliation_approved=False,stock_written=False)

def prove(db,row):
 require(row is not None,'missing')
 p=row.payload_jsonb;require(digest(p)==row.payload_sha256,'payload_hash')
 cmd=CaptureCommand.model_validate(p['command'])
 require(p['schema']=='rsc.daily_cutoff_receipt_candidate.v1' and p['cutoff_id']==str(row.id)
  and p['actor_snapshot']['user_id']==row.actor_user_id and p['actor_snapshot']['person_id']==str(row.actor_person_id)
  and p['actor_snapshot']['authorization_version']==row.actor_authorization_version
  and p['auth_session_id']==row.auth_session_id and at(p['created_at'])==preparation._aware(row.created_at),'identity_binding')
 require(cmd.source_publication_id==row.source_publication_id and cmd.mapping_decision_id==row.mapping_decision_id
  and cmd.business_date==row.business_date and cmd.request_id==row.request_id and cmd.idempotency_key==row.idempotency_key
  and digest(dict(actor_user_id=row.actor_user_id,command=p['command']))==row.request_sha256,'request_binding')
 mapped=mapping_service._prove(db,db.get(MappingDecision,row.mapping_decision_id,populate_existing=True))
 require(mapped.action=='grant' and mapped.payload_sha256==cmd.mapping_decision_sha256
  and mapped.rules_jsonb['mapping']==p['mapping'],'mapping_history')
 source=publication.prove_control_publication(db,db.get(Publication,row.source_publication_id,populate_existing=True))
 require(source.source_system_id==row.source_system_id and source.region_org_id==row.region_org_id
  and source.payload_sha256==cmd.source_publication_sha256,'source_history')
 # Full publication/normalization/capture graph is re-proved historically.
 actual=read_source_graph(db,publication_id=source.id,expected_hash=source.payload_sha256,
  source_id=source.source_system_id,region_id=source.region_org_id,maximum_origins=100000)
 require({k:v for k,v in p['source'].items() if k not in ('database_observation','content_sha256')}==actual,'source_capture_history')
 report=compare_captures(day=cmd.business_date,source=p['source'],ledger=p['ledger'],mapping=p['mapping'])
 require(report==p['comparison'] and p['ledger']['cutoff_cursor']==row.local_ledger_cursor
  and at(p['source']['captured_at'])==preparation._aware(row.source_captured_at)
  and at(p['ledger']['observation']['captured_at'])==preparation._aware(row.local_captured_at),'comparison_history')
 require(p['mapping_approval']['decision_id']==str(mapped.id) and p['mapping_approval']['decision_sha256']==mapped.payload_sha256,
  'approval_binding')
 event=verify_audit_event_in_read_snapshot(db,stream_key='authorization',event_id=row.audit_event_id)
 require(event.action=='daily_reconciliation.capture' and event.aggregate_type=='daily_reconciliation_cutoff'
  and event.aggregate_id==str(row.id) and event.actor_user_id==row.actor_user_id and event.before_jsonb=={}
  and event.after_jsonb==audit_payload(row) and event.request_id=='daily-cutoff:'+str(row.id)
  and preparation._aware(event.occurred_at)==preparation._aware(row.created_at),'audit_binding')
 return row

def existing(db,principal,command):
 rows=list(db.scalars(select(DailyCutoff).where(DailyCutoff.actor_user_id==principal.user_id,
  or_(DailyCutoff.idempotency_key==command.idempotency_key,DailyCutoff.request_id==command.request_id)).execution_options(populate_existing=True)))
 if not rows:return None
 require(len(rows)==1 and rows[0].request_sha256==request_hash(principal,command),'request_conflict')
 return prove(db,rows[0])

def recover(db,*,access_token,expected_authorization_version,command):
 command,principal,session,claims,settings=context(db,access_token,expected_authorization_version,command)
 row=existing(db,principal,command)
 configuration._finish(db,principal,session,claims,settings)
 return receipt(row) if row else dict(recorded=False,daily_reconciliation_approved=False,stock_written=False)

def _new_coordinates(db,command):
 source=db.get(Publication,command.source_publication_id,populate_existing=True)
 require(source is not None and source.payload_sha256==command.source_publication_sha256,'source_mismatch')
 root=db.get(Preparation,source.preparation_id,populate_existing=True)
 require(root is not None,'source_preparation_missing')
 db.scalar(select(Binding.id).where(Binding.id==root.binding_id).with_for_update())
 mapped=mapping_service.resolve_inventory_control_mapping(db,binding_id=root.binding_id,catalog_id=root.catalog_id,
  decision_id=command.mapping_decision_id)
 require(mapped['decision_sha256']==command.mapping_decision_sha256,'mapping_mismatch')
 current_authority=admission.inspect_inventory_control_admission(db,preparation_id=source.preparation_id)
 normalization_mapping.resolve_inventory_control_mapping(db,binding_id=root.binding_id,catalog_id=root.catalog_id,
  decision_id=source.mapping_decision_id)
 now=authority._now(db);require(now<preparation._aware(source.valid_until),'source_expired')
 mapping_row=db.get(MappingDecision,command.mapping_decision_id)
 require(preparation._aware(source.captured_at)>=preparation._aware(mapping_row.valid_from),'source_before_mapping')
 return source,root,mapped,current_authority,now

def preview(db,*,access_token,expected_authorization_version,command):
 """Inspect exact coordinates only; never capture a ledger or create a cutoff."""
 command,principal,session,claims,settings=context(db,access_token,expected_authorization_version,command)
 prior=existing(db,principal,command)
 if prior is not None:
  configuration._finish(db,principal,session,claims,settings)
  return dict(inspection_only=True,capture_performed=False,**receipt(prior))
 source,root,mapped,current_authority,now=_new_coordinates(db,command)
 captured=preparation._aware(source.captured_at)
 local_date=ZoneInfo('Asia/Shanghai')
 require(captured.astimezone(local_date).date()==command.business_date and
  now.astimezone(local_date).date()==command.business_date,'business_date_mismatch')
 require(timedelta(0)<=now-captured<=timedelta(seconds=300),'source_capture_age_invalid')
 configuration._finish(db,principal,session,claims,settings)
 return dict(inspection_only=True,capture_performed=False,recorded=False,
  business_date=command.business_date.isoformat(),source_publication_id=str(source.id),
  source_publication_sha256=source.payload_sha256,mapping_decision_id=mapped['decision_id'],
  mapping_decision_sha256=mapped['decision_sha256'],binding_id=str(root.binding_id),
  catalog_id=str(root.catalog_id),source_captured_at=captured.isoformat(),
  inspected_at=now.isoformat(),source_valid_until=preparation._aware(source.valid_until).isoformat(),
  mapping_valid_until=mapped['valid_until'],daily_reconciliation_approved=False,stock_written=False)

def _capture_and_record(db,*,access_token,expected_authorization_version,command,source_reader_engine,ledger_reader_connection,deadline):
 command,principal,session,claims,settings=context(db,access_token,expected_authorization_version,command)
 prior=existing(db,principal,command)
 if prior is not None:
  configuration._finish(db,principal,session,claims,settings)
  return receipt(prior)
 source,root,mapped,current_authority,now=_new_coordinates(db,command)
 source_capture=capture_published_control(source_reader_engine,publication_id=source.id,expected_hash=source.payload_sha256,
  source_id=source.source_system_id,region_id=source.region_org_id,maximum_origins=100000,maximum_seconds=deadline.reader_seconds())
 # Freeze reference phantoms before the posting head, following the writer's
 # reference->head order. Hold head only during local capture + atomic archive.
 db.execute(text('LOCK TABLE public.stock_accounts IN SHARE MODE'))
 cursor=db.execute(text("SELECT next_cursor-1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR SHARE")).scalar_one()
 ledger_capture=capture_ledger(ledger_reader_connection,maximum_rows=100000,maximum_seconds=deadline.reader_seconds())
 require(ledger_capture['cutoff_cursor']==cursor,'capture_head_changed')
 mapping=mapped['rules']['mapping']
 report=compare_captures(day=command.business_date,source=source_capture,ledger=ledger_capture,mapping=mapping)
 now=authority._now(db);require(now<preparation._aware(source.valid_until) and
  (mapped['valid_until'] is None or now<at(mapped['valid_until'])) and now<at(current_authority['observation']['valid_until']),'expired_during_capture')
 row=DailyCutoff(id=uuid4(),business_date=command.business_date,source_publication_id=source.id,
  mapping_decision_id=command.mapping_decision_id,source_system_id=source.source_system_id,region_org_id=source.region_org_id,
  local_ledger_cursor=cursor,source_captured_at=at(source_capture['captured_at']),local_captured_at=at(ledger_capture['observation']['captured_at']),
  created_at=now,actor_user_id=principal.user_id,actor_person_id=principal.person_id,actor_authorization_version=principal.authorization_version,
  auth_session_id=session.id,idempotency_key=command.idempotency_key,request_id=command.request_id,request_sha256=request_hash(principal,command))
 row.payload_jsonb=dict(schema='rsc.daily_cutoff_receipt_candidate.v1',cutoff_id=str(row.id),command=command.model_dump(mode='json'),
  actor_snapshot=authority._json(asdict(principal)),auth_session_id=session.id,access_issued_at=claims['iat'],access_expires_at=claims['exp'],
  created_at=now.isoformat(),source=source_capture,ledger=ledger_capture,mapping=mapping,mapping_approval=mapped,
  source_current_authority=authority._json(current_authority),comparison=report,daily_reconciliation_approved=False,stock_written=False)
 row.payload_sha256=digest(row.payload_jsonb)
 event=append_audit_event(db,stream_key='authorization',actor_user_id=principal.user_id,action='daily_reconciliation.capture',
  aggregate_type='daily_reconciliation_cutoff',aggregate_id=str(row.id),request_id='daily-cutoff:'+str(row.id),
  before_jsonb={},after_jsonb=audit_payload(row),occurred_at=now,created_at=now)
 row.audit_event_id=event.id;db.add(row);db.flush()
 configuration._finish(db,principal,session,claims,settings)
 return receipt(prove(db,row))
