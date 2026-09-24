"""Recover an original trace or permanently fence it in the same lock graph."""
from uuid import uuid4
from sqlalchemy import select,text
from ..foundation_models import AuditEvent
from ..formal_services.audit_chain import append_audit_event,verify_audit_event_in_read_snapshot
from . import review_core as core,review_service as service
from .recovery_http import Reference,RecoveryOutput,Seal
from .recovery_models import DailyReviewRequestSeal
from .review_models import DailyReviewEvent,DailyReviewConsumption,DailyReviewBinding

def require_unsealed(db,principal,trace):
    row=db.scalar(select(DailyReviewRequestSeal.id).where(DailyReviewRequestSeal.actor_user_id==principal.user_id,
        DailyReviewRequestSeal.request_id==trace))
    core.require(row is None,'request_sealed')

def reference_for(row):
    return Reference(cutoff_id=row.cutoff_id,actor_person_id=row.actor_person_id,
        original_authorization_version=row.original_authorization_version,original_review_version=row.original_review_version,
        operation=row.operation,trace_request_id=row.request_id)

def seal_payload(row):
    return dict(reference=reference_for(row).model_dump(mode='json'),seal_id=str(row.id),permanent_nonexecution=True)

def audit(db,*,kind,identifier,actor,action,request_id,before,after,stamp):
    rows=list(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type==kind,AuditEvent.aggregate_id==str(identifier),
        AuditEvent.request_id==request_id).limit(2)))
    core.require(len(rows)==1,'recovery_evidence_invalid');row=rows[0]
    core.require(row.stream_key=='authorization' and row.actor_user_id==actor and row.action==action
        and row.before_jsonb==before and row.after_jsonb==after
        and core.at(row.occurred_at)==core.at(stamp) and core.at(row.created_at)==core.at(stamp),'recovery_evidence_invalid')
    verify_audit_event_in_read_snapshot(db,stream_key='authorization',event_id=row.id)
    return row

def observe(db,snapshot,principal,reference):
    core.require(reference.actor_person_id==principal.person_id and
        reference.original_authorization_version<=principal.authorization_version,'request_conflict')
    rows=list(db.scalars(select(DailyReviewEvent).where(DailyReviewEvent.actor_user_id==principal.user_id,
        DailyReviewEvent.request_id==reference.trace_request_id).limit(2)))
    seals=list(db.scalars(select(DailyReviewRequestSeal).where(DailyReviewRequestSeal.actor_user_id==principal.user_id,
        DailyReviewRequestSeal.request_id==reference.trace_request_id).limit(2)))
    core.require(len(rows)+len(seals)<=1,'recovery_evidence_invalid')
    receipt=None;seal=None
    if rows:
        row=rows[0];event=core.Event.model_validate(row.payload_jsonb)
        core.require(row.cutoff_id==reference.cutoff_id and row.actor_person_id==reference.actor_person_id
            and event.actor.authorization_version==reference.original_authorization_version
            and event.request.expected_version==reference.original_review_version and event.request.operation==reference.operation
            and event.request.request_id==reference.trace_request_id,'request_conflict')
        events=service.history(db,snapshot)
        receipt=core.recover(snapshot,events,principal=principal,request=event.request,now=service.now(db))
        core.require(receipt is not None and receipt.model_dump(mode='json')==row.receipt_jsonb
            and event.event_id==row.id and event.sha256==row.payload_sha256,'recovery_evidence_invalid')
        binding=db.get(DailyReviewBinding,row.run_id)
        state=core.prove_history(snapshot,events)
        core.require(binding is not None and binding.cutoff_id==snapshot.cutoff_id and binding.version==state.version
            and binding.state_jsonb==state.model_dump(mode='json') and binding.last_event_id==events[-1].event_id,'recovery_evidence_invalid')
        evidence=audit(db,kind='daily_reconciliation_run',identifier=row.run_id,actor=row.actor_user_id,
            action='daily_reconciliation.'+event.request.operation,request_id='daily-review:'+str(row.id),
            before=dict(version=row.version-1),after=dict(event_id=str(row.id),event_sha256=row.payload_sha256,receipt=row.receipt_jsonb),stamp=row.created_at)
        consumption=db.get(DailyReviewConsumption,row.id)
        core.require(consumption is not None and consumption.audit_event_id==evidence.id,'recovery_evidence_invalid')
    if seals:
        row=seals[0];core.require(reference_for(row)==reference,'request_conflict')
        core.require(core.at(row.created_at)<=core.at(service.now(db)),'recovery_evidence_invalid')
        # Exactly one seal audit, including rows with a forged request reference.
        ids=list(db.scalars(select(AuditEvent.id).where(AuditEvent.aggregate_type=='daily_review_request_seal',AuditEvent.aggregate_id==str(row.id)).limit(2)))
        core.require(len(ids)==1,'recovery_evidence_invalid')
        audit(db,kind='daily_review_request_seal',identifier=row.id,actor=row.actor_user_id,
            action='daily_reconciliation.request_sealed',request_id='daily-review-seal:'+str(row.id),
            before={},after=seal_payload(row),stamp=row.created_at)
        seal=Seal(seal_id=row.id,sealed_at=row.created_at,reference=reference)
    return RecoveryOutput(reference=reference,current_authorization_version=principal.authorization_version,
        outcome='found' if receipt else 'sealed' if seal else 'not_observed',receipt=receipt,seal=seal)

def execute(db,*,access_token,expected_authorization_version,reference,seal=False):
    reference=Reference.model_validate(reference.model_dump())
    snapshot,p,session,claims,settings=service.authority_context(db,access_token,expected_authorization_version,
        reference.cutoff_id,reference.trace_request_id,'read')
    original=observe(db,snapshot,p,reference)
    if seal and original.outcome=='not_observed':
        stamp=service.now(db);actor=core.authorize(p,snapshot,'read',stamp)
        row=DailyReviewRequestSeal(id=uuid4(),cutoff_id=reference.cutoff_id,actor_user_id=p.user_id,actor_person_id=p.person_id,
            original_authorization_version=reference.original_authorization_version,original_review_version=reference.original_review_version,
            operation=reference.operation,request_id=reference.trace_request_id,actor_snapshot=actor.model_dump(mode='json'),
            auth_session_id=session.id,access_issued_at=claims['iat'],access_expires_at=claims['exp'],created_at=stamp,
            transaction_id=db.scalar(text('SELECT txid_current()')))
        db.add(row);db.flush()
        append_audit_event(db,stream_key='authorization',actor_user_id=p.user_id,action='daily_reconciliation.request_sealed',
            aggregate_type='daily_review_request_seal',aggregate_id=str(row.id),request_id='daily-review-seal:'+str(row.id),
            before_jsonb={},after_jsonb=seal_payload(row),occurred_at=stamp,created_at=stamp)
        db.flush();original=observe(db,snapshot,p,reference)
    service.finish(db,snapshot,p,session,claims,settings,'read')
    return original.model_dump(mode='json')
