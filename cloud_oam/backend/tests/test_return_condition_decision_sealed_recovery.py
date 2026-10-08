"""Real historical read tests with explicitly seeded closure rows/audits.

This verifies the candidate reader. SQLite fixture insertion is NOT evidence
that the PostgreSQL registrar or reciprocal COMMIT constraints have passed.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_history as graph
from app.formal_services.stock_loss_corrections import return_condition_history_read as history
from app.formal_services.stock_loss_corrections import return_condition_keys as keys
from app.formal_services.stock_loss_corrections import return_condition_decision_seal_reads as reads
from app.formal_services.stock_loss_corrections import return_condition_decision_sealed_recovery as subject
from app.formal_services.stock_loss_corrections.return_condition_decision_seal_admission import canonical
from app.return_condition_decision_seal_schema import CASES, SOURCE
from test_return_condition_decision_seal_admission import review_grant
from test_return_condition_decisions import start, make_request
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived,
    ready, parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source, ERRORS,
)

pytestmark = pytest.mark.parametrize('stock,command_name',
    [('quantity','quantity_command'),('serial','serial_command')],indirect=['stock'])


def seed_reader_fixture(db, actor, command, proved):
    # The shared fixture already contains the complete ten-table schema.
    table = reads.table()
    assert table.name in __import__('sqlalchemy').inspect(db.connection()).get_table_names()
    tables = graph.tables()
    case = db.execute(select(tables[CASES]).where(tables[CASES].c.id == command.case_id)).mappings().one()
    events = tables['stock_condition_events']
    previous = db.execute(select(events).where(events.c.id == command.expected_event_id)).mappings().one()
    document = canonical(command); at = datetime.now(timezone.utc)
    row = dict(id=uuid4(),created_at=at,actor_user_id=actor.user_id,actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version,request_id=command.request_id,reason=command.reason,
        command_jsonb=document,request_hash=posting._canonical_hash(document),
        idempotency_key_hash=document['idempotency_key_hash'],**keys.aliases(command.idempotency_key),
        kind=command.action,case_id=command.case_id,expected_event_id=command.expected_event_id,
        historical_state=previous['to_state'],historical_sequence=previous['event_sequence'],
        claimed_event_hash=command.expected_event_hash,history_hash=proved.graph.fingerprint,
        **{name:case[name] for name in SOURCE})
    db.execute(table.insert(),row)
    append_audit_event(db,stream_key='inventory',actor_user_id=actor.user_id,
        action='seal_condition_decision_request',aggregate_type='stock_condition_decision_seal',
        aggregate_id=str(row['id']),request_id='condition-decision-seal:'+str(row['id']),
        before_jsonb={},after_jsonb=reads.payload(row),occurred_at=at,created_at=at)
    db.commit()
    return row


def test_old_seal_readonly_after_new_decision_and_write_denial(db,regional_source,request,command_name):
    c=regional_source
    initial=start(db,c,request.getfixturevalue(command_name)); db.commit()
    actor,missing=make_request(db,c.reviewer,initial,'return_evidence')
    missing=missing.model_copy(update={'expected_event_hash':'a'*64})
    proved=history.read(db,actor=actor,inbound_line_id=c.line)
    row=seed_reader_fixture(db,actor,missing,proved)
    # A separate real action advances the case. The sealed old request remains
    # a closed original input, never a substitute for this business result.
    actor,executed=make_request(db,c.reviewer,initial,'return_evidence')
    moved=decisions.decide(db,actor=actor,request=executed); db.commit()
    assert moved['status'] != initial['status']
    review_grant(db,c).effect='deny'; db.commit()
    actor=load_formal_principal(db,actor.user_id)
    before=snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        result=subject.lookup(db,actor=actor,request=missing)
        assert result['request_state']=='sealed' and result['result'] is None
        assert result['seal']['id']==str(row['id']) and result['seal']['claimed_event_hash']=='a'*64
        assert result['absence_sealed'] and not result['retry_allowed']
        assert not result['current_stock_verified'] and not result['original_preflight_verified']
        assert result['stock_effect']=='none'
        assert not db.new and not db.dirty and snapshot(db)==before
        found=subject.lookup(db,actor=actor,request=executed)
        assert found['request_state']=='found' and found['result']==moved
        with pytest.raises(ERRORS):
            subject.lookup(db,actor=actor,request=missing.model_copy(update={'reason':'changed original input'}))
        with pytest.raises(ERRORS):
            subject.lookup(db,actor=actor,request=missing.model_copy(update={'idempotency_key':'wrong-client-key-0001'}))
        assert snapshot(db)==before
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
