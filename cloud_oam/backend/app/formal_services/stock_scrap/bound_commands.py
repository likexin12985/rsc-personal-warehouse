"""Candidate PostgreSQL composition; caller commits, no public route installed."""
from app.formal_services.stock_loss_corrections.bound_commands import register
from uuid import UUID
from sqlalchemy import text
from . import recovery_execution, execution, recovery_approval, recovery_facts, request_guard


def register_scrap(db, *, kind, identifier, client_key):
    db.flush()
    db.execute(text('SELECT public.rsc_register_scrap_request_binding_0165(:kind,:fact,:key)'),
        dict(kind=kind, fact=UUID(str(identifier)), key=client_key))


def original(db, *, actor, request):
    actor, request = request_guard.prepare(db, actor=actor, request=request, kind='original')
    result = execution.execute(db, actor=actor, request=request)
    register_scrap(db, kind='original', identifier=result['root_disposition_id'], client_key=request.idempotency_key)
    return result


def review(db, *, actor, request):
    stage = next((stage for stage, model in recovery_facts.CONTRACTS.items() if type(request) is model), None)
    if stage is None:
        raise ValueError('an exact recovery review command is required')
    actor, request = request_guard.prepare(db, actor=actor, request=request, kind=stage)
    result = recovery_approval.submit(db, actor=actor, request=request)
    register_scrap(db, kind=stage, identifier=result['fact_id'], client_key=request.idempotency_key)
    return result


def recover(db, *, actor, request):
    actor, request = request_guard.prepare(db, actor=actor, request=request, kind='execute')
    result = recovery_execution.execute(db, actor=actor, request=request)
    # Preserve the exact actual client key. The DB derives both the new recovery
    # hash and all three retained legacy aliases; a transformed key is invalid.
    register(db, kind='inverse', identifier=result['reversal_id'], client_key=request.idempotency_key)
    return result


def correct(db, *, actor, request):
    actor, request = request_guard.prepare(db, actor=actor, request=request, kind='correction')
    result = execution.execute(db, actor=actor, request=request)
    register(db, kind='correction', identifier=result['correction_execution_id'], client_key=request.idempotency_key)
    return result
