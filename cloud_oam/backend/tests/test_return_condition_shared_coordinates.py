"""Boundary checks for combined action evidence, not historical DB acceptance."""
from uuid import uuid4
import pytest
from app import return_condition_complete_schema as complete
from app.formal_services.stock_loss_corrections import return_condition_coordinates as subject
from app.formal_services.inventory_query import InventoryReadError


def blank():
    return {name: () for name in (*subject.TABLES, 'audits', 'states', 'outbox', 'notifications')}


def closed():
    seal = dict(id=uuid4())
    evidence = blank()
    evidence[subject.DECISION_SEAL] = (seal['id'],)
    evidence['audits'] = ((uuid4(), 'stock_condition_decision_seal', str(seal['id'])),)
    return seal, evidence


def test_complete_model_exposes_all_coordinate_tables():
    metadata = complete.build_schema()[0]
    assert all(name in metadata.tables for name in subject.TABLES)
    assert len(set(subject.TABLES)) == len(subject.TABLES)
    assert subject.DECISION_SEAL in subject.TABLES and subject.SETTLEMENT_INPUT in subject.TABLES


def test_proved_decision_closure_has_only_its_row_and_audit():
    seal, evidence = closed()
    subject.verify(evidence, decision_seal=seal)
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence)


@pytest.mark.parametrize('table', [subject.SETTLEMENT_INPUT, subject.SEAL_NAME,
    'stock_condition_events', 'stock_condition_submission_requests', subject.KEY_NAME])
def test_closure_cannot_mask_other_action_evidence(table):
    seal, evidence = closed()
    evidence[table] = (uuid4(),)
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence, decision_seal=seal)


@pytest.mark.parametrize('variant', ['wrong_row', 'second_row', 'wrong_audit', 'missing_audit', 'stock_effect'])
def test_closure_row_audit_and_no_stock_effect_are_exact(variant):
    seal, evidence = closed()
    if variant == 'wrong_row':
        evidence[subject.DECISION_SEAL] = (uuid4(),)
    elif variant == 'second_row':
        evidence[subject.DECISION_SEAL] += (uuid4(),)
    elif variant == 'wrong_audit':
        evidence['audits'] = ((uuid4(), 'stock_condition_decision_seal', str(uuid4())),)
    elif variant == 'missing_audit':
        evidence['audits'] = ()
    else:
        evidence['states'] = ((uuid4(), 'inventory_transaction', str(uuid4())),)
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence, decision_seal=seal)


@pytest.mark.parametrize('kind', ['execute', 'release'])
def test_proved_settlement_requires_input_and_rejects_decision_seal(kind):
    event = dict(id=uuid4(), case_id=uuid4(), kind=kind, posting_transaction_id=uuid4())
    evidence = blank()
    for name in ('stock_condition_events', subject.KEY_NAME, subject.SETTLEMENT_INPUT):
        evidence[name] = (event['id'],)
    business = (uuid4(), 'stock_condition_event', str(event['id']))
    inventory = (uuid4(), 'inventory_transaction', str(event['posting_transaction_id']))
    evidence.update(audits=(business, inventory), states=(business, inventory),
        outbox=(business,), notifications=(business,))
    subject.verify(evidence, event=event)
    evidence[subject.DECISION_SEAL] = (uuid4(),)
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence, event=event)
    evidence[subject.DECISION_SEAL] = ()
    evidence[subject.SETTLEMENT_INPUT] = ()
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence, event=event)


def test_multiple_proof_types_are_rejected():
    seal, evidence = closed()
    with pytest.raises(InventoryReadError, match='原请求存在冲突'):
        subject.verify(evidence, seal=seal, decision_seal=seal)


@pytest.mark.parametrize('kind', ['submit', 'withdraw', 'execute', 'release'])
@pytest.mark.parametrize('outcome', ['sealed', 'unknown', 'denied'])
def test_late_write_only_labels_fully_proved_closure_as_conflict(monkeypatch, kind, outcome):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from pydantic import TypeAdapter
    from app.return_condition_http_schemas import ConditionCommand
    from app.formal_services.stock_loss_corrections import return_condition_recovery as initial
    from app.formal_services.stock_loss_corrections import return_condition_decision_sealed_recovery as actions
    from test_return_condition_http_adapter import command

    request = TypeAdapter(ConditionCommand).validate_python(command(kind))
    actor = object(); db = SimpleNamespace(no_autoflush=nullcontext())
    observed = blank()
    observed[subject.SEAL_NAME if kind == 'submit' else subject.DECISION_SEAL] = (uuid4(),)
    monkeypatch.setattr(subject, 'capture', lambda *a, **k: observed)
    calls = []
    def verified_read(actual_db, *, actor: object, request: object):
        calls.append((actual_db, actor, request))
        if outcome == 'denied':
            raise InventoryReadError(code='scope_denied', status_code=403, message='scope denied')
        return dict(request_state=outcome, absence_sealed=outcome == 'sealed',
            retry_allowed=False, stock_effect='none')
    monkeypatch.setattr(initial if kind == 'submit' else actions, 'lookup', verified_read)
    with pytest.raises(InventoryReadError) as error:
        subject.require_unused(db, actor=actor, request=request)
    assert calls == [(db, actor, request)]
    assert error.value.status_code == {'sealed':409, 'unknown':503, 'denied':403}[outcome]
    assert error.value.code == {'sealed':'return_condition_request_sealed',
        'unknown':'return_condition_request_outcome_unknown', 'denied':'scope_denied'}[outcome]
