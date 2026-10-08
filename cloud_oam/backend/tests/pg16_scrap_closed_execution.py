"""Six live business stages: service denial plus bypassed COMMIT fence."""
from uuid import uuid4
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import (
    bound_commands, execution, recovery_execution, recovery_approval,
)
from pg16_scrap_seal_sources import arguments
from pg16_scrap_seal_persistence import register, snapshot, only_closure_changed, rejected_transaction


def write(db, *, actor, command):
    kind = arguments(command)['kind']
    handler = {'original': bound_commands.original, 'correction': bound_commands.correct,
        'execute': bound_commands.recover}.get(kind, bound_commands.review)
    return handler(db, actor=actor, request=command)


def raw_write(db, *, actor, command):
    """Deliberately bypass only application admission, retain real registration."""
    kind = arguments(command)['kind']
    if kind in ('original', 'correction'):
        result = execution.execute(db, actor=actor, request=command)
        if kind == 'original':
            bound_commands.register_scrap(db, kind=kind, identifier=result['root_disposition_id'], client_key=command.idempotency_key)
        else:
            bound_commands.register(db, kind=kind, identifier=result['correction_execution_id'], client_key=command.idempotency_key)
    elif kind == 'execute':
        result = recovery_execution.execute(db, actor=actor, request=command)
        bound_commands.register(db, kind='inverse', identifier=result['reversal_id'], client_key=command.idempotency_key)
    else:
        result = recovery_approval.submit(db, actor=actor, request=command)
        bound_commands.register_scrap(db, kind=kind, identifier=result['fact_id'], client_key=command.idempotency_key)
    return result


def require_denial(db, *, actor, command, conflict=False):
    kind = arguments(command)['kind']
    prefix = 'stock_scrap' if kind in ('original', 'correction') else 'stock_scrap_recovery'
    try:
        write(db, actor=actor, command=command)
    except InventoryReadError as error:
        assert error.status_code == 409 and error.code == prefix + ('_request_conflict' if conflict else '_request_sealed'), error.code
    else:
        raise AssertionError('closed or changed request reached execution')


def before_stage(context, *, actor_id, command):
    kind = arguments(command)['kind']
    outcomes = context.setdefault('closed_stage_write_proofs', {})
    if kind in outcomes:
        return
    owner, api = (context['engines'][n] for n in ('star_oam_migrator', 'star_oam_api'))
    closed = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
    before = snapshot(owner)
    from pg16_scrap_seal_races import commit_seal
    answer = commit_seal(context, actor_id=actor_id, command=closed)
    assert not answer['created']  # real registrar repeat after the service created it
    retained = snapshot(owner)
    only_closure_changed(before, retained)
    with Session(api) as db:
        require_denial(db, actor=load_formal_principal(db, actor_id), command=closed)
        db.commit()
    assert snapshot(owner) == retained
    def bypass(db):
        raw_write(db, actor=load_formal_principal(db, actor_id), command=closed)
    rejected_transaction(api, bypass, phase='commit', message='sealed key or request has another registry fact')
    assert snapshot(owner) == retained
    outcomes[kind] = dict(serviceCode='request_sealed', rawBypassPhase='commit', sqlstate='23514', fullRollback=True)
    context.setdefault('closed_stage_requests', []).append((actor_id, closed, answer))
    print(kind + ' live stage closed request: service 409 and raw API COMMIT fence PASS', flush=True)
