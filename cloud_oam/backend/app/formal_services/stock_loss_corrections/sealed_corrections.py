"""Close an exact unexecuted correction command; caller owns the transaction.

Not an HTTP route. Native late-write fences and immutable key binding remain
mandatory before this candidate can be installed or exposed.
"""
from datetime import datetime, timezone
from uuid import uuid4

from app.formal_access import lock_formal_principal_graph
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from .request_contracts import ACTIONS, CorrectionApprove, CorrectionExecute, original_request, validate
from . import correction_recovery, correction_seal_facts as facts, request_authority


def seal(db, *, actor, request):
    request = validate(request)
    if type(request) not in facts.MODELS:
        raise ValueError('complete original correction approval or execution request required')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    refs = request_authority.references(db, actor=actor, request=request)
    current = refs.actor
    answer = correction_recovery.lookup(db, actor=current, request=request)
    if answer['request_state'] != 'not_found':
        return answer
    binding = original_request(actor=current, request=request)
    hashes = facts.keys(request)
    at = datetime.now(timezone.utc)
    extra = {}
    if type(request) is CorrectionExecute:
        extra = dict(correction_decision_id=refs.decision.id,
            expected_correction_decision_hash=request.expected_correction_decision_hash,
            plan_hash=request.expected_plan_hash)
    model = facts.MODELS[type(request)]
    row = model(id=uuid4(), root_disposition_id=refs.root.id, reversal_id=refs.reversal.id,
        expected_reversal_hash=request.expected_reversal_hash,
        disposition=request.disposition if type(request) is CorrectionApprove else refs.decision.disposition,
        actor_user_id=current.user_id, actor_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=request.request_id,
        idempotency_key_hash=binding.key_hash, reversal_key_hash=hashes[0], approval_key_hash=hashes[1],
        correction_key_hash=hashes[2], request_reference=posting._request_reference(request.request_id),
        request_hash=binding.request_hash, reason=request.reason, command_jsonb=binding.document,
        created_at=at, **extra)
    db.add(row)
    db.flush()
    aggregate, kind = facts.KINDS[model]
    append_audit_event(db, stream_key='inventory', actor_user_id=current.user_id, action=kind,
        aggregate_type=aggregate, aggregate_id=str(row.id), before_jsonb={}, after_jsonb=facts.payload(row),
        request_id='loss-correction-seal:' + str(row.id), occurred_at=at, created_at=at)
    db.flush()
    request_authority.authorize(db, actor=current, order=refs.order, action=ACTIONS[type(request)])
    return correction_recovery.lookup(db, actor=current, request=request)
