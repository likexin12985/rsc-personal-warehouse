"""Compile one locked scrap preparation into its atomic persistence records.

This performs no I/O and grants no posting authority. The writer must obtain a
fresh preparation under the ledger/reference/principal locks, execute the
unified posting command, then insert these records and the event bundle in the
same transaction. Database COMMIT proofs and exact request recovery remain
mandatory. A bundle alone must never be passed as a permission to post SN.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import re
from uuid import UUID, uuid4

from app.stock_scrap_schemas import ScrapPreview, ScrapExecute, validated_scrap_request
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.stock_loss_corrections.reversal_stock import StockPreparation


def _need(condition):
    if not condition:
        raise ValueError('scrap execution must match the complete checked preparation')


def _id(value):
    identifier = UUID(str(value))
    _need(identifier.int != 0)
    return identifier


@dataclass(frozen=True)
class ExecutionBundle:
    """Detached, immutable intent. Persisted identities are allocated once."""
    document_json: str
    posting_command: posting.InventoryPostingCommand
    posting_key: str
    checked_at: datetime

    @property
    def document(self):
        return json.loads(self.document_json)

    def rows(self, *, transaction_id, movement_id):
        transaction_id, movement_id = _id(transaction_id), _id(movement_id)
        _need(transaction_id != movement_id)
        records = self.document['records']
        for name, entries in records.items():
            for row in entries:
                row['created_at'] = self.checked_at
                for key, value in tuple(row.items()):
                    if (key == 'id' or key.endswith('_id')) and key not in ('actor_user_id', 'request_id'):
                        row[key] = _id(value) if value is not None else None
                if 'quantity' in row:
                    row['quantity'] = Decimal(row['quantity'])
                if name in ('stock_operation_orders', 'stock_loss_dispositions',
                            'stock_loss_correction_executions', 'stock_scrap_lines'):
                    row['posting_transaction_id'] = transaction_id
                if name != 'stock_operation_orders' and name in (
                    'stock_loss_dispositions', 'stock_loss_correction_executions', 'stock_scrap_lines',
                ):
                    row['posting_movement_id'] = movement_id
        return records


def build(*, actor, request, preparation):
    request = validated_scrap_request(request)
    _need(type(request) is ScrapExecute and type(preparation) is StockPreparation)
    plan = preparation.document
    intent = ScrapPreview.model_validate(request.model_dump(include=set(ScrapPreview.model_fields))).model_dump(mode='json')
    intent['evidence_file_ids'].sort()
    _need(preparation.checked_at.tzinfo is not None
        and plan.get('stage') == 'scrap_stock_preparation_only' and plan.get('stock_effect') == 'none'
        and plan.get('intent') == intent and sources._hash(plan) == preparation.plan_hash == request.expected_plan_hash
        and plan.get('actor_user_id') == actor.user_id and plan.get('actor_person_id') == str(actor.person_id)
        and plan.get('authorization_version') == actor.authorization_version
        and plan.get('movement_type') == 'scrap' and plan.get('target_account_id') is None
        and plan.get('external_boundary_code') == 'stock_operation_scrap')
    quantity = Decimal(plan['quantity'])
    _need(quantity.is_finite() and quantity > 0 and quantity.as_tuple().exponent >= -3
        and quantity < Decimal('1000000000000000'))
    serial_ids = tuple(_id(s) for s in plan['serial_ids'])
    _need(serial_ids == tuple(sorted(set(serial_ids), key=str)))
    _need(not serial_ids or quantity == len(serial_ids))
    serial_details = plan['serials']
    _need([row['serial_id'] for row in serial_details] == list(map(str, serial_ids)))
    _need([row['file_id'] for row in plan['evidence']] == intent['evidence_file_ids'])

    operation_id, scrap_line_id = uuid4(), uuid4()
    corrected = request.source.kind == 'correction'
    root_id = request.source.root_disposition_id if corrected else uuid4()
    correction_id = uuid4() if corrected else None
    fact_id = correction_id or root_id
    source = request.source
    _need(plan['decision_id'] == str(source.correction_decision_id if corrected else source.headquarters_decision_id)
        and plan['predecessor_reversal_id'] == (str(source.reversal_id) if corrected else None))
    command = dict(schema_version=1, action='scrap', intent=intent,
        request_id=request.request_id, expected_plan_hash=preparation.plan_hash)
    posting_key = 'stock-scrap:' + request.idempotency_key
    key_hash = posting._storage_hash(posting_key)
    common = dict(actor_user_id=actor.user_id, authorization_version=actor.authorization_version,
        request_id=request.request_id, idempotency_key_hash=key_hash,
        request_hash=sources._hash(command), command_jsonb=command,
        plan_hash=preparation.plan_hash, plan_jsonb=plan)
    source_id = _id(plan['source_account_id'])
    order = dict(common, id=str(operation_id), operation_no='SCRAP-' + key_hash[:24].upper(),
        operation_type='scrap', status='posted', oam_work_order_id=None,
        loss_headquarters_decision_id=None if corrected else str(source.headquarters_decision_id),
        loss_correction_decision_id=str(source.correction_decision_id) if corrected else None,
        source_location_id=plan['location_id'], target_location_id=None, transit_location_id=None,
        target_custody_assignment_id=None, requester_id=plan['custodian_person_id'], reason=request.execution_reason)
    disposition = dict(common, id=str(fact_id), disposition='scrap', source_account_id=str(source_id),
        target_account_id=None, custody_assignment_id=plan['custody_assignment_id'], quantity=plan['quantity'],
        return_operation_id=None, scrap_operation_id=str(operation_id))
    if corrected:
        disposition.update(root_disposition_id=str(root_id), actor_person_id=str(actor.person_id),
            reason=request.execution_reason, correction_decision_id=str(source.correction_decision_id),
            reversal_id=str(source.reversal_id))
    else:
        disposition.update(operation_id=plan['operation_id'], line_id=plan['line_id'],
            headquarters_decision_id=str(source.headquarters_decision_id), executor_person_id=str(actor.person_id))
    line = dict(id=str(scrap_line_id), operation_id=str(operation_id), operation_type='scrap',
        loss_line_id=plan['line_id'], root_disposition_id=str(root_id), source_kind=source.kind,
        original_decision_id=None if corrected else str(source.headquarters_decision_id),
        correction_decision_id=str(source.correction_decision_id) if corrected else None,
        predecessor_reversal_id=str(source.reversal_id) if corrected else None,
        correction_execution_id=str(correction_id) if corrected else None,
        frozen_account_id=str(source_id), custody_assignment_id=plan['custody_assignment_id'],
        quantity=plan['quantity'], source_hash=sources._hash(source.model_dump(mode='json')),
        plan_hash=preparation.plan_hash, plan_jsonb=plan)
    serials = []
    for item in serial_details:
        _need(item['lifecycle_before'] == 'active' and item['lifecycle_after'] == 'scrapped'
            and type(item['previous_ledger_cursor']) is int and 0 < item['previous_ledger_cursor'] <= plan['ledger_cursor'])
        serials.append(dict(scrap_line_id=str(scrap_line_id), serial_id=str(_id(item['serial_id'])),
            previous_movement_id=str(_id(item['previous_movement_id'])),
            admission_movement_id=str(_id(item['admission_movement_id']))))
    files = []
    for item in plan['evidence']:
        _need(re.fullmatch('[a-f0-9]{64}', item['metadata_sha256']) is not None)
        files.append(dict(scrap_line_id=str(scrap_line_id), file_id=str(_id(item['file_id'])),
            metadata_sha256=item['metadata_sha256']))
    records = dict(stock_operation_orders=[order], stock_scrap_lines=[line],
        stock_scrap_serials=serials, stock_scrap_files=files)
    records['stock_loss_correction_executions' if corrected else 'stock_loss_dispositions'] = [disposition]
    posting_command = posting.InventoryPostingCommand(
        transaction_no='INV-SCRAP-' + key_hash[:24].upper(), movement_type='scrap',
        source_document_type='stock_loss_correction_execution' if corrected else 'stock_loss_disposition',
        source_document_id=str(fact_id), posting_key=f'stock-scrap:{fact_id}:{key_hash}',
        effective_at=preparation.checked_at,
        movements=(posting.InventoryMovementCommand(from_account_id=source_id, to_account_id=None,
            quantity=quantity, serial_ids=serial_ids, external_boundary_code='stock_operation_scrap'),))
    return ExecutionBundle(json.dumps(dict(records=records), ensure_ascii=False, sort_keys=True,
        separators=(',', ':')), posting_command, posting_key, preparation.checked_at)
