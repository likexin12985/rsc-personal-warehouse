"""Read-only scrap proof at its original ledger cursor, independent of roles today.

Composes with the full chain verifier; does not authorize another write, load
cached balances as facts, or erase later executions. Native SQL proof remains
an independent requirement for production activation.
"""
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import or_, select

from app.foundation_models import FileObject
from app.inventory_models import InventoryTransaction, StockAccount, CustodyAssignment
from app.stock_operation_models import (StockLossDisposition, StockOperationOrder, StockOperationLine,
    StockLossHeadquartersDecision, StockLossHeadquartersReview)
from app.stock_loss_correction_models import StockLossCorrectionExecution, StockLossCorrectionDecision, StockLossDispositionReversal
from app.formal_services import stock_loss_facts as submission, stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_sources as sources, inventory_posting as posting
from app.formal_services.stock_loss_corrections.historical_original import _bound
from app.formal_services.stock_loss_corrections.historical_holds import read_hold_snapshot
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.stock_loss_corrections import posting_events
from app.formal_services.stock_loss_plan import _evidence
from app.formal_services.serial_ledger import rebuild_serial_states
from app.formal_services.work_order_query import _aware
from . import events, request_facts
from .tables import tables


def need(value):
    if not value:
        raise InvalidChain('stock_scrap_historical_facts_invalid')


def _one(db, table, *conditions):
    rows = db.execute(select(table).where(*conditions)).mappings().all()
    need(len(rows) == 1)
    return dict(rows[0])


def records(db, fact):
    schema = tables(); corrected = type(fact) is StockLossCorrectionExecution
    need(type(fact) in (StockLossDisposition, StockLossCorrectionExecution))
    name = 'stock_loss_correction_executions' if corrected else 'stock_loss_dispositions'
    raw = _one(db, schema[name], schema[name].c.id == fact.id)
    child = schema['stock_scrap_lines']
    line = _one(db, child, child.c.correction_execution_id == fact.id if corrected else
        (child.c.root_disposition_id == fact.id) & (child.c.source_kind == 'original'))
    order = _one(db, schema['stock_operation_orders'], schema['stock_operation_orders'].c.id == line['operation_id'])
    result = {name: [raw], 'stock_operation_orders': [order], 'stock_scrap_lines': [line]}
    for name, key in (('stock_scrap_serials', 'serial_id'), ('stock_scrap_files', 'file_id')):
        table = schema[name]
        result[name] = [dict(r) for r in db.execute(select(table).where(table.c.scrap_line_id == line['id'])
            .order_by(table.c[key])).mappings()]
    return result


@dataclass(frozen=True)
class ScrapProof:
    fact_id: UUID
    root_disposition_id: UUID
    posting_transaction_id: UUID
    posting_cursor: int
    observed_ledger_cursor: int
    plan_hash: str


