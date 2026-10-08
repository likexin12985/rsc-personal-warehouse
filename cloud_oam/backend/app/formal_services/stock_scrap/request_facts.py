"""Canonical historical scrap requests, without reconstructing a usable key."""
import re
from uuid import UUID
from app.stock_scrap_schemas import ScrapExecute, ScrapPreview
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.work_order_query import _aware


def need(value):
    if not value:
        raise InvalidChain('stock_scrap_historical_request_invalid')


def verify(*, row, root, order, review=None, reversal=None, decision=None):
    try:
        document = row.command_jsonb
        request = ScrapExecute.model_validate(dict(document['intent'], request_id=document['request_id'],
            expected_plan_hash=document['expected_plan_hash'], idempotency_key='historical-proof-placeholder'))
        intent = ScrapPreview.model_validate(request.model_dump(include=set(ScrapPreview.model_fields))).model_dump(mode='json')
        intent['evidence_file_ids'].sort()
        canonical = dict(schema_version=1, action='scrap', intent=intent,
            request_id=request.request_id, expected_plan_hash=request.expected_plan_hash)
        source = request.source
        corrected = source.kind == 'correction'
        person = row.actor_person_id if corrected else row.executor_person_id
        actor_id = UUID(row.actor_user_id)
        need(row.disposition == 'scrap' and row.target_account_id is None
            and document == canonical and sources._hash(canonical) == row.request_hash
            and request.request_id == row.request_id and request.expected_plan_hash == row.plan_hash
            and sources._hash(row.plan_jsonb) == row.plan_hash
            and type(person) is UUID and person.int != 0
            and actor_id.int != 0 and str(actor_id) == row.actor_user_id
            and type(row.authorization_version) is int and row.authorization_version > 0
            and re.fullmatch('[a-f0-9]{64}', row.idempotency_key_hash) is not None
            and root.operation_id == order.id and order.operation_type == 'loss_report'
            and source.expected_submission_plan_hash == order.plan_hash)
        if corrected:
            need(reversal is not None and decision is not None
                and row.root_disposition_id == source.root_disposition_id == root.id
                and source.expected_root_request_hash == root.request_hash
                and row.reversal_id == source.reversal_id == reversal.id
                and row.correction_decision_id == source.correction_decision_id == decision.id
                and decision.root_disposition_id == reversal.root_disposition_id == root.id
                and decision.reversal_id == reversal.id and decision.disposition == 'scrap'
                and decision.expected_reversal_hash == source.expected_reversal_hash == reversal.request_hash
                and source.expected_correction_decision_hash == decision.request_hash
                and row.reason == request.execution_reason
                and _aware(row.created_at) >= _aware(decision.created_at) >= _aware(reversal.created_at))
        else:
            need(row.id == root.id and review is not None and decision is not None
                and source.headquarters_decision_id == row.headquarters_decision_id == decision.id
                and decision.line_id == root.line_id and decision.review_id == review.id
                and decision.disposition == 'scrap' and review.operation_id == order.id
                and source.expected_headquarters_review_hash == review.request_hash
                and _aware(row.created_at) >= _aware(review.created_at))
        return request
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise InvalidChain('stock_scrap_historical_request_invalid') from error


def posting_command(row):
    corrected = hasattr(row, 'root_disposition_id')
    return posting.InventoryPostingCommand(transaction_no='INV-SCRAP-' + row.idempotency_key_hash[:24].upper(),
        movement_type='scrap', source_document_type='stock_loss_correction_execution' if corrected else 'stock_loss_disposition',
        source_document_id=str(row.id), posting_key=f'stock-scrap:{row.id}:{row.idempotency_key_hash}',
        effective_at=_aware(row.created_at), movements=(posting.InventoryMovementCommand(
            from_account_id=row.source_account_id, to_account_id=None, quantity=row.quantity,
            serial_ids=tuple(UUID(s) for s in row.plan_jsonb['serial_ids']), external_boundary_code='stock_operation_scrap'),))


def original_payload(row):
    return dict(disposition_id=str(row.id), operation_id=str(row.operation_id), line_id=str(row.line_id),
        headquarters_decision_id=str(row.headquarters_decision_id), disposition='scrap',
        executor_person_id=str(row.executor_person_id), authorization_version=row.authorization_version,
        posting_transaction_id=str(row.posting_transaction_id), posting_movement_id=str(row.posting_movement_id),
        quantity=format(row.quantity, '.3f'), source_account_id=str(row.source_account_id), target_account_id=None,
        request_id=row.request_id, request_hash=row.request_hash, plan_hash=row.plan_hash, status='posted')
