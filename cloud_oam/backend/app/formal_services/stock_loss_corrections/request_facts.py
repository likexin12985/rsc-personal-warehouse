"""Prove canonical persisted request intent without recovering a raw key.

This component composes with inventory edges, event bundles and historical
authority proof. It never applies today's permissions to a historical actor,
and never returns a reconstructed command that could be replayed.
"""
import re
from types import SimpleNamespace
from uuid import UUID

from pydantic import ValidationError

from app.formal_services import stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .chain_projection import InvalidChain
from .correction_models import StockLossDispositionReversal as Inverse
from .correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution
from . import request_contracts as contracts


SCHEMAS = {Inverse: contracts.ReversalExecute, Decision: contracts.CorrectionApprove,
    Execution: contracts.CorrectionExecute}


def _need(condition):
    if not condition:
        raise InvalidChain('correction_request_fact_mismatch')


def verify_request_fact(*, row, root, order, reversed_execution=None, reversal=None, decision=None):
    """Validate one fact against exact parent rows already loaded by the graph.

    Correctness of those parents' stock/audit facts is the composed verifier's
    responsibility. This check alone is not a complete historical proof.
    """
    if type(row) is Inverse and row.source_account_id is None:
        from ..stock_scrap.recovery_history import request_fact
        request_fact(row=row, root=root, order=order, reversed_execution=reversed_execution)
        return {'request_hash': row.request_hash, 'action': 'execute_scrap_recovery'}
    if type(row) is Execution and row.disposition == 'scrap':
        from ..stock_scrap.request_facts import verify as verify_scrap
        verify_scrap(row=row, root=root, order=order, reversal=reversal, decision=decision)
        return {'request_hash': row.request_hash, 'action': 'scrap'}
    schema = SCHEMAS.get(type(row))
    _need(schema is not None and type(row.command_jsonb) is dict)
    document = row.command_jsonb
    _need(type(document.get('intent')) is dict)
    try:
        # The raw key is intentionally absent from persistence. It does not
        # enter request_hash; its separate stored hash is checked by recovery.
        values = dict(document['intent'], request_id=document['request_id'],
            idempotency_key='historical-proof-placeholder')
        if schema is not contracts.CorrectionApprove:
            values['expected_plan_hash'] = document['expected_plan_hash']
        request = schema.model_validate(values)
        actor = SimpleNamespace(user_id=row.actor_user_id,person_id=row.actor_person_id)
        canonical = contracts.original_request(actor=actor,request=request)
        actor_id = UUID(row.actor_user_id)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise InvalidChain('correction_request_fact_mismatch') from exc
    _need(document == canonical.document and row.request_hash == canonical.request_hash
        and sources._hash(document) == canonical.request_hash
        and row.request_id == request.request_id and row.reason == request.reason
        and row.root_disposition_id == request.root_disposition_id == root.id
        and root.operation_id == order.id
        and request.expected_root_request_hash == root.request_hash
        and request.expected_submission_plan_hash == order.plan_hash
        and type(row.actor_person_id) is UUID and row.actor_person_id.int != 0
        and actor_id.int != 0 and str(actor_id) == row.actor_user_id
        and type(row.authorization_version) is int and row.authorization_version > 0
        and isinstance(row.idempotency_key_hash,str)
        and re.fullmatch(r'[a-f0-9]{64}',row.idempotency_key_hash) is not None
        and _aware(row.created_at) >= _aware(root.created_at))
    if type(row) is Inverse:
        target = root if request.reversed_correction_id is None else reversed_execution
        _need(target is not None)
        _need(row.reversed_correction_id == request.reversed_correction_id
            and ((request.reversed_correction_id is None and target is root)
                or (request.reversed_correction_id is not None and type(target) is Execution
                    and target.id == request.reversed_correction_id and target.root_disposition_id == root.id))
            and request.expected_execution_request_hash == target.request_hash
            and row.original_transaction_id == target.posting_transaction_id
            and row.original_movement_id == target.posting_movement_id
            and row.plan_hash == request.expected_plan_hash
            and _aware(row.created_at) >= _aware(target.created_at))
    else:
        _need(type(reversal) is Inverse and reversal.id == row.reversal_id == request.reversal_id
            and reversal.root_disposition_id == root.id
            and request.expected_reversal_hash == reversal.request_hash
            and _aware(row.created_at) >= _aware(reversal.created_at))
        if type(row) is Decision:
            _need(row.expected_reversal_hash == request.expected_reversal_hash
                and row.disposition == request.disposition
                and row.actor_user_id != order.actor_user_id
                and row.actor_person_id != order.requester_id)
        else:
            _need(type(decision) is Decision and decision.id == row.correction_decision_id == request.correction_decision_id
                and decision.root_disposition_id == root.id and decision.reversal_id == reversal.id
                and decision.expected_reversal_hash == reversal.request_hash
                and request.expected_correction_decision_hash == decision.request_hash
                and row.disposition == decision.disposition and row.plan_hash == request.expected_plan_hash
                and _aware(row.created_at) >= _aware(decision.created_at))
    # No internal placeholder or recoverable raw key leaves this function.
    return {'request_hash':row.request_hash,'action':document['action']}
