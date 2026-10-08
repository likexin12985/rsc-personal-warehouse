"""Private PostgreSQL request closure; caller owns commit and rollback.

Inventory and principal locks serialize closure with new execution. Database
fences and the audit must all pass at COMMIT before closure is durable.
"""
from datetime import datetime
from sqlalchemy import text
from app.formal_access import lock_formal_principal_graph
from app.return_condition_requests import validate_submit
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from . import return_condition_recovery as recovery
from . import return_condition_seal_admission as admission
from . import return_condition_coordinates as coordinates


def seal(db, *, actor, request):
    request = validate_submit(request)
    if db.get_bind().dialect.name != 'postgresql':
        raise RuntimeError('condition request seal registrar requires PostgreSQL')
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    existing = recovery.lookup(db, actor=actor, request=request)
    if existing['request_state'] in ('found', 'sealed'):
        return existing
    prepared = admission.authorize_absence_seal(db, actor=actor, request=request)
    current = prepared.actor
    result = db.scalar(text('SELECT public.rsc_register_condition_seal(:actor,:version,:person,CAST(:command AS jsonb),:key)'),
        dict(actor=current.user_id, version=current.authorization_version, person=current.person_id,
             command=prepared.original_input_json, key=request.idempotency_key))
    if not isinstance(result, dict) or type(result.get('created')) is not bool:
        coordinates.unknown()
    if result['created']:
        row = result['seal']
        at = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
        append_audit_event(db, stream_key='inventory', actor_user_id=current.user_id,
            action='seal_condition_request', aggregate_type='stock_condition_request_seal', aggregate_id=row['id'],
            request_id='condition-seal:' + row['id'], before_jsonb={}, after_jsonb=result['audit_payload'],
            occurred_at=at, created_at=at)
    db.flush()
    checked = recovery.lookup(db, actor=current, request=request)
    if checked['request_state'] != 'sealed' or checked['original_input_hash'] != prepared.original_input_hash:
        coordinates.unknown()
    return checked
