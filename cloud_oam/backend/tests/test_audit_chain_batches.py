"""Batch boundaries never replace complete linked-hash validation."""
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.foundation_models import AuditEvent
from app.formal_services import audit_chain
from test_audit_chain import db, make_actor, seed_head, append_kwargs, STREAM_KEY, NOW


@pytest.fixture
def chain(db):
    actor = make_actor(db)
    seed_head(db)
    seed_head(db, 'inventory')
    identifiers = []
    for index in range(259):
        kwargs = dict(append_kwargs(actor), request_id=f'batch-audit-{index:04}',
            occurred_at=NOW + timedelta(seconds=index))
        identifiers.append(audit_chain.append_audit_event(db, **kwargs).id)
    # A different stream must neither satisfy membership nor hide a gap.
    audit_chain.append_audit_event(db, **dict(append_kwargs(actor),
        stream_key='inventory', request_id='unrelated-audit'))
    db.commit()
    return tuple(identifiers)


@pytest.mark.parametrize('mode', ['read_snapshot', 'locked', 'prelocked'])
def test_two_batches_verify_complete_prefix_with_bounded_query_count(db, chain, mode):
    if mode == 'read_snapshot':
        verify = audit_chain.verify_audit_event_in_read_snapshot
    elif mode == 'locked':
        verify = audit_chain.verify_audit_event_in_stream
    else:
        _, proof = audit_chain._lock_audit_chain_head_with_proof(db, stream_key=STREAM_KEY)
        verify = lambda session, **kw: audit_chain._verify_audit_event_with_prelocked_proof(
            session, proof=proof, **kw)
    statements = []

    def observe(conn, cursor, sql, parameters, context, executemany):
        assert sql.lstrip().upper().startswith('SELECT ')
        statements.append(sql)

    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        found = verify(db, stream_key=STREAM_KEY, event_id=chain[0])
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert found.id == chain[0]
    assert len(statements) <= 8, len(statements)
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('damage', ['genesis_payload', 'second_batch_gap', 'wrong_stream'])
def test_event_at_head_does_not_short_circuit_damaged_earlier_batch(db, chain, damage):
    first = db.get(AuditEvent, chain[0])
    if damage == 'genesis_payload':
        first.after_jsonb = {'forged': True}
    elif damage == 'second_batch_gap':
        db.delete(first)
    else:
        first.stream_key = 'inventory'
        first.stream_version = 2
    db.commit()
    with pytest.raises(audit_chain.AuditChainStateError):
        audit_chain.verify_audit_event_in_read_snapshot(db,
            stream_key=STREAM_KEY, event_id=chain[-1])
