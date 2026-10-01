"""Authorized exception projection preserves old postings and original recovery."""
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db
from app.dependencies import get_formal_principal
from test_stock_loss_source_routes import private
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.stock_operation_models import StockOperationReceiptSerial
from app.formal_services import stock_return_inbound_plan as planning
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services.stock_return_inbound_quality import AcceptedPart
from app.formal_services.work_order_return_sources import _hash
from app.formal_services.stock_loss_corrections import return_history
from test_loss_return_damaged_inbound import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, acceptance, regional_opening,
    receipt_evidence, abnormal, post,
)
from test_loss_return_history import headquarters_reader
from test_stock_return_inbound import snapshot


@pytest.mark.parametrize('stock,legacy,damage', [
    ('quantity', True, '1'), ('quantity', False, '1'),
    ('quantity', True, '.375'), ('quantity', False, '.375'),
    ('serial', True, '1'), ('serial', False, '1'),
], indirect=['stock'])
def test_registered_history_get_preserves_facts_and_exposes_classification(db, stock, approved, derived, acceptance, monkeypatch, legacy, damage):
    file, _ = receipt_evidence(db, stock, acceptance, monkeypatch)
    value = abnormal(acceptance, file.id, 'damaged')
    acceptance.request = value.model_copy(update={'lines': (
        value.lines[0].model_copy(update={'damaged_qty': Decimal(damage)}),)})
    if legacy:
        current = planning.plan_return_inbound
        def historical_parts(db, *, origin, source, at):
            identifiers = tuple(db.scalars(select(StockOperationReceiptSerial.serial_id).where(
                StockOperationReceiptSerial.line_id == origin.id,
                StockOperationReceiptSerial.result == 'accepted').order_by(StockOperationReceiptSerial.serial_id)))
            return (AcceptedPart(source.condition_code, origin.accepted_qty, identifiers),)
        def old_plan(*args, **kwargs):
            result = current(*args, **kwargs)
            result['schema_version'] = '1.0'
            result['plan_hash'] = _hash(planning.plan_document(result))
            return result
        # Recreate the historical application contract only in synthetic SQLite.
        # Native old-code/0161 upgrade coverage remains a separate gate.
        with monkeypatch.context() as old:
            old.setattr(planning, 'receipt_parts', historical_parts)
            old.setattr(commands, 'plan_return_inbound', old_plan)
            _, posted = post(db, acceptance, schema='1.0')
    else:
        _, posted = post(db, acceptance)
    actor = headquarters_reader(db, stock, approved)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    try:
        history = return_history.read(db, actor=actor, root_disposition_id=UUID(derived.result['disposition_id']))
        assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
        assert history.write_authorization_provided is False
        assert sum(share.quantity for share in history.lines[0].shares if share.stage == 'posted_inbound') == 1
        if not legacy:
            assert history.classification_exceptions == ()
        else:
            assert len(history.classification_exceptions) == 1
            issue = history.classification_exceptions[0]
            assert issue.inbound_id == posted['inbound_id']
            assert issue.affected_quantity == Decimal(damage)
            assert issue.recorded_condition == 'new' and issue.required_condition == 'damaged'
            assert issue.code == 'legacy_damaged_acceptance_classification'
            assert issue.current_stock_verified is False and issue.correction_authorized is False
            assert set(issue.affected_serial_ids) == set(acceptance.request.lines[0].damaged_serial_ids)
        monkeypatch.setattr(app, 'dependency_overrides', {
            get_db: lambda: db, get_formal_principal: lambda: actor,
        })
        client = TestClient(app, raise_server_exceptions=False)
        try:
            path = '/api/v1/stock-operations/loss-reports/corrections/return-history/'
            response = client.get(path + str(derived.result['disposition_id']))
            assert response.status_code == 200, response.text
            private(response)
            body = response.json()
            assert body['result_scope'] == 'verified_loss_return_history'
            assert body['stock_effect'] == 'none'
            assert body['write_authorization_provided'] is False
            assert body['current_stock_verified'] is False
            assert len(body['classification_exceptions']) == int(legacy)
            assert body['coordinates']['inbounds'] == [str(posted['inbound_id'])]
            assert Decimal(body['lines'][0]['damaged_accepted_quantity']) == Decimal(damage)
            if legacy:
                issue = body['classification_exceptions'][0]
                assert Decimal(issue['affected_quantity']) == Decimal(damage)
                assert set(issue['affected_serial_ids']) == {str(i) for i in acceptance.request.lines[0].damaged_serial_ids}
                assert issue['correction_authorized'] is False
            for secret in ('idempotency_key', 'command_jsonb', 'plan_jsonb', 'key_hash', 'intent_hash'):
                assert secret not in response.text
            from uuid import uuid4
            missing = client.get(path + str(uuid4()))
            assert missing.status_code == 404, missing.text
            private(missing)
            # A known exact root must not become readable by a sender lacking HQ scope.
            app.dependency_overrides[get_formal_principal] = lambda: derived.actor
            stale = client.get(path + str(derived.result['disposition_id']))
            assert stale.status_code == 412, stale.text
            assert stale.json()['detail']['code'] == 'actor_principal_stale'
            private(stale)
            # The synthetic posting loader holds the currently selected identity.
            # Switch it as well, then separately verify the real HQ scope denial.
            stock.world.current_principal = derived.actor
            denied = client.get(path + str(derived.result['disposition_id']))
            assert denied.status_code == 403, denied.text
            private(denied)
            assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
        finally:
            client.close()
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