def verify(db, *, fact, proved_inverse_ids=None):
    with db.no_autoflush:
        start = _bound(db)
        corrected = type(fact) is StockLossCorrectionExecution
        fact = db.get(type(fact), fact.id, populate_existing=True)
        need(fact is not None and fact.disposition == 'scrap')
        root = db.get(StockLossDisposition, fact.root_disposition_id, populate_existing=True) if corrected else fact
        need(root is not None)
        order = db.get(StockOperationOrder, root.operation_id, populate_existing=True)
        line = db.get(StockOperationLine, root.line_id, populate_existing=True)
        first_decision = db.get(StockLossHeadquartersDecision, root.headquarters_decision_id, populate_existing=True)
        review = db.get(StockLossHeadquartersReview, first_decision.review_id, populate_existing=True) if first_decision else None
        need(all(x is not None for x in (order, line, first_decision, review)))
        need(first_decision.line_id == line.id and line.operation_id == order.id == review.operation_id
            and order.operation_type == line.operation_type == 'loss_report')
        submission.submission_evidence(db, order=order)
        headquarters.verified(db, row=review, order=order)
        inverse = db.get(StockLossDispositionReversal, fact.reversal_id, populate_existing=True) if corrected else None
        decision = db.get(StockLossCorrectionDecision, fact.correction_decision_id, populate_existing=True) if corrected else first_decision
        if corrected:
            need(inverse is not None and proved_inverse_ids is not None and inverse.id in proved_inverse_ids)
        request = request_facts.verify(row=fact, root=root, order=order, review=review, reversal=inverse, decision=decision)
        data = records(db, fact); child = data['stock_scrap_lines'][0]; header = data['stock_operation_orders'][0]
        raw = data['stock_loss_correction_executions' if corrected else 'stock_loss_dispositions'][0]
        tx = db.get(InventoryTransaction, fact.posting_transaction_id, populate_existing=True)
        source = db.get(StockAccount, fact.source_account_id, populate_existing=True)
        need(tx is not None and source is not None and tx.ledger_cursor <= start[0])
        at = _aware(fact.created_at); cursor = tx.ledger_cursor - 1
        person = fact.actor_person_id if corrected else fact.executor_person_id
        need(source.id == root.source_account_id == line.reserved_account_id and source.availability_bucket == 'frozen'
            and fact.quantity == root.quantity == line.quantity and fact.quantity > 0
            and source.custodian_person_id == order.requester_id and source.material_id == line.material_id)
        for key in ('actor_user_id', 'authorization_version', 'request_id', 'idempotency_key_hash',
                    'request_hash', 'plan_hash', 'command_jsonb', 'plan_jsonb', 'posting_transaction_id'):
            need(header[key] == raw[key] == getattr(fact, key))
        need(header['id'] == raw['scrap_operation_id'] == child['operation_id']
            and header['operation_type'] == child['operation_type'] == 'scrap' and header['status'] == 'posted'
            and header['operation_no'] == 'SCRAP-' + fact.idempotency_key_hash[:24].upper()
            and header['source_location_id'] == source.location_id and header['requester_id'] == order.requester_id
            and header['reason'] == request.execution_reason
            and all(header[k] is None for k in ('target_location_id', 'transit_location_id', 'target_custody_assignment_id', 'oam_work_order_id'))
            and raw['target_account_id'] is None and raw['return_operation_id'] is None
            and child['source_kind'] == request.source.kind
            and child['source_hash'] == sources._hash(request.source.model_dump(mode='json'))
            and child['loss_line_id'] == line.id and child['root_disposition_id'] == root.id
            and child['frozen_account_id'] == source.id and child['quantity'] == fact.quantity
            and child['posting_transaction_id'] == tx.id and child['posting_movement_id'] == fact.posting_movement_id
            and child['plan_hash'] == fact.plan_hash and child['plan_jsonb'] == fact.plan_jsonb)
        need(child['original_decision_id'] == (None if corrected else first_decision.id)
            and child['correction_decision_id'] == (decision.id if corrected else None)
            and child['predecessor_reversal_id'] == (inverse.id if corrected else None)
            and child['correction_execution_id'] == (fact.id if corrected else None)
            and header['loss_correction_decision_id'] == (decision.id if corrected else None)
            and header['loss_headquarters_decision_id'] == (None if corrected else first_decision.id))
        need(all(_aware(r['created_at']) == at for rows in data.values() for r in rows))
        custody = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == source.location_id,
            CustodyAssignment.valid_from <= at, or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > at))))
        need(len(custody) == 1 and custody[0].id == fact.custody_assignment_id == child['custody_assignment_id']
            and custody[0].custodian_person_id == source.custodian_person_id)
        holds = read_hold_snapshot(db, source_account_id=source.id, through_cursor=cursor)
        share = next((s for s in holds.lines if s.line_id == line.id), None)
        need(holds.observed_ledger_cursor == start[0] and share is not None and share.active_execution_id is None
            and share.pending_reversal_id == (inverse.id if corrected else None)
            and share.frozen_quantity == fact.quantity and holds.balance_quantity >= fact.quantity)
        serial_ids = tuple(sorted(share.frozen_serial_ids, key=str))
        states = rebuild_serial_states(db, serial_ids, through_cursor=cursor)
        serial_basis = []
        for identifier in serial_ids:
            state = states[identifier]
            need(state.stock_account_id == source.id and state.lifecycle_status == 'active'
                and state.last_movement_id is not None and state.admission_movement_id is not None)
            serial_basis.append(dict(serial_id=str(identifier), previous_movement_id=str(state.last_movement_id),
                admission_movement_id=str(state.admission_movement_id), previous_ledger_cursor=state.ledger_cursor,
                lifecycle_before='active', lifecycle_after='scrapped'))
        need([(r['serial_id'],r['previous_movement_id'],r['admission_movement_id']) for r in data['stock_scrap_serials']]
            == [(s, states[s].last_movement_id, states[s].admission_movement_id) for s in serial_ids])
        evidence = _evidence(db, SimpleNamespace(user_id=fact.actor_user_id, person_id=person,
            authorization_version=fact.authorization_version), request.evidence_file_ids)
        need([(r['file_id'],r['metadata_sha256']) for r in data['stock_scrap_files']]
            == [(UUID(r['file_id']),r['metadata_sha256']) for r in evidence])
        for identifier in request.evidence_file_ids:
            file = db.get(FileObject, identifier, populate_existing=True)
            need(_aware(file.created_at) <= _aware(datetime.fromisoformat(file.metadata_jsonb['completion']['verified_at'])) <= at)
        policies, fingerprint = sources._policies(db, {source.material_id}, at)
        recorded = fact.plan_jsonb.get('policy_fingerprint')
        need(type(recorded) is list and len(recorded) == 1 and type(recorded[0]) is list
            and len(recorded[0]) == 7 and recorded[0][:6] == list(fingerprint[0][:6]))
        if recorded[0][6] is not None:
            need(recorded[0][6] == fingerprint[0][6] and _aware(datetime.fromisoformat(recorded[0][6])) > at)
        command = request_facts.posting_command(fact)
        posting._validate_tracking_rules(command, {source.id: source}, policies)
        expected = dict(schema_version='1.0', stage='scrap_stock_preparation_only', stock_effect='none',
            intent=fact.command_jsonb['intent'], actor_user_id=fact.actor_user_id, actor_person_id=str(person),
            authorization_version=fact.authorization_version, operation_id=str(order.id), line_id=str(line.id),
            decision_id=str(decision.id), predecessor_reversal_id=str(inverse.id) if corrected else None,
            source_account_id=str(source.id), target_account_id=None, owner_org_id=str(source.owner_org_id),
            custodian_person_id=str(source.custodian_person_id), location_id=str(source.location_id), material_id=str(source.material_id),
            lot_id=str(source.lot_id) if source.lot_id else None, source_condition=source.condition_code,
            custody_assignment_id=str(custody[0].id), quantity=format(fact.quantity,'.3f'),
            serial_ids=list(map(str,serial_ids)), serials=serial_basis, movement_type='scrap',
            external_boundary_code='stock_operation_scrap', ledger_cursor=cursor,
            source_balance_version=holds.balance_version, source_balance_quantity=format(holds.balance_quantity,'.3f'),
            frozen_holds_before=holds.plan_basis(), policy_fingerprint=recorded, evidence=evidence)
        need(sources._hash(expected) == sources._hash(fact.plan_jsonb) == fact.plan_hash)
        posting_events.verify(db, fact=fact)
        events.verify(db, records=data)
        need(_bound(db) == start)
        return ScrapProof(fact.id, root.id, tx.id, tx.ledger_cursor, start[0], fact.plan_hash)
