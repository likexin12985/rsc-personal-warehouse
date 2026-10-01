"""Verify real synthetic flows and checked-in browser contracts without rewriting sources.

Explicit export is opt-in via RSC_LOSS_CORRECTION_FIXTURE_EXPORT_DIR; ordinary
pytest runs write generated examples only under their own temporary directory.
"""
from pathlib import Path
from uuid import UUID, uuid4
import json
import os
from types import SimpleNamespace
import pytest
from pydantic import TypeAdapter

from app.formal_services.stock_loss_execution_sources import execution_sources
from app.formal_services.stock_loss_corrections import (
    read_sources, reversal_stock, correction_stock, sealed_inverse, sealed_corrections,
    correction_approval, correction_execution, correction_recovery,
)
from app.formal_services.stock_loss_corrections.request_contracts import (
    ReversalPreview, ReversalExecute, CorrectionApprove, CorrectionPreview, CorrectionExecute, original_request,
)
from app.formal_services.stock_loss_corrections.correction_models import (
    StockLossDispositionReversal as Inverse, StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution,
)
from app.stock_loss_correction_http_schemas import (
    public_preview, InversePreview, ExecutionPreview, InverseRecovery, ApprovalRecovery, ExecutionRecovery,
)
from app.stock_loss_correction_source_schemas import CorrectionSources
from app.stock_loss_execution_source_schemas import ExecutionSourcesOut
from test_stock_loss_correction_multigeneration import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, active,
)
from test_stock_loss_original_history_recovery import bind_fixture

FIXTURES = Path(__file__).resolve().parents[2] / 'frontend/src/test-fixtures/loss-correction'


