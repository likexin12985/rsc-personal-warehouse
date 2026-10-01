"""Register DB-owned key provenance after the real service, in the same transaction."""
from uuid import UUID
from sqlalchemy import text
from . import binding_contracts as binding_sql
from . import sealed_inverse as sealed_inverse
from . import correction_approval as correction_approval
from . import correction_execution as correction_execution
from . import sealed_corrections
from .request_contracts import CorrectionApprove, CorrectionExecute


def register(db, *, kind, identifier, client_key):
    db.flush()
    db.execute(text('SELECT public.'+binding_sql.REGISTER+'(:kind,:fact,:key)'),
        dict(kind=kind,fact=UUID(str(identifier)),key=client_key))


def inverse(db, *, actor, request):
    result=sealed_inverse.execute(db,actor=actor,request=request)
    register(db,kind='inverse',identifier=result['reversal_id'],client_key=request.idempotency_key)
    return result


def approve(db, *, actor, request):
    result=correction_approval.approve(db,actor=actor,request=request)
    register(db,kind='approval',identifier=result['correction_decision_id'],client_key=request.idempotency_key)
    return result


def correct(db, *, actor, request):
    result=correction_execution.execute(db,actor=actor,request=request)
    register(db,kind='correction',identifier=result['correction_execution_id'],client_key=request.idempotency_key)
    return result


def seal(db, *, actor, request):
    handler = sealed_corrections.seal if type(request) in (CorrectionApprove, CorrectionExecute) else sealed_inverse.seal
    result=handler(db,actor=actor,request=request)
    if result['request_state']=='sealed':
        kind = ('approval_seal' if type(request) is CorrectionApprove else
                'correction_seal' if type(request) is CorrectionExecute else 'inverse_seal')
        register(db,kind=kind,identifier=result['seal']['seal_id'],client_key=request.idempotency_key)
    return result


def return_inverse(db, *, actor, request):
    """Candidate dedicated return-stop composition; no public route yet."""
    result = sealed_inverse.execute_unshipped_return(db, actor=actor, request=request)
    register(db, kind='inverse', identifier=result['reversal_id'], client_key=request.idempotency_key)
    return result


def seal_return_inverse(db, *, actor, request):
    result = sealed_inverse.seal_unshipped_return(db, actor=actor, request=request)
    if result['request_state'] == 'sealed':
        register(db, kind='inverse_seal', identifier=result['seal']['seal_id'], client_key=request.idempotency_key)
    return result
