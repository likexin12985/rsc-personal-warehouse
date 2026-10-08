"""Real approved stock preparations, without activating an unproved writer."""
from dataclasses import replace
import json
from uuid import UUID, uuid4

import pytest

from app.stock_scrap_schemas import ScrapExecute
from app.return_condition_application_schema import predecessor_schema
from app.formal_services import stock_scrap_plan, stock_loss_sources
from app.formal_services.stock_scrap.execution_bundle import build
from test_stock_scrap_plan import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, request, snapshot,
)


def execution(preview, prepared):
    return ScrapExecute(**preview.model_dump(), expected_plan_hash=prepared.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)


def check_bundle(bundle, *, prepared, corrected):
    # The 0165 bundle predates nullable condition-correction provenance.
    metadata = predecessor_schema()
    transaction, movement = uuid4(), uuid4()
    rows = bundle.rows(transaction_id=transaction, movement_id=movement)
    for name, entries in rows.items():
        table = metadata.tables[name]
        for row in entries:
            assert set(row) == set(table.columns.keys()), name
            assert all(row[column.name] is not None for column in table.columns if not column.nullable)
    header = rows['stock_operation_orders'][0]
    line = rows['stock_scrap_lines'][0]
    fact = rows['stock_loss_correction_executions' if corrected else 'stock_loss_dispositions'][0]
    assert header['id'] == line['operation_id'] == fact['scrap_operation_id']
    assert header['id'] != UUID(prepared.document['operation_id'])
    assert line['id'] != UUID(prepared.document['line_id'])
    assert header['posting_transaction_id'] == line['posting_transaction_id'] == fact['posting_transaction_id'] == transaction
    assert line['posting_movement_id'] == fact['posting_movement_id'] == movement
    assert fact['target_account_id'] is None and header['target_location_id'] is None
    assert fact['plan_hash'] == header['plan_hash'] == line['plan_hash'] == prepared.plan_hash
    assert fact['request_hash'] == header['request_hash']
    command = bundle.posting_command
    assert command.source_document_id == str(fact['id'])
    assert command.movement_type == 'scrap' and len(command.movements) == 1
    edge = command.movements[0]
    assert edge.from_account_id == fact['source_account_id'] == line['frozen_account_id']
    assert edge.to_account_id is None and edge.external_boundary_code == 'stock_operation_scrap'
    assert edge.quantity == fact['quantity'] == line['quantity']
    assert set(edge.serial_ids) == {row['serial_id'] for row in rows['stock_scrap_serials']}
    assert len(rows['stock_scrap_serials']) == len(prepared.document['serials'])
    for serial, basis in zip(rows['stock_scrap_serials'], prepared.document['serials']):
        assert serial['previous_movement_id'] == UUID(basis['previous_movement_id'])
        assert serial['admission_movement_id'] == UUID(basis['admission_movement_id'])
    # A caller modifying a returned mapping cannot change the cached intent.
    line['quantity'] = -1
    assert bundle.rows(transaction_id=transaction, movement_id=movement)['stock_scrap_lines'][0]['quantity'] > 0
    return rows


def test_original_bundle_has_one_real_scrap_child_and_exact_external_posting(db, approved, monkeypatch):
    preview = request(db, approved, monkeypatch)
    prepared = stock_scrap_plan.prepare(db, actor=approved.actor, request=preview)
    before = snapshot(db)
    bundle = build(actor=approved.actor, request=execution(preview, prepared), preparation=prepared)
    rows = check_bundle(bundle, prepared=prepared, corrected=False)
    assert 'stock_loss_correction_executions' not in rows
    assert rows['stock_scrap_lines'][0]['root_disposition_id'] == rows['stock_loss_dispositions'][0]['id']
    assert rows['stock_scrap_lines'][0]['correction_execution_id'] is None
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('change', ['plan_hash', 'actor', 'auth_version', 'request_intent', 'missing_admission'])
def test_bundle_rejects_changed_execution_or_incomplete_serial_preparation(db, allowed, approved, monkeypatch, change):
    if change == 'missing_admission' and not allowed.tracked:
        pytest.skip('admission proof applies to serial stock')
    preview = request(db, approved, monkeypatch)
    prepared = stock_scrap_plan.prepare(db, actor=approved.actor, request=preview)
    command = execution(preview, prepared)
    actor = approved.actor
    if change == 'plan_hash':
        command = command.model_copy(update={'expected_plan_hash': 'f' * 64})
    elif change == 'actor':
        actor = replace(actor, person_id=uuid4())
    elif change == 'auth_version':
        actor = replace(actor, authorization_version=actor.authorization_version + 1)
    elif change == 'request_intent':
        command = command.model_copy(update={'execution_reason': '另一份实物处置'})
    else:
        value = prepared.document
        value['serials'][0]['admission_movement_id'] = None
        prepared = replace(prepared, document_json=json.dumps(value), plan_hash=stock_loss_sources._hash(value))
        command = command.model_copy(update={'expected_plan_hash': prepared.plan_hash})
    before = snapshot(db)
    with pytest.raises((ValueError, TypeError)):
        build(actor=actor, request=command, preparation=prepared)
    assert snapshot(db) == before