@pytest.mark.parametrize('execution', ['restore_available'], indirect=True)
def test_real_correction_flow_contracts(db, active, tmp_path):
    w = active
    original_fixtures = {p: p.read_bytes() for p in FIXTURES.glob('*.json')}
    export = {}
    def source():
        return read_sources.read(db, actor=w.actor, root_disposition_id=w.root.id)
    for flow in ('inverses', 'approvals', 'executions'):
        before = source()
        if flow == 'inverses':
            reference = ReversalPreview(**before.inverse_preview_reference.model_dump(), reason='核验原处置后的冲销')
            prepared = reversal_stock.prepare(db, actor=w.actor, request=reference)
            preview = public_preview(prepared, InversePreview).model_dump(mode='json')
            command = ReversalExecute(**reference.model_dump(), expected_plan_hash=prepared.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)
            lookup, seal, execute = sealed_inverse.lookup, sealed_inverse.seal, sealed_inverse.execute
            schema, model, identifier, kind = InverseRecovery, Inverse, 'reversal_id', 'inverse'
        elif flow == 'approvals':
            preview = None
            command = CorrectionApprove(**before.approval_reference.model_dump(), reason='总部独立复核后转旧件', disposition='convert_used', request_id=uuid4().hex, idempotency_key=uuid4().hex)
            lookup, seal, execute = correction_recovery.lookup, sealed_corrections.seal, correction_approval.approve
            schema, model, identifier, kind = ApprovalRecovery, Decision, 'correction_decision_id', 'approval'
        else:
            assert len(before.approval_choices) == 1
            reference = CorrectionPreview(**before.approval_choices[0].preview_reference.model_dump(), reason='按明确选择的总部决定执行')
            prepared = correction_stock.prepare(db, actor=w.actor, request=reference)
            preview = public_preview(prepared, ExecutionPreview).model_dump(mode='json')
            command = CorrectionExecute(**reference.model_dump(), expected_plan_hash=prepared.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)
            lookup, seal, execute = correction_recovery.lookup, sealed_corrections.seal, correction_execution.execute
            schema, model, identifier, kind = ExecutionRecovery, Execution, 'correction_execution_id', 'correction'
        adapter = TypeAdapter(schema)
        missing = adapter.validate_python(lookup(db, actor=w.actor, request=command)).model_dump(mode='json')
        sealed_command = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
        sealed = adapter.validate_python(seal(db, actor=w.actor, request=sealed_command)).model_dump(mode='json')
        db.commit()
        assert sealed['request_state'] == 'sealed'
        posted = execute(db, actor=w.actor, request=command)
        db.commit()
        row = db.get(model, UUID(posted[identifier]))
        bind_fixture(db, row, command, kind)
        found = adapter.validate_python(lookup(db, actor=w.actor, request=command)).model_dump(mode='json')
        assert found['result'] == posted and found['request_state'] == 'found'
        export[flow] = dict(source=before.model_dump(mode='json'), preview=preview,
            original=command.model_dump(mode='json'), missing=missing, found=found,
            request_hash=original_request(actor=w.actor, request=command).request_hash,
            sealed_command=sealed_command.model_dump(mode='json'), sealed=sealed,
            after=source().model_dump(mode='json'))
    origin = execution_sources(db, actor=w.actor, operation_id=w.order.id).model_dump(mode='json')
    for data in export.values():
        data['origin'] = origin
    tracking = 'serial' if export['inverses']['source']['serial_ids'] else 'quantity'
    export_dir = os.getenv('RSC_LOSS_CORRECTION_FIXTURE_EXPORT_DIR')
    destination_dir = Path(export_dir) if export_dir else tmp_path / 'contracts'
    assert destination_dir.is_absolute(), 'explicit fixture export requires an absolute path'
    destination = destination_dir / (tracking + '.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(export, ensure_ascii=False, indent=2) + '\n')
    assert json.loads(destination.read_text()) == export
    if not export_dir:
        assert {p: p.read_bytes() for p in FIXTURES.glob('*.json')} == original_fixtures


@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
@pytest.mark.parametrize('flow', ['inverses', 'approvals', 'executions'])
def test_checked_in_correction_contract_matches_public_backend(tracking, flow):
    data = json.loads((FIXTURES / (tracking + '.json')).read_text())[flow]
    command_type, recovery_type = {
        'inverses': (ReversalExecute, InverseRecovery),
        'approvals': (CorrectionApprove, ApprovalRecovery),
        'executions': (CorrectionExecute, ExecutionRecovery),
    }[flow]
    for field in ('source', 'after'):
        assert CorrectionSources.model_validate(data[field]).model_dump(mode='json') == data[field]
    assert ExecutionSourcesOut.model_validate(data['origin']).model_dump(mode='json') == data['origin']
    adapter = TypeAdapter(recovery_type)
    for field in ('missing', 'found', 'sealed'):
        assert adapter.validate_python(data[field]).model_dump(mode='json') == data[field]
        assert data[field]['retry_allowed'] is False
    source, fact = data['source'], data['found']['result']
    actor = SimpleNamespace(user_id=fact['actor_user_id'], person_id=UUID(source['person_id']))
    for field, result_field in (('original','found'), ('sealed_command','sealed')):
        command = command_type.model_validate(data[field])
        assert command.model_dump(mode='json') == data[field]
        assert original_request(actor=actor, request=command).request_hash == data[result_field]['request_hash']
        assert command.request_id == data[result_field]['request_id']
        assert str(command.root_disposition_id) == source['root_disposition_id']
    assert data['request_hash'] == data['found']['request_hash'] == data['missing']['request_hash']
    assert fact['root_disposition_id'] == source['root_disposition_id']
    assert fact['operation_id'] == source['operation_id'] == data['origin']['report']['operation_id']
    assert fact['line_id'] == source['line_id']
    assert fact['actor_person_id'] == source['person_id']
    assert bool(source['serial_ids']) == (tracking == 'serial')
    assert data['sealed']['seal']['root_disposition_id'] == source['root_disposition_id']
    if flow == 'approvals':
        assert data['preview'] is None and fact['stock_effect'] == 'none'
    else:
        preview_type = InversePreview if flow == 'inverses' else ExecutionPreview
        preview = preview_type.model_validate(data['preview']).model_dump(mode='json')
        assert preview == data['preview']
        assert preview['quantity'] == fact['quantity'] == source['quantity']
        assert preview['plan_hash'] == fact['plan_hash'] == data['original']['expected_plan_hash']
        assert set(preview['serial_ids']) == set(source['serial_ids'])
