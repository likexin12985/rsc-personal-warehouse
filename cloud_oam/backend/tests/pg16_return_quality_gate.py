"""Condition-aware return gates shared by local owned PG16 and isolated CI legs."""
from contextlib import nullcontext
from unittest.mock import patch
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
import pg16_stock_loss_return_receipt_gate as receipts
import pg16_return_quality_business as business
from pg16_return_quality_serial_fixture import two_serials


def release(engines, *, tracking, scenario, migrate, provision):
    if tracking not in ('quantity', 'serial') or scenario not in ('whole', 'mixed'):
        raise ValueError('explicit quantity/serial tracking and whole/mixed quality scenario required')
    mixed = scenario == 'mixed'
    def provision_and_check():
        provision()
        with engines['star_oam_api'].connect() as db:
            with pytest.raises(DBAPIError) as error:
                db.execute(text('SELECT public.rsc_require_return_inbound_quality_0162()'))
            assert error.value.orig.sqlstate == '42501'
            db.rollback()
    context = two_serials(business) if mixed and tracking == 'serial' else nullcontext()
    with context, patch.object(receipts, 'exercise', lambda value: business.exercise(value, mixed=mixed)):
        result = receipts.release(engines, tracking=tracking, migrate=migrate, provision=provision_and_check)
    result['returnInboundQuality'] = result.pop('returnReceipt')
    result.update(qualityScenario=scenario, privateQualityGuardExecutionDenied=True)
    return result
