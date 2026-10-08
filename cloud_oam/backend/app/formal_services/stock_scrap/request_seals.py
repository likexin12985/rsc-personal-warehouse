"""Candidate exact closure composition; caller owns COMMIT, no HTTP route."""
from datetime import datetime
import json
from sqlalchemy import text
from app.formal_access import lock_formal_principal_graph
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from app.stock_scrap_schemas import ScrapRequestSeal, ScrapRequestLookup, validated_scrap_request
from app.stock_scrap_recovery_schemas import ScrapRecoveryRequestSeal, ScrapRecoveryRequestLookup, validated_recovery_request
from . import request_lookup, recovery_lookup, recovery_facts


def seal(db, *, actor, request):
    if type(request) is ScrapRequestSeal:
        request = validated_scrap_request(request)
        query = ScrapRequestLookup.model_validate(request.model_dump())
        lookup = request_lookup.lookup
        kind = request.original.source.kind
        document = request_lookup.canonical(request.original)
    elif type(request) is ScrapRecoveryRequestSeal:
        request = validated_recovery_request(request)
        query = ScrapRecoveryRequestLookup.model_validate(request.model_dump())
        lookup = recovery_lookup.lookup
        kind = next((k for k,m in recovery_facts.CONTRACTS.items() if type(request.original) is m),'execute')
        document = recovery_facts.intent(request.original)
    else:
        raise ValueError('an exact original scrap or recovery seal request is required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db,(actor.user_id,))
    current = posting._require_current_actor(db,posting._validate_supplied_actor(actor))
    answer = lookup(db,actor=current,request=query)
    if answer['request_state']!='not_found':
        return answer
    stored = db.scalar(text('SELECT public.rsc_register_scrap_seal_0165('
        ':actor,:version,:person,:kind,CAST(:command AS jsonb),:key)'),
        dict(actor=current.user_id,version=current.authorization_version,person=current.person_id,
            kind=kind,command=json.dumps(document,ensure_ascii=False),key=request.original.idempotency_key))
    if stored['created']:
        row=stored['seal'];at=datetime.fromisoformat(row['created_at'])
        append_audit_event(db,stream_key='inventory',actor_user_id=current.user_id,action='seal_scrap_request',
            aggregate_type='stock_scrap_request_seal',aggregate_id=row['id'],before_jsonb={},
            after_jsonb=stored['audit_payload'],request_id='scrap-seal:'+row['id'],occurred_at=at,created_at=at)
    db.flush()
    return lookup(db,actor=current,request=query)
