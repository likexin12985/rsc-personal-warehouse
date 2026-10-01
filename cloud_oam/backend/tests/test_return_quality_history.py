"""Authorized exception projection preserves old postings and original recovery."""
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
def test_proved_legacy_anomaly_is_separate_from_valid_original_history(db, stock, approved, derived, acceptance, monkeypatch, legacy, damage):
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
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
