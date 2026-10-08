"""Real scoped source and submission history; SQLite/FakeStorage are explicit."""
import json

import pytest
from sqlalchemy import select, update, text

from app.foundation_models import Permission, RolePermission
from app.stock_operation_models import StockOperationReturnInbound, StockOperationReturnInboundLine
from app.formal_access import load_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_case_read as subject
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.return_condition_read_schemas import ConditionCaseHistory
from app.routers import formal_return_condition_history as api
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready,
    parcel, acceptance, prepared, regional_opening, reader_tables, context, regional_source,
)


@pytest.mark.parametrize('stock,command_name', [('quantity', 'quantity_command'), ('serial', 'serial_command')], indirect=['stock'])
def test_real_public_history_is_readonly_and_scope_bound(db, regional_source, request, command_name, monkeypatch):
    c = regional_source; command = request.getfixturevalue(command_name)
    empty = subject.read(db, actor=c.actor, inbound_line_id=c.line)
    assert empty.cases == [] and empty.events == []
    line = db.get(StockOperationReturnInboundLine, c.line)
    header = db.get(StockOperationReturnInbound, line.inbound_id)
    receipt_id = header.receipt_id
    discovered = subject.receipt_sources(db, actor=c.actor, receipt_id=receipt_id)
    assert discovered.status == 'posted' and str(discovered.inbound_id) == str(header.id)
    assert [item.inbound_line_id for item in discovered.items] == [c.line]
    assert discovered.items[0] == empty
    db.rollback()
    posted = writer.submit(db, actor=c.actor, request=command); db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    value = subject.read(db, actor=c.actor, inbound_line_id=c.line)
    db.execute(text('PRAGMA query_only=OFF'))
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=ON'))
    found = subject.receipt_sources(db, actor=c.actor, receipt_id=receipt_id)
    db.execute(text('PRAGMA query_only=OFF'))
    assert found.items == [value] and snapshot(db) == before
    assert value.events[0].fact.model_dump(mode='json') == posted
    assert value.held_quantity == command.quantity and value.corrected_quantity == 0
    assert value.cases[0].latest_event_id == value.events[0].fact.event_id
    assert value.cases[0].serial_ids == [s.serial_id for s in command.serial_verifications]
    assert value.sku_code and value.material_name and value.base_unit
    assert {s.serial_id for s in value.serials}.issuperset(value.cases[0].serial_ids)
    raw = value.model_dump(mode='json'); encoded = json.dumps(raw)
    assert command.idempotency_key not in encoded
    for private in ('command_jsonb', 'input_jsonb', 'source_jsonb', 'idempotency_key', 'download_url'):
        assert private not in encoded
    assert not value.current_stock_verified and not value.write_authorization_provided and not value.retry_allowed
    # A public projection cannot silently omit an event or re-label approval as stock.
    for changed in (dict(raw, events=[]), dict(raw, corrected_quantity='1.000'),
                    dict(raw, current_stock_verified=True), dict(raw, secret='internal')):
        with pytest.raises(ValueError): ConditionCaseHistory.model_validate(changed)
    db.rollback()
    # The actual route uses the same scoped reader and never commits.
    with monkeypatch.context() as patch:
        patch.setattr(db, 'commit', lambda: pytest.fail('history route must not commit'))
        assert api.read(c.line, db, c.actor) == raw
    grant = db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation', Permission.action == 'submit_return_condition'))
    grant.effect = 'deny'; db.commit()
    actor = load_formal_principal(db, c.actor.user_id)
    assert subject.read(db, actor=actor, inbound_line_id=c.line).model_dump(mode='json') == raw
    db.rollback()
    # Final scope check must reject revocation after proof, before disclosure.
    real_capture = subject.graph.capture
    grant = db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id == c.regional_role.id,
        Permission.resource == 'stock_operation', Permission.action == 'read'))
    original_read = subject.history.read
    proved = False
    def verified(*args, **kwargs):
        nonlocal proved
        result = original_read(*args, **kwargs); proved = True; return result
    def revoke_after(*args, **kwargs):
        result = real_capture(*args, **kwargs)
        if proved: db.execute(update(RolePermission).where(RolePermission.id == grant.id).values(effect='deny'))
        return result
    with monkeypatch.context() as patch:
        patch.setattr(subject.history, 'read', verified)
        patch.setattr(subject.graph, 'capture', revoke_after)
        with pytest.raises(InventoryReadError) as error:
            subject.read(db, actor=actor, inbound_line_id=c.line)
        assert error.value.code == 'return_condition_history_forbidden'
    db.rollback()

