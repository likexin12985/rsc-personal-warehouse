"""Mock-only checks for the hosted inventory receipt rejection expectation.

These exercise the actual gate helper without a database and do not replace
the next candidate's real PostgreSQL 16 inventory evidence.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

import pg16_stock_return_receipt_gate as checks


PRIMARY = '0169 fulfillment request reference missing'


def rejection(sqlstate='23503', primary=PRIMARY, *, diagnostic=True):
    original = RuntimeError(PRIMARY)
    original.sqlstate = sqlstate
    if diagnostic:
        original.diag = SimpleNamespace(message_primary=primary)
    return DBAPIError('INSERT INTO inbound_orders', None, original)


def prepare(monkeypatch, *, error=None, unexpected_success=False,
            drift=False, rollback_failure=False):
    sessions = []
    events = []
    receipt_id = uuid4()
    context = SimpleNamespace(package=SimpleNamespace(
        shipment_id=uuid4(), target_location_id=uuid4()),
        actor=SimpleNamespace(person_id=uuid4()))

    def session(_engine):
        db = MagicMock()
        db.pending_inbound = False
        db.closed = False
        db.__enter__.return_value = db
        db.__exit__.side_effect = lambda *_: setattr(db, 'closed', True)
        db.begin_nested.side_effect = lambda: nullcontext()
        db.get.return_value = SimpleNamespace(idempotency_key_hash='synthetic-key',
            actor_user_id='synthetic-sender')
        db.execute.side_effect = rejection('42501', 'permission denied')

        def add(value):
            assert isinstance(value, checks.InboundOrder)
            db.pending_inbound = True

        def rollback():
            events.append(('rollback', db.pending_inbound))
            if db.pending_inbound and rollback_failure:
                raise RuntimeError('synthetic rollback failure')

        db.add.side_effect = add
        db.rollback.side_effect = rollback
        sessions.append(db)
        return db

    def snapshot(_engine):
        if sessions:
            last = sessions[-1]
            assert last.closed, 'fact readback must use a closed prior session'
            last.rollback.assert_called_once_with()
        events.append(('snapshot', bool(sessions and sessions[-1].pending_inbound)))
        return ('changed-facts',) if drift and sessions and sessions[-1].pending_inbound else ('original-facts',)

    def checkpoint(db):
        assert db.pending_inbound
        if not unexpected_success:
            raise error if error is not None else rejection()

    capture = MagicMock(side_effect=snapshot)
    monkeypatch.setattr(checks, 'Session', session)
    monkeypatch.setattr(checks, 'receipt_snapshot', capture)
    monkeypatch.setattr(checks, '_context', lambda *_: context)
    monkeypatch.setattr(checks, '_command', lambda *_: object())
    monkeypatch.setattr(checks, '_execute', lambda *_: SimpleNamespace(receipt_id=receipt_id))
    # The prior 0105 cases are unchanged; stub their original database error.
    prior = RuntimeError('0105 synthetic prior rejection')
    prior.sqlstate = '23514'
    monkeypatch.setattr(checks, '_raw_receipt', MagicMock(
        side_effect=DBAPIError('prior receipt negative', None, prior)))
    monkeypatch.setattr(checks, '_checkpoint', checkpoint)
    return SimpleNamespace(sessions=sessions, events=events, capture=capture,
        receipt_id=receipt_id, context=context)


def invoke(kind='quantity'):
    checks.assert_receipt_sql_rejections(object(), {}, kind)


@pytest.mark.parametrize('kind,prior_count', [('quantity', 7), ('serial', 8)])
def test_exact_0169_refusal_rolls_back_before_final_fact_readback(monkeypatch, kind, prior_count):
    fixture = prepare(monkeypatch)
    invoke(kind)
    assert len(fixture.sessions) == prior_count + 1
    assert fixture.capture.call_count == prior_count + 2
    for db in fixture.sessions:
        db.rollback.assert_called_once_with()
        assert db.closed
    attempted = fixture.sessions[-1].add.call_args.args[0]
    assert attempted.receipt_id == fixture.receipt_id
    assert attempted.target_location_id == fixture.context.package.target_location_id
    assert attempted.target_person_id == fixture.context.actor.person_id
    assert fixture.events[-2:] == [('rollback', True), ('snapshot', True)]


@pytest.mark.parametrize('sqlstate', ['23514', 'P0001', '42501', None])
def test_other_sqlstate_is_not_accepted_despite_exact_primary_message(monkeypatch, sqlstate):
    fixture = prepare(monkeypatch, error=rejection(sqlstate=sqlstate))
    with pytest.raises(AssertionError):
        invoke()
    fixture.sessions[-1].rollback.assert_called_once_with()
    assert fixture.sessions[-1].closed
    assert fixture.events[-1] == ('rollback', True)


@pytest.mark.parametrize('primary,diagnostic', [
    (PRIMARY + ' extra', True),
    ('different database refusal', True),
    (None, True),
    (PRIMARY, False),
])
def test_23503_requires_exact_primary_diagnostic_not_display_text(monkeypatch, primary, diagnostic):
    fixture = prepare(monkeypatch, error=rejection(primary=primary, diagnostic=diagnostic))
    with pytest.raises(AssertionError):
        invoke()
    fixture.sessions[-1].rollback.assert_called_once_with()
    assert fixture.events[-1] == ('rollback', True)


def test_unexpected_success_still_fails_and_rolls_back(monkeypatch):
    fixture = prepare(monkeypatch, unexpected_success=True)
    with pytest.raises(pytest.fail.Exception, match='DID NOT RAISE'):
        invoke()
    fixture.sessions[-1].rollback.assert_called_once_with()
    assert fixture.events[-1] == ('rollback', True)


def test_non_database_exception_is_not_a_successful_guard_refusal(monkeypatch):
    fixture = prepare(monkeypatch, error=RuntimeError(PRIMARY))
    with pytest.raises(RuntimeError, match=PRIMARY):
        invoke()
    fixture.sessions[-1].rollback.assert_called_once_with()


def test_changed_facts_after_rollback_fail_the_real_gate_assertion(monkeypatch):
    fixture = prepare(monkeypatch, drift=True)
    with pytest.raises(AssertionError):
        invoke()
    fixture.sessions[-1].rollback.assert_called_once_with()
    assert fixture.events[-2:] == [('rollback', True), ('snapshot', True)]


def test_rollback_failure_is_not_hidden_by_a_successful_refusal(monkeypatch):
    fixture = prepare(monkeypatch, rollback_failure=True)
    with pytest.raises(RuntimeError, match='synthetic rollback failure'):
        invoke()
    assert fixture.events[-1] == ('rollback', True)
